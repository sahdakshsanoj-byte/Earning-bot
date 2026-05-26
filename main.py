"""main.py — Daksh Grand Earn Bot
================================
Telegram earning bot with Flask API backend.
Runs on Render with MongoDB as the database.
Uses pyTelegramBotAPI (telebot) for Telegram integration.

Environment Variables Required:
    BOT_TOKEN        : Telegram Bot Token
    MONGO_URI        : MongoDB connection string
    ADMIN_ID         : Telegram user ID of the admin
    BOT_USERNAME     : Bot username (without @)
    RENDER_URL       : Render deploy URL (for uptime ping)
    FRONTEND_URL     : Frontend GitHub Pages URL
    ADMIN_TOKEN      : Secret token for Admin Panel API
    REFERRAL_ACTIVE  : "true" = referrals required for withdraw (default)
                       "false" = referral check bypassed
    LOTTERY_CHANNEL  : (optional) Channel username/ID for auto-draw announcements
                       e.g. "@MyChannel" or "-100xxxxxxxxx"
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

import psutil
import pymongo
import requests as req_lib
from flask import Flask, jsonify, request
from flask_cors import CORS
from telebot import types
import telebot


# ============================================================
# 1. LOGGING SETUP
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================
# 2. ENVIRONMENT VARIABLES
# ============================================================

BOT_TOKEN      = (os.getenv("BOT_TOKEN")      or "").strip()
MONGO_URI      = (os.getenv("MONGO_URI")       or "").strip()
ADMIN_ID_STR   = (os.getenv("ADMIN_ID")        or "").strip()
BOT_USERNAME   = (os.getenv("BOT_USERNAME")    or "YourBotUsername").strip()
RENDER_URL     = (os.getenv("RENDER_URL")      or "").strip()
FRONTEND_URL   = (os.getenv("FRONTEND_URL")    or "https://sahdakshsanoj-byte.github.io").strip()
ADMIN_TOKEN    = (os.getenv("ADMIN_TOKEN")     or "").strip()
MOD_TOKEN      = (os.getenv("MOD_TOKEN")       or "").strip()
WEBHOOK_SECRET = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32] if BOT_TOKEN else ""

# Referral Lock — Render dashboard mein set karo: REFERRAL_ACTIVE = false → bypass referral check
# Default: true (5 referrals required before withdrawal)
REFERRAL_ACTIVE = os.getenv("REFERRAL_ACTIVE", "true").strip().lower() == "true"

# Lottery Channel — optional: set to a channel username/ID for auto-draw announcements
LOTTERY_CHANNEL = (os.getenv("LOTTERY_CHANNEL") or "").strip()

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

    users_col                 = db["users"]
    withdrawals_col           = db["withdrawals"]
    rate_col                  = db["rate_limits"]
    config_col                = db["config"]
    sponsor_clicks_col        = db["sponsor_clicks"]
    promos_col                = db["promos"]
    support_messages_col      = db["support_messages"]
    ad_reward_tokens_col      = db["ad_reward_tokens"]
    code_filter_rules_col     = db["code_filter_rules"]
    group_code_violations_col = db["group_code_violations"]
    promo_tasks_col           = db["promo_tasks"]
    lottery_col               = db["lottery"]

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
        group_code_violations_col.create_index(
            [("chat_id", 1), ("user_id", 1)], unique=True, **_idx_opts
        )
        promo_tasks_col.create_index("task_id", unique=True, **_idx_opts)
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
    "yt1":   "CODE1",
    "yt2":   "CODE2",
    "yt3":   "CODE3",
    "web1":  "DASH98",
    "web2":  "GYM567",
    "web3":  "SHU234",
    "slot3": "SLOT3",
    "slot4": "SLOT4",
}

TASK_REWARDS = {
    "yt1":   5,
    "yt2":   5,
    "yt3":   5,
    "web1":  3,
    "web2":  3,
    "web3":  3,
    "slot3": 4,
    "slot4": 4,
}

ONE_TIME_TASK_IDS      = {"slot3", "slot4"}
MAX_YT_TASKS_PER_DAY  = 3
MAX_WEB_TASKS_PER_DAY = 3

CHANNEL_IDS                = ["official", "channel2", "channel3", "slot1", "slot2", "slot3", "slot4"]
CHANNEL_REWARD_PER_CHANNEL = 5          # default reward for official channels
CHANNEL_REWARDS            = {          # per-channel override (sponsor slots earn less)
    "slot1": 3,
    "slot2": 3,
}
CHANNEL_TOTAL_REWARD       = 15

MAX_ADS_PER_DAY            = 10
AD_COIN_REWARD             = 5
AD_CLAIM_TOKEN_TTL_SECONDS = 300
AD_MIN_PER_DAY             = 8

PROMO_TASK_REWARD = 5
ALL_TASKS_BONUS   = 10

MIN_WITHDRAW      = 5000
MAX_WITHDRAW      = 100000
WITHDRAW_COOLDOWN = 86400

DEVICE_RESET_COOLDOWN_DAYS = 3

# ============================================================
# 4b. SPIN WHEEL + COIN MINING DEFAULTS
# ============================================================

# --- Spin Wheel ---
SPIN_PER_DAY     = 5       # Max spins per day
SPIN_AD_REQUIRED = True    # Har spin ke liye 1 ad zaroori hai
SPIN_TOKEN_TTL   = 300     # Ad token validity: 5 min

# Rewards list aur unka weight (probability)
# 0 = Miss (koi coin nahi)
SPIN_REWARDS = [0,  5,  10, 15, 20,  30,  50,  100]
SPIN_WEIGHTS = [15, 30, 25, 15, 8,   4,   2,   1  ]

# --- Coin Mining ---
MINING_ADS_REQUIRED   = 2  # Mining shuru karne ke liye 2 ads dekhne honge
MINING_COOLDOWN_SECS  = 60  # 1 minute baad hi dubara mine kar sakte ho
MINING_DURATION_HOURS = 1  # 1 ghante ki mining session
MINING_REWARD         = 10 # 1 session = 10 coins
MINING_TOKEN_TTL      = 300

_feature_cfg_cache      = None
_feature_cfg_cache_time = 0.0
FEATURE_CFG_CACHE_TTL   = 30


def get_feature_config() -> dict:
    """
    DB se spin_active + mining_active fetch karo.
    Default: dono active.
    Admin /togglespin aur /togglemining se control kar sakta hai.
    """
    global _feature_cfg_cache, _feature_cfg_cache_time
    now = time.time()
    if _feature_cfg_cache is not None and now - _feature_cfg_cache_time < FEATURE_CFG_CACHE_TTL:
        return _feature_cfg_cache
    try:
        doc = config_col.find_one({"_id": "feature_config"}) or {}
        merged = {
            "spin_active":   bool(doc.get("spin_active",   True)),
            "mining_active": bool(doc.get("mining_active", True)),
        }
    except Exception:
        merged = {"spin_active": True, "mining_active": True}
    _feature_cfg_cache      = merged
    _feature_cfg_cache_time = now
    return merged


def _bust_feature_cache() -> None:
    global _feature_cfg_cache, _feature_cfg_cache_time
    _feature_cfg_cache      = None
    _feature_cfg_cache_time = 0.0


# ============================================================
# 4c. LOTTERY DEFAULTS
# ============================================================

LOTTERY_DEFAULTS = {
    "ticket_price": 50,
    "prize":        500,
    "active":       True,
}
_lottery_cfg_cache      = None
_lottery_cfg_cache_time = 0.0
LOTTERY_CFG_CACHE_TTL   = 60

SUPPORT_MAX_MSGS                  = 1
SUPPORT_WINDOW_HRS                = 24
TASK_FAIL_COOLDOWN                = 60
TASK_MAX_FAILS                    = 3
VALID_TASK_IDS                    = set(TASK_CODES.keys())
GROUP_CODE_FILTER_ENABLED         = True
GROUP_CODE_MAX_VIOLATIONS         = 3
GROUP_CODE_VIOLATION_WINDOW_HOURS = 24
DEFAULT_GROUP_CODE_PATTERNS       = [
    r"\b[A-Z]{3}\b",
    r"\b\d{3}\b",
]


# ============================================================
# 5. IN-MEMORY RATE LIMIT CACHE
# ============================================================

_rate_cache: dict = {}
_rate_cache_lock  = threading.Lock()


def is_rate_limited(key: str, cooldown_seconds: int) -> bool:
    now = time.time()
    with _rate_cache_lock:
        expires = _rate_cache.get(key, 0.0)
        if expires > now:
            return True
        _rate_cache[key] = now + cooldown_seconds
        return False


def _cleanup_rate_cache() -> None:
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
# 5b. SECURITY — IP RATE LIMIT + SUSPICIOUS ACTIVITY + ADMIN LOCKOUT
# ============================================================

# --- Constants ---
IP_RATE_LIMIT_REQUESTS  = 50    # Max requests per IP per minute
IP_RATE_LIMIT_WINDOW    = 60    # Window in seconds
IP_BAN_DURATION         = 900   # 15 min ban after exceeding limit

ADMIN_MAX_FAIL_ATTEMPTS = 5     # Wrong admin tokens allowed
ADMIN_LOCKOUT_SECONDS   = 900   # 15 min lockout after 5 fails

SUSPICIOUS_THRESHOLD    = 20    # Requests per minute to trigger alert

# --- In-memory trackers ---
_ip_request_log:      dict = {}   # { ip: [(timestamp, endpoint), ...] }
_ip_banned:           dict = {}   # { ip: ban_expires_timestamp }
_failed_admin_logins: dict = {}   # { ip: {"count": int, "expires": float} }
_suspicious_alerted:  dict = {}   # { ip: last_alert_timestamp }
_security_lock = threading.Lock()


def _get_client_ip() -> str:
    """Request context se real IP nikalo.
    X-Forwarded-For ke last IP ko use karo — Render proxy real IP append karta hai end mein.
    Pehla IP attacker set kar sakta hai (spoofing), isliye last trusted hai.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        ips = [ip.strip() for ip in forwarded.split(",") if ip.strip()]
        if ips:
            return ips[-1]
    return request.remote_addr or ""


def _is_ip_banned(ip: str) -> bool:
    """Check karo agar IP temporarily banned hai."""
    now = time.time()
    with _security_lock:
        expires = _ip_banned.get(ip, 0.0)
        return expires > now


def _ban_ip(ip: str, duration: int = IP_BAN_DURATION) -> None:
    """IP ko temporarily ban karo."""
    with _security_lock:
        _ip_banned[ip] = time.time() + duration
    logger.warning("SECURITY: IP %s banned for %ds due to rate limit breach.", ip, duration)


def _record_ip_request(ip: str, endpoint: str) -> int:
    """
    IP ka request count track karo last 60 seconds mein.
    Returns: current count in window.
    """
    now = time.time()
    cutoff = now - IP_RATE_LIMIT_WINDOW
    with _security_lock:
        history = _ip_request_log.get(ip, [])
        history = [(ts, ep) for ts, ep in history if ts > cutoff]
        history.append((now, endpoint))
        _ip_request_log[ip] = history
        return len(history)


def _alert_admin_suspicious(ip: str, count: int, endpoint: str) -> None:
    """Admin ko Telegram pe suspicious activity alert bhejo (max 1 alert per 5 min per IP)."""
    now = time.time()
    with _security_lock:
        last = _suspicious_alerted.get(ip, 0.0)
        if now - last < 300:
            return
        _suspicious_alerted[ip] = now

    def _send():
        try:
            bot.send_message(
                ADMIN_ID,
                f"\u26a0\ufe0f *Suspicious Activity Detected!*\n\n"
                f"\U0001f310 IP: `{ip}`\n"
                f"\U0001f4ca Requests: `{count}` in last 60 seconds\n"
                f"\U0001f517 Endpoint: `{endpoint}`\n\n"
                f"_IP has been temporarily blocked for {IP_BAN_DURATION // 60} minutes._",
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.warning("Suspicious alert send failed: %s", exc)

    threading.Thread(target=_send, daemon=True, name=f"sec-alert-{ip}").start()


def check_ip_security(endpoint: str) -> tuple[bool, str]:
    """
    Har request ke liye call karo.
    Returns: (allowed: bool, reason: str)
    """
    ip = _get_client_ip()
    if not ip:
        return True, ""

    if _is_ip_banned(ip):
        logger.warning("SECURITY: Blocked request from banned IP %s to %s", ip, endpoint)
        return False, "Too many requests. You have been temporarily blocked."

    count = _record_ip_request(ip, endpoint)

    if count > IP_RATE_LIMIT_REQUESTS:
        _ban_ip(ip)
        _alert_admin_suspicious(ip, count, endpoint)
        return False, "Too many requests. You have been temporarily blocked."

    if count > SUSPICIOUS_THRESHOLD:
        _alert_admin_suspicious(ip, count, endpoint)

    return True, ""


def check_admin_login_attempt(ip: str, success: bool) -> tuple[bool, int]:
    """
    Admin login attempts track karo.
    Returns: (allowed: bool, remaining_attempts: int)
    """
    now = time.time()
    with _security_lock:
        entry = _failed_admin_logins.get(ip, {"count": 0, "expires": 0.0})

        if entry["expires"] > now:
            remaining_secs = int(entry["expires"] - now)
            return False, remaining_secs

        if success:
            _failed_admin_logins.pop(ip, None)
            return True, 0

        count = entry["count"] + 1
        if count >= ADMIN_MAX_FAIL_ATTEMPTS:
            _failed_admin_logins[ip] = {"count": count, "expires": now + ADMIN_LOCKOUT_SECONDS}
            logger.warning("SECURITY: Admin login locked for IP %s after %d failed attempts.", ip, count)
        else:
            _failed_admin_logins[ip] = {"count": count, "expires": 0.0}

        return True, ADMIN_MAX_FAIL_ATTEMPTS - count


def _cleanup_security_caches() -> None:
    """Purane IP ban/tracking data clean karo — memory leak se bachao."""
    while True:
        time.sleep(600)
        now = time.time()
        cutoff = now - IP_RATE_LIMIT_WINDOW
        with _security_lock:
            for ip in list(_ip_request_log.keys()):
                _ip_request_log[ip] = [(ts, ep) for ts, ep in _ip_request_log[ip] if ts > cutoff]
                if not _ip_request_log[ip]:
                    del _ip_request_log[ip]
            for ip in list(_ip_banned.keys()):
                if _ip_banned[ip] <= now:
                    del _ip_banned[ip]
            for ip in list(_failed_admin_logins.keys()):
                if _failed_admin_logins[ip]["expires"] <= now and _failed_admin_logins[ip]["count"] == 0:
                    del _failed_admin_logins[ip]
            for ip in list(_suspicious_alerted.keys()):
                if _suspicious_alerted[ip] < now - 600:
                    del _suspicious_alerted[ip]
        logger.debug("Security cache cleanup done.")


# ============================================================
# 6. LEADERBOARD CACHE
# ============================================================

_leaderboard_cache      = "none"
_leaderboard_cache_time = 0.0
LEADERBOARD_TTL         = 600


def get_leaderboard_cached() -> str:
    global _leaderboard_cache, _leaderboard_cache_time
    now = time.time()
    if now - _leaderboard_cache_time < LEADERBOARD_TTL:
        return _leaderboard_cache
    _leaderboard_cache      = get_leaderboard()
    _leaderboard_cache_time = now
    return _leaderboard_cache


def refresh_leaderboard_loop() -> None:
    global _leaderboard_cache, _leaderboard_cache_time
    while True:
        time.sleep(LEADERBOARD_TTL)
        try:
            _leaderboard_cache      = get_leaderboard()
            _leaderboard_cache_time = time.time()
            logger.info("Leaderboard cache refreshed.")
        except Exception as exc:
            logger.error("Leaderboard refresh error: %s", exc)


# ============================================================
# 6b. TASK CODES CACHE
# ============================================================

_task_codes_cache      = None
_task_codes_cache_time = 0.0
TASK_CODES_CACHE_TTL   = 300


def get_live_task_codes() -> dict:
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
        params        = dict(parse_qsl(init_data, strict_parsing=True))
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
# 9. RATE LIMITING (MongoDB-backed)
# ============================================================

def check_support_limit(user_id: int) -> tuple[bool, str]:
    now  = datetime.utcnow()
    user = users_col.find_one(
        {"user_id": user_id},
        {"support_window_start": 1, "support_count": 1},
    )
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
            {"$set": {"support_window_start": now.isoformat(), "support_count": 0}},
        )
        return True, ""

    if count >= SUPPORT_MAX_MSGS:
        start_dt  = datetime.fromisoformat(window_start_str)
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
    now       = datetime.utcnow()
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
    block_key   = f"task_block_{user_id}_{task_id}"
    now         = datetime.utcnow()
    try:
        doc           = rate_col.find_one({"_id": counter_key})
        current_count = doc.get("count", 0) if doc else 0
        new_count     = current_count + 1
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
# 12. INPUT SANITIZATION & GROUP HELPERS
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
        can_ban    = bool(getattr(member, "can_restrict_members", False))
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
    now          = datetime.utcnow()
    window_start = now - timedelta(hours=GROUP_CODE_VIOLATION_WINDOW_HOURS)
    try:
        current = group_code_violations_col.find_one({"chat_id": chat_id, "user_id": user_id})
        if current:
            last_violation_raw = current.get("last_violation_at", "")
            try:
                last_violation_at = datetime.fromisoformat(last_violation_raw)
            except Exception:
                last_violation_at = now
            count = 1 if last_violation_at < window_start else int(current.get("count", 0)) + 1
        else:
            count = 1
        group_code_violations_col.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {"$set": {"count": count, "last_violation_at": now.isoformat()}},
            upsert=True,
        )
        return count
    except Exception as exc:
        logger.error(
            "Unable to record group code violation for %s in %s: %s", user_id, chat_id, exc
        )
        return 1


# ============================================================
# 13. FLASK + BOT SETUP
# ============================================================

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
CORS(app, origins=[FRONTEND_URL], supports_credentials=False)


# ============================================================
# 14. SECURITY HEADERS + IP FIREWALL
# ============================================================

@app.before_request
def ip_firewall():
    """Har request se pehle IP check karo — banned IPs block, suspicious IPs alert."""
    # Static assets ya health endpoint skip karo
    if request.path in ("/", "/favicon.ico"):
        return None

    ip = _get_client_ip()
    if not ip:
        return None

    allowed, reason = check_ip_security(request.path)
    if not allowed:
        logger.warning("FIREWALL: Blocked IP %s → %s", ip, request.path)
        return jsonify({"status": "error", "message": reason}), 429

    return None


@app.after_request
def add_security_headers(response):
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"]       = "1; mode=block"
    response.headers["Referrer-Policy"]        = "no-referrer"
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
    share_text    = "\U0001f4b0 Earn coins daily by watching ads & completing tasks! \U0001f680 Join now and start earning instantly!"
    share_url     = (
        "https://t.me/share/url"
        f"?url={quote(referral_link, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("\U0001f4e4 Share Now", url=share_url))
    try:
        bot.send_message(
            user_id,
            "\U0001f465 Invite your friends and earn 30 coins for each referral!\n\n"
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
                "user_id":                user_id,
                "username":               username,
                "coins":                  0,
                "referred_by":            None,
                "referral_count":         0,
                "task_completions":       {},
                "channel_claims":         {},
                "promo_task_completions": [],
                "last_claim_ts":          "",
                "allcomplete_bonus_date": "",
                "ads_today":              0,
                "ads_date":               "",
                "support_count":          0,
                "support_window_start":   "",
                "ip_flagged":             False,
                "fp_flagged":             False,
                "blocked":                False,
                "joined":                 str(date.today()),
            }
            if referrer_id and str(referrer_id) != str(user_id):
                referrer = users_col.find_one({"user_id": int(referrer_id)})
                if referrer:
                    users_col.update_one(
                        {"user_id": int(referrer_id)},
                        {"$inc": {"coins": 30, "referral_count": 1}},
                    )
                    new_user["referred_by"] = str(referrer_id)
                    try:
                        bot.send_message(
                            int(referrer_id),
                            "\U0001f38a *Referral Bonus!*\n\nYou earned 30 coins for inviting a friend!",
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


def count_task_type_completions_today(
    task_completions: dict, prefix: str, today: str, live_codes: dict
) -> int:
    count = 0
    for tid, info in task_completions.items():
        if tid.startswith(prefix) and isinstance(info, dict):
            if info.get("date") == today and info.get("code") == live_codes.get(tid, ""):
                count += 1
    return count


# ============================================================
# 16. STATS CACHE
# ============================================================

_stats_cache: dict       = {}
_stats_cache_time: float = 0.0
STATS_CACHE_TTL          = 60


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

        today    = str(date.today())
        ads_date  = user.get("ads_date", "")
        ads_today = user.get("ads_today", 0) if ads_date == today else 0

        task_completions = user.get("task_completions", {})
        live_task_codes  = get_live_task_codes()
        completed_today  = []
        verify_completions = {}
        for tid, info in task_completions.items():
            if isinstance(info, dict):
                code_matches = info.get("code") == live_task_codes.get(tid, "")
                if tid in ONE_TIME_TASK_IDS:
                    verify_completions[tid] = {
                        "code": info.get("code", ""),
                        "link": info.get("link", ""),
                    }
                    if code_matches:
                        completed_today.append(tid)
                else:
                    if info.get("date") == today and code_matches:
                        completed_today.append(tid)

        promo_task_completions = user.get("promo_task_completions", [])

        return jsonify({
            "status":                 "success",
            "coins":                  user.get("coins", 0),
            "leaderboard":            get_leaderboard_cached(),
            "referrals":              get_referral_list(user_id),
            "completed_tasks":        completed_today,
            "verify_completions":     verify_completions,
            "last_claim":             user.get("last_claim_ts", ""),
            "referred_by":            user.get("referred_by", ""),
            "ads_today":              ads_today,
            "ads_date":               ads_date,
            "channel_claims":         user.get("channel_claims", {}),
            "promo_task_completions": promo_task_completions,
            "allcomplete_bonus_date": user.get("allcomplete_bonus_date", ""),
            "pending_winner_popup":   user.get("pending_winner_popup", False),
            "pending_winner_prize":   user.get("pending_winner_prize", 0),
        })
    except Exception as exc:
        logger.error("get_user error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


@app.route("/ack_winner_popup/<int:user_id>", methods=["POST"])
def ack_winner_popup(user_id: int):
    """Frontend calls this once the winner popup is shown — clears the flag."""
    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"pending_winner_popup": False, "pending_winner_prize": 0}},
    )
    return jsonify({"status": "ok"})


@app.route("/get_leaderboard")
def get_leaderboard_api():
    return jsonify({"status": "success", "leaderboard": get_leaderboard_cached()})


# ── DAILY CLAIM AD TOKEN ──────────────────────────────────────────────────
# Frontend calls this FIRST (before showing the ad).
# Returns a short-lived token. claim_daily_api consumes it.
# Token TTL = 10 minutes. Only one active token per user at a time.

DAILY_CLAIM_TOKEN_TTL = 600  # 10 minutes


@app.route("/daily_claim_token/<int:user_id>", methods=["POST"])
def daily_claim_token_api(user_id: int):
    if user_id <= 0:
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    if is_rate_limited(f"dct_{user_id}", 15):
        return jsonify({"status": "error", "message": "Please wait a moment before trying again."}), 429

    try:
        user = users_col.find_one({"user_id": user_id}, {"blocked": 1, "last_claim_ts": 1})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        now   = datetime.utcnow()
        last_ts = user.get("last_claim_ts", "")
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                # 24-hour strict window — date comparison nahi, exact time check
                elapsed = (now - last_dt).total_seconds()
                if elapsed < 86400:
                    next_claim_dt = last_dt + timedelta(hours=24)
                    remaining     = next_claim_dt - now
                    total_secs    = max(0, int(remaining.total_seconds()))
                    h = total_secs // 3600
                    m = (total_secs % 3600) // 60
                    s = total_secs % 60
                    return jsonify({
                        "status":  "error",
                        "message": f"Already claimed! Come back in {h}h {m}m {s}s.",
                        "data":    {
                            "remaining_seconds": total_secs,
                            "next_claim_utc":    next_claim_dt.isoformat(),
                        },
                    }), 400
            except ValueError:
                pass

        # Remove any stale tokens for this user first
        ad_reward_tokens_col.delete_many({"user_id": user_id, "source": "daily_claim"})

        raw_token  = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = now + timedelta(seconds=DAILY_CLAIM_TOKEN_TTL)

        ad_reward_tokens_col.insert_one({
            "_id":        token_hash,
            "user_id":    user_id,
            "source":     "daily_claim",
            "created_at": now,
            "expires_at": expires_at,
        })

        logger.debug("Daily claim token issued for user %s", user_id)
        return jsonify({
            "status":     "success",
            "token":      raw_token,
            "expires_in": DAILY_CLAIM_TOKEN_TTL,
        }), 200

    except Exception as exc:
        logger.error("daily_claim_token error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


# ── CLAIM DAILY BONUS ─────────────────────────────────────────────────────

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

        data            = request.get_json(silent=True) or {}
        claim_token_raw = data.get("token", "").strip()

        if claim_token_raw:
            token_hash = hashlib.sha256(claim_token_raw.encode()).hexdigest()
            # Pehle find karo, expiry check karo, PHIR delete karo
            token_doc = ad_reward_tokens_col.find_one({
                "_id":     token_hash,
                "user_id": user_id,
                "source":  "daily_claim",
            })
            if not token_doc:
                return jsonify({
                    "status":  "error",
                    "message": "Invalid or already used token. Please watch the ad again.",
                }), 403
            if token_doc.get("expires_at") and token_doc["expires_at"] < datetime.utcnow():
                ad_reward_tokens_col.delete_one({"_id": token_hash})
                return jsonify({
                    "status":  "error",
                    "message": "Ad session expired. Please try again.",
                }), 403
            # Valid token — delete karo (consume)
            ad_reward_tokens_col.delete_one({"_id": token_hash})

        now     = datetime.utcnow()
        last_ts = user.get("last_claim_ts", "")
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                # 24-hour strict window — date nahi, exact time check
                elapsed = (now - last_dt).total_seconds()
                if elapsed < 86400:
                    next_claim_dt = last_dt + timedelta(hours=24)
                    remaining     = next_claim_dt - now
                    total_secs    = max(0, int(remaining.total_seconds()))
                    h = total_secs // 3600
                    m = (total_secs % 3600) // 60
                    s = total_secs % 60
                    return jsonify({
                        "status":  "error",
                        "message": f"Already claimed! Come back in {h}h {m}m {s}s.",
                        "data":    {
                            "remaining_seconds": total_secs,
                            "next_claim_utc":    next_claim_dt.isoformat(),
                        },
                    }), 400
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
    if is_rate_limited(f"allbonus_{user_id}", 30):
        return jsonify({"status": "error", "message": "Please wait before trying again."}), 429

    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        today = str(date.today())

        if user.get("allcomplete_bonus_date") == today:
            return jsonify({
                "status":  "error",
                "message": "All-tasks bonus already claimed today! Come back tomorrow.",
            }), 400

        last_ts    = user.get("last_claim_ts", "")
        daily_done = False
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                if last_dt.date().isoformat() == today:
                    daily_done = True
            except ValueError:
                pass
        if not daily_done:
            return jsonify({"status": "error", "message": "Claim your daily bonus first!"}), 400

        ads_date  = user.get("ads_date", "")
        ads_today = user.get("ads_today", 0) if ads_date == today else 0
        if ads_today < MAX_ADS_PER_DAY:
            return jsonify({
                "status":  "error",
                "message": f"Watch {MAX_ADS_PER_DAY - ads_today} more ad(s) to complete all tasks!",
            }), 400

        task_completions = user.get("task_completions", {})
        live_codes       = get_live_task_codes()
        yt_done  = count_task_type_completions_today(task_completions, "yt",  today, live_codes)
        web_done = count_task_type_completions_today(task_completions, "web", today, live_codes)

        if yt_done < MAX_YT_TASKS_PER_DAY:
            return jsonify({
                "status":  "error",
                "message": f"Complete {MAX_YT_TASKS_PER_DAY - yt_done} more YouTube task(s) first!",
            }), 400
        if web_done < MAX_WEB_TASKS_PER_DAY:
            return jsonify({
                "status":  "error",
                "message": f"Complete {MAX_WEB_TASKS_PER_DAY - web_done} more website task(s) first!",
            }), 400

        users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"coins": ALL_TASKS_BONUS}, "$set": {"allcomplete_bonus_date": today}},
        )
        logger.info("All-tasks bonus of %s coins credited to user %s", ALL_TASKS_BONUS, user_id)
        return jsonify({
            "status":  "success",
            "message": f"\U0001f389 All tasks complete! Bonus {ALL_TASKS_BONUS} coins credited!",
            "data":    {"bonus": ALL_TASKS_BONUS},
        })
    except Exception as exc:
        logger.error("claim_allcomplete_bonus error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


# ============================================================
# WITHDRAW — REFERRAL_ACTIVE env var se control hota hai
# ============================================================

@app.route("/withdraw", methods=["POST"])
def withdraw_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    user_id_raw      = data.get("user_id")
    requested_amount = data.get("amount")

    method_raw      = sanitize_text(data.get("method", "upi")).lower().strip()
    payment_address = sanitize_text(
        data.get("payment_address", "") or data.get("upi_id", ""), max_length=256
    )

    METHOD_ALIASES = {
        "upi":           "upi",
        "usdt":          "usdt_trc20",
        "usdt_trc20":    "usdt_trc20",
        "google":        "google_redeem",
        "google_redeem": "google_redeem",
    }
    method = METHOD_ALIASES.get(method_raw)
    if not method:
        return jsonify({"status": "error", "message": "Invalid withdrawal method."}), 400

    if not user_id_raw or requested_amount is None:
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

    if method == "upi":
        upi_pattern = re.compile(r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$")
        if not payment_address or not upi_pattern.match(payment_address):
            return jsonify({
                "status":  "error",
                "message": "Invalid UPI ID format. (Example: name@upi)",
            }), 400
    elif method == "usdt_trc20":
        trc20_pattern = re.compile(r"^T[A-Za-z0-9]{33}$")
        if not payment_address or not trc20_pattern.match(payment_address):
            return jsonify({
                "status":  "error",
                "message": "Invalid TRC20 address. Must start with T and be 34 characters.",
            }), 400
    elif method == "google_redeem":
        payment_address = "via_telegram"

    # MongoDB-backed withdrawal cooldown — server restart se reset nahi hoga
    _last_wd = withdrawals_col.find_one(
        {"user_id": user_id},
        sort=[("timestamp", -1)],
        projection={"timestamp": 1},
    )
    if _last_wd and _last_wd.get("timestamp"):
        _wd_elapsed = (datetime.utcnow() - _last_wd["timestamp"]).total_seconds()
        if _wd_elapsed < WITHDRAW_COOLDOWN:
            _wd_remaining_h = int((WITHDRAW_COOLDOWN - _wd_elapsed) // 3600)
            _wd_remaining_m = int(((WITHDRAW_COOLDOWN - _wd_elapsed) % 3600) // 60)
            return jsonify({
                "status":  "error",
                "message": f"One withdrawal per day allowed. Try again in {_wd_remaining_h}h {_wd_remaining_m}m.",
            }), 429

    if REFERRAL_ACTIVE:
        _ref_user = users_col.find_one({"user_id": user_id}, {"referral_count": 1, "_id": 0})
        ref_count = _ref_user.get("referral_count", 0) if _ref_user else 0
        if ref_count < 5:
            return jsonify({
                "status":  "error",
                "message": f"You need {5 - ref_count} more referrals to withdraw.",
            }), 400

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
        return jsonify({
            "status":  "error",
            "message": f"Insufficient balance. You have {user.get('coins', 0)} coins.",
        }), 400

    inr_value = requested_amount * 0.02

    METHOD_LABELS = {
        "upi":           "\U0001f3e6 UPI",
        "usdt_trc20":    "\U0001f48e USDT TRC20",
        "google_redeem": "\U0001f381 Google Play",
    }
    method_label = METHOD_LABELS.get(method, method)

    now_utc    = datetime.utcnow()
    IST_OFFSET = timedelta(hours=5, minutes=30)
    now_ist    = now_utc + IST_OFFSET
    withdrawal = {
        "user_id":         user_id,
        "method":          method,
        "payment_address": payment_address,
        "upi_id":          payment_address,
        "amount":          requested_amount,
        "inr_value":       inr_value,
        "status":          "Pending \u23f3",
        "timestamp":       now_utc,
        "date":            now_ist.strftime("%d %b %Y, %I:%M %p IST"),
    }
    withdrawals_col.insert_one(withdrawal)

    addr_display  = payment_address if payment_address != "via_telegram" else "Telegram DM"
    tg_username   = result.get("username") or ""
    username_line = ""
    if method == "google_redeem":
        username_line = f"Username: @{tg_username}\n" if tg_username else "Username: _(not set)_\n"

    try:
        bot.send_message(
            ADMIN_ID,
            f"\U0001f4b8 *New Withdrawal Request*\n\n"
            f"User ID: `{user_id}`\n"
            f"{username_line}"
            f"Method: {method_label}\n"
            f"Address: `{addr_display}`\n"
            f"Requested: `{requested_amount}` coins (\u20b9{inr_value:.2f})\n"
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
            withdrawals_col.find({"user_id": user_id}, {"_id": 0})
            .sort("timestamp", -1)
            .limit(10)
        )
        return jsonify({"status": "success", "data": {"history": history}})
    except Exception as exc:
        logger.error("get_history error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/verify_task", methods=["POST"])
def verify_task_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    task_id      = sanitize_text(data.get("task_id", "")).lower()
    user_code    = sanitize_text(data.get("code", "")).upper()
    sponsor_link = sanitize_text(data.get("link", ""), max_length=512)

    if not task_id or not user_code:
        return jsonify({"status": "error", "message": "Missing task ID or code."}), 400
    if task_id not in VALID_TASK_IDS:
        return jsonify({"status": "error", "message": "Invalid task ID."}), 400

    _u = users_col.find_one({"user_id": user_id}, {"blocked": 1})
    if _u and _u.get("blocked"):
        return jsonify({"status": "error", "message": "Your account has been suspended."}), 403

    if is_task_attempt_blocked(user_id, task_id):
        return jsonify({
            "status":  "error",
            "message": f"Too many wrong attempts. Wait {TASK_FAIL_COOLDOWN // 60} minute(s) before retrying.",
        }), 429

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

        today            = str(date.today())
        task_completions = user.get("task_completions", {})
        live_codes       = get_live_task_codes()
        existing         = task_completions.get(task_id, {})

        if task_id in ONE_TIME_TASK_IDS:
            if isinstance(existing, dict) and existing.get("code") == correct_code:
                return jsonify({"status": "error", "message": "You have already completed this task!"}), 400
        else:
            if (
                isinstance(existing, dict)
                and existing.get("date") == today
                and existing.get("code") == correct_code
            ):
                return jsonify({
                    "status":  "error",
                    "message": "Task already completed today! Come back tomorrow.",
                }), 400

        if task_id.startswith("yt"):
            done_today = count_task_type_completions_today(task_completions, "yt", today, live_codes)
            if done_today >= MAX_YT_TASKS_PER_DAY:
                return jsonify({
                    "status":  "error",
                    "message": f"Daily YouTube task limit reached ({MAX_YT_TASKS_PER_DAY}/{MAX_YT_TASKS_PER_DAY}). Come back tomorrow!",
                }), 400
        elif task_id.startswith("web"):
            done_today = count_task_type_completions_today(task_completions, "web", today, live_codes)
            if done_today >= MAX_WEB_TASKS_PER_DAY:
                return jsonify({
                    "status":  "error",
                    "message": f"Daily website task limit reached ({MAX_WEB_TASKS_PER_DAY}/{MAX_WEB_TASKS_PER_DAY}). Come back tomorrow!",
                }), 400

        completion_field = f"task_completions.{task_id}"
        if task_id in ONE_TIME_TASK_IDS:
            guard_filter = {
                "user_id": user_id, "blocked": {"$ne": True},
                "$or": [
                    {completion_field: {"$exists": False}},
                    {f"{completion_field}.code": {"$ne": correct_code}},
                ],
            }
        else:
            guard_filter = {
                "user_id": user_id, "blocked": {"$ne": True},
                "$or": [
                    {completion_field: {"$exists": False}},
                    {f"{completion_field}.date": {"$ne": today}},
                    {f"{completion_field}.code": {"$ne": correct_code}},
                ],
            }

        completion_record = {"date": today, "code": correct_code}
        if sponsor_link:
            completion_record["link"] = sponsor_link

        upd = users_col.update_one(
            guard_filter,
            {"$inc": {"coins": reward}, "$set": {completion_field: completion_record}},
        )
        if upd.modified_count == 0:
            return jsonify({"status": "error", "message": "Task already completed."}), 400

        clear_task_fail_counter(user_id, task_id)
        return jsonify({
            "status":  "success",
            "message": f"{reward} coins added to your balance!",
            "data":    {"reward": reward},
        })
    except Exception as exc:
        logger.error("verify_task error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ============================================================
# AD REWARD SYSTEM
# ============================================================

def create_ad_claim_token(user_id: int) -> tuple[dict, int]:
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
                    return {"status": "error", "message": "Please wait 30 seconds before watching the next ad."}, 429
            except ValueError:
                pass

        today     = str(date.today())
        ads_date  = user.get("ads_date", "")
        ads_today = user.get("ads_today", 0) if ads_date == today else 0

        if ads_today >= MAX_ADS_PER_DAY:
            return {
                "status":  "error",
                "message": f"Daily ad limit reached ({MAX_ADS_PER_DAY}/{MAX_ADS_PER_DAY}). Come back tomorrow!",
                "data":    {"ads_done": ads_today, "ads_total": MAX_ADS_PER_DAY, "remaining": 0},
            }, 400

        raw_token  = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.utcnow() + timedelta(seconds=AD_CLAIM_TOKEN_TTL_SECONDS)
        ad_reward_tokens_col.insert_one({
            "_id":        token_hash,
            "user_id":    user_id,
            "created_at": datetime.utcnow(),
            "expires_at": expires_at,
            "source":     "sdk_ad",
        })
        return {"status": "success", "token": raw_token, "expires_in": AD_CLAIM_TOKEN_TTL_SECONDS}, 200
    except Exception as exc:
        logger.error("create_ad_claim_token error for %s: %s", user_id, exc)
        return {"status": "error", "message": "Server error."}, 500


def manual_ad_reward(user_id: int, claim_token: str) -> tuple[dict, int]:
    if user_id <= 0:
        return {"status": "error", "message": "Invalid user ID."}, 400
    if not claim_token:
        return {"status": "error", "message": "Ad verification token missing."}, 400
    try:
        token_hash = hashlib.sha256(claim_token.encode()).hexdigest()
        token_doc  = ad_reward_tokens_col.find_one_and_delete({"_id": token_hash, "user_id": user_id})
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
                    return {"status": "error", "message": "Please wait 30 seconds before claiming the next ad reward."}, 429
            except ValueError:
                pass

        today     = str(date.today())
        ads_date  = user.get("ads_date", "")
        ads_today = user.get("ads_today", 0) if ads_date == today else 0

        if ads_today >= MAX_ADS_PER_DAY:
            return {
                "status":  "error",
                "message": f"Daily ad limit reached ({MAX_ADS_PER_DAY}/{MAX_ADS_PER_DAY}). Come back tomorrow!",
                "data":    {"ads_done": ads_today, "ads_total": MAX_ADS_PER_DAY, "remaining": 0},
            }, 400

        done      = ads_today + 1
        remaining = MAX_ADS_PER_DAY - done
        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": AD_COIN_REWARD},
                "$set": {
                    "ads_date":        today,
                    "ads_today":       done,
                    "last_ad_claim_at": datetime.utcnow().isoformat(),
                },
            },
        )
        return {
            "status":  "success",
            "message": f"{AD_COIN_REWARD} coins earned! ({done}/{MAX_ADS_PER_DAY} ads watched today)",
            "data":    {"reward": AD_COIN_REWARD, "ads_done": done, "ads_total": MAX_ADS_PER_DAY, "remaining": remaining},
        }, 200
    except Exception as exc:
        logger.error("manual_ad_reward error for %s: %s", user_id, exc)
        return {"status": "error", "message": "Server error."}, 500


@app.route("/claim_ad/<int:user_id>", methods=["POST"])
def claim_ad_api(user_id: int):
    data = request.get_json(silent=True) or {}
    payload, status_code = manual_ad_reward(user_id, (data.get("token") or "").strip())
    return jsonify(payload), status_code


@app.route("/ad_claim_token/<int:user_id>", methods=["POST"])
def ad_claim_token_api(user_id: int):
    payload, status_code = create_ad_claim_token(user_id)
    return jsonify(payload), status_code


# ============================================================
# CHANNEL CLAIM
# ============================================================

@app.route("/claim_channel", methods=["POST"])
def claim_channel_api():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    channel_id  = sanitize_text(data.get("channel_id", "")).lower()
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
            return jsonify({"status": "error", "message": "Reward already claimed for this channel! \u2705"}), 400

        if channel_url:
            ch_username = extract_channel_username(channel_url)
            if ch_username:
                is_member = verify_channel_membership(ch_username, user_id)
                if not is_member:
                    return jsonify({
                        "status":  "not_joined",
                        "message": "Please join the channel first, then tap Retry!",
                    }), 400

        reward = CHANNEL_REWARDS.get(channel_id, CHANNEL_REWARD_PER_CHANNEL)
        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": reward},
                "$set": {f"channel_claims.{channel_id}": True},
            },
        )
        return jsonify({
            "status":  "success",
            "message": f"{reward} coins credited for joining the channel!",
            "data":    {"reward": reward},
        })
    except Exception as exc:
        logger.error("claim_channel error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ============================================================
# PROMOTION TASKS
# ============================================================

@app.route("/get_promo_tasks", methods=["GET"])
def get_promo_tasks_api():
    try:
        tasks = list(promo_tasks_col.find({"active": True}, {"_id": 0}))
        return jsonify({"status": "success", "tasks": tasks})
    except Exception as exc:
        logger.error("get_promo_tasks error: %s", exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/claim_promo_task", methods=["POST"])
def claim_promo_task_api():
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
            {"$inc": {"coins": reward}, "$addToSet": {"promo_task_completions": task_id}},
        )
        return jsonify({
            "status":  "success",
            "message": f"{reward} coins added for completing the promotion task!",
            "data":    {"reward": reward},
        })
    except Exception as exc:
        logger.error("claim_promo_task error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ============================================================
# ADMIN: PROMO TASK MANAGEMENT (API)
# ============================================================

@app.route("/admin/add_promo_task", methods=["POST"])
def admin_add_promo_task():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data        = request.get_json(silent=True) or {}
    task_id     = sanitize_text(data.get("task_id", "")).strip()
    title       = sanitize_text(data.get("title", "")).strip()
    description = sanitize_text(data.get("description", ""), max_length=300).strip()
    link        = sanitize_text(data.get("link", ""), max_length=500).strip()
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
            "task_id":     task_id,
            "title":       title,
            "description": description,
            "link":        link,
            "reward":      reward,
            "active":      True,
            "created_at":  datetime.utcnow().isoformat(),
        })
        logger.info("Admin added promo task: %s", task_id)
        return jsonify({"status": "success", "message": f"Promo task '{task_id}' added."})
    except Exception as exc:
        logger.error("admin_add_promo_task error: %s", exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/admin/remove_promo_task", methods=["POST"])
def admin_remove_promo_task():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data    = request.get_json(silent=True) or {}
    task_id = sanitize_text(data.get("task_id", "")).strip()
    if not task_id:
        return jsonify({"status": "error", "message": "task_id is required."}), 400
    try:
        result = promo_tasks_col.update_one(
            {"task_id": task_id},
            {"$set": {"active": False, "deactivated_at": datetime.utcnow().isoformat()}},
        )
        if result.matched_count:
            return jsonify({"status": "success", "message": f"Promo task '{task_id}' deactivated."})
        return jsonify({"status": "error", "message": "Task not found."}), 404
    except Exception as exc:
        logger.error("admin_remove_promo_task error: %s", exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ============================================================
# SPONSOR CLICK TRACKING
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

    slot_id  = sanitize_text(data.get("slot_id", ""), max_length=20).lower()
    link_url = sanitize_text(data.get("link_url", ""), max_length=500)

    if slot_id not in ("slot1", "slot2", "slot3", "slot4") or not link_url:
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


# ============================================================
# DEVICE CHECK
# ============================================================

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

        if fingerprint:
            fp_siblings     = list(
                users_col.find(
                    {"fingerprint": fingerprint, "user_id": {"$ne": user_id}},
                    {"user_id": 1, "joined": 1, "blocked": 1},
                ).limit(50)
            )
            active_siblings = [s for s in fp_siblings if not s.get("blocked")]

            if active_siblings:
                all_accounts = active_siblings + [
                    {"user_id": user_id, "joined": current_user.get("joined"), "blocked": False}
                ]
                all_accounts.sort(key=lambda u: u.get("joined") or "")
                original   = all_accounts[0]
                duplicates = all_accounts[1:]

                banned_ids = []
                for dup in duplicates:
                    dup_id = dup["user_id"]
                    res    = users_col.update_one(
                        {"user_id": dup_id, "blocked": {"$ne": True}},
                        {
                            "$set": {
                                "blocked":      True,
                                "block_reason": "duplicate_device_fingerprint",
                                "blocked_at":   datetime.utcnow().isoformat(),
                                "fp_flagged":   True,
                            }
                        },
                    )
                    if res.modified_count:
                        banned_ids.append(dup_id)
                        logger.warning(
                            "AUTO-BAN: user %s blocked (duplicate fingerprint of %s)",
                            dup_id, original["user_id"],
                        )
                        try:
                            bot.send_message(
                                dup_id,
                                "\U0001f6ab Your account has been blocked.\n"
                                "Reason: Another account from the same device is already registered.\n"
                                "Only one account per device is allowed.",
                            )
                        except Exception:
                            pass

                if banned_ids:
                    try:
                        bot.send_message(
                            ADMIN_ID,
                            "\U0001f6ab *Auto-Ban (Duplicate Device)*\n"
                            f"Original: `{original['user_id']}`\n"
                            f"Banned: {', '.join('`' + str(b) + '`' for b in banned_ids)}",
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass

                if user_id != original["user_id"]:
                    return jsonify({"status": "blocked"})

        if ip:
            ip_siblings_count = users_col.count_documents(
                {"ip": ip, "user_id": {"$ne": user_id}, "blocked": {"$ne": True}}
            )
            if ip_siblings_count > 0 and not current_user.get("ip_flagged"):
                users_col.update_one({"user_id": user_id}, {"$set": {"ip_flagged": True}})
                try:
                    bot.send_message(
                        ADMIN_ID,
                        f"\u26a0\ufe0f IP Conflict (review only, no auto-ban)\n"
                        f"User `{user_id}` shares IP `{ip}` with {ip_siblings_count} other account(s).",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

        update_fields = {}
        if ip:
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
# PROMO CODE
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

        used_by  = promo.get("used_by", [])
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
            "status":  "success",
            "message": f"{coins} coins added to your balance!",
            "data":    {"reward": coins},
        })
    except Exception as exc:
        logger.error("redeem_promo_api error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


# ============================================================
# SUPPORT
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
            f"\U0001f3a7 *Support Message*\n\nUser ID: `{user_id}`\n\n{msg_text}",
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
        page  = max(1, int(request.args.get("page", 1)))
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
            ).sort("coins", -1).skip(skip).limit(limit)
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
        query         = {"status": status_filter} if status_filter else {}
        withdrawals   = list(
            withdrawals_col.find(query, {"_id": 0}).sort("timestamp", -1).limit(50)
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
            bot.send_message(
                uid,
                "\U0001f389 *Your withdrawal has been approved!* Payment is being processed. \u2705",
                parse_mode="Markdown",
            )
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
            bot.send_message(
                uid,
                f"\u274c Your withdrawal was rejected. {withdraw['amount']} coins have been refunded.",
            )
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
        uid    = int(data.get("user_id", 0))
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
        bot.send_message(uid, "\u26d4 Your account has been blocked for violating our terms of service.")
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
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    limit  = 100
    skip   = (page - 1) * limit
    total  = users_col.count_documents({"blocked": True})
    banned = list(
        users_col.find(
            {"blocked": True},
            {"user_id": 1, "username": 1, "coins": 1, "block_reason": 1, "blocked_at": 1,
             "joined": 1, "ip_flagged": 1, "fp_flagged": 1, "_id": 0},
        ).sort("blocked_at", -1).skip(skip).limit(limit)
    )
    return jsonify({"status": "success", "banned_users": banned, "total": total, "page": page})


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
        docs   = list(sponsor_clicks_col.find({}, {"_id": 1, "link_url": 1, "count": 1}))
        result = {d["_id"]: {"count": d.get("count", 0), "link_url": d.get("link_url", "")} for d in docs}
        return jsonify({"status": "success", "clicks": result})
    except Exception as exc:
        logger.error("admin_sponsor_clicks error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


# ============================================================
# SERVER HEALTH
# ============================================================

_SERVER_START_TIME = time.time()


@app.route("/admin/health", methods=["GET"])
def admin_health():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        cpu_pct    = psutil.cpu_percent(interval=0.5)
        mem        = psutil.virtual_memory()
        disk       = psutil.disk_usage("/")
        uptime_sec = int(time.time() - _SERVER_START_TIME)
        h, rem     = divmod(uptime_sec, 3600)
        m, s       = divmod(rem, 60)
        return jsonify({
            "status":        "ok",
            "cpu_pct":       cpu_pct,
            "mem_used_mb":   round(mem.used / 1024 / 1024, 1),
            "mem_total_mb":  round(mem.total / 1024 / 1024, 1),
            "mem_pct":       mem.percent,
            "disk_used_gb":  round(disk.used / 1024 / 1024 / 1024, 2),
            "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 2),
            "disk_pct":      disk.percent,
            "uptime":        f"{h}h {m}m {s}s",
            "uptime_sec":    uptime_sec,
        })
    except Exception as exc:
        logger.error("admin_health error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


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
    msg  = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"status": "error", "message": "Empty message"}), 400
    total = users_col.count_documents({})
    Thread(target=_do_broadcast, args=(msg,), daemon=True).start()
    return jsonify({"status": "success", "message": f"Broadcast started for ~{total} users."})


# ============================================================
# ADMIN PANEL ENDPOINTS
# ============================================================

@app.route("/admin/login", methods=["POST"])
def admin_login():
    ip        = _get_client_ip()
    data      = request.json or {}
    submitted = (data.get("token") or "").strip()

    if not ADMIN_TOKEN:
        return jsonify({"status": "error", "message": "ADMIN_TOKEN not configured on server"}), 500

    # Lockout check karo pehle
    allowed, info = check_admin_login_attempt(ip, success=False)
    if not allowed:
        mins = info // 60
        secs = info % 60
        logger.warning("SECURITY: Admin login blocked for IP %s (lockout %dm %ds remaining).", ip, mins, secs)
        try:
            bot.send_message(
                ADMIN_ID,
                f"\U0001f6a8 *Admin Login Lockout Active*\n\n"
                f"\U0001f310 IP: `{ip}`\n"
                f"\u23f3 Lockout remaining: `{mins}m {secs}s`\n\n"
                f"_Someone is repeatedly trying to access admin panel from this IP._",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return jsonify({
            "status":  "error",
            "message": f"Too many failed attempts. Try again in {mins}m {secs}s.",
        }), 429

    if submitted and submitted == ADMIN_TOKEN:
        check_admin_login_attempt(ip, success=True)
        logger.info("Admin login success from IP %s", ip)
        return jsonify({"status": "success", "message": "Authenticated"})

    # Wrong token — attempts count badha do
    _, remaining = check_admin_login_attempt(ip, success=False)
    logger.warning("SECURITY: Admin login failed from IP %s (%d attempts remaining).", ip, remaining)
    if remaining <= 2:
        try:
            bot.send_message(
                ADMIN_ID,
                f"\u26a0\ufe0f *Admin Login Alert*\n\n"
                f"\U0001f310 IP: `{ip}`\n"
                f"\u274c Wrong token entered\n"
                f"\U0001f512 Attempts remaining before lockout: `{remaining}`",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    return jsonify({
        "status":  "error",
        "message": f"Invalid token. {remaining} attempt(s) remaining before lockout.",
    }), 401


@app.route("/admin/get_config", methods=["GET"])
def admin_get_config():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        codes  = get_live_task_codes() or {}
        merged = {**TASK_CODES, **codes}
        return jsonify({"status": "success", "task_codes": merged})
    except Exception as exc:
        logger.error("admin_get_config error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/admin/update_codes", methods=["POST"])
def admin_update_codes():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data     = request.json or {}
    codes_in = data.get("codes") or {}
    notify   = bool(data.get("notify", False))

    if not isinstance(codes_in, dict) or not codes_in:
        return jsonify({"status": "error", "message": "No codes provided"}), 400

    accepted = {}
    rejected = []
    for k, v in codes_in.items():
        if k not in TASK_CODES:
            rejected.append(k)
            continue
        v_clean = str(v or "").strip().upper()
        if not re.match(r"^[A-Z0-9_]{2,30}$", v_clean):
            rejected.append(k)
            continue
        accepted[k] = v_clean

    if not accepted:
        return jsonify({
            "status":  "error",
            "message": f"No valid codes. Rejected: {', '.join(rejected) or 'all'}",
        }), 400

    try:
        for k, v in accepted.items():
            TASK_CODES[k] = v
        merged = {**TASK_CODES}
        config_col.update_one(
            {"_id": "task_codes"},
            {"$set": {"codes": merged, "updated_at": datetime.utcnow().isoformat()}},
            upsert=True,
        )
        global _task_codes_cache, _task_codes_cache_time
        _task_codes_cache      = None
        _task_codes_cache_time = 0.0

        logger.info("Admin panel updated codes: %s (notify=%s)", list(accepted.keys()), notify)

        if notify:
            lines = ["\U0001f504 *Task Codes Updated!*\n"]
            for k, v in accepted.items():
                display = TASK_DISPLAY_NAMES.get(k, k.upper())
                lines.append(f"\u2022 {display}: `{v}`")
            lines.append("\nOpen the Mini App and use the new codes to earn coins! \U0001fa99")
            notice = "\n".join(lines)
            _spawn_broadcast(
                notice,
                admin_chat_id=ADMIN_ID,
                summary_label=f"Codes updated: {', '.join(accepted.keys())}",
            )

        return jsonify({
            "status":  "success",
            "message": f"{len(accepted)} code(s) updated" + (", broadcasting\u2026" if notify else " (silent)"),
            "updated": accepted,
            "rejected": rejected,
        })
    except Exception as exc:
        logger.error("admin_update_codes error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/admin/withdrawals", methods=["GET"])
def admin_withdrawals_panel():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        status_arg = (request.args.get("status") or "pending").lower()
        if status_arg == "all":
            query = {}
        elif status_arg == "approved":
            query = {"status": "Approved \u2705"}
        elif status_arg == "rejected":
            query = {"status": "Rejected \u274c"}
        else:
            query = {"status": "Pending \u23f3"}

        rows = list(withdrawals_col.find(query, {"_id": 0}).sort("date", -1).limit(100))
        for w in rows:
            if "amount" in w and "coins" not in w:
                w["coins"] = w["amount"]
        return jsonify({"status": "success", "withdrawals": rows})
    except Exception as exc:
        logger.error("admin_withdrawals_panel error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/admin/update_withdrawal", methods=["POST"])
def admin_update_withdrawal():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        uid = int(data.get("user_id", 0))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    action = (data.get("action") or "").strip().lower()
    if action not in {"approve", "reject"}:
        return jsonify({"status": "error", "message": "Action must be 'approve' or 'reject'"}), 400

    pending_status = "Pending \u23f3"
    withdraw       = withdrawals_col.find_one({"user_id": uid, "status": pending_status})
    if not withdraw:
        return jsonify({"status": "error", "message": "No pending withdrawal found"}), 404

    try:
        if action == "approve":
            withdrawals_col.update_one(
                {"user_id": uid, "status": pending_status},
                {"$set": {"status": "Approved \u2705"}},
            )
            try:
                bot.send_message(
                    uid,
                    "\U0001f389 *Your withdrawal has been approved!* Payment is being processed. \u2705",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
            logger.info("Admin panel approved withdrawal for %s", uid)
            return jsonify({"status": "success", "message": f"Withdrawal approved for user {uid}"})

        amount = int(withdraw.get("amount", withdraw.get("coins", 0)))
        users_col.update_one({"user_id": uid}, {"$inc": {"coins": amount}})
        withdrawals_col.update_one(
            {"user_id": uid, "status": pending_status},
            {"$set": {"status": "Rejected \u274c"}},
        )
        try:
            bot.send_message(
                uid,
                f"\u274c Your withdrawal was rejected. {amount} coins have been refunded to your balance.",
            )
        except Exception:
            pass
        logger.info("Admin panel rejected withdrawal for %s (refund %s)", uid, amount)
        return jsonify({"status": "success", "message": f"Withdrawal rejected, {amount} coins refunded"})
    except Exception as exc:
        logger.error("admin_update_withdrawal error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/admin/stats", methods=["GET"])
def admin_stats():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        total_users = users_col.count_documents({})
        pending     = withdrawals_col.count_documents({"status": "Pending \u23f3"})
        approved    = withdrawals_col.count_documents({"status": "Approved \u2705"})
        agg         = list(users_col.aggregate([
            {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$coins", 0]}}}}
        ]))
        total_coins = int(agg[0]["total"]) if agg else 0
        return jsonify({
            "status":       "success",
            "total_users":  total_users,
            "pending":      pending,
            "approved":     approved,
            "total_coins":  total_coins,
        })
    except Exception as exc:
        logger.error("admin_stats error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/admin/search_user", methods=["GET"])
def admin_search_user():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    user_id_raw  = (request.args.get("user_id")  or "").strip()
    username_raw = (request.args.get("username") or "").strip().lstrip("@")
    query = {}
    if user_id_raw:
        try:
            query = {"user_id": int(user_id_raw)}
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "Invalid user ID"}), 400
    elif username_raw:
        query = {"username": {"$regex": f"^{re.escape(username_raw)}$", "$options": "i"}}
    else:
        return jsonify({"status": "error", "message": "User ID or username is required"}), 400

    user = users_col.find_one(
        query,
        {
            "_id": 0, "user_id": 1, "username": 1, "coins": 1, "referrals": 1,
            "referral_count": 1, "blocked": 1, "joined": 1, "joined_at": 1,
            "ip_flagged": 1, "fp_flagged": 1, "task_completions": 1,
            "promo_task_completions": 1, "channel_claims": 1,
            "ads_today": 1, "ads_date": 1,
        },
    )
    if not user:
        return jsonify({"status": "error", "message": "User not found"}), 404

    referrals      = get_referral_list(int(user["user_id"]))
    referred_users = [r for r in referrals.split(",") if r.strip()]
    if "joined" not in user:
        user["joined"] = user.get("joined_at", "\u2014")
    user["referral_count"] = user.get("referral_count", len(referred_users))
    user["referrals"]      = referrals
    user["referrals_made"] = len(referred_users)
    return jsonify({"status": "success", "user": user})


# ============================================================
# LOTTERY HELPERS + USER ENDPOINTS
# ============================================================

def get_lottery_config() -> dict:
    global _lottery_cfg_cache, _lottery_cfg_cache_time
    now = time.time()
    if _lottery_cfg_cache is not None and now - _lottery_cfg_cache_time < LOTTERY_CFG_CACHE_TTL:
        return _lottery_cfg_cache
    try:
        cfg    = config_col.find_one({"_id": "lottery_config"}) or {}
        merged = {
            "ticket_price": int(cfg.get("ticket_price", LOTTERY_DEFAULTS["ticket_price"])),
            "prize":        int(cfg.get("prize",        LOTTERY_DEFAULTS["prize"])),
            "active":       bool(cfg.get("active",      LOTTERY_DEFAULTS["active"])),
        }
    except Exception:
        merged = dict(LOTTERY_DEFAULTS)
    _lottery_cfg_cache      = merged
    _lottery_cfg_cache_time = now
    return merged


def _bust_lottery_cache():
    global _lottery_cfg_cache, _lottery_cfg_cache_time
    _lottery_cfg_cache      = None
    _lottery_cfg_cache_time = 0.0


def _today_round_id() -> str:
    return f"round_{date.today().isoformat()}"


def _get_or_create_today_round() -> dict:
    rid = _today_round_id()
    cfg = get_lottery_config()
    round_doc = lottery_col.find_one_and_update(
        {"_id": rid},
        {"$setOnInsert": {
            "ticket_price": cfg["ticket_price"],
            "prize":        cfg["prize"],
            "participants": [],
            "winner":       None,
            "drawn":        False,
            "created_at":   datetime.utcnow().isoformat(),
        }},
        upsert=True,
        return_document=True,
    )
    return round_doc or lottery_col.find_one({"_id": rid})


def _perform_auto_draw(rid: str, notify_chat_id: int | None = None) -> dict:
    """
    Core lottery draw logic — reusable by both manual /drawlottery and
    the midnight auto-draw thread.
    """
    rdoc = lottery_col.find_one({"_id": rid})
    if not rdoc:
        return {"success": False, "message": "No lottery round found for today (no tickets sold yet)."}
    if rdoc.get("drawn"):
        winner = rdoc.get("winner", "?")
        return {"success": False, "message": f"Round already drawn. Winner: {winner}"}

    participants = rdoc.get("participants", [])
    if not participants:
        return {"success": False, "message": "No participants in today's round. Draw skipped."}

    prize     = int(rdoc.get("prize", LOTTERY_DEFAULTS["prize"]))
    winner_id = secrets.choice(participants)

    locked = lottery_col.find_one_and_update(
        {"_id": rid, "drawn": False},
        {"$set": {"drawn": True, "winner": winner_id, "drawn_at": datetime.utcnow().isoformat()}},
        return_document=True,
    )
    if not locked:
        return {"success": False, "message": "Round was just drawn by another process."}

    users_col.update_one(
        {"user_id": winner_id},
        {
            "$inc": {"coins": prize},
            "$set": {
                "pending_winner_popup": True,
                "pending_winner_prize": prize,
            },
        },
    )
    logger.info("Lottery drawn: round=%s winner=%s prize=%s", rid, winner_id, prize)

    def _notify():
        try:
            bot.send_message(
                winner_id,
                f"\U0001f389 *CONGRATULATIONS!* \U0001f389\n\n"
                f"You won today's lottery!\n\n"
                f"\U0001f3c6 Prize: *{prize}* \U0001fa99 has been added to your balance!\n"
                f"\U0001f3b0 Round: `{rid}`\n\n"
                f"Open the Mini App to check your new balance \U0001f680",
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.warning("Notify winner %s failed: %s", winner_id, exc)

        losers = [uid for uid in participants if uid != winner_id]
        for uid in losers:
            try:
                bot.send_message(
                    uid,
                    f"\U0001f3b0 *Today's Lottery Result*\n\n"
                    f"Winner: `{winner_id}` (won {prize} \U0001fa99)\n"
                    f"Total tickets: {len(participants)}\n\n"
                    f"Better luck next time! \U0001f340\n"
                    f"_New round opens at 00:00 UTC._",
                    parse_mode="Markdown",
                )
                time.sleep(0.05)
            except Exception:
                pass

        if LOTTERY_CHANNEL:
            try:
                bot.send_message(
                    LOTTERY_CHANNEL,
                    f"\U0001f3b0 *Daily Lottery Result \u2014 {rid}*\n\n"
                    f"\U0001f3c6 Winner: `{winner_id}`\n"
                    f"\U0001f4b0 Prize: *{prize}* \U0001fa99\n"
                    f"\U0001f465 Total tickets: {len(participants)}\n\n"
                    f"Congratulations to the winner! \U0001f389\n"
                    f"_New round starts at 00:00 UTC. Buy your ticket in the bot!_",
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.warning("Lottery channel announce failed: %s", exc)

        if notify_chat_id:
            try:
                bot.send_message(
                    notify_chat_id,
                    f"\u2705 Lottery notifications sent to {len(losers)} loser(s) + 1 winner.",
                )
            except Exception:
                pass

    threading.Thread(target=_notify, daemon=True, name=f"lottery-notify-{rid}").start()

    return {
        "success":      True,
        "message":      f"Draw complete! Winner: {winner_id}, Prize: {prize} \U0001fa99",
        "winner_id":    winner_id,
        "prize":        prize,
        "participants": len(participants),
    }


@app.route("/admin/lottery", methods=["GET"])
def admin_lottery_status():
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    try:
        cfg          = get_lottery_config()
        round_doc    = lottery_col.find_one({"_id": _today_round_id()}, {"_id": 0}) or {}
        participants = round_doc.get("participants", [])
        return jsonify({
            "status": "success",
            "config": cfg,
            "today": {
                "round_id":     _today_round_id(),
                "ticket_price": round_doc.get("ticket_price", cfg["ticket_price"]),
                "prize":        round_doc.get("prize", cfg["prize"]),
                "tickets_sold": len(participants),
                "pool_coins":   len(participants) * round_doc.get("ticket_price", cfg["ticket_price"]),
                "drawn":        round_doc.get("drawn", False),
                "winner":       round_doc.get("winner"),
            },
        })
    except Exception as exc:
        logger.error("admin_lottery_status error: %s", exc)
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route("/get_lottery_status", methods=["GET"])
def get_lottery_status():
    try:
        user_id = int(request.args.get("user_id", 0))
    except (ValueError, TypeError):
        user_id = 0

    cfg       = get_lottery_config()
    round_doc = _get_or_create_today_round()
    participants = round_doc.get("participants", [])

    last_drawn = lottery_col.find_one(
        {"drawn": True, "winner": {"$ne": None}},
        sort=[("drawn_at", -1)],
        projection={"_id": 1, "winner": 1, "prize": 1, "drawn_at": 1},
    ) or {}

    return jsonify({
        "status":       "success",
        "active":       cfg["active"],
        "ticket_price": round_doc.get("ticket_price", cfg["ticket_price"]),
        "prize":        round_doc.get("prize", cfg["prize"]),
        "tickets_sold": len(participants),
        "has_ticket":   user_id in participants if user_id else False,
        "drawn":        round_doc.get("drawn", False),
        "round_id":     round_doc.get("_id"),
        "last_winner":  {
            "user_id":  last_drawn.get("winner"),
            "prize":    last_drawn.get("prize"),
            "round_id": last_drawn.get("_id"),
        } if last_drawn else None,
    })


@app.route("/buy_lottery_ticket", methods=["POST"])
def buy_lottery_ticket():
    data = request.json or {}
    try:
        user_id = int(data.get("user_id", 0))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400
    if not user_id:
        return jsonify({"status": "error", "message": "User ID required."}), 400

    cfg = get_lottery_config()
    if not cfg["active"]:
        return jsonify({"status": "error", "message": "Lottery is currently disabled."}), 400

    round_doc = _get_or_create_today_round()
    if round_doc.get("drawn"):
        return jsonify({"status": "error", "message": "Today's round already drawn. Try tomorrow!"}), 400

    ticket_price = int(round_doc.get("ticket_price", cfg["ticket_price"]))
    rid          = round_doc["_id"]

    if user_id in round_doc.get("participants", []):
        return jsonify({"status": "error", "message": "You already have a ticket for today!"}), 400

    user = users_col.find_one_and_update(
        {"user_id": user_id, "coins": {"$gte": ticket_price}, "blocked": {"$ne": True}},
        {"$inc": {"coins": -ticket_price}},
        return_document=True,
    )
    if not user:
        u = users_col.find_one({"user_id": user_id}, {"coins": 1, "blocked": 1, "_id": 0})
        if not u:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if u.get("blocked"):
            return jsonify({"status": "error", "message": "Your account is blocked."}), 403
        return jsonify({
            "status":  "error",
            "message": f"Need {ticket_price} coins. You have {u.get('coins', 0)}.",
        }), 400

    add_result = lottery_col.update_one(
        {"_id": rid, "participants": {"$ne": user_id}, "drawn": False},
        {"$addToSet": {"participants": user_id}},
    )
    if add_result.modified_count != 1:
        users_col.update_one({"user_id": user_id}, {"$inc": {"coins": ticket_price}})
        return jsonify({"status": "error", "message": "Could not buy ticket. Please try again."}), 409

    new_balance = int(user.get("coins", 0))
    refreshed   = lottery_col.find_one({"_id": rid}, {"participants": 1, "_id": 0}) or {}
    return jsonify({
        "status":       "success",
        "message":      "\U0001f3ab Ticket purchased! Good luck \U0001f340",
        "new_balance":  new_balance,
        "tickets_sold": len(refreshed.get("participants", [])),
    })


# ============================================================
# SPIN WHEEL + COIN MINING API ENDPOINTS
# ============================================================

# ── Feature Config (frontend ke liye) ────────────────────────

@app.route("/get_feature_config", methods=["GET"])
def get_feature_config_api():
    """Frontend is se puchta hai ki spin/mining lock hai ya nahi."""
    cfg = get_feature_config()
    return jsonify({"status": "success", **cfg})


# ══════════════════════════════════════════════════
#  🎡 SPIN WHEEL
# ══════════════════════════════════════════════════

@app.route("/spin_token/<int:user_id>", methods=["POST"])
def spin_token_api(user_id: int):
    """
    Step 1: Frontend pehle yeh call kare — ad dikhane se pehle.
    Token milega, jisko /do_spin mein use karo.
    """
    if user_id <= 0:
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    cfg = get_feature_config()
    if not cfg.get("spin_active"):
        return jsonify({"status": "locked", "message": "Spin Wheel is currently locked by admin."}), 403

    if is_rate_limited(f"spintoken_{user_id}", 15):
        return jsonify({"status": "error", "message": "Please wait before requesting another spin."}), 429

    try:
        user = users_col.find_one({"user_id": user_id}, {"blocked": 1, "spins_today": 1, "spins_date": 1})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        today     = str(date.today())
        spins_date = user.get("spins_date", "")
        spins_done = user.get("spins_today", 0) if spins_date == today else 0

        if spins_done >= SPIN_PER_DAY:
            return jsonify({
                "status":  "error",
                "message": f"Daily spin limit reached ({SPIN_PER_DAY}/{SPIN_PER_DAY}). Come back tomorrow!",
                "data":    {"spins_done": spins_done, "spins_total": SPIN_PER_DAY},
            }), 400

        # Stale tokens saaf karo
        ad_reward_tokens_col.delete_many({"user_id": user_id, "source": "spin_wheel"})

        raw_token  = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.utcnow() + timedelta(seconds=SPIN_TOKEN_TTL)

        ad_reward_tokens_col.insert_one({
            "_id":        token_hash,
            "user_id":    user_id,
            "source":     "spin_wheel",
            "created_at": datetime.utcnow(),
            "expires_at": expires_at,
        })

        return jsonify({
            "status":      "success",
            "token":       raw_token,
            "expires_in":  SPIN_TOKEN_TTL,
            "spins_done":  spins_done,
            "spins_total": SPIN_PER_DAY,
        })
    except Exception as exc:
        logger.error("spin_token_api error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/do_spin/<int:user_id>", methods=["POST"])
def do_spin_api(user_id: int):
    """
    Step 2: Ad dekhne ke baad yahan token bhejo — spin result milega.
    """
    if user_id <= 0:
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    cfg = get_feature_config()
    if not cfg.get("spin_active"):
        return jsonify({"status": "locked", "message": "Spin Wheel is currently locked by admin."}), 403

    if is_rate_limited(f"dospin_{user_id}", 10):
        return jsonify({"status": "error", "message": "Please wait before spinning again."}), 429

    data      = request.get_json(silent=True) or {}
    raw_token = (data.get("token") or "").strip()
    if not raw_token:
        return jsonify({"status": "error", "message": "Spin token missing. Watch the ad first."}), 400

    try:
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_doc  = ad_reward_tokens_col.find_one_and_delete({
            "_id":     token_hash,
            "user_id": user_id,
            "source":  "spin_wheel",
        })
        if not token_doc:
            return jsonify({"status": "error", "message": "Invalid or already used spin token."}), 403
        if token_doc.get("expires_at") and token_doc["expires_at"] < datetime.utcnow():
            return jsonify({"status": "error", "message": "Spin token expired. Watch the ad again."}), 403

        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        today      = str(date.today())
        spins_date = user.get("spins_date", "")
        spins_done = user.get("spins_today", 0) if spins_date == today else 0

        if spins_done >= SPIN_PER_DAY:
            return jsonify({
                "status":  "error",
                "message": f"Daily spin limit reached ({SPIN_PER_DAY}/{SPIN_PER_DAY}). Come back tomorrow!",
            }), 400

        # Weighted random spin result
        import random
        reward = random.choices(SPIN_REWARDS, weights=SPIN_WEIGHTS, k=1)[0]
        new_spins = spins_done + 1

        update_op = {
            "$set": {"spins_today": new_spins, "spins_date": today},
        }
        if reward > 0:
            update_op["$inc"] = {"coins": reward}

        users_col.update_one({"user_id": user_id}, update_op)
        logger.info("User %s spun: reward=%s spins=%s/%s", user_id, reward, new_spins, SPIN_PER_DAY)

        if reward == 0:
            msg = "Better luck next time! \U0001f340"
        elif reward >= 50:
            msg = f"\U0001f389 JACKPOT! You won *{reward} coins*!"
        else:
            msg = f"You won *{reward} coins*! \U0001fa99"

        return jsonify({
            "status":      "success",
            "reward":      reward,
            "message":     msg,
            "spins_done":  new_spins,
            "spins_total": SPIN_PER_DAY,
            "spins_left":  SPIN_PER_DAY - new_spins,
        })
    except Exception as exc:
        logger.error("do_spin_api error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/get_spin_status/<int:user_id>", methods=["GET"])
def get_spin_status_api(user_id: int):
    """Frontend ke liye spin status — kitne spins bache hain."""
    cfg = get_feature_config()
    try:
        user = users_col.find_one({"user_id": user_id}, {"spins_today": 1, "spins_date": 1})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        today      = str(date.today())
        spins_date = user.get("spins_date", "")
        spins_done = user.get("spins_today", 0) if spins_date == today else 0
        return jsonify({
            "status":       "success",
            "spin_active":  cfg.get("spin_active", True),
            "spins_done":   spins_done,
            "spins_total":  SPIN_PER_DAY,
            "spins_left":   max(0, SPIN_PER_DAY - spins_done),
            "ad_required":  SPIN_AD_REQUIRED,
        })
    except Exception as exc:
        logger.error("get_spin_status error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ══════════════════════════════════════════════════
#  ⛏️ COIN MINING
# ══════════════════════════════════════════════════

@app.route("/mining_ad_token/<int:user_id>", methods=["POST"])
def mining_ad_token_api(user_id: int):
    """
    Step 1: Mining shuru karne se pehle 2 ads dekhne hote hain.
    Har ad ke liye ek token generate hota hai.
    """
    if user_id <= 0:
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    cfg = get_feature_config()
    if not cfg.get("mining_active"):
        return jsonify({"status": "locked", "message": "Coin Mining is currently locked by admin."}), 403

    if is_rate_limited(f"miningtoken_{user_id}", 15):
        return jsonify({"status": "error", "message": "Please wait before requesting another mining token."}), 429

    try:
        user = users_col.find_one(
            {"user_id": user_id},
            {"blocked": 1, "mining_start_time": 1, "mining_ads_count": 1,
             "mining_ads_date": 1, "last_mining_collect": 1}
        )
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        # Mining cooldown check — 1 ghanta baad hi fir mine kar sakte ho
        last_collect_str = user.get("last_mining_collect", "")
        if last_collect_str:
            try:
                last_collect_dt = datetime.fromisoformat(last_collect_str)
                elapsed         = (datetime.utcnow() - last_collect_dt).total_seconds()
                cooldown_sec    = MINING_COOLDOWN_SECS
                if elapsed < cooldown_sec:
                    remaining  = int(cooldown_sec - elapsed)
                    mins       = remaining // 60
                    secs       = remaining % 60
                    return jsonify({
                        "status":  "cooldown",
                        "message": f"Mining cooldown active. Wait {mins}m {secs}s.",
                        "data":    {"remaining_seconds": remaining},
                    }), 400
            except ValueError:
                pass

        # Check active mining session nahi hai
        mining_start_str = user.get("mining_start_time", "")
        if mining_start_str:
            try:
                mining_start_dt = datetime.fromisoformat(mining_start_str)
                elapsed         = (datetime.utcnow() - mining_start_dt).total_seconds()
                if elapsed < MINING_DURATION_HOURS * 3600:
                    remaining  = int(MINING_DURATION_HOURS * 3600 - elapsed)
                    mins       = remaining // 60
                    secs       = remaining % 60
                    return jsonify({
                        "status":  "mining",
                        "message": f"Mining already in progress! Collect in {mins}m {secs}s.",
                        "data":    {"remaining_seconds": remaining},
                    }), 400
            except ValueError:
                pass

        # Today ke ads count
        today    = str(date.today())
        ads_date = user.get("mining_ads_date", "")
        ads_done = user.get("mining_ads_count", 0) if ads_date == today else 0

        if ads_done >= MINING_ADS_REQUIRED:
            return jsonify({
                "status":  "error",
                "message": "Already watched all required ads. Start mining now!",
                "data":    {"ads_done": ads_done, "ads_required": MINING_ADS_REQUIRED},
            }), 400

        raw_token  = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.utcnow() + timedelta(seconds=MINING_TOKEN_TTL)

        ad_reward_tokens_col.insert_one({
            "_id":        token_hash,
            "user_id":    user_id,
            "source":     "coin_mining",
            "created_at": datetime.utcnow(),
            "expires_at": expires_at,
        })

        return jsonify({
            "status":       "success",
            "token":        raw_token,
            "expires_in":   MINING_TOKEN_TTL,
            "ads_done":     ads_done,
            "ads_required": MINING_ADS_REQUIRED,
        })
    except Exception as exc:
        logger.error("mining_ad_token error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/start_mining/<int:user_id>", methods=["POST"])
def start_mining_api(user_id: int):
    """
    Step 2: Dono ads dekhne ke baad yahan call karo — mining shuru hogi.
    Token consume hoga, mining_start_time set hogi.
    """
    if user_id <= 0:
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    cfg = get_feature_config()
    if not cfg.get("mining_active"):
        return jsonify({"status": "locked", "message": "Coin Mining is currently locked by admin."}), 403

    if is_rate_limited(f"startmining_{user_id}", 20):
        return jsonify({"status": "error", "message": "Please wait before starting mining."}), 429

    data      = request.get_json(silent=True) or {}
    raw_token = (data.get("token") or "").strip()
    if not raw_token:
        return jsonify({"status": "error", "message": "Mining token missing. Watch the ad first."}), 400

    try:
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_doc  = ad_reward_tokens_col.find_one_and_delete({
            "_id":     token_hash,
            "user_id": user_id,
            "source":  "coin_mining",
        })
        if not token_doc:
            return jsonify({"status": "error", "message": "Invalid or already used mining token."}), 403
        if token_doc.get("expires_at") and token_doc["expires_at"] < datetime.utcnow():
            return jsonify({"status": "error", "message": "Mining token expired. Watch the ad again."}), 403

        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        today    = str(date.today())
        ads_date = user.get("mining_ads_date", "")
        ads_done = user.get("mining_ads_count", 0) if ads_date == today else 0
        new_ads  = ads_done + 1

        users_col.update_one(
            {"user_id": user_id},
            {"$set": {"mining_ads_count": new_ads, "mining_ads_date": today}},
        )

        # Agar dono ads dekh liye — mining shuru
        if new_ads >= MINING_ADS_REQUIRED:
            now = datetime.utcnow()
            users_col.update_one(
                {"user_id": user_id},
                {"$set": {
                    "mining_start_time": now.isoformat(),
                    "mining_ads_count":  0,
                    "mining_ads_date":   "",
                }},
            )
            collect_at = now + timedelta(hours=MINING_DURATION_HOURS)
            logger.info("User %s started mining. Collect at %s", user_id, collect_at.isoformat())
            return jsonify({
                "status":          "mining_started",
                "message":         f"\u26cf\ufe0f Mining started! Come back in {MINING_DURATION_HOURS} hour(s) to collect {MINING_REWARD} coins!",
                "collect_at_utc":  collect_at.isoformat(),
                "collect_seconds": MINING_DURATION_HOURS * 3600,
                "reward":          MINING_REWARD,
            })

        return jsonify({
            "status":       "ad_counted",
            "message":      f"Ad {new_ads}/{MINING_ADS_REQUIRED} counted! Watch {MINING_ADS_REQUIRED - new_ads} more ad(s) to start mining.",
            "ads_done":     new_ads,
            "ads_required": MINING_ADS_REQUIRED,
        })
    except Exception as exc:
        logger.error("start_mining_api error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/collect_mining/<int:user_id>", methods=["POST"])
def collect_mining_api(user_id: int):
    """
    Step 3: 1 ghante baad collect karo — 10 coins milenge.
    """
    if user_id <= 0:
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    cfg = get_feature_config()
    if not cfg.get("mining_active"):
        return jsonify({"status": "locked", "message": "Coin Mining is currently locked by admin."}), 403

    if is_rate_limited(f"collectmining_{user_id}", 10):
        return jsonify({"status": "error", "message": "Please wait before collecting."}), 429

    try:
        user = users_col.find_one(
            {"user_id": user_id},
            {"blocked": 1, "mining_start_time": 1}
        )
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404
        if user.get("blocked"):
            return jsonify({"status": "error", "message": "Your account has been blocked."}), 403

        mining_start_str = user.get("mining_start_time", "")
        if not mining_start_str:
            return jsonify({"status": "error", "message": "No active mining session. Start mining first!"}), 400

        try:
            mining_start_dt = datetime.fromisoformat(mining_start_str)
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid mining session. Please start again."}), 400

        elapsed     = (datetime.utcnow() - mining_start_dt).total_seconds()
        required    = MINING_DURATION_HOURS * 3600

        if elapsed < required:
            remaining = int(required - elapsed)
            mins      = remaining // 60
            secs      = remaining % 60
            return jsonify({
                "status":  "not_ready",
                "message": f"Mining in progress! Collect in {mins}m {secs}s.",
                "data":    {"remaining_seconds": remaining},
            }), 400

        now = datetime.utcnow()
        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": MINING_REWARD},
                "$set": {
                    "mining_start_time":   "",
                    "last_mining_collect": now.isoformat(),
                    "mining_ads_count":    0,
                    "mining_ads_date":     "",
                },
            },
        )
        logger.info("User %s collected mining reward: %s coins", user_id, MINING_REWARD)
        return jsonify({
            "status":  "success",
            "message": f"\u26cf\ufe0f Mining complete! *{MINING_REWARD} coins* added to your balance! \U0001fa99",
            "reward":  MINING_REWARD,
        })
    except Exception as exc:
        logger.error("collect_mining_api error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route("/get_mining_status/<int:user_id>", methods=["GET"])
def get_mining_status_api(user_id: int):
    """Frontend ke liye mining ka current state."""
    cfg = get_feature_config()
    try:
        user = users_col.find_one(
            {"user_id": user_id},
            {"blocked": 1, "mining_start_time": 1, "mining_ads_count": 1,
             "mining_ads_date": 1, "last_mining_collect": 1}
        )
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404

        now      = datetime.utcnow()
        today    = str(date.today())
        ads_date = user.get("mining_ads_date", "")
        ads_done = user.get("mining_ads_count", 0) if ads_date == today else 0

        mining_start_str   = user.get("mining_start_time", "")
        is_mining          = False
        collect_ready      = False
        remaining_seconds  = 0
        cooldown_remaining = 0

        if mining_start_str:
            try:
                mining_start_dt = datetime.fromisoformat(mining_start_str)
                elapsed         = (now - mining_start_dt).total_seconds()
                required        = MINING_DURATION_HOURS * 3600
                if elapsed < required:
                    is_mining         = True
                    remaining_seconds = int(required - elapsed)
                else:
                    collect_ready = True
            except ValueError:
                pass

        last_collect_str = user.get("last_mining_collect", "")
        if last_collect_str and not is_mining and not collect_ready:
            try:
                last_dt  = datetime.fromisoformat(last_collect_str)
                elapsed  = (now - last_dt).total_seconds()
                cooldown = MINING_COOLDOWN_SECS
                if elapsed < cooldown:
                    cooldown_remaining = int(cooldown - elapsed)
            except ValueError:
                pass

        return jsonify({
            "status":              "success",
            "mining_active":       cfg.get("mining_active", True),
            "is_mining":           is_mining,
            "collect_ready":       collect_ready,
            "remaining_seconds":   remaining_seconds,
            "cooldown_remaining":  cooldown_remaining,
            "ads_done":            ads_done,
            "ads_required":        MINING_ADS_REQUIRED,
            "reward":              MINING_REWARD,
        })
    except Exception as exc:
        logger.error("get_mining_status error for %s: %s", user_id, exc)
        return jsonify({"status": "error", "message": "Server error."}), 500


# ── Admin API: feature lock/unlock ────────────────────────────

@app.route("/admin/set_feature", methods=["POST"])
def admin_set_feature():
    """Admin spin ya mining ko lock/unlock kare."""
    if not check_admin_token(request):
        return jsonify({"status": "error"}), 401
    data    = request.get_json(silent=True) or {}
    feature = (data.get("feature") or "").strip().lower()
    active  = bool(data.get("active", True))
    if feature not in ("spin", "mining"):
        return jsonify({"status": "error", "message": "feature must be 'spin' or 'mining'"}), 400
    field = "spin_active" if feature == "spin" else "mining_active"
    config_col.update_one(
        {"_id": "feature_config"},
        {"$set": {field: active}},
        upsert=True,
    )
    _bust_feature_cache()
    label  = "Spin Wheel" if feature == "spin" else "Coin Mining"
    status = "UNLOCKED \u2705" if active else "LOCKED \U0001f512"
    logger.info("Admin set %s → %s", feature, status)
    return jsonify({"status": "success", "message": f"{label} is now {status}"})


# ============================================================
# AUTO LOTTERY DRAW LOOP — Midnight UTC mein automatic draw
# ============================================================

def auto_lottery_draw_loop() -> None:
    """Background thread: har raat 00:00 UTC pe automatically lottery draw karta hai."""
    logger.info("Auto-lottery draw loop started.")
    last_drawn_date = None

    while True:
        try:
            now       = datetime.utcnow()
            today_str = now.date().isoformat()

            if now.hour == 0 and now.minute < 5 and last_drawn_date != today_str:
                cfg = get_lottery_config()
                if cfg.get("active"):
                    rid    = _today_round_id()
                    result = _perform_auto_draw(rid, notify_chat_id=None)
                    if result["success"]:
                        logger.info(
                            "Auto-draw success: round=%s winner=%s prize=%s participants=%s",
                            rid, result.get("winner_id"), result.get("prize"), result.get("participants"),
                        )
                        last_drawn_date = today_str
                        try:
                            bot.send_message(
                                ADMIN_ID,
                                f"\U0001f3b0 *Auto Lottery Drawn!*\n\n"
                                f"Round: `{rid}`\n"
                                f"\U0001f3c6 Winner: `{result['winner_id']}`\n"
                                f"\U0001f4b0 Prize: `{result['prize']}` \U0001fa99\n"
                                f"\U0001f465 Total Tickets: `{result['participants']}`\n\n"
                                f"_Participants notified automatically._",
                                parse_mode="Markdown",
                            )
                        except Exception as notify_exc:
                            logger.warning("Admin auto-draw notify failed: %s", notify_exc)
                    else:
                        logger.info("Auto-draw skipped: %s", result["message"])
                        if "already drawn" in result["message"].lower() or "no participants" in result["message"].lower():
                            last_drawn_date = today_str
                else:
                    logger.debug("Auto-draw skipped: lottery is inactive.")
                    last_drawn_date = today_str

            if last_drawn_date and last_drawn_date != today_str:
                last_drawn_date = None

        except Exception as exc:
            logger.error("Auto-lottery draw loop error: %s", exc)

        time.sleep(60)


# ============================================================
# BOT COMMANDS
# ============================================================

@bot.message_handler(commands=["start"])
def start(message):
    user_id   = message.from_user.id
    username  = message.from_user.first_name or "User"
    params    = message.text.split()
    referrer_id = params[1] if len(params) > 1 else None

    user          = get_or_create_user(user_id, username, referrer_id)
    current_coins = user.get("coins", 0)
    web_app_url   = f"https://sahdakshsanoj-byte.github.io/Earning-bot/?user_id={user_id}"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("\U0001f4b0 Open Earning Hub", web_app=types.WebAppInfo(web_app_url)))
    markup.add(types.InlineKeyboardButton("\U0001f465 Invite Friends", callback_data="invite_friends"))

    bot.send_message(
        user_id,
        f"\U0001f44b *Hello {username}!*\n\n"
        f"\U0001f4b0 Balance: *{current_coins} \U0001fa99*\n\n"
        f"Invite friends and earn *30 coins* for each referral!\n"
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
        bot.reply_to(
            message,
            f"\U0001f4b0 Your balance: *{coins} \U0001fa99*\n\nKeep earning daily to unlock withdrawal! \U0001f680",
            parse_mode="Markdown",
        )
    else:
        bot.reply_to(message, "Please use /start to register first!")


# ============================================================
# /resetdevice COMMAND
# ============================================================

@bot.message_handler(commands=["resetdevice"])
def reset_device_command(message):
    user_id = message.from_user.id
    parts   = message.text.split()

    # Admin version: /resetdevice <target_user_id>
    if int(user_id) == ADMIN_ID and len(parts) > 1:
        try:
            target_id = int(parts[1])
        except ValueError:
            return bot.reply_to(message, "\u274c Invalid user ID. Usage: /resetdevice <user_id>")

        target_user = users_col.find_one(
            {"user_id": target_id}, {"blocked": 1, "block_reason": 1, "username": 1}
        )
        if not target_user:
            return bot.reply_to(message, f"\u274c User `{target_id}` not found.", parse_mode="Markdown")

        was_blocked = (
            target_user.get("blocked")
            and target_user.get("block_reason") == "duplicate_device_fingerprint"
        )

        users_col.update_one(
            {"user_id": target_id},
            {
                "$unset": {"fingerprint": "", "ip": ""},
                "$set": {
                    "ip_flagged":        False,
                    "fp_flagged":        False,
                    "last_device_reset": datetime.utcnow().isoformat(),
                    **({"blocked": False, "block_reason": "", "blocked_at": ""} if was_blocked else {}),
                },
            },
        )

        try:
            if was_blocked:
                bot.send_message(
                    target_id,
                    "\u2705 *Device Reset by Admin*\n\n"
                    "Your fingerprint & IP data has been cleared by the admin.\n"
                    "Your account has been *unblocked*.\n\n"
                    "You can now use the bot on your new device! \U0001f389",
                    parse_mode="Markdown",
                )
            else:
                bot.send_message(
                    target_id,
                    "\U0001f504 *Device Reset by Admin*\n\n"
                    "Your fingerprint & IP data has been cleared.\n"
                    "You can now use the bot on your new device! \U0001f4f1",
                    parse_mode="Markdown",
                )
        except Exception as notify_exc:
            logger.warning("resetdevice: notify user %s failed: %s", target_id, notify_exc)

        uname  = target_user.get("username", "N/A")
        status = "\u2705 Unblocked + Reset" if was_blocked else "\u2705 Reset only"
        bot.reply_to(
            message,
            f"\u2705 *Device Reset Done*\n\nUser: `{target_id}` ({uname})\nStatus: {status}",
            parse_mode="Markdown",
        )
        logger.info("ADMIN reset device for user %s (was_blocked=%s)", target_id, was_blocked)
        return

    # User version: /resetdevice (khud ke liye)
    user = users_col.find_one({"user_id": user_id})
    if not user:
        return bot.reply_to(message, "\u274c Please use /start to register first.")

    last_reset_str = user.get("last_device_reset", "")
    if last_reset_str:
        try:
            last_reset_dt = datetime.fromisoformat(last_reset_str)
            elapsed_sec   = (datetime.utcnow() - last_reset_dt).total_seconds()
            cooldown_sec  = DEVICE_RESET_COOLDOWN_DAYS * 24 * 3600
            if elapsed_sec < cooldown_sec:
                remaining = cooldown_sec - elapsed_sec
                days      = int(remaining // 86400)
                hours     = int((remaining % 86400) // 3600)
                mins      = int((remaining % 3600) // 60)
                time_str  = f"{days}d {hours}h {mins}m" if days > 0 else f"{hours}h {mins}m"
                return bot.reply_to(
                    message,
                    f"\u23f3 *Reset Cooldown Active*\n\n"
                    f"You can reset your device again in: *{time_str}*\n\n"
                    f"If you need urgent help, contact admin: {ADMIN_ID}",
                    parse_mode="Markdown",
                )
        except Exception:
            pass

    was_blocked = user.get("blocked") and user.get("block_reason") == "duplicate_device_fingerprint"
    had_fp_flag = user.get("fp_flagged", False)
    had_ip_flag = user.get("ip_flagged", False)

    update_set = {
        "ip_flagged":        False,
        "fp_flagged":        False,
        "last_device_reset": datetime.utcnow().isoformat(),
    }
    if was_blocked:
        update_set["blocked"]      = False
        update_set["block_reason"] = ""
        update_set["blocked_at"]   = ""

    users_col.update_one(
        {"user_id": user_id},
        {"$unset": {"fingerprint": "", "ip": ""}, "$set": update_set},
    )

    try:
        flag_info = []
        if had_fp_flag: flag_info.append("FP flagged")
        if had_ip_flag: flag_info.append("IP flagged")
        if was_blocked: flag_info.append("\U0001f513 Auto-unblocked")
        flag_str = ", ".join(flag_info) if flag_info else "No flags"
        bot.send_message(
            ADMIN_ID,
            f"\U0001f504 *User Device Reset*\n\n"
            f"User: `{user_id}`\n"
            f"Name: {user.get('username', 'N/A')}\n"
            f"Flags cleared: {flag_str}",
            parse_mode="Markdown",
        )
    except Exception as admin_exc:
        logger.warning("resetdevice: admin notify failed: %s", admin_exc)

    logger.info(
        "User %s reset device (was_blocked=%s, fp_flag=%s, ip_flag=%s)",
        user_id, was_blocked, had_fp_flag, had_ip_flag,
    )

    if was_blocked:
        bot.reply_to(
            message,
            "\u2705 *Device Reset Successful!*\n\n"
            "Your old device data has been cleared.\n"
            "Your account has been *unblocked*. \U0001f389\n\n"
            "You can now use the bot normally on your new device!\n\n"
            f"_Note: You can reset again after {DEVICE_RESET_COOLDOWN_DAYS} days._",
            parse_mode="Markdown",
        )
    else:
        bot.reply_to(
            message,
            "\u2705 *Device Reset Successful!*\n\n"
            "Your fingerprint & IP data has been cleared.\n"
            "The bot will recognize your new device on next visit! \U0001f4f1\n\n"
            f"_Note: You can reset again after {DEVICE_RESET_COOLDOWN_DAYS} days._",
            parse_mode="Markdown",
        )


@bot.message_handler(commands=["redeem"])
def redeem_promo_command(message):
    user_id = message.from_user.id
    parts   = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /redeem <CODE>\nExample: /redeem WELCOME100")
    code = parts[1].upper()
    user = users_col.find_one({"user_id": user_id}, {"blocked": 1})
    if not user:
        return bot.reply_to(message, "Please use /start to register first.")
    if user.get("blocked"):
        return bot.reply_to(message, "\u26d4 Your account has been blocked.")
    if is_rate_limited(f"promo_{user_id}", 10):
        return bot.reply_to(message, "\u23f3 Please wait a moment before trying again.")
    try:
        promo = promos_col.find_one({"code": code})
        if not promo:
            return bot.reply_to(message, "\u274c Invalid promo code. Please check and try again.")
        if not promo.get("active", True):
            return bot.reply_to(message, "\u274c This promo code has expired or been deactivated.")
        used_by  = promo.get("used_by", [])
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
            "Usage: /createpromo <CODE> <COINS> [max\\_uses] [silent]\n\n"
            "Examples:\n"
            "`/createpromo WELCOME100 100` \u2014 unlimited uses, broadcast\n"
            "`/createpromo VIP500 500 50` \u2014 max 50 uses, broadcast\n"
            "`/createpromo SECRET 100 0 silent` \u2014 no broadcast",
            parse_mode="Markdown",
        )
    silent = parts[-1].lower() in {"silent", "quiet", "nobroadcast", "no"} if len(parts) >= 4 else False
    if silent:
        parts = parts[:-1]
    code = parts[1].upper()
    try:
        coins    = int(parts[2])
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
                f"\u26a0\ufe0f Code `{code}` already exists!\nCoins: `{existing.get('coins', 0)}`\nUse /deletepromo {code} to remove it first.",
                parse_mode="Markdown",
            )
        promo_doc = {
            "code":       code,
            "coins":      coins,
            "max_uses":   max_uses,
            "used_by":    [],
            "active":     True,
            "created_at": datetime.utcnow().isoformat(),
            "created_by": ADMIN_ID,
        }
        promos_col.insert_one(promo_doc)
        logger.info("Admin created promo '%s' for %s coins (max_uses=%s)", code, coins, max_uses)
        uses_str = f"{max_uses}" if max_uses > 0 else "Unlimited"
        bot.reply_to(
            message,
            f"\u2705 *Promo Code Created!*\n\nCode: `{code}`\nCoins: `{coins}`\nMax Uses: `{uses_str}`\n\n"
            f"Users can redeem with: /redeem {code}\n"
            + ("\U0001f515 Silent mode \u2014 no user notification." if silent else "\U0001f4e2 Broadcasting to all users in background..."),
            parse_mode="Markdown",
        )
        if not silent:
            notice = (
                f"\U0001f389 *New Promo Code Released!*\n\n"
                f"\U0001f3ab Code: `{code}`\n"
                f"\U0001fa99 Reward: *{coins} coins*\n"
                f"\U0001f465 Max Uses: *{uses_str}*\n\n"
                f"Open the Mini App \u2192 Rewards tab \u2192 *Promo Code* section\n"
                f"and enter `{code}` to claim. Hurry, first-come first-served!"
            )
            _spawn_broadcast(notice, admin_chat_id=int(message.chat.id), summary_label=f"Promo: `{code}` ({coins} coins)")
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
            lines.append(f"\u2022 `{p['code']}` \u2014 {p['coins']} coins \u2014 Max: {uses_str}")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        logger.error("list_promos_command error: %s", exc)
        bot.reply_to(message, "\u26a0\ufe0f Server error. Please try again.")


@bot.message_handler(commands=["addpromtask"])
def add_promo_task_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split(None, 3)
    if len(parts) < 4:
        return bot.reply_to(
            message,
            "Usage: /addpromtask <task_id> <reward_coins> <title> [| silent]\n"
            "Example: /addpromtask promo_yt1 5 Watch our YouTube video",
        )
    task_id = parts[1].strip()
    try:
        reward = int(parts[2])
    except ValueError:
        return bot.reply_to(message, "Invalid reward amount. Must be an integer.")
    title_raw = parts[3].strip()
    silent    = False
    if "|" in title_raw:
        title_part, _, flag = title_raw.rpartition("|")
        if flag.strip().lower() in {"silent", "quiet", "nobroadcast", "no"}:
            silent    = True
            title_raw = title_part.strip()
    title = title_raw
    try:
        existing = promo_tasks_col.find_one({"task_id": task_id})
        if existing:
            return bot.reply_to(message, f"Task '{task_id}' already exists. Use /delpromtask first.")
        promo_tasks_col.insert_one({
            "task_id":    task_id,
            "title":      title,
            "description": "",
            "link":       "",
            "reward":     reward,
            "active":     True,
            "created_at": datetime.utcnow().isoformat(),
        })
        bot.reply_to(
            message,
            f"\u2705 Promo task '{task_id}' added with {reward} coins reward.\n"
            + ("\U0001f515 Silent mode." if silent else "\U0001f4e2 Broadcasting to all users in background..."),
        )
        if not silent:
            notice = (
                f"\U0001f4e2 *New Promotion Task Added!*\n\n"
                f"\U0001f4dd *{title}*\n"
                f"\U0001fa99 Reward: *{reward} coins* (one-time)\n\n"
                f"Open the Mini App \u2192 *Rewards* tab \u2192 *Promotion Tasks* section\n"
                f"and tap *Mark as Done & Claim* to earn!"
            )
            _spawn_broadcast(
                notice,
                admin_chat_id=int(message.chat.id),
                summary_label=f"Promo Task: `{task_id}` ({reward} coins)",
            )
    except Exception as exc:
        logger.error("add_promo_task_command error: %s", exc)
        bot.reply_to(message, "Server error. Please try again.")


@bot.message_handler(commands=["delpromtask"])
def del_promo_task_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Usage: /delpromtask <task_id>")
    task_id = parts[1].strip()
    try:
        result = promo_tasks_col.update_one({"task_id": task_id}, {"$set": {"active": False}})
        if result.matched_count:
            bot.reply_to(message, f"\u2705 Promo task '{task_id}' deactivated.")
        else:
            bot.reply_to(message, f"Task '{task_id}' not found.")
    except Exception as exc:
        logger.error("del_promo_task_command error: %s", exc)
        bot.reply_to(message, "Server error. Please try again.")


@bot.message_handler(commands=["listpromtasks"])
def list_promo_tasks_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    try:
        tasks = list(
            promo_tasks_col.find({"active": True}, {"_id": 0, "task_id": 1, "title": 1, "reward": 1})
        )
        if not tasks:
            return bot.reply_to(message, "No active promotion tasks.")
        lines = ["\U0001f4e2 *Active Promotion Tasks*\n"]
        for t in tasks:
            lines.append(f"\u2022 `{t['task_id']}` \u2014 {t['reward']} coins \u2014 {t['title']}")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        logger.error("list_promo_tasks_command error: %s", exc)
        bot.reply_to(message, "Server error. Please try again.")


@bot.message_handler(commands=["adminpanel"])
def admin_panel_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    text = (
        "\U0001f6e0 *Admin Panel \u2014 All Commands*\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4ca *Stats & Info*\n"
        "\u2022 /stats \u2014 Bot statistics\n"
        "\u2022 /balance `<user_id>` \u2014 User balance\n"
        "\u2022 /health \u2014 Server CPU/RAM/uptime status\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4b0 *Coins*\n"
        "\u2022 /addcoins `<user_id> <amount>` \u2014 Add coins\n"
        "\u2022 /penalty `<user_id> <amount>` \u2014 Deduct coins\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4b8 *Withdrawals*\n"
        "\u2022 /approve `<user_id>` \u2014 Approve withdrawal\n"
        "\u2022 /reject `<user_id>` \u2014 Reject withdrawal\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f3ab *Promo Codes*\n"
        "\u2022 /createpromo `<code> <coins> <uses>` \u2014 Create promo\n"
        "\u2022 /deletepromo `<code>` \u2014 Delete promo\n"
        "\u2022 /listpromos \u2014 List all promos\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4e2 *Promotion Tasks*\n"
        "\u2022 /addpromtask `<id> <coins> <title>` \u2014 Add task\n"
        "\u2022 /delpromtask `<id>` \u2014 Delete task\n"
        "\u2022 /listpromtasks \u2014 List all tasks\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u2705 *Task Codes*\n"
        "\u2022 /settask `<task_id> <code>` \u2014 Update task code\n"
        "  IDs: yt1 yt2 yt3 web1 web2 web3 slot3 slot4\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f3b0 *Lottery*\n"
        "\u2022 /setlottery `<price> <prize> [active|inactive]` \u2014 Configure\n"
        "\u2022 /lotterystats \u2014 Today's stats\n"
        "\u2022 /drawlottery \u2014 Manual draw (auto at 00:00 UTC)\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f3a1 *Spin Wheel & Mining*\n"
        "\u2022 /togglespin \u2014 Lock \U0001f512 / Unlock \u2705 Spin Wheel\n"
        "\u2022 /togglemining \u2014 Lock \U0001f512 / Unlock \u2705 Coin Mining\n"
        "\u2022 /featurestatus \u2014 Check spin & mining lock status\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f6ab *User Management*\n"
        "\u2022 /block `<user_id>` \u2014 Block user\n"
        "\u2022 /unblock `<user_id>` \u2014 Unblock user\n"
        "\u2022 /resetdevice `<user_id>` \u2014 Reset device & auto-unblock\n"
        "\u2022 /listbanned \u2014 List all banned users\n"
        "\u2022 /searchuser `<user_id or @username>` \u2014 Find user info\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f4e3 *Broadcast*\n"
        "\u2022 /broadcast `<message>` \u2014 Send to all users\n"
        "\u2022 /msg `<user_id> <message>` \u2014 Message a user\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f524 *Word Filter (Group)*\n"
        "\u2022 /addcodefilter `<word>` \u2014 Block a word\n"
        "\u2022 /delcodefilter `<word>` \u2014 Unblock a word\n"
        "\u2022 /listcodefilters \u2014 List blocked words\n"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["togglespin"])
def toggle_spin_command(message):
    """Admin: /togglespin — Spin Wheel ko lock ya unlock karo."""
    if int(message.from_user.id) != ADMIN_ID:
        return
    try:
        doc = config_col.find_one({"_id": "feature_config"}) or {}
        current = bool(doc.get("spin_active", True))
        new_val = not current
        config_col.update_one(
            {"_id": "feature_config"},
            {"$set": {"spin_active": new_val}},
            upsert=True,
        )
        _bust_feature_cache()
        status = "\u2705 UNLOCKED — Users can now spin!" if new_val else "\U0001f512 LOCKED — Spin Wheel hidden for all users."
        bot.reply_to(
            message,
            f"\U0001f3a1 *Spin Wheel Updated*\n\n{status}",
            parse_mode="Markdown",
        )
        logger.info("Admin toggled spin_active → %s", new_val)
    except Exception as exc:
        logger.error("togglespin error: %s", exc)
        bot.reply_to(message, "Server error. Please try again.")


@bot.message_handler(commands=["togglemining"])
def toggle_mining_command(message):
    """Admin: /togglemining — Coin Mining ko lock ya unlock karo."""
    if int(message.from_user.id) != ADMIN_ID:
        return
    try:
        doc = config_col.find_one({"_id": "feature_config"}) or {}
        current = bool(doc.get("mining_active", True))
        new_val = not current
        config_col.update_one(
            {"_id": "feature_config"},
            {"$set": {"mining_active": new_val}},
            upsert=True,
        )
        _bust_feature_cache()
        status = "\u2705 UNLOCKED — Users can now mine coins!" if new_val else "\U0001f512 LOCKED — Coin Mining hidden for all users."
        bot.reply_to(
            message,
            f"\u26cf\ufe0f *Coin Mining Updated*\n\n{status}",
            parse_mode="Markdown",
        )
        logger.info("Admin toggled mining_active → %s", new_val)
    except Exception as exc:
        logger.error("togglemining error: %s", exc)
        bot.reply_to(message, "Server error. Please try again.")


@bot.message_handler(commands=["featurestatus"])
def feature_status_command(message):
    """Admin: /featurestatus — Spin & Mining ka current lock status dekho."""
    if int(message.from_user.id) != ADMIN_ID:
        return
    try:
        cfg = get_feature_config()
        spin_s   = "\u2705 Unlocked" if cfg.get("spin_active")   else "\U0001f512 Locked"
        mining_s = "\u2705 Unlocked" if cfg.get("mining_active") else "\U0001f512 Locked"
        bot.reply_to(
            message,
            f"\U0001f3a1 *Feature Status*\n\n"
            f"\U0001f3a1 Spin Wheel:  {spin_s}\n"
            f"\u26cf\ufe0f Coin Mining: {mining_s}\n\n"
            f"Use /togglespin or /togglemining to change.",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("featurestatus error: %s", exc)
        bot.reply_to(message, "Server error. Please try again.")


@bot.message_handler(commands=["stats"])
def get_stats(message):
    if int(message.from_user.id) != ADMIN_ID:
        return

    try:
        # ── User counts ──
        total_users   = users_col.count_documents({})
        today_joined  = users_col.count_documents({"joined": str(date.today())})
        banned_users  = users_col.count_documents({"blocked": True})
        active_today  = users_col.count_documents({"last_active": str(date.today())})

        # ── Coins currently held by all users ──
        coins_agg = list(users_col.aggregate([
            {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$coins", 0]}}}}
        ]))
        coins_in_wallets = int(coins_agg[0]["total"]) if coins_agg else 0

        # ── Coins paid out via approved withdrawals ──
        wd_agg = list(withdrawals_col.aggregate([
            {"$match": {"status": "Approved \u2705"}},
            {"$group": {"_id": None, "total": {"$sum": {
                "$ifNull": ["$coins", {"$ifNull": ["$amount", 0]}]
            }}}}
        ]))
        coins_paid_out = int(wd_agg[0]["total"]) if wd_agg else 0

        # ── Total coins ever generated = held + paid out ──
        total_coins_generated = coins_in_wallets + coins_paid_out

        # ── Withdrawal counts ──
        pending_wd  = withdrawals_col.count_documents({"status": "Pending \u23f3"})
        approved_wd = withdrawals_col.count_documents({"status": "Approved \u2705"})
        rejected_wd = withdrawals_col.count_documents({"status": "Rejected \u274c"})

        # ── Top earner ──
        top = users_col.find_one(
            {"blocked": {"$ne": True}},
            {"username": 1, "first_name": 1, "coins": 1},
            sort=[("coins", -1)]
        )
        top_name  = top.get("username") or top.get("first_name") or "Unknown" if top else "—"
        top_coins = top.get("coins", 0) if top else 0

        text = (
            "\U0001f4ca *Daksh Grand Earn — Full Stats*\n"
            "\n"
            "\U0001f465 *Users*\n"
            f"  Total: `{total_users:,}`\n"
            f"  Today Joined: `{today_joined:,}`\n"
            f"  Active Today: `{active_today:,}`\n"
            f"  Banned: `{banned_users}`\n"
            "\n"
            "\U0001fa99 *Coins*\n"
            f"  Total Generated: `{total_coins_generated:,}` \U0001fa99\n"
            f"  In Wallets: `{coins_in_wallets:,}` \U0001fa99\n"
            f"  Paid Out: `{coins_paid_out:,}` \U0001fa99\n"
            "\n"
            "\U0001f4b8 *Withdrawals*\n"
            f"  Pending: `{pending_wd}`\n"
            f"  Approved: `{approved_wd}`\n"
            f"  Rejected: `{rejected_wd}`\n"
            "\n"
            "\U0001f3c6 *Top Earner*\n"
            f"  {top_name}: `{top_coins:,}` \U0001fa99"
        )

    except Exception as exc:
        logger.error("get_stats error: %s", exc)
        text = "\u26a0\ufe0f Error fetching stats. Check logs."

    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["health"])
def server_health(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    try:
        cpu_pct    = psutil.cpu_percent(interval=0.5)
        mem        = psutil.virtual_memory()
        disk       = psutil.disk_usage("/")
        uptime_sec = int(time.time() - _SERVER_START_TIME)
        h, rem     = divmod(uptime_sec, 3600)
        m, s       = divmod(rem, 60)

        def bar(pct, length=10):
            filled = round(pct / 100 * length)
            return "\u2588" * filled + "\u2591" * (length - filled)

        def status_emoji(pct):
            if pct < 60: return "\U0001f7e2"
            if pct < 85: return "\U0001f7e1"
            return "\U0001f534"

        text = (
            "\U0001f5a5\ufe0f *Server Health*\n\n"
            f"\u23f1\ufe0f *Uptime:* `{h}h {m}m {s}s`\n\n"
            f"{status_emoji(cpu_pct)} *CPU:* `{cpu_pct:.1f}%`\n`{bar(cpu_pct)}`\n\n"
            f"{status_emoji(mem.percent)} *Memory:* `{mem.percent:.1f}%` "
            f"(`{mem.used//1024//1024} MB / {mem.total//1024//1024} MB`)\n`{bar(mem.percent)}`\n\n"
            f"{status_emoji(disk.percent)} *Disk:* `{disk.percent:.1f}%` "
            f"(`{disk.used//1024//1024//1024:.1f} GB / {disk.total//1024//1024//1024:.1f} GB`)\n`{bar(disk.percent)}`\n\n"
            "\U0001f7e2 *Flask:* Running\n\U0001f7e2 *Bot:* Online"
        )
        bot.reply_to(message, text, parse_mode="Markdown")
    except Exception as exc:
        bot.reply_to(message, f"\u274c Health check failed: {exc}")


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
    withdraw = withdrawals_col.find_one_and_update(
        {"user_id": target_id, "status": "Pending \u23f3"},
        {"$set": {"status": "Rejected \u274c"}},
    )
    if withdraw:
        users_col.update_one({"user_id": target_id}, {"$inc": {"coins": withdraw["amount"]}})
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
        amount    = int(parts[2])
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
    sent = failed = 0
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
        bot.send_message(target_id, "\u26d4 Your account has been blocked for violating our terms of service.")
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


@bot.message_handler(commands=["listbanned"])
def cmd_list_banned(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    try:
        total  = users_col.count_documents({"blocked": True})
        banned = list(
            users_col.find(
                {"blocked": True},
                {"user_id": 1, "username": 1, "coins": 1, "block_reason": 1, "blocked_at": 1, "_id": 0},
            ).limit(20)
        )
        if not banned:
            return bot.reply_to(message, "\u2705 No banned users found.")

        def _safe_date(val):
            if val is None: return "Unknown"
            if hasattr(val, "strftime"): return val.strftime("%Y-%m-%d")
            return str(val)[:10]

        def _esc(text):
            return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")

        lines = [f"\U0001f6ab *Banned Users* (Total: {total}, showing up to 20)\n"]
        for u in banned:
            uid     = u.get("user_id", "?")
            uname   = f"@{_esc(u['username'])}" if u.get("username") else "\u2014"
            coins   = u.get("coins", 0)
            reason  = _esc(u.get("block_reason") or "No reason")
            blk_str = _safe_date(u.get("blocked_at"))
            lines.append(f"\u2022 `{uid}` {uname}\n  \U0001f4b0 {coins} coins | \U0001f4c5 {blk_str}\n  \u2757 {reason}")

        text = "\n".join(lines)
        if len(text) <= 4096:
            bot.reply_to(message, text, parse_mode="Markdown")
        else:
            for i in range(0, len(text), 4000):
                bot.send_message(message.chat.id, text[i:i+4000], parse_mode="Markdown")
    except Exception as exc:
        logger.error("listbanned error: %s", exc)
        bot.reply_to(message, f"\u274c Error: {exc}")


@bot.message_handler(commands=["searchuser"])
def cmd_search_user(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(
            message,
            "Usage:\n\u2022 `/searchuser 123456789` \u2014 search by Telegram ID\n"
            "\u2022 `/searchuser Daksh` \u2014 search by name",
            parse_mode="Markdown",
        )
    query_raw = parts[1].strip().lstrip("@")
    try:
        user = None
        if query_raw.lstrip("-").isdigit():
            user = users_col.find_one({"user_id": int(query_raw)})
        if not user:
            safe_q      = re.escape(query_raw)
            users_found = list(
                users_col.find(
                    {"username": {"$regex": safe_q, "$options": "i"}},
                    {"user_id": 1, "username": 1, "coins": 1, "blocked": 1,
                     "block_reason": 1, "joined": 1, "referral_count": 1, "_id": 0},
                ).limit(5)
            )
            if len(users_found) == 1:
                user = users_found[0]
            elif len(users_found) > 1:
                lines = [f"\U0001f50d *{len(users_found)} users found for* `{query_raw}`:\n"]
                for u in users_found:
                    status = "\U0001f6ab" if u.get("blocked") else "\u2705"
                    lines.append(f"{status} `{u.get('user_id', '?')}` \u2014 {u.get('username') or '\u2014'} | \U0001f4b0{u.get('coins', 0)}")
                lines.append("\n_Use /searchuser <ID> to get full details._")
                return bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

        if not user:
            return bot.reply_to(message, f"\u274c No user found for `{query_raw}`", parse_mode="Markdown")

        def _safe_date(val):
            if val is None: return "Unknown"
            if hasattr(val, "strftime"): return val.strftime("%Y-%m-%d")
            return str(val)[:10]

        uid      = user.get("user_id", "?")
        uname    = user.get("username") or "\u2014"
        coins    = user.get("coins", 0)
        blocked  = "\U0001f6ab Banned" if user.get("blocked") else "\u2705 Active"
        reason   = user.get("block_reason") or "\u2014"
        joined_s = _safe_date(user.get("joined"))
        refs     = user.get("referral_count", 0)
        text     = (
            f"\U0001f464 *User Info*\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"\U0001f194 ID: `{uid}`\n"
            f"\U0001f4db Name: {uname}\n"
            f"\U0001f4c5 Joined: {joined_s}\n"
            f"\U0001f4b0 Coins: {coins}\n"
            f"\U0001f465 Referrals: {refs}\n"
            f"\U0001f512 Status: {blocked}\n"
            f"\u2757 Ban Reason: {reason}"
        )
        bot.reply_to(message, text, parse_mode="Markdown")
    except Exception as exc:
        logger.error("searchuser error: %s", exc)
        bot.reply_to(message, f"\u274c Error: {exc}")


# ============================================================
# TASK DISPLAY NAMES + BROADCAST HELPERS
# ============================================================

TASK_DISPLAY_NAMES = {
    "yt1":   "\U0001f4fa YouTube Task 1",
    "yt2":   "\U0001f4fa YouTube Task 2",
    "yt3":   "\U0001f4fa YouTube Task 3",
    "web1":  "\U0001f310 Website Task 1",
    "web2":  "\U0001f310 Website Task 2",
    "web3":  "\U0001f310 Website Task 3",
    "slot3": "\U0001f31f Sponsor Slot 3 (Code Verification)",
    "slot4": "\U0001f310 Sponsor Slot 4 (Website Verify)",
}


def _broadcast_to_all_users(notice: str, admin_chat_id: int, summary_label: str) -> None:
    sent = failed = 0
    try:
        cursor = users_col.find(
            {"$or": [{"blocked": {"$exists": False}}, {"blocked": False}]},
            {"user_id": 1, "_id": 0},
        )
        for u in cursor:
            uid = u.get("user_id")
            if not uid:
                continue
            try:
                bot.send_message(int(uid), notice, parse_mode="Markdown", disable_web_page_preview=True)
                sent += 1
                time.sleep(0.05)
            except Exception:
                failed += 1
    except Exception as exc:
        logger.error("Broadcast cursor error (%s): %s", summary_label, exc)
    logger.info("Broadcast '%s' done: sent=%d, failed=%d", summary_label, sent, failed)
    try:
        bot.send_message(
            admin_chat_id,
            f"\U0001f4e2 *Broadcast Complete*\n\n{summary_label}\n\u2705 Sent: *{sent}*\n\u274c Failed: *{failed}*",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.warning("Admin broadcast summary failed: %s", exc)


def _spawn_broadcast(notice: str, admin_chat_id: int, summary_label: str) -> None:
    threading.Thread(
        target=_broadcast_to_all_users,
        args=(notice, admin_chat_id, summary_label),
        daemon=True,
        name=f"broadcast-{summary_label[:30]}",
    ).start()


def _broadcast_task_update(task_id: str, new_code: str, admin_chat_id: int) -> None:
    display     = TASK_DISPLAY_NAMES.get(task_id, task_id.upper())
    is_one_time = task_id in ONE_TIME_TASK_IDS
    if is_one_time:
        notice = (
            f"\U0001f389 *New Task Available!*\n\n"
            f"{display} has been refreshed with a new code.\n"
            f"Open the Mini App and complete it to earn coins! \U0001fa99"
        )
    else:
        notice = (
            f"\U0001f504 *Task Code Updated!*\n\n"
            f"{display} has a fresh code today.\n"
            f"Open the Mini App, watch/visit the link, and enter the new code to earn! \U0001fa99"
        )
    _broadcast_to_all_users(
        notice, admin_chat_id, summary_label=f"Task: `{task_id}` \u2192 `{new_code}`"
    )


@bot.message_handler(commands=["settask"])
def set_task_code(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts    = message.text.split()
    if len(parts) < 3:
        return bot.reply_to(message, "Usage: /settask <task_id> <new_code> [silent]")
    task_id  = parts[1].lower()
    new_code = parts[2].upper()
    silent   = len(parts) >= 4 and parts[3].lower() in {"silent", "quiet", "nobroadcast", "no"}

    if task_id not in TASK_CODES:
        return bot.reply_to(message, f"Invalid task ID. Valid: {', '.join(TASK_CODES.keys())}")

    TASK_CODES[task_id] = new_code
    global _task_codes_cache, _task_codes_cache_time
    _task_codes_cache      = None
    _task_codes_cache_time = 0.0

    if silent:
        bot.reply_to(
            message,
            f"\u2705 Task `{task_id}` code updated to `{new_code}`!\n\U0001f515 Silent mode \u2014 no user notification sent.",
            parse_mode="Markdown",
        )
        return

    bot.reply_to(
        message,
        f"\u2705 Task `{task_id}` code updated to `{new_code}`!\n\U0001f4e2 Broadcasting to all users in background...",
        parse_mode="Markdown",
    )
    threading.Thread(
        target=_broadcast_task_update,
        args=(task_id, new_code, int(message.chat.id)),
        daemon=True,
        name=f"task-broadcast-{task_id}",
    ).start()


@bot.message_handler(commands=["setlottery"])
def set_lottery_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 3:
        cur = get_lottery_config()
        return bot.reply_to(
            message,
            "Usage: `/setlottery <ticket_price> <prize> [active|inactive]`\n\n"
            f"Current \u2192 ticket: `{cur['ticket_price']}` \U0001fa99, prize: `{cur['prize']}` \U0001fa99, "
            f"status: `{'ACTIVE' if cur['active'] else 'DISABLED'}`",
            parse_mode="Markdown",
        )
    try:
        new_price = int(parts[1])
        new_prize = int(parts[2])
        if new_price <= 0 or new_prize <= 0:
            return bot.reply_to(message, "Ticket price and prize must be positive.")
    except ValueError:
        return bot.reply_to(message, "Invalid numbers. Usage: /setlottery <ticket_price> <prize> [active|inactive]")
    active_flag = True
    if len(parts) >= 4 and parts[3].lower() in {"inactive", "off", "disable", "stop"}:
        active_flag = False
    config_col.update_one(
        {"_id": "lottery_config"},
        {"$set": {"ticket_price": new_price, "prize": new_prize, "active": active_flag}},
        upsert=True,
    )
    _bust_lottery_cache()
    bot.reply_to(
        message,
        f"\U0001f3b0 *Lottery Updated!*\n\n"
        f"\U0001f3ab Ticket Price: `{new_price}` \U0001fa99\n"
        f"\U0001f3c6 Prize: `{new_prize}` \U0001fa99\n"
        f"\U0001f4ca Status: `{'ACTIVE \u2705' if active_flag else 'DISABLED \u274c'}`\n\n"
        f"_Note: Today's existing round keeps its locked-in values. New settings apply from next round (00:00 UTC)._",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["lotterystats"])
def lottery_stats_command(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    cfg          = get_lottery_config()
    rid          = _today_round_id()
    rdoc         = lottery_col.find_one({"_id": rid}) or {}
    participants = rdoc.get("participants", [])
    ticket_price = rdoc.get("ticket_price", cfg["ticket_price"])
    prize        = rdoc.get("prize", cfg["prize"])
    pool         = len(participants) * ticket_price
    profit       = pool - prize if participants else 0
    last         = lottery_col.find_one({"drawn": True, "winner": {"$ne": None}}, sort=[("drawn_at", -1)]) or {}
    last_line    = ""
    if last:
        last_line = (
            f"\n\n*Last Round:* `{last.get('_id', '?')}`\n"
            f"\U0001f3c6 Winner: `{last.get('winner', '?')}` won `{last.get('prize', 0)}` \U0001fa99"
        )
    bot.reply_to(
        message,
        f"\U0001f3b0 *Lottery \u2014 Today*\n\n"
        f"Round: `{rid}`\n"
        f"\U0001f4b0 Status: `{'ACTIVE' if cfg['active'] else 'DISABLED'}`\n"
        f"\U0001f3ab Ticket Price: `{ticket_price}` \U0001fa99\n"
        f"\U0001f3c6 Prize: `{prize}` \U0001fa99\n"
        f"\U0001f465 Tickets Sold: `{len(participants)}`\n"
        f"\U0001f4b5 Pool Collected: `{pool}` \U0001fa99\n"
        f"\U0001f4c8 Net Burn (pool - prize): `{profit}` \U0001fa99\n"
        f"\U0001f3b2 Drawn: `{'YES \u2705' if rdoc.get('drawn') else 'NO \u23f3'}`"
        f"{last_line}",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["drawlottery"])
def draw_lottery_command(message):
    """Admin: manually pick a winner. Uses shared _perform_auto_draw()."""
    if int(message.from_user.id) != ADMIN_ID:
        return
    rid    = _today_round_id()
    result = _perform_auto_draw(rid, notify_chat_id=int(message.chat.id))
    if not result["success"]:
        bot.reply_to(message, f"\u26a0\ufe0f {result['message']}")
        return
    bot.reply_to(
        message,
        f"\U0001f389 *Lottery Drawn!*\n\n"
        f"Round: `{rid}`\n"
        f"\U0001f3c6 Winner: `{result['winner_id']}`\n"
        f"\U0001f4b0 Prize: `{result['prize']}` \U0001fa99\n"
        f"\U0001f465 Total Tickets: `{result['participants']}`\n\n"
        f"_Notifying all participants in background..._",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["penalty"])
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
    current  = user.get("coins", 0)
    new_bal  = max(0, current - amount)
    deducted = current - new_bal
    users_col.update_one({"user_id": target_id}, {"$set": {"coins": new_bal}})
    try:
        bot.send_message(
            target_id,
            f"\u26a0\ufe0f *Penalty Applied!*\n\n"
            f"`{deducted}` coins deducted.\n"
            f"New Balance: `{new_bal}` \U0001fa99\n\n"
            f"Reason: Rule violation.",
            parse_mode="Markdown",
        )
    except Exception as notify_exc:
        logger.warning("Notify failed for penalty %s: %s", target_id, notify_exc)
    bot.reply_to(
        message,
        f"\u26a0\ufe0f Penalty applied: {deducted} coins deducted from user {target_id}. New balance: {new_bal}.",
    )


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
            {"$set": {
                "pattern":    pattern,
                "label":      code,
                "active":     True,
                "created_at": datetime.utcnow().isoformat(),
                "created_by": ADMIN_ID,
            }},
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
            {"$set": {
                "pattern":    pattern,
                "label":      pattern,
                "active":     True,
                "created_at": datetime.utcnow().isoformat(),
                "created_by": ADMIN_ID,
            }},
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
        rules = list(
            code_filter_rules_col.find({"active": True}, {"_id": 0, "label": 1, "pattern": 1}).limit(50)
        )
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
    code          = parts[1].strip()
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
    text            = message.text or ""
    matched_pattern = message_matches_group_code_filter(text)
    if not matched_pattern:
        return
    chat_id             = message.chat.id
    user_id             = message.from_user.id
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
            logger.warning(
                "User %s exceeded code violations in %s but is group admin/creator.", user_id, chat_id
            )
            return
    except Exception as exc:
        logger.warning(
            "Unable to verify user role before ban for %s in %s: %s", user_id, chat_id, exc
        )
    if not can_ban:
        logger.warning(
            "User %s exceeded code violations in %s but bot cannot ban users.", user_id, chat_id
        )
        return
    try:
        bot.ban_chat_member(chat_id, user_id)
        logger.info("Banned user %s from group %s after %s code violations.", user_id, chat_id, violations)
    except Exception as exc:
        logger.error("Failed to ban user %s from group %s: %s", user_id, chat_id, exc)


# ============================================================
# BOT POLLING THREAD
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
    now        = datetime.utcnow()
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
            {"$set": {"instance_id": instance_id, "expires_at": expires_at, "updated_at": now.isoformat()}},
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
                {"$set": {"expires_at": now + timedelta(seconds=lease_seconds), "updated_at": now.isoformat()}},
            )
        except Exception as exc:
            logger.warning("Bot polling lock refresh failed: %s", exc)


def release_bot_polling_lock(instance_id: str) -> None:
    try:
        config_col.delete_one({"_id": "bot_polling_lock", "instance_id": instance_id})
    except Exception as exc:
        logger.warning("Bot polling lock release failed: %s", exc)


# ============================================================
# UPTIME PING
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
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    Thread(target=run_bot,                    daemon=True).start()
    Thread(target=uptime_ping,                daemon=True).start()
    Thread(target=refresh_leaderboard_loop,   daemon=True).start()
    Thread(target=_cleanup_rate_cache,        daemon=True).start()
    Thread(target=auto_lottery_draw_loop,     daemon=True).start()
    Thread(target=_cleanup_security_caches,   daemon=True).start()

    port = int(os.getenv("PORT", 5000))
    logger.info("Starting Flask on port %s...", port)
    app.run(host="0.0.0.0", port=port)
