# استخدام صورة بايثون الرسمية
FROM python:3.9-slim

# تثبيت FFmpeg
RUN apt-get update && apt-get install -y ffmpeg

# نسخ ملفات المشروع إلى الصورة
COPY . /app

# تعيين مجلد العمل
WORKDIR /app

# تثبيت مكتبات بايثون
RUN pip install -r requirements.txt

# تشغيل التطبيق باستخدام Gunicorn
CMD ["gunicorn", "tgfs.__main__:app", "--bind", "0.0.0.0:8000"]
