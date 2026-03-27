import telebot
import os
import pymongo
import time
from flask import Flask, jsonify, request
from flask_cors import CORS
from threading import Thread
from telebot import types
from datetime import datetime, date

# ============================================================
# 1. SETUP & DATABASE CONNECTION
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 6613528513))
MONGO_URI = os.getenv("MONGO_URI")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")

client = pymongo.MongoClient(MONGO_URI, maxPoolSize=50, serverSelectionTimeoutMS=5000)
db = client['earning_bot_db']
users_col = db['users']
withdrawals_col = db['withdrawals']
support_col = db['support']

TASK_CODES = {
    "yt1": "CODE1",
    "yt2": "CODE2",
    "yt3": "CODE3",
    "web1": "SITE1",
    "web2": "SITE2",
    "web3": "SITE3",
}

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
CORS(app, origins="*")


# ============================================================
# 2. FLASK API ROUTES
# ============================================================

@app.route('/')
def home():
    return jsonify({"status": "Bot is Running Live!", "time": str(datetime.now())})


@app.route('/get_user/<int:user_id>')
def get_user_data_api(user_id):
    user = users_col.find_one({"user_id": user_id})
    if not user:
        return jsonify({"status": "error", "message": "User not found"}), 404

    top_users = get_leaderboard()
    referrals = get_referral_list(user_id)
    completed_tasks = user.get('completed_tasks', [])

    return jsonify({
        "status": "success",
        "coins": user.get('coins', 0),
        "leaderboard": top_users,
        "referrals": referrals,
        "completed_tasks": completed_tasks,
        "last_claim": user.get('last_claim', ""),
        "referred_by": user.get('referred_by', "")
    })


@app.route('/claim_bonus/<int:user_id>', methods=['POST'])
def claim_daily_api(user_id):
    user = users_col.find_one({"user_id": user_id})
    if not user:
        return jsonify({"status": "error", "message": "User not found"}), 404

    today = str(date.today())
    if user.get('last_claim') == today:
        return jsonify({"status": "error", "message": "Already claimed today! Come back tomorrow."}), 400

    users_col.update_one(
        {"user_id": user_id},
        {"$inc": {"coins": 10}, "$set": {"last_claim": today}}
    )
    return jsonify({"status": "success", "message": "10 coins credited!", "bonus": 10})


@app.route('/withdraw', methods=['POST'])
def withdraw_api():
    data = request.get_json()
    user_id = data.get('user_id')
    upi_id = data.get('upi_id', '').strip()

    if not user_id or not upi_id:
        return jsonify({"status": "error", "message": "Missing data"}), 400

    user = users_col.find_one({"user_id": int(user_id)})
    if not user:
        return jsonify({"status": "error", "message": "User not found"}), 404

    coins = user.get('coins', 0)
    if coins < 1000:
        return jsonify({"status": "error", "message": f"Need 1000 coins. You have {coins}."}), 400

    if '@' not in upi_id:
        return jsonify({"status": "error", "message": "Invalid UPI ID format"}), 400

    withdrawal = {
        "user_id": int(user_id),
        "upi_id": upi_id,
        "amount": coins,
        "status": "Pending ⏳",
        "date": str(datetime.now().strftime("%d %b %Y, %I:%M %p"))
    }
    withdrawals_col.insert_one(withdrawal)
    users_col.update_one({"user_id": int(user_id)}, {"$set": {"coins": 0}})

    try:
        bot.send_message(
            ADMIN_ID,
            f"💸 *New Withdrawal Request*\n\n"
            f"User ID: `{user_id}`\n"
            f"UPI ID: `{upi_id}`\n"
            f"Amount: `{coins}` coins\n"
            f"Date: {withdrawal['date']}",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Admin notify error: {e}")

    return jsonify({"status": "success", "message": "Withdrawal request submitted!"})


@app.route('/get_history/<int:user_id>')
def get_history_api(user_id):
    history = list(withdrawals_col.find(
        {"user_id": user_id},
        {"_id": 0}
    ).sort("date", -1).limit(10))
    return jsonify({"status": "success", "history": history})


@app.route('/verify_task', methods=['POST'])
def verify_task_api():
    data = request.get_json()
    user_id = int(data.get('user_id'))
    task_id = data.get('task_id')
    user_code = data.get('code', '').strip().upper()
    reward = int(data.get('reward', 0))

    if not all([user_id, task_id, user_code]):
        return jsonify({"status": "error", "message": "Missing data"}), 400

    user = users_col.find_one({"user_id": user_id})
    if not user:
        return jsonify({"status": "error", "message": "User not found"}), 404

    if task_id in user.get('completed_tasks', []):
        return jsonify({"status": "error", "message": "Task already completed!"}), 400

    correct_code = TASK_CODES.get(task_id, "").upper()
    if user_code != correct_code:
        return jsonify({"status": "error", "message": "Wrong code! Try again."}), 400

    users_col.update_one(
        {"user_id": user_id},
        {"$inc": {"coins": reward}, "$push": {"completed_tasks": task_id}}
    )
    return jsonify({"status": "success", "message": f"{reward} coins added!", "reward": reward})


@app.route('/send_support', methods=['POST'])
def send_support_api():
    data = request.get_json()
    user_id = int(data.get('user_id'))
    message_text = data.get('message', '').strip()

    if not message_text:
        return jsonify({"status": "error", "message": "Message is empty"}), 400

    support_col.insert_one({
        "user_id": user_id,
        "message": message_text,
        "date": str(datetime.now())
    })

    try:
        bot.send_message(
            ADMIN_ID,
            f"🎧 *Support Message*\n\nFrom User ID: `{user_id}`\n\n{message_text}",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Support notify error: {e}")

    return jsonify({"status": "success", "message": "Support message sent!"})


# ============================================================
# 3. HELPER FUNCTIONS
# ============================================================

def get_leaderboard():
    top_users = list(users_col.find({}, {"user_id": 1, "coins": 1, "_id": 0}).sort("coins", -1).limit(10))
    data = [f"{u['user_id']}:{u.get('coins', 0)}" for u in top_users]
    return "|".join(data) if data else "none"


def get_referral_list(user_id):
    refs = list(users_col.find({"referred_by": str(user_id)}, {"user_id": 1, "_id": 0}))
    data = [str(r['user_id']) for r in refs]
    return ",".join(data) if data else "none"


def get_or_create_user(user_id, username, referrer_id=None):
    user = users_col.find_one({"user_id": user_id})
    if not user:
        new_user = {
            "user_id": user_id,
            "username": username,
            "coins": 0,
            "referred_by": None,
            "completed_tasks": [],
            "last_claim": "",
            "joined": str(date.today())
        }
        if referrer_id and str(referrer_id) != str(user_id):
            referrer = users_col.find_one({"user_id": int(referrer_id)})
            if referrer:
                users_col.update_one({"user_id": int(referrer_id)}, {"$inc": {"coins": 50}})
                new_user["referred_by"] = str(referrer_id)
                try:
                    bot.send_message(
                        int(referrer_id),
                        f"🎊 *Referral Bonus!*\n\nAapne ek friend invite kiya aur 50 coins kamaye!",
                        parse_mode="Markdown"
                    )
                except:
                    pass
        users_col.insert_one(new_user)
        return new_user
    return user


# ============================================================
# 4. BOT COMMANDS
# ============================================================

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.first_name or "User"
    params = message.text.split()
    referrer_id = params[1] if len(params) > 1 else None

    user = get_or_create_user(user_id, username, referrer_id)
    current_coins = user.get('coins', 0)

    web_app_url = f"https://sahdakshsanoj-byte.github.io/Earning-bot/?user_id={user_id}"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        "💰 Open Earning Hub",
        web_app=types.WebAppInfo(web_app_url)
    ))
    markup.add(types.InlineKeyboardButton(
        "👥 Invite Friends",
        url=f"https://tt.me/{BOT_USERNAME}?start={user_id}&text=Join+and+earn+free+coins!"
    ))

    bot.send_message(
        user_id,
        f"👋 *Hello {username}!*\n\n"
        f"💰 Balance: *{current_coins} 🪙*\n\n"
        f"Refer friends aur *50 coins* kamaao har ek ke liye!\n"
        f"Neeche button dabao aur start karo earning! 🚀",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['balance'])
def check_balance(message):
    user_id = message.from_user.id
    user = users_col.find_one({"user_id": user_id})
    if user:
        bot.reply_to(message, f"💰 Tumhara balance: *{user.get('coins', 0)} 🪙*", parse_mode="Markdown")
    else:
        bot.reply_to(message, "Pehle /start karo!")


@bot.message_handler(commands=['stats'])
def get_stats(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    total_u = users_col.count_documents({})
    pending_w = withdrawals_col.count_documents({"status": "Pending ⏳"})
    today_joined = users_col.count_documents({"joined": str(date.today())})
    bot.reply_to(
        message,
        f"📊 *Bot Stats*\n\n"
        f"👥 Total Users: `{total_u}`\n"
        f"🆕 Today Joined: `{today_joined}`\n"
        f"💸 Pending Withdrawals: `{pending_w}`",
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
        {"user_id": target_id, "status": "Pending ⏳"},
        {"$set": {"status": "Approved ✅"}}
    )
    if result.modified_count:
        try:
            bot.send_message(target_id, "🎉 *Tumhara withdrawal approve ho gaya!* Payment processing mein hai. ✅", parse_mode="Markdown")
        except:
            pass
        bot.reply_to(message, f"✅ User {target_id} ka withdrawal approved!")
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
    withdraw = withdrawals_col.find_one({"user_id": target_id, "status": "Pending ⏳"})
    if withdraw:
        users_col.update_one({"user_id": target_id}, {"$inc": {"coins": withdraw['amount']}})
        withdrawals_col.update_one(
            {"user_id": target_id, "status": "Pending ⏳"},
            {"$set": {"status": "Rejected ❌"}}
        )
        try:
            bot.send_message(target_id, f"❌ Tumhara withdrawal reject hua.\n{withdraw['amount']} coins wapas de diye gaye.", parse_mode="Markdown")
        except:
            pass
        bot.reply_to(message, f"❌ User {target_id} ka withdrawal rejected aur coins refunded.")
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
    amount = int(parts[2])
    users_col.update_one({"user_id": target_id}, {"$inc": {"coins": amount}})
    try:
        bot.send_message(target_id, f"🎁 Admin ne tumhe *{amount} coins* diye!", parse_mode="Markdown")
    except:
        pass
    bot.reply_to(message, f"✅ {amount} coins added to user {target_id}")


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
        except:
            failed += 1
    bot.reply_to(message, f"📢 Sent: {sent} | Failed: {failed}")


@bot.message_handler(commands=['settask'])
def set_task_code(message):
    if int(message.from_user.id) != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 3:
        return bot.reply_to(message, "Usage: /settask <task_id> <new_code>\nExample: /settask yt1 WATCH123")
    task_id = parts[1].lower()
    new_code = parts[2].upper()
    if task_id not in TASK_CODES:
        return bot.reply_to(message, f"Invalid task ID. Valid: {', '.join(TASK_CODES.keys())}")
    TASK_CODES[task_id] = new_code
    bot.reply_to(message, f"✅ Task `{task_id}` ka code update ho gaya: `{new_code}`", parse_mode="Markdown")


@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    import json
    try:
        data = json.loads(message.web_app_data.data)
        user_id = message.from_user.id
        action = data.get('type')

        if action == 'claim_bonus':
            today = str(date.today())
            user = users_col.find_one({"user_id": user_id})
            if user and user.get('last_claim') == today:
                bot.send_message(user_id, "❌ Aaj ka bonus pehle se claim ho chuka hai! Kal wapas aana.")
            else:
                users_col.update_one({"user_id": user_id}, {"$inc": {"coins": 10}, "$set": {"last_claim": today}})
                bot.send_message(user_id, "🎁 *10 coins claim ho gaye!* Daily wapas aana. 😊", parse_mode="Markdown")

        elif action == 'withdraw_request':
            amount = data.get('amount', 0)
            upi = data.get('upi', '')
            if amount < 1000:
                bot.send_message(user_id, "❌ Minimum 1000 coins chahiye withdraw ke liye!")
            elif '@' not in upi:
                bot.send_message(user_id, "❌ Valid UPI ID enter karo (example: name@upi)")
            else:
                withdrawal = {
                    "user_id": user_id, "upi_id": upi, "amount": amount,
                    "status": "Pending ⏳", "date": str(datetime.now().strftime("%d %b %Y, %I:%M %p"))
                }
                withdrawals_col.insert_one(withdrawal)
                users_col.update_one({"user_id": user_id}, {"$set": {"coins": 0}})
                bot.send_message(user_id, f"💸 *Withdrawal Request Submitted!*\n\nAmount: `{amount}` coins\nUPI: `{upi}`\nStatus: Pending ⏳", parse_mode="Markdown")
                bot.send_message(ADMIN_ID, f"💸 *New Withdrawal*\nUser: `{user_id}`\nUPI: `{upi}`\nAmount: `{amount}`", parse_mode="Markdown")

    except Exception as e:
        print(f"WebApp data error: {e}")


# ============================================================
# 5. THREADING
# ============================================================

def run_flask():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    print("🚀 Bot starting...")

    # === 409 FIX — Forcefully purana session band karo ===
    try:
        bot.delete_webhook(drop_pending_updates=True)
        print("✅ Webhook deleted.")
    except Exception as e:
        print(f"Webhook delete error (ignore): {e}")

    time.sleep(3)  # Purana process band hone ka wait karo

    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    print("✅ Flask server started.")

    print("✅ Bot polling started...")
    while True:
        try:
            bot.infinity_polling(
                timeout=20,
                long_polling_timeout=10,
                skip_pending=True,
                allowed_updates=['message', 'web_app_data']
            )
        except Exception as e:
            print(f"Polling error: {e}. Restarting in 5 seconds...")
            time.sleep(5)
            try:
                bot.delete_webhook(drop_pending_updates=True)
            except:
                pass
            time.sleep(3)
