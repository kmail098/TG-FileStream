# main.py (مُحسّن - نسخة كاملة)
import os
from flask import Flask, request, send_file, Response, jsonify
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Dispatcher, MessageHandler, Filters, CallbackQueryHandler, CommandHandler
from telegram.utils.request import Request
from datetime import datetime, timedelta
import qrcode
from io import BytesIO
import requests
from pymongo import MongoClient
from threading import Thread
import time
import traceback
from dashboard import init_dashboard

# ======== إعداد Flask ========
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "1qaz@xsw2$3edc@vfr4")
app.config["ADMIN_PASS"] = os.getenv("ADMIN_PASS", "0plm$nko9$8ijb")

# ======== إعداد المتغيرات والـ Bot ========
BOT_TOKEN = os.getenv("BOT_TOKEN")
BIN_CHANNEL = os.getenv("BIN_CHANNEL")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7485195087"))
MONGO_URI = os.getenv("MONGO_URI", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير مضبوط في المتغيرات البيئية")

bot = Bot(token=BOT_TOKEN, request=Request(con_pool_size=8))
dispatcher = Dispatcher(bot, None, workers=0, use_context=True)

# ======== محاولة الاتصال بقاعدة البيانات ========
mongo_client_active = False
client = None
links_collection = None
users_collection = None
settings_collection = None
activity_collection = None

try:
    if MONGO_URI:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.server_info()  # raise if cannot connect
        db = client.get_database("file_stream_db")
        links_collection = db.get_collection("links")
        users_collection = db.get_collection("users")
        settings_collection = db.get_collection("settings")
        activity_collection = db.get_collection("activity_log")
        # ensure settings doc
        if settings_collection.count_documents({}) == 0:
            settings_collection.insert_one({"_id": "global_settings", "public_mode": False, "notifications_enabled": True})
        mongo_client_active = True
        print("✅ MongoDB متصل.")
    else:
        print("⚠️ MONGO_URI غير مضبوط — العمل سيكون على الذاكرة فقط.")
except Exception as e:
    print("⚠️ فشل الاتصال بـ MongoDB:", e)
    mongo_client_active = False

# ======== بنى بيانات داخلية (fallback) ========
# سوف نخزن في الذاكرة نسخة من الروابط للسرعة، ونزامنها مع DB عند الإمكان
# structure: links_memory[file_id] = {expire_time, file_name, file_size, uploader, views, expired}
links_memory = {}

# load from DB at startup (if possible)
if mongo_client_active:
    try:
        for doc in links_collection.find({"expired": {"$ne": True}}):
            fid = doc["_id"]
            links_memory[fid] = {
                "expire_time": doc.get("expire_time"),
                "file_name": doc.get("file_name"),
                "file_size": doc.get("file_size"),
                "uploader": doc.get("uploader"),
                "views": doc.get("views", 0),
                "expired": doc.get("expired", False)
            }
        print("✅ تم استرجاع روابط غير منتهية من MongoDB")
    except Exception as e:
        print("⚠️ خطأ عند استرجاع links:", e)

# ======== دوال مساعدة لقاعدة البيانات والضبط ========
def get_setting(key):
    if not mongo_client_active:
        return False
    doc = settings_collection.find_one({"_id": "global_settings"})
    return doc.get(key) if doc else False

def update_setting(key, value):
    if not mongo_client_active:
        return
    settings_collection.update_one({"_id": "global_settings"}, {"$set": {key: value}}, upsert=True)

def log_activity(msg):
    try:
        if mongo_client_active:
            activity_collection.insert_one({"timestamp": datetime.now(), "message": msg})
        else:
            print(f"[LOG] {datetime.now()} - {msg}")
    except Exception as e:
        print("⚠️ خطأ سجّل النشاط:", e)

def add_user_to_db(user_id):
    try:
        if not mongo_client_active:
            return False
        users_collection.update_one({"user_id": int(user_id)}, {"$set": {"user_id": int(user_id), "is_allowed": True}}, upsert=True)
        return True
    except Exception as e:
        print("⚠️ خطأ إضافة مستخدم:", e)
        return False

def is_allowed_user(user_id):
    # إذا Mongo غير متاح، فقط الأدمن مسموح (يمكنك تغيير هذا السلوك)
    if not mongo_client_active:
        return int(user_id) == ADMIN_ID
    if get_setting("public_mode"):
        return True
    return users_collection.count_documents({"user_id": int(user_id), "is_allowed": True}) > 0

# ======== دوال QR ووقت متبقي ========
def generate_qr_bytes(url):
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

def format_time_left(expire_time):
    if not expire_time:
        return "غير معروف"
    remaining = expire_time - datetime.now()
    if remaining.total_seconds() <= 0:
        return "⛔ انتهت"
    days = remaining.days
    hours, rem = divmod(int(remaining.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)
    if days > 0:
        return f"⏳ {days}ي {hours}س {minutes}د"
    return f"⏳ {hours}س {minutes}د {seconds}ث"

# ======== تحديث زر الوقت في رسالة تليجرام كل 30 ثانية ========
def background_update_button(chat_id, message_id, file_id, stop_on_expire=True, interval=30):
    try:
        while True:
            info = links_memory.get(file_id)
            if not info:
                break
            expire = info.get("expire_time")
            if expire and datetime.now() > expire:
                # mark expired
                try:
                    if mongo_client_active:
                        links_collection.update_one({"_id": file_id}, {"$set": {"expired": True}})
                except Exception:
                    pass
                try:
                    del links_memory[file_id]
                except KeyError:
                    pass
                break
            remaining = format_time_left(expire)
            keyboard = [
                [InlineKeyboardButton("📥 تحميل", url=f"{PUBLIC_URL}/get_file/{file_id}"),
                 InlineKeyboardButton("🎬 مشاهدة", url=f"{PUBLIC_URL}/get_file/{file_id}")],
                [InlineKeyboardButton(f"⏱ {remaining}", callback_data="time_left_disabled")]
            ]
            try:
                bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception:
                # ممكن يكون المستخدم حذف الرسالة أو لا يملك صلاحية التعديل، تجاهل
                pass
            time.sleep(interval)
    except Exception as e:
        print("⚠️ خطأ في background_update_button:", e, traceback.format_exc())

# ======== /start handler ========
def start(update, context):
    try:
        user_id = update.message.from_user.id
        # تسجيل مستخدم جديد إن لم يكن مسجلاً (خيار)
        if mongo_client_active and not users_collection.find_one({"user_id": user_id}):
            add_user_to_db(user_id)
            log_activity(f"New user registered: {user_id}")

        if not is_allowed_user(user_id):
            update.message.reply_text("❌ ليس لديك صلاحية استخدام البوت.")
            return

        public_mode = get_setting("public_mode") if mongo_client_active else False
        notifications_enabled = get_setting("notifications_enabled") if mongo_client_active else True

        text = "<b>🤖 أهلاً بك في البوت الاحترافي!</b>\n"
        text += "<i>جميع الملفات صالحة لمدة 24 ساعة فقط.</i>\n"
        if public_mode:
            text += "\n⚠️ الوضع العام مفعل، كل شخص يمكنه استخدام البوت."
        text += "\n" + ("🔔 الإشعارات مفعلة." if notifications_enabled else "🔕 الإشعارات متوقفة.")

        if user_id == ADMIN_ID:
            keyboard = [
                [InlineKeyboardButton("🔓 تفعيل Public Mode", callback_data="public_on") if not public_mode else InlineKeyboardButton("🔒 إيقاف Public Mode", callback_data="public_off")],
                [InlineKeyboardButton("➕ إضافة مستخدم", callback_data="add_user"), InlineKeyboardButton("➖ إزالة مستخدم", callback_data="remove_user")],
                [InlineKeyboardButton("📋 قائمة المستخدمين", callback_data="list_users"), InlineKeyboardButton("📝 سجل النشاطات", callback_data="activity_log")],
                [InlineKeyboardButton("🔔 تفعيل الإشعارات", callback_data="notifications_on") if not notifications_enabled else InlineKeyboardButton("🔕 إيقاف الإشعارات", callback_data="notifications_off")]
            ]
            update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            return

        # عرض آخر 5 ملفات إذا وجدت
        files = [fid for fid, info in links_memory.items() if info.get("uploader") == user_id]
        last5 = files[-5:]
        if not last5:
            keyboard = [[InlineKeyboardButton("رفع ملف جديد", callback_data="upload_file")]]
            update.message.reply_text(text + "\n\n📂 لا توجد ملفات لديك بعد.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            return

        message = text + "\n\n📂 آخر ملفاتك:\n"
        buttons = []
        for fid in last5:
            info = links_memory.get(fid, {})
            time_text = format_time_left(info.get("expire_time"))
            views = info.get("views", 0)
            size_mb = f"{info.get('file_size',0)/(1024*1024):.2f}"
            message += f"- {info.get('file_name','ملف')} | {size_mb}MB | {views} مشاهدات | {time_text}\n"
            buttons.append([InlineKeyboardButton("📥 تحميل", url=f"{PUBLIC_URL}/get_file/{fid}"),
                            InlineKeyboardButton("🎬 مشاهدة", url=f"{PUBLIC_URL}/get_file/{fid}"),
                            InlineKeyboardButton(time_text, callback_data="time_left_disabled")])
        update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
    except Exception as e:
        print("خطأ في start:", e, traceback.format_exc())

# ======== upload / handle file ========
def handle_file(update, context):
    try:
        user_id = update.message.from_user.id
        if not is_allowed_user(user_id):
            update.message.reply_text("❌ ليس لديك صلاحية رفع الملفات.")
            return

        msg = update.message
        file_id = None
        file_size = 0
        file_name = "file"

        # Determine file and forward to BIN channel (archive)
        if msg.photo:
            sent = bot.send_photo(chat_id=BIN_CHANNEL, photo=msg.photo[-1].file_id)
            file_id = sent.photo[-1].file_id
            file_size = sent.photo[-1].file_size or 0
            file_name = msg.photo[-1].file_unique_id + ".jpg"
            ftype = "صورة"
        elif msg.video:
            sent = bot.send_video(chat_id=BIN_CHANNEL, video=msg.video.file_id)
            file_id = sent.video.file_id
            file_size = sent.video.file_size or 0
            file_name = msg.video.file_name or (msg.video.file_unique_id + ".mp4")
            ftype = "فيديو"
        elif msg.audio:
            sent = bot.send_audio(chat_id=BIN_CHANNEL, audio=msg.audio.file_id)
            file_id = sent.audio.file_id
            file_size = sent.audio.file_size or 0
            file_name = msg.audio.file_name or (msg.audio.file_unique_id + ".mp3")
            ftype = "صوت"
        elif msg.document:
            sent = bot.send_document(chat_id=BIN_CHANNEL, document=msg.document.file_id)
            file_id = sent.document.file_id
            file_size = sent.document.file_size or 0
            file_name = msg.document.file_name or (msg.document.file_unique_id + ".dat")
            ftype = "مستند"
        else:
            update.message.reply_text("❌ لم يتم التعرف على الملف.")
            return

        if file_size > 100 * 1024 * 1024:
            update.message.reply_text("⚠️ الملف كبير جدًا (>100MB)، قد يستغرق رفعه وقت أطول.")

        expire_time = datetime.now() + timedelta(hours=24)

        # حفظ في DB وذاكرة
        links_memory[file_id] = {
            "expire_time": expire_time,
            "file_name": file_name,
            "file_size": file_size,
            "uploader": user_id,
            "views": 0,
            "expired": False
        }
        if mongo_client_active:
            try:
                links_collection.update_one({"_id": file_id}, {"$set": {
                    "expire_time": expire_time,
                    "file_name": file_name,
                    "file_size": file_size,
                    "uploader": user_id,
                    "views": 0,
                    "expired": False
                }}, upsert=True)
            except Exception as e:
                print("⚠️ خطأ حفظ الروابط في Mongo:", e)

        file_url = f"{PUBLIC_URL}/get_file/{file_id}"
        qr_bytes = generate_qr_bytes(file_url)
        remaining = format_time_left(expire_time)

        keyboard = [
            [InlineKeyboardButton("📥 تحميل", url=file_url), InlineKeyboardButton("🎬 مشاهدة", url=file_url)],
            [InlineKeyboardButton(f"⏱ {remaining}", callback_data="time_left_disabled")]
        ]
        sent_msg = update.message.reply_photo(qr_bytes, caption=f"📎 الرابط صالح لمدة 24 ساعة", reply_markup=InlineKeyboardMarkup(keyboard))
        log_activity(f"User {user_id} uploaded {file_id}")

        # إشعار إلى القناة (alert)
        alert = f"المستخدم: `{msg.from_user.first_name}` ({user_id})\nرفع: {ftype}\nالاسم: `{file_name}`\nالحجم: `{file_size/(1024*1024):.2f} MB`"
        try:
            if get_setting("notifications_enabled") if mongo_client_active else True:
                if BIN_CHANNEL:
                    bot.send_message(chat_id=BIN_CHANNEL, text=alert + f"\n{file_url}", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            print("⚠️ خطأ في إرسال إشعار:", e)

        # start background thread to update time button
        Thread(target=background_update_button, args=(sent_msg.chat_id, sent_msg.message_id, file_id), daemon=True).start()

    except Exception as e:
        print("خطأ في handle_file:", e, traceback.format_exc())
        try:
            update.message.reply_text(f"❌ حدث خطأ: {e}")
        except Exception:
            pass

# ======== زرّات الرد (admin-only actions) ========
def button_handler(update, context):
    try:
        query = update.callback_query
        query.answer()
        if str(query.from_user.id) != str(ADMIN_ID):
            # للمستخدمين العاديين لا نسمح بالتحكم الاداري هنا
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
            if not mongo_client_active:
                query.edit_message_text("❌ لا يمكن الوصول إلى قاعدة البيانات.")
                return
            allowed = [int(d['user_id']) for d in users_collection.find({"is_allowed": True})]
            if allowed:
                query.edit_message_text("📋 قائمة المستخدمين:\n" + "\n".join(str(u) for u in allowed))
            else:
                query.edit_message_text("⚠️ لا يوجد مستخدمون مصرح لهم.")
        elif query.data == "activity_log":
            if not mongo_client_active:
                query.edit_message_text("❌ لا يمكن الوصول إلى السجل.")
                return
            docs = activity_collection.find().sort("timestamp", -1).limit(20)
            text = "\n".join([f"{d['timestamp'].strftime('%Y-%m-%d %H:%M:%S')} - {d['message']}" for d in docs])
            query.edit_message_text("📝 سجل النشاطات (آخر 20):\n" + (text or "لا توجد سجلات."))
    except Exception as e:
        print("خطأ في button_handler:", e, traceback.format_exc())

# ======== نصوص تعامُل الادمن لإضافة/حذف مستخدم ========
def handle_text(update, context):
    try:
        user_id = update.message.from_user.id
        if str(user_id) != str(ADMIN_ID):
            return
        action = context.user_data.get('action')
        if not action:
            return
        try:
            target = int(update.message.text.strip())
        except Exception:
            update.message.reply_text("❌ معرف غير صالح. الرجاء إرسال رقم.")
            return
        if action == 'add_user':
            add_user_to_db(target)
            update.message.reply_text(f"✅ تم إضافة المستخدم: {target}")
            log_activity(f"Admin أضاف المستخدم {target}")
        elif action == 'remove_user':
            if mongo_client_active:
                users_collection.update_one({"user_id": target}, {"$set": {"is_allowed": False}})
                update.message.reply_text(f"✅ تم إزالة المستخدم: {target}")
                log_activity(f"Admin حذف المستخدم {target}")
            else:
                update.message.reply_text("❌ MongoDB غير متاح.")
        context.user_data['action'] = None
    except Exception as e:
        print("خطأ في handle_text:", e, traceback.format_exc())

# ======== endpoint لإرجاع وقت متبقي ومشاهدات (JSON) ========
@app.route("/time_left/<file_id>", methods=["GET"])
def time_left(file_id):
    try:
        info = links_memory.get(file_id)
        if not info and mongo_client_active:
            doc = links_collection.find_one({"_id": file_id})
            if doc and not doc.get("expired", False):
                info = {
                    "expire_time": doc.get("expire_time"),
                    "file_name": doc.get("file_name"),
                    "file_size": doc.get("file_size"),
                    "views": doc.get("views", 0)
                }
                # cache
                links_memory[file_id] = info
        if not info:
            return jsonify({"ok": False, "error": "not_found"}), 404
        remaining = format_time_left(info.get("expire_time"))
        views = info.get("views", 0)
        return jsonify({"ok": True, "remaining": remaining, "views": views})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ======== QR route ========
@app.route("/qr/<file_id>", methods=["GET"])
def qr_route(file_id):
    try:
        url = f"{PUBLIC_URL}/get_file/{file_id}"
        img = generate_qr_bytes(url)
        return Response(img.getvalue(), mimetype="image/png")
    except Exception as e:
        return f"حدث خطأ: {e}", 400

# ======== صفحة مشاهدة الملف (احترافية باستخدام Plyr.js) ========
@app.route("/get_file/<file_id>", methods=["GET"])
def get_file_view(file_id):
    try:
        info = links_memory.get(file_id)
        if not info and mongo_client_active:
            doc = links_collection.find_one({"_id": file_id})
            if doc and not doc.get("expired", False):
                info = {
                    "expire_time": doc.get("expire_time"),
                    "file_name": doc.get("file_name"),
                    "file_size": doc.get("file_size"),
                    "uploader": doc.get("uploader"),
                    "views": doc.get("views", 0)
                }
                links_memory[file_id] = info

        if not info:
            return "❌ الرابط غير صالح أو انتهت صلاحيته.", 400

        if info.get("expire_time") and datetime.now() > info.get("expire_time"):
            # mark expired
            if mongo_client_active:
                links_collection.update_one({"_id": file_id}, {"$set": {"expired": True}})
            try:
                del links_memory[file_id]
            except KeyError:
                pass
            return "❌ الرابط انتهت صلاحيته بعد 24 ساعة.", 400

        # increment views
        info["views"] = info.get("views", 0) + 1
        if mongo_client_active:
            try:
                links_collection.update_one({"_id": file_id}, {"$inc": {"views": 1}})
            except Exception:
                pass

        # get telegram file url
        tgfile = bot.get_file(file_id)
        file_url = tgfile.file_path
        ext = os.path.splitext(file_url)[1].lower()
        is_video = ext in [".mp4", ".mkv", ".mov", ".webm", ".ogg", ".ogv"]

        size_mb = f"{info.get('file_size',0)/(1024*1024):.2f}"
        remaining = format_time_left(info.get("expire_time"))
        views = info.get("views", 0)

        if is_video:
            html = f"""
            <!doctype html>
            <html>
            <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="width=device-width,initial-scale=1" />
                <title>{info.get('file_name')}</title>
                <link href="https://cdn.plyr.io/3.7.8/plyr.css" rel="stylesheet" />
                <style>
                  body{{background:#0b1221;color:#e6eef8;font-family:Arial,Helvetica,sans-serif;margin:0;padding:20px;}}
                  .container{{max-width:1100px;margin:0 auto}}
                  .meta{{display:flex;justify-content:space-between;align-items:center;margin:10px 0;gap:10px;flex-wrap:wrap}}
                  .btn{{background:#1f6feb;color:#fff;padding:8px 12px;border-radius:8px;text-decoration:none}}
                  .time{{background:#2b2f3a;padding:6px 10px;border-radius:8px}}
                </style>
            </head>
            <body>
              <div class="container">
                <div id="player-wrap">
                  <video id="player" playsinline controls crossorigin>
                    <source src="{PUBLIC_URL}/stream_video/{file_id}" type="video/mp4" />
                  </video>
                </div>

                <div class="meta">
                  <div>الحجم: {size_mb} MB &nbsp; • &nbsp; المشاهدات: <span id="views">{views}</span></div>
                  <div>
                    <span class="time" id="remaining">{remaining}</span>
                    &nbsp;
                    <a class="btn" href="{PUBLIC_URL}/get_file/{file_id}" download>📥 تحميل</a>
                  </div>
                </div>
              </div>

              <script src="https://cdn.plyr.io/3.7.8/plyr.polyfilled.js"></script>
              <script>
                const player = new Plyr('#player', {{controls: ['play', 'progress', 'current-time', 'mute', 'volume', 'settings', 'fullscreen']}});
                async function poll() {{
                  try {{
                    const r = await fetch('{PUBLIC_URL}/time_left/{file_id}');
                    if (!r.ok) return;
                    const j = await r.json();
                    if (j.ok) {{
                      document.getElementById('remaining').innerText = j.remaining;
                      document.getElementById('views').innerText = j.views;
                    }}
                  }} catch(e){{ console.error(e); }}
                }}
                poll(); setInterval(poll, 30000);
              </script>
            </body>
            </html>
            """
            return Response(html, mimetype="text/html")
        else:
            # non-video -> provide download link
            return f"<a href='{PUBLIC_URL}/download_file/{file_id}'>تحميل {info.get('file_name')}</a><br><small>{remaining} • المشاهدات: {views} • الحجم: {size_mb} MB</small>", 200

    except Exception as e:
        print("خطأ get_file_view:", e, traceback.format_exc())
        return f"حدث خطأ: {e}", 400

# ======== download_file (non-video) ========
@app.route("/download_file/<file_id>", methods=["GET"])
def download_file(file_id):
    try:
        doc = links_memory.get(file_id) or (links_collection.find_one({"_id": file_id}) if mongo_client_active else None)
        if not doc:
            return "❌ الرابط غير صالح أو انتهت صلاحيته.", 400
        expire_time = doc.get("expire_time")
        if expire_time and datetime.now() > expire_time:
            if mongo_client_active:
                links_collection.delete_one({"_id": file_id})
            try:
                del links_memory[file_id]
            except:
                pass
            return "❌ الرابط انتهت صلاحيته بعد 24 ساعة.", 400
        tgfile = bot.get_file(file_id)
        telegram_url = tgfile.file_path
        with requests.get(telegram_url, stream=True) as r:
            r.raise_for_status()
            buf = BytesIO(r.content)
            filename = doc.get("file_name", "file")
            return send_file(buf, as_attachment=True, download_name=filename)
    except Exception as e:
        print("خطأ download_file:", e, traceback.format_exc())
        return f"حدث خطأ: {e}", 400

# ======== stream_video (supports streaming from telegram) ========
@app.route("/stream_video/<file_id>", methods=["GET"])
def stream_video(file_id):
    try:
        # check valid
        doc = links_memory.get(file_id) or (links_collection.find_one({"_id": file_id}) if mongo_client_active else None)
        if not doc:
            return "❌ الرابط غير صالح أو انتهت صلاحيته.", 400
        if doc.get("expire_time") and datetime.now() > doc.get("expire_time"):
            if mongo_client_active:
                links_collection.update_one({"_id": file_id}, {"$set": {"expired": True}})
            try:
                del links_memory[file_id]
            except:
                pass
            return "❌ الرابط انتهت صلاحيته بعد 24 ساعة.", 400

        tgfile = bot.get_file(file_id)
        telegram_url = tgfile.file_path
        # stream with chunked generator
        def generate():
            with requests.get(telegram_url, stream=True) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
        # choose mimetype by extension
        ext = os.path.splitext(telegram_url)[1].lower().strip(".")
        mime = f"video/{ext if ext else 'mp4'}"
        return Response(generate(), mimetype=mime)
    except Exception as e:
        print("خطأ stream_video:", e, traceback.format_exc())
        return f"حدث خطأ: {e}", 400

# ======== test + test_alert ========
@app.route("/test", methods=["GET"])
def test_route():
    return "Flask يعمل ✅", 200

@app.route("/test_alert", methods=["GET"])
def test_alert():
    try:
        if BIN_CHANNEL:
            bot.send_message(chat_id=BIN_CHANNEL, text="✅ هذا إشعار تجريبي ناجح!")
            return "تم إرسال الإشعار التجريبي بنجاح إلى القناة.", 200
        return "BIN_CHANNEL غير مضبوط", 500
    except Exception as e:
        return f"❌ فشل إرسال الإشعار: {e}", 500

# ======== stats command (admin) ========
def show_stats(update, context):
    try:
        user_id = update.message.from_user.id
        if user_id != ADMIN_ID:
            update.message.reply_text("❌ ليس لديك صلاحية الوصول إلى الإحصائيات.")
            return
        if not mongo_client_active:
            update.message.reply_text("❌ لا يمكن الوصول إلى قاعدة البيانات.")
            return
        total_users = users_collection.count_documents({"is_allowed": True})
        total_activities = activity_collection.count_documents({})
        total_links = links_collection.count_documents({})
        total_views = 0
        for d in links_collection.find({}, {"views": 1}):
            total_views += d.get("views", 0)
        stats = f"📊 إحصائيات البوت:\n\nالمستخدمون المسموحون: {total_users}\nإجمالي الأنشطة: {total_activities}\nالروابط: {total_links}\nإجمالي المشاهدات: {total_views}"
        update.message.reply_text(stats)
    except Exception as e:
        print("خطأ show_stats:", e, traceback.format_exc())

# ======== إضافة معالجات تيليجرام ========
dispatcher.add_handler(MessageHandler(Filters.document | Filters.video | Filters.audio | Filters.photo, handle_file))
dispatcher.add_handler(CallbackQueryHandler(button_handler))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("stats", show_stats))

# ======== Webhook endpoint ========
@app.route("/", methods=["POST"])
def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
    return "OK", 200

# ======== run ========
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
