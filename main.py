   import telebot
import sqlite3
import json
from telebot import types
from datetime import datetime
from keep_alive import keep_alive
keep_alive()

# 1. Setup
BOT_TOKEN = "bot_token" # Apna asli token yahan dalo
ADMIN_ID = 6613528513  
bot = telebot.TeleBot(BOT_TOKEN)

# 2. Database Initializing
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
    # Withdrawals Table
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

# Function to get Leaderboard Data
def get_leaderboard():
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, coins FROM users ORDER BY coins DESC LIMIT 10")
    top_users = cursor.fetchall()
    conn.close()
    # Format: ID:Coins|ID:Coins
    return "|".join([f"{u[0]}:{u[1]}" for u in top_users]) if top_users else "none"

# Function to get Referral List
def get_referral_list(user_id):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE referred_by=?", (user_id,))
    refs = cursor.fetchall()
    conn.close()
    return ",".join([str(r[0]) for r in refs]) if refs else "none"

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
        # New User logic
        if referrer_id and str(referrer_id) != str(user_id):
            cursor.execute("UPDATE users SET coins = coins + 50 WHERE user_id=?", (referrer_id,))
            try: bot.send_message(referrer_id, "🎊 Referral Bonus! You received 50 coins!")
            except: pass
            cursor.execute("INSERT INTO users (user_id, coins, referred_by) VALUES (?, ?, ?)", (user_id, 0, referrer_id))
        else:
            cursor.execute("INSERT INTO users (user_id, coins) VALUES (?, ?)", (user_id, 0))
        conn.commit()
        current_coins = 0
    else:
        current_coins = user_data[0]
    conn.close()

    # Get Dynamic Data for WebApp
    top_users = get_leaderboard()
    ref_list = get_referral_list(user_id)

    base_url = "https://sahdakshsanoj-byte.github.io/Earning-bot/"
    # Sending Coins, Leaderboard, and Referrals in URL
    web_app_url = f"{base_url}?coins={current_coins}&top_users={top_users}&ref_list={ref_list}"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💰 Open Earning Hub", web_app=types.WebAppInfo(web_app_url)))
    
    bot.send_message(user_id, f"Hello {username}!\nBalance: {current_coins} 🪙\n\nInvite friends and earn 50 coins each!", reply_markup=markup)

# 4. ADMIN COMMANDS
@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if message.from_user.id == ADMIN_ID:
        msg_text = message.text.replace('/broadcast ', '')
        if not msg_text or msg_text == '/broadcast':
            bot.reply_to(message, "Usage: /broadcast [Message]")
            return
        
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()
        conn.close()

        sent = 0
        for u in users:
            try:
                bot.send_message(u[0], msg_text)
                sent += 1
            except: pass
        bot.reply_to(message, f"📢 Sent to {sent} users!")

@bot.message_handler(commands=['stats'])
def get_stats(message):
    if message.from_user.id == ADMIN_ID:
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_u = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM withdrawals WHERE status LIKE 'Pending%'")
        pending_w = cursor.fetchone()[0]
        conn.close()
        bot.reply_to(message, f"📊 **Bot Stats**\n\nTotal Users: {total_u}\nPending Withdraws: {pending_w}")

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

        elif data.get('type') == 'withdraw_request':
            amount = data.get('amount')
            upi = data.get('upi')
            date_now = datetime.now().strftime("%d/%m/%Y")
            cursor.execute("UPDATE users SET coins = 0 WHERE user_id=?", (user_id,))
            cursor.execute("INSERT INTO withdrawals (user_id, amount, upi, date) VALUES (?, ?, ?, ?)", 
                           (user_id, amount, upi, date_now))
            conn.commit()
            bot.send_message(ADMIN_ID, f"💰 **Withdrawal Request**\nID: `{user_id}`\nUPI: `{upi}`\nAmt: {amount} 🪙\n/approve_{user_id}")

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

bot.polling()
     
