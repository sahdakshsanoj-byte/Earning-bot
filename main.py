import telebot
import os
import re
import hmac
import hashlib
import logging
import pymongo
import time
from urllib.parse import parse_qsl
from flask import Flask, jsonify, request
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
    support_col     = db['support']
    rate_col        = db['rate_limits']
    try:
        rate_col.create_index("expires_at", expireAfterSeconds=0)
        logger.info("TTL index created on rate_limits.expires_at")
    except Exception as idx_err:
        logger.warning(f"TTL index creation skipped (may already exist): {idx_err}")
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
    "sponsor1": 10,
}

MAX_ADS_PER_DAY      = 5
AD_COIN_REWARD       = 10
MIN_WITHDRAW         = 4000
MAX_WITHDRAW         = 100000
WITHDRAW_COOLDOWN    = 10800   # 3 hours
SUPPORT_MAX_MSGS     = 2
SUPPORT_WINDOW_HRS   = 6
TASK_FAIL_COOLDOWN   = 60      # 1 min cooldown after 3 wrong attempts
TASK_MAX_FAILS       = 3       # max wrong attempts before cooldown

# Allowed task IDs whitelist (for validation)
VALID_TASK_IDS = set(TASK_CODES.keys())

# ============================================================
# 5. TELEGRAM INIT DATA VERIFICATION
# ============================================================
def verify_telegram_init_data(init_data: str) -> dict | None:
    """
    Verifies Telegram Mini App initData using HMAC-SHA256.
    Returns parsed user dict on success, None on failure.
    """
    if not init_data:
        return None
    try:
        params = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = params.pop("hash", None)
        if not received_hash:
            return None

        # Build data-check-string: sorted key=value pairs joined by \n
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(params.items())
        )

        # Secret key = HMAC-SHA256("WebAppData", BOT_TOKEN)
        secret_key = hmac.new(
            b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
        ).digest()
        computed = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed, received_hash):
            return None

        # Optionally verify auth_date not too old (10 min tolerance)
        auth_date = int(params.get("auth_date", 0))
        if time.time() - auth_date > 600:
            return None

        return params
    except Exception as e:
        logger.warning(f"initData verification failed: {e}")
        return None


def get_verified_user_id(request_data: dict) -> int | None:
    """
    Extract and verify user_id from request.
    Accepts either JSON body with init_data or just user_id (fallback for testing).
    """
    init_data = request_data.get("init_data", "")
    if init_data:
        params = verify_telegram_init_data(init_data)
        if params is None:
            return None
        # Extract user_id from user param in initData
        import json
        user_str = params.get("user", "{}")
        try:
            user_obj = json.loads(user_str)
            return int(user_obj.get("id", 0)) or None
        except Exception:
            return None
    # Fallback: trust user_id from body (less secure, but keeps compatibility)
    raw = request_data.get("user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None

# ============================================================
# 6. RATE LIMITING (MongoDB-backed, restart-safe)
# ============================================================
def is_rate_limited(key: str, cooldown_seconds: int) -> bool:
    """Returns True if key is still in cooldown."""
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
        return False  # Fail open on DB error so users aren't locked out


def check_support_limit(user_id: int):
    """Returns (allowed: bool, message: str). 2 messages per 6 hours."""
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
        return False, f"Message limit reached ({SUPPORT_MAX_MSGS}/{SUPPORT_MAX_MSGS}). Try again in {h}h {m}m."

    return True, ""

# ============================================================
# 7. TASK ATTEMPT TRACKING
# ============================================================
def is_task_attempt_blocked(user_id: int, task_id: str) -> bool:
    """
    Track wrong code attempts per user per task.
    After TASK_MAX_FAILS wrong attempts, block for TASK_FAIL_COOLDOWN seconds.
    """
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
    """Increment wrong attempt counter; block if threshold reached."""
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
            # Set block
            rate_col.update_one(
                {"_id": block_key},
                {"$set": {"expires_at": now + timedelta(seconds=TASK_FAIL_COOLDOWN)}},
                upsert=True
            )
            # Reset counter
            rate_col.delete_one({"_id": counter_key})
    except Exception as e:
        logger.error(f"Record task fail error: {e}")


def clear_task_fail_counter(user_id: int, task_id: str):
    """Clear fail counter on successful verify."""
    counter_key = f"task_fail_{user_id}_{task_id}"
    try:
        rate_col.delete_one({"_id": counter_key})
    except Exception as e:
        logger.error(f"Clear task fail error: {e}")

# ============================================================
# 8. INPUT SANITIZATION
# ============================================================
def sanitize_text(value: str, max_length: int = 1000) -> str:
    """Strip whitespace and limit length."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]

# ============================================================
# 9. FLASK + BOT SETUP
# ============================================================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# CORS — only allow your frontend origin
CORS(app, origins=[FRONTEND_URL], supports_credentials=False)

# ============================================================
# 10. SECURITY HEADERS (applied to every response)
# ============================================================
@app.after_request
def add_security_headers(response):
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]          = "no-referrer"
    return response

# ============================================================
# 11. FLASK API ROUTES
# ============================================================

@app.route('/')
def home():
    return jsonify({"status": "ok", "message": "Bot is Running Live!"})


@app.route('/get_user/<int:user_id>')
def get_user_data_api(user_id):
    # Basic per-user rate limit on data fetch
    if is_rate_limited(f"getuser_{user_id}", 3):
        return jsonify({"status": "error", "message": "Too many requests. Slow down."}), 429
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404

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
            "leaderboard":     get_leaderboard(),
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


@app.route('/claim_daily/<int:user_id>', methods=['POST'])
def claim_daily_api(user_id):
    # Rate limit: one call per minute max
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

    # Backend amount validation
    if requested_amount <= 0:
        return jsonify({"status": "error", "message": "Amount cannot be zero or negative."}), 400
    if requested_amount < MIN_WITHDRAW:
        return jsonify({"status": "error", "message": f"Minimum withdrawal is {MIN_WITHDRAW} coins."}), 400
    if requested_amount > MAX_WITHDRAW:
        return jsonify({"status": "error", "message": "Amount exceeds maximum limit."}), 400

    # UPI ID format validation
    upi_pattern = re.compile(r'^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$')
    if not upi_pattern.match(upi_id):
        return jsonify({"status": "error", "message": "Invalid UPI ID format. (Example: name@upi)"}), 400

    # 3-hour withdrawal cooldown
    if is_rate_limited(f"withdraw_{user_id}", WITHDRAW_COOLDOWN):
        return jsonify({"status": "error", "message": "One withdrawal request allowed every 3 hours."}), 429

    # Referral requirement
    ref_count = users_col.count_documents({"referred_by": str(user_id)})
    if ref_count < 5:
        return jsonify({"status": "error", "message": f"You need {5 - ref_count} more referrals to withdraw."}), 400

    # Atomic deduction — only if user has enough coins and is not blocked
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

    # Whitelist task IDs
    if task_id not in VALID_TASK_IDS:
        return jsonify({"status": "error", "message": "Invalid task ID."}), 400

    # Check if user is in cooldown due to too many wrong attempts
    if is_task_attempt_blocked(user_id, task_id):
        return jsonify({"status": "error", "message": f"Too many wrong attempts. Wait {TASK_FAIL_COOLDOWN // 60} minute(s) before retrying."}), 429

    # Per-request rate limit (10s)
    if is_rate_limited(f"task_{user_id}_{task_id}", 10):
        return jsonify({"status": "error", "message": "Please wait 10 seconds before trying again."}), 429

    reward = TASK_REWARDS.get(task_id)
    if reward is None:
        return jsonify({"status": "error", "message": "Invalid task ID."}), 400

    correct_code = TASK_CODES.get(task_id, "").upper()
    if user_code != correct_code:
        # Record wrong attempt
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
        # Clear fail counter on success
        clear_task_fail_counter(user_id, task_id)
        return jsonify({"status": "success", "message": f"{reward} coins added to your balance!", "data": {"reward": reward}})
    except Exception as e:
        logger.error(f"verify_task error for {user_id}: {e}")
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route('/watch_ad/<int:user_id>', methods=['POST'])
def watch_ad_api(user_id):
    # 30s cooldown between ads
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

    channel_id = sanitize_text(data.get('channel_id', '')).lower()

    # Whitelist check
    if channel_id not in CHANNEL_REWARDS:
        return jsonify({"status": "error", "message": "Invalid channel."}), 400

    if is_rate_limited(f"channel_{user_id}_{channel_id}", 10):
        return jsonify({"status": "error", "message": "Please wait a moment."}), 429

    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get('blocked'):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        channel_claims = user.get('channel_claims', {})
        if channel_claims.get(channel_id):
            return jsonify({"status": "error", "message": "Reward already claimed for this channel! \u2705"}), 400

        reward = CHANNEL_REWARDS[channel_id]
        users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"coins": reward}, "$set": {f"channel_claims.{channel_id}": True}}
        )
        return jsonify({
            "status":  "success",
            "message": f"{reward} coins credited for joining the channel!",
            "data":    {"reward": reward}
        })
    except Exception as e:
        logger.error(f"claim_channel error for {user_id}: {e}")
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route('/check_device', methods=['POST'])
def check_device_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "ok"})

    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"status": "ok"})

    # Extract IP (use X-Forwarded-For if behind proxy)
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

        # Soft IP check — flag only, don't hard-block
        ip_conflict = users_col.find_one({"ip": ip, "user_id": {"$ne": user_id}}) if ip else None
        fp_conflict = users_col.find_one({"fingerprint": fingerprint, "user_id": {"$ne": user_id}}) if fingerprint else None

        if ip_conflict:
            # Soft flag — log and notify admin, but don't auto-block
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

        # Update stored IP and fingerprint
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

    # Rate limit support messages
    if is_rate_limited(f"support_req_{user_id}", 30):
        return jsonify({"status": "error", "message": "Please wait before sending another message."}), 429

    allowed, limit_msg = check_support_limit(user_id)
    if not allowed:
        return jsonify({"status": "error", "message": limit_msg}), 429

    try:
        support_col.insert_one({
            "user_id": user_id,
            "message": message_text,
            "date":    datetime.utcnow().isoformat()
        })
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
# 12. HELPER FUNCTIONS
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
                    users_col.update_one({"user_id": int(referrer_id)}, {"$inc": {"coins": 50}})
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
# 13. BOT COMMANDS
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
    bot.reply_to(
        message,
        f"\u2705 Task `{task_id}` code updated to `{new_code}` — task reset for all users!",
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
# 14. BOT POLLING THREAD
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
# 15. UPTIME PING (for UptimeRobot)
# ============================================================
def uptime_ping():
    if not RENDER_URL:
        return
    while True:
        try:
            requests.get(RENDER_URL, timeout=10)
            logger.debug("Uptime ping sent.")
        except Exception as e:
            logger.warning(f"Uptime ping failed: {e}")
        time.sleep(600)  # ping every 10 minutes

# ============================================================
# 16. ENTRY POINT
# ============================================================
if __name__ == '__main__':
    # Start bot polling in background
    Thread(target=run_bot, daemon=True).start()
    # Start uptime ping in background
    Thread(target=uptime_ping, daemon=True).start()
    port = int(os.getenv("PORT", 5000))
    logger.info(f"Starting Flask on port {port}...")
    app.run(host='0.0.0.0', port=port)
