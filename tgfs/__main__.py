from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
import os

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)

app = Flask(__name__)

# dispatcher لمعالجة الأوامر
dispatcher = Dispatcher(bot, None, workers=0)

# أوامر بسيطة للتجربة
def start(update, context):
    update.message.reply_text("البوت شغال ✅")

dispatcher.add_handler(CommandHandler("start", start))

@app.route("/", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

@app.route("/", methods=["GET"])
def index():
    return "بوت TG-FileStream شغال على Vercel ✅"
