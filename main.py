"""main.py — Daksh Grand Earn Bot
================================
Telegram earning bot with Flask API backend.
Runs on Render with MongoDB as the database.
Uses pyTelegramBotAPI (telebot) for Telegram integration.

Environment Variables Required:
    BOT_TOKEN    : Telegram Bot Token
    MONGO_URI    : MongoDB connection string
    ADMIN_ID     : Telegram user ID of the admin
    BOT_USERNAME : Bot username (without @)
    RENDER_URL   : Render deploy URL (for uptime ping)
    FRONTEND_URL : Frontend GitHub Pages URL
    ADMIN_TOKEN  : Secret token for Admin Panel API

Coin Economy:
    5000 coins = ₹100
    1 coin = ₹0.02
"""

import hmac
import hashlib
import json
import logging
import os
import re
import secrets
import time
import threading
from datetime import date, datetime, timedelta
from threading import Thread
from urllib.parse import parse_qsl, quote

import pymongo
import requests as req_lib
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from telebot import types
import telebot


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

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
MONGO_URI = (os.getenv("MONGO_URI") or "").strip()
ADMIN_ID_STR = (os.getenv("ADMIN_ID") or "").strip()
BOT_USERNAME = (os.getenv("BOT_USERNAME") or "YourBotUsername").strip()
RENDER_URL = (os.getenv("RENDER_URL") or "").strip()
FRONTEND_URL = (os.getenv("FRONTEND_URL") or "https://sahdakshsanoj-byte.github.io").strip()
ADMIN_TOKEN    = (os.getenv("ADMIN_TOKEN")    or "").strip()
MOD_TOKEN      = (os.getenv("MOD_TOKEN")      or "").strip()
WEBHOOK_SECRET = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32] if BOT_TOKEN else ""

if not BOT_TOKEN:
    raise EnvironmentError("FATAL: BOT_TOKEN environment variable is not set!")
if not MONGO_URI:
    raise EnvironmentError("FATAL: MONGO_URI environment variable is not set!")
if not ADMIN_ID_STR:
    raise EnvironmentError("FATAL: ADMIN_ID environment variable is not set!")

try:
    ADMIN_ID = int(ADMIN_ID_STR)
except ValueError as exc:
    raise EnvironmentError(
        f"FATAL: ADMIN_ID '{ADMIN_ID_STR}' is not a valid integer!"
    ) from exc


# ============================================================
# 3. DATABASE CONNECTION
# ============================================================

try:
    client = pymongo.MongoClient(
        MONGO_URI,
        maxPoolSize=50,
        serverSelectionTimeoutMS=5000,
        w=1,
    )
    db = client["earning_bot_db"]

    users_col = db["users"]
    withdrawals_col = db["withdrawals"]
    rate_col = db["rate_limits"]
    config_col = db["config"]
    sponsor_clicks_col   = db["sponsor_clicks"]
    promos_col           = db["promos"]
    support_messages_col = db["support_messages"]
    ad_reward_tokens_col = db["ad_reward_tokens"]
    code_filter_rules_col = db["code_filter_rules"]
    group_code_violations_col = db["group_code_violations"]
    promo_tasks_col = db["promo_tasks"]

    try:
        rate_col.create_index("expires_at", expireAfterSeconds=0)
        ad_reward_tokens_col.create_index("expires_at", expireAfterSeconds=0)
        logger.info("TTL index ensured on rate_limits.expires_at")
    except Exception as idx_err:
        logger.warning("TTL index creation skipped (may already exist): %s", idx_err)

    _idx_opts = {"background": True}
    try:
        users_col.create_index("user_id", unique=True, **_idx_opts)
        users_col.create_index("referred_by", **_idx_opts)
        users_col.create_index("coins", **_idx_opts)
        users_col.create_index("ip", sparse=True, **_idx_opts)
        users_col.create_index("fingerprint", sparse=True, **_idx_opts)
        users_col.create_index("blocked", sparse=True, **_idx_opts)
        users_col.create_index("joined", **_idx_opts)
        withdrawals_col.create_index("user_id", **_idx_opts)
        withdrawals_col.create_index("status", **_idx_opts)
        promos_col.create_index("code", unique=True, **_idx_opts)
        support_messages_col.create_index("user_id", **_idx_opts)
        support_messages_col.create_index("created_at", **_idx_opts)
        code_filter_rules_col.create_index("pattern", unique=True, **_idx_opts)
        group_code_violations_col.create_index([("chat_id", 1), ("user_id", 1)], unique=True, **_idx_opts)
        promo_tasks_col.create_index("task_id", unique=True, **_idx_opts)
        logger.info("MongoDB indexes ensured.")
    except Exception as idx_err:
        logger.warning("Index creation warning: %s", idx_err)

    logger.info("MongoDB connected successfully.")
except Exception as db_exc:
    logger.error("MongoDB connection failed: %s", db_exc)
    raise


# ============================================================
# 4. CONSTANTS  (UPDATED coin economy)
# ============================================================

# Coin conversion: 5000 coins = ₹100,  1 coin = ₹0.02

TASK_CODES = {
    "yt1": "CODE1",
    "yt2": "CODE2",
    "yt3": "CODE3",
    "web1": "DASH98",
    "web2": "GYM567",
    "web3": "SHU234",
}

# Updated rewards per spec
TASK_REWARDS = {
    "yt1": 5,    # YouTube tasks: 5 coins each
    "yt2": 5,
    "yt3": 5,
    "web1": 3,   # Website/link tasks: 3 coins each
    "web2": 3,
    "web3": 3,
}

# Daily limits per task type
MAX_YT_TASKS_PER_DAY = 3      # max 3 YouTube tasks per day
MAX_WEB_TASKS_PER_DAY = 3     # max 3 website tasks per day

# Telegram channel one-time rewards (5 coins each, reward only after ALL 3 joined)
CHANNEL_IDS = ["official", "channel2", "channel3"]
CHANNEL_REWARD_PER_CHANNEL = 5
CHANNEL_TOTAL_REWARD = 15     # 5 * 3 channels, credited once after all joined

# Ads
MAX_ADS_PER_DAY = 10           # 8-10 ads/day limit
AD_COIN_REWARD = 5             # 5 coins per ad completion
AD_CLAIM_TOKEN_TTL_SECONDS = 300
AD_MIN_PER_DAY = 8             # informational minimum shown to user

# Promotion tasks: fixed 5 coins, admin-managed, one-time per user per task
PROMO_TASK_REWARD = 5

# All-tasks-complete daily bonus
ALL_TASKS_BONUS = 10           # Extra 10 coins for completing all daily tasks

# Withdrawal
MIN_WITHDRAW = 5000            # 5000 coins = ₹100
MAX_WITHDRAW = 100000
WITHDRAW_COOLDOWN = 86400      # 24 hours

SUPPORT_MAX_MSGS = 1
SUPPORT_WINDOW_HRS = 24
TASK_FAIL_COOLDOWN = 60
TASK_MAX_FAILS = 3

VALID_TASK_IDS = set(TASK_CODES.keys())

GROUP_CODE_FILTER_ENABLED = True
GROUP_CODE_MAX_VIOLATIONS = 3
GROUP_CODE_VIOLATION_WINDOW_HOURS = 24
DEFAULT_GROUP_CODE_PATTERNS = [
    r"\b[A-Z]{3}\b",
    r"\b\d{3}\b",
]


# ============================================================
# 5. IN-MEMORY RATE LIMIT CACHE
# ============================================================

_rate_cache: dict = {}
_rate_cache_lock = threading.Lock()


def is_rate_limited(key: str, cooldown_seconds: int) -> bool:
    """Check and set a rate limit using an in-memory TTL cache."""
    now = time.time()
    with _rate_cache_lock:
        expires = _rate_cache.get(key, 0.0)
        if expires > now:
            return True
        _rate_cache[key] = now + cooldown_seconds
        return False


def _cleanup_rate_cache() -> None:
    """Background thread: remove expired entries from the rate cache every 5 min."""
    while True:
        time.sleep(300)
        now = time.time()
        with _rate_cache_lock:
            expired = [k for k, v in _rate_cache.items() if v <= now]
            for k in expired:
                del _rate_cache[k]
        if expired:
            logger.debug("Rate cache cleanup: removed %d expired keys.", len(expired))


# ============================================================
# 6. LEADERBOARD CACHE
# ============================================================

_leaderboard_cache = "none"
_leaderboard_cache_time = 0.0
LEADERBOARD_TTL = 600


def get_leaderboard_cached() -> str:
    global _leaderboard_cache, _leaderboard_cache_time
    now = time.time()
    if now - _leaderboard_cache_time < LEADERBOARD_TTL:
        return _leaderboard_cache
    _leaderboard_cache = get_leaderboard()
    _leaderboard_cache_time = now
    return _leaderboard_cache


def refresh_leaderboard_loop() -> None:
    global _leaderboard_cache, _leaderboard_cache_time
    while True:
        time.sleep(LEADERBOARD_TTL)
        try:
            _leaderboard_cache = get_leaderboard()
            _leaderboard_cache_time = time.time()
            logger.info("Leaderboard cache refreshed.")
        except Exception as exc:
            logger.error("Leaderboard refresh error: %s", exc)


# ============================================================
# 6b. TASK CODES CACHE
# ============================================================

_task_codes_cache = None
_task_codes_cache_time = 0.0
TASK_CODES_CACHE_TTL = 300


def get_live_task_codes() -> dict:
    """Return task verification codes from MongoDB with 5-minute cache."""
    global _task_codes_cache, _task_codes_cache_time
    now = time.time()
    if _task_codes_cache is not None and now - _task_codes_cache_time < TASK_CODES_CACHE_TTL:
        return _task_codes_cache
    try:
        cfg = config_col.find_one({"_id": "task_codes"})
        if cfg and cfg.get("codes"):
            _task_codes_cache = cfg["codes"]
            _task_codes_cache_time = now
            return _task_codes_cache
    except Exception:
        pass
    _task_codes_cache = TASK_CODES
    _task_codes_cache_time = now
    return TASK_CODES


# ============================================================
# 7. ADMIN TOKEN HELPER
# ============================================================

def check_admin_token(req) -> bool:
    return (
        req.headers.get("X-Admin-Token", "").strip() == ADMIN_TOKEN
        and ADMIN_TOKEN != ""
    )


# ============================================================
# 8. TELEGRAM INIT DATA VERIFICATION
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
    except Exception as exc:
        logger.warning("initData verification failed: %s", exc)
        return None


def get_verified_user_id(request_data: dict) -> int | None:
    init_data = request_data.get("init_data", "")
    if init_data:
        params = verify_telegram_init_data(init_data)
        if params is None:
            return None
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
# 9. RATE LIMITING (MongoDB-backed for persistence)
# ============================================================

def check_support_limit(user_id: int) -> tuple[bool, str]:
    now = datetime.utcnow()
    user = users_col.find_one(
        {"user_id": user_id},
        {"support_window_start": 1, "support_count": 1},
    )
    if not user:
        return False, "User not found."

    window_start_str = user.get("support_window_start", "")
    count = user.get("support_count", 0)
    window_expired = True

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
            {"$set": {"support_window_start": now.isoformat(), "support_count": 0}},
        )
        return True, ""

    if count >= SUPPORT_MAX_MSGS:
        start_dt = datetime.fromisoformat(window_start_str)
        remaining = timedelta(hours=SUPPORT_WINDOW_HRS) - (now - start_dt)
        h = int(remaining.total_seconds() // 3600)
        m = int((remaining.total_seconds() % 3600) // 60)
        return False, f"Daily message limit reached. Try again in {h}h {m}m."

    return True, ""


# ============================================================
# 10. TASK ATTEMPT TRACKING
# ============================================================

def is_task_attempt_blocked(user_id: int, task_id: str) -> bool:
    block_key = f"task_block_{user_id}_{task_id}"
    now = datetime.utcnow()
    try:
        doc = rate_col.find_one({"_id": block_key})
        if doc and doc.get("expires_at") > now:
            return True
        return False
    except Exception as exc:
        logger.error("Task block check error: %s", exc)
        return False


def record_task_fail(user_id: int, task_id: str) -> None:
    counter_key = f"task_fail_{user_id}_{task_id}"
    block_key = f"task_block_{user_id}_{task_id}"
    now = datetime.utcnow()
    try:
        doc = rate_col.find_one({"_id": counter_key})
        current_count = doc.get("count", 0) if doc else 0
        new_count = current_count + 1
        rate_col.update_one(
            {"_id": counter_key},
            {"$set": {"count": new_count, "expires_at": now + timedelta(seconds=TASK_FAIL_COOLDOWN)}},
            upsert=True,
        )
        if new_count >= TASK_MAX_FAILS:
            rate_col.update_one(
                {"_id": block_key},
                {"$set": {"expires_at": now + timedelta(seconds=TASK_FAIL_COOLDOWN)}},
                upsert=True,
            )
            rate_col.delete_one({"_id": counter_key})
    except Exception as exc:
        logger.error("Record task fail error: %s", exc)


def clear_task_fail_counter(user_id: int, task_id: str) -> None:
    counter_key = f"task_fail_{user_id}_{task_id}"
    try:
        rate_col.delete_one({"_id": counter_key})
    except Exception as exc:
        logger.error("Clear task fail error: %s", exc)


# ============================================================
# 11. CHANNEL MEMBERSHIP VERIFICATION
# ============================================================

def extract_channel_username(url: str) -> str | None:
    if not url:
        return None
    match = re.search(r"t\.me/([a-zA-Z0-9_]+)", url)
    if match:
        username = match.group(1)
        if username.lower() in ("joinchat", "share", "+"):
            return None
        return "@" + username
    return None


def verify_channel_membership(
    channel_id_or_username: str,
    user_id: int,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> bool:
    for attempt in range(1, max_retries + 1):
        try:
            member = bot.get_chat_member(channel_id_or_username, user_id)
            if member.status in ("member", "administrator", "creator"):
                return True
            logger.debug(
                "Membership check attempt %d/%d: user %s status='%s' in %s",
                attempt, max_retries, user_id, member.status, channel_id_or_username,
            )
        except Exception as exc:
            logger.warning(
                "Membership check attempt %d/%d failed for %s (user %s): %s",
                attempt, max_retries, channel_id_or_username, user_id, exc,
            )

        if attempt < max_retries:
            time.sleep(retry_delay)

    logger.info(
        "Membership check: user %s NOT confirmed in %s after %d attempts.",
        user_id, channel_id_or_username, max_retries,
    )
    return False


# ============================================================
# 12. INPUT SANITIZATION
# ============================================================

def sanitize_text(value: str, max_length: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]


def is_group_chat(message) -> bool:
    return getattr(message.chat, "type", "") in ("group", "supergroup")


def get_bot_user_id() -> int | None:
    if not hasattr(get_bot_user_id, "_bot_id"):
        try:
            get_bot_user_id._bot_id = bot.get_me().id
        except Exception as exc:
            logger.warning("Unable to fetch bot user ID: %s", exc)
            get_bot_user_id._bot_id = None
    return get_bot_user_id._bot_id


def bot_has_group_moderation_rights(chat_id: int) -> tuple[bool, bool]:
    bot_id = get_bot_user_id()
    if not bot_id:
        return False, False
    try:
        member = bot.get_chat_member(chat_id, bot_id)
        if member.status == "creator":
            return True, True
        if member.status != "administrator":
            return False, False
        can_delete = bool(getattr(member, "can_delete_messages", False))
        can_ban = bool(getattr(member, "can_restrict_members", False))
        return can_delete, can_ban
    except Exception as exc:
        logger.warning("Unable to check bot moderation rights in chat %s: %s", chat_id, exc)
        return False, False


def get_group_code_patterns() -> list[str]:
    patterns = list(DEFAULT_GROUP_CODE_PATTERNS)
    try:
        custom_rules = code_filter_rules_col.find({"active": True}, {"pattern": 1, "_id": 0})
        patterns.extend(rule["pattern"] for rule in custom_rules if rule.get("pattern"))
    except Exception as exc:
        logger.warning("Unable to load custom group code patterns: %s", exc)
    return patterns


def message_matches_group_code_filter(text: str) -> str | None:
    if not text:
        return None
    for pattern in get_group_code_patterns():
        try:
            if re.search(pattern, text):
                return pattern
        except re.error as exc:
            logger.warning("Invalid group code filter pattern '%s': %s", pattern, exc)
    return None


def record_group_code_violation(chat_id: int, user_id: int) -> int:
    now = datetime.utcnow()
    window_start = now - timedelta(hours=GROUP_CODE_VIOLATION_WINDOW_HOURS)
    try:
        current = group_code_violations_col.find_one({"chat_id": chat_id, "user_id": user_id})
        if current:
            last_violation_raw = current.get("last_violation_at", "")
            try:
                last_violation_at = datetime.fromisoformat(last_violation_raw)
            except Exception:
                last_violation_at = now
            if last_violation_at < window_start:
                count = 1
            else:
                count = int(current.get("count", 0)) + 1
        else:
            count = 1
        group_code_violations_col.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {
                "$set": {
                    "count": count,
                    "last_violation_at": now.isoformat(),
                }
            },
            upsert=True,
        )
        return count
    except Exception as exc:
        logger.error("Unable to record group code violation for %s in %s: %s", user_id, chat_id, exc)
        return 1


# ============================================================
# 13. FLASK + BOT SETUP
# ============================================================

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
CORS(app, origins=[FRONTEND_URL], supports_credentials=False)


# ============================================================
# 14. SECURITY HEADERS
# ============================================================

@app.after_request
def add_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ============================================================
# 15. HELPER FUNCTIONS
# ============================================================

def get_leaderboard() -> str:
    try:
        top_users = list(
            users_col.find({}, {"user_id": 1, "coins": 1, "_id": 0})
            .sort("coins", -1)
            .limit(10)
        )
        data = [f"{u['user_id']}:{u.get('coins', 0)}" for u in top_users]
        return "|".join(data) if data else "none"
    except Exception as exc:
        logger.error("get_leaderboard error: %s", exc)
        return "none"


def get_referral_list(user_id: int) -> str:
    try:
        refs = list(
            users_col.find({"referred_by": str(user_id)}, {"user_id": 1, "_id": 0})
        )
        return ",".join(str(r["user_id"]) for r in refs) if refs else ""
    except Exception as exc:
        logger.error("get_referral_list error for %s: %s", user_id, exc)
        return ""


def get_referral_link(user_id: int) -> str:
    bot_username = BOT_USERNAME.lstrip("@")
    return f"https://t.me/{bot_username}?start={user_id}"


def send_referral_link(user_id: int) -> bool:
    referral_link = get_referral_link(user_id)
    share_text = "💰 Earn coins daily by watching ads & completing tasks! 🚀 Join now and start earning instantly!"
    share_url = (
        "https://t.me/share/url"
        f"?url={quote(referral_link, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📤 Share Now", url=share_url))

    try:
        bot.send_message(
            user_id,
            "👥 Invite your friends and earn 50 coins for each referral!\n\n"
            f"Your referral link:\n{referral_link}",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        logger.error("send_referral_link error for %s: %s", user_id, exc)
        return False


def get_or_create_user(user_id: int, username: str, referrer_id=None) -> dict:
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            new_user = {
                "user_id": user_id,
                "username": username,
                "coins": 0,
                "referred_by": None,
                "referral_count": 0,
                "task_completions": {},
                "channel_claims": {},
                "promo_task_completions": [],
                "last_claim_ts": "",
                "allcomplete_bonus_date": "",
                "ads_today": 0,
                "ads_date": "",
                "support_count": 0,
                "support_window_start": "",
                "ip_flagged": False,
                "fp_flagged": False,
                "blocked": False,
                "joined": str(date.today()),
            }
            if referrer_id and str(referrer_id) != str(user_id):
                referrer = users_col.find_one({"user_id": int(referrer_id)})
                if referrer:
                    users_col.update_one(
                        {"user_id": int(referrer_id)},
                        {"$inc": {"coins": 50, "referral_count": 1}},
                    )
                    new_user["referred_by"] = str(referrer_id)
                    try:
                        bot.send_message(
                            int(referrer_id),
                            "\U0001f38a *Referral Bonus!*\n\nYou earned 50 coins for inviting a friend!",
                            parse_mode="Markdown",
                        )
                    except Exception as notify_exc:
                        logger.warning("Referral notify failed: %s", notify_exc)
            users_col.insert_one(new_user)
            return new_user
        return user
    except Exception as exc:
        logger.error("get_or_create_user error for %s: %s", user_id, exc)
        return {}


def count_task_type_completions_today(task_completions: dict, prefix: str, today: str, live_codes: dict) -> int:
    """Count how many tasks of a given type prefix (yt/web) user completed today."""
    count = 0
    for tid, info in task_completions.items():
        if tid.startswith(prefix) and isinstance(info, dict):
            if info.get("date") == today and info.get("code") == live_codes.get(tid, ""):
                count += 1
    return count


# ============================================================
# 16. STATS CACHE
# ============================================================

_stats_cache: dict = {}
_stats_cache_time: float = 0.0
STATS_CACHE_TTL = 60


# ============================================================
# 17. FLASK API ROUTES
# ============================================================

@app.route("/")
def home():
    return jsonify({"status": "ok", "message": "Bot is Running Live!"})


@app.route("/get_user/<int:user_id>")
def get_user_data_api(user_id: int):
    if is_rate_limited(f"getuser_{user_id}", 3):
        return jsonify({"status": "error", "message": "Too many requests. Slow down."}), 429
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "blocked"})

        today = str(date.today())
        ads_date = user.get("ads_date", "")
        ads_today = user.get("ads_today", 0) if ads_date == today else 0

        task_completions = user.get("task_completions", {})
        live_task_codes = get_live_task_codes()
        completed_today = []
        for tid, info in task_completions.items():
            if isinstance(info, dict):
                if info.get("date") == today and info.get("code") == live_task_codes.get(tid, ""):
                    completed_today.append(tid)

        promo_task_completions = user.get("promo_task_completions", [])

        return jsonify(
            {
                "status": "success",
                "coins": user.get("coins", 0),
                "leaderboard": get_leaderboard_cached(),
                "referrals": get_referral_list(user_id),
                "completed_tasks": completed_today,
                "last_claim": user.get("last_claim_ts", ""),
                "referred_by": user.get("referred_by", ""),
                "ads_today": ads_today,
                "ads_date": ads_date,
                "channel_claims": user.get("channel_claims", {}),
                "promo_task_completions": promo_task_completions,
                "allcomplete_bonus_date": user.get("allcomplete_bonus_date", ""),
            }
        )
    except Exception as exc:
        logger.error("get_user error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


@app.route("/get_leaderboard")
def get_leaderboard_api():
    return jsonify({"status": "success", "leaderboard": get_leaderboard_cached()})


@app.route("/claim_daily/<int:user_id>", methods=["POST"])
def claim_daily_api(user_id: int):
    if is_rate_limited(f"claim_{user_id}", 60):
        return jsonify({"status": "error", "message": "Please wait before trying again."}), 429
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        now = datetime.utcnow()
        last_ts = user.get("last_claim_ts", "")
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                if now - last_dt < timedelta(hours=24):
                    remaining = timedelta(hours=24) - (now - last_dt)
                    total_secs = int(remaining.total_seconds())
                    h = total_secs // 3600
                    m = (total_secs % 3600) // 60
                    s = total_secs % 60
                    return (
                        jsonify({
                            "status": "error",
                            "message": f"Already claimed! Come back in {h}h {m}m {s}s.",
                            "data": {
                                "remaining_seconds": total_secs,
                                "next_claim_utc": (last_dt + timedelta(hours=24)).isoformat(),
                            },
                        }),
                        400,
                    )
            except ValueError:
                pass

        users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"coins": 10}, "$set": {"last_claim_ts": now.isoformat()}},
        )
        return jsonify(
            {"status": "success", "message": "10 coins credited to your account!", "data": {"bonus": 10}}
        )
    except Exception as exc:
        logger.error("claim_daily error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


@app.route("/claim_allcomplete_bonus/<int:user_id>", methods=["POST"])
def claim_allcomplete_bonus_api(user_id: int):
    """Credit 10 extra coins when user completes ALL daily tasks in one day.

    All-tasks required:
      - Daily bonus claimed today
      - All 10 ads watched today
      - All 3 YouTube tasks completed today
      - All 3 website tasks completed today

    One bonus per day per user.
    """
    if is_rate_limited(f"allbonus_{user_id}", 30):
        return jsonify({"status": "error", "message": "Please wait before trying again."}), 429

    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        today = str(date.today())

        # Already claimed today?
        if user.get("allcomplete_bonus_date") == today:
            return jsonify({"status": "error", "message": "All-tasks bonus already claimed today! Come back tomorrow."}), 400

        # Check daily bonus claimed today
        last_ts = user.get("last_claim_ts", "")
        daily_done = False
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                if (datetime.utcnow() - last_dt) < timedelta(hours=24):
                    # claimed within last 24h — check if it was today's date
                    daily_done = True
            except ValueError:
                pass
        if not daily_done:
            return jsonify({"status": "error", "message": "Claim your daily bonus first!"}), 400

        # Check ads — must have done MAX_ADS_PER_DAY today
        ads_date = user.get("ads_date", "")
        ads_today = user.get("ads_today", 0) if ads_date == today else 0
        if ads_today < MAX_ADS_PER_DAY:
            remaining_ads = MAX_ADS_PER_DAY - ads_today
            return jsonify({
                "status": "error",
                "message": f"Watch {remaining_ads} more ad(s) to complete all tasks!"
            }), 400

        # Check YT tasks (yt1, yt2, yt3) and web tasks (web1, web2, web3)
        task_completions = user.get("task_completions", {})
        live_codes = get_live_task_codes()

        yt_done = count_task_type_completions_today(task_completions, "yt", today, live_codes)
        web_done = count_task_type_completions_today(task_completions, "web", today, live_codes)

        if yt_done < MAX_YT_TASKS_PER_DAY:
            return jsonify({
                "status": "error",
                "message": f"Complete {MAX_YT_TASKS_PER_DAY - yt_done} more YouTube task(s) first!"
            }), 400

        if web_done < MAX_WEB_TASKS_PER_DAY:
            return jsonify({
                "status": "error",
                "message": f"Complete {MAX_WEB_TASKS_PER_DAY - web_done} more website task(s) first!"
            }), 400

        # All conditions met — credit bonus
        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": ALL_TASKS_BONUS},
                "$set": {"allcomplete_bonus_date": today},
            },
        )
        logger.info("All-tasks bonus of %s coins credited to user %s", ALL_TASKS_BONUS, user_id)
        return jsonify({
            "status": "success",
            "message": f"🎉 All tasks complete! Bonus {ALL_TASKS_BONUS} coins credited!",
            "data": {"bonus": ALL_TASKS_BONUS},
        })
    except Exception as exc:
        logger.error("claim_allcomplete_bonus error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


@app.route("/withdraw", methods=["POST"])
def withdraw_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    user_id_raw = data.get("user_id")
    upi_id = sanitize_text(data.get("upi_id", ""))
    requested_amount = data.get("amount")

    if not user_id_raw or not upi_id or requested_amount is None:
        return jsonify({"status": "error", "message": "Missing required fields."}), 400

    try:
        user_id = int(user_id_raw)
        requested_amount = int(requested_amount)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID or amount."}), 400

    if requested_amount <= 0:
        return jsonify({"status": "error", "message": "Amount cannot be zero or negative."}), 400
    if requested_amount < MIN_WITHDRAW:
        return jsonify({"status": "error", "message": f"Minimum withdrawal is {MIN_WITHDRAW} coins (₹{MIN_WITHDRAW // 50})."}), 400
    if requested_amount > MAX_WITHDRAW:
        return jsonify({"status": "error", "message": "Amount exceeds maximum limit."}), 400

    upi_pattern = re.compile(r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$")
    if not upi_pattern.match(upi_id):
        return jsonify({"status": "error", "message": "Invalid UPI ID format. (Example: name@upi)"}), 400

    if is_rate_limited(f"withdraw_{user_id}", WITHDRAW_COOLDOWN):
        return jsonify(
            {"status": "error", "message": "One withdrawal request allowed per day. Please try again tomorrow."}
        ), 429

    _ref_user = users_col.find_one({"user_id": user_id}, {"referral_count": 1, "_id": 0})
    ref_count = _ref_user.get("referral_count", 0) if _ref_user else 0
    if ref_count < 5:
        return jsonify(
            {"status": "error", "message": f"You need {5 - ref_count} more referrals to withdraw."}
        ), 400

    result = users_col.find_one_and_update(
        {"user_id": user_id, "coins": {"$gte": requested_amount}, "blocked": {"$ne": True}},
        {"$inc": {"coins": -requested_amount}},
        return_document=True,
    )
    if result is None:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403
        return jsonify(
            {"status": "error", "message": f"Insufficient balance. You have {user.get('coins', 0)} coins."}
        ), 400

    inr_value = requested_amount * 0.02
    withdrawal = {
        "user_id": user_id,
        "upi_id": upi_id,
        "amount": requested_amount,
        "inr_value": inr_value,
        "status": "Pending \u23f3",
        "date": datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC"),
    }
    withdrawals_col.insert_one(withdrawal)
    try:
        bot.send_message(
            ADMIN_ID,
            f"\U0001f4b8 *New Withdrawal Request*\n\n"
            f"User ID: `{user_id}`\n"
            f"UPI ID: `{upi_id}`\n"
            f"Requested: `{requested_amount}` coins (₹{inr_value:.2f})\n"
            f"Remaining Balance: `{result.get('coins', 0)}` coins\n"
            f"Date: {withdrawal['date']}",
            parse_mode="Markdown",
        )
    except Exception as notify_exc:
        logger.warning("Admin notify failed for withdrawal: %s", notify_exc)

    return jsonify({"status": "success", "message": "Withdrawal request submitted successfully!"})


@app.route("/get_history/<int:user_id>")
def get_history_api(user_id: int):
    if is_rate_limited(f"history_{user_id}", 5):
        return jsonify({"status": "error", "message": "Please wait before refreshing."}), 429
    try:
        history = list(
            withdrawals_col.find({"user_id": user_id}, {"_id": 0}).sort("date", -1).limit(10)
        )
        return jsonify({"status": "success", "data": {"history": history}})
    except Exception as exc:
        logger.error("get_history error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/verify_task", methods=["POST"])
def verify_task_api():
    """Verify a task completion code and credit coins if correct.

    Enforces:
    - Daily limits: max 3 YouTube tasks, max 3 website/link tasks
    - Anti-cheat: rate limits + fail attempt tracking
    - One completion per task per day
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    task_id = sanitize_text(data.get("task_id", "")).lower()
    user_code = sanitize_text(data.get("code", "")).upper()

    if not task_id or not user_code:
        return jsonify({"status": "error", "message": "Missing task ID or code."}), 400
    if task_id not in VALID_TASK_IDS:
        return jsonify({"status": "error", "message": "Invalid task ID."}), 400

    _u = users_col.find_one({"user_id": user_id}, {"blocked": 1})
    if _u and _u.get("blocked"):
        return jsonify({"status": "error", "message": "Your account has been suspended."}), 403

    if is_task_attempt_blocked(user_id, task_id):
        return jsonify(
            {"status": "error", "message": f"Too many wrong attempts. Wait {TASK_FAIL_COOLDOWN // 60} minute(s) before retrying."}
        ), 429

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
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        today = str(date.today())
        task_completions = user.get("task_completions", {})
        live_codes = get_live_task_codes()

        existing = task_completions.get(task_id, {})
        if (
            isinstance(existing, dict)
            and existing.get("date") == today
            and existing.get("code") == correct_code
        ):
            return jsonify({"status": "error", "message": "Task already completed today! Come back tomorrow."}), 400

        # Enforce daily task-type limits
        if task_id.startswith("yt"):
            done_today = count_task_type_completions_today(task_completions, "yt", today, live_codes)
            if done_today >= MAX_YT_TASKS_PER_DAY:
                return jsonify({
                    "status": "error",
                    "message": f"Daily YouTube task limit reached ({MAX_YT_TASKS_PER_DAY}/{MAX_YT_TASKS_PER_DAY}). Come back tomorrow!"
                }), 400
        elif task_id.startswith("web"):
            done_today = count_task_type_completions_today(task_completions, "web", today, live_codes)
            if done_today >= MAX_WEB_TASKS_PER_DAY:
                return jsonify({
                    "status": "error",
                    "message": f"Daily website task limit reached ({MAX_WEB_TASKS_PER_DAY}/{MAX_WEB_TASKS_PER_DAY}). Come back tomorrow!"
                }), 400

        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": reward},
                "$set": {f"task_completions.{task_id}": {"date": today, "code": correct_code}},
            },
        )
        clear_task_fail_counter(user_id, task_id)
        return jsonify(
            {
                "status": "success",
                "message": f"{reward} coins added to your balance!",
                "data": {"reward": reward},
            }
        )
    except Exception as exc:
        logger.error("verify_task error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ============================================================
# AD REWARD SYSTEM (SDK-based, reward only after completion)
# ============================================================

def create_ad_claim_token(user_id: int) -> tuple[dict, int]:
    """Create a short-lived one-time token before showing an ad."""
    if user_id <= 0:
        return {"status": "error", "message": "Invalid user ID."}, 400

    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return {"status": "error", "message": "User not found."}, 404
        if user.get("blocked"):
            return {"status": "error", "message": "Your account has been blocked."}, 403

        last_ad_claim = user.get("last_ad_claim_at")
        if last_ad_claim:
            try:
                last_ad_claim_dt = datetime.fromisoformat(last_ad_claim)
                if datetime.utcnow() - last_ad_claim_dt < timedelta(seconds=30):
                    return {
                        "status": "error",
                        "message": "Please wait 30 seconds before watching the next ad.",
                    }, 429
            except ValueError:
                pass

        today = str(date.today())
        ads_date = user.get("ads_date", "")
        ads_today = user.get("ads_today", 0) if ads_date == today else 0

        if ads_today >= MAX_ADS_PER_DAY:
            return {
                "status": "error",
                "message": f"Daily ad limit reached ({MAX_ADS_PER_DAY}/{MAX_ADS_PER_DAY}). Come back tomorrow!",
                "data": {
                    "ads_done": ads_today,
                    "ads_total": MAX_ADS_PER_DAY,
                    "remaining": 0,
                },
            }, 400

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.utcnow() + timedelta(seconds=AD_CLAIM_TOKEN_TTL_SECONDS)
        ad_reward_tokens_col.insert_one({
            "_id": token_hash,
            "user_id": user_id,
            "created_at": datetime.utcnow(),
            "expires_at": expires_at,
            "source": "sdk_ad",
        })
        return {
            "status": "success",
            "token": raw_token,
            "expires_in": AD_CLAIM_TOKEN_TTL_SECONDS,
        }, 200
    except Exception as exc:
        logger.error("create_ad_claim_token error for %s: %s", user_id, exc)
        return {"status": "error", "message": "Server error."}, 500


def manual_ad_reward(user_id: int, claim_token: str) -> tuple[dict, int]:
    """Credit coins only after SDK confirms ad completion."""
    if user_id <= 0:
        return {"status": "error", "message": "Invalid user ID."}, 400

    if not claim_token:
        return {"status": "error", "message": "Ad verification token missing."}, 400

    try:
        token_hash = hashlib.sha256(claim_token.encode()).hexdigest()
        token_doc = ad_reward_tokens_col.find_one_and_delete({
            "_id": token_hash,
            "user_id": user_id,
        })
        if not token_doc:
            return {"status": "error", "message": "Invalid or already used ad token."}, 403
        if token_doc.get("expires_at") and token_doc["expires_at"] < datetime.utcnow():
            return {"status": "error", "message": "Ad token expired. Please watch another ad."}, 403

        user = users_col.find_one({"user_id": user_id})
        if not user:
            return {"status": "error", "message": "User not found."}, 404
        if user.get("blocked"):
            return {"status": "error", "message": "Your account has been blocked."}, 403

        last_ad_claim = user.get("last_ad_claim_at")
        if last_ad_claim:
            try:
                last_ad_claim_dt = datetime.fromisoformat(last_ad_claim)
                if datetime.utcnow() - last_ad_claim_dt < timedelta(seconds=30):
                    return {
                        "status": "error",
                        "message": "Please wait 30 seconds before claiming the next ad reward.",
                    }, 429
            except ValueError:
                pass

        today = str(date.today())
        ads_date = user.get("ads_date", "")
        ads_today = user.get("ads_today", 0) if ads_date == today else 0

        if ads_today >= MAX_ADS_PER_DAY:
            return {
                "status": "error",
                "message": f"Daily ad limit reached ({MAX_ADS_PER_DAY}/{MAX_ADS_PER_DAY}). Come back tomorrow!",
                "data": {
                    "ads_done": ads_today,
                    "ads_total": MAX_ADS_PER_DAY,
                    "remaining": 0,
                },
            }, 400

        done = ads_today + 1
        remaining = MAX_ADS_PER_DAY - done
        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": AD_COIN_REWARD},
                "$set": {
                    "ads_date": today,
                    "ads_today": done,
                    "last_ad_claim_at": datetime.utcnow().isoformat(),
                },
            },
        )

        return {
            "status": "success",
            "message": f"{AD_COIN_REWARD} coins earned! ({done}/{MAX_ADS_PER_DAY} ads watched today)",
            "data": {
                "reward": AD_COIN_REWARD,
                "ads_done": done,
                "ads_total": MAX_ADS_PER_DAY,
                "remaining": remaining,
            },
        }, 200
    except Exception as exc:
        logger.error("manual_ad_reward error for %s: %s", user_id, exc)
        return {"status": "error", "message": "Server error."}, 500


@app.route("/claim_ad/<int:user_id>", methods=["POST"])
def claim_ad_api(user_id: int):
    """Reward endpoint called only after SDK ad completion."""
    data = request.get_json(silent=True) or {}
    payload, status_code = manual_ad_reward(user_id, (data.get("token") or "").strip())
    return jsonify(payload), status_code


@app.route("/ad_claim_token/<int:user_id>", methods=["POST"])
def ad_claim_token_api(user_id: int):
    """Create a short-lived one-time token for an ad reward attempt."""
    payload, status_code = create_ad_claim_token(user_id)
    return jsonify(payload), status_code


# ============================================================
# CHANNEL CLAIM (One-time, reward only after ALL 3 channels joined)
# ============================================================

@app.route("/claim_channel", methods=["POST"])
def claim_channel_api():
    """Claim coins for joining all required Telegram channels.

    Rules:
    - Each of the 3 channels can be claimed once (one-time)
    - Reward (CHANNEL_REWARD_PER_CHANNEL coins) credited per channel
    - Prevents duplicate claims per channel
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    channel_id = sanitize_text(data.get("channel_id", "")).lower()
    channel_url = sanitize_text(data.get("channel_url", ""), max_length=500)

    if channel_id not in CHANNEL_IDS:
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
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        channel_claims = user.get("channel_claims", {})

        if channel_claims.get(channel_id):
            return jsonify({"status": "error", "message": "Reward already claimed for this channel! ✅"}), 400

        if channel_url:
            ch_username = extract_channel_username(channel_url)
            if ch_username:
                is_member = verify_channel_membership(ch_username, user_id)
                if not is_member:
                    return jsonify(
                        {"status": "not_joined", "message": "Please join the channel first, then tap Retry!"}
                    ), 400

        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": CHANNEL_REWARD_PER_CHANNEL},
                "$set": {f"channel_claims.{channel_id}": True}
            },
        )
        return jsonify(
            {
                "status": "success",
                "message": f"{CHANNEL_REWARD_PER_CHANNEL} coins credited for joining the channel!",
                "data": {"reward": CHANNEL_REWARD_PER_CHANNEL},
            }
        )
    except Exception as exc:
        logger.error("claim_channel error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ============================================================
# PROMOTION TASKS (Manual, admin-only, one-time per user)
# ============================================================

@app.route("/get_promo_tasks", methods=["GET"])
def get_promo_tasks_api():
    """Return all active promotion tasks (visible to all users).

    Returns:
        JSON: List of active promo task objects.
    """
    try:
        tasks = list(promo_tasks_col.find({"active": True}, {"_id": 0}))
        return jsonify({"status": "success", "tasks": tasks})
    except Exception as exc:
        logger.error("get_promo_tasks error: %s", exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/claim_promo_task", methods=["POST"])
def claim_promo_task_api():
    """Claim reward for a promotion task.

    Rules:
    - Fixed reward: PROMO_TASK_REWARD coins per task
    - Each user can only complete each promo task once (one-time)
    - Task must exist and be active

    Returns:
        JSON: Success with reward, or error.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    task_id = sanitize_text(data.get("task_id", "")).strip()

    if not task_id:
        return jsonify({"status": "error", "message": "Missing task ID."}), 400

    if is_rate_limited(f"promo_task_{user_id}_{task_id}", 15):
        return jsonify({"status": "error", "message": "Please wait a moment before trying again."}), 429

    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        promo_completions = user.get("promo_task_completions", [])
        if task_id in promo_completions:
            return jsonify({"status": "error", "message": "You have already completed this promotion task!"}), 400

        task = promo_tasks_col.find_one({"task_id": task_id, "active": True})
        if not task:
            return jsonify({"status": "error", "message": "Promotion task not found or no longer active."}), 404

        reward = task.get("reward", PROMO_TASK_REWARD)

        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": reward},
                "$addToSet": {"promo_task_completions": task_id},
            },
        )
        return jsonify(
            {
                "status": "success",
                "message": f"{reward} coins added for completing the promotion task!",
                "data": {"reward": reward},
            }
        )
    except Exception as exc:
        logger.error("claim_promo_task error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ============================================================
# ADMIN: PROMO TASK MANAGEMENT
# ============================================================

@app.route("/admin/add_promo_task", methods=["POST"])
def admin_add_promo_task():
    """Admin only: Add a new promotion task.

    Body fields:
        task_id   : Unique task identifier string
        title     : Task title (shown to users)
        description: Short description (optional)
        link      : URL for the task (optional, e.g. channel or website)
        reward    : Coin reward (defaults to PROMO_TASK_REWARD = 5)

    Returns:
        JSON: Success or error.
    """
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401

    data = request.get_json(silent=True) or {}
    task_id = sanitize_text(data.get("task_id", "")).strip()
    title = sanitize_text(data.get("title", "")).strip()
    description = sanitize_text(data.get("description", ""), max_length=300).strip()
    link = sanitize_text(data.get("link", ""), max_length=500).strip()

    try:
        reward = int(data.get("reward", PROMO_TASK_REWARD))
        if reward <= 0:
            reward = PROMO_TASK_REWARD
    except (TypeError, ValueError):
        reward = PROMO_TASK_REWARD

    if not task_id or not title:
        return jsonify({"status": "error", "message": "task_id and title are required."}), 400

    try:
        existing = promo_tasks_col.find_one({"task_id": task_id})
        if existing:
            return jsonify({"status": "error", "message": f"Task '{task_id}' already exists."}), 400

        promo_tasks_col.insert_one({
            "task_id": task_id,
            "title": title,
            "description": description,
            "link": link,
            "reward": reward,
            "active": True,
            "created_at": datetime.utcnow().isoformat(),
        })
        logger.info("Admin added promo task: %s", task_id)
        return jsonify({"status": "success", "message": f"Promo task '{task_id}' added."})
    except Exception as exc:
        logger.error("admin_add_promo_task error: %s", exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/admin/remove_promo_task", methods=["POST"])
def admin_remove_promo_task():
    """Admin only: Deactivate a promotion task.

    Body fields:
        task_id: Task identifier to deactivate.

    Returns:
        JSON: Success or error.
    """
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401

    data = request.get_json(silent=True) or {}
    task_id = sanitize_text(data.get("task_id", "")).strip()

    if not task_id:
        return jsonify({"status": "error", "message": "task_id is required."}), 400

    try:
        result = promo_tasks_col.update_one(
            {"task_id": task_id},
            {"$set": {"active": False, "deactivated_at": datetime.utcnow().isoformat()}}
        )
        if result.matched_count:
            logger.info("Admin deactivated promo task: %s", task_id)
            return jsonify({"status": "success", "message": f"Promo task '{task_id}' deactivated."})
        return jsonify({"status": "error", "message": "Task not found."}), 404
    except Exception as exc:
        logger.error("admin_remove_promo_task error: %s", exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ============================================================
# SPONSOR CLICK TRACKING (kept for compatibility, no auto-reward)
# ============================================================

@app.route("/click_sponsor", methods=["POST"])
def click_sponsor_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "ok"})

    try:
        user_id = int(data.get("user_id", 0))
    except (TypeError, ValueError):
        return jsonify({"status": "ok"})

    slot_id = sanitize_text(data.get("slot_id", ""), max_length=20).lower()
    link_url = sanitize_text(data.get("link_url", ""), max_length=500)

    if slot_id not in ("slot1", "slot2", "slot3") or not link_url:
        return jsonify({"status": "ok"})

    if is_rate_limited(f"sponsorclick_{user_id}_{slot_id}", 600):
        return jsonify({"status": "ok"})

    try:
        doc = sponsor_clicks_col.find_one({"_id": slot_id})
        if doc is None or doc.get("link_url") != link_url:
            sponsor_clicks_col.replace_one(
                {"_id": slot_id},
                {"_id": slot_id, "link_url": link_url, "count": 1, "users": [user_id]},
                upsert=True,
            )
        else:
            existing_users = doc.get("users", [])
            if user_id not in existing_users:
                sponsor_clicks_col.update_one(
                    {"_id": slot_id},
                    {"$inc": {"count": 1}, "$push": {"users": {"$each": [user_id], "$slice": -3000}}},
                )
        return jsonify({"status": "ok"})
    except Exception as exc:
        logger.error("click_sponsor error: %s", exc)
        return jsonify({"status": "ok"})


@app.route("/check_device", methods=["POST"])
def check_device_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "ok"})

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "ok"})

    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip:
        ip = ip.split(",")[0].strip()

    fingerprint = sanitize_text(data.get("fingerprint", ""), max_length=128)

    try:
        current_user = users_col.find_one({"user_id": user_id})
        if not current_user:
            return jsonify({"status": "ok"})
        if current_user.get("blocked"):
            return jsonify({"status": "blocked"})

        ip_conflict = users_col.find_one({"ip": ip, "user_id": {"$ne": user_id}}) if ip else None
        fp_conflict = (
            users_col.find_one({"fingerprint": fingerprint, "user_id": {"$ne": user_id}})
            if fingerprint
            else None
        )

        if ip_conflict:
            logger.warning("IP conflict: user %s shares IP %s with another account.", user_id, ip)
            users_col.update_one({"user_id": user_id}, {"$set": {"ip_flagged": True}})
            try:
                bot.send_message(
                    ADMIN_ID,
                    f"⚠️ IP Conflict Detected\nUser: `{user_id}` shares IP with `{ip_conflict.get('user_id')}`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        if fp_conflict:
            logger.warning("Fingerprint conflict: user %s shares fingerprint with another account.", user_id)
            users_col.update_one({"user_id": user_id}, {"$set": {"fp_flagged": True}})

        update_fields = {}
        if ip and not current_user.get("ip"):
            update_fields["ip"] = ip
        if fingerprint and not current_user.get("fingerprint"):
            update_fields["fingerprint"] = fingerprint
        if update_fields:
            users_col.update_one({"user_id": user_id}, {"$set": update_fields})

        return jsonify({"status": "ok"})
    except Exception as exc:
        logger.error("check_device error for %s: %s", user_id, exc)
        return jsonify({"status": "ok"})


# ============================================================
# PROMO CODE (web app endpoint)
# ============================================================

@app.route("/redeem_promo", methods=["POST"])
def redeem_promo_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    code = sanitize_text(data.get("code", "")).upper()
    if not code:
        return jsonify({"status": "error", "message": "Missing promo code."}), 400

    if is_rate_limited(f"promo_{user_id}", 10):
        return jsonify({"status": "error", "message": "Please wait before trying again."}), 429

    user = users_col.find_one({"user_id": user_id}, {"blocked": 1})
    if not user:
        return jsonify({"status": "error", "message": "User not found."}), 404
    if user.get("blocked"):
        return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

    try:
        promo = promos_col.find_one({"code": code})
        if not promo:
            return jsonify({"status": "error", "message": "Invalid promo code."}), 400
        if not promo.get("active", True):
            return jsonify({"status": "error", "message": "This promo code has expired."}), 400

        used_by = promo.get("used_by", [])
        if user_id in used_by:
            return jsonify({"status": "error", "message": "You have already used this promo code."}), 400

        max_uses = promo.get("max_uses", 0)
        if max_uses > 0 and len(used_by) >= max_uses:
            return jsonify({"status": "error", "message": "This promo code has reached its usage limit."}), 400

        coins = promo.get("coins", 0)
        if coins <= 0:
            return jsonify({"status": "error", "message": "Invalid promo code value."}), 400

        promos_col.update_one({"code": code}, {"$push": {"used_by": user_id}})
        users_col.update_one({"user_id": user_id}, {"$inc": {"coins": coins}})

        logger.info("Promo '%s' redeemed by user %s for %s coins.", code, user_id, coins)
        return jsonify({
            "status": "success",
            "message": f"{coins} coins added to your balance!",
            "data": {"reward": coins}
        })
    except Exception as exc:
        logger.error("redeem_promo_api error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


# ============================================================
# SUPPORT ENDPOINT
# ============================================================

@app.route("/send_support", methods=["POST"])
def send_support_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    msg_text = sanitize_text(data.get("message", ""), max_length=1000).strip()
    if not msg_text:
        return jsonify({"status": "error", "message": "Message cannot be empty."}), 400

    allowed, err_msg = check_support_limit(user_id)
    if not allowed:
        return jsonify({"status": "error", "message": err_msg}), 429

    try:
        bot.send_message(
            ADMIN_ID,
            f"🎧 *Support Message*\n\nUser ID: `{user_id}`\n\n{msg_text}",
            parse_mode="Markdown",
        )
        users_col.update_one({"user_id": user_id}, {"$inc": {"support_count": 1}})
        return jsonify({"status": "success", "message": "Message sent to admin!"})
    except Exception as exc:
        logger.error("send_support error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ============================================================
# ADMIN API ROUTES
# ============================================================

@app.route("/admin/get_users", methods=["GET"])
def admin_get_users():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        page = max(1, int(request.args.get("page", 1)))
        limit = min(50, int(request.args.get("limit", 20)))
    except (ValueError, TypeError):
        page, limit = 1, 20
    skip = (page - 1) * limit
    try:
        total = users_col.count_documents({})
        users = list(
            users_col.find(
                {},
                {"user_id": 1, "username": 1, "coins": 1, "referral_count": 1, "blocked": 1, "joined": 1, "_id": 0},
            )
            .sort("coins", -1)
            .skip(skip)
            .limit(limit)
        )
        return jsonify({"status": "success", "total": total, "users": users})
    except Exception as exc:
        logger.error("admin_get_users error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/admin/get_withdrawals", methods=["GET"])
def admin_get_withdrawals():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        status_filter = request.args.get("status", "")
        query = {"status": status_filter} if status_filter else {}
        withdrawals = list(
            withdrawals_col.find(query, {"_id": 0}).sort("date", -1).limit(50)
        )
        return jsonify({"status": "success", "withdrawals": withdrawals})
    except Exception as exc:
        logger.error("admin_get_withdrawals error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/admin/approve_withdrawal", methods=["POST"])
def admin_approve_withdrawal():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        uid = int(data.get("user_id", 0))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    result = withdrawals_col.update_one(
        {"user_id": uid, "status": "Pending \u23f3"},
        {"$set": {"status": "Approved \u2705"}},
    )
    if result.modified_count:
        try:
            bot.send_message(uid, "\U0001f389 *Your withdrawal has been approved!* Payment is being processed. \u2705", parse_mode="Markdown")
        except Exception:
            pass
        return jsonify({"status": "success", "message": f"Withdrawal approved for user {uid}"})
    return jsonify({"status": "error", "message": "No pending withdrawal found"}), 404


@app.route("/admin/reject_withdrawal", methods=["POST"])
def admin_reject_withdrawal():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        uid = int(data.get("user_id", 0))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    withdraw = withdrawals_col.find_one({"user_id": uid, "status": "Pending \u23f3"})
    if withdraw:
        users_col.update_one({"user_id": uid}, {"$inc": {"coins": withdraw["amount"]}})
        withdrawals_col.update_one(
            {"user_id": uid, "status": "Pending \u23f3"},
            {"$set": {"status": "Rejected \u274c"}},
        )
        try:
            bot.send_message(uid, f"\u274c Your withdrawal was rejected. {withdraw['amount']} coins have been refunded.")
        except Exception:
            pass
        return jsonify({"status": "success", "message": f"Withdrawal rejected, coins refunded to user {uid}"})
    return jsonify({"status": "error", "message": "No pending withdrawal found"}), 404


@app.route("/admin/add_coins", methods=["POST"])
def admin_add_coins():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        uid = int(data.get("user_id", 0))
        amount = int(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID or amount"}), 400
    if amount <= 0:
        return jsonify({"status": "error", "message": "Amount must be positive"}), 400
    users_col.update_one({"user_id": uid}, {"$inc": {"coins": amount}})
    try:
        bot.send_message(uid, f"\U0001f381 Admin has gifted you *{amount} coins*!", parse_mode="Markdown")
    except Exception:
        pass
    return jsonify({"status": "success", "message": f"{amount} coins added to user {uid}"})


@app.route("/admin/ban_user", methods=["POST"])
def admin_ban_user():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        uid = int(data.get("user_id", 0))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    users_col.update_one({"user_id": uid}, {"$set": {"blocked": True}})
    try:
        bot.send_message(uid, "\u26d4 Your account has been suspended for violating our terms of service.")
    except Exception:
        pass
    logger.info("Admin banned user %s", uid)
    return jsonify({"status": "success", "message": f"User {uid} banned"})


@app.route("/admin/unban_user", methods=["POST"])
def admin_unban_user():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        uid = int(data.get("user_id", 0))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    users_col.update_one(
        {"user_id": uid},
        {"$set": {"blocked": False, "fp_flagged": False, "ip_flagged": False}},
    )
    try:
        bot.send_message(uid, "\u2705 Your account has been reinstated. Welcome back!")
    except Exception:
        pass
    logger.info("Admin unbanned user %s", uid)
    return jsonify({"status": "success", "message": f"User {uid} unbanned"})


@app.route("/admin/list_banned", methods=["GET"])
def admin_list_banned():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    banned = list(
        users_col.find(
            {"blocked": True}, {"user_id": 1, "username": 1, "coins": 1, "_id": 0}
        ).limit(50)
    )
    return jsonify({"status": "success", "banned_users": banned})


@app.route("/admin/send_dm", methods=["POST"])
def admin_send_dm():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        uid = int(data.get("user_id", 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400
    msg = (data.get("message") or "").strip()
    if not uid or not msg:
        return jsonify({"status": "error", "message": "User ID and message are required."}), 400
    user = users_col.find_one({"user_id": uid}, {"user_id": 1})
    if not user:
        return jsonify({"status": "error", "message": f"User {uid} not registered."}), 404
    try:
        try:
            bot.send_message(uid, msg, parse_mode="Markdown")
        except Exception:
            bot.send_message(uid, msg)
        return jsonify({"status": "success", "message": f"Message sent to user {uid}!"})
    except Exception as exc:
        logger.error("admin_send_dm error for %s: %s", uid, exc)
        return jsonify({"status": "error", "message": "Failed to send message."}), 500


@app.route("/admin/sponsor_clicks", methods=["GET"])
def admin_sponsor_clicks():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        docs = list(sponsor_clicks_col.find({}, {"_id": 1, "link_url": 1, "count": 1}))
        result = {d["_id"]: {"count": d.get("count", 0), "link_url": d.get("link_url", "")} for d in docs}
        return jsonify({"status": "success", "clicks": result})
    except Exception as exc:
        logger.error("admin_sponsor_clicks error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


def _do_broadcast(msg: str) -> None:
    user_ids = [u["user_id"] for u in users_col.find({}, {"user_id": 1})]
    sent = failed = 0
    for uid in user_ids:
        try:
            bot.send_message(uid, msg, parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1
        time.sleep(0.05)
    logger.info("Broadcast complete: %s sent, %s failed", sent, failed)


@app.route("/admin/broadcast", methods=["POST"])
def admin_broadcast():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    msg = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"status": "error", "message": "Empty message"}), 400
    total = users_col.count_documents({})
    Thread(target=_do_broadcast, args=(msg,), daemon=True).start()
    return jsonify({"status": "success", "message": f"Broadcast started for ~{total} users."})


# ============================================================
# 20. BOT COMMANDS
# ============================================================

@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.first_name or "User"
    params = message.text.split()
    referrer_id = params[1] if len(params) > 1 else None

    user = get_or_create_user(user_id, username, referrer_id)
    current_coins = user.get("coins", 0)
    web_app_url = f"https://sahdakshsanoj-byte.github.io/Earning-bot/?user_id={user_id}"

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "\U0001f4b0 Open Earning Hub", web_app=types.WebAppInfo(web_app_url)
        )
    )
    markup.add(
        types.InlineKeyboardButton(
            "\U0001f465 Invite Friends",
            callback_data="invite_friends",
        )
    )

    bot.send_message(
        user_id,
        f"\U0001f44b *Hello {username}!*\n\n"
        f"\U0001f4b0 Balance: *{current_coins} \U0001fa99*\n\n"
        f"Invite friends and earn *50 coins* for each referral!\n"
        f"Tap the button below to start earning! \U0001f680",
        reply_markup=markup,
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["invite"])
def invite_command(message):
    user_id = message.from_user.id
    get_or_create_user(user_id, message.from_user.first_name or "User")
    if not send_referral_link(user_id):
        bot.reply_to(message, "Unable to send invite right now. Please try again later.")


@bot.callback_query_handler(func=lambda call: call.data == "invite_friends")
def invite_friends_callback(call):
    user_id = call.from_user.id
    get_or_create_user(user_id, call.from_user.first_name or "User")
    if send_referral_link(user_id):
        bot.answer_callback_query(call.id, "Referral link sent!")
    else:
        bot.answer_callback_query(call.id, "Unable to send invite. Try again later.", show_alert=True)


@bot.message_handler(commands=["balance"])
def check_balance(message):
    user = users_col.find_one({"user_id": message.from_user.id})
    if user:
        coins = user.get("coins", 0)
        inr = coins * 0.02
        bot.reply_to(
            message,
            f"\U0001f4b0 Your balance: *{coins} \U0001fa99* (≈ ₹{inr:.2f})",
            parse_mode="Markdown",
        )
    else:
        bot.reply_to(message, "Please use /start to register first!")


@bot.message_handler(commands=["redeem"])
def redeem_promo_command(message):
    user_id = message.from_user.id
    parts = message.text.split()

    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /redeem <CODE>\nExample: /redeem WELCOME100")

    code = parts[1].upper()

    user = users_col.find_one({"user_id": user_id}, {"blocked": 1})
    if not user:
        return bot.reply_to(message, "Please use /start to register first.")
    if user.get("blocked"):
        return bot.reply_to(message, "\u26d4 Your account has been suspended.")

    if is_rate_limited(f"promo_{user_id}", 10):
        return bot.reply_to(message, "\u23f3 Please wait a moment before trying again.")

    try:
        promo = promos_col.find_one({"code": code})

        if not promo:
            return bot.reply_to(message, "\u274c Invalid promo code. Please check and try again.")
        if not promo.get("active", True):
            return bot.reply_to(message, "\u274c This promo code has expired or been deactivated.")

        used_by = promo.get("used_by", [])
        if user_id in used_by:
            return bot.reply_to(message, "\u274c You have already used this promo code.")

        max_uses = promo.get("max_uses", 0)
        if max_uses > 0 and len(used_by) >= max_uses:
            return bot.reply_to(message, "\u274c This promo code has reached its usage limit.")

        coins = promo.get("coins", 0)
        if coins <= 0:
            return bot.reply_to(message, "\u274c Invalid promo code value.")

        promos_col.update_one({"code": code}, {"$push": {"used_by": user_id}})
        users_col.update_one({"user_id": user_id}, {"$inc": {"coins": coins}})

        logger.info("Promo '%s' redeemed via bot by user %s for %s coins.", code, user_id, coins)
        bot.reply_to(
            message,
            f"\U0001f389 *Promo Code Redeemed!*\n\n*{coins} coins* have been added to your balance! \U0001fa99",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("redeem_promo_command error for %s: %s", user_id, exc)
        bot.reply_to(message, "\u26a0\ufe0f Server error. Please try again.")


@bot.message_handler(commands=["createpromo"])
def create_promo_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 3:
        return bot.reply_to(
            message,
            "Usage: /createpromo <CODE> <COINS> [max\\_uses]\n\n"
            "Examples:\n"
            "`/createpromo WELCOME100 100` — unlimited uses\n"
            "`/createpromo VIP500 500 50` — max 50 uses",
            parse_mode="Markdown",
        )

    code = parts[1].upper()
    try:
        coins = int(parts[2])
        max_uses = int(parts[3]) if len(parts) >= 4 else 0
    except ValueError:
        return bot.reply_to(message, "\u274c Invalid coins or max_uses value. Must be integers.")

    if coins <= 0:
        return bot.reply_to(message, "\u274c Coins must be greater than 0.")
    if max_uses < 0:
        return bot.reply_to(message, "\u274c max_uses must be 0 (unlimited) or a positive number.")
    if not re.match(r"^[A-Z0-9_]{2,30}$", code):
        return bot.reply_to(message, "\u274c Invalid code format. Use only letters, numbers, and underscores (2-30 chars).")

    try:
        existing = promos_col.find_one({"code": code})
        if existing:
            return bot.reply_to(
                message,
                f"\u26a0\ufe0f Code `{code}` already exists!\n"
                f"Coins: `{existing.get('coins', 0)}`\n"
                f"Max Uses: `{existing.get('max_uses', 0) or 'Unlimited'}`\n"
                f"Used: `{len(existing.get('used_by', []))}`\n\n"
                f"Use /deletepromo {code} to remove it first.",
                parse_mode="Markdown",
            )

        promo_doc = {
            "code": code,
            "coins": coins,
            "max_uses": max_uses,
            "used_by": [],
            "active": True,
            "created_at": datetime.utcnow().isoformat(),
            "created_by": ADMIN_ID,
        }
        promos_col.insert_one(promo_doc)
        logger.info("Admin created promo '%s' for %s coins (max_uses=%s)", code, coins, max_uses)

        uses_str = f"{max_uses}" if max_uses > 0 else "Unlimited"
        bot.reply_to(
            message,
            f"\u2705 *Promo Code Created!*\n\n"
            f"Code: `{code}`\n"
            f"Coins: `{coins}`\n"
            f"Max Uses: `{uses_str}`\n\n"
            f"Users can redeem with: /redeem {code}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("create_promo_command error: %s", exc)
        bot.reply_to(message, "\u26a0\ufe0f Server error. Please try again.")


@bot.message_handler(commands=["deletepromo"])
def delete_promo_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /deletepromo <CODE>")
    code = parts[1].upper()
    try:
        result = promos_col.update_one({"code": code}, {"$set": {"active": False}})
        if result.matched_count:
            logger.info("Admin deactivated promo '%s'", code)
            bot.reply_to(message, f"\u2705 Promo code `{code}` has been deactivated.", parse_mode="Markdown")
        else:
            bot.reply_to(message, f"\u274c Promo code `{code}` not found.", parse_mode="Markdown")
    except Exception as exc:
        logger.error("delete_promo_command error: %s", exc)
        bot.reply_to(message, "\u26a0\ufe0f Server error. Please try again.")


@bot.message_handler(commands=["listpromos"])
def list_promos_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    try:
        promos = list(promos_col.find({"active": True}, {"_id": 0, "used_by": 0}))
        if not promos:
            return bot.reply_to(message, "\U0001f4cb No active promo codes found.")
        lines = ["\U0001f3ab *Active Promo Codes*\n"]
        for p in promos:
            uses_str = f"{p.get('max_uses', 0)}" if p.get("max_uses", 0) > 0 else "Unlimited"
            lines.append(f"• `{p['code']}` — {p['coins']} coins — Max: {uses_str}")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        logger.error("list_promos_command error: %s", exc)
        bot.reply_to(message, "\u26a0\ufe0f Server error. Please try again.")


@bot.message_handler(commands=["addpromtask"])
def add_promo_task_command(message):
    """Admin: /addpromtask <task_id> <reward> <title...>

    Example: /addpromtask promo_yt1 5 Watch our YouTube video
    """
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split(None, 3)
    if len(parts) < 4:
        return bot.reply_to(
            message,
            "Usage: /addpromtask <task_id> <reward_coins> <title>\n"
            "Example: /addpromtask promo_yt1 5 Watch our YouTube video",
        )
    task_id = parts[1].strip()
    try:
        reward = int(parts[2])
    except ValueError:
        return bot.reply_to(message, "Invalid reward amount. Must be an integer.")
    title = parts[3].strip()

    try:
        existing = promo_tasks_col.find_one({"task_id": task_id})
        if existing:
            return bot.reply_to(message, f"Task '{task_id}' already exists. Use /delpromtask first.")
        promo_tasks_col.insert_one({
            "task_id": task_id,
            "title": title,
            "description": "",
            "link": "",
            "reward": reward,
            "active": True,
            "created_at": datetime.utcnow().isoformat(),
        })
        bot.reply_to(message, f"✅ Promo task '{task_id}' added with {reward} coins reward.")
    except Exception as exc:
        logger.error("add_promo_task_command error: %s", exc)
        bot.reply_to(message, "Server error. Please try again.")


@bot.message_handler(commands=["delpromtask"])
def del_promo_task_command(message):
    """Admin: /delpromtask <task_id>"""
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /delpromtask <task_id>")
    task_id = parts[1].strip()
    try:
        result = promo_tasks_col.update_one(
            {"task_id": task_id},
            {"$set": {"active": False}},
        )
        if result.matched_count:
            bot.reply_to(message, f"✅ Promo task '{task_id}' deactivated.")
        else:
            bot.reply_to(message, f"Task '{task_id}' not found.")
    except Exception as exc:
        logger.error("del_promo_task_command error: %s", exc)
        bot.reply_to(message, "Server error. Please try again.")


@bot.message_handler(commands=["listpromtasks"])
def list_promo_tasks_command(message):
    """Admin: List all active promotion tasks."""
    if int(message.from_user.id) != ADMIN_ID:
        return
    try:
        tasks = list(promo_tasks_col.find({"active": True}, {"_id": 0, "task_id": 1, "title": 1, "reward": 1}))
        if not tasks:
            return bot.reply_to(message, "No active promotion tasks.")
        lines = ["📢 *Active Promotion Tasks*\n"]
        for t in tasks:
            lines.append(f"• `{t['task_id']}` — {t['reward']} coins — {t['title']}")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        logger.error("list_promo_tasks_command error: %s", exc)
        bot.reply_to(message, "Server error. Please try again.")


@bot.message_handler(commands=["stats"])
def get_stats(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    total_u = users_col.count_documents({})
    pending_w = withdrawals_col.count_documents({"status": "Pending \u23f3"})
    today_j = users_col.count_documents({"joined": str(date.today())})
    bot.reply_to(
        message,
        f"\U0001f4ca *Bot Stats*\n\n"
        f"\U0001f465 Total Users: `{total_u}`\n"
        f"\U0001f195 Today Joined: `{today_j}`\n"
        f"\U0001f4b8 Pending Withdrawals: `{pending_w}`\n"
        f"\U0001fa99 Coin Rate: 5000 coins = ₹100",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["approve"])
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
        {"$set": {"status": "Approved \u2705"}},
    )
    if result.modified_count:
        try:
            bot.send_message(
                target_id,
                "\U0001f389 *Your withdrawal has been approved!* Payment is being processed. \u2705",
                parse_mode="Markdown",
            )
        except Exception as notify_exc:
            logger.warning("Notify failed for approved user %s: %s", target_id, notify_exc)
        bot.reply_to(message, f"\u2705 User {target_id} withdrawal approved!")
    else:
        bot.reply_to(message, "No pending withdrawal found.")


@bot.message_handler(commands=["reject"])
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
        users_col.update_one({"user_id": target_id}, {"$inc": {"coins": withdraw["amount"]}})
        withdrawals_col.update_one(
            {"user_id": target_id, "status": "Pending \u23f3"},
            {"$set": {"status": "Rejected \u274c"}},
        )
        try:
            bot.send_message(
                target_id,
                f"\u274c Your withdrawal was rejected. {withdraw['amount']} coins have been refunded.",
                parse_mode="Markdown",
            )
        except Exception as notify_exc:
            logger.warning("Notify failed for rejected user %s: %s", target_id, notify_exc)
        bot.reply_to(message, f"\u274c User {target_id} rejected. Coins refunded.")
    else:
        bot.reply_to(message, "No pending withdrawal found.")


@bot.message_handler(commands=["addcoins"])
def add_coins(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 3:
        return bot.reply_to(message, "Usage: /addcoins <user_id> <amount>")
    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        return bot.reply_to(message, "Invalid user ID or amount.")
    users_col.update_one({"user_id": target_id}, {"$inc": {"coins": amount}})
    try:
        bot.send_message(target_id, f"\U0001f381 Admin has gifted you *{amount} coins*!", parse_mode="Markdown")
    except Exception as notify_exc:
        logger.warning("Notify failed for addcoins %s: %s", target_id, notify_exc)
    bot.reply_to(message, f"\u2705 {amount} coins added to user {target_id}")


@bot.message_handler(commands=["broadcast"])
def broadcast(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    msg_text = message.text.replace("/broadcast ", "", 1)
    if not msg_text or msg_text == "/broadcast":
        return bot.reply_to(message, "Usage: /broadcast [Message]")
    all_users = list(users_col.find({}, {"user_id": 1}))
    sent, failed = 0, 0
    for u in all_users:
        try:
            bot.send_message(u["user_id"], msg_text, parse_mode="Markdown")
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
    bot.reply_to(message, f"\U0001f4e2 Sent: {sent} | Failed: {failed}")


@bot.message_handler(commands=["msg"])
def send_personal_message_cmd(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split(None, 2)
    if len(parts) < 3:
        return bot.reply_to(message, "Usage: /msg <user_id> <message text>")
    try:
        target_id = int(parts[1])
    except ValueError:
        return bot.reply_to(message, "\u274c Invalid User ID.")
    text = parts[2].strip()
    if not text:
        return bot.reply_to(message, "\u274c Message text cannot be empty.")
    try:
        bot.send_message(target_id, f"\U0001f4e9 *Message from Admin:*\n\n{text}", parse_mode="Markdown")
        bot.reply_to(message, f"\u2705 Message sent to User {target_id}!")
        logger.info("Admin sent personal message to user %s.", target_id)
    except Exception as exc:
        bot.reply_to(message, f"\u274c Failed to send message: {exc}")


@bot.message_handler(commands=["block"])
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
    users_col.update_one({"user_id": target_id}, {"$set": {"blocked": True}})
    try:
        bot.send_message(target_id, "\u26d4 Your account has been suspended for violating our terms of service.")
    except Exception as notify_exc:
        logger.warning("Notify failed for block %s: %s", target_id, notify_exc)
    bot.reply_to(message, f"\U0001f6ab User {target_id} blocked!")


@bot.message_handler(commands=["unblock"])
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
        {"$set": {"blocked": False, "fp_flagged": False, "ip_flagged": False}},
    )
    try:
        bot.send_message(target_id, "\u2705 Your account has been unblocked!", parse_mode="Markdown")
    except Exception as notify_exc:
        logger.warning("Notify failed for unblock %s: %s", target_id, notify_exc)
    bot.reply_to(message, f"\u2705 User {target_id} unblocked!")


@bot.message_handler(commands=["settask"])
def set_task_code(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 3:
        return bot.reply_to(message, "Usage: /settask <task_id> <new_code>")
    task_id = parts[1].lower()
    new_code = parts[2].upper()
    if task_id not in TASK_CODES:
        return bot.reply_to(message, f"Invalid task ID. Valid: {', '.join(TASK_CODES.keys())}")
    TASK_CODES[task_id] = new_code
    global _task_codes_cache, _task_codes_cache_time
    _task_codes_cache = None
    _task_codes_cache_time = 0.0
    bot.reply_to(message, f"\u2705 Task `{task_id}` code updated to `{new_code}`!", parse_mode="Markdown")


@bot.message_handler(commands=["penalty"])
def penalize_user(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /penalty <user_id> [amount]\nDefault: 200 coins")
    try:
        target_id = int(parts[1])
        amount = int(parts[2]) if len(parts) >= 3 else 200
        if amount <= 0:
            return bot.reply_to(message, "Amount must be greater than 0.")
    except ValueError:
        return bot.reply_to(message, "Invalid user ID or amount.")

    user = users_col.find_one({"user_id": target_id})
    if not user:
        return bot.reply_to(message, f"User {target_id} not found.")

    current = user.get("coins", 0)
    new_bal = max(0, current - amount)
    deducted = current - new_bal
    users_col.update_one({"user_id": target_id}, {"$set": {"coins": new_bal}})
    try:
        bot.send_message(
            target_id,
            f"\u26a0\ufe0f *Penalty Applied!*\n\n"
            f"`{deducted}` coins deducted.\nNew Balance: `{new_bal}` \U0001fa99\n\nReason: Rule violation.",
            parse_mode="Markdown",
        )
    except Exception as notify_exc:
        logger.warning("Notify failed for penalty %s: %s", target_id, notify_exc)
    bot.reply_to(message, f"\u26a0\ufe0f Penalty applied: {deducted} coins deducted from user {target_id}. New balance: {new_bal}.")


@bot.message_handler(commands=["addcodefilter"])
def add_code_filter_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /addcodefilter CODE\nExample: /addcodefilter ABC")
    code = parts[1].strip()
    if not code or len(code) > 80:
        return bot.reply_to(message, "Invalid code. Maximum length is 80 characters.")
    pattern = rf"\b{re.escape(code)}\b"
    try:
        code_filter_rules_col.update_one(
            {"pattern": pattern},
            {
                "$set": {
                    "pattern": pattern,
                    "label": code,
                    "active": True,
                    "created_at": datetime.utcnow().isoformat(),
                    "created_by": ADMIN_ID,
                }
            },
            upsert=True,
        )
        bot.reply_to(message, f"Code filter added: {code}")
    except Exception as exc:
        logger.error("add_code_filter_command error: %s", exc)
        bot.reply_to(message, "Server error while adding code filter.")


@bot.message_handler(commands=["addcodepattern"])
def add_code_pattern_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return bot.reply_to(message, r"Usage: /addcodepattern REGEX")
    pattern = parts[1].strip()
    if not pattern or len(pattern) > 200:
        return bot.reply_to(message, "Invalid pattern. Maximum length is 200 characters.")
    try:
        re.compile(pattern)
        code_filter_rules_col.update_one(
            {"pattern": pattern},
            {
                "$set": {
                    "pattern": pattern,
                    "label": pattern,
                    "active": True,
                    "created_at": datetime.utcnow().isoformat(),
                    "created_by": ADMIN_ID,
                }
            },
            upsert=True,
        )
        bot.reply_to(message, f"Code pattern added: {pattern}")
    except re.error as exc:
        bot.reply_to(message, f"Invalid regex pattern: {exc}")
    except Exception as exc:
        logger.error("add_code_pattern_command error: %s", exc)
        bot.reply_to(message, "Server error while adding code pattern.")


@bot.message_handler(commands=["listcodefilters"])
def list_code_filters_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    try:
        rules = list(code_filter_rules_col.find({"active": True}, {"_id": 0, "label": 1, "pattern": 1}).limit(50))
        lines = [
            "Active group code filters:",
            "Default: uppercase 3-letter codes",
            "Default: 3-number codes",
        ]
        for idx, rule in enumerate(rules, start=1):
            lines.append(f"{idx}. {rule.get('label') or rule.get('pattern')}")
        bot.reply_to(message, "\n".join(lines))
    except Exception as exc:
        logger.error("list_code_filters_command error: %s", exc)
        bot.reply_to(message, "Server error while listing code filters.")


@bot.message_handler(commands=["delcodefilter"])
def delete_code_filter_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /delcodefilter CODE")
    code = parts[1].strip()
    exact_pattern = rf"\b{re.escape(code)}\b"
    try:
        result = code_filter_rules_col.update_many(
            {"$or": [{"label": code}, {"pattern": code}, {"pattern": exact_pattern}]},
            {"$set": {"active": False, "deleted_at": datetime.utcnow().isoformat()}},
        )
        if result.modified_count:
            bot.reply_to(message, f"Code filter removed: {code}")
        else:
            bot.reply_to(message, "No matching custom code filter found.")
    except Exception as exc:
        logger.error("delete_code_filter_command error: %s", exc)
        bot.reply_to(message, "Server error while deleting code filter.")


@bot.message_handler(commands=["resetcodeviolations"])
def reset_code_violations_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    if not is_group_chat(message):
        return bot.reply_to(message, "Use this command inside the group where you want to reset violations.")
    group_code_violations_col.delete_many({"chat_id": message.chat.id})
    bot.reply_to(message, "Code violation counts reset for this group.")


@bot.message_handler(
    func=lambda message: (
        GROUP_CODE_FILTER_ENABLED
        and is_group_chat(message)
        and bool(getattr(message, "text", ""))
        and not message.text.strip().startswith("/")
    ),
    content_types=["text"],
)
def group_code_filter_handler(message):
    text = message.text or ""
    matched_pattern = message_matches_group_code_filter(text)
    if not matched_pattern:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    can_delete, can_ban = bot_has_group_moderation_rights(chat_id)

    if not can_delete:
        logger.warning("Group code filter matched in %s but bot cannot delete messages.", chat_id)
        return

    try:
        bot.delete_message(chat_id, message.message_id)
        logger.info(
            "Deleted group code message | chat_id: %s | user_id: %s | pattern: %s",
            chat_id, user_id, matched_pattern,
        )
    except Exception as exc:
        logger.warning("Failed to delete group code message in %s from %s: %s", chat_id, user_id, exc)
        return

    violations = record_group_code_violation(chat_id, user_id)
    if violations <= GROUP_CODE_MAX_VIOLATIONS:
        return

    try:
        member = bot.get_chat_member(chat_id, user_id)
        if member.status in ("administrator", "creator"):
            logger.warning("User %s exceeded code violations in %s but is group admin/creator.", user_id, chat_id)
            return
    except Exception as exc:
        logger.warning("Unable to verify user role before ban for %s in %s: %s", user_id, chat_id, exc)

    if not can_ban:
        logger.warning("User %s exceeded code violations in %s but bot cannot ban users.", user_id, chat_id)
        return

    try:
        bot.ban_chat_member(chat_id, user_id)
        logger.info("Banned user %s from group %s after %s code violations.", user_id, chat_id, violations)
    except Exception as exc:
        logger.error("Failed to ban user %s from group %s: %s", user_id, chat_id, exc)


# ============================================================
# 21. BOT POLLING THREAD
# ============================================================

def run_bot() -> None:
    instance_id = f"{os.getenv('RENDER_INSTANCE_ID') or os.getenv('HOSTNAME') or os.getpid()}-{int(time.time())}"
    logger.info("Starting Telegram bot polling worker: %s", instance_id)
    while True:
        if not acquire_bot_polling_lock(instance_id):
            logger.warning("Another bot polling instance is already active. Retrying in 30s...")
            time.sleep(30)
            continue

        stop_event = threading.Event()
        Thread(target=refresh_bot_polling_lock, args=(instance_id, stop_event), daemon=True).start()

        try:
            try:
                bot.remove_webhook()
            except Exception as webhook_exc:
                logger.warning("Could not remove webhook before polling: %s", webhook_exc)

            bot.polling(none_stop=True, interval=1, timeout=20)
        except Exception as exc:
            error_text = str(exc)
            if "409" in error_text or "Conflict" in error_text or "getUpdates" in error_text:
                logger.error("Telegram polling conflict detected. Retrying in 60s: %s", exc)
                time.sleep(60)
            else:
                logger.error("Bot polling crashed: %s. Restarting in 5s...", exc)
                time.sleep(5)
        finally:
            stop_event.set()
            release_bot_polling_lock(instance_id)


def acquire_bot_polling_lock(instance_id: str, lease_seconds: int = 90) -> bool:
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=lease_seconds)
    try:
        lock = config_col.find_one_and_update(
            {
                "_id": "bot_polling_lock",
                "$or": [
                    {"expires_at": {"$lte": now}},
                    {"instance_id": instance_id},
                    {"expires_at": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "instance_id": instance_id,
                    "expires_at": expires_at,
                    "updated_at": now.isoformat(),
                }
            },
            upsert=True,
            return_document=pymongo.ReturnDocument.AFTER,
        )
        return bool(lock and lock.get("instance_id") == instance_id)
    except pymongo.errors.DuplicateKeyError:
        return False
    except Exception as exc:
        logger.error("Bot polling lock acquire failed: %s", exc)
        return False


def refresh_bot_polling_lock(instance_id: str, stop_event: threading.Event, lease_seconds: int = 90) -> None:
    while not stop_event.wait(30):
        now = datetime.utcnow()
        try:
            config_col.update_one(
                {"_id": "bot_polling_lock", "instance_id": instance_id},
                {
                    "$set": {
                        "expires_at": now + timedelta(seconds=lease_seconds),
                        "updated_at": now.isoformat(),
                    }
                },
            )
        except Exception as exc:
            logger.warning("Bot polling lock refresh failed: %s", exc)


def release_bot_polling_lock(instance_id: str) -> None:
    try:
        config_col.delete_one({"_id": "bot_polling_lock", "instance_id": instance_id})
    except Exception as exc:
        logger.warning("Bot polling lock release failed: %s", exc)


# ============================================================
# 22. UPTIME PING
# ============================================================

def uptime_ping() -> None:
    if not RENDER_URL:
        return
    while True:
        try:
            req_lib.get(RENDER_URL, timeout=10)
            logger.debug("Uptime ping sent.")
        except Exception as exc:
            logger.warning("Uptime ping failed: %s", exc)
        time.sleep(600)


# ============================================================
# 23. ENTRY POINT
# ============================================================

if __name__ == "__main__":
    Thread(target=run_bot, daemon=True).start()
    Thread(target=uptime_ping, daemon=True).start()
    Thread(target=refresh_leaderboard_loop, daemon=True).start()
    Thread(target=_cleanup_rate_cache, daemon=True).start()
    port = int(os.getenv("PORT", 5000))
    logger.info("Starting Flask on port %s...", port)
    app.run(host="0.0.0.0", port=port, debug=False)
