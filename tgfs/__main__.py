import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
from telegram.utils.request import Request

# ======== إنشاء تطبيق Flask ========
app = Flask(__name__)

# ======== إعداد البوت ========
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BIN_CHANNEL = os.getenv("BIN_CHANNEL")
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://tg-file-stream-gamma.vercel.app")

bot = Bot(token=BOT_TOKEN, request=Request(con_pool_size=8))
dispatcher = Dispatcher(bot, None, workers=0, use_context=True)

# ======== أوامر البوت ========
def start(update, context):
    update.message.reply_text("✅ البوت شغال على Vercel")

dispatcher.add_handler(CommandHandler("start", start))

# ======== استقبال الملفات والفيديوهات والصور ========
def handle_file(update, context):
    msg = update.message
    file_id = None

    # الصور
    if msg.photo:
        sent = context.bot.send_photo(chat_id=BIN_CHANNEL, photo=msg.photo[-1].file_id)
        file_id = sent.photo[-1].file_id

    # الفيديو
    elif msg.video:
        sent = context.bot.send_video(chat_id=BIN_CHANNEL, video=msg.video.file_id)
        file_id = sent.video.file_id

    # الصوت
    elif msg.audio:
        sent = context.bot.send_audio(chat_id=BIN_CHANNEL, audio=msg.audio.file_id)
        file_id = sent.audio.file_id

    # ملفات أخرى
    elif msg.document:
        sent = context.bot.send_document(chat_id=BIN_CHANNEL, document=msg.document.file_id)
        file_id = sent.document.file_id

    # لا يوجد ملف
    else:
        update.message.reply_text("❌ لم يتم التعرف على الملف.")
        return

    # رابط التحميل/المشاهدة
    link = f"{PUBLIC_URL}/get_file/{file_id}"
    update.message.reply_text(f"📎 رابط الملف:\n{link}")

dispatcher.add_handler(MessageHandler(Filters.document | Filters.video | Filters.audio | Filters.photo, handle_file))

# ======== مسار التحميل والمشاهدة ========
@app.route("/get_file/<file_id>", methods=["GET"])
def get_file(file_id):
    try:
        file = bot.get_file(file_id)
        file_url = file.file_path

        # عرض الفيديو مباشرة إذا كان من نوع mp4 أو mkv أو mov أو webm
        if file.file_path.endswith(('.mp4', '.mkv', '.mov', '.webm')):
            html_content = f"""
            <html>
            <body style="display:flex;justify-content:center;align-items:center;height:100vh;">
            <video width="80%" height="80%" controls autoplay>
              <source src="{file_url}" type="video/mp4">
              المتصفح لا يدعم الفيديو.
            </video>
            </body>
            </html>
            """
            return html_content, 200
        else:
            # الملفات الأخرى → رابط تحميل مباشر
            return f"<a href='{file_url}'>اضغط هنا لتحميل الملف</a>", 200

    except Exception as e:
        return f"حدث خطأ: {e}", 400

# ======== مسار / Webhook ========
@app.route("/", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

# ======== مسار اختبار Flask ========
@app.route("/test", methods=["GET"])
def test():
    return "Flask يعمل على Vercel ✅", 200

# ======== نقطة الدخول ========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
