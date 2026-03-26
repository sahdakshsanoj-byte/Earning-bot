from flask import Flask
from threading import Thread
import os # Ye import karna mat bhulna

app = Flask('')

@app.route('/')
def home():
    return "Bot is Alive!"

def run():
    # Render se port lene ke liye os.environ.get use karo
    port = int(os.environ.get("PORT", 8080)) 
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
