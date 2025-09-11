from datetime import datetime

def get_time_left(expire_time):
    """
    يحسب الوقت المتبقي حتى انتهاء صلاحية الرابط
    """
    if not expire_time:
        return "❌ الرابط غير صالح"

    now = datetime.now()
    remaining = expire_time - now

    if remaining.total_seconds() <= 0:
        return "❌ انتهت الصلاحية"

    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        return f"{hours} ساعة و {minutes} دقيقة"
    elif minutes > 0:
        return f"{minutes} دقيقة و {seconds} ثانية"
    else:
        return f"{seconds} ثانية"
