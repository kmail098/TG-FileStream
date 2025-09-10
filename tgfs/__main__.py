import os
from flask import Flask
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

# ======== استقبال الملفات ========
def handle_file(update, context):
    file = update.message.document or update.message.video or update.message.audio or update.message.photo[-1]

    if file:
        # رفع الملف للقناة الخاصة
        sent = context.bot.send_document(chat_id=BIN_CHANNEL, document=file.file_id)

        # إنشاء رابط تحميل/مشاهدة مباشر
        file_id = sent.document.file_id
        link = f"{PUBLIC_URL}/get_file/{file_id}"

        # إرسال الرابط للمستخدم
        update.message.reply_text(f"📎 رابط الملف:\n{link}")
    else:
        update.message.reply_text("❌ لم يتم التعرف على الملف.")

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

# ======== نقطة الدخول ========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
