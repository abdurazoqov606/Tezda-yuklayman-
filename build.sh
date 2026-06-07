#!/usr/bin/env bash
# Xatolik bo'lsa darhol to'xtash
set -o erxit

# 1. Python kutubxonalarini o'rnatish
pip install -r requirements.txt

# 2. FFmpeg dasturini yuklab olish va sozlash
echo "📥 FFmpeg yuklab olinmoqda..."
mkdir -p -m 777 /tmp/bin
curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-i686-static.tar.xz | tar -xJ -C /tmp/bin --strip-components=1

# Tizim yo'liga (PATH) qo'shish uchun muhit yaratish
export PATH="/tmp/bin:$PATH"
echo "✅ FFmpeg muvaffaqiyatli o'rnatildi!"
