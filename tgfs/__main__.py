import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
from telegram.utils.request import Request

# ======== إنشاء تطبيق Flask ========
app = Flask(__name__)

# ======== إعداد البوت ========
BOT_TOKEN = os.getenv("BOT_TOKEN")
BIN_CHANNEL = os.getenv("BIN_CHANNEL")
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://tg-file-stream-gamma.vercel.app")

bot = Bot(token=BOT_TOKEN, request=Request(con_pool_size=8))
dispatcher = Dispatcher(bot, None, workers=0, use_context=True)

# ======== قائمة المستخدمين المسموح لهم ========
ALLOWED_USERS_FILE = "allowed_users.txt"
ADMIN_ID = 7485195087  # المسؤول الوحيد

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
    return update.message.from_user.id in allowed_users

# ======== أوامر البوت ========
def start(update, context):
    if not is_allowed_user(update):
        return
    update.message.reply_text("✅ البوت شغال على Vercel")

# ======== إدارة المستخدمين ========
def add_user(update, context):
    if update.message.from_user.id != ADMIN_ID:
        update.message.reply_text("❌ فقط المسؤول يمكنه إدارة المستخدمين.")
        return

    if len(context.args) != 1:
        update.message.reply_text("❌ الاستخدام: /adduser <USER_ID>")
        return

    try:
        new_id = int(context.args[0])
        if new_id in allowed_users:
            update.message.reply_text("✅ المستخدم موجود بالفعل.")
        else:
            allowed_users.append(new_id)
            save_allowed_users(allowed_users)
            update.message.reply_text(f"✅ تم إضافة المستخدم {new_id} بنجاح.")
    except ValueError:
        update.message.reply_text("❌ يرجى إدخال رقم صحيح.")

def remove_user(update, context):
    if update.message.from_user.id != ADMIN_ID:
        update.message.reply_text("❌ فقط المسؤول يمكنه إدارة المستخدمين.")
        return

    if len(context.args) != 1:
        update.message.reply_text("❌ الاستخدام: /removeuser <USER_ID>")
        return

    try:
        del_id = int(context.args[0])
        if del_id not in allowed_users:
            update.message.reply_text("❌ المستخدم غير موجود.")
        else:
            allowed_users.remove(del_id)
            save_allowed_users(allowed_users)
            update.message.reply_text(f"✅ تم حذف المستخدم {del_id} بنجاح.")
    except ValueError:
        update.message.reply_text("❌ يرجى إدخال رقم صحيح.")

def list_users(update, context):
    if update.message.from_user.id != ADMIN_ID:
        update.message.reply_text("❌ فقط المسؤول يمكنه عرض المستخدمين.")
        return

    if not allowed_users:
        update.message.reply_text("📃 لا يوجد أي مستخدم مسموح له حاليًا.")
        return

    user_list = "\n".join([str(uid) for uid in allowed_users])
    update.message.reply_text(f"📃 قائمة المستخدمين المسموح لهم:\n{user_list}")

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("adduser", add_user))
dispatcher.add_handler(CommandHandler("removeuser", remove_user))
dispatcher.add_handler(CommandHandler("listusers", list_users))

# ======== استقبال الملفات والفيديوهات والصور ========
def handle_file(update, context):
    if not is_allowed_user(update):
        return  # تجاهل أي مستخدم غير مسموح له

    msg = update.message
    file_id = None

    if msg.photo:
        sent = context.bot.send_photo(chat_id=BIN_CHANNEL, photo=msg.photo[-1].file_id)
        file_id = sent.photo[-1].file_id
    elif msg.video:
        sent = context.bot.send_video(chat_id=BIN_CHANNEL, video=msg.video.file_id)
        file_id = sent.video.file_id
    elif msg.audio:
        sent = context.bot.send_audio(chat_id=BIN_CHANNEL, audio=msg.audio.file_id)
        file_id = sent.audio.file_id
    elif msg.document:
        sent = context.bot.send_document(chat_id=BIN_CHANNEL, document=msg.document.file_id)
        file_id = sent.document.file_id
    else:
        update.message.reply_text("❌ لم يتم التعرف على الملف.")
        return

    link = f"{PUBLIC_URL}/get_file/{file_id}"
    update.message.reply_text(f"📎 رابط الملف:\n{link}")

dispatcher.add_handler(MessageHandler(Filters.document | Filters.video | Filters.audio | Filters.photo, handle_file))

# ======== مسار التحميل والمشاهدة ========
@app.route("/get_file/<file_id>", methods=["GET"])
def get_file(file_id):
    try:
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
