from datetime import datetime

def get_time_left(expire_time):
    if not expire_time:
        return "❌ غير صالح"
    now = datetime.now()
    remaining = expire_time - now
    if remaining.total_seconds() <= 0:
        return "❌ انتهى"
    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours} ساعة {minutes} دقيقة {seconds} ثانية"
