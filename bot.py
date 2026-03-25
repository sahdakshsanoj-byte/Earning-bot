import telebot
import sqlite3
import json
from telebot import types
from datetime import datetime

# 1. Setup
BOT_TOKEN = "bot_token" # Apna asli token yahan dalo
ADMIN_ID = 6613528513  
bot = telebot.TeleBot(BOT_TOKEN)

# 2. Database Initializing (New Table for Withdrawals)
def init_db():
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            coins INTEGER DEFAULT 0,
            referred_by INTEGER
        )
    ''')
    # Withdrawals Table (Permanent History)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            upi TEXT,
            status TEXT DEFAULT 'Pending ⏳',
            date TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# 3. Start Command
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.first_name
    params = message.text.split()
    referrer_id = params[1] if len(params) > 1 else None

    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute("SELECT coins FROM users WHERE user_id=?", (user_id,))
    user_data = cursor.fetchone()

    if not user_data:
        current_coins = 0
        if referrer_id and str(referrer_id) != str(user_id):
            cursor.execute("UPDATE users SET coins = coins + 50 WHERE user_id=?", (referrer_id,))
            try: bot.send_message(referrer_id, "🎊 Referral Bonus! You received 50 coins!")
            except: pass
            cursor.execute("INSERT INTO users (user_id, coins, referred_by) VALUES (?, ?, ?)", (user_id, 0, referrer_id))
        else:
            cursor.execute("INSERT INTO users (user_id, coins) VALUES (?, ?)", (user_id, 0))
        conn.commit()
    else:
        current_coins = user_data[0]
    conn.close()

    base_url = "https://sahdakshsanoj-byte.github.io/Earning-bot/"
    web_app_url = f"{base_url}?coins={current_coins}"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💰 Open Earning Hub", web_app=types.WebAppInfo(web_app_url)))
    
    bot.send_message(user_id, f"Hello {username}!\nBalance: {current_coins} 🪙", reply_markup=markup)

# 4. ADMIN COMMANDS (/stats and /approve)
@bot.message_handler(commands=['stats'])
def get_stats(message):
    if message.from_user.id == ADMIN_ID:
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_u = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM withdrawals WHERE status='Pending ⏳'")
        pending_w = cursor.fetchone()[0]
        conn.close()
        bot.reply_to(message, f"📊 **Bot Stats**\n\nTotal Users: {total_u}\nPending Withdraws: {pending_w}")

@bot.message_handler(commands=['approve'])
def approve_payment(message):
    if message.from_user.id == ADMIN_ID:
        try:
            # Command: /approve [user_id]
            u_id = message.text.split()[1]
            conn = sqlite3.connect('users.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE withdrawals SET status='Success ✅' WHERE user_id=? AND status='Pending ⏳'", (u_id,))
            conn.commit()
            conn.close()
            bot.send_message(u_id, "✅ Your withdrawal has been approved! Payment sent.")
            bot.reply_to(message, f"Done! Payment for {u_id} marked as Success.")
        except:
            bot.reply_to(message, "Usage: /approve [user_id]")

# 5. Handle Mini App Data
@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    try:
        data = json.loads(message.web_app_data.data)
        user_id = message.from_user.id
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()

        if data.get('type') == 'claim_bonus':
            amount = data.get('amount', 10)
            cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id=?", (amount, user_id))
            conn.commit()
            bot.send_message(user_id, f"✅ +{amount} coins saved!")

        elif data.get('type') == 'withdraw_request':
            amount = data.get('amount')
            upi = data.get('upi')
            date_now = datetime.now().strftime("%d/%m/%Y")
            
            # 1. Update User Balance
            cursor.execute("UPDATE users SET coins = 0 WHERE user_id=?", (user_id,))
            # 2. Save to Permanent History
            cursor.execute("INSERT INTO withdrawals (user_id, amount, upi, date) VALUES (?, ?, ?, ?)", 
                           (user_id, amount, upi, date_now))
            conn.commit()
            
            bot.send_message(ADMIN_ID, f"💰 **Withdrawal Request**\nID: `{user_id}`\nUPI: `{upi}`\nAmt: {amount} 🪙")
            bot.send_message(user_id, "✅ Request Sent! Check history in 3-dot menu.")

        elif data.get('type') == 'support':
            bot.send_message(ADMIN_ID, f"📩 **Support**\nFrom: {message.from_user.first_name}\nMsg: {data.get('message')}")
            bot.reply_to(message, "Sent to Admin! ✅")

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

# 6. Forwarding
@bot.message_handler(func=lambda m: True)
def handle_text(message):
    if message.from_user.id != ADMIN_ID:
        bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
        bot.reply_to(message, "Forwarded to Support Team. ✅")

bot.polling()
