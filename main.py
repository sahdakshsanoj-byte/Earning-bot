import telebot
import os
import re
import hmac
import hashlib
import logging
import pymongo
import time
from urllib.parse import parse_qsl
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from threading import Thread
from telebot import types
from datetime import datetime, date, timedelta

# ============================================================
# 1. LOGGING SETUP
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================
# 2. ENVIRONMENT VARIABLES
# ============================================================
BOT_TOKEN    = (os.getenv("BOT_TOKEN")    or "").strip()
MONGO_URI    = (os.getenv("MONGO_URI")    or "").strip()
ADMIN_ID_STR = (os.getenv("ADMIN_ID")    or "").strip()
BOT_USERNAME = (os.getenv("BOT_USERNAME") or "YourBotUsername").strip()
RENDER_URL   = (os.getenv("RENDER_URL")   or "").strip()
FRONTEND_URL = (os.getenv("FRONTEND_URL") or "https://sahdakshsanoj-byte.github.io").strip()
ADMIN_TOKEN  = (os.getenv("ADMIN_TOKEN")  or "").strip()

if not BOT_TOKEN:
    raise EnvironmentError("FATAL: BOT_TOKEN environment variable is not set!")
if not MONGO_URI:
    raise EnvironmentError("FATAL: MONGO_URI environment variable is not set!")
if not ADMIN_ID_STR:
    raise EnvironmentError("FATAL: ADMIN_ID environment variable is not set!")
try:
    ADMIN_ID = int(ADMIN_ID_STR)
except ValueError:
    raise EnvironmentError(f"FATAL: ADMIN_ID '{ADMIN_ID_STR}' is not a valid integer!")

# ============================================================
# 3. DATABASE CONNECTION
# ============================================================
try:
    client = pymongo.MongoClient(
        MONGO_URI,
        maxPoolSize=50,
        serverSelectionTimeoutMS=5000,
        w=1
    )
    db              = client['earning_bot_db']
    users_col       = db['users']
    withdrawals_col = db['withdrawals']
    rate_col        = db['rate_limits']
    config_col          = db['config']
    sponsor_clicks_col  = db['sponsor_clicks']
    try:
        rate_col.create_index("expires_at", expireAfterSeconds=0)
        logger.info("TTL index created on rate_limits.expires_at")
    except Exception as idx_err:
        logger.warning(f"TTL index creation skipped (may already exist): {idx_err}")

    # Performance indexes — critical for 1000+ users
    _idx_opts = {"background": True}
    try:
        users_col.create_index("user_id",     unique=True,  **_idx_opts)
        users_col.create_index("referred_by",              **_idx_opts)
        users_col.create_index("coins",                    **_idx_opts)
        users_col.create_index("ip",          sparse=True,  **_idx_opts)
        users_col.create_index("fingerprint", sparse=True,  **_idx_opts)
        users_col.create_index("blocked",     sparse=True,  **_idx_opts)
        users_col.create_index("joined",                   **_idx_opts)
        withdrawals_col.create_index("user_id",            **_idx_opts)
        withdrawals_col.create_index("status",             **_idx_opts)
        logger.info("MongoDB indexes ensured.")
    except Exception as idx_err:
        logger.warning(f"Index creation warning: {idx_err}")

    logger.info("MongoDB connected successfully.")
except Exception as e:
    logger.error(f"MongoDB connection failed: {e}")
    raise

# ============================================================
# 4. CONSTANTS
# ============================================================
TASK_CODES = {
    "yt1":      "CODE1",
    "yt2":      "CODE2",
    "yt3":      "CODE3",
    "web1":     "SITE1",
    "web2":     "SITE2",
    "web3":     "SITE3",
    "partner1": "PARTNER1",
}

TASK_REWARDS = {
    "yt1":      20,
    "yt2":      20,
    "yt3":      20,
    "web1":     15,
    "web2":     15,
    "web3":     15,
    "partner1": 15,
}

CHANNEL_REWARDS = {
    "official": 30,
    "channel2": 20,
    "channel3": 20,
    "slot1":    10,   # Sponsor Slot 1 (was sponsor1)
    "slot2":    10,   # Sponsor Slot 2
}

MAX_ADS_PER_DAY      = 5
AD_COIN_REWARD       = 10
MIN_WITHDRAW         = 4000
MAX_WITHDRAW         = 100000
WITHDRAW_COOLDOWN    = 86400   # 24 hours (was 3 hours)
SUPPORT_MAX_MSGS     = 1       # 1 message per day (was 2)
SUPPORT_WINDOW_HRS   = 24      # 24 hour window (was 6)
TASK_FAIL_COOLDOWN   = 60
TASK_MAX_FAILS       = 3

VALID_TASK_IDS = set(TASK_CODES.keys())

# ============================================================
# LEADERBOARD CACHE (in-memory, refreshed every 10 minutes)
# ============================================================
_leaderboard_cache      = "none"
_leaderboard_cache_time = 0
LEADERBOARD_TTL         = 600  # 10 minutes in seconds

def get_leaderboard_cached() -> str:
    global _leaderboard_cache, _leaderboard_cache_time
    now = time.time()
    if now - _leaderboard_cache_time < LEADERBOARD_TTL:
        return _leaderboard_cache
    _leaderboard_cache      = get_leaderboard()
    _leaderboard_cache_time = now
    return _leaderboard_cache

def refresh_leaderboard_loop():
    """Background thread: refreshes leaderboard every 10 minutes."""
    global _leaderboard_cache, _leaderboard_cache_time
    while True:
        time.sleep(LEADERBOARD_TTL)
        try:
            _leaderboard_cache      = get_leaderboard()
            _leaderboard_cache_time = time.time()
            logger.info("Leaderboard cache refreshed.")
        except Exception as e:
            logger.error(f"Leaderboard refresh error: {e}")

_task_codes_cache      = None
_task_codes_cache_time = 0.0
TASK_CODES_CACHE_TTL   = 300  # 5 minutes


def get_live_task_codes():
    """Returns task codes from MongoDB with 5-min in-memory cache. Falls back to TASK_CODES."""
    global _task_codes_cache, _task_codes_cache_time
    now = time.time()
    if _task_codes_cache is not None and now - _task_codes_cache_time < TASK_CODES_CACHE_TTL:
        return _task_codes_cache
    try:
        cfg = config_col.find_one({"_id": "task_codes"})
        if cfg and cfg.get("codes"):
            _task_codes_cache      = cfg["codes"]
            _task_codes_cache_time = now
            return _task_codes_cache
    except Exception:
        pass
    _task_codes_cache      = TASK_CODES
    _task_codes_cache_time = now
    return TASK_CODES

def check_admin_token(req):
    return req.headers.get("X-Admin-Token", "").strip() == ADMIN_TOKEN and ADMIN_TOKEN != ""

# ============================================================
# 5. TELEGRAM INIT DATA VERIFICATION
# ============================================================
def verify_telegram_init_data(init_data: str) -> dict | None:
    if not init_data:
        return None
    try:
        params = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = params.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(params.items())
        )
        secret_key = hmac.new(
            b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
        ).digest()
        computed = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(computed, received_hash):
            return None
        auth_date = int(params.get("auth_date", 0))
        if time.time() - auth_date > 600:
            return None
        return params
    except Exception as e:
        logger.warning(f"initData verification failed: {e}")
        return None


def get_verified_user_id(request_data: dict) -> int | None:
    init_data = request_data.get("init_data", "")
    if init_data:
        params = verify_telegram_init_data(init_data)
        if params is None:
            return None
        import json
        user_str = params.get("user", "{}")
        try:
            user_obj = json.loads(user_str)
            return int(user_obj.get("id", 0)) or None
        except Exception:
            return None
    raw = request_data.get("user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None

# ============================================================
# 6. RATE LIMITING
# ============================================================
def is_rate_limited(key: str, cooldown_seconds: int) -> bool:
    now = datetime.utcnow()
    try:
        doc = rate_col.find_one({"_id": key})
        if doc and doc.get("expires_at") > now:
            return True
        rate_col.update_one(
            {"_id": key},
            {"$set": {"expires_at": now + timedelta(seconds=cooldown_seconds)}},
            upsert=True
        )
        return False
    except Exception as e:
        logger.error(f"Rate limit check error for key '{key}': {e}")
        return False


def check_support_limit(user_id: int):
    """Returns (allowed: bool, message: str). 1 message per 24 hours."""
    now  = datetime.utcnow()
    user = users_col.find_one({"user_id": user_id}, {"support_window_start": 1, "support_count": 1})
    if not user:
        return False, "User not found."

    window_start_str = user.get("support_window_start", "")
    count            = user.get("support_count", 0)
    window_expired   = True

    if window_start_str:
        try:
            start_dt = datetime.fromisoformat(window_start_str)
            if now - start_dt < timedelta(hours=SUPPORT_WINDOW_HRS):
                window_expired = False
        except ValueError:
            pass

    if window_expired:
        users_col.update_one(
            {"user_id": user_id},
            {"$set": {"support_window_start": now.isoformat(), "support_count": 0}}
        )
        return True, ""

    if count >= SUPPORT_MAX_MSGS:
        remaining = timedelta(hours=SUPPORT_WINDOW_HRS) - (now - datetime.fromisoformat(window_start_str))
        h = int(remaining.total_seconds() // 3600)
        m = int((remaining.total_seconds() % 3600) // 60)
        return False, f"Daily message limit reached. Try again in {h}h {m}m."

    return True, ""

# ============================================================
# 7. TASK ATTEMPT TRACKING
# ============================================================
def is_task_attempt_blocked(user_id: int, task_id: str) -> bool:
    block_key = f"task_block_{user_id}_{task_id}"
    now = datetime.utcnow()
    try:
        doc = rate_col.find_one({"_id": block_key})
        if doc and doc.get("expires_at") > now:
            return True
        return False
    except Exception as e:
        logger.error(f"Task block check error: {e}")
        return False


def record_task_fail(user_id: int, task_id: str):
    counter_key = f"task_fail_{user_id}_{task_id}"
    block_key   = f"task_block_{user_id}_{task_id}"
    now = datetime.utcnow()
    try:
        doc = rate_col.find_one({"_id": counter_key})
        current_count = doc.get("count", 0) if doc else 0
        new_count = current_count + 1
        rate_col.update_one(
            {"_id": counter_key},
            {"$set": {"count": new_count, "expires_at": now + timedelta(seconds=TASK_FAIL_COOLDOWN)}},
            upsert=True
        )
        if new_count >= TASK_MAX_FAILS:
            rate_col.update_one(
                {"_id": block_key},
                {"$set": {"expires_at": now + timedelta(seconds=TASK_FAIL_COOLDOWN)}},
                upsert=True
            )
            rate_col.delete_one({"_id": counter_key})
    except Exception as e:
        logger.error(f"Record task fail error: {e}")


def clear_task_fail_counter(user_id: int, task_id: str):
    counter_key = f"task_fail_{user_id}_{task_id}"
    try:
        rate_col.delete_one({"_id": counter_key})
    except Exception as e:
        logger.error(f"Clear task fail error: {e}")

# ============================================================
# 8. CHANNEL MEMBERSHIP VERIFICATION
# ============================================================
def extract_channel_username(url: str) -> str | None:
    """Extract @username from a t.me link."""
    if not url:
        return None
    match = re.search(r't\.me/([a-zA-Z0-9_]+)', url)
    if match:
        username = match.group(1)
        # Skip joinchat/invite links — cannot verify private channels this way
        if username.lower() in ('joinchat', 'share', '+'):
            return None
        return '@' + username
    return None


def verify_channel_membership(channel_id_or_username: str, user_id: int) -> bool:
    """
    Returns True if user is a confirmed member of the channel.
    Returns False on API error (fail closed) — prevents reward without actual join.
    """
    try:
        member = bot.get_chat_member(channel_id_or_username, user_id)
        return member.status in ('member', 'administrator', 'creator')
    except Exception as e:
        logger.warning(f"Channel membership check failed for {channel_id_or_username}: {e}")
        return False  # Fail closed — do not reward if verification fails

# ============================================================
# 9. INPUT SANITIZATION
# ============================================================
def sanitize_text(value: str, max_length: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]

# ============================================================
# 10. FLASK + BOT SETUP
# ============================================================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

CORS(app, origins=[FRONTEND_URL], supports_credentials=False)

# ============================================================
# 11. SECURITY HEADERS
# ============================================================
@app.after_request
def add_security_headers(response):
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]          = "no-referrer"
    return response

# ============================================================
# 12. FLASK API ROUTES
# ============================================================

@app.route('/')
def home():
    return jsonify({"status": "ok", "message": "Bot is Running Live!"})


@app.route('/get_user/<int:user_id>')
def get_user_data_api(user_id):
    if is_rate_limited(f"getuser_{user_id}", 3):
        return jsonify({"status": "error", "message": "Too many requests. Slow down."}), 429
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404

        if user.get('blocked'):
            return jsonify({"status": "blocked"})

        today     = str(date.today())
        ads_date  = user.get('ads_date', '')
        ads_today = user.get('ads_today', 0) if ads_date == today else 0

        task_completions = user.get('task_completions', {})
        completed_today  = []
        for tid, info in task_completions.items():
            if isinstance(info, dict):
                if info.get('date') == today and info.get('code') == TASK_CODES.get(tid, ''):
                    completed_today.append(tid)

        return jsonify({
            "status":          "success",
            "coins":           user.get('coins', 0),
            "leaderboard":     get_leaderboard_cached(),
            "referrals":       get_referral_list(user_id),
            "completed_tasks": completed_today,
            "last_claim":      user.get('last_claim_ts', ""),
            "referred_by":     user.get('referred_by', ""),
            "ads_today":       ads_today,
            "ads_date":        ads_date,
            "channel_claims":  user.get('channel_claims', {}),
        })
    except Exception as e:
        logger.error(f"get_user error for {user_id}: {e}")
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


@app.route('/get_leaderboard')
def get_leaderboard_api():
    """Dedicated endpoint for leaderboard refresh — returns cached data."""
    return jsonify({"status": "success", "leaderboard": get_leaderboard_cached()})


@app.route('/claim_daily/<int:user_id>', methods=['POST'])
def claim_daily_api(user_id):
    if is_rate_limited(f"claim_{user_id}", 60):
        return jsonify({"status": "error", "message": "Please wait before trying again."}), 429
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get('blocked'):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        now     = datetime.utcnow()
        last_ts = user.get('last_claim_ts', "")
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                if now - last_dt < timedelta(hours=24):
                    remaining = timedelta(hours=24) - (now - last_dt)
                    h = int(remaining.total_seconds() // 3600)
                    m = int((remaining.total_seconds() % 3600) // 60)
                    return jsonify({"status": "error", "message": f"Already claimed! Come back in {h}h {m}m."}), 400
            except ValueError:
                pass

        users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"coins": 10}, "$set": {"last_claim_ts": now.isoformat()}}
        )
        return jsonify({"status": "success", "message": "10 coins credited to your account!", "data": {"bonus": 10}})
    except Exception as e:
        logger.error(f"claim_daily error for {user_id}: {e}")
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


@app.route('/withdraw', methods=['POST'])
def withdraw_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    user_id_raw      = data.get('user_id')
    upi_id           = sanitize_text(data.get('upi_id', ''))
    requested_amount = data.get('amount')

    if not user_id_raw or not upi_id or requested_amount is None:
        return jsonify({"status": "error", "message": "Missing required fields."}), 400

    try:
        user_id          = int(user_id_raw)
        requested_amount = int(requested_amount)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID or amount."}), 400

    if requested_amount <= 0:
        return jsonify({"status": "error", "message": "Amount cannot be zero or negative."}), 400
    if requested_amount < MIN_WITHDRAW:
        return jsonify({"status": "error", "message": f"Minimum withdrawal is {MIN_WITHDRAW} coins."}), 400
    if requested_amount > MAX_WITHDRAW:
        return jsonify({"status": "error", "message": "Amount exceeds maximum limit."}), 400

    upi_pattern = re.compile(r'^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$')
    if not upi_pattern.match(upi_id):
        return jsonify({"status": "error", "message": "Invalid UPI ID format. (Example: name@upi)"}), 400

    # 24-hour withdrawal cooldown (1 per day)
    if is_rate_limited(f"withdraw_{user_id}", WITHDRAW_COOLDOWN):
        return jsonify({"status": "error", "message": "One withdrawal request allowed per day. Please try again tomorrow."}), 429

    _ref_user = users_col.find_one({"user_id": user_id}, {"referral_count": 1, "_id": 0})
    ref_count = _ref_user.get("referral_count", 0) if _ref_user else 0
    if ref_count < 5:
        return jsonify({"status": "error", "message": f"You need {5 - ref_count} more referrals to withdraw."}), 400

    result = users_col.find_one_and_update(
        {"user_id": user_id, "coins": {"$gte": requested_amount}, "blocked": {"$ne": True}},
        {"$inc": {"coins": -requested_amount}},
        return_document=True
    )
    if result is None:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get('blocked'):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403
        return jsonify({"status": "error", "message": f"Insufficient balance. You have {user.get('coins', 0)} coins."}), 400

    withdrawal = {
        "user_id": user_id,
        "upi_id":  upi_id,
        "amount":  requested_amount,
        "status":  "Pending \u23f3",
        "date":    datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC")
    }
    withdrawals_col.insert_one(withdrawal)
    try:
        bot.send_message(
            ADMIN_ID,
            f"\U0001f4b8 *New Withdrawal Request*\n\n"
            f"User ID: `{user_id}`\n"
            f"UPI ID: `{upi_id}`\n"
            f"Requested: `{requested_amount}` coins\n"
            f"Remaining Balance: `{result.get('coins', 0)}` coins\n"
            f"Date: {withdrawal['date']}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Admin notify failed for withdrawal: {e}")

    return jsonify({"status": "success", "message": "Withdrawal request submitted successfully!"})


@app.route('/get_history/<int:user_id>')
def get_history_api(user_id):
    if is_rate_limited(f"history_{user_id}", 5):
        return jsonify({"status": "error", "message": "Please wait before refreshing."}), 429
    try:
        history = list(
            withdrawals_col.find({"user_id": user_id}, {"_id": 0}).sort("date", -1).limit(10)
        )
        return jsonify({"status": "success", "data": {"history": history}})
    except Exception as e:
        logger.error(f"get_history error for {user_id}: {e}")
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route('/verify_task', methods=['POST'])
def verify_task_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    task_id   = sanitize_text(data.get('task_id', '')).lower()
    user_code = sanitize_text(data.get('code', '')).upper()

    if not task_id or not user_code:
        return jsonify({"status": "error", "message": "Missing task ID or code."}), 400

    if task_id not in VALID_TASK_IDS:
        return jsonify({"status": "error", "message": "Invalid task ID."}), 400

    _u = users_col.find_one({"user_id": user_id}, {"blocked": 1})
    if _u and _u.get("blocked"):
        return jsonify({"status": "error", "message": "Your account has been suspended."}), 403

    if is_task_attempt_blocked(user_id, task_id):
        return jsonify({"status": "error", "message": f"Too many wrong attempts. Wait {TASK_FAIL_COOLDOWN // 60} minute(s) before retrying."}), 429

    if is_rate_limited(f"task_{user_id}_{task_id}", 10):
        return jsonify({"status": "error", "message": "Please wait 10 seconds before trying again."}), 429

    reward = TASK_REWARDS.get(task_id)
    if reward is None:
        return jsonify({"status": "error", "message": "Invalid task ID."}), 400

    correct_code = get_live_task_codes().get(task_id, "").upper()
    if user_code != correct_code:
        record_task_fail(user_id, task_id)
        return jsonify({"status": "error", "message": "Incorrect code! Please try again."}), 400

    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get('blocked'):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        today            = str(date.today())
        task_completions = user.get('task_completions', {})
        existing         = task_completions.get(task_id, {})

        if (isinstance(existing, dict)
                and existing.get('date') == today
                and existing.get('code') == correct_code):
            return jsonify({"status": "error", "message": "Task already completed today! Come back tomorrow."}), 400

        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": reward},
                "$set": {f"task_completions.{task_id}": {"date": today, "code": correct_code}}
            }
        )
        clear_task_fail_counter(user_id, task_id)
        return jsonify({"status": "success", "message": f"{reward} coins added to your balance!", "data": {"reward": reward}})
    except Exception as e:
        logger.error(f"verify_task error for {user_id}: {e}")
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route('/watch_ad/<int:user_id>', methods=['POST'])
def watch_ad_api(user_id):
    if is_rate_limited(f"ad_{user_id}", 30):
        return jsonify({"status": "error", "message": "Please wait 30 seconds before the next ad."}), 429
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get('blocked'):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        today     = str(date.today())
        ads_date  = user.get('ads_date', '')
        ads_today = user.get('ads_today', 0) if ads_date == today else 0

        if ads_today >= MAX_ADS_PER_DAY:
            return jsonify({
                "status":  "error",
                "message": f"Daily ad limit reached ({MAX_ADS_PER_DAY}/5). Come back tomorrow!"
            }), 400

        users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"coins": AD_COIN_REWARD}, "$set": {"ads_date": today, "ads_today": ads_today + 1}}
        )
        done      = ads_today + 1
        remaining = MAX_ADS_PER_DAY - done
        return jsonify({
            "status":  "success",
            "message": f"{AD_COIN_REWARD} coins earned! ({done}/{MAX_ADS_PER_DAY} ads today)",
            "data": {
                "ads_done":  done,
                "ads_total": MAX_ADS_PER_DAY,
                "remaining": remaining
            }
        })
    except Exception as e:
        logger.error(f"watch_ad error for {user_id}: {e}")
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route('/claim_channel', methods=['POST'])
def claim_channel_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    channel_id  = sanitize_text(data.get('channel_id', '')).lower()
    channel_url = sanitize_text(data.get('channel_url', ''), max_length=500)
    claimed_link = sanitize_text(data.get('claimed_link', ''), max_length=500)

    if channel_id not in CHANNEL_REWARDS:
        return jsonify({"status": "error", "message": "Invalid channel."}), 400

    if is_rate_limited(f"channel_{user_id}_{channel_id}", 10):
        return jsonify({"status": "error", "message": "Please wait a moment."}), 429

    _u = users_col.find_one({"user_id": user_id}, {"blocked": 1})
    if _u and _u.get("blocked"):
        return jsonify({"status": "error", "message": "Your account has been suspended."}), 403

    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get('blocked'):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        channel_claims = user.get('channel_claims', {})
        existing_claim = channel_claims.get(channel_id)

        # ── For sponsor slots (slot1, slot2): allow reclaim if link changed ──
        if channel_id in ('slot1', 'slot2'):
            if existing_claim and isinstance(existing_claim, dict):
                stored_link = existing_claim.get('claimed_link', '')
                # If same link was already claimed, reject
                if stored_link == claimed_link and stored_link != '':
                    return jsonify({"status": "error", "message": "Reward already claimed for this slot! ✅"}), 400
            elif existing_claim is True:
                # Old boolean format — treat as claimed (legacy)
                # But since link may have changed, we allow reclaim by not blocking here
                pass
        else:
            # For regular channels: one-time claim
            if existing_claim:
                return jsonify({"status": "error", "message": "Reward already claimed for this channel! ✅"}), 400

        # ── Verify user has actually joined the channel ──
        if channel_url:
            ch_username = extract_channel_username(channel_url)
            if ch_username:
                is_member = verify_channel_membership(ch_username, user_id)
                if not is_member:
                    return jsonify({
                        "status":  "not_joined",
                        "message": "Please join the channel first, then tap Retry!"
                    }), 400

        reward = CHANNEL_REWARDS[channel_id]

        # Store claim with link info for sponsor slots
        if channel_id in ('slot1', 'slot2'):
            claim_value = {"claimed_link": claimed_link, "claimed_at": datetime.utcnow().isoformat()}
        else:
            claim_value = True

        users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"coins": reward}, "$set": {f"channel_claims.{channel_id}": claim_value}}
        )
        return jsonify({
            "status":  "success",
            "message": f"{reward} coins credited for joining the channel!",
            "data":    {"reward": reward}
        })
    except Exception as e:
        logger.error(f"claim_channel error for {user_id}: {e}")
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route('/click_sponsor', methods=['POST'])
def click_sponsor_api():
    """
    Track unique user clicks on a sponsor slot link.
    Count resets automatically when the link changes.
    Stored temporarily in MongoDB (sponsor_clicks collection).
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "ok"})

    try:
        user_id = int(data.get('user_id', 0))
    except (TypeError, ValueError):
        return jsonify({"status": "ok"})

    slot_id  = sanitize_text(data.get('slot_id', ''), max_length=20).lower()
    link_url = sanitize_text(data.get('link_url', ''), max_length=500)

    if slot_id not in ('slot1', 'slot2', 'slot3') or not link_url:
        return jsonify({"status": "ok"})

    # Light rate-limit: 1 click tracked per user per slot per 10 min
    if is_rate_limited(f"sponsorclick_{user_id}_{slot_id}", 600):
        return jsonify({"status": "ok"})

    try:
        doc = sponsor_clicks_col.find_one({"_id": slot_id})

        if doc is None or doc.get('link_url') != link_url:
            # New link or first time — reset counter
            sponsor_clicks_col.replace_one(
                {"_id": slot_id},
                {
                    "_id":      slot_id,
                    "link_url": link_url,
                    "count":    1,
                    "users":    [user_id]
                },
                upsert=True
            )
        else:
            # Same link — increment if this user hasn't clicked before
            existing_users = doc.get('users', [])
            if user_id not in existing_users:
                sponsor_clicks_col.update_one(
                    {"_id": slot_id},
                    {
                        "$inc": {"count": 1},
                        # $push with $slice keeps array capped at 3000 entries max
                        "$push": {"users": {"$each": [user_id], "$slice": -3000}}
                    }
                )

        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"click_sponsor error: {e}")
        return jsonify({"status": "ok"})


@app.route('/check_device', methods=['POST'])
def check_device_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "ok"})

    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"status": "ok"})

    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip:
        ip = ip.split(',')[0].strip()

    fingerprint = sanitize_text(data.get('fingerprint', ''), max_length=128)

    try:
        current_user = users_col.find_one({"user_id": user_id})
        if not current_user:
            return jsonify({"status": "ok"})
        if current_user.get('blocked'):
            return jsonify({"status": "blocked"})

        ip_conflict = users_col.find_one({"ip": ip, "user_id": {"$ne": user_id}}) if ip else None
        fp_conflict = users_col.find_one({"fingerprint": fingerprint, "user_id": {"$ne": user_id}}) if fingerprint else None

        if ip_conflict:
            logger.warning(f"IP conflict: user {user_id} shares IP {ip} with another account.")
            users_col.update_one({"user_id": user_id}, {"$set": {"ip_flagged": True}})
            try:
                bot.send_message(
                    ADMIN_ID,
                    f"\u26a0\ufe0f *IP Conflict Detected*\nUser `{user_id}` shares IP with another account.\nIP: `{ip}`\n_Manual review recommended._",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Admin notify failed: {e}")

        if fp_conflict:
            users_col.update_one({"user_id": user_id}, {"$set": {"fp_flagged": True}})

        users_col.update_one(
            {"user_id": user_id},
            {"$set": {"fingerprint": fingerprint, "ip": ip}},
            upsert=False
        )
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"check_device error for {user_id}: {e}")
        return jsonify({"status": "ok"})


@app.route('/send_support', methods=['POST'])
def send_support_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    user_id_raw  = data.get('user_id')
    message_text = sanitize_text(data.get('message', ''), max_length=1000)

    if not user_id_raw:
        return jsonify({"status": "error", "message": "User ID is missing."}), 400
    if not message_text:
        return jsonify({"status": "error", "message": "Message cannot be empty."}), 400
    if len(message_text) > 1000:
        return jsonify({"status": "error", "message": "Message is too long (max 1000 characters)."}), 400

    try:
        user_id = int(user_id_raw)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    if is_rate_limited(f"support_req_{user_id}", 30):
        return jsonify({"status": "error", "message": "Please wait before sending another message."}), 429

    allowed, limit_msg = check_support_limit(user_id)
    if not allowed:
        return jsonify({"status": "error", "message": limit_msg}), 429

    try:
        users_col.update_one({"user_id": user_id}, {"$inc": {"support_count": 1}})
        bot.send_message(
            ADMIN_ID,
            f"\U0001f3a7 *Support Message*\nFrom: `{user_id}`\n\n{message_text}",
            parse_mode="Markdown"
        )
        return jsonify({"status": "success", "message": "Your message has been sent to Admin!"})
    except Exception as e:
        logger.error(f"send_support error for {user_id}: {e}")
        return jsonify({"status": "error", "message": "Failed to send message. Please try again."}), 500


# ============================================================
# 13. HELPER FUNCTIONS
# ============================================================

def get_leaderboard() -> str:
    try:
        top_users = list(
            users_col.find({}, {"user_id": 1, "coins": 1, "_id": 0}).sort("coins", -1).limit(10)
        )
        data = [f"{u['user_id']}:{u.get('coins', 0)}" for u in top_users]
        return "|".join(data) if data else "none"
    except Exception as e:
        logger.error(f"get_leaderboard error: {e}")
        return "none"


def get_referral_list(user_id: int) -> str:
    try:
        refs = list(users_col.find({"referred_by": str(user_id)}, {"user_id": 1, "_id": 0}))
        return ",".join(str(r['user_id']) for r in refs) if refs else ""
    except Exception as e:
        logger.error(f"get_referral_list error for {user_id}: {e}")
        return ""


def get_or_create_user(user_id: int, username: str, referrer_id=None):
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            new_user = {
                "user_id":              user_id,
                "username":             username,
                "coins":                0,
                "referred_by":          None,
                "referral_count":       0,
                "task_completions":     {},
                "channel_claims":       {},
                "last_claim_ts":        "",
                "ads_today":            0,
                "ads_date":             "",
                "support_count":        0,
                "support_window_start": "",
                "ip_flagged":           False,
                "fp_flagged":           False,
                "blocked":              False,
                "joined":               str(date.today())
            }
            if referrer_id and str(referrer_id) != str(user_id):
                referrer = users_col.find_one({"user_id": int(referrer_id)})
                if referrer:
                    # Increment both coins AND referral_count atomically
                    users_col.update_one(
                        {"user_id": int(referrer_id)},
                        {"$inc": {"coins": 50, "referral_count": 1}}
                    )
                    new_user["referred_by"] = str(referrer_id)
                    try:
                        bot.send_message(
                            int(referrer_id),
                            "\U0001f38a *Referral Bonus!*\n\nYou earned 50 coins for inviting a friend!",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"Referral notify failed: {e}")
            users_col.insert_one(new_user)
            return new_user
        return user
    except Exception as e:
        logger.error(f"get_or_create_user error for {user_id}: {e}")
        return {}

# ============================================================
# 14. BOT COMMANDS
# ============================================================

@bot.message_handler(commands=['start'])
def start(message):
    user_id     = message.from_user.id
    username    = message.from_user.first_name or "User"
    params      = message.text.split()
    referrer_id = params[1] if len(params) > 1 else None

    user          = get_or_create_user(user_id, username, referrer_id)
    current_coins = user.get('coins', 0)
    web_app_url   = f"https://sahdakshsanoj-byte.github.io/Earning-bot/?user_id={user_id}"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("\U0001f4b0 Open Earning Hub", web_app=types.WebAppInfo(web_app_url)))
    markup.add(types.InlineKeyboardButton(
        "\U0001f465 Invite Friends",
        url=f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start={user_id}&text=Join+and+earn+free+coins!"
    ))

    bot.send_message(
        user_id,
        f"\U0001f44b *Hello {username}!*\n\n"
        f"\U0001f4b0 Balance: *{current_coins} \U0001fa99*\n\n"
        f"Invite friends and earn *50 coins* for each referral!\n"
        f"Tap the button below to start earning! \U0001f680",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['balance'])
def check_balance(message):
    user = users_col.find_one({"user_id": message.from_user.id})
    if user:
        bot.reply_to(message, f"\U0001f4b0 Your balance: *{user.get('coins', 0)} \U0001fa99*", parse_mode="Markdown")
    else:
        bot.reply_to(message, "Please use /start to register first!")


@bot.message_handler(commands=['stats'])
def get_stats(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    total_u   = users_col.count_documents({})
    pending_w = withdrawals_col.count_documents({"status": "Pending \u23f3"})
    today_j   = users_col.count_documents({"joined": str(date.today())})
    bot.reply_to(
        message,
        f"\U0001f4ca *Bot Stats*\n\n"
        f"\U0001f465 Total Users: `{total_u}`\n"
        f"\U0001f195 Today Joined: `{today_j}`\n"
        f"\U0001f4b8 Pending Withdrawals: `{pending_w}`",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['approve'])
def approve_withdrawal(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /approve <user_id>")
    try:
        target_id = int(parts[1])
    except ValueError:
        return bot.reply_to(message, "Invalid user ID.")
    result = withdrawals_col.update_one(
        {"user_id": target_id, "status": "Pending \u23f3"},
        {"$set": {"status": "Approved \u2705"}}
    )
    if result.modified_count:
        try:
            bot.send_message(
                target_id,
                "\U0001f389 *Your withdrawal has been approved!* Payment is being processed. \u2705",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Notify failed for approved user {target_id}: {e}")
        bot.reply_to(message, f"\u2705 User {target_id} withdrawal approved!")
    else:
        bot.reply_to(message, "No pending withdrawal found.")


@bot.message_handler(commands=['reject'])
def reject_withdrawal(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /reject <user_id>")
    try:
        target_id = int(parts[1])
    except ValueError:
        return bot.reply_to(message, "Invalid user ID.")
    withdraw = withdrawals_col.find_one({"user_id": target_id, "status": "Pending \u23f3"})
    if withdraw:
        users_col.update_one({"user_id": target_id}, {"$inc": {"coins": withdraw['amount']}})
        withdrawals_col.update_one(
            {"user_id": target_id, "status": "Pending \u23f3"},
            {"$set": {"status": "Rejected \u274c"}}
        )
        try:
            bot.send_message(
                target_id,
                f"\u274c Your withdrawal was rejected. {withdraw['amount']} coins have been refunded.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Notify failed for rejected user {target_id}: {e}")
        bot.reply_to(message, f"\u274c User {target_id} rejected. Coins refunded.")
    else:
        bot.reply_to(message, "No pending withdrawal found.")


@bot.message_handler(commands=['addcoins'])
def add_coins(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 3:
        return bot.reply_to(message, "Usage: /addcoins <user_id> <amount>")
    try:
        target_id = int(parts[1])
        amount    = int(parts[2])
    except ValueError:
        return bot.reply_to(message, "Invalid user ID or amount.")
    users_col.update_one({"user_id": target_id}, {"$inc": {"coins": amount}})
    try:
        bot.send_message(target_id, f"\U0001f381 Admin has gifted you *{amount} coins*!", parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Notify failed for addcoins {target_id}: {e}")
    bot.reply_to(message, f"\u2705 {amount} coins added to user {target_id}")


@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    msg_text = message.text.replace('/broadcast ', '', 1)
    if not msg_text or msg_text == '/broadcast':
        return bot.reply_to(message, "Usage: /broadcast [Message]")
    all_users = list(users_col.find({}, {"user_id": 1}))
    sent, failed = 0, 0
    for u in all_users:
        try:
            bot.send_message(u['user_id'], msg_text, parse_mode="Markdown")
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
    bot.reply_to(message, f"\U0001f4e2 Sent: {sent} | Failed: {failed}")


@bot.message_handler(commands=['block'])
def block_user(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /block <user_id>")
    try:
        target_id = int(parts[1])
    except ValueError:
        return bot.reply_to(message, "Invalid user ID.")
    users_col.update_one(
        {"user_id": target_id},
        {"$set": {"blocked": True}}
    )
    try:
        bot.send_message(target_id, "\u26d4 Your account has been suspended for violating our terms of service.")
    except Exception as e:
        logger.warning(f"Notify failed for block {target_id}: {e}")
    bot.reply_to(message, f"\U0001f6ab User {target_id} blocked!")


@bot.message_handler(commands=['unblock'])
def unblock_user(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /unblock <user_id>")
    try:
        target_id = int(parts[1])
    except ValueError:
        return bot.reply_to(message, "Invalid user ID.")
    users_col.update_one(
        {"user_id": target_id},
        {"$set": {"blocked": False, "fp_flagged": False, "ip_flagged": False}}
    )
    try:
        bot.send_message(target_id, "\u2705 Your account has been unblocked!", parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Notify failed for unblock {target_id}: {e}")
    bot.reply_to(message, f"\u2705 User {target_id} unblocked!")


@bot.message_handler(commands=['settask'])
def set_task_code(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 3:
        return bot.reply_to(message, "Usage: /settask <task_id> <new_code>")
    task_id  = parts[1].lower()
    new_code = parts[2].upper()
    if task_id not in TASK_CODES:
        return bot.reply_to(message, f"Invalid task ID. Valid: {', '.join(TASK_CODES.keys())}")
    TASK_CODES[task_id] = new_code
    # Invalidate in-memory cache so the new code is served immediately
    global _task_codes_cache, _task_codes_cache_time
    _task_codes_cache      = None
    _task_codes_cache_time = 0.0
    bot.reply_to(
        message,
        f"\u2705 Task `{task_id}` code updated to `{new_code}`!",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['penalty'])
def penalize_user(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /penalty <user_id> [amount]\nDefault: 200 coins")
    try:
        target_id = int(parts[1])
        amount    = int(parts[2]) if len(parts) >= 3 else 200
        if amount <= 0:
            return bot.reply_to(message, "Amount must be greater than 0.")
    except ValueError:
        return bot.reply_to(message, "Invalid user ID or amount.")

    user = users_col.find_one({"user_id": target_id})
    if not user:
        return bot.reply_to(message, f"User {target_id} not found.")

    current = user.get('coins', 0)
    new_bal  = max(0, current - amount)
    deducted = current - new_bal
    users_col.update_one({"user_id": target_id}, {"$set": {"coins": new_bal}})
    try:
        bot.send_message(
            target_id,
            f"\u26a0\ufe0f *Penalty Applied!*\n\n"
            f"`{deducted}` coins deducted.\nNew Balance: `{new_bal}` \U0001fa99\n\nReason: Rule violation.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Notify failed for penalty {target_id}: {e}")
    bot.reply_to(
        message,
        f"\u26a0\ufe0f Penalty applied!\nUser: `{target_id}`\nDeducted: `{deducted}`\nNew Balance: `{new_bal}`",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['listblocked'])
def list_blocked(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    blocked = list(
        users_col.find({"blocked": True}, {"user_id": 1, "username": 1, "coins": 1, "_id": 0}).limit(20)
    )
    if not blocked:
        return bot.reply_to(message, "\u2705 No blocked users found.")
    lines = [f"\U0001f6ab *Blocked Users ({len(blocked)})*\n"]
    for u in blocked:
        lines.append(f"• `{u['user_id']}` — {u.get('username', 'Unknown')} — {u.get('coins', 0)} \U0001fa99")
    lines.append("\nUse /unblock <user\\_id> to unblock.")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")


@bot.message_handler(commands=['userinfo'])
def user_info(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /userinfo <user_id>")
    try:
        target_id = int(parts[1])
    except ValueError:
        return bot.reply_to(message, "Invalid user ID.")
    user = users_col.find_one({"user_id": target_id})
    if not user:
        return bot.reply_to(message, f"User {target_id} not found.")
    ref_count = users_col.count_documents({"referred_by": str(target_id)})
    if user.get('blocked'):
        status = "\U0001f6ab Blocked"
    elif user.get('ip_flagged') or user.get('fp_flagged'):
        status = "\u26a0\ufe0f Flagged"
    else:
        status = "\u2705 Active"
    bot.reply_to(
        message,
        f"\U0001f464 *User Info*\n\n"
        f"ID: `{target_id}`\n"
        f"Name: {user.get('username', 'Unknown')}\n"
        f"Balance: `{user.get('coins', 0)}` \U0001fa99\n"
        f"Referrals: `{ref_count}`\n"
        f"Referred By: `{user.get('referred_by', 'None')}`\n"
        f"Joined: `{user.get('joined', 'Unknown')}`\n"
        f"Status: {status}",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['adminpanel'])
def admin_panel(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    total_u   = users_col.count_documents({})
    pending_w = withdrawals_col.count_documents({"status": "Pending \u23f3"})
    blocked_u = users_col.count_documents({"blocked": True})
    today_j   = users_col.count_documents({"joined": str(date.today())})

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Full Stats",          callback_data="ap_stats"),
        types.InlineKeyboardButton("🚫 Blocked Users",       callback_data="ap_blocked"),
        types.InlineKeyboardButton("💸 Pending Withdrawals", callback_data="ap_pending"),
        types.InlineKeyboardButton("📢 Broadcast",           callback_data="ap_broadcast"),
    )
    bot.send_message(
        message.chat.id,
        f"\U0001f6e1\ufe0f *Admin Control Panel*\n\n"
        f"\U0001f465 Total Users: `{total_u}`\n"
        f"\U0001f195 Joined Today: `{today_j}`\n"
        f"\U0001f4b8 Pending Withdrawals: `{pending_w}`\n"
        f"\U0001f6ab Blocked Users: `{blocked_u}`\n\n"
        f"*Quick Commands:*\n"
        f"`/block <id>` — Block user\n"
        f"`/unblock <id>` — Unblock user\n"
        f"`/penalty <id> [amt]` — Deduct coins (default 200)\n"
        f"`/approve <id>` — Approve withdrawal\n"
        f"`/reject <id>` — Reject withdrawal\n"
        f"`/addcoins <id> <amt>` — Add coins\n"
        f"`/userinfo <id>` — View user details\n"
        f"`/listblocked` — List all blocked users\n"
        f"`/settask <task\\_id> <code>` — Update task code\n"
        f"`/broadcast <msg>` — Send message to all",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("ap_"))
def admin_panel_callback(call):
    if int(call.from_user.id) != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Access denied.")

    if call.data == "ap_stats":
        total_u   = users_col.count_documents({})
        pending_w = withdrawals_col.count_documents({"status": "Pending \u23f3"})
        approved  = withdrawals_col.count_documents({"status": "Approved \u2705"})
        rejected  = withdrawals_col.count_documents({"status": "Rejected \u274c"})
        today_j   = users_col.count_documents({"joined": str(date.today())})
        blocked   = users_col.count_documents({"blocked": True})
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"\U0001f4ca *Detailed Stats*\n\n"
            f"\U0001f465 Total Users: `{total_u}`\n"
            f"\U0001f195 Joined Today: `{today_j}`\n"
            f"\U0001f6ab Blocked: `{blocked}`\n\n"
            f"\U0001f4b8 Withdrawals:\n"
            f"  \u23f3 Pending: `{pending_w}`\n"
            f"  \u2705 Approved: `{approved}`\n"
            f"  \u274c Rejected: `{rejected}`",
            parse_mode="Markdown"
        )

    elif call.data == "ap_blocked":
        blocked_users = list(
            users_col.find({"blocked": True}, {"user_id": 1, "username": 1, "_id": 0}).limit(15)
        )
        bot.answer_callback_query(call.id)
        if not blocked_users:
            bot.send_message(call.message.chat.id, "\u2705 No blocked users.")
            return
        lines = [f"\U0001f6ab *Blocked Users ({len(blocked_users)})*\n"]
        for u in blocked_users:
            lines.append(f"• `{u['user_id']}` — {u.get('username', '?')}")
        lines.append("\nUse `/unblock <id>` to unblock.")
        bot.send_message(call.message.chat.id, "\n".join(lines), parse_mode="Markdown")

    elif call.data == "ap_pending":
        pending = list(withdrawals_col.find({"status": "Pending \u23f3"}).limit(10))
        bot.answer_callback_query(call.id)
        if not pending:
            bot.send_message(call.message.chat.id, "\u2705 No pending withdrawals.")
            return
        lines = [f"\U0001f4b8 *Pending Withdrawals ({len(pending)})*\n"]
        for w in pending:
            lines.append(
                f"• User `{w['user_id']}` — `{w['amount']}` coins — UPI: `{w.get('upi_id', '?')}`"
            )
        lines.append("\nUse `/approve <id>` or `/reject <id>`.")
        bot.send_message(call.message.chat.id, "\n".join(lines), parse_mode="Markdown")

    elif call.data == "ap_broadcast":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "Use: /broadcast <your message>")

# ============================================================
# 15. BOT POLLING THREAD
# ============================================================
def run_bot():
    logger.info("Starting Telegram bot polling...")
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            logger.error(f"Bot polling crashed: {e}. Restarting in 5s...")
            time.sleep(5)

# ============================================================
# 16. ADMIN PANEL ROUTES
# ============================================================
@app.route('/admin')
def admin_panel_page():
    return send_file('admin.html')

@app.route('/admin/login', methods=['POST'])
def admin_login():
    data  = request.json or {}
    token = (data.get('token') or '').strip()
    if token == ADMIN_TOKEN and ADMIN_TOKEN != '':
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Invalid token"}), 401

@app.route('/admin/get_config', methods=['GET'])
def admin_get_config():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    return jsonify({"status": "success", "task_codes": get_live_task_codes()})

@app.route('/admin/update_codes', methods=['POST'])
def admin_update_codes():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data  = request.json or {}
    codes = {k: str(v).strip().upper() for k, v in data.get("codes", {}).items() if v}
    if not codes:
        return jsonify({"status": "error", "message": "No codes provided"}), 400
    config_col.update_one({"_id": "task_codes"}, {"$set": {"codes": codes}}, upsert=True)
    logger.info(f"Admin updated task codes: {list(codes.keys())}")
    return jsonify({"status": "success", "message": "Codes updated!"})

@app.route('/admin/withdrawals', methods=['GET'])
def admin_withdrawals():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    pending = list(withdrawals_col.find({"status": "Pending \u23f3"}, {"_id": 0}).sort("date", -1))
    return jsonify({"status": "success", "withdrawals": pending})

@app.route('/admin/update_withdrawal', methods=['POST'])
def admin_update_withdrawal():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data    = request.json or {}
    user_id = data.get("user_id")
    action  = data.get("action")
    if not user_id or action not in ("approve", "reject"):
        return jsonify({"status": "error", "message": "Invalid request"}), 400
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    wd = withdrawals_col.find_one({"user_id": uid, "status": "Pending \u23f3"})
    if not wd:
        return jsonify({"status": "error", "message": "No pending withdrawal found"}), 404
    if action == "approve":
        withdrawals_col.update_one(
            {"user_id": uid, "status": "Pending \u23f3"},
            {"$set": {"status": "Approved \u2705"}}
        )
        try:
            bot.send_message(uid, f"\U0001f389 Your withdrawal of {wd.get('amount', 0)} coins has been approved!\n\U0001f4b8 Payment will be sent to your UPI shortly.")
        except Exception:
            pass
        logger.info(f"Admin approved withdrawal for user {uid}")
    else:
        withdrawals_col.update_one(
            {"user_id": uid, "status": "Pending \u23f3"},
            {"$set": {"status": "Rejected \u274c"}}
        )
        users_col.update_one({"user_id": uid}, {"$inc": {"coins": wd.get("amount", 0)}})
        try:
            bot.send_message(uid, f"\u274c Your withdrawal of {wd.get('amount', 0)} coins was rejected.\n\U0001fa99 Coins have been refunded to your account.")
        except Exception:
            pass
        logger.info(f"Admin rejected withdrawal for user {uid}, refunded {wd.get('amount', 0)} coins")
    return jsonify({"status": "success"})

_stats_cache      = {}
_stats_cache_time = 0.0
STATS_CACHE_TTL   = 60  # seconds


@app.route('/admin/stats', methods=['GET'])
def admin_stats():
    global _stats_cache, _stats_cache_time
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    now = time.time()
    if _stats_cache and now - _stats_cache_time < STATS_CACHE_TTL:
        return jsonify(_stats_cache)
    total_users = users_col.count_documents({})
    pending     = withdrawals_col.count_documents({"status": "Pending \u23f3"})
    approved    = withdrawals_col.count_documents({"status": "Approved \u2705"})
    coins_agg   = list(users_col.aggregate([{"$group": {"_id": None, "total": {"$sum": "$coins"}}}]))
    total_coins = coins_agg[0]["total"] if coins_agg else 0
    _stats_cache = {"status": "success", "total_users": total_users, "pending": pending, "approved": approved, "total_coins": total_coins}
    _stats_cache_time = now
    return jsonify(_stats_cache)

@app.route('/admin/search_user', methods=['GET'])
def admin_search_user():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        uid = int(request.args.get('user_id', 0))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    user = users_col.find_one({"user_id": uid}, {"_id": 0})
    if not user:
        return jsonify({"status": "error", "message": "User not found"}), 404
    return jsonify({"status": "success", "user": user})

@app.route('/admin/ban_user', methods=['POST'])
def admin_ban_user():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        uid = int(data.get('user_id', 0))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    users_col.update_one({"user_id": uid}, {"$set": {"blocked": True}})
    try:
        bot.send_message(uid, "\u26d4 Your account has been suspended for violating our terms of service.")
    except Exception:
        pass
    logger.info(f"Admin banned user {uid}")
    return jsonify({"status": "success", "message": f"User {uid} banned"})

@app.route('/admin/unban_user', methods=['POST'])
def admin_unban_user():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        uid = int(data.get('user_id', 0))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    users_col.update_one({"user_id": uid}, {"$set": {"blocked": False, "fp_flagged": False, "ip_flagged": False}})
    try:
        bot.send_message(uid, "\u2705 Your account has been reinstated. Welcome back!")
    except Exception:
        pass
    logger.info(f"Admin unbanned user {uid}")
    return jsonify({"status": "success", "message": f"User {uid} unbanned"})

@app.route('/admin/sponsor_clicks', methods=['GET'])
def admin_sponsor_clicks():
    """Returns click counts for all sponsor slots (slot1, slot2, slot3)."""
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        docs = list(sponsor_clicks_col.find({}, {"_id": 1, "link_url": 1, "count": 1}))
        result = {}
        for d in docs:
            result[d['_id']] = {
                "count":    d.get('count', 0),
                "link_url": d.get('link_url', '')
            }
        return jsonify({"status": "success", "clicks": result})
    except Exception as e:
        logger.error(f"admin_sponsor_clicks error: {e}")
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route('/admin/list_banned', methods=['GET'])
def admin_list_banned():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    banned = list(
        users_col.find({"blocked": True}, {"user_id": 1, "username": 1, "coins": 1, "_id": 0}).limit(50)
    )
    return jsonify({"status": "success", "banned_users": banned})

@app.route('/admin/send_dm', methods=['POST'])
def admin_send_dm():
    """Send a direct Telegram message to a specific user."""
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data    = request.json or {}
    try:
        uid = int(data.get("user_id", 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400
    msg = (data.get("message") or "").strip()
    if not uid or not msg:
        return jsonify({"status": "error", "message": "User ID aur message required hai."}), 400
    user = users_col.find_one({"user_id": uid}, {"user_id": 1})
    if not user:
        return jsonify({"status": "error", "message": f"User {uid} bot mein registered nahi hai."}), 404
    try:
        bot.send_message(uid, msg, parse_mode="Markdown")
        logger.info(f"Admin sent DM to {uid}")
        return jsonify({"status": "success", "message": f"Message user {uid} ko bhej diya gaya!"})
    except Exception as e:
        logger.error(f"admin_send_dm error for {uid}: {e}")
        err_str = str(e)
        if "bot was blocked" in err_str:
            return jsonify({"status": "error", "message": "User ne bot ko block kar rakha hai."}), 400
        if "user is deactivated" in err_str:
            return jsonify({"status": "error", "message": "User ka account deactivated hai."}), 400
        return jsonify({"status": "error", "message": "Message send nahi ho saka. Telegram error."}), 500


def _do_broadcast(msg: str):
    """Runs broadcast in a background thread with delays to avoid flooding Telegram API."""
    user_ids = [u["user_id"] for u in users_col.find({}, {"user_id": 1})]
    sent = failed = 0
    for uid in user_ids:
        try:
            bot.send_message(uid, msg, parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1
        time.sleep(0.05)  # 20 msgs/sec — safe for Telegram rate limits
    logger.info(f"Broadcast complete: {sent} sent, {failed} failed")


@app.route('/admin/broadcast', methods=['POST'])
def admin_broadcast():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    msg  = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"status": "error", "message": "Empty message"}), 400
    total = users_col.count_documents({})
    Thread(target=_do_broadcast, args=(msg,), daemon=True).start()
    return jsonify({"status": "success", "message": f"Broadcast started for ~{total} users. Sends in background."})

# ============================================================
# 17. UPTIME PING (for UptimeRobot)
# ============================================================
def uptime_ping():
    import requests as req_lib
    if not RENDER_URL:
        return
    while True:
        try:
            req_lib.get(RENDER_URL, timeout=10)
            logger.debug("Uptime ping sent.")
        except Exception as e:
            logger.warning(f"Uptime ping failed: {e}")
        time.sleep(600)

# ============================================================
# 18. ENTRY POINT
# ============================================================
if __name__ == '__main__':
    Thread(target=run_bot, daemon=True).start()
    Thread(target=uptime_ping, daemon=True).start()
    Thread(target=refresh_leaderboard_loop, daemon=True).start()
    port = int(os.getenv("PORT", 5000))
    logger.info(f"Starting Flask on port {port}...")
    app.run(host='0.0.0.0', port=port)
