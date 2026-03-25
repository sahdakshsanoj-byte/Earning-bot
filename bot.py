import telebot
import sqlite3
import json
from telebot import types

# 1. Setup
BOT_TOKEN = "8658984116:AAGb37UHU5JZ00BzbdKCk19O5Sj6tyfhZ0Q"
ADMIN_ID =   6613528513  # Replace with your actual numerical ID (No quotes)
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

# 3. Start Command with Referral Detection
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
    
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    user_exists = cursor.fetchone()

    if not user_exists:
        if referrer_id and str(referrer_id) != str(user_id):
            # Credit 50 coins to the referrer
            cursor.execute("UPDATE users SET coins = coins + 50 WHERE user_id=?", (referrer_id,))
            bot.send_message(referrer_id, f"🎊 Congratulations! A new user joined via your link. You have received 50 coins!")
            
            cursor.execute("INSERT INTO users (user_id, coins, referred_by) VALUES (?, ?, ?)", (user_id, 0, referrer_id))
        else:
            cursor.execute("INSERT INTO users (user_id, coins) VALUES (?, ?)", (user_id, 0))
        
        conn.commit()
    conn.close()

    # Mini App Button
    markup = types.InlineKeyboardMarkup()
    # Replace with your actual GitHub Pages URL
    web_app = types.WebAppInfo("https://YOUR_GITHUB_USERNAME.github.io/YOUR_REPO/")
    markup.add(types.InlineKeyboardButton("💰 Open Earning Hub", web_app=web_app))
    
    welcome_text = (f"Hello {username}!\n\nWelcome to the Earning Hub Bot. "
                   "Click the button below to start earning coins and rewards!")
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
            bot.reply_to(message, "Your message has been sent to the Admin! ✅ You will receive a response within 5-6 hours.")
    except Exception as e:
        print(f"Error parsing WebApp data: {e}")

# 5. Direct Message Forwarding
@bot.message_handler(func=lambda m: True)
def handle_text(message):
    if message.from_user.id != ADMIN_ID:
        bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
        bot.reply_to(message, "Your message has been forwarded to the Support Team. ✅")

bot.polling()
