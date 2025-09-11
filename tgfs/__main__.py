import os
from flask import Flask, request
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Dispatcher, MessageHandler, Filters, CallbackQueryHandler
from telegram.utils.request import Request
from datetime import datetime, timedelta

# ======== إعداد Flask ========
app = Flask(__name__)

# ======== إعداد البوت ========
BOT_TOKEN = os.getenv("BOT_TOKEN")
BIN_CHANNEL = os.getenv("BIN_CHANNEL")
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://tg-file-stream-gamma.vercel.app")

bot = Bot(token=BOT_TOKEN, request=Request(con_pool_size=8))
dispatcher = Dispatcher(bot, None, workers=0, use_context=True)

# ======== المستخدمين والصلاحيات ========
ALLOWED_USERS_FILE = "allowed_users.txt"
ADMIN_ID = 7485195087  # معرفك أنت المسؤول
PUBLIC_MODE = False  # False = محدود، True = عام

def load_allowed_users():
    if not os.path.exists(ALLOWED_USERS_FILE):
        return []
    with open(ALLOWED_USERS_FILE, "r") as f:
        return [int(line.strip()) for line in f.readlines()]

def save_allowed_users(users):
    with open(ALLOWED_USERS_FILE, "w") as f:
        for uid in users:
            f.write(f"{uid}\n")

allowed_users = load_allowed_users()

def is_allowed_user(update):
    if PUBLIC_MODE:
        return True
    return update.message.from_user.id in allowed_users

# ======== تخزين روابط الملفات المؤقتة ========
temporary_links = {}  # {file_id: expire_time}

# ======== بدء البوت ========
def start(update, context):
    user_id = update.message.from_user.id
    if not is_allowed_user(update):
        return
    text = "✅ البوت شغال على Vercel\n📌 جميع الملفات صالحة لمدة 24 ساعة فقط."
    if PUBLIC_MODE:
        text += "\n⚠️ الوضع العام مفعل، كل شخص يمكنه استخدام البوت."
    # لوحة الإدارة للمسؤول
    if user_id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("🔓 تفعيل Public Mode", callback_data="public_on")],
            [InlineKeyboardButton("🔒 إيقاف Public Mode", callback_data="public_off")],
            [InlineKeyboardButton("➕ إضافة مستخدم", callback_data="add_user")],
            [InlineKeyboardButton("➖ إزالة مستخدم", callback_data="remove_user")],
            [InlineKeyboardButton("📋 قائمة المستخدمين", callback_data="list_users")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(text, reply_markup=reply_markup)
    else:
        update.message.reply_text(text)

# ======== التعامل مع الملفات ========
def handle_file(update, context):
    if not is_allowed_user(update):
        return

    msg = update.message
    file_id = None

    if msg.photo:
        sent = bot.send_photo(chat_id=BIN_CHANNEL, photo=msg.photo[-1].file_id)
        file_id = sent.photo[-1].file_id
    elif msg.video:
        sent = bot.send_video(chat_id=BIN_CHANNEL, video=msg.video.file_id)
        file_id = sent.video.file_id
    elif msg.audio:
        sent = bot.send_audio(chat_id=BIN_CHANNEL, audio=msg.audio.file_id)
        file_id = sent.audio.file_id
    elif msg.document:
        sent = bot.send_document(chat_id=BIN_CHANNEL, document=msg.document.file_id)
        file_id = sent.document.file_id
    else:
        update.message.reply_text("❌ لم يتم التعرف على الملف.")
        return

    expire_time = datetime.now() + timedelta(hours=24)
    temporary_links[file_id] = expire_time

    file_url = f"{PUBLIC_URL}/get_file/{file_id}"

    # أزرار Inline للمستخدم
    keyboard = [[
        InlineKeyboardButton("📥 تحميل الملف", url=file_url),
        InlineKeyboardButton("🎬 مشاهدة الفيديو", url=file_url)
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(f"📎 الرابط صالح لمدة 24 ساعة", reply_markup=reply_markup)

# ======== التعامل مع ضغطات الأزرار ========
def button_handler(update, context):
    query = update.callback_query
    query.answer()
    global PUBLIC_MODE

    if query.data == "public_on":
        PUBLIC_MODE = True
        query.edit_message_text("✅ تم تفعيل الوضع العام، كل المستخدمين يمكنهم استخدام البوت.")
    elif query.data == "public_off":
        PUBLIC_MODE = False
        query.edit_message_text("✅ تم إيقاف الوضع العام، فقط المستخدمين المصرح لهم يمكنهم استخدام البوت.")
    elif query.data == "add_user":
        query.edit_message_text("📌 أرسل معرف المستخدم الذي تريد إضافته بعد الضغط على الزر")
    elif query.data == "remove_user":
        query.edit_message_text("📌 أرسل معرف المستخدم الذي تريد حذفه بعد الضغط على الزر")
    elif query.data == "list_users":
        if allowed_users:
            users_text = "\n".join(str(uid) for uid in allowed_users)
            query.edit_message_text(f"📋 قائمة المستخدمين المصرح لهم:\n{users_text}")
        else:
            query.edit_message_text("⚠️ لا يوجد أي مستخدم مصرح له حالياً.")

# ======== مسار التحميل / المشاهدة ========
@app.route("/get_file/<file_id>", methods=["GET"])
def get_file(file_id):
    try:
        if file_id not in temporary_links:
            return "❌ الرابط غير صالح أو انتهت صلاحيته.", 400

        if datetime.now() > temporary_links[file_id]:
            del temporary_links[file_id]
            return "❌ الرابط انتهت صلاحيته بعد 24 ساعة.", 400

        file = bot.get_file(file_id)
        file_url = file.file_path

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
            return f"<a href='{file_url}'>اضغط هنا لتحميل الملف</a>", 200
    except Exception as e:
        return f"حدث خطأ: {e}", 400

# ======== Webhook ========
@app.route("/", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

# ======== اختبار Flask ========
@app.route("/test", methods=["GET"])
def test():
    return "Flask يعمل على Vercel ✅", 200

# ======== إضافة المعالجات ========
dispatcher.add_handler(MessageHandler(Filters.document | Filters.video | Filters.audio | Filters.photo, handle_file))
dispatcher.add_handler(CallbackQueryHandler(button_handler))
dispatcher.add_handler(MessageHandler(Filters.command, start))  # /start للبوت

# ======== تشغيل التطبيق ========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
