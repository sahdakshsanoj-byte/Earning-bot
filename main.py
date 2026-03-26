 import telebot
import json
import os
import pymongo
import time
from flask import Flask, jsonify, request
from threading import Thread
from telebot import types
from datetime import datetime

# --- 1. Setup & Database Connection ---
BOT_TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID", 6613528513))
MONGO_URI = os.getenv("MONGO_URI")

# Fast Connection Settings
client = pymongo.MongoClient(MONGO_URI, maxPoolSize=50, waitQueueMultiple=10)
db = client['earning_bot_db']
users_col = db['users']
withdrawals_col = db['withdrawals']

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# --- 2. Flask API for Fast Data (Live Sync) ---

@app.route('/')
def home():
    return "Bot is Running Live!"

@app.route('/get_user/<int:user_id>')
def get_user_data_api(user_id):
    # Seedha Database se latest data uthayega
    user = users_col.find_one({"user_id": user_id})
    if user:
        # Leaderboard aur Referral bhi fetch karega taaki App fast load ho
        top_users = get_leaderboard()
        return jsonify({
            "status": "success",
            "coins": user.get('coins', 0),
            "leaderboard": top_users,
            "referred_by": user.get('referred_by')
        })
    return jsonify({"status": "error", "message": "User not found"}), 404

# --- 3. Helper Functions ---

def get_leaderboard():
    top_users = users_col.find().sort("coins", -1).limit(10)
    data = [f"{u['user_id']}:{u.get('coins', 0)}" for u in top_users]
    return "|".join(data) if data else "none"

def get_referral_list(user_id):
    refs = users_col.find({"referred_by": str(user_id)})
    data = [str(r['user_id']) for r in refs]
    return ",".join(data) if data else "none"

# --- 4. Bot Commands ---

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.first_name
    params = message.text.split()
    referrer_id = params[1] if len(params) > 1 else None

    user_data = users_col.find_one({"user_id": user_id})

    if not user_data:
        new_user = {"user_id": user_id, "coins": 0, "referred_by": None}
        if referrer_id and str(referrer_id) != str(user_id):
            users_col.update_one({"user_id": int(referrer_id)}, {"$inc": {"coins": 50}})
            new_user["referred_by"] = str(referrer_id)
            try:
                bot.send_message(referrer_id, "🎊 Referral Bonus! You received 50 coins!")
            except: pass
        users_col.insert_one(new_user)
        current_coins = 0
    else:
        current_coins = user_data.get('coins', 0)

    # WebApp URL with latest data
    base_url = "https://sahdakshsanoj-byte.github.io/Earning-bot/"
    web_app_url = f"{base_url}?user_id={user_id}" # Ab sirf ID bhejenge, baaki data API se aayega
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💰 Open Earning Hub", web_app=types.WebAppInfo(web_app_url)))
    
    bot.send_message(user_id, f"Hello {username}!\nBalance: {current_coins} 🪙\n\nInvite friends and earn 50 coins each!", reply_markup=markup)

@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if int(message.from_user.id) == ADMIN_ID:
        msg_text = message.text.replace('/broadcast ', '')
        if not msg_text or msg_text == '/broadcast':
            return bot.reply_to(message, "Usage: /broadcast [Message]")
        
        all_users = users_col.find({}, {"user_id": 1})
        sent = 0
        for u in all_users:
            try:
                bot.send_message(u['user_id'], msg_text)
                sent += 1
                time.sleep(0.05) # Anti-Ban Delay
            except: pass
        bot.reply_to(message, f"📢 Sent to {sent} users!")

@bot.message_handler(commands=['stats'])
def get_stats(message):
    if int(message.from_user.id) == ADMIN_ID:
        total_u = users_col.count_documents({})
        pending_w = withdrawals_col.count_documents({"status": "Pending ⏳"})
        bot.reply_to(message, f"📊 **Bot Stats**\n\nTotal Users: {total_u}\nPending Withdraws: {pending_w}")

# --- 5. Threading to run Bot and Flask together ---
def run_flask():
    app.run(host="0.0.0.0", port=8080)

if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.start()
    bot.polling(none_stop=True)
   
