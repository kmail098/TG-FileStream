import os
import re
import json
from flask import Flask, request, send_file, Response, redirect
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Dispatcher, MessageHandler, Filters, CallbackQueryHandler, CommandHandler
from telegram.utils.request import Request
from datetime import datetime, timedelta
import qrcode
from io import BytesIO
import requests
from pymongo import MongoClient
import urllib.parse

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
            with open(os.path.join(LANG_DIR, filename), 'r', encoding='utf-8') as f:
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
        lang_code = 'en'  # اللغة الافتراضية
    
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
            bot.send_message(chat_id=BIN_CHANNEL, text=message, parse_mode=ParseMode.MARKDOWN)
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

# ======== رفع الملفات ========
def handle_file(update, context):
    user = update.message.from_user
    user_lang = get_user_lang(user.id, user.language_code)
    if not is_allowed_user(user.id):
        update.message.reply_text(get_string(user_lang, 'no_permission'))
        return

    msg = update.message
    file_id = None
    file_size = 0
    file_name = "unknown_file"
    thumb_id = None
    
    try:
        if not users_collection.find_one({"user_id": user.id}):
            add_user(user.id)
            new_user_alert = get_string('ar', 'new_user_alert', user_id=user.id, user_name=user.first_name)
            send_alert(new_user_alert)
            log_activity(f"New user {user.id} registered")

        file_type = ""
        if msg.photo:
            sent = bot.send_photo(chat_id=BIN_CHANNEL, photo=msg.photo[-1].file_id)
            file_id = sent.photo[-1].file_id
            file_size = msg.photo[-1].file_size
            file_type = "image"
            file_name = msg.photo[-1].file_unique_id + ".jpg"
        elif msg.video:
            sent = bot.send_video(chat_id=BIN_CHANNEL, video=msg.video.file_id)
            file_id = sent.video.file_id
            file_size = msg.video.file_size
            file_type = "video"
            file_name = msg.video.file_name if msg.video.file_name else msg.video.file_unique_id + ".mp4"
            if msg.video.thumb:
                thumb_id = msg.video.thumb.file_id
        
        elif msg.audio:
            sent = bot.send_audio(chat_id=BIN_CHANNEL, audio=msg.audio.file_id)
            file_id = sent.audio.file_id
            file_size = msg.audio.file_size
            file_type = "audio"
            file_name = msg.audio.file_name if msg.audio.file_name else msg.audio.file_unique_id + ".mp3"
        elif msg.document:
            sent = bot.send_document(chat_id=BIN_CHANNEL, document=msg.document.file_id)
            file_id = sent.document.file_id
            file_size = msg.document.file_size
            file_type = "document"
            file_name = msg.document.file_name if msg.document.file_name else msg.document.file_unique_id + ".dat"
        else:
            update.message.reply_text(get_string(user_lang, 'unrecognized_file'))
            return

        if file_size > 100 * 1024 * 1024:
            update.message.reply_text(get_string(user_lang, 'file_too_large'))

        expire_time = datetime.now() + timedelta(hours=24)
        
        links_collection.insert_one({
            "_id": file_id,
            "expire_time": expire_time,
            "file_name": file_name,
            "file_size": file_size,
            "thumb_id": thumb_id
        })

        file_url = f"{PUBLIC_URL}/get_file/{file_id}"
        qr_image = generate_qr(file_url)
        
        caption_text = get_string(user_lang, 'link_caption')
        
        keyboard = [[
            InlineKeyboardButton(get_string(user_lang, 'download_button'), url=file_url),
            InlineKeyboardButton(format_time_left(expire_time), callback_data="time_left_disabled")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_msg = update.message.reply_photo(qr_image, caption=caption_text, reply_markup=reply_markup)
        
        log_activity(f"User {user.id} uploaded a file {file_id}")

        alert_message = get_string('ar', 'upload_alert',
            user_name=user.first_name,
            user_id=user.id,
            file_type=file_type,
            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            size=format_file_size(file_size)
        )
        send_alert(alert_message, file_url)

    except Exception as e:
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
@app.route("/get_file/<file_id>", methods=["GET"])
def get_file(file_id):
    try:
        link_doc = links_collection.find_one({"_id": file_id})
        if not link_doc:
            return get_string('ar', 'link_invalid'), 400

        expire_time = link_doc["expire_time"]
        file_name = link_doc.get("file_name", "الملف")
        file_size = link_doc.get("file_size", 0)
        thumb_id = link_doc.get("thumb_id")

        if datetime.now() > expire_time:
            links_collection.delete_one({"_id": file_id})
            return get_string('ar', 'link_expired'), 400

        file_info = bot.get_file(file_id)
        file_extension = os.path.splitext(file_info.file_path)[1].lower()
        
        is_video = file_extension in ['.mp4', '.mkv', '.mov', '.webm', '.ogg', '.ogv']
        is_audio = file_extension in ['.mp3', '.ogg', '.wav', '.flac']
        is_image = file_extension in ['.jpg', '.jpeg', '.png', '.gif', '.webp']
        is_document = file_extension in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt']
        
        stream_url = f"{PUBLIC_URL}/stream_file/{file_id}"
        thumbnail_url = f"{PUBLIC_URL}/get_thumbnail/{thumb_id}" if thumb_id else ""
        
        # HTML Content
        html_content = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{file_name}</title>
            <link rel="stylesheet" href="https://cdn.plyr.io/3.7.8/plyr.css" />
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
            <style>
                body {{
                    background-color: #0d0d0d;
                    color: #fff;
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    flex-direction: column;
                    padding: 20px;
                }}
                .container {{
                    max-width: 900px;
                    width: 100%;
                    background-color: #1a1a1a;
                    border-radius: 12px;
                    padding: 20px;
                    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                }}
                .info {{
                    margin-bottom: 20px;
                    text-align: center;
                }}
                .info h1 {{
                    font-size: 1.8em;
                    margin: 0;
                    color: #fff;
                }}
                .info p {{
                    font-size: 0.9em;
                    color: #ccc;
                    margin: 5px 0 0;
                }}
                .countdown-timer {{
                    font-size: 1.2em;
                    color: #4CAF50;
                    font-weight: bold;
                    margin-top: 10px;
                }}
                .player-container {{
                    width: 100%;
                    max-width: 800px;
                    height: auto;
                    border-radius: 8px;
                    background-color: #000;
                }}
                .button-group {{
                    display: flex;
                    justify-content: center;
                    gap: 15px;
                    margin-top: 20px;
                }}
                .btn {{
                    display: flex;
                    align-items: center;
                    gap: 5px;
                    padding: 12px 24px;
                    background-color: #383838;
                    color: #fff;
                    text-decoration: none;
                    border-radius: 6px;
                    font-weight: bold;
                    transition: background-color 0.3s;
                }}
                .btn:hover {{
                    background-color: #555;
                }}
                .plyr__controls {{
                    background-color: rgba(26, 26, 26, 0.9) !important;
                }}
                .plyr--full-ui input[type=range] {{
                    color: #e50914 !important;
                }}
                .file-preview {{
                    width: 100%;
                    max-width: 800px;
                    height: auto;
                    max-height: 600px;
                    border-radius: 8px;
                    object-fit: contain;
                    margin-bottom: 20px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="info">
                    <h1>{file_name}</h1>
                    <p>الحجم: {format_file_size(file_size)}</p>
                    <p id="countdown" class="countdown-timer"></p>
                </div>
                {'<div class="player-container">' if is_video or is_audio else ''}
                {'' if is_image or is_document or (not is_video and not is_audio) else '<video id="player" playsinline controls poster="' + thumbnail_url + '">' if is_video else '<audio id="player" controls>'}
                {'' if not (is_video or is_audio) else f'<source src="{stream_url}" type="{"video/" if is_video else "audio/"}{file_extension.strip(".")}"</source>'}
                {'' if not (is_video or is_audio) else '</video>' if is_video else '</audio>'}
                {'</div>' if is_video or is_audio else ''}

                {f'<img src="{stream_url}" class="file-preview" alt="Image Preview">' if is_image else ''}
                {f'<iframe src="https://docs.google.com/gview?url={urllib.parse.quote_plus(stream_url)}&embedded=true" class="file-preview" style="width:100%; height:500px;" frameborder="0"></iframe>' if is_document and file_extension in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'] else ''}
                {f'<iframe src="{stream_url}" class="file-preview" style="width:100%; height:500px;" frameborder="0"></iframe>' if is_document and file_extension in ['.pdf', '.txt'] else ''}

                <div class="button-group">
                    <a href="{stream_url}" class="btn">
                        <i class="fas fa-download"></i>
                        {get_string('ar', 'download_file')}
                    </a>
                    <button class="btn" onclick="copyLink()">
                        <i class="fas fa-share-alt"></i>
                        <span>{get_string('ar', 'share_button')}</span>
                    </button>
                </div>
            </div>

            <script src="https://cdn.plyr.io/3.7.8/plyr.js"></script>
            <script>
                const player = document.getElementById("player");
                if (player) {{
                    const plyrPlayer = new Plyr('#player');
                }}
                var expire_time = new Date("{expire_time.isoformat()}Z");
                var countdown_el = document.getElementById("countdown");

                function updateCountdown() {{
                    var now = new Date();
                    var remaining = expire_time.getTime() - now.getTime();
                    
                    if (remaining <= 0) {{
                        countdown_el.innerHTML = "{get_string('ar', 'link_expired')}";
                        clearInterval(interval);
                        return;
                    }}

                    var hours = Math.floor((remaining / (1000 * 60 * 60)));
                    var minutes = Math.floor((remaining % (1000 * 60 * 60)) / (1000 * 60));
                    var seconds = Math.floor((remaining % (1000 * 60)) / 1000);

                    countdown_el.innerHTML = "{get_string('ar', 'time_left_button', time_left=hours, minutes=minutes)}";
                }}

                function copyLink() {{
                    navigator.clipboard.writeText(window.location.href);
                    alert("تم نسخ الرابط بنجاح!");
                }}

                updateCountdown();
                var interval = setInterval(updateCountdown, 1000);
            </script>
        </body>
        </html>
        """
        return html_content, 200

    except Exception as e:
        return get_string('ar', 'upload_failed', error=e), 400

# ======== مسار تحميل الملف (للملفات غير الفيديو) ========
@app.route("/download_file/<file_id>", methods=["GET"])
def download_file(file_id):
    try:
        link_doc = links_collection.find_one({"_id": file_id})
        if not link_doc:
            return get_string('ar', 'link_invalid'), 400

        expire_time = link_doc["expire_time"]
        file_name = link_doc.get("file_name", "الملف")
        if datetime.now() > expire_time:
            links_collection.delete_one({"_id": file_id})
            return get_string('ar', 'link_expired'), 400
        
        file_info = bot.get_file(file_id)
        telegram_file_url = file_info.file_path
        
        response = requests.get(telegram_file_url)
        return send_file(BytesIO(response.content), as_attachment=True, download_name=file_name)
    except Exception as e:
        return get_string('ar', 'upload_failed', error=e), 400

# ======== مسار تشغيل الفيديو (الخادم الوسيط) ========
@app.route("/stream_file/<file_id>", methods=["GET"])
def stream_file(file_id):
    try:
        link_doc = links_collection.find_one({"_id": file_id})
        if not link_doc or datetime.now() > link_doc["expire_time"]:
            return get_string('ar', 'link_invalid'), 400
            
        file_info = bot.get_file(file_id)
        telegram_file_url = file_info.file_path
        
        range_header = request.headers.get('Range', None)
        if range_header:
            start, end = 0, None
            m = re.search(r'bytes=(\d+)-(\d*)', range_header)
            if m:
                start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
            
            headers = {"Range": range_header}
            r = requests.get(telegram_file_url, headers=headers, stream=True)
            
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
