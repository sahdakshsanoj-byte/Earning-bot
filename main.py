import telebot
import os
import re
import pymongo
import time
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from threading import Thread
from telebot import types
from datetime import datetime, date, timedelta

# ============================================================
# 1. ENVIRONMENT VARIABLES
# ============================================================
BOT_TOKEN    = os.getenv("BOT_TOKEN")
MONGO_URI    = os.getenv("MONGO_URI")
ADMIN_ID_STR = os.getenv("ADMIN_ID")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")
RENDER_URL   = os.getenv("RENDER_URL", "")

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
# 2. DATABASE CONNECTION
# ============================================================
client          = pymongo.MongoClient(MONGO_URI, maxPoolSize=50, serverSelectionTimeoutMS=5000)
db              = client['earning_bot_db']
users_col       = db['users']
withdrawals_col = db['withdrawals']
support_col     = db['support']
rate_col        = db['rate_limits']

try:
    rate_col.create_index("expires_at", expireAfterSeconds=0)
except Exception:
    pass

# ============================================================
# 3. CONSTANTS
# ============================================================

TASK_CODES = {
    "yt1":      "CODE1",
    "yt2":      "CODE2",
    "yt3":      "CODE3",
    "web1":     "SITE1",
    "web2":     "SITE2",
    "web3":     "SITE3",
    "partner1": "PARTNER1",   # Partnership slot task code
}

TASK_REWARDS = {
    "yt1":      20,
    "yt2":      20,
    "yt3":      20,
    "web1":     15,
    "web2":     15,
    "web3":     15,
    "partner1": 15,           # Partnership slot — code verification reward
}

CHANNEL_REWARDS = {
    "official": 30,
    "channel2": 20,
    "channel3": 20,
    "sponsor1": 10,    # Sponsor Slot 1 — one-time claim, no code needed
}

MAX_ADS_PER_DAY     = 5
AD_COIN_REWARD      = 10
MIN_WITHDRAW        = 4000
MAX_WITHDRAW        = 100000
WITHDRAW_COOLDOWN   = 10800   # 3 hours in seconds
SUPPORT_MAX_MSGS    = 2
SUPPORT_WINDOW_HRS  = 6

# ============================================================
# 4. MONGODB RATE LIMITING (restart-safe)
# ============================================================

def is_rate_limited(key, cooldown_seconds):
    now = datetime.utcnow()
    doc = rate_col.find_one({"_id": key})
    if doc and doc.get("expires_at") > now:
        return True
    rate_col.update_one(
        {"_id": key},
        {"$set": {"expires_at": now + timedelta(seconds=cooldown_seconds)}},
        upsert=True
    )
    return False


def check_support_limit(user_id):
    """Returns (allowed: bool, message: str). Tracks 2 messages per 6 hours."""
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
        return False, f"Message limit reached ({SUPPORT_MAX_MSGS}/{SUPPORT_MAX_MSGS}). Please try again in {h}h {m}m."

    return True, ""

# ============================================================
# 5. FLASK + BOT SETUP
# ============================================================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
CORS(app, origins="*")

# ============================================================
# 6. FLASK API ROUTES
# ============================================================

@app.route('/')
def home():
    return jsonify({"status": "Bot is Running Live!"})


@app.route('/get_user/<int:user_id>')
def get_user_data_api(user_id):
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
    except Exception:
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


@app.route('/claim_daily/<int:user_id>', methods=['POST'])
def claim_daily_api(user_id):
    if is_rate_limited(f"claim_{user_id}", 60):
        return jsonify({"status": "error", "message": "Please wait a moment before trying again."}), 429
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404

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
        return jsonify({"status": "success", "message": "10 coins credited to your account!", "bonus": 10})
    except Exception:
        return jsonify({"status": "error", "message": "Server error. Please try again."}), 500


@app.route('/withdraw', methods=['POST'])
def withdraw_api():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    user_id          = data.get('user_id')
    upi_id           = data.get('upi_id', '').strip()
    requested_amount = data.get('amount')

    if not user_id or not upi_id or requested_amount is None:
        return jsonify({"status": "error", "message": "Missing required fields."}), 400

    try:
        user_id          = int(user_id)
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

    # 3 hour withdrawal cooldown
    if is_rate_limited(f"withdraw_{user_id}", WITHDRAW_COOLDOWN):
        return jsonify({"status": "error", "message": "You can only submit one withdrawal request every 3 hours."}), 429

    ref_count = users_col.count_documents({"referred_by": str(user_id)})
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
        "date":    datetime.now().strftime("%d %b %Y, %I:%M %p")
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
    except Exception:
        pass
    return jsonify({"status": "success", "message": "Withdrawal request submitted successfully!"})


@app.route('/get_history/<int:user_id>')
def get_history_api(user_id):
    try:
        history = list(withdrawals_col.find({"user_id": user_id}, {"_id": 0}).sort("date", -1).limit(10))
        return jsonify({"status": "success", "history": history})
    except Exception:
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route('/verify_task', methods=['POST'])
def verify_task_api():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    task_id   = data.get('task_id', '').strip().lower()
    user_code = data.get('code', '').strip().upper()

    if not task_id or not user_code:
        return jsonify({"status": "error", "message": "Missing task ID or code."}), 400

    if is_rate_limited(f"task_{user_id}_{task_id}", 10):
        return jsonify({"status": "error", "message": "Please wait 10 seconds before trying again."}), 429

    reward = TASK_REWARDS.get(task_id)
    if reward is None:
        return jsonify({"status": "error", "message": "Invalid task ID."}), 400

    correct_code = TASK_CODES.get(task_id, "").upper()
    if user_code != correct_code:
        return jsonify({"status": "error", "message": "Incorrect code! Please try again."}), 400

    try:
        user  = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404

        today            = str(date.today())
        task_completions = user.get('task_completions', {})
        existing         = task_completions.get(task_id, {})

        if (isinstance(existing, dict) and
                existing.get('date') == today and
                existing.get('code') == correct_code):
            return jsonify({"status": "error", "message": "Task already completed today! Come back tomorrow."}), 400

        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": reward},
                "$set": {f"task_completions.{task_id}": {"date": today, "code": correct_code}}
            }
        )
        return jsonify({"status": "success", "message": f"{reward} coins added to your balance!", "reward": reward})
    except Exception:
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route('/watch_ad/<int:user_id>', methods=['POST'])
def watch_ad_api(user_id):
    if is_rate_limited(f"ad_{user_id}", 30):
        return jsonify({"status": "error", "message": "Please wait 30 seconds before the next ad."}), 429
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404

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
            "status":    "success",
            "message":   f"{AD_COIN_REWARD} coins earned! ({done}/{MAX_ADS_PER_DAY} ads today)",
            "ads_done":  done,
            "ads_total": MAX_ADS_PER_DAY,
            "remaining": remaining
        })
    except Exception:
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route('/claim_channel', methods=['POST'])
def claim_channel_api():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user ID."}), 400

    channel_id = data.get('channel_id', '').strip().lower()
    if channel_id not in CHANNEL_REWARDS:
        return jsonify({"status": "error", "message": "Invalid channel."}), 400

    if is_rate_limited(f"channel_{user_id}_{channel_id}", 10):
        return jsonify({"status": "error", "message": "Please wait a moment."}), 429

    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found."}), 404

        channel_claims = user.get('channel_claims', {})
        if channel_claims.get(channel_id):
            return jsonify({"status": "error", "message": "Reward already claimed for this channel! \u2705"}), 400

        reward = CHANNEL_REWARDS[channel_id]
        users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"coins": reward}, "$set": {f"channel_claims.{channel_id}": True}}
        )
        return jsonify({"status": "success", "message": f"{reward} coins credited for joining the channel!", "reward": reward})
    except Exception:
        return jsonify({"status": "error", "message": "Server error."}), 500


@app.route('/check_device', methods=['POST'])
def check_device_api():
    data = request.get_json()
    if not data:
        return jsonify({"status": "ok"})
    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"status": "ok"})

    ip          = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip:
        ip = ip.split(',')[0].strip()
    fingerprint = data.get('fingerprint', '')

    try:
        current_user = users_col.find_one({"user_id": user_id})
        if not current_user:
            return jsonify({"status": "ok"})
        if current_user.get('blocked'):
            return jsonify({"status": "blocked"})

        ip_conflict = users_col.find_one({"ip": ip, "user_id": {"$ne": user_id}}) if ip else None
        fp_conflict = users_col.find_one({"fingerprint": fingerprint, "user_id": {"$ne": user_id}}) if fingerprint else None

        if ip_conflict:
            users_col.update_one({"user_id": user_id}, {"$set": {"blocked": True}})
            try:
                bot.send_message(ADMIN_ID, f"\U0001f6a8 *Multi-Account Detected (IP)*\nUser `{user_id}` blocked.\nIP: `{ip}`", parse_mode="Markdown")
            except Exception:
                pass
            return jsonify({"status": "blocked"})

        if fp_conflict:
            users_col.update_one({"user_id": user_id}, {"$set": {"fp_flagged": True}})

        users_col.update_one({"user_id": user_id}, {"$set": {"fingerprint": fingerprint, "ip": ip}}, upsert=False)
        return jsonify({"status": "ok"})
    except Exception:
        return jsonify({"status": "ok"})


@app.route('/send_support', methods=['POST'])
def send_support_api():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400

    user_id_raw  = data.get('user_id')
    message_text = data.get('message', '').strip()

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

    # 6-hour / 2-message limit
    allowed, limit_msg = check_support_limit(user_id)
    if not allowed:
        return jsonify({"status": "error", "message": limit_msg}), 429

    try:
        support_col.insert_one({"user_id": user_id, "message": message_text, "date": str(datetime.now())})
        # Increment support count
        users_col.update_one({"user_id": user_id}, {"$inc": {"support_count": 1}})
        bot.send_message(ADMIN_ID, f"\U0001f3a7 *Support Message*\nFrom User: `{user_id}`\n\n{message_text}", parse_mode="Markdown")
        return jsonify({"status": "success", "message": "Your message has been sent to Admin!"})
    except Exception:
        return jsonify({"status": "error", "message": "Failed to send message. Please try again."}), 500


# ============================================================
# 7. HELPER FUNCTIONS
# ============================================================

def get_leaderboard():
    try:
        top_users = list(users_col.find({}, {"user_id": 1, "coins": 1, "_id": 0}).sort("coins", -1).limit(10))
        data = [f"{u['user_id']}:{u.get('coins', 0)}" for u in top_users]
        return "|".join(data) if data else "none"
    except Exception:
        return "none"


def get_referral_list(user_id):
    try:
        refs = list(users_col.find({"referred_by": str(user_id)}, {"user_id": 1, "_id": 0}))
        return ",".join(str(r['user_id']) for r in refs) if refs else ""
    except Exception:
        return ""


def get_or_create_user(user_id, username, referrer_id=None):
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
                "joined":               str(date.today())
            }
            if referrer_id and str(referrer_id) != str(user_id):
                referrer = users_col.find_one({"user_id": int(referrer_id)})
                if referrer:
                    users_col.update_one({"user_id": int(referrer_id)}, {"$inc": {"coins": 50}})
                    new_user["referred_by"] = str(referrer_id)
                    try:
                        bot.send_message(int(referrer_id), "\U0001f38a *Referral Bonus!*\n\nYou earned 50 coins for inviting a friend!", parse_mode="Markdown")
                    except Exception:
                        pass
            users_col.insert_one(new_user)
            return new_user
        return user
    except Exception:
        return {}


# ============================================================
# 8. BOT COMMANDS
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
    markup.add(types.InlineKeyboardButton("\U0001f465 Invite Friends",
        url=f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start={user_id}&text=Join+and+earn+free+coins!"))

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
    bot.reply_to(message,
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
    target_id = int(parts[1])
    result = withdrawals_col.update_one(
        {"user_id": target_id, "status": "Pending \u23f3"},
        {"$set": {"status": "Approved \u2705"}}
    )
    if result.modified_count:
        try:
            bot.send_message(target_id, "\U0001f389 *Your withdrawal has been approved!* Payment is being processed. \u2705", parse_mode="Markdown")
        except Exception:
            pass
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
    target_id = int(parts[1])
    withdraw  = withdrawals_col.find_one({"user_id": target_id, "status": "Pending \u23f3"})
    if withdraw:
        users_col.update_one({"user_id": target_id}, {"$inc": {"coins": withdraw['amount']}})
        withdrawals_col.update_one(
            {"user_id": target_id, "status": "Pending \u23f3"},
            {"$set": {"status": "Rejected \u274c"}}
        )
        try:
            bot.send_message(target_id, f"\u274c Your withdrawal was rejected. {withdraw['amount']} coins have been refunded.", parse_mode="Markdown")
        except Exception:
            pass
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
    target_id = int(parts[1])
    amount    = int(parts[2])
    users_col.update_one({"user_id": target_id}, {"$inc": {"coins": amount}})
    try:
        bot.send_message(target_id, f"\U0001f381 Admin has gifted you *{amount} coins*!", parse_mode="Markdown")
    except Exception:
        pass
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
    target_id = int(parts[1])
    users_col.update_one({"user_id": target_id}, {"$set": {"blocked": False, "fp_flagged": False}})
    try:
        bot.send_message(target_id, "\u2705 Your account has been unblocked!", parse_mode="Markdown")
    except Exception:
        pass
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
    bot.reply_to(message, f"\u2705 Task `{task_id}` code updated to `{new_code}` — Task reset for all users!", parse_mode="Markdown")


@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    import json
    try:
        data    = json.loads(message.web_app_data.data)
        user_id = message.from_user.id
        action  = data.get('type')
        if action == 'claim_bonus':
            now  = datetime.utcnow()
            user = users_col.find_one({"user_id": user_id})
            if not user:
                return
            last_ts = user.get('last_claim_ts', "")
            if last_ts:
                try:
                    last_dt = datetime.fromisoformat(last_ts)
                    if now - last_dt < timedelta(hours=24):
                        bot.send_message(user_id, "\u274c Daily bonus already claimed! Please come back tomorrow.")
                        return
                except ValueError:
                    pass
            users_col.update_one({"user_id": user_id}, {"$inc": {"coins": 10}, "$set": {"last_claim_ts": now.isoformat()}})
            bot.send_message(user_id, "\U0001f381 *10 coins have been credited!* Come back tomorrow for more.", parse_mode="Markdown")
    except Exception:
        pass


# ============================================================
# 9. KEEP-ALIVE SELF-PING
# ============================================================

def keep_alive():
    if not RENDER_URL:
        return
    while True:
        time.sleep(300)
        try:
            requests.get(RENDER_URL, timeout=10)
        except Exception:
            pass


# ============================================================
# 10. THREADING
# ============================================================

def run_flask():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception:
        pass

    Thread(target=run_flask,  daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()

    bot.infinity_polling(
        timeout=20,
        long_polling_timeout=10,
        skip_pending=True,
        allowed_updates=['message', 'web_app_data']
    )
