import os
import telebot
import json
from telebot import types
from datetime import datetime
from pymongo import MongoClient
from keep_alive import keep_alive

# 1. Setup & Keep Alive
keep_alive()

# Render ke Environment Variables se values uthayega
BOT_TOKEN = os.environ.get('BOT_TOKEN')
MONGO_URL = os.environ.get('MONGO_URL') # Jo link tumne Render mein dala hai
ADMIN_ID = 6613528513  

bot = telebot.TeleBot(BOT_TOKEN)

# 2. MongoDB Connection
client = MongoClient(MONGO_URL)
db = client['earning_app_db']
users_col = db['users']
withdraws_col = db['withdrawals']

print("✅ MongoDB Connected Successfully!")

# --- Helper Functions (MongoDB Version) ---

def get_user_data(user_id):
    user = users_col.find_one({"user_id": user_id})
    if not user:
        # Naya user create karo
        new_user = {"user_id": user_id, "coins": 0, "referred_by": None}
        users_col.insert_one(new_user)
        return new_user
    return user

def get_leaderboard():
    # Top 10 users coins ke hisaab se
    top_users = users_col.find().sort("coins", -1).limit(10)
    data = [f"{u['user_id']}:{u['coins']}" for u in top_users]
    return "|".join(data) if data else "none"

def get_referral_list(user_id):
    # Jin logo ko is user ne refer kiya
    refs = users_col.find({"referred_by": user_id})
    data = [str(r['user_id']) for r in refs]
    return ",".join(data) if data else "none"

# --- 3. Start Command ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.first_name
    params = message.text.split()
    referrer_id = int(params[1]) if len(params) > 1 and params[1].isdigit() else None

    user_data = users_col.find_one({"user_id": user_id})

    if not user_data:
        # New User logic
        if referrer_id and referrer_id != user_id:
            # Referral bonus add karo
            users_col.update_one({"user_id": referrer_id}, {"$inc": {"coins": 50}})
            try:
                bot.send_message(referrer_id, "🎊 Referral Bonus! You received 50 coins!")
            except: pass
            users_col.insert_one({"user_id": user_id, "coins": 0, "referred_by": referrer_id})
        else:
            users_col.insert_one({"user_id": user_id, "coins": 0, "referred_by": None})
        current_coins = 0
    else:
        current_coins = user_data.get('coins', 0)

    # Get Dynamic Data for WebApp
    top_users = get_leaderboard()
    ref_list = get_referral_list(user_id)

    # Tumhara GitHub Pages Link
    base_url = "https://sahdakshsanoj-byte.github.io/Earning-bot/"
    web_app_url = f"{base_url}?coins={current_coins}&top_users={top_users}&ref_list={ref_list}"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💰 Open Earning Hub", web_app=types.WebAppInfo(web_app_url)))
    
    bot.send_message(user_id, f"Hello {username}!\nBalance: {current_coins} 🪙\n\nInvite friends and earn 50 coins each!", reply_markup=markup)

# --- 4. ADMIN COMMANDS ---
@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if message.from_user.id == ADMIN_ID:
        msg_text = message.text.replace('/broadcast ', '')
        if not msg_text or msg_text == '/broadcast':
            bot.reply_to(message, "Usage: /broadcast [Message]")
            return
        
        users = users_col.find({}, {"user_id": 1})
        sent = 0
        for u in users:
            try:
                bot.send_message(u['user_id'], msg_text)
                sent += 1
            except: pass
        bot.reply_to(message, f"📢 Sent to {sent} users!")

@bot.message_handler(commands=['stats'])
def get_stats(message):
    if message.from_user.id == ADMIN_ID:
        total_u = users_col.count_documents({})
        pending_w = withdraws_col.count_documents({"status": "Pending ⏳"})
        bot.reply_to(message, f"📊 **Bot Stats**\n\nTotal Users: {total_u}\nPending Withdraws: {pending_w}")

# --- 5. Handle Mini App Data ---
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        user_id = message.from_user.id

        if data.get('type') == 'claim_bonus':
            amount = data.get('amount', 10)
            users_col.update_one({"user_id": user_id}, {"$inc": {"coins": amount}})
            bot.send_message(user_id, f"✅ Claimed {amount} coins!")

        elif data.get('type') == 'withdraw_request':
            amount = data.get('amount')
            upi = data.get('upi')
            date_now = datetime.now().strftime("%d/%m/%Y")
            
            # Balance zero karo aur withdrawal record banao
            users_col.update_one({"user_id": user_id}, {"$set": {"coins": 0}})
            withdraws_col.insert_one({
                "user_id": user_id,
                "amount": amount,
                "upi": upi,
                "status": "Pending ⏳",
                "date": date_now
            })
            
            bot.send_message(user_id, "🚀 Withdraw request sent! Wait for admin approval.")
            bot.send_message(ADMIN_ID, f"💰 **Withdrawal Request**\nID: `{user_id}`\nUPI: `{upi}`\nAmt: {amount} 🪙\n/approve_{user_id}")

    except Exception as e:
        print(f"Error handling web_app_data: {e}")

# Start Polling
bot.polling(none_stop=True)
        
