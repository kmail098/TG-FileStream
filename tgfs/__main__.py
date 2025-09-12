import os
from flask import Flask, request, send_file
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Dispatcher, MessageHandler, Filters, CallbackQueryHandler, CommandHandler
from telegram.utils.request import Request
from datetime import datetime, timedelta
import qrcode
from io import BytesIO
import requests
from pymongo import MongoClient
import urllib.parse
from threading import Thread
import time

# ======== إعداد Flask ========
app = Flask(__name__)

# ======== إعداد البوت ========
BOT_TOKEN = os.getenv("BOT_TOKEN")
BIN_CHANNEL = os.getenv("BIN_CHANNEL")
PUBLIC_URL = os.getenv("PUBLIC_URL")
ADMIN_ID = "7485195087"
MONGO_URI = os.getenv("MONGO_URI")

bot = Bot(token=BOT_TOKEN, request=Request(con_pool_size=8))
dispatcher = Dispatcher(bot, None, workers=0, use_context=True)

# ======== الاتصال بقاعدة البيانات ========
try:
    client = MongoClient(MONGO_URI)
    db = client.get_database("file_stream_db")
    users_collection = db.get_collection("users")
    settings_collection = db.get_collection("settings")
    activity_collection = db.get_collection("activity_log")
    links_collection = db.get_collection("links")
    
    if settings_collection.count_documents({}) == 0:
        settings_collection.insert_one({"_id": "global_settings", "public_mode": False, "notifications_enabled": True})
        
    print("✅ تم الاتصال بقاعدة بيانات MongoDB بنجاح.")
    mongo_client_active = True
except Exception as e:
    print(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
    mongo_client_active = False

# ======== دوال MongoDB المساعدة ========
def get_setting(key):
    if mongo_client_active:
        settings = settings_collection.find_one({"_id": "global_settings"})
        return settings.get(key)
    return False

def update_setting(key, value):
    if mongo_client_active:
        settings_collection.update_one({"_id": "global_settings"}, {"$set": {key: value}})

def get_allowed_users():
    if mongo_client_active:
        return [doc['user_id'] for doc in users_collection.find({"is_allowed": True})]
    return []

def is_allowed_user(user_id):
    if not mongo_client_active: return False
    if get_setting("public_mode"):
        return True
    return users_collection.count_documents({"user_id": user_id, "is_allowed": True}) > 0

def add_user(user_id):
    if mongo_client_active:
        user_doc = users_collection.find_one({"user_id": user_id})
        if user_doc and user_doc.get("is_allowed"):
            return False
        users_collection.update_one({"user_id": user_id}, {"$set": {"is_allowed": True}}, upsert=True)
        return True
    return False

def remove_user(user_id):
    if mongo_client_active:
        users_collection.update_one({"user_id": user_id}, {"$set": {"is_allowed": False}})
        return True
    return False

def log_activity(msg):
    if mongo_client_active:
        activity_collection.insert_one({
            "timestamp": datetime.now(),
            "message": msg
        })

# ======== دوال المساعدة ========
def send_alert(message, file_url=None):
    if get_setting("notifications_enabled"):
        try:
            notification_text = f"🔔 إشعار جديد:\n\n{message}"
            if file_url:
                notification_text += f"\n\n🔗 رابط: {file_url}"
            bot.send_message(chat_id=BIN_CHANNEL, text=notification_text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            print(f"❌ فشل إرسال الإشعار إلى القناة. السبب: {e}")

def generate_qr(url):
    qr = qrcode.QRCode(version=1, box_size=5, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

def format_time_left(expire_time):
    remaining = expire_time - datetime.now()
    if remaining.total_seconds() <= 0:
        return "انتهى"
    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    return f"⏳ {hours} س {minutes} د"

# ======== /start مع لوحة المستخدم ========
def start(update, context):
    user_id = update.message.from_user.id
    if not is_allowed_user(user_id):
        update.message.reply_text("❌ ليس لديك صلاحية استخدام البوت.")
        return

    public_mode = get_setting("public_mode")
    notifications_enabled = get_setting("notifications_enabled")

    text = "<b>🤖 أهلاً بك في البوت الاحترافي!</b>\n"
    text += "<i>جميع الملفات صالحة لمدة 24 ساعة فقط.</i>\n"
    if public_mode:
        text += "\n⚠️ الوضع العام مفعل، كل شخص يمكنه استخدام البوت."
    if notifications_enabled:
        text += "\n🔔 الإشعارات مفعلة للمسؤول وتُرسل إلى قناة الأرشيف."
    else:
        text += "\n🔕 الإشعارات متوقفة للمسؤول."

    if str(user_id) == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("🔓 تفعيل Public Mode", callback_data="public_on") if not public_mode else InlineKeyboardButton("🔒 إيقاف Public Mode", callback_data="public_off")],
            [InlineKeyboardButton("➕ إضافة مستخدم", callback_data="add_user"),
             InlineKeyboardButton("➖ إزالة مستخدم", callback_data="remove_user")],
            [InlineKeyboardButton("📋 قائمة المستخدمين", callback_data="list_users"),
             InlineKeyboardButton("📝 سجل النشاطات", callback_data="activity_log")],
            [InlineKeyboardButton("🔔 تفعيل الإشعارات", callback_data="notifications_on") if not notifications_enabled else InlineKeyboardButton("🔕 إيقاف الإشعارات", callback_data="notifications_off")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        keyboard = [[InlineKeyboardButton("رفع ملف جديد", callback_data="upload_file")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# ======== رفع الملفات ========
def handle_file(update, context):
    user_id = update.message.from_user.id
    if not is_allowed_user(user_id):
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
        
        links_collection.insert_one({
            "_id": file_id,
            "expire_time": expire_time
        })

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

    except Exception as e:
        update.message.reply_text(f"❌ حدث خطأ: {e}")

# ======== التعامل مع الأزرار ========
def button_handler(update, context):
    query = update.callback_query
    query.answer()
    
    if str(query.from_user.id) != ADMIN_ID:
        return

    if query.data == "public_on":
        update_setting("public_mode", True)
        query.edit_message_text("✅ تم تفعيل الوضع العام.")
    elif query.data == "public_off":
        update_setting("public_mode", False)
        query.edit_message_text("✅ تم إيقاف الوضع العام.")
    elif query.data == "notifications_on":
        update_setting("notifications_enabled", True)
        query.edit_message_text("🔔 تم تفعيل الإشعارات.")
    elif query.data == "notifications_off":
        update_setting("notifications_enabled", False)
        query.edit_message_text("🔕 تم إيقاف الإشعارات.")
    elif query.data == "add_user":
        query.edit_message_text("📌 أرسل معرف المستخدم الجديد بعد هذه الرسالة.")
        context.user_data['action'] = 'add_user'
    elif query.data == "remove_user":
        query.edit_message_text("📌 أرسل معرف المستخدم المراد حذفه بعد هذه الرسالة.")
        context.user_data['action'] = 'remove_user'
    elif query.data == "list_users":
        if mongo_client_active:
            allowed_users = get_allowed_users()
            if allowed_users:
                users_text = "\n".join(str(uid) for uid in allowed_users)
                query.edit_message_text(f"📋 قائمة المستخدمين:\n{users_text}")
            else:
                query.edit_message_text("⚠️ لا يوجد أي مستخدم مصرح له حالياً.")
        else:
            query.edit_message_text("❌ لا يمكن الوصول إلى قاعدة البيانات.")
    elif query.data == "activity_log":
        if mongo_client_active:
            logs = activity_collection.find().sort("timestamp", -1).limit(20)
            logs_text = "\n".join([log['message'] for log in logs])
            query.edit_message_text(f"📝 سجل النشاطات (آخر 20):\n{logs_text}")
        else:
            query.edit_message_text("❌ لا يمكن الوصول إلى السجل.")

# ======== التعامل مع إدخال المعرف ========
def handle_text(update, context):
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_ID:
        return

    action = context.user_data.get('action')
    if not action:
        return

    try:
        target_id = int(update.message.text.strip())
    except ValueError:
        update.message.reply_text("❌ معرف غير صالح. الرجاء إرسال رقم.")
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
        link_doc = links_collection.find_one({"_id": file_id})
        if not link_doc:
            return "❌ الرابط غير صالح أو انتهت صلاحيته.", 400

        expire_time = link_doc["expire_time"]
        if datetime.now() > expire_time:
            links_collection.delete_one({"_id": file_id})
            return "❌ الرابط انتهت صلاحيته بعد 24 ساعة.", 400

        file = bot.get_file(file_id)
        file_url = file.file_path
        
        file_extension = os.path.splitext(file_url)[1].lower()

        if file_extension in ['.mp4', '.mkv', '.mov', '.webm']:
            html_content = f"""
            <html>
            <head>
                <style>
                    body {{
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        flex-direction: column;
                        background: #000;
                        color: #fff;
                        font-family: Arial, sans-serif;
                    }}
                </style>
            </head>
            <body>
                <video width="90%" height="90%" controls>
                  <source src="{file_url}" type="video/mp4">
                  المتصفح لا يدعم هذا الفيديو.
                </video>
                <p id="countdown" style="text-align: center; margin-top: 10px;"></p>
                <script>
                    var expire_time = new Date("{expire_time.isoformat()}Z");
                    var countdown_el = document.getElementById("countdown");

                    function updateCountdown() {{
                        var now = new Date();
                        var remaining = expire_time.getTime() - now.getTime();
                        
                        if (remaining <= 0) {{
                            countdown_el.innerHTML = "انتهى";
                            clearInterval(interval);
                            return;
                        }}

                        var hours = Math.floor((remaining / (1000 * 60 * 60)));
                        var minutes = Math.floor((remaining % (1000 * 60 * 60)) / (1000 * 60));
                        var seconds = Math.floor((remaining % (1000 * 60)) / 1000);

                        countdown_el.innerHTML = "⏳ " + hours + " س " + minutes + " د " + seconds + " ث";
                    }}

                    updateCountdown();
                    var interval = setInterval(updateCountdown, 1000);
                </script>
            </body>
            </html>
            """
            return html_content, 200
        else:
            response = requests.get(file_url, stream=True)
            if response.status_code != 200:
                return "❌ فشل تحميل الملف من تيليجرام.", 400
            return send_file(BytesIO(response.content), as_attachment=True, download_name=file_id + file_extension)

    except Exception as e:
        return f"حدث خطأ: {e}", 400

# ======== Webhook ========
@app.route("/", methods=["POST"])
def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
    return "OK", 200

# ======== اختبار Flask ========
@app.route("/test", methods=["GET"])
def test():
    return "Flask يعمل على Vercel ✅", 200

# ======== اختبار الإشعارات ========
@app.route("/test_alert", methods=["GET"])
def test_alert():
    try:
        bot.send_message(chat_id=BIN_CHANNEL, text="✅ هذا إشعار تجريبي ناجح!")
        return "تم إرسال الإشعار التجريبي بنجاح إلى القناة.", 200
    except Exception as e:
        return f"❌ فشل إرسال الإشعار: {e}", 500

# ======== ميزة الإحصائيات الجديدة ========
def show_stats(update, context):
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_ID:
        update.message.reply_text("❌ ليس لديك صلاحية الوصول إلى الإحصائيات.")
        return
    
    if not mongo_client_active:
        update.message.reply_text("❌ لا يمكن الوصول إلى قاعدة البيانات.")
        return
    
    total_users_count = users_collection.count_documents({"is_allowed": True})
    total_activity_logs = activity_collection.count_documents({})
    
    stats_text = (
        "📊 **إحصائيات البوت:**\n\n"
        f"**الوضع العام:** {'مفعل ✅' if get_setting('public_mode') else 'متوقف 🔒'}\n"
        f"**عدد المستخدمين المسجلين:** {total_users_count} مستخدم\n"
        f"**إجمالي عدد الأنشطة:** {total_activity_logs} نشاط\n"
        "*(ملاحظة: هذه الإحصائيات دائمة ومحفوظة في قاعدة البيانات)*"
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
