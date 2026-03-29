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
# 1. ENVIRONMENT VARIABLES — Hard fail if missing
# ============================================================
BOT_TOKEN    = os.getenv("BOT_TOKEN")
MONGO_URI    = os.getenv("MONGO_URI")
ADMIN_ID_STR = os.getenv("ADMIN_ID")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")
RENDER_URL   = os.getenv("RENDER_URL", "")

if not BOT_TOKEN:
    raise EnvironmentError("FATAL: BOT_TOKEN environment variable set nahi hai!")
if not MONGO_URI:
    raise EnvironmentError("FATAL: MONGO_URI environment variable set nahi hai!")
if not ADMIN_ID_STR:
    raise EnvironmentError("FATAL: ADMIN_ID environment variable set nahi hai!")
try:
    ADMIN_ID = int(ADMIN_ID_STR)
except ValueError:
    raise EnvironmentError(f"FATAL: ADMIN_ID '{ADMIN_ID_STR}' valid integer nahi hai!")

# ============================================================
# 2. DATABASE CONNECTION
# ============================================================
client        = pymongo.MongoClient(MONGO_URI, maxPoolSize=50, serverSelectionTimeoutMS=5000)
db            = client['earning_bot_db']
users_col     = db['users']
withdrawals_col = db['withdrawals']
support_col   = db['support']
rate_col      = db['rate_limits']   # MongoDB-backed rate limiting

# TTL index: rate limit documents auto-delete after their cooldown expires
try:
    rate_col.create_index("expires_at", expireAfterSeconds=0)
except Exception:
    pass

# ============================================================
# 3. CONSTANTS
# ============================================================

# Task verification codes (admin /settask se change kar sakta hai)
TASK_CODES = {
    "yt1":  "CODE1",
    "yt2":  "CODE2",
    "yt3":  "CODE3",
    "web1": "SITE1",
    "web2": "SITE2",
    "web3": "SITE3",
}

# Backend-controlled rewards — frontend se reward kabhi accept nahi hoga
TASK_REWARDS = {
    "yt1":  20,
    "yt2":  20,
    "yt3":  20,
    "web1": 15,
    "web2": 15,
    "web3": 15,
}

# Channel one-time rewards
CHANNEL_REWARDS = {
    "official": 30,
    "channel2": 20,
    "channel3": 20,
}

MAX_ADS_PER_DAY = 5
AD_COIN_REWARD  = 10   # 10 coins per ad
MIN_WITHDRAW    = 4000
MAX_WITHDRAW    = 100000

# ============================================================
# 4. MONGODB RATE LIMITING (restart-safe)
# ============================================================

def is_rate_limited(key, cooldown_seconds):
    """MongoDB mein rate limit check karo — restart se nahi jaata."""
    now = datetime.utcnow()
    doc = rate_col.find_one({"_id": key})
    if doc and doc.get("expires_at") > now:
        return True
    # Set/refresh the rate limit entry
    rate_col.update_one(
        {"_id": key},
        {"$set": {"expires_at": now + timedelta(seconds=cooldown_seconds)}},
        upsert=True
    )
    return False

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
            return jsonify({"status": "error", "message": "User not found"}), 404

        today = str(date.today())
        ads_date  = user.get('ads_date', '')
        ads_today = user.get('ads_today', 0) if ads_date == today else 0

        # Build completed task status for frontend
        task_completions = user.get('task_completions', {})
        completed_today = []
        for tid, info in task_completions.items():
            if isinstance(info, dict):
                if info.get('date') == today and info.get('code') == TASK_CODES.get(tid, ''):
                    completed_today.append(tid)
            else:
                # Legacy format support
                if info == today:
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
        return jsonify({"status": "error", "message": "Server error"}), 500


# FIX: Daily Claim — proper 24h timestamp cooldown
@app.route('/claim_daily/<int:user_id>', methods=['POST'])
def claim_daily_api(user_id):
    if is_rate_limited(f"claim_{user_id}", 60):
        return jsonify({"status": "error", "message": "Ek minute ruko!"}), 429
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found"}), 404

        now = datetime.utcnow()
        last_ts = user.get('last_claim_ts', "")
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                if now - last_dt < timedelta(hours=24):
                    remaining = timedelta(hours=24) - (now - last_dt)
                    h = int(remaining.total_seconds() // 3600)
                    m = int((remaining.total_seconds() % 3600) // 60)
                    return jsonify({"status": "error", "message": f"Already claimed! {h}h {m}m baad wapas aao."}), 400
            except ValueError:
                pass

        users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"coins": 10}, "$set": {"last_claim_ts": now.isoformat()}}
        )
        return jsonify({"status": "success", "message": "10 coins credited!", "bonus": 10})
    except Exception:
        return jsonify({"status": "error", "message": "Server error"}), 500


# Withdraw — atomic + strong validation
@app.route('/withdraw', methods=['POST'])
def withdraw_api():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    user_id          = data.get('user_id')
    upi_id           = data.get('upi_id', '').strip()
    requested_amount = data.get('amount')

    if not user_id or not upi_id or requested_amount is None:
        return jsonify({"status": "error", "message": "Missing data"}), 400

    try:
        user_id          = int(user_id)
        requested_amount = int(requested_amount)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "\u274c Invalid user_id or amount"}), 400

    if requested_amount <= 0:
        return jsonify({"status": "error", "message": "\u274c Amount zero ya negative nahi ho sakta"}), 400
    if requested_amount < MIN_WITHDRAW:
        return jsonify({"status": "error", "message": f"\u274c Minimum withdrawal {MIN_WITHDRAW} coins hai"}), 400
    if requested_amount > MAX_WITHDRAW:
        return jsonify({"status": "error", "message": "\u274c Amount bahut zyada hai"}), 400

    upi_pattern = re.compile(r'^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$')
    if not upi_pattern.match(upi_id):
        return jsonify({"status": "error", "message": "\u274c Invalid UPI ID (example: name@upi)"}), 400

    if is_rate_limited(f"withdraw_{user_id}", 300):
        return jsonify({"status": "error", "message": "\u274c 5 minute baad try karo"}), 429

    ref_count = users_col.count_documents({"referred_by": str(user_id)})
    if ref_count < 5:
        return jsonify({"status": "error", "message": f"\u274c {5 - ref_count} aur referrals chahiye!"}), 400

    # Atomic deduction
    result = users_col.find_one_and_update(
        {"user_id": user_id, "coins": {"$gte": requested_amount}, "blocked": {"$ne": True}},
        {"$inc": {"coins": -requested_amount}},
        return_document=True
    )
    if result is None:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found"}), 404
        if user.get('blocked'):
            return jsonify({"status": "error", "message": "\u274c Account blocked hai"}), 403
        return jsonify({"status": "error", "message": f"\u274c Insufficient balance. Paas mein {user.get('coins', 0)} coins hain."}), 400

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
            f"Remaining: `{result.get('coins', 0)}` coins\n"
            f"Date: {withdrawal['date']}",
            parse_mode="Markdown"
        )
    except Exception:
        pass
    return jsonify({"status": "success", "message": "Withdrawal request submitted!"})


@app.route('/get_history/<int:user_id>')
def get_history_api(user_id):
    try:
        history = list(withdrawals_col.find({"user_id": user_id}, {"_id": 0}).sort("date", -1).limit(10))
        return jsonify({"status": "success", "history": history})
    except Exception:
        return jsonify({"status": "error", "message": "Server error"}), 500


# FIX: verify_task — daily reset + code-change reset
@app.route('/verify_task', methods=['POST'])
def verify_task_api():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400

    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user_id"}), 400

    task_id   = data.get('task_id', '').strip().lower()
    user_code = data.get('code', '').strip().upper()

    if not task_id or not user_code:
        return jsonify({"status": "error", "message": "Missing data"}), 400

    if is_rate_limited(f"task_{user_id}_{task_id}", 10):
        return jsonify({"status": "error", "message": "10 second baad try karo"}), 429

    reward = TASK_REWARDS.get(task_id)
    if reward is None:
        return jsonify({"status": "error", "message": "Invalid task ID"}), 400

    correct_code = TASK_CODES.get(task_id, "").upper()
    if user_code != correct_code:
        return jsonify({"status": "error", "message": "Wrong code! Try again."}), 400

    try:
        user  = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found"}), 404

        today = str(date.today())
        task_completions = user.get('task_completions', {})
        existing = task_completions.get(task_id, {})

        # Already done today with same code? Block.
        if (isinstance(existing, dict) and
                existing.get('date') == today and
                existing.get('code') == correct_code):
            return jsonify({"status": "error", "message": "Task aaj already complete! Kal wapas aao."}), 400

        # Mark done with today's date + current code version
        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": reward},
                "$set": {f"task_completions.{task_id}": {"date": today, "code": correct_code}}
            }
        )
        return jsonify({"status": "success", "message": f"{reward} coins added!", "reward": reward})
    except Exception:
        return jsonify({"status": "error", "message": "Server error"}), 500


# FIX: watch_ad — 10 coins per ad, 0/5 daily counter
@app.route('/watch_ad/<int:user_id>', methods=['POST'])
def watch_ad_api(user_id):
    if is_rate_limited(f"ad_{user_id}", 30):
        return jsonify({"status": "error", "message": "30 second ruko next ad ke liye!"}), 429
    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found"}), 404

        today     = str(date.today())
        ads_date  = user.get('ads_date', '')
        ads_today = user.get('ads_today', 0) if ads_date == today else 0

        if ads_today >= MAX_ADS_PER_DAY:
            return jsonify({
                "status": "error",
                "message": f"\u274c Aaj ke {MAX_ADS_PER_DAY} ads complete! Kal wapas aao."
            }), 400

        users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"coins": AD_COIN_REWARD}, "$set": {"ads_date": today, "ads_today": ads_today + 1}}
        )
        done      = ads_today + 1
        remaining = MAX_ADS_PER_DAY - done
        return jsonify({
            "status":    "success",
            "message":   f"{AD_COIN_REWARD} coins mile! ({done}/{MAX_ADS_PER_DAY} ads aaj)",
            "ads_done":  done,
            "ads_total": MAX_ADS_PER_DAY,
            "remaining": remaining
        })
    except Exception:
        return jsonify({"status": "error", "message": "Server error"}), 500


# NEW: Channel join — one-time reward per channel
@app.route('/claim_channel', methods=['POST'])
def claim_channel_api():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400

    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid user_id"}), 400

    channel_id = data.get('channel_id', '').strip().lower()
    if channel_id not in CHANNEL_REWARDS:
        return jsonify({"status": "error", "message": "Invalid channel"}), 400

    if is_rate_limited(f"channel_{user_id}_{channel_id}", 10):
        return jsonify({"status": "error", "message": "Thoda ruko!"}), 429

    try:
        user = users_col.find_one({"user_id": user_id})
        if not user:
            return jsonify({"status": "error", "message": "User not found"}), 404

        channel_claims = user.get('channel_claims', {})
        if channel_claims.get(channel_id):
            return jsonify({"status": "error", "message": "Pehle se claim ho chuka hai! \u2705"}), 400

        reward = CHANNEL_REWARDS[channel_id]
        users_col.update_one(
            {"user_id": user_id},
            {
                "$inc": {"coins": reward},
                "$set": {f"channel_claims.{channel_id}": True}
            }
        )
        return jsonify({"status": "success", "message": f"{reward} coins mile channel join karne ke liye!", "reward": reward})
    except Exception:
        return jsonify({"status": "error", "message": "Server error"}), 500


# Device check — IP hard block, fingerprint soft flag
@app.route('/check_device', methods=['POST'])
def check_device_api():
    data = request.get_json()
    if not data:
        return jsonify({"status": "ok"})
    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"status": "ok"})

    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
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
                bot.send_message(ADMIN_ID, f"\U0001f6a8 *Multi-Account (IP)*\nUser `{user_id}` blocked. IP: `{ip}`", parse_mode="Markdown")
            except Exception:
                pass
            return jsonify({"status": "blocked"})

        if fp_conflict:
            users_col.update_one({"user_id": user_id}, {"$set": {"fp_flagged": True}})

        users_col.update_one({"user_id": user_id}, {"$set": {"fingerprint": fingerprint, "ip": ip}}, upsert=False)
        return jsonify({"status": "ok"})
    except Exception:
        return jsonify({"status": "ok"})


# Support message
@app.route('/send_support', methods=['POST'])
def send_support_api():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    user_id_raw  = data.get('user_id')
    message_text = data.get('message', '').strip()

    if not user_id_raw:
        return jsonify({"status": "error", "message": "User ID missing"}), 400
    if not message_text:
        return jsonify({"status": "error", "message": "Message is empty"}), 400
    if len(message_text) > 1000:
        return jsonify({"status": "error", "message": "Message bahut lamba (max 1000 chars)"}), 400

    try:
        user_id = int(user_id_raw)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid user ID"}), 400

    if is_rate_limited(f"support_{user_id}", 120):
        return jsonify({"status": "error", "message": "2 minute baad dobara try karo"}), 429

    try:
        support_col.insert_one({"user_id": user_id, "message": message_text, "date": str(datetime.now())})
        bot.send_message(ADMIN_ID, f"\U0001f3a7 *Support*\nFrom: `{user_id}`\n\n{message_text}", parse_mode="Markdown")
        return jsonify({"status": "success", "message": "Support message sent!"})
    except Exception:
        return jsonify({"status": "error", "message": "Message send nahi hua"}), 500


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
                "user_id":          user_id,
                "username":         username,
                "coins":            0,
                "referred_by":      None,
                "task_completions": {},
                "channel_claims":   {},
                "last_claim_ts":    "",
                "ads_today":        0,
                "ads_date":         "",
                "joined":           str(date.today())
            }
            if referrer_id and str(referrer_id) != str(user_id):
                referrer = users_col.find_one({"user_id": int(referrer_id)})
                if referrer:
                    users_col.update_one({"user_id": int(referrer_id)}, {"$inc": {"coins": 50}})
                    new_user["referred_by"] = str(referrer_id)
                    try:
                        bot.send_message(int(referrer_id), "\U0001f38a *Referral Bonus!*\n\n50 coins mile!", parse_mode="Markdown")
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

    user         = get_or_create_user(user_id, username, referrer_id)
    current_coins = user.get('coins', 0)
    web_app_url  = f"https://sahdakshsanoj-byte.github.io/Earning-bot/?user_id={user_id}"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("\U0001f4b0 Open Earning Hub", web_app=types.WebAppInfo(web_app_url)))
    markup.add(types.InlineKeyboardButton("\U0001f465 Invite Friends",
        url=f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start={user_id}&text=Join+and+earn+free+coins!"))

    bot.send_message(
        user_id,
        f"\U0001f44b *Hello {username}!*\n\n"
        f"\U0001f4b0 Balance: *{current_coins} \U0001fa99*\n\n"
        f"Refer friends aur *50 coins* kamaao! \U0001f680",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['balance'])
def check_balance(message):
    user = users_col.find_one({"user_id": message.from_user.id})
    if user:
        bot.reply_to(message, f"\U0001f4b0 Balance: *{user.get('coins', 0)} \U0001fa99*", parse_mode="Markdown")
    else:
        bot.reply_to(message, "Pehle /start karo!")


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
            bot.send_message(target_id, "\U0001f389 *Withdrawal approve ho gaya!* \u2705", parse_mode="Markdown")
        except Exception:
            pass
        bot.reply_to(message, f"\u2705 User {target_id} approved!")
    else:
        bot.reply_to(message, "Koi pending withdrawal nahi mila.")


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
        withdrawals_col.update_one({"user_id": target_id, "status": "Pending \u23f3"}, {"$set": {"status": "Rejected \u274c"}})
        try:
            bot.send_message(target_id, f"\u274c Withdrawal reject hua. {withdraw['amount']} coins wapas.", parse_mode="Markdown")
        except Exception:
            pass
        bot.reply_to(message, f"\u274c User {target_id} rejected, coins refunded.")
    else:
        bot.reply_to(message, "Koi pending withdrawal nahi mila.")


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
        bot.send_message(target_id, f"\U0001f381 Admin ne *{amount} coins* diye!", parse_mode="Markdown")
    except Exception:
        pass
    bot.reply_to(message, f"\u2705 {amount} coins added to {target_id}")


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
        bot.send_message(target_id, "\u2705 Account unblock ho gaya!", parse_mode="Markdown")
    except Exception:
        pass
    bot.reply_to(message, f"\u2705 User {target_id} unblocked!")


# Admin command: change task code — task automatically resets for all users
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
        return bot.reply_to(message, f"Invalid task. Valid: {', '.join(TASK_CODES.keys())}")
    TASK_CODES[task_id] = new_code
    # Note: task auto-resets for everyone because stored code won't match new code
    bot.reply_to(message, f"\u2705 Task `{task_id}` code: `{new_code}` — Task sabke liye reset ho gaya!", parse_mode="Markdown")


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
                        bot.send_message(user_id, "\u274c Aaj ka bonus pehle claim ho chuka!")
                        return
                except ValueError:
                    pass
            users_col.update_one({"user_id": user_id}, {"$inc": {"coins": 10}, "$set": {"last_claim_ts": now.isoformat()}})
            bot.send_message(user_id, "\U0001f381 *10 coins claim ho gaye!*", parse_mode="Markdown")
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
# 10. THREADING — Flask + Bot + Keep-Alive
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

    Thread(target=run_flask,   daemon=True).start()
    Thread(target=keep_alive,  daemon=True).start()

    bot.infinity_polling(
        timeout=20,
        long_polling_timeout=10,
        skip_pending=True,
        allowed_updates=['message', 'web_app_data']
    )
