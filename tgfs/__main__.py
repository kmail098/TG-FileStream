import os
from flask import Flask, request
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Dispatcher, MessageHandler, Filters, CallbackQueryHandler, CommandHandler
from telegram.utils.request import Request
from datetime import datetime, timedelta
import qrcode
from io import BytesIO
from threading import Thread
import time

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
ADMIN_ID = 7485195087
PUBLIC_MODE = False
NOTIFICATIONS_ENABLED = True  # <--- إضافة متغير حالة الإشعارات
activity_log = []
user_files = {}  # {user_id: [file_ids]}

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

# ======== روابط الملفات المؤقتة مع عداد وقت متبقي ========
temporary_links = {}  # {file_id: expire_time}

# ======== دوال المساعدة ========
def add_user(user_id):
    if user_id in allowed_users:
        return False
    added = True
    allowed_users.append(user_id)
    save_allowed_users(allowed_users)
    if added:
        alert_message = f"المستخدم: `{user_id}`\nالعملية: إضافة مستخدم جديد"
        send_alert(alert_message)
    return added

def remove_user(user_id):
    if user_id not in allowed_users:
        return False
    removed = False
    if user_id in allowed_users:
        allowed_users.remove(user_id)
        save_allowed_users(allowed_users)
        removed = True
    if removed:
        alert_message = f"المستخدم: `{user_id}`\nالعملية: حذف مستخدم"
        send_alert(alert_message)
    return removed

def log_activity(msg):
    activity_log.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {msg}")

# ======== دالة الإشعارات الذكية ========
def send_alert(message, file_url=None):
    if NOTIFICATIONS_ENABLED:
        try:
            notification_text = f"🔔 إشعار جديد:\n\n{message}"
            if file_url:
                notification_text += f"\n\n🔗 رابط: {file_url}"
            bot.send_message(chat_id=ADMIN_ID, text=notification_text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            print(f"Failed to send notification: {e}")


# ======== إنشاء QR Code ========
def generate_qr(url):
    qr = qrcode.QRCode(version=1, box_size=5, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

# ======== دوال الوقت المتبقي ========
def format_time_left(expire_time):
    remaining = expire_time - datetime.now()
    if remaining.total_seconds() <= 0:
        return "انتهى"
    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    return f"⏳ {hours} س {minutes} د"

def update_time_left_message(chat_id, message_id, file_id):
    while file_id in temporary_links:
        remaining = format_time_left(temporary_links[file_id])
        try:
            bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📥 تحميل الملف", url=f"{PUBLIC_URL}/get_file/{file_id}"),
                    InlineKeyboardButton("🎬 مشاهدة الفيديو", url=f"{PUBLIC_URL}/get_file/{file_id}"),
                    InlineKeyboardButton(remaining, callback_data="time_left_disabled")
                ]])
            )
        except:
            pass
        time.sleep(60)  # تحديث كل دقيقة

# ======== /start مع لوحة المستخدم ========
def start(update, context):
    user_id = update.message.from_user.id
    if not is_allowed_user(update):
        update.message.reply_text("❌ ليس لديك صلاحية استخدام البوت.")
        return

    text = "<b>🤖 أهلاً بك في البوت الاحترافي!</b>\n"
    text += "<i>جميع الملفات صالحة لمدة 24 ساعة فقط.</i>\n"
    if PUBLIC_MODE:
        text += "\n⚠️ الوضع العام مفعل، كل شخص يمكنه استخدام البوت."
    if NOTIFICATIONS_ENABLED:
        text += "\n🔔 الإشعارات مفعلة للمسؤول."
    else:
        text += "\n🔕 الإشعارات متوقفة للمسؤول."

    if user_id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("🔓 تفعيل Public Mode", callback_data="public_on"),
             InlineKeyboardButton("🔒 إيقاف Public Mode", callback_data="public_off")],
            [InlineKeyboardButton("➕ إضافة مستخدم", callback_data="add_user"),
             InlineKeyboardButton("➖ إزالة مستخدم", callback_data="remove_user")],
            [InlineKeyboardButton("📋 قائمة المستخدمين", callback_data="list_users"),
             InlineKeyboardButton("📝 سجل النشاطات", callback_data="activity_log")],
            [InlineKeyboardButton("🔔 تفعيل الإشعارات", callback_data="notifications_on"),
             InlineKeyboardButton("🔕 إيقاف الإشعارات", callback_data="notifications_off")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        user_recent_files = user_files.get(user_id, [])
        files_text = ""
        if user_recent_files:
            for fid in user_recent_files[-5:]:
                remaining = format_time_left(temporary_links.get(fid))
                files_text += f"- <a href='{PUBLIC_URL}/get_file/{fid}'>ملف</a> | متبقي: {remaining}\n"
        else:
            files_text = "لا توجد ملفات بعد."
        keyboard = [[InlineKeyboardButton("رفع ملف جديد", callback_data="upload_file")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(text + "\n📂 آخر الملفات الخاصة بك:\n" + files_text,
                                  reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# ======== رفع الملفات ========
def handle_file(update, context):
    if not is_allowed_user(update):
        update.message.reply_text("❌ ليس لديك صلاحية رفع الملفات.")
        return

    msg = update.message
    file_id = None
    file_size = 0

    try:
        file_type = ""
        if msg.photo:
            sent = bot.send_photo(chat_id=BIN_CHANNEL, photo=msg.photo[-1].file_id)
            file_id = sent.photo[-1].file_id
            file_size = msg.photo[-1].file_size
            file_type = "صورة"
        elif msg.video:
            sent = bot.send_video(chat_id=BIN_CHANNEL, video=msg.video.file_id)
            file_id = sent.video.file_id
            file_size = msg.video.file_size
            file_type = "فيديو"
        elif msg.audio:
            sent = bot.send_audio(chat_id=BIN_CHANNEL, audio=msg.audio.file_id)
            file_id = sent.audio.file_id
            file_size = msg.audio.file_size
            file_type = "ملف صوتي"
        elif msg.document:
            sent = bot.send_document(chat_id=BIN_CHANNEL, document=msg.document.file_id)
            file_id = sent.document.file_id
            file_size = msg.document.file_size
            file_type = "مستند"
        else:
            update.message.reply_text("❌ لم يتم التعرف على الملف.")
            return

        if file_size > 100 * 1024 * 1024:
            update.message.reply_text("⚠️ الملف كبير جدًا (>100MB)، قد يستغرق رفعه وقت أطول.")

        expire_time = datetime.now() + timedelta(hours=24)
        temporary_links[file_id] = expire_time
        user_files.setdefault(msg.from_user.id, []).append(file_id)

        file_url = f"{PUBLIC_URL}/get_file/{file_id}"
        qr_image = generate_qr(file_url)

        remaining = format_time_left(expire_time)
        keyboard = [[
            InlineKeyboardButton("📥 تحميل الملف", url=file_url),
            InlineKeyboardButton("🎬 مشاهدة الفيديو", url=file_url),
            InlineKeyboardButton(remaining, callback_data="time_left_disabled")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_msg = update.message.reply_photo(qr_image, caption=f"📎 الرابط صالح لمدة 24 ساعة", reply_markup=reply_markup)
        log_activity(f"User {msg.from_user.id} رفع ملف {file_id}")

        alert_message = (
            f"المستخدم: `{msg.from_user.first_name}` ({msg.from_user.id})\n"
            f"العملية: رفع {file_type}\n"
            f"الوقت: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"الحجم: `{file_size / (1024 * 1024):.2f}` ميجابايت"
        )
        send_alert(alert_message, file_url)

        Thread(target=update_time_left_message, args=(update.message.chat_id, sent_msg.message_id, file_id), daemon=True).start()

    except Exception as e:
        update.message.reply_text(f"❌ حدث خطأ: {e}")

# ======== التعامل مع الأزرار ========
def button_handler(update, context):
    query = update.callback_query
    query.answer()
    global PUBLIC_MODE, NOTIFICATIONS_ENABLED

    if query.data == "public_on":
        PUBLIC_MODE = True
        query.edit_message_text("✅ تم تفعيل الوضع العام.")
    elif query.data == "public_off":
        PUBLIC_MODE = False
        query.edit_message_text("✅ تم إيقاف الوضع العام.")
    elif query.data == "notifications_on":
        NOTIFICATIONS_ENABLED = True
        query.edit_message_text("🔔 تم تفعيل الإشعارات.")
    elif query.data == "notifications_off":
        NOTIFICATIONS_ENABLED = False
        query.edit_message_text("🔕 تم إيقاف الإشعارات.")
    elif query.data == "add_user":
        query.edit_message_text("📌 أرسل معرف المستخدم الجديد بعد هذه الرسالة.")
        context.user_data['action'] = 'add_user'
    elif query.data == "remove_user":
        query.edit_message_text("📌 أرسل معرف المستخدم المراد حذفه بعد هذه الرسالة.")
        context.user_data['action'] = 'remove_user'
    elif query.data == "list_users":
        if allowed_users:
            users_text = "\n".join(str(uid) for uid in allowed_users)
            query.edit_message_text(f"📋 قائمة المستخدمين:\n{users_text}")
        else:
            query.edit_message_text("⚠️ لا يوجد أي مستخدم مصرح له حالياً.")
    elif query.data == "activity_log":
        if activity_log:
            logs = "\n".join(activity_log[-20:])
            query.edit_message_text(f"📝 سجل النشاطات (آخر 20):\n{logs}")
        else:
            query.edit_message_text("⚠️ لا توجد أي نشاطات حتى الآن.")

# ======== التعامل مع إدخال المعرف ========
def handle_text(update, context):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        return

    action = context.user_data.get('action')
    if not action:
        return

    try:
        target_id = int(update.message.text.strip())
    except:
        update.message.reply_text("❌ معرف غير صالح.")
        return

    if action == 'add_user':
        if add_user(target_id):
            update.message.reply_text(f"✅ تم إضافة المستخدم: {target_id}")
            log_activity(f"Admin أضاف المستخدم {target_id}")
        else:
            update.message.reply_text(f"⚠️ المستخدم موجود بالفعل: {target_id}")
    elif action == 'remove_user':
        if remove_user(target_id):
            update.message.reply_text(f"✅ تم إزالة المستخدم: {target_id}")
            log_activity(f"Admin حذف المستخدم {target_id}")
        else:
            update.message.reply_text(f"⚠️ المستخدم غير موجود: {target_id}")

    context.user_data['action'] = None

# ======== مسار التحميل / المشاهدة مع عداد الوقت ========
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
        remaining = format_time_left(temporary_links[file_id])

        if file.file_path.endswith(('.mp4', '.mkv', '.mov', '.webm')):
            html_content = f"""
            <html>
            <body style="display:flex;justify-content:center;align-items:center;height:100vh;flex-direction:column;">
            <video width="80%" height="80%" controls autoplay>
              <source src="{file_url}" type="video/mp4">
              المتصفح لا يدعم الفيديو.
            </video>
            <p>{remaining}</p>
            </body>
            </html>
            """
            return html_content, 200
        else:
            return f"<a href='{file_url}'>اضغط هنا لتحميل الملف</a> | {remaining}", 200
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

# ======== ميزة الإحصائيات الجديدة ========
def show_stats(update, context):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        update.message.reply_text("❌ ليس لديك صلاحية الوصول إلى الإحصائيات.")
        return

    total_users_count = len(allowed_users)
    total_files_uploaded = len(temporary_links)
    
    stats_text = (
        "📊 **إحصائيات البوت:**\n\n"
        f"**الوضع العام:** {'مفعل ✅' if PUBLIC_MODE else 'متوقف 🔒'}\n"
        f"**عدد المستخدمين المسجلين:** {total_users_count} مستخدم\n"
        f"**إجمالي الملفات المرفوعة حاليًا:** {total_files_uploaded} ملف\n"
        "*(ملاحظة: هذه الإحصائيات مؤقتة وستُعاد إلى الصفر عند إعادة تشغيل البوت)*"
    )
    
    update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)

# ======== إضافة المعالجات ========
dispatcher.add_handler(MessageHandler(Filters.document | Filters.video | Filters.audio | Filters.photo, handle_file))
dispatcher.add_handler(CallbackQueryHandler(button_handler))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("stats", show_stats))

# ======== تشغيل التطبيق ========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
