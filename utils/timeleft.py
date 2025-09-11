from telegram import InlineKeyboardButton
from telegram.ext import CallbackQueryHandler
import time

def format_time_left(expiry_time: int) -> str:
    remaining = expiry_time - int(time.time())
    if remaining <= 0:
        return "⛔ الرابط منتهي!"
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    return f"⏳ متبقي: {hours} ساعة {minutes} دقيقة {seconds} ثانية"

def generate_time_button(expiry_time: int):
    return InlineKeyboardButton("⏳ تحقق من الوقت المتبقي", callback_data=f"timeleft:{expiry_time}")

def check_timeleft(update, context):
    query = update.callback_query
    query.answer()

    data = query.data.split(":")
    if len(data) == 2 and data[0] == "timeleft":
        expiry_time = int(data[1])
        time_left_msg = format_time_left(expiry_time)

        query.edit_message_text(
            text=f"{time_left_msg}\n⚠️ الروابط تنتهي بعد 24 ساعة من إنشائها.",
            reply_markup=query.message.reply_markup
        )

def register(dispatcher):
    dispatcher.add_handler(CallbackQueryHandler(check_timeleft, pattern=r"^timeleft:"))
