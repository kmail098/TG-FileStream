# استخدم صورة بايثون 3.9 الرسمية
FROM python:3.9-slim

# تثبيت FFmpeg باستخدام مدير الحزم apt-get
RUN apt-get update && apt-get install -y ffmpeg

# نسخ ملفات المشروع إلى المجلد المخصص للتطبيق
COPY . /app

# تعيين مجلد العمل
WORKDIR /app

# تثبيت مكتبات بايثون من requirements.txt
RUN pip install -r requirements.txt

# تشغيل التطبيق باستخدام Gunicorn على منفذ 8000
CMD ["gunicorn", "tgfs.__main__:app", "--bind", "0.0.0.0:8000"]
