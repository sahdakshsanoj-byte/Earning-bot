import telebot
import sqlite3
import json
from telebot import types

# 1. Setup
BOT_TOKEN = "bot_token" # Apna naya token yahan dalo
ADMIN_ID = 6613528513  
bot = telebot.TeleBot(BOT_TOKEN)

# 2. Database Initializing
def init_db():
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            coins INTEGER DEFAULT 0,
            referred_by INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# 3. Start Command with Referral & Balance Fetching
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.first_name
    
    params = message.text.split()
    referrer_id = None
    if len(params) > 1:
        referrer_id = params[1]

    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    # Check if user exists
    cursor.execute("SELECT coins FROM users WHERE user_id=?", (user_id,))
    user_data = cursor.fetchone()

    if not user_data:
        # Naya user register ho raha hai
        current_coins = 0
        if referrer_id and str(referrer_id) != str(user_id):
            # Referrer ko 50 coins dena
            cursor.execute("UPDATE users SET coins = coins + 50 WHERE user_id=?", (referrer_id,))
            try:
                bot.send_message(referrer_id, f"🎊 Congratulations! A new user joined via your link. You received 50 coins!")
            except:
                pass
            
            cursor.execute("INSERT INTO users (user_id, coins, referred_by) VALUES (?, ?, ?)", (user_id, 0, referrer_id))
        else:
            cursor.execute("INSERT INTO users (user_id, coins) VALUES (?, ?)", (user_id, 0))
        conn.commit()
    else:
        # Purana user hai, database se uske coins lo
        current_coins = user_data[0]
    
    conn.close()

    # Mini App Button with Dynamic Coins URL
    # Replace the URL below with your actual GitHub Pages link
    base_url = "https://sahdakshsanoj-byte.github.io/Earning-bot/"
    web_app_url = f"{base_url}?coins={current_coins}"
    
    markup = types.InlineKeyboardMarkup()
    web_app = types.WebAppInfo(web_app_url)
    markup.add(types.InlineKeyboardButton("💰 Open Earning Hub", web_app=web_app))
    
    welcome_text = (f"Hello {username}!\n\n"
                   f"Your current balance: {current_coins} 🪙\n\n"
                   "Click the button below to start earning more rewards!")
    
    bot.send_message(user_id, welcome_text, reply_markup=markup)

# 4. Handle Support Messages (Mini App Data)
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        
        if data.get('type') == 'support':
            user_msg = data.get('message')
            user_info = f"👤 User: {message.from_user.first_name} (ID: {message.from_user.id})"
            
            # Forward to Admin
            bot.send_message(ADMIN_ID, f"📩 **New Support Request**\n\n{user_info}\n💬 Message: {user_msg}")
            bot.reply_to(message, "Your message has been sent to the Admin! ✅")
    except Exception as e:
        print(f"Error: {e}")

# 5. Direct Message Forwarding
@bot.message_handler(func=lambda m: True)
def handle_text(message):
    if message.from_user.id != ADMIN_ID:
        bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
        bot.reply_to(message, "Your message has been forwarded to the Support Team. ✅")

bot.polling()
