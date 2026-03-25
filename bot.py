import telebot

bot = telebot.TeleBot("YOUR_BOT_TOKEN")

# User se support message lena (Mini App se trigger hoga)
@bot.message_handler(func=lambda m: True)
def handle_support_request(message):
    # Agar user bot ko direct message bhejta hai
    admin_id = "YOUR_PERSONAL_TELEGRAM_ID"
    bot.forward_message(admin_id, message.chat.id, message.message_id)
    bot.reply_to(message, "Aapka message Admin ko bhej diya gaya hai! ✅")

bot.polling()
