import os
import re
import json
from flask import Flask, request, send_file, Response, redirect, render_template_string
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Dispatcher, MessageHandler, Filters, CallbackQueryHandler, CommandHandler
from telegram.utils.request import Request
from datetime import datetime, timedelta
import qrcode
from io import BytesIO
import requests
from pymongo import MongoClient
import urllib.parse
import traceback
import urllib3

# ======== إعداد Flask ========
app = Flask(__name__)

# ======== إعداد البوت ========
BOT_TOKEN = os.getenv("BOT_TOKEN")
BIN_CHANNEL = os.getenv("BIN_CHANNEL")
PUBLIC_URL = os.getenv("PUBLIC_URL")
ADMIN_ID = "7485195087"
MONGO_URI = os.getenv("MONGO_URI")

bot = Bot(token=BOT_TOKEN)
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
        settings_collection.insert_one({
            "_id": "global_settings",
            "public_mode": False,
            "notifications_enabled": True,
            "last_cleanup": datetime.now()
        })
        
    print("✅ تم الاتصال بقاعدة بيانات MongoDB بنجاح.")
    mongo_client_active = True
except Exception as e:
    print(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
    mongo_client_active = False

# ======== دوال الترجمة ========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LANG_DIR = os.path.join(BASE_DIR, 'lang')

def load_translations():
    translations = {}
    for filename in os.listdir(LANG_DIR):
        if filename.endswith('.json'):
            lang_code = filename.split('.')[0]
            with open(os.path.join(LANG_DIR, 'ar.json'), 'r', encoding='utf-8') as f:
                translations[lang_code] = json.load(f)
    return translations

TRANSLATIONS = load_translations()
SUPPORTED_LANGUAGES = {'ar': 'العربية', 'en': 'English'}

def get_user_lang(user_id, tg_lang_code):
    if not mongo_client_active:
        return tg_lang_code
        
    user_doc = users_collection.find_one({"user_id": user_id})
    if user_doc and 'preferred_lang' in user_doc:
        return user_doc['preferred_lang']
    
    return tg_lang_code

def get_string(user_lang, key, **kwargs):
    lang_code = user_lang.split('-')[0]
    if lang_code not in TRANSLATIONS:
        lang_code = 'ar'  # اللغة الافتراضية
    
    text = TRANSLATIONS[lang_code].get(key, key)
    return text.format(**kwargs)

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
            full_message = message
            if file_url:
                full_message += f"\n\n🔗 رابط: {file_url}"
            bot.send_message(chat_id=BIN_CHANNEL, text=full_message, parse_mode=ParseMode.MARKDOWN)
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
        return get_string('ar', 'link_expired')
    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    return get_string('ar', 'time_left_button', time_left=hours, minutes=minutes)

def format_file_size(size_in_bytes):
    if size_in_bytes < 1024:
        return f"{size_in_bytes} bytes"
    elif size_in_bytes < 1024 * 1024:
        return f"{size_in_bytes / 1024:.2f} KB"
    else:
        return f"{size_in_bytes / (1024 * 1024):.2f} MB"

# ======== /start مع لوحة المستخدم ========
def start(update, context):
    user = update.message.from_user
    user_lang = get_user_lang(user.id, user.language_code)
    
    if not is_allowed_user(user.id):
        update.message.reply_text(get_string(user_lang, 'no_permission'))
        return

    public_mode = get_setting("public_mode")
    notifications_enabled = get_setting("notifications_enabled")
    
    text = get_string(user_lang, 'welcome_message')
    if public_mode:
        text += "\n" + get_string(user_lang, 'public_mode_on_alert')
    if notifications_enabled:
        text += "\n" + get_string(user_lang, 'notifications_on_alert')
    else:
        text += "\n" + get_string(user_lang, 'notifications_off_alert')

    if str(user.id) == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton(get_string(user_lang, 'public_on') if not public_mode else get_string(user_lang, 'public_off'), callback_data="public_toggle")],
            [InlineKeyboardButton(get_string(user_lang, 'add_user_button'), callback_data="add_user"),
             InlineKeyboardButton(get_string(user_lang, 'remove_user_button'), callback_data="remove_user")],
            [InlineKeyboardButton(get_string(user_lang, 'list_users_button'), callback_data="list_users"),
             InlineKeyboardButton(get_string(user_lang, 'activity_log_button'), callback_data="activity_log")],
            [InlineKeyboardButton(get_string(user_lang, 'notifications_on') if not notifications_enabled else get_string(user_lang, 'notifications_off'), callback_data="notifications_toggle")],
            [InlineKeyboardButton(get_string(user_lang, 'language_button'), callback_data="select_language")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        keyboard = [[InlineKeyboardButton(get_string(user_lang, 'upload_file_button'), callback_data="upload_file")],
                    [InlineKeyboardButton(get_string(user_lang, 'language_button'), callback_data="select_language")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# ======== رفع الملفات (تم التحديث) ========
def handle_file(update, context):
    user = update.message.from_user
    user_lang = get_user_lang(user.id, user.language_code)
    if not is_allowed_user(user.id):
        update.message.reply_text(get_string(user_lang, 'no_permission'))
        return

    msg = update.message
    file_info = None
    file_type = ""
    file_name = "unknown_file"
    thumb_id = None
    
    if msg.photo:
        file_info = msg.photo[-1]
        file_type = "image"
        file_name = f"{file_info.file_unique_id}.jpg"
    elif msg.video:
        file_info = msg.video
        file_type = "video"
        file_name = file_info.file_name if file_info.file_name else f"{file_info.file_unique_id}.mp4"
        if file_info.thumb:
            thumb_id = file_info.thumb.file_id
    elif msg.audio:
        file_info = msg.audio
        file_type = "audio"
        file_name = file_info.file_name if file_info.file_name else f"{file_info.file_unique_id}.mp3"
    elif msg.document:
        file_info = msg.document
        file_type = "document"
        file_name = file_info.file_name if file_info.file_name else f"{file_info.file_unique_id}.dat"
    else:
        update.message.reply_text(get_string(user_lang, 'unrecognized_file'))
        return

    try:
        if not users_collection.find_one({"user_id": user.id}):
            add_user(user.id)
            new_user_alert = get_string('ar', 'new_user_alert', user_id=user.id, user_name=user.first_name)
            send_alert(new_user_alert)
            log_activity(f"New user {user.id} registered")

        # إرسال الملف إلى القناة للحصول على file_id الثابت
        if file_type == "image":
            sent = bot.send_photo(chat_id=BIN_CHANNEL, photo=file_info.file_id)
        elif file_type == "video":
            sent = bot.send_video(chat_id=BIN_CHANNEL, video=file_info.file_id)
        elif file_type == "audio":
            sent = bot.send_audio(chat_id=BIN_CHANNEL, audio=file_info.file_id)
        elif file_type == "document":
            sent = bot.send_document(chat_id=BIN_CHANNEL, document=file_info.file_id)

        # استخراج file_unique_id من الرسالة الأصلية
        file_unique_id = file_info.file_unique_id

        expire_time = datetime.now() + timedelta(hours=24)
        
        links_collection.insert_one({
            "_id": file_unique_id, 
            "file_id": file_info.file_id,
            "expire_time": expire_time,
            "file_name": file_name,
            "file_size": file_info.file_size,
            "thumb_id": thumb_id
        })

        file_url = f"{PUBLIC_URL}/get_file/{file_unique_id}"
        qr_image = generate_qr(file_url)
        
        caption_text = get_string(user_lang, 'link_caption')
        
        keyboard = [[
            InlineKeyboardButton(get_string(user_lang, 'download_button'), url=file_url),
            InlineKeyboardButton(format_time_left(expire_time), callback_data="time_left_disabled")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        update.message.reply_photo(qr_image, caption=caption_text, reply_markup=reply_markup)
        
        log_activity(f"User {user.id} uploaded a file {file_unique_id}")

        alert_message = get_string('ar', 'upload_alert',
            user_name=user.first_name,
            user_id=user.id,
            file_type=file_type,
            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            size=format_file_size(file_info.file_size)
        )
        send_alert(alert_message, file_url=file_url)

    except Exception as e:
        print(f"❌ حدث خطأ في معالجة الملف: {traceback.format_exc()}")
        update.message.reply_text(get_string(user_lang, 'upload_failed', error=e))


# ======== التعامل مع الأزرار ========
def button_handler(update, context):
    query = update.callback_query
    query.answer()
    
    user_id = query.from_user.id
    user_lang = get_user_lang(user_id, query.from_user.language_code)

    if query.data == "select_language":
        keyboard = [[InlineKeyboardButton(name, callback_data=f"set_lang_{code}")] for code, name in SUPPORTED_LANGUAGES.items()]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(get_string(user_lang, 'choose_language_message'), reply_markup=reply_markup)
        return
    
    if query.data.startswith("set_lang_"):
        new_lang_code = query.data.split("_")[2]
        if mongo_client_active:
            users_collection.update_one({"user_id": user_id}, {"$set": {"preferred_lang": new_lang_code}}, upsert=True)
            new_lang_name = SUPPORTED_LANGUAGES.get(new_lang_code, 'Unknown')
            query.edit_message_text(f"✅ تم تغيير لغة البوت إلى {new_lang_name}.")
        else:
            query.edit_message_text(get_string(user_lang, 'no_db_access'))
        return

    if str(user_id) != ADMIN_ID:
        return

    if query.data == "public_toggle":
        is_public = get_setting("public_mode")
        update_setting("public_mode", not is_public)
        query.edit_message_text(get_string(user_lang, 'public_mode_enabled' if not is_public else 'public_mode_disabled'))
    elif query.data == "notifications_toggle":
        is_enabled = get_setting("notifications_enabled")
        update_setting("notifications_enabled", not is_enabled)
        query.edit_message_text(get_string(user_lang, 'notifications_enabled' if not is_enabled else 'notifications_disabled'))
    elif query.data == "add_user":
        query.edit_message_text(get_string(user_lang, 'send_user_id'))
        context.user_data['action'] = 'add_user'
    elif query.data == "remove_user":
        query.edit_message_text(get_string(user_lang, 'remove_user_id'))
        context.user_data['action'] = 'remove_user'
    elif query.data == "list_users":
        if mongo_client_active:
            allowed_users = get_allowed_users()
            if allowed_users:
                users_text = "\n".join(str(uid) for uid in allowed_users)
                query.edit_message_text(f"{get_string(user_lang, 'list_users_button')}:\n{users_text}")
            else:
                query.edit_message_text(get_string(user_lang, 'no_allowed_users'))
        else:
            query.edit_message_text(get_string(user_lang, 'no_db_access'))
    elif query.data == "activity_log":
        if mongo_client_active:
            logs = activity_collection.find().sort("timestamp", -1).limit(20)
            logs_text = "\n".join([log['message'] for log in logs])
            query.edit_message_text(f"{get_string(user_lang, 'activity_log_button')}\n{logs_text}")
        else:
            query.edit_message_text(get_string(user_lang, 'no_db_access'))

# ======== التعامل مع إدخال المعرف ========
def handle_text(update, context):
    user = update.message.from_user
    user_lang = get_user_lang(user.id, user.language_code)
    if str(user.id) != ADMIN_ID:
        return

    action = context.user_data.get('action')
    if not action:
        return

    try:
        target_id = int(update.message.text.strip())
    except ValueError:
        update.message.reply_text(get_string(user_lang, 'invalid_user_id'))
        return

    if action == 'add_user':
        if add_user(target_id):
            update.message.reply_text(get_string(user_lang, 'user_added', user_id=target_id))
            log_activity(f"Admin added user {target_id}")
        else:
            update.message.reply_text(get_string(user_lang, 'user_exists', user_id=target_id))
    elif action == 'remove_user':
        if remove_user(target_id):
            update.message.reply_text(get_string(user_lang, 'user_removed', user_id=target_id))
            log_activity(f"Admin removed user {target_id}")
        else:
            update.message.reply_text(get_string(user_lang, 'user_not_found', user_id=target_id))

    context.user_data['action'] = None

# ======== مسار صفحة الملفات ========
@app.route("/get_file/<file_unique_id>", methods=["GET"])
def get_file(file_unique_id):
    try:
        print(f"Received request for file unique ID: {file_unique_id}")
        
        link_doc = links_collection.find_one({"_id": file_unique_id})
        
        if not link_doc:
            print(f"File unique ID {file_unique_id} not found in database.")
            return get_string('ar', 'link_invalid'), 400

        expire_time = link_doc["expire_time"]
        file_name = link_doc.get("file_name", "الملف")
        file_id = link_doc["file_id"]
        
        if datetime.now() > expire_time:
            links_collection.delete_one({"_id": file_unique_id})
            print(f"Link for file unique ID {file_unique_id} has expired.")
            return get_string('ar', 'link_expired'), 400

        file_info = bot.get_file(file_id)
        file_extension = os.path.splitext(file_info.file_path)[1].lower()
        
        is_video = file_extension in ['.mp4', '.mkv', '.mov', '.webm', '.ogv']
        is_audio = file_extension in ['.mp3', '.ogg', '.wav', '.flac']
        is_image = file_extension in ['.jpg', '.jpeg', '.png', '.gif', '.webp']
        is_document = file_extension in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt']
        
        mime_type = "video/mp4" if is_video else "audio/mpeg" if is_audio else "image/jpeg" if is_image else "application/octet-stream"
        
        return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ file_name }}</title>
    <link rel="stylesheet" href="https://cdn.plyr.io/3.7.8/plyr.css" />
    <style>
        body {
            margin: 0;
            padding: 0;
            height: 100vh;
            width: 100%;
            display: flex;
            justify-content: center;
            align-items: center;
            background-image: url('https://upload.wikimedia.org/wikipedia/commons/thumb/0/00/Flag_of_Palestine_%28ISO_3166-2%29.svg/1200px-Flag_of_Palestine_%28ISO_3166-2%29.svg.png');
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
            animation: fadein 2s ease-in-out;
            position: relative;
        }
        
        body::before {
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.65);
            backdrop-filter: blur(5px);
            z-index: -1;
        }

        @keyframes fadein {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        .player-container {
            width: 90%;
            max-width: 900px;
            background: rgba(0,0,0,0.65);
            border-radius: 20px;
            padding: 20px;
            box-shadow: 0 0 30px rgba(0,0,0,0.7);
            z-index: 1;
        }

        video, audio, iframe, img {
            width: 100%;
            border-radius: 15px;
        }

        h2 {
            text-align: center;
            font-family: 'Arial', sans-serif;
            color: #fff;
            margin-bottom: 15px;
            text-shadow: 2px 2px 8px #000;
        }
    </style>
</head>
<body>
    <div class="player-container">
        <h2>📽️ {{ file_name }}</h2>
        {% if file_type in ["video", "audio"] %}
        <video controls crossorigin playsinline>
            <source src="/stream_file/{{ file_unique_id }}" type="{{ mime_type }}">
        </video>
        {% elif file_type == "image" %}
        <img src="/stream_file/{{ file_unique_id }}" alt="Image">
        {% elif file_type == "document" %}
        <iframe src="/stream_file/{{ file_unique_id }}" style="height: 500px;"></iframe>
        {% else %}
        <p style="color:white;">File preview not supported. <a href="/stream_file/{{ file_unique_id }}" style="color:#00ffea;">Download here</a>.</p>
        {% endif %}
    </div>
    <script src="https://cdn.plyr.io/3.7.8/plyr.polyfilled.js"></script>
    <script>
        const player = new Plyr('video, audio', { controls: ['play', 'progress', 'current-time', 'mute', 'volume', 'fullscreen'] });
    </script>
</body>
</html>
""", file_name=file_name, file_unique_id=file_unique_id, mime_type=mime_type, file_type=file_type)

    except Exception as e:
        print(f"An error occurred in get_file: {traceback.format_exc()}")
        return get_string('ar', 'upload_failed', error=e), 400

# ======== مسار تشغيل الفيديو/التحميل (الخادم الوسيط) ========
@app.route("/stream_file/<file_unique_id>", methods=["GET"])
def stream_file(file_unique_id):
    try:
        print(f"Received stream request for file unique ID: {file_unique_id}")
        link_doc = links_collection.find_one({"_id": file_unique_id})
        
        if not link_doc or datetime.now() > link_doc["expire_time"]:
            print(f"Stream link for file unique ID {file_unique_id} is invalid or expired.")
            return get_string('ar', 'link_invalid'), 400
            
        file_id = link_doc["file_id"]
        file_info = bot.get_file(file_id)
        telegram_file_url = file_info.file_path
        
        is_download_request = request.args.get('download', 'false').lower() == 'true'
        file_name = link_doc.get("file_name", "file")
        
        if is_download_request:
            headers = {
                'Content-Disposition': f'attachment; filename="{file_name}"',
                'Content-Type': 'application/octet-stream'
            }
            r = requests.get(telegram_file_url, stream=True)
            return Response(r.iter_content(chunk_size=8192), headers=headers, status=r.status_code)

        range_header = request.headers.get('Range', None)
        if range_header:
            r = requests.get(telegram_file_url, headers={"Range": range_header}, stream=True)
            response = Response(r.iter_content(chunk_size=8192), status=r.status_code)
            response.headers.update(r.headers)
            return response
        else:
            def generate_stream():
                with requests.get(telegram_file_url, stream=True) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=8192):
                        yield chunk
            return Response(generate_stream(), mimetype='video/mp4')
            
    except Exception as e:
        print(f"An error occurred in stream_file: {traceback.format_exc()}")
        return get_string('ar', 'upload_failed', error=e), 400

# ======== مسار عرض الصورة المصغرة ========
@app.route("/get_thumbnail/<thumb_id>", methods=["GET"])
def get_thumbnail(thumb_id):
    try:
        if not thumb_id:
            return get_string('ar', 'no_thumbnail'), 404
        
        file_info = bot.get_file(thumb_id)
        telegram_file_url = file_info.file_path
        
        response = requests.get(telegram_file_url, stream=True)
        return Response(response.iter_content(chunk_size=8192), content_type=response.headers['Content-Type'])
    except Exception as e:
        return get_string('ar', 'upload_failed', error=e), 400

# ======== وظيفة الحذف التلقائي الجديدة ========
def cleanup_expired_links():
    if not mongo_client_active:
        print(get_string('ar', 'no_db_access'))
        return
    
    current_time = datetime.now()
    result = links_collection.delete_many({"expire_time": {"$lt": current_time}})
    if result.deleted_count > 0:
        print(f"✅ تم حذف {result.deleted_count} رابط منتهي الصلاحية.")
        log_activity(f"تم حذف {result.deleted_count} رابط منتهي الصلاحية.")
        send_alert(get_string('ar', 'cleanup_success', count=result.deleted_count), file_url=None)
    else:
        print("ℹ️ لا توجد روابط منتهية الصلاحية ليتم حذفها.")

# ======== وظيفة التحقق والتشغيل ========
def check_and_run_cleanup():
    if not mongo_client_active:
        return
        
    settings = settings_collection.find_one({"_id": "global_settings"})
    last_cleanup = settings.get("last_cleanup", datetime.now() - timedelta(hours=2))
    
    if datetime.now() - last_cleanup > timedelta(hours=1):
        cleanup_expired_links()
        update_setting("last_cleanup", datetime.now())

# ======== Webhook ========
@app.route("/", methods=["POST"])
def webhook():
    if request.method == "POST":
        check_and_run_cleanup()
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
    return "OK", 200

# ======== اختبار Flask ========
@app.route("/test", methods=["GET"])
def test():
    check_and_run_cleanup()
    return "Flask يعمل على Vercel ✅", 200

# ======== اختبار الإشعارات ========
@app.route("/test_alert", methods=["GET"])
def test_alert():
    try:
        bot.send_message(chat_id=BIN_CHANNEL, text=get_string('ar', 'test_alert_success'))
        return "تم إرسال الإشعار التجريبي بنجاح إلى القناة.", 200
    except Exception as e:
        return f"❌ فشل إرسال الإشعار: {e}", 500

# ======== ميزة الإحصائيات الجديدة ========
def show_stats(update, context):
    user = update.message.from_user
    user_lang = get_user_lang(user.id, user.language_code)
    if str(user.id) != ADMIN_ID:
        update.message.reply_text(get_string(user_lang, 'stats_no_permission'))
        return
    
    if not mongo_client_active:
        update.message.reply_text(get_string(user_lang, 'no_db_access'))
        return
    
    total_users_count = users_collection.count_documents({"is_allowed": True})
    total_activity_logs = activity_collection.count_documents({})
    
    public_mode_status = get_string(user_lang, 'stats_on' if get_setting('public_mode') else 'stats_off')
    
    stats_text = get_string(user_lang, 'stats_text',
        public_mode=public_mode_status,
        user_count=total_users_count,
        activity_count=total_activity_logs
    )
    
    update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)

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
