import os
import telebot
import json
from telebot import types
from datetime import datetime
from pymongo import MongoClient
from keep_alive import keep_alive

# 1. Setup & Keep Alive
keep_alive()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
MONGO_URL = os.environ.get('MONGO_URL')
ADMIN_ID = 6613528513  

bot = telebot.TeleBot(BOT_TOKEN)

# 2. MongoDB Connection
client = MongoClient(MONGO_URL)
db = client['earning_app_db']
users_col = db['users']
withdraws_col = db['withdrawals']

print("✅ MongoDB Connected & Ready for Ads Section!")

# --- Helper Functions ---

def get_user(user_id):
    user = users_col.find_one({"user_id": user_id})
    today = datetime.now().strftime("%d/%m/%Y")
    
    if not user:
        new_user = {
            "user_id": user_id, 
            "coins": 0, 
            "referred_by": None,
            "daily_ads_done": 0,
            "last_ad_date": today,
            "joined_sponsors": [],
            "completed_tasks": [] # Konsa YT/Web task ho gaya hai
        }
        users_col.insert_one(new_user)
        return new_user
    
    if user.get('last_ad_date') != today:
        users_col.update_one(
            {"user_id": user_id}, 
            {"$set": {"daily_ads_done": 0, "last_ad_date": today}}
        )
        user['daily_ads_done'] = 0
        
    return user

# --- 3. Start Command ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.first_name
    params = message.text.split()
    referrer_id = int(params[1]) if len(params) > 1 and params[1].isdigit() else None

    user_data = get_user(user_id)

    # Referral Logic
    if user_data['coins'] == 0 and not user_data['referred_by']:
        if referrer_id and referrer_id != user_id:
            users_col.update_one({"user_id": referrer_id}, {"$inc": {"coins": 50}})
            users_col.update_one({"user_id": user_id}, {"$set": {"referred_by": referrer_id}})
            try: bot.send_message(referrer_id, f"🎊 Referral Bonus! {username} joined. +50 coins!")
            except: pass

    # Leaderboard Data
    top_users = users_col.find().sort("coins", -1).limit(10)
    leaderboard_str = "|".join([f"{u['user_id']}:{u['coins']}" for u in top_users])
    
    # Refer List (User ne kis kis ko refer kiya)
    refs = users_col.find({"referred_by": user_id})
    ref_list_str = ",".join([str(r['user_id']) for r in refs]) if refs else "none"

    # Mini App URL
    base_url = "https://sahdakshsanoj-byte.github.io/Earning-bot/"
    web_app_url = f"{base_url}?coins={user_data['coins']}&ads={user_data['daily_ads_done']}&top={leaderboard_str}&ref_list={ref_list_str}"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💰 Open Earning Hub", web_app=types.WebAppInfo(web_app_url)))
    
    bot.send_message(user_id, f"Hello {username}!\nBalance: {user_data['coins']} 🪙\n\nComplete 3+3 Tasks & Join Sponsors to earn!", reply_markup=markup)

# --- 4. Handle Mini App Actions ---
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        user_id = message.from_user.id
        user = get_user(user_id)

        # Action 1: Ads Reward (+10)
        if data.get('type') == 'ad_click':
            if user['daily_ads_done'] < 5:
                users_col.update_one({"user_id": user_id}, {"$inc": {"coins": 10, "daily_ads_done": 1}})
                bot.send_message(user_id, f"✅ Ad Reward: +10 Coins!")
            else:
                bot.send_message(user_id, "⚠️ Limit reached (5/5). Kal aana!")

        # Action 2: YT/Web Task Reward (+20 or any)
        elif data.get('type') == 'claim_bonus':
            amount = data.get('amount', 10)
            users_col.update_one({"user_id": user_id}, {"$inc": {"coins": amount}})
            bot.send_message(user_id, f"🎁 Task Complete! +{amount} Coins Added.")

        # Action 3: Sponsor Join Check
        elif data.get('type') == 'check_sponsor':
            channel_id = data.get('channel_id')
            try:
                member = bot.get_chat_member(channel_id, user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    if channel_id not in user.get('joined_sponsors', []):
                        users_col.update_one({"user_id": user_id}, {
                            "$inc": {"coins": 50}, # Fixed reward for sponsors
                            "$push": {"joined_sponsors": channel_id}
                        })
                        bot.send_message(user_id, f"✅ Sponsor Join: +50 Coins!")
                    else:
                        bot.send_message(user_id, "⚠️ Already claimed.")
                else:
                    bot.send_message(user_id, f"❌ Join {channel_id} first!")
            except:
                bot.send_message(user_id, "❌ Error: Bot ko channel mein Admin banayein!")

        # Action 4: Withdraw Request
        elif data.get('type') == 'withdraw_request':
            amount = data.get('amount')
            upi = data.get('upi')
            withdraws_col.insert_one({
                "user_id": user_id,
                "amount": amount,
                "upi": upi,
                "status": "Pending",
                "date": datetime.now().strftime("%d/%m/%Y")
            })
            bot.send_message(ADMIN_ID, f"🔔 NEW WITHDRAW!\nUser: {user_id}\nAmount: {amount}\nUPI: {upi}")
            bot.send_message(user_id, "🚀 Withdraw request sent! Status: Pending.")

    except Exception as e:
        print(f"Error handling web_app_data: {e}")

bot.polling(none_stop=True)
            
