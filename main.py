import telebot
import json
import os
import pymongo
from telebot import types
from datetime import datetime
from keep_alive import keep_alive

keep_alive()

# --- 1. Setup & Database Connection ---
BOT_TOKEN = "bot_token" # Token yahan dalo
ADMIN_ID = 6613528513  

# Render par Environment Variable set karna: MONGO_URI
MONGO_URI = os.getenv("MONGO_URI") 
client = pymongo.MongoClient(MONGO_URI)
db = client['earning_bot_db'] # Database Name
users_col = db['users']       # Collection for User Data
withdrawals_col = db['withdrawals'] # Collection for Withdrawals

bot = telebot.TeleBot(BOT_TOKEN)

# --- 2. Database Functions (Updated for MongoDB) ---

def get_leaderboard():
    # Top 10 users with highest coins
    top_users = users_col.find().sort("coins", -1).limit(10)
    data = []
    for u in top_users:
        data.append(f"{u['user_id']}:{u.get('coins', 0)}")
    return "|".join(data) if data else "none"

def get_referral_list(user_id):
    # Find users referred by this user_id
    refs = users_col.find({"referred_by": str(user_id)})
    data = [str(r['user_id']) for r in refs]
    return ",".join(data) if data else "none"

# --- 3. Start Command ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.first_name
    params = message.text.split()
    referrer_id = params[1] if len(params) > 1 else None

    # Check if user exists
    user_data = users_col.find_one({"user_id": user_id})

    if not user_data:
        # New User Logic
        new_user = {"user_id": user_id, "coins": 0, "referred_by": None}
        
        if referrer_id and str(referrer_id) != str(user_id):
            # Reward the referrer
            users_col.update_one({"user_id": int(referrer_id)}, {"$inc": {"coins": 50}})
            new_user["referred_by"] = str(referrer_id)
            try:
                bot.send_message(referrer_id, "🎊 Referral Bonus! You received 50 coins!")
            except: pass
        
        users_col.insert_one(new_user)
        current_coins = 0
    else:
        current_coins = user_data.get('coins', 0)

    # Dynamic Data for WebApp
    top_users = get_leaderboard()
    ref_list = get_referral_list(user_id)

    base_url = "https://sahdakshsanoj-byte.github.io/Earning-bot/"
    web_app_url = f"{base_url}?coins={current_coins}&top_users={top_users}&ref_list={ref_list}"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💰 Open Earning Hub", web_app=types.WebAppInfo(web_app_url)))
    
    bot.send_message(user_id, f"Hello {username}!\nBalance: {current_coins} 🪙\n\nInvite friends and earn 50 coins each!", reply_markup=markup)

# --- 4. Admin Commands ---
@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if message.from_user.id == ADMIN_ID:
        msg_text = message.text.replace('/broadcast ', '')
        if not msg_text or msg_text == '/broadcast':
            bot.reply_to(message, "Usage: /broadcast [Message]")
            return
        
        all_users = users_col.find({}, {"user_id": 1})
        sent = 0
        for u in all_users:
            try:
                bot.send_message(u['user_id'], msg_text)
                sent += 1
            except: pass
        bot.reply_to(message, f"📢 Sent to {sent} users!")

@bot.message_handler(commands=['stats'])
def get_stats(message):
    if message.from_user.id == ADMIN_ID:
        total_u = users_col.count_documents({})
        pending_w = withdrawals_col.count_documents({"status": "Pending ⏳"})
        bot.reply_to(message, f"📊 **Bot Stats (MongoDB)**\n\nTotal Users: {total_u}\nPending Withdraws: {pending_w}")

# --- 5. WebApp Data Handling ---
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        user_id = message.from_user.id

        if data.get('type') == 'claim_bonus':
            amount = data.get('amount', 10)
            users_col.update_one({"user_id": user_id}, {"$inc": {"coins": amount}})

        elif data.get('type') == 'withdraw_request':
            amount = data.get('amount')
            upi = data.get('upi')
            date_now = datetime.now().strftime("%d/%m/%Y")
            
            # Deduct coins and record withdrawal
            users_col.update_one({"user_id": user_id}, {"$set": {"coins": 0}})
            withdrawals_col.insert_one({
                "user_id": user_id,
                "amount": amount,
                "upi": upi,
                "status": "Pending ⏳",
                "date": date_now
            })
            bot.send_message(ADMIN_ID, f"💰 **Withdrawal Request**\nID: `{user_id}`\nUPI: `{upi}`\nAmt: {amount} 🪙\n/approve_{user_id}")

    except Exception as e:
        print(f"Error: {e}")

bot.polling()
