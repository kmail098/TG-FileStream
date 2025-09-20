#!/bin/bash

# تثبيت FFmpeg باستخدام مدير الحزم apt-get
apt-get update
apt-get install -y ffmpeg

# تثبيت مكتبات بايثون
pip install -r requirements.txt

# بناء ملفات المشروع النهائية
vercel build
