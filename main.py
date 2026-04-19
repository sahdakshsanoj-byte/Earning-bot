"""Nana tu wapas Ja
main.py — Daksh Grand Earn Bot
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
# Webhook secret — auto-derived from BOT_TOKEN so you don't need to set it manually
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

    # Collections
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

    # TTL index on rate_limits — auto-expire documents
    try:
        rate_col.create_index("expires_at", expireAfterSeconds=0)
        ad_reward_tokens_col.create_index("expires_at", expireAfterSeconds=0)
        logger.info("TTL index ensured on rate_limits.expires_at")
    except Exception as idx_err:
        logger.warning("TTL index creation skipped (may already exist): %s", idx_err)

    # Performance indexes
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
        logger.info("MongoDB indexes ensured.")
    except Exception as idx_err:
        logger.warning("Index creation warning: %s", idx_err)

    logger.info("MongoDB connected successfully.")
except Exception as db_exc:
    logger.error("MongoDB connection failed: %s", db_exc)
    raise


# ============================================================
# 4. CONSTANTS
# ============================================================

TASK_CODES = {
    "yt1": "CODE1",
    "yt2": "CODE2",
    "yt3": "CODE3",
    "web1": "DASH98",
    "web2": "GYM567",
    "web3": "SHU234",
    "partner1": "PARTNER1",
    "partner2": "PARTNER2",
}

TASK_REWARDS = {
    "yt1": 20,
    "yt2": 20,
    "yt3": 20,
    "web1": 15,
    "web2": 15,
    "web3": 15,
    "partner1": 15,
    "partner2": 15,
}

CHANNEL_REWARDS = {
    "official": 30,
    "channel2": 20,
    "channel3": 20,
    "slot1": 10,
    "slot2": 10,
}

MAX_ADS_PER_DAY = 5
AD_COIN_REWARD = 10
AD_CLAIM_TOKEN_TTL_SECONDS = 300
MIN_WITHDRAW = 4000
MAX_WITHDRAW = 100000
WITHDRAW_COOLDOWN = 86400     # 24 hours
SUPPORT_MAX_MSGS = 1          # 1 message per day
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
# Replaces MongoDB for rate limiting — zero DB roundtrips per request.
# A background thread cleans up expired entries every 5 minutes.
# For withdrawal cooldown (24h), we also do a DB check to survive restarts.

_rate_cache: dict = {}
_rate_cache_lock = threading.Lock()


def is_rate_limited(key: str, cooldown_seconds: int) -> bool:
    """Check and set a rate limit using an in-memory TTL cache.

    This replaces the previous MongoDB-backed rate limiting to reduce
    database load. Each check is now a pure in-memory dict lookup — no
    network call required.

    Args:
        key: Unique string key for this rate-limit (e.g. ``"claim_12345"``).
        cooldown_seconds: How many seconds to block after first use.

    Returns:
        bool: True if currently rate-limited (request should be rejected),
              False if allowed (rate limit has been set for next call).
    """
    now = time.time()
    with _rate_cache_lock:
        expires = _rate_cache.get(key, 0.0)
        if expires > now:
            return True
        _rate_cache[key] = now + cooldown_seconds
        return False


def _cleanup_rate_cache() -> None:
    """Background thread: remove expired entries from the rate cache every 5 min.

    Prevents unbounded memory growth when many unique keys accumulate.
    """
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
# 6. LEADERBOARD CACHE (in-memory, refreshed every 10 minutes)
# ============================================================

_leaderboard_cache = "none"
_leaderboard_cache_time = 0.0
LEADERBOARD_TTL = 600  # seconds


def get_leaderboard_cached() -> str:
    """Return the leaderboard string from in-memory cache.

    Refreshes the cache if it is older than LEADERBOARD_TTL seconds.

    Returns:
        str: Pipe-separated leaderboard string (e.g. "123:500|456:400").
             Returns "none" if no users are found or on error.
    """
    global _leaderboard_cache, _leaderboard_cache_time
    now = time.time()
    if now - _leaderboard_cache_time < LEADERBOARD_TTL:
        return _leaderboard_cache
    _leaderboard_cache = get_leaderboard()
    _leaderboard_cache_time = now
    return _leaderboard_cache


def refresh_leaderboard_loop() -> None:
    """Background thread that refreshes the leaderboard cache every 10 minutes."""
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
# 6. TASK CODES CACHE (5-minute in-memory cache)
# ============================================================

_task_codes_cache = None
_task_codes_cache_time = 0.0
TASK_CODES_CACHE_TTL = 300  # seconds


def get_live_task_codes() -> dict:
    """Return task verification codes from MongoDB with 5-minute cache.

    Falls back to the hardcoded TASK_CODES dict if MongoDB is unavailable
    or if the config document does not exist.

    Returns:
        dict: Mapping of task_id -> verification_code (uppercase strings).
    """
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
    """Validate the X-Admin-Token header in a Flask request.

    Args:
        req: Flask request object.

    Returns:
        bool: True if the token matches ADMIN_TOKEN and ADMIN_TOKEN is non-empty.
    """
    return (
        req.headers.get("X-Admin-Token", "").strip() == ADMIN_TOKEN
        and ADMIN_TOKEN != ""
    )


# ============================================================
# 8. TELEGRAM INIT DATA VERIFICATION
# ============================================================

def verify_telegram_init_data(init_data: str) -> dict | None:
    """Verify and parse Telegram Web App initData string.

    Validates the HMAC signature and checks that auth_date is within 10 minutes.

    Args:
        init_data: URL-encoded initData string from Telegram.WebApp.initData.

    Returns:
        dict: Parsed parameters if verification succeeds, None otherwise.
    """
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
    """Extract and verify the Telegram user ID from request data.

    Prefers verified initData over raw user_id for security.

    Args:
        request_data: Parsed JSON body from the Flask request.

    Returns:
        int: Verified user ID, or None if verification fails.
    """
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
# 9. RATE LIMITING
# ============================================================

def is_rate_limited(key: str, cooldown_seconds: int) -> bool:
    """Check if a given key is currently rate-limited.

    Uses MongoDB TTL documents. Creates a new limit document if not limited.

    Args:
        key: Unique string key for this rate-limit check (e.g. "claim_12345").
        cooldown_seconds: Duration in seconds to block subsequent requests.

    Returns:
        bool: True if the key is currently rate-limited, False otherwise.
    """
    now = datetime.utcnow()
    try:
        doc = rate_col.find_one({"_id": key})
        if doc and doc.get("expires_at") > now:
            return True
        rate_col.update_one(
            {"_id": key},
            {"$set": {"expires_at": now + timedelta(seconds=cooldown_seconds)}},
            upsert=True,
        )
        return False
    except Exception as exc:
        logger.error("Rate limit check error for key '%s': %s", key, exc)
        return False


def check_support_limit(user_id: int) -> tuple[bool, str]:
    """Check whether a user is allowed to send a support message.

    Allows 1 message per 24-hour window. Resets the window automatically
    when it expires.

    Args:
        user_id: Telegram user ID.

    Returns:
        tuple: (allowed: bool, message: str).
               message is an empty string if allowed, or an explanation if not.
    """
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
    """Check if a user is temporarily blocked from attempting a task.

    A block is placed after TASK_MAX_FAILS consecutive wrong attempts.

    Args:
        user_id: Telegram user ID.
        task_id: Task identifier (e.g. "yt1").

    Returns:
        bool: True if the user is blocked from this task, False otherwise.
    """
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
    """Record a failed task attempt and block the user if max fails reached.

    After TASK_MAX_FAILS consecutive failures, the user is blocked for
    TASK_FAIL_COOLDOWN seconds. The fail counter is reset after the block is set.

    Args:
        user_id: Telegram user ID.
        task_id: Task identifier.
    """
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
    """Clear the fail counter for a user-task pair after a successful attempt.

    Args:
        user_id: Telegram user ID.
        task_id: Task identifier.
    """
    counter_key = f"task_fail_{user_id}_{task_id}"
    try:
        rate_col.delete_one({"_id": counter_key})
    except Exception as exc:
        logger.error("Clear task fail error: %s", exc)


# ============================================================
# 11. CHANNEL MEMBERSHIP VERIFICATION
# ============================================================

def extract_channel_username(url: str) -> str | None:
    """Extract the Telegram @username from a t.me URL.

    Skips invite/joinchat links that cannot be used for membership checks.

    Args:
        url: Full Telegram channel URL (e.g. "https://t.me/mychannel").

    Returns:
        str: Channel username with '@' prefix, or None if not extractable.
    """
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
    """Check whether a user is a member of a Telegram channel, with retries.

    Telegram's API sometimes reflects a new channel join with a 2-4 second
    delay. This function retries up to ``max_retries`` times with
    ``retry_delay`` seconds between each attempt so that a legitimate join
    is not incorrectly rejected due to API lag.

    Fails closed (returns False) after all retries are exhausted, to prevent
    granting rewards without a confirmed join.

    Requirements:
        - The bot must be an admin in **private** channels to call
          get_chat_member. For **public** channels, bot membership is enough.
        - Add the bot as admin to all channels you want to verify.

    Args:
        channel_id_or_username: Channel @username or numeric chat ID.
        user_id: Telegram user ID to verify.
        max_retries: Number of total attempts (default 3).
        retry_delay: Seconds to wait between failed attempts (default 2.0).

    Returns:
        bool: True if the user is a confirmed member, False otherwise.
    """
    for attempt in range(1, max_retries + 1):
        try:
            member = bot.get_chat_member(channel_id_or_username, user_id)
            if member.status in ("member", "administrator", "creator"):
                return True
            # User found but not a member yet — Telegram API lag possible
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
    """Strip and truncate a string to a maximum length.

    Args:
        value: Input string to sanitize.
        max_length: Maximum allowed length after stripping. Defaults to 1000.

    Returns:
        str: Sanitized string, or empty string if value is not a string.
    """
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
    """Add security headers to every Flask response.

    Args:
        response: Flask response object.

    Returns:
        Flask response with security headers attached.
    """
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ============================================================
# 15. HELPER FUNCTIONS
# ============================================================

def get_leaderboard() -> str:
    """Fetch the top-10 users by coins from MongoDB.

    Returns:
        str: Pipe-separated string in format "user_id:coins|user_id:coins".
             Returns "none" if no users found or on error.
    """
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
    """Get a comma-separated list of user IDs referred by this user.

    Args:
        user_id: Telegram user ID of the referrer.

    Returns:
        str: Comma-separated referred user IDs, or empty string if none.
    """
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
    """Send a direct referral link and Telegram share button."""
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
    """Retrieve an existing user or create a new one with optional referral credit.

    If the user is new and a valid referrer_id is provided, the referrer
    receives 50 coins and their referral_count is incremented atomically.

    Args:
        user_id: Telegram user ID of the new user.
        username: Display name (first_name) of the new user.
        referrer_id: Telegram user ID of the referrer, or None.

    Returns:
        dict: User document. Returns empty dict on error.
    """
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
                "last_claim_ts": "",
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


# ============================================================
# 16. STATS CACHE
# ============================================================

_stats_cache: dict = {}
_stats_cache_time: float = 0.0
STATS_CACHE_TTL = 60  # seconds


# ============================================================
# 17. FLASK API ROUTES
# ============================================================

@app.route("/")
def home():
    """Health check endpoint.

    Returns:
        JSON: {"status": "ok", "message": "Bot is Running Live!"}
    """
    return jsonify({"status": "ok", "message": "Bot is Running Live!"})


@app.route("/get_user/<int:user_id>")
def get_user_data_api(user_id: int):
    """Fetch user data for the frontend dashboard.

    Includes coins, leaderboard, referrals, completed tasks today,
    daily bonus timestamp, ad counter, and channel claims.

    Args:
        user_id: Telegram user ID (URL path parameter).

    Returns:
        JSON: User data dict with status "success", or error with HTTP code.
    """
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
            }
        )
    except Exception as exc:
        logger.error("get_user error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


@app.route("/get_leaderboard")
def get_leaderboard_api():
    """Return the cached leaderboard data.

    Returns:
        JSON: {"status": "success", "leaderboard": "<pipe-separated string>"}
    """
    return jsonify({"status": "success", "leaderboard": get_leaderboard_cached()})


@app.route("/claim_daily/<int:user_id>", methods=["POST"])
def claim_daily_api(user_id: int):
    """Award 10 daily bonus coins to the user.

    Only one claim is allowed per 24 hours.

    Args:
        user_id: Telegram user ID (URL path parameter).

    Returns:
        JSON: Success with coins credited, or error with remaining time.
    """
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
                    h = int(remaining.total_seconds() // 3600)
                    m = int((remaining.total_seconds() % 3600) // 60)
                    return (
                        jsonify({"status": "error", "message": f"Already claimed! Come back in {h}h {m}m."}),
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


@app.route("/withdraw", methods=["POST"])
def withdraw_api():
    """Process a coin withdrawal request.

    Validates UPI ID, coin balance, referral count, and 24h cooldown.
    Deducts coins atomically and notifies the admin.

    Returns:
        JSON: Success or error with descriptive message.
    """
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
        return jsonify({"status": "error", "message": f"Minimum withdrawal is {MIN_WITHDRAW} coins."}), 400
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

    withdrawal = {
        "user_id": user_id,
        "upi_id": upi_id,
        "amount": requested_amount,
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
            f"Requested: `{requested_amount}` coins\n"
            f"Remaining Balance: `{result.get('coins', 0)}` coins\n"
            f"Date: {withdrawal['date']}",
            parse_mode="Markdown",
        )
    except Exception as notify_exc:
        logger.warning("Admin notify failed for withdrawal: %s", notify_exc)

    return jsonify({"status": "success", "message": "Withdrawal request submitted successfully!"})


@app.route("/get_history/<int:user_id>")
def get_history_api(user_id: int):
    """Fetch the last 10 withdrawal records for a user.

    Args:
        user_id: Telegram user ID (URL path parameter).

    Returns:
        JSON: List of withdrawal history records.
    """
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

    Applies rate limiting and fail-attempt tracking. Task completions
    are stored per-day so the same task can be completed again next day.

    Returns:
        JSON: Success with reward amount, or error with descriptive message.
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
        existing = task_completions.get(task_id, {})

        if (
            isinstance(existing, dict)
            and existing.get("date") == today
            and existing.get("code") == correct_code
        ):
            return jsonify({"status": "error", "message": "Task already completed today! Come back tomorrow."}), 400

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


def create_ad_claim_token(user_id: int) -> tuple[dict, int]:
    """Create a short-lived one-time token before showing a Monetag ad."""
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

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.utcnow() + timedelta(seconds=AD_CLAIM_TOKEN_TTL_SECONDS)
        ad_reward_tokens_col.insert_one({
            "_id": token_hash,
            "user_id": user_id,
            "created_at": datetime.utcnow(),
            "expires_at": expires_at,
            "source": "monetag",
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
    """Credit coins only after Monetag confirms a valued ad event."""
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
            "message": f"{AD_COIN_REWARD} coins earned! ({done}/{MAX_ADS_PER_DAY} ad rewards claimed today)",
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
    """Reward endpoint called only after Monetag ad completion."""
    data = request.get_json(silent=True) or {}
    payload, status_code = manual_ad_reward(user_id, (data.get("token") or "").strip())
    return jsonify(payload), status_code


@app.route("/ad_claim_token/<int:user_id>", methods=["POST"])
def ad_claim_token_api(user_id: int):
    """Create a short-lived one-time token for a Monetag ad reward attempt."""
    payload, status_code = create_ad_claim_token(user_id)
    return jsonify(payload), status_code


@app.route("/claim_channel", methods=["POST"])
def claim_channel_api():
    """Claim coins for joining a Telegram channel.

    Sponsor slots (slot1, slot2) support reclaiming if the link changes.
    Regular channels allow only a one-time claim.

    Returns:
        JSON: Success with reward, "not_joined" if membership check fails,
              or error with descriptive message.
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
    claimed_link = sanitize_text(data.get("claimed_link", ""), max_length=500)

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
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        channel_claims = user.get("channel_claims", {})
        existing_claim = channel_claims.get(channel_id)

        if channel_id in ("slot1", "slot2"):
            if existing_claim and isinstance(existing_claim, dict):
                stored_link = existing_claim.get("claimed_link", "")
                if stored_link == claimed_link and stored_link != "":
                    return jsonify({"status": "error", "message": "Reward already claimed for this slot! ✅"}), 400
        else:
            if existing_claim:
                return jsonify({"status": "error", "message": "Reward already claimed for this channel! ✅"}), 400

        if channel_url:
            ch_username = extract_channel_username(channel_url)
            if ch_username:
                is_member = verify_channel_membership(ch_username, user_id)
                if not is_member:
                    return jsonify(
                        {"status": "not_joined", "message": "Please join the channel first, then tap Retry!"}
                    ), 400

        reward = CHANNEL_REWARDS[channel_id]
        if channel_id in ("slot1", "slot2"):
            claim_value = {"claimed_link": claimed_link, "claimed_at": datetime.utcnow().isoformat()}
        else:
            claim_value = True

        users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"coins": reward}, "$set": {f"channel_claims.{channel_id}": claim_value}},
        )
        return jsonify(
            {
                "status": "success",
                "message": f"{reward} coins credited for joining the channel!",
                "data": {"reward": reward},
            }
        )
    except Exception as exc:
        logger.error("claim_channel error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/click_sponsor", methods=["POST"])
def click_sponsor_api():
    """Track unique user clicks on a sponsor slot link.

    Click count resets automatically when the sponsor link changes.
    Each user is counted at most once per slot per 10 minutes.

    Returns:
        JSON: {"status": "ok"} always (non-critical tracking endpoint).
    """
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
    """Check device fingerprint and IP for duplicate account detection.

    Flags suspicious accounts and notifies the admin. Stores the
    fingerprint and IP on the user document for future checks.

    Returns:
        JSON: {"status": "ok"} or {"status": "blocked"}.
    """
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
                    f"\u26a0\ufe0f *IP Conflict Detected*\nUser `{user_id}` shares IP with another account.\n"
                    f"IP: `{ip}`\n_Manual review recommended._",
                    parse_mode="Markdown",
                )
            except Exception as notify_exc:
                logger.warning("Admin notify failed: %s", notify_exc)

        if fp_conflict:
            users_col.update_one({"user_id": user_id}, {"$set": {"fp_flagged": True}})

        users_col.update_one(
            {"user_id": user_id},
            {"$set": {"fingerprint": fingerprint, "ip": ip}},
            upsert=False,
        )
        return jsonify({"status": "ok"})
    except Exception as exc:
        logger.error("check_device error for %s: %s", user_id, exc)
        return jsonify({"status": "ok"})


@app.route("/send_support", methods=["POST"])
def send_support_api():
    """Forward a support message from a user to the admin.

    Enforces a rate limit of 1 message per 24-hour window.

    Returns:
        JSON: Success or error with descriptive message.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    user_id_raw = data.get("user_id")
    message_text = sanitize_text(data.get("message", ""), max_length=1000)

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
        support_messages_col.insert_one({
            "user_id":    user_id,
            "message":    message_text,
            "created_at": datetime.utcnow(),
            "date":       datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC"),
            "replied":    False,
        })
        bot.send_message(
            ADMIN_ID,
            f"\U0001f3a7 *Support Message*\nFrom: `{user_id}`\n\n{message_text}",
            parse_mode="Markdown",
        )
        return jsonify({"status": "success", "message": "Your message has been sent to Admin!"})
    except Exception as exc:
        logger.error("send_support error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Failed to send message. Please try again."}), 500


# ============================================================
# 18. PROMO CODE API ENDPOINT
# ============================================================

@app.route("/redeem_promo", methods=["POST"])
def redeem_promo_api():
    """Redeem a promo code and credit coins to the user.

    Each promo code can only be used once per user. Supports optional
    max_uses limit set during code creation.

    Expected JSON body:
        {
            "user_id": <int>,
            "code": "<str>"
        }

    Returns:
        JSON: Success with coins credited, or error with descriptive message.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    raw_code = sanitize_text(data.get("code", ""), max_length=50)
    code = raw_code.upper()

    if not code:
        return jsonify({"status": "error", "message": "Please enter a promo code."}), 400

    if is_rate_limited(f"promo_{user_id}", 10):
        return jsonify({"status": "error", "message": "Please wait before trying again."}), 429

    try:
        user = users_col.find_one({"user_id": user_id}, {"blocked": 1})
        if not user:
            return jsonify({"status": "error", "message": "User not found. Use /start first."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been suspended."}), 403

        promo = promos_col.find_one({"code": code})

        if not promo:
            return jsonify({"status": "error", "message": "Invalid promo code. Please check and try again."}), 400

        if not promo.get("active", True):
            return jsonify({"status": "error", "message": "This promo code has expired or been deactivated."}), 400

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
        return jsonify(
            {
                "status": "success",
                "message": f"🎉 {coins} coins credited to your balance!",
                "data": {"coins": coins},
            }
        )
    except Exception as exc:
        logger.error("redeem_promo error for user %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


# ============================================================
# 19. MODERATOR PANEL API ROUTES
# ============================================================

def check_mod_token(req) -> bool:
    """Verify the X-Mod-Token header for moderator panel access.

    Args:
        req: Flask request object.

    Returns:
        bool: True if the token matches MOD_TOKEN, False otherwise.
    """
    if not MOD_TOKEN:
        return False
    token = req.headers.get("X-Mod-Token", "").strip()
    return hmac.compare_digest(token, MOD_TOKEN)


@app.route("/mod/withdrawals")
def mod_list_withdrawals():
    """List withdrawals filtered by status (default: Pending).

    Requires X-Mod-Token header. Returns up to 50 most recent records.

    Returns:
        JSON: List of withdrawal documents (excluding MongoDB _id as ObjectId).
    """
    if not check_mod_token(request):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    status_filter = request.args.get("status", "Pending")
    try:
        query = {}
        if status_filter and status_filter != "all":
            query["status"] = {"$regex": status_filter, "$options": "i"}
        docs = list(
            withdrawals_col.find(query, {"_id": 1, "user_id": 1, "upi_id": 1, "amount": 1, "status": 1, "date": 1})
            .sort("_id", -1)
            .limit(50)
        )
        for d in docs:
            d["_id"] = str(d["_id"])
        return jsonify({"status": "success", "withdrawals": docs})
    except Exception as exc:
        logger.error("mod_list_withdrawals error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/mod/action_withdrawal", methods=["POST"])
def mod_action_withdrawal():
    """Approve or reject a withdrawal request.

    Requires X-Mod-Token header.

    Expected JSON body:
        {
            "withdrawal_id": "<str>",
            "action": "approve" | "reject"
        }

    Returns:
        JSON: Success or error message.
    """
    if not check_mod_token(request):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    wd_id_str = data.get("withdrawal_id", "")
    action    = data.get("action", "").lower()

    if action not in ("approve", "reject"):
        return jsonify({"status": "error", "message": "Action must be 'approve' or 'reject'"}), 400

    try:
        from bson import ObjectId
        wd_id = ObjectId(wd_id_str)
    except Exception:
        return jsonify({"status": "error", "message": "Invalid withdrawal ID"}), 400

    try:
        new_status = "Approved ✅" if action == "approve" else "Rejected ❌"
        result = withdrawals_col.find_one_and_update(
            {"_id": wd_id},
            {"$set": {"status": new_status}},
            return_document=True,
        )
        if not result:
            return jsonify({"status": "error", "message": "Withdrawal not found"}), 404

        try:
            bot.send_message(
                result["user_id"],
                f"{'✅' if action == 'approve' else '❌'} *Your withdrawal request has been {action}d!*\n\n"
                f"Amount: `{result['amount']}` coins\n"
                f"UPI: `{result['upi_id']}`",
                parse_mode="Markdown",
            )
        except Exception as notify_exc:
            logger.warning("Mod withdrawal notify failed: %s", notify_exc)

        logger.info("Mod %sd withdrawal %s for user %s.", action, wd_id_str, result.get("user_id"))
        return jsonify({"status": "success", "message": f"Withdrawal {action}d successfully."})
    except Exception as exc:
        logger.error("mod_action_withdrawal error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/mod/support_messages")
def mod_list_support():
    """List recent support messages.

    Requires X-Mod-Token header. Returns up to 50 most recent messages.

    Returns:
        JSON: List of support message documents.
    """
    if not check_mod_token(request):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    try:
        docs = list(
            support_messages_col.find(
                {},
                {"_id": 0, "user_id": 1, "message": 1, "date": 1, "replied": 1}
            )
            .sort("created_at", -1)
            .limit(50)
        )
        return jsonify({"status": "success", "messages": docs})
    except Exception as exc:
        logger.error("mod_list_support error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/mod/reply_support", methods=["POST"])
def mod_reply_support():
    """Send a reply to a user via Telegram bot.

    Requires X-Mod-Token header. The reply is sent directly to the user's
    Telegram chat.

    Expected JSON body:
        {
            "user_id": <int>,
            "message": "<str>"
        }

    Returns:
        JSON: Success or error message.
    """
    if not check_mod_token(request):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400

    reply_text = sanitize_text(data.get("message", ""), max_length=1000)
    if not reply_text:
        return jsonify({"status": "error", "message": "Message cannot be empty"}), 400

    try:
        bot.send_message(
            user_id,
            f"\U0001f3a7 *Support Reply*\n\n{reply_text}",
            parse_mode="Markdown",
        )
        support_messages_col.update_many(
            {"user_id": user_id, "replied": False},
            {"$set": {"replied": True}},
        )
        logger.info("Mod replied to user %s.", user_id)
        return jsonify({"status": "success", "message": f"Reply sent to user {user_id}."})
    except Exception as exc:
        logger.error("mod_reply_support error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Failed to send reply. Check the user ID."}), 500


@app.route("/mod/unban", methods=["POST"])
def mod_unban_user():
    """Unban (unblock) a user account.

    Requires X-Mod-Token header. Moderators cannot ban — only unban.

    Expected JSON body:
        {
            "user_id": <int>
        }

    Returns:
        JSON: Success or error message.
    """
    if not check_mod_token(request):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400

    try:
        result = users_col.update_one(
            {"user_id": user_id},
            {"$set": {"blocked": False}},
        )
        if result.matched_count == 0:
            return jsonify({"status": "error", "message": "User not found in database"}), 404

        try:
            bot.send_message(
                user_id,
                "\U0001f513 *Your account has been unbanned!*\n\nYou can now use the bot again.",
                parse_mode="Markdown",
            )
        except Exception as notify_exc:
            logger.warning("Mod unban notify failed for user %s: %s", user_id, notify_exc)

        logger.info("Mod unbanned user %s.", user_id)
        return jsonify({"status": "success", "message": f"User {user_id} has been unbanned."})
    except Exception as exc:
        logger.error("mod_unban error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/mod/send_message", methods=["POST"])
def mod_send_message():
    """Send a personal Telegram message to a specific user.

    Requires X-Mod-Token header. The message is delivered via the bot
    and appears as a notification to the target user.

    Expected JSON body:
        {
            "user_id": <int>,
            "message": <str>   (max 1000 characters)
        }

    Returns:
        JSON: Success or error message.
    """
    if not check_mod_token(request):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    target_id_raw = data.get("user_id")
    text = sanitize_text(data.get("message", "")).strip()

    if not target_id_raw or not text:
        return jsonify({"status": "error", "message": "Missing user_id or message"}), 400

    if len(text) > 1000:
        return jsonify({"status": "error", "message": "Message too long (max 1000 characters)"}), 400

    try:
        target_id = int(target_id_raw)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400

    try:
        user = users_col.find_one({"user_id": target_id}, {"_id": 0, "user_id": 1})
        if not user:
            return jsonify({"status": "error", "message": "User not found in database"}), 404

        bot.send_message(
            target_id,
            f"\U0001f4e9 *Message from Admin:*\n\n{text}",
            parse_mode="Markdown",
        )
        logger.info("Mod sent personal message to user %s.", target_id)
        return jsonify({"status": "success", "message": f"Message sent to User {target_id}!"})
    except Exception as exc:
        logger.error("mod_send_message error for user %s: %s", target_id_raw, exc)
        return jsonify({"status": "error", "message": f"Failed to deliver: {exc}"}), 500


# ============================================================
# 20. BOT COMMANDS
# ============================================================

@bot.message_handler(commands=["start"])
def start(message):
    """Handle /start command — register the user and show the main menu.

    If a referrer ID is included in the start payload, it is passed to
    get_or_create_user for referral processing.

    Args:
        message: Telegram Message object.
    """
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
    """Handle /invite command — send the direct referral link."""
    user_id = message.from_user.id
    get_or_create_user(user_id, message.from_user.first_name or "User")
    if not send_referral_link(user_id):
        bot.reply_to(message, "Unable to send invite right now. Please try again later.")


@bot.callback_query_handler(func=lambda call: call.data == "invite_friends")
def invite_friends_callback(call):
    """Handle Invite Friends button click."""
    user_id = call.from_user.id
    get_or_create_user(user_id, call.from_user.first_name or "User")
    if send_referral_link(user_id):
        bot.answer_callback_query(call.id, "Referral link sent!")
    else:
        bot.answer_callback_query(call.id, "Unable to send invite. Try again later.", show_alert=True)


@bot.message_handler(commands=["balance"])
def check_balance(message):
    """Handle /balance command — show the user's current coin balance.

    Args:
        message: Telegram Message object.
    """
    user = users_col.find_one({"user_id": message.from_user.id})
    if user:
        bot.reply_to(
            message,
            f"\U0001f4b0 Your balance: *{user.get('coins', 0)} \U0001fa99*",
            parse_mode="Markdown",
        )
    else:
        bot.reply_to(message, "Please use /start to register first!")


@bot.message_handler(commands=["redeem"])
def redeem_promo_command(message):
    """Handle /redeem CODE command — let a user redeem a promo code.

    Usage: /redeem <CODE>

    Args:
        message: Telegram Message object.
    """
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
    """Handle /createpromo command — admin only, create a new promo code.

    Usage: /createpromo <CODE> <COINS> [max_uses]
    - CODE     : Alphanumeric promo code (auto-uppercased)
    - COINS    : Number of coins to award on redemption
    - max_uses : (Optional) Maximum number of uses; 0 = unlimited

    Args:
        message: Telegram Message object.
    """
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
        return bot.reply_to(
            message,
            "\u274c Invalid code format. Use only letters, numbers, and underscores (2-30 chars).",
        )

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
    """Handle /deletepromo CODE command — admin only, deactivate a promo code.

    Deactivated codes cannot be redeemed. The document is kept in the
    database for audit purposes.

    Usage: /deletepromo <CODE>

    Args:
        message: Telegram Message object.
    """
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
    """Handle /listpromos command — admin only, list all active promo codes.

    Shows code, coins, usage count, and max uses for each active promo.

    Args:
        message: Telegram Message object.
    """
    if int(message.from_user.id) != ADMIN_ID:
        return

    try:
        promos = list(promos_col.find({"active": True}, {"_id": 0, "used_by": 0}))
        if not promos:
            return bot.reply_to(message, "\U0001f4cb No active promo codes found.")

        lines = ["\U0001f3ab *Active Promo Codes*\n"]
        for p in promos:
            uses_str = f"{p.get('max_uses', 0)}" if p.get("max_uses", 0) > 0 else "Unlimited"
            lines.append(
                f"• `{p['code']}` — {p['coins']} coins — Max: {uses_str}"
            )
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        logger.error("list_promos_command error: %s", exc)
        bot.reply_to(message, "\u26a0\ufe0f Server error. Please try again.")


@bot.message_handler(commands=["stats"])
def get_stats(message):
    """Handle /stats command — admin only, show bot statistics.

    Args:
        message: Telegram Message object.
    """
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
        f"\U0001f4b8 Pending Withdrawals: `{pending_w}`",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["approve"])
def approve_withdrawal(message):
    """Handle /approve command — admin only, approve a pending withdrawal.

    Usage: /approve <user_id>

    Args:
        message: Telegram Message object.
    """
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
    """Handle /reject command — admin only, reject a pending withdrawal and refund coins.

    Usage: /reject <user_id>

    Args:
        message: Telegram Message object.
    """
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
    """Handle /addcoins command — admin only, add coins to a user's balance.

    Usage: /addcoins <user_id> <amount>

    Args:
        message: Telegram Message object.
    """
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
    """Handle /broadcast command — admin only, send a message to all users.

    Usage: /broadcast <message>

    Args:
        message: Telegram Message object.
    """
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
    """Handle /msg USER_ID text — admin only, send a personal message to one user.

    Usage: /msg <user_id> <message text>

    Args:
        message: Telegram Message object.
    """
    if int(message.from_user.id) != ADMIN_ID:
        return

    parts = message.text.split(None, 2)
    if len(parts) < 3:
        return bot.reply_to(message, "Usage: /msg <user_id> <message text>\nExample: /msg 123456789 Hello there!")

    try:
        target_id = int(parts[1])
    except ValueError:
        return bot.reply_to(message, "\u274c Invalid User ID. Please enter a numeric ID.")

    text = parts[2].strip()
    if not text:
        return bot.reply_to(message, "\u274c Message text cannot be empty.")

    try:
        bot.send_message(
            target_id,
            f"\U0001f4e9 *Message from Admin:*\n\n{text}",
            parse_mode="Markdown",
        )
        bot.reply_to(message, f"\u2705 Message sent to User {target_id}!")
        logger.info("Admin sent personal message to user %s.", target_id)
    except Exception as exc:
        bot.reply_to(message, f"\u274c Failed to send message: {exc}")


@bot.message_handler(commands=["block"])
def block_user(message):
    """Handle /block command — admin only, block a user account.

    Usage: /block <user_id>

    Args:
        message: Telegram Message object.
    """
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
    """Handle /unblock command — admin only, unblock a user account.

    Usage: /unblock <user_id>

    Args:
        message: Telegram Message object.
    """
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
    """Handle /settask command — admin only, update a task verification code.

    Usage: /settask <task_id> <new_code>

    Args:
        message: Telegram Message object.
    """
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
    bot.reply_to(
        message,
        f"\u2705 Task `{task_id}` code updated to `{new_code}`!",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["penalty"])
def penalize_user(message):
    """Handle /penalty command — admin only, deduct coins from a user.

    Usage: /penalty <user_id> [amount]  (default: 200 coins)

    Args:
        message: Telegram Message object.
    """
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
    bot.reply_to(
        message,
        f"\u26a0\ufe0f Penalty applied!\nUser: `{target_id}`\nDeducted: `{deducted}`\nNew Balance: `{new_bal}`",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["listblocked"])
def list_blocked(message):
    """Handle /listblocked command — admin only, list all blocked users.

    Args:
        message: Telegram Message object.
    """
    if int(message.from_user.id) != ADMIN_ID:
        return
    blocked = list(
        users_col.find(
            {"blocked": True}, {"user_id": 1, "username": 1, "coins": 1, "_id": 0}
        ).limit(20)
    )
    if not blocked:
        return bot.reply_to(message, "\u2705 No blocked users found.")
    lines = [f"\U0001f6ab *Blocked Users ({len(blocked)})*\n"]
    for u in blocked:
        lines.append(f"• `{u['user_id']}` — {u.get('username', 'Unknown')} — {u.get('coins', 0)} \U0001fa99")
    lines.append("\nUse /unblock <user\\_id> to unblock.")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")


@bot.message_handler(commands=["userinfo"])
def user_info(message):
    """Handle /userinfo command — admin only, show detailed info about a user.

    Usage: /userinfo <user_id>

    Args:
        message: Telegram Message object.
    """
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
    if user.get("blocked"):
        status = "\U0001f6ab Blocked"
    elif user.get("ip_flagged") or user.get("fp_flagged"):
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
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["adminpanel"])
def admin_panel(message):
    """Handle /adminpanel command — admin only, show the admin control panel.

    Args:
        message: Telegram Message object.
    """
    if int(message.from_user.id) != ADMIN_ID:
        return
    total_u = users_col.count_documents({})
    pending_w = withdrawals_col.count_documents({"status": "Pending \u23f3"})
    blocked_u = users_col.count_documents({"blocked": True})
    today_j = users_col.count_documents({"joined": str(date.today())})

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Full Stats", callback_data="ap_stats"),
        types.InlineKeyboardButton("🚫 Blocked Users", callback_data="ap_blocked"),
        types.InlineKeyboardButton("💸 Pending Withdrawals", callback_data="ap_pending"),
        types.InlineKeyboardButton("📢 Broadcast", callback_data="ap_broadcast"),
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
        f"`/msg <id> <message>` — Message one user\n"
        f"`/userinfo <id>` — View user details\n"
        f"`/listblocked` — List all blocked users\n"
        f"`/settask <task\\_id> <code>` — Update task code\n"
        f"`/broadcast <msg>` — Send message to all\n"
        f"`/createpromo <CODE> <coins> [max]` — Create promo\n"
        f"`/deletepromo <CODE>` — Deactivate promo\n"
        f"`/listpromos` — List active promos\n"
        f"`/redeem <CODE>` — Redeem promo (users)",
        reply_markup=markup,
        parse_mode="Markdown",
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("ap_"))
def admin_panel_callback(call):
    """Handle inline button callbacks from the admin panel.

    Args:
        call: Telegram CallbackQuery object.
    """
    if int(call.from_user.id) != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Access denied.")

    if call.data == "ap_stats":
        total_u = users_col.count_documents({})
        pending_w = withdrawals_col.count_documents({"status": "Pending \u23f3"})
        approved = withdrawals_col.count_documents({"status": "Approved \u2705"})
        rejected = withdrawals_col.count_documents({"status": "Rejected \u274c"})
        today_j = users_col.count_documents({"joined": str(date.today())})
        blocked = users_col.count_documents({"blocked": True})
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
            parse_mode="Markdown",
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
# 20. ADMIN PANEL WEB ROUTES
# ============================================================

@app.route("/admin")
def admin_panel_page():
    """Serve the admin panel HTML page.

    Returns:
        HTML: admin.html file.
    """
    return send_file("admin.html")


@app.route("/admin/login", methods=["POST"])
def admin_login():
    """Validate the admin token for web panel login.

    Returns:
        JSON: {"status": "success"} or 401 error.
    """
    data = request.json or {}
    token = (data.get("token") or "").strip()
    if token == ADMIN_TOKEN and ADMIN_TOKEN != "":
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Invalid token"}), 401


@app.route("/admin/get_config", methods=["GET"])
def admin_get_config():
    """Return the current task codes config for the admin panel.

    Returns:
        JSON: Task codes dict or 401 error.
    """
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    return jsonify({"status": "success", "task_codes": get_live_task_codes()})


@app.route("/admin/update_codes", methods=["POST"])
def admin_update_codes():
    """Update task verification codes from the admin panel.

    Returns:
        JSON: Success or error message.
    """
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    codes = {k: str(v).strip().upper() for k, v in data.get("codes", {}).items() if v}
    if not codes:
        return jsonify({"status": "error", "message": "No codes provided"}), 400
    config_col.update_one({"_id": "task_codes"}, {"$set": {"codes": codes}}, upsert=True)
    logger.info("Admin updated task codes: %s", list(codes.keys()))
    return jsonify({"status": "success", "message": "Codes updated!"})


@app.route("/admin/withdrawals", methods=["GET"])
def admin_withdrawals():
    """Return all pending withdrawal requests for the admin panel.

    Returns:
        JSON: List of pending withdrawal documents.
    """
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    pending = list(withdrawals_col.find({"status": "Pending \u23f3"}, {"_id": 0}).sort("date", -1))
    return jsonify({"status": "success", "withdrawals": pending})


@app.route("/admin/update_withdrawal", methods=["POST"])
def admin_update_withdrawal():
    """Approve or reject a withdrawal from the admin panel.

    Returns:
        JSON: Success or error message.
    """
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    user_id = data.get("user_id")
    action = data.get("action")
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
            {"$set": {"status": "Approved \u2705"}},
        )
        try:
            bot.send_message(
                uid,
                f"\U0001f389 Your withdrawal of {wd.get('amount', 0)} coins has been approved!\n"
                f"\U0001f4b8 Payment will be sent to your UPI shortly.",
            )
        except Exception:
            pass
        logger.info("Admin approved withdrawal for user %s", uid)
    else:
        withdrawals_col.update_one(
            {"user_id": uid, "status": "Pending \u23f3"},
            {"$set": {"status": "Rejected \u274c"}},
        )
        users_col.update_one({"user_id": uid}, {"$inc": {"coins": wd.get("amount", 0)}})
        try:
            bot.send_message(
                uid,
                f"\u274c Your withdrawal of {wd.get('amount', 0)} coins was rejected.\n"
                f"\U0001fa99 Coins have been refunded to your account.",
            )
        except Exception:
            pass
        logger.info("Admin rejected withdrawal for user %s, refunded %s coins", uid, wd.get("amount", 0))
    return jsonify({"status": "success"})


@app.route("/admin/stats", methods=["GET"])
def admin_stats():
    """Return aggregated bot statistics for the admin panel (cached 60s).

    Returns:
        JSON: User counts, withdrawal counts, and total coins in circulation.
    """
    global _stats_cache, _stats_cache_time
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    now = time.time()
    if _stats_cache and now - _stats_cache_time < STATS_CACHE_TTL:
        return jsonify(_stats_cache)
    total_users = users_col.count_documents({})
    pending = withdrawals_col.count_documents({"status": "Pending \u23f3"})
    approved = withdrawals_col.count_documents({"status": "Approved \u2705"})
    coins_agg = list(users_col.aggregate([{"$group": {"_id": None, "total": {"$sum": "$coins"}}}]))
    total_coins = coins_agg[0]["total"] if coins_agg else 0
    _stats_cache = {
        "status": "success",
        "total_users": total_users,
        "pending": pending,
        "approved": approved,
        "total_coins": total_coins,
    }
    _stats_cache_time = now
    return jsonify(_stats_cache)


@app.route("/admin/search_user", methods=["GET"])
def admin_search_user():
    """Search for a user by ID from the admin panel.

    Returns:
        JSON: User document or error if not found.
    """
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        uid = int(request.args.get("user_id", 0))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    user = users_col.find_one({"user_id": uid}, {"_id": 0})
    if not user:
        return jsonify({"status": "error", "message": "User not found"}), 404
    return jsonify({"status": "success", "user": user})


@app.route("/admin/ban_user", methods=["POST"])
def admin_ban_user():
    """Ban a user from the admin panel.

    Returns:
        JSON: Success or error message.
    """
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
    """Unban a user from the admin panel.

    Returns:
        JSON: Success or error message.
    """
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


@app.route("/admin/sponsor_clicks", methods=["GET"])
def admin_sponsor_clicks():
    """Return click counts for all sponsor slots from the admin panel.

    Returns:
        JSON: Dict of slot_id -> {count, link_url}.
    """
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        docs = list(sponsor_clicks_col.find({}, {"_id": 1, "link_url": 1, "count": 1}))
        result = {d["_id"]: {"count": d.get("count", 0), "link_url": d.get("link_url", "")} for d in docs}
        return jsonify({"status": "success", "clicks": result})
    except Exception as exc:
        logger.error("admin_sponsor_clicks error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/admin/list_banned", methods=["GET"])
def admin_list_banned():
    """Return a list of all banned users from the admin panel.

    Returns:
        JSON: List of banned user documents (up to 50).
    """
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
    """Send a direct Telegram message to a specific user from the admin panel.

    Returns:
        JSON: Success or error with descriptive message.
    """
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
        return jsonify({"status": "error", "message": f"User {uid} is not registered in the bot."}), 404
    try:
        try:
            bot.send_message(uid, msg, parse_mode="Markdown")
        except Exception as markdown_exc:
            logger.warning("Markdown DM failed for %s, retrying as plain text: %s", uid, markdown_exc)
            bot.send_message(uid, msg)
        logger.info("Admin sent DM to %s", uid)
        return jsonify({"status": "success", "message": f"Message sent to user {uid}!"})
    except Exception as exc:
        logger.error("admin_send_dm error for %s: %s", uid, exc)
        err_str = str(exc)
        if "bot was blocked" in err_str:
            return jsonify({"status": "error", "message": "User has blocked the bot."}), 400
        if "user is deactivated" in err_str:
            return jsonify({"status": "error", "message": "User's account is deactivated."}), 400
        return jsonify({"status": "error", "message": "Failed to send message. Telegram error."}), 500


def _do_broadcast(msg: str) -> None:
    """Run a broadcast in a background thread with Telegram rate-limit delays.

    Sends the message to every user in the database at a rate of ~20/sec.
    Logs the final sent/failed counts.

    Args:
        msg: Markdown-formatted message string to broadcast.
    """
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
    """Start a broadcast to all users from the admin panel.

    Runs the broadcast in a daemon thread so the response returns immediately.

    Returns:
        JSON: Success with estimated user count, or error if message is empty.
    """
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    msg = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"status": "error", "message": "Empty message"}), 400
    total = users_col.count_documents({})
    Thread(target=_do_broadcast, args=(msg,), daemon=True).start()
    return jsonify({"status": "success", "message": f"Broadcast started for ~{total} users."})


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
        return bot.reply_to(message, r"Usage: /addcodepattern REGEX\nExample: /addcodepattern \b[A-Z]{4}\b")
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
            chat_id,
            user_id,
            matched_pattern,
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
    """Start Telegram bot polling with a MongoDB single-instance lock."""
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
                logger.error(
                    "Telegram polling conflict detected. Stop other deployments/processes using this BOT_TOKEN. Retrying in 60s: %s",
                    exc,
                )
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
# 22. UPTIME PING (for UptimeRobot / Render keep-alive)
# ============================================================

def uptime_ping() -> None:
    """Ping the Render URL every 10 minutes to prevent cold starts.

    Does nothing if RENDER_URL is not configured.
    """
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
    port = int(os.getenv("PORT", 5000))
    logger.info("Starting Flask on port %s...", port)
    app.run(host="0.0.0.0", port=port, debug=False)
