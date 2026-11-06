#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║        🚀 TEZDA YUKLAYMAN BOT — PREMIUM EDITION v2.0        ║
║        Muallif  : @uzs_kr                                    ║
║        Versiya  : 2.0 — Render.com uchun moslashtirilgan    ║
╚══════════════════════════════════════════════════════════════╝
 
XUSUSIYATLAR:
  ✅ Parallel (bir vaqtda) video + audio yuklab olish
  ✅ Tanlash bilan ovora bo'lsangiz ham yuklab olinadi
  ✅ Instagram, TikTok, YouTube, Facebook, Snapchat, va boshqalar
  ✅ Shazam — qo'shiq nomi, ijrochi, matni
  ✅ Majburiy obuna tizimi
  ✅ Admin panel (broadcast, foydalanuvchilar, kanallar)
  ✅ Server keshi yo'q — fayllar darhol o'chiriladi
  ✅ Guruhlarda ham ishlaydi
  ✅ Render.com uchun web server
"""
 
# ─────────────────────────────────────────────────────────────
#  📦  IMPORT
# ─────────────────────────────────────────────────────────────
import os
import re
import sys
import time
import uuid
import html
import shutil
import signal
import asyncio
import logging
import sqlite3
import threading
import subprocess
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
 
import yt_dlp
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException
from flask import Flask, jsonify
 
try:
    from shazamio import Shazam
    SHAZAM_OK = True
except ImportError:
    SHAZAM_OK = False
 
# ─────────────────────────────────────────────────────────────
#  📋  LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
 
# ─────────────────────────────────────────────────────────────
#  ⚙️  SOZLAMALAR — bu yerni o'zgartiring
# ─────────────────────────────────────────────────────────────
BOT_TOKEN  = "8660090967:AAH_FYJE-TP5Ehn5rM_ULkngd4K9vrXwkd8"
ADMIN_IDS  = [8426582765]
BOT_NAME   = "@TezdaYuklaymanbot"
AUTHOR     = "@uzs_kr"
DB_FILE    = "tezda_bot.db"
TEMP_DIR   = "/tmp/tezda"
MAX_BYTES  = 49 * 1024 * 1024   # 49 MB  (Telegram limiti 50 MB)
TASK_TTL   = 300                 # 5 daqiqa — vazifani xotiradan o'chirish
 
os.makedirs(TEMP_DIR, exist_ok=True)
 
# ─────────────────────────────────────────────────────────────
#  ⚡  TEZLIK / BARQARORLIK SOZLAMALARI (xohlasangiz o'zgartiring)
# ─────────────────────────────────────────────────────────────
DB_FILE          = os.environ.get("DB_FILE", DB_FILE)  # Render Disk ishlatsangiz: /data/tezda_bot.db
VIDEO_MAX_HEIGHT = 720      # 720p — Telegram uchun eng yaxshi tezlik/sifat balansi
VIDEO_LOW_HEIGHT = 360      # fayl 50 MB dan katta chiqsa, avtomatik shu sifatda qayta yuklanadi
AUDIO_CODEC      = "m4a"    # "m4a" = qayta kodlashsiz (juda tez). MP3 xohlasangiz: "mp3"
FRAGMENTS        = 8        # bir vaqtda yuklanadigan bo'laklar soni
MAX_PARALLEL_DL  = 6        # serverni to'ldirib yubormaslik uchun bir vaqtdagi yuklashlar
WAIT_TIMEOUT     = 240      # foydalanuvchi tugmani bosgandan keyin max kutish (soniya)
KEEPALIVE_SEC    = 240      # o'z-o'ziga ping (Render 15 daqiqada uxlatadi)
SUB_CACHE_TTL    = 120      # obuna tekshiruvi keshi (soniya)
BACKUP_EVERY     = 3600     # baza zaxirasi (soniya, faqat o'zgarish bo'lsa)
DB_BACKUP_ON     = os.environ.get("DB_BACKUP", "1") != "0"
 
# PATH ga Python bin papkasini qo'shamiz (pip orqali o'rnatilgan deno shu yerda bo'ladi)
os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
 
 
def _find_ffmpeg() -> str | None:
    """Tizim ffmpeg → bo'lmasa imageio-ffmpeg (pip) ichidagi binar."""
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
 
 
FFMPEG_PATH = _find_ffmpeg()
HAS_FFMPEG  = bool(FFMPEG_PATH)
 
# YouTube cookies (ixtiyoriy): Render muhit o'zgaruvchisi YT_COOKIES ga cookies.txt matnini qo'ying
COOKIES_FILE = None
if os.environ.get("YT_COOKIES"):
    COOKIES_FILE = os.path.join(TEMP_DIR, "cookies.txt")
    with open(COOKIES_FILE, "w", encoding="utf-8") as _cf:
        _cf.write(os.environ["YT_COOKIES"])
elif os.path.exists("cookies.txt"):
    COOKIES_FILE = os.path.join(TEMP_DIR, "cookies.txt")
    shutil.copyfile("cookies.txt", COOKIES_FILE)   # yt-dlp yozishi mumkin, shuning uchun nusxa
 
# ─────────────────────────────────────────────────────────────
#  🤖  BOT OBYEKTI
# ─────────────────────────────────────────────────────────────
telebot.apihelper.CONNECT_TIMEOUT = 15
telebot.apihelper.READ_TIMEOUT = 60
telebot.apihelper.SESSION_TIME_TO_LIVE = 5 * 60
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML', num_threads=10)
if SHAZAM_OK:
    shazam_engine = Shazam()
 
# Xotiradagi vazifalar: { task_id: { ... } }
tasks: dict = {}
# Admin holati: { uid: { state, ... } }
adm_state: dict = {}
 
 
# ═══════════════════════════════════════════════════════════════
#  🌐  RENDER.COM KEEP-ALIVE WEB SERVER
# ═══════════════════════════════════════════════════════════════
app = Flask(__name__)
START_TIME = time.time()
 
 
@app.route("/")
def home():
    try:
        users, dls = db_stats()
    except Exception:
        users, dls = "—", "—"
    body = (
        "<html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'></head>"
        "<body style='font-family:sans-serif;text-align:center;padding:40px'>"
        "<h1>🚀 TezdaYuklayman Bot</h1>"
        "<p>Holat: <b style='color:green'>ISHLAYAPTI ✅</b></p>"
        f"<p>👥 Foydalanuvchilar: <b>{users}</b></p>"
        f"<p>📥 Jami yuklamalar: <b>{dls}</b></p>"
        f"<p>🕒 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</p>"
        "</body></html>"
    )
    return body, 200, {"Content-Type": "text/html; charset=utf-8"}
 
 
@app.route("/health")
@app.route("/healthz")
def health():
    """Tez va bazaga tegmaydi — Render health check va ping uchun."""
    return jsonify(status="ok", uptime=int(time.time() - START_TIME)), 200
 
 
@app.route("/ping")
def ping():
    return "pong", 200
 
 
def keep_alive_loop():
    """
    Render (bepul tarif) 15 daqiqa kirish trafigi bo'lmasa servisni uxlatadi.
    Servis o'z ommaviy URL'iga muntazam so'rov yuborib uyg'oq turadi.
    """
    base = os.environ.get("SELF_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not base:
        logger.info("ℹ️ RENDER_EXTERNAL_URL yo'q — self-ping o'chiq (lokal rejim)")
        return
    url = base.rstrip("/") + "/health"
    logger.info(f"🔁 Keep-alive: {url} har {KEEPALIVE_SEC} soniyada")
    time.sleep(30)
    while True:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tezda-keepalive"})
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read(64)
        except Exception as e:
            logger.warning(f"keep-alive xatosi: {e}")
        time.sleep(KEEPALIVE_SEC)
 
 
def run_web_server():
    """Asosiy oqimda ishlaydi (SIGTERM ni shu oqim ushlaydi)."""
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🌐 Web-server port {port} da ishga tushmoqda")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=4, ident="tezda")
    except ImportError:
        logger.warning("waitress yo'q — Flask ichki serveri ishlatilmoqda")
        app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
 
 
# ═══════════════════════════════════════════════════════════════
#  🗄️  MA'LUMOTLAR BAZASI
# ═══════════════════════════════════════════════════════════════
DB_DIRTY = threading.Event()   # baza o'zgardi → zaxira kerak
 
 
@contextmanager
def _conn():
    """Har safar yangi ulanish: commit + HAR DOIM yopiladi, "database is locked" kamayadi."""
    c = sqlite3.connect(DB_FILE, timeout=20, check_same_thread=False)
    try:
        c.execute("PRAGMA synchronous=NORMAL")
        with c:
            yield c
        if c.total_changes:
            DB_DIRTY.set()
    finally:
        c.close()
 
 
def init_db():
    with _conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                uid       INTEGER PRIMARY KEY,
                username  TEXT    DEFAULT '',
                fullname  TEXT    DEFAULT '',
                joined    TEXT    DEFAULT (datetime('now')),
                last_seen TEXT    DEFAULT (datetime('now')),
                dl_count  INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS channels (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT    UNIQUE,
                title    TEXT    DEFAULT '',
                added_at TEXT    DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS broadcasts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sent       INTEGER DEFAULT 0,
                failed     INTEGER DEFAULT 0,
                created_at TEXT    DEFAULT (datetime('now'))
            );
        """)
 
 
def db_save_user(user):
    with _conn() as c:
        c.execute(
            "INSERT INTO users (uid, username, fullname) VALUES (?,?,?) "
            "ON CONFLICT(uid) DO UPDATE SET username=excluded.username, "
            "fullname=excluded.fullname, last_seen=datetime('now')",
            (user.id, user.username or '', user.first_name or '')
        )
 
 
def db_inc_dl(uid):
    with _conn() as c:
        c.execute("UPDATE users SET dl_count=dl_count+1 WHERE uid=?", (uid,))
 
 
def db_stats():
    with _conn() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        dls   = c.execute("SELECT COALESCE(SUM(dl_count),0) FROM users").fetchone()[0]
    return users, dls
 
 
def db_all_uids():
    with _conn() as c:
        return [r[0] for r in c.execute("SELECT uid FROM users").fetchall()]
 
 
def db_recent_users(n=30):
    with _conn() as c:
        return c.execute(
            "SELECT uid, username, fullname, dl_count, last_seen "
            "FROM users ORDER BY last_seen DESC LIMIT ?", (n,)
        ).fetchall()
 
 
def db_channels():
    with _conn() as c:
        return c.execute("SELECT username, title FROM channels").fetchall()
 
 
def db_add_channel(username, title=''):
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO channels (username, title) VALUES (?,?)",
            (username, title)
        )
 
 
def db_del_channel(username):
    with _conn() as c:
        c.execute("DELETE FROM channels WHERE username=?", (username,))
 
 
# ═══════════════════════════════════════════════════════════════
#  💾  BAZA ZAXIRASI — Render'da disk o'chib ketishidan himoya
# ═══════════════════════════════════════════════════════════════
# Render bepul tarifida disk har restart/deploy'da tozalanadi. Shuning uchun baza
# vaqti-vaqti bilan birinchi adminga hujjat qilib yuboriladi va PIN qilinadi;
# yangi ishga tushganda baza bo'sh bo'lsa, o'sha PIN dagi fayldan tiklanadi.
BACKUP_TAG   = "DB_BACKUP"
_backup_lock = threading.Lock()
_last_backup = 0.0
 
 
def _db_snapshot(dst_path: str):
    src = sqlite3.connect(DB_FILE, timeout=20)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
 
 
def backup_db(reason: str = "auto") -> bool:
    global _last_backup
    if not (DB_BACKUP_ON and ADMIN_IDS):
        return False
    if not _backup_lock.acquire(blocking=False):
        return False
    snap = os.path.join(TEMP_DIR, "backup_snapshot.db")
    try:
        users, _ = db_stats()
        if users == 0 and not db_channels():
            return False          # bo'sh bazani hech qachon zaxiralamaymiz
        admin = ADMIN_IDS[0]
        old_id = None
        try:
            pm = getattr(bot.get_chat(admin), 'pinned_message', None)
            if pm and (pm.caption or '').startswith(BACKUP_TAG):
                old_id = pm.message_id
        except Exception:
            pass
        DB_DIRTY.clear()
        _db_snapshot(snap)
        with open(snap, 'rb') as f:
            m = bot.send_document(
                admin, f,
                caption=f"{BACKUP_TAG} {datetime.now():%Y-%m-%d %H:%M:%S} users={users} ({reason})",
                visible_file_name="tezda_bot.db",
                disable_notification=True,
                timeout=60,
            )
        try:
            bot.pin_chat_message(admin, m.message_id, disable_notification=True)
        except Exception as e:
            logger.warning(f"Zaxirani pin qilib bo'lmadi: {e}")
        if old_id:
            try:
                bot.delete_message(admin, old_id)
            except Exception:
                pass
        _last_backup = time.time()
        logger.info(f"💾 Baza zaxirasi yuborildi ({reason}), users={users}")
        return True
    except Exception as e:
        DB_DIRTY.set()
        logger.warning(f"Zaxira xatosi: {e}")
        return False
    finally:
        try:
            os.remove(snap)
        except Exception:
            pass
        _backup_lock.release()
 
 
def restore_db_if_empty() -> bool:
    if not (DB_BACKUP_ON and ADMIN_IDS):
        return False
    try:
        users, _ = db_stats()
        if users > 0 or db_channels():
            return False
        pm = getattr(bot.get_chat(ADMIN_IDS[0]), 'pinned_message', None)
        if not pm or not getattr(pm, 'document', None) or not (pm.caption or '').startswith(BACKUP_TAG):
            logger.info("💾 Zaxira topilmadi — toza baza bilan boshlanadi")
            return False
        fi   = bot.get_file(pm.document.file_id)
        data = bot.download_file(fi.file_path)
        if not data.startswith(b"SQLite format 3"):
            logger.warning("💾 Zaxira fayli yaroqsiz")
            return False
        tmp = os.path.join(TEMP_DIR, "restore.db")
        with open(tmp, 'wb') as f:
            f.write(data)
        chk = sqlite3.connect(tmp)
        try:
            n = chk.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            chk.execute("SELECT COUNT(*) FROM channels").fetchone()
        finally:
            chk.close()
        for ext in ('', '-wal', '-shm'):
            try:
                os.remove(DB_FILE + ext)
            except FileNotFoundError:
                pass
        shutil.move(tmp, DB_FILE)
        init_db()
        DB_DIRTY.clear()
        logger.info(f"💾 Baza zaxiradan tiklandi: {n} ta foydalanuvchi")
        return True
    except Exception as e:
        logger.warning(f"Tiklash xatosi: {e}")
        return False
 
 
# ═══════════════════════════════════════════════════════════════
#  🔒  MAJBURIY OBUNA
# ═══════════════════════════════════════════════════════════════
_sub_cache: dict = {}   # uid -> amal qilish muddati (faqat musbat natijalar)
 
 
def is_subscribed(uid: int) -> bool:
    channels = db_channels()
    if not channels:
        return True
    now = time.time()
    if _sub_cache.get(uid, 0) > now:
        return True
    for ch, _ in channels:
        try:
            member = bot.get_chat_member(ch, uid)
            st = member.status
            if st in ('left', 'kicked') or (st == 'restricted' and not getattr(member, 'is_member', True)):
                _sub_cache.pop(uid, None)
                return False
        except ApiTelegramException as e:
            if e.error_code == 429 or e.error_code >= 500:
                return True        # Telegram vaqtinchalik muammosi — foydalanuvchini to'smaymiz
            logger.warning(f"is_subscribed({ch}): {e}")
            return False
        except Exception:
            return True            # tarmoq uzilishi — to'smaymiz
    _sub_cache[uid] = now + SUB_CACHE_TTL
    return True
 
 
def send_sub_wall(uid: int):
    channels = db_channels()
    kb = types.InlineKeyboardMarkup(row_width=1)
    text = "🔒 <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:</b>\n\n"
    for ch, title in channels:
        clean = ch.lstrip('@')
        text += f"📢 {title or ch}\n"
        kb.add(types.InlineKeyboardButton(f"➕ {title or ch}", url=f"https://t.me/{clean}"))
    kb.add(types.InlineKeyboardButton("✅ Obunani tekshirish", callback_data="chk_sub"))
    try:
        bot.send_message(uid, text, reply_markup=kb)
    except Exception as e:
        logger.warning(f"send_sub_wall error: {e}")
 
 
def require_sub(fn):
    @wraps(fn)
    def wrapper(message, *a, **kw):
        uid = message.from_user.id
        if message.chat.type == 'private' and not is_subscribed(uid):
            send_sub_wall(uid)
            return
        return fn(message, *a, **kw)
    return wrapper
 
 
# ═══════════════════════════════════════════════════════════════
#  🛠  YORDAMCHI FUNKSIYALAR
# ═══════════════════════════════════════════════════════════════
URL_RE = re.compile(r'https?://[^\s]+')
 
def extract_url(text: str) -> str | None:
    m = URL_RE.search(text or '')
    return m.group(0) if m else None
 
 
PLATFORMS = {
    'youtube.com':   '🎬 YouTube',
    'youtu.be':      '🎬 YouTube',
    'instagram.com': '📸 Instagram',
    'tiktok.com':    '🎵 TikTok',
    'facebook.com':  '👥 Facebook',
    'fb.watch':      '👥 Facebook',
    'snapchat.com':  '👻 Snapchat',
    'likee.video':   '🎥 Likee',
    'pinterest.com': '📌 Pinterest',
    'threads.net':   '🧵 Threads',
    'twitter.com':   '🐦 Twitter/X',
    'x.com':         '🐦 Twitter/X',
    'vimeo.com':     '🎬 Vimeo',
    'dailymotion.com': '🎬 Dailymotion',
    'reddit.com':    '🔴 Reddit',
    'twitch.tv':     '💜 Twitch',
    'bilibili.com':  '📺 Bilibili',
}
 
def get_platform(url: str) -> str:
    low = url.lower()
    for domain, name in PLATFORMS.items():
        if domain in low:
            return name
    return '🌐 Video'
 
 
def clean(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
 
 
def clean_prefix(prefix: str):
    """prefix bilan boshlanadigan BARCHA fayllarni (yarim yuklanganlarini ham) o'chiradi."""
    try:
        for f in os.listdir(TEMP_DIR):
            if f.startswith(prefix):
                clean(os.path.join(TEMP_DIR, f))
    except Exception:
        pass
 
 
def find_file(prefix: str) -> str | None:
    """TEMP_DIR ichida prefix bilan boshlanadigan TUGALLANGAN faylni topadi."""
    try:
        for f in os.listdir(TEMP_DIR):
            if f.startswith(prefix) and '.part' not in f and not f.endswith(('.ytdl', '.temp', '.tmp')):
                fp = os.path.join(TEMP_DIR, f)
                if os.path.isfile(fp) and os.path.getsize(fp) > 100:
                    return fp
    except Exception:
        pass
    return None
 
 
def safe_edit(text, chat_id, msg_id, **kw):
    try:
        bot.edit_message_text(text, chat_id, msg_id, **kw)
    except Exception:
        pass
 
 
def cleanup_old_tasks():
    """Eskirgan vazifalar va fayllarni o'chiradi (tugmani bosganlarga tegmaydi)."""
    now = time.time()
    for tid, t in list(tasks.items()):
        if t.get('busy'):
            continue
        if now - t.get('created', now) > TASK_TTL:
            t['dead'] = True
            t['cancel_vid'] = t['cancel_aud'] = True
            tasks.pop(tid, None)
            clean_prefix(f"vid_{tid}")
            clean_prefix(f"aud_{tid}")
 
 
def sweep_orphans(max_age: int = 900):
    """TEMP_DIR dagi 15 daqiqadan eski begona fayllarni tozalaydi (disk to'lmasin)."""
    now = time.time()
    try:
        for f in os.listdir(TEMP_DIR):
            if f == "cookies.txt":
                continue
            fp = os.path.join(TEMP_DIR, f)
            if os.path.isfile(fp) and now - os.path.getmtime(fp) > max_age:
                clean(fp)
    except Exception:
        pass
 
 
def janitor_loop():
    """Orqa fondagi tozalovchi: vazifalar, fayllar, kesh, baza zaxirasi."""
    while True:
        time.sleep(60)
        try:
            cleanup_old_tasks()
            sweep_orphans()
            now = time.time()
            for k in [k for k, v in list(_sub_cache.items()) if v < now]:
                _sub_cache.pop(k, None)
            if DB_DIRTY.is_set() and now - _last_backup >= BACKUP_EVERY:
                backup_db("auto")
        except Exception as e:
            logger.warning(f"janitor: {e}")
 
 
# ═══════════════════════════════════════════════════════════════
#  📥  YUKLOVCHI — PARALLEL DOWNLOAD ENGINE
# ═══════════════════════════════════════════════════════════════
DL_SEM = threading.BoundedSemaphore(MAX_PARALLEL_DL)
_ANSI  = re.compile(r'\x1b\[[0-9;]*m')
 
 
class _Cancelled(Exception):
    pass
 
 
def _ydl_run(url: str, opts: dict):
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)
 
 
def _short_err(e: Exception) -> str:
    msg = _ANSI.sub('', str(e)).replace('ERROR: ', '').strip()
    return msg[:250]
 
 
def video_format(max_h: int) -> str:
    """Telegram uchun qulay (H.264/mp4), hajmi 50 MB dan oshmaydigan eng yaxshi format."""
    if HAS_FFMPEG:
        return (
            f"bv*[height<={max_h}][vcodec^=avc1][ext=mp4][filesize<?40M]+ba[ext=m4a][filesize<?9M]/"
            f"bv*[height<={max_h}][ext=mp4][filesize<?40M]+ba[ext=m4a]/"
            f"b[height<={max_h}][ext=mp4][filesize<?49M]/"
            f"b[height<={max_h}][filesize<?49M]/"
            f"b[filesize<?49M]/b"
        )
    return f"b[height<={max_h}][ext=mp4][filesize<?49M]/b[ext=mp4][filesize<?49M]/b"
 
 
def _base_opts() -> dict:
    o = {
        'quiet': True, 'no_warnings': True, 'noplaylist': True, 'noprogress': True,
        'socket_timeout': 20, 'retries': 3, 'fragment_retries': 3, 'extractor_retries': 2,
        'concurrent_fragment_downloads': FRAGMENTS,
        'http_chunk_size': 10 * 1024 * 1024,
        'cachedir': False, 'overwrites': True,
    }
    if FFMPEG_PATH:
        o['ffmpeg_location'] = FFMPEG_PATH
    if COOKIES_FILE:
        o['cookiefile'] = COOKIES_FILE
    return o
 
 
def _cancel_hook(task: dict, key: str):
    def hook(_d):
        if task.get(key) or task.get('dead'):
            raise _Cancelled()
    return hook
 
 
def _result_path(info: dict, prefix: str) -> str | None:
    for d in (info.get('requested_downloads') or []):
        fp = d.get('filepath') or d.get('_filename')
        if fp and os.path.isfile(fp):
            return fp
    return find_file(prefix)
 
 
def _fill_meta(task: dict, info: dict):
    task['title']    = task['title']    or (info.get('title') or '')[:80]
    task['duration'] = task['duration'] or (info.get('duration') or 0)
    task['uploader'] = task['uploader'] or (info.get('uploader') or info.get('channel') or '')[:64]
 
 
def new_task(url: str, uid: int, platform: str) -> dict:
    return {
        'url': url, 'uid': uid, 'platform': platform, 'created': time.time(),
        'vid_evt': threading.Event(), 'aud_evt': threading.Event(),
        'vid_err': None, 'aud_err': None, 'vid_path': None, 'aud_path': None,
        'title': '', 'duration': 0, 'uploader': '', 'width': 0, 'height': 0,
        'busy': False, 'dead': False, 'cancel_vid': False, 'cancel_aud': False,
    }
 
 
def predownload(task_id: str, task: dict):
    """
    Foydalanuvchi formatni tanlaguncha orqa fonda VIDEO va AUDIOni
    bir vaqtda ikki alohida thread'da yuklab oladi. Biri tanlansa, ikkinchisi
    to'xtatiladi (server resursi tejaladi).
    """
    url = task['url']
 
    vid_opts = _base_opts()
    vid_opts.update({
        'format':              video_format(VIDEO_MAX_HEIGHT),
        'outtmpl':             os.path.join(TEMP_DIR, f'vid_{task_id}.%(ext)s'),
        'merge_output_format': 'mp4',
        'progress_hooks':      [_cancel_hook(task, 'cancel_vid')],
    })
 
    aud_opts = _base_opts()
    aud_opts.update({
        'format':         'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl':        os.path.join(TEMP_DIR, f'aud_{task_id}.%(ext)s'),
        'progress_hooks': [_cancel_hook(task, 'cancel_aud')],
    })
    if HAS_FFMPEG:
        pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': AUDIO_CODEC}
        if AUDIO_CODEC == 'mp3':
            pp['preferredquality'] = '192'
        aud_opts['postprocessors'] = [pp]
 
    def job(kind: str, opts: dict):
        prefix = f"{kind}_{task_id}"
        try:
            with DL_SEM:
                if task.get(f'cancel_{kind}') or task.get('dead'):
                    raise _Cancelled()
                info = _ydl_run(url, opts)
            _fill_meta(task, info)
            if kind == 'vid':
                task['width']  = info.get('width')  or 0
                task['height'] = info.get('height') or 0
            task[f'{kind}_path'] = _result_path(info, prefix)
        except _Cancelled:
            pass
        except Exception as e:
            task[f'{kind}_err'] = _short_err(e)
            logger.error(f"[{task_id}] {kind} error: {e}")
        finally:
            if task.get('dead'):
                clean_prefix(prefix)
            task[f'{kind}_evt'].set()
 
    threading.Thread(target=job, args=('vid', vid_opts), daemon=True).start()
    threading.Thread(target=job, args=('aud', aud_opts), daemon=True).start()
 
 
def redownload_low(task_id: str, task: dict) -> str | None:
    """Video 50 MB dan katta chiqsa — pastroq sifatda bir marta qayta yuklaydi."""
    clean_prefix(f"vid_{task_id}")
    opts = _base_opts()
    opts.update({
        'format':              video_format(VIDEO_LOW_HEIGHT),
        'outtmpl':             os.path.join(TEMP_DIR, f'vid_{task_id}.%(ext)s'),
        'merge_output_format': 'mp4',
    })
    with DL_SEM:
        info = _ydl_run(task['url'], opts)
    task['width']  = info.get('width')  or 0
    task['height'] = info.get('height') or 0
    return _result_path(info, f"vid_{task_id}")
 
 
# ═══════════════════════════════════════════════════════════════
#  📝  XABAR SHABLONLARI
# ═══════════════════════════════════════════════════════════════
WELCOME = (
    "🔥 Assalomu alaykum. {bot} ga Xush kelibsiz.\n"
    "Bot orqali quyidagilarni yuklab olishingiz mumkin:\n\n"
    "• Instagram — post va IGTV + audio bilan;\n"
    "• TikTok — suv belgisiz video + audio bilan;\n"
    "• YouTube — videolar va Shorts + audio bilan;\n"
    "• Snapchat — suv belgisiz video + audio bilan;\n"
    "• Likee — suv belgisiz video + audio bilan;\n"
    "• Pinterest — suv belgisiz video va rasmlar + audio bilan;\n"
    "• Threads — video va rasmlar + audio bilan;\n"
    "• Facebook, Twitter/X va ko'plab boshqalar!\n\n"
    "🎵 <b>Shazam funksiyasi:</b>\n"
    "Ovozli xabar, audio, video yoki video xabar yuboring\n\n"
    "🚀 Yuklab olish uchun havola yuboring!\n"
    "😎 Bot guruhlarda ham ishlaydi!"
)
 
HELP_TEXT = (
    "ℹ️ <b>Yordam va ko'rsatmalar</b>\n\n"
    "📥 <b>Video / Audio yuklab olish:</b>\n"
    "Havola yuboring → format tanlang → tayyor!\n\n"
    "🎵 <b>Shazam (qo'shiq aniqlash):</b>\n"
    "Ovozli xabar, audio, video yoki video xabar yuboring\n\n"
    "🌐 <b>Qo'llab-quvvatlanadigan saytlar:</b>\n"
    "YouTube, Instagram, TikTok, Facebook,\n"
    "Snapchat, Pinterest, Threads, Likee,\n"
    "Twitter/X, Vimeo, Reddit, Twitch va boshqalar\n\n"
    "⚡ <b>Texnik jihat:</b>\n"
    "• Maksimal fayl: 50 MB\n"
    f"• Sifat: HD {VIDEO_MAX_HEIGHT}p gacha\n"
    f"• Audio: {'M4A (AAC), original sifat' if AUDIO_CODEC == 'm4a' else 'MP3 192 kbps'}\n\n"
    f"📞 <b>Qo'llab-quvvatlash:</b> {AUTHOR}"
)
 
 
# ═══════════════════════════════════════════════════════════════
#  🚀  KOMANDALAR
# ═══════════════════════════════════════════════════════════════
@bot.message_handler(commands=['start'])
def cmd_start(message):
    db_save_user(message.from_user)
    uid = message.from_user.id
 
    if message.chat.type != 'private':
        return
 
    if not is_subscribed(uid):
        send_sub_wall(uid)
        return
 
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("❓ Yordam",     callback_data="cb_help"),
        types.InlineKeyboardButton("📊 Statistika", callback_data="cb_stats")
    )
    kb.add(
        types.InlineKeyboardButton(
            "➕ Guruhga qo'shish",
            url="https://t.me/TezdaYuklaymanbot?startgroup=true"
        )
    )
    bot.send_message(
        uid, WELCOME.format(bot=BOT_NAME),
        reply_markup=kb, disable_web_page_preview=True
    )
 
 
@bot.message_handler(commands=['help'])
def cmd_help(message):
    db_save_user(message.from_user)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🏠 Bosh menyu", callback_data="cb_main"))
    bot.send_message(message.chat.id, HELP_TEXT, reply_markup=kb)
 
 
# ═══════════════════════════════════════════════════════════════
#  👑  ADMIN HOLATI (reklama / kanal qo'shish) — ENG BIRINCHI ushlanadi
# ═══════════════════════════════════════════════════════════════
# Avval admin reklamasida havola yoki video bo'lsa, u yuklagich/Shazam'ga ketib qolardi.
@bot.message_handler(
    func=lambda m: (m.from_user is not None and m.from_user.id in adm_state
                    and not (m.text or '').startswith('/')),
    content_types=['text', 'photo', 'video', 'document', 'animation', 'sticker',
                   'audio', 'voice', 'video_note']
)
def handle_admin_state(message):
    uid = message.from_user.id
    st  = adm_state.pop(uid, None)
    if not st:
        return
 
    # Reklama yuborish — copy_message har qanday turni formati bilan nusxalaydi
    if st['state'] == 'wait_broadcast':
        users  = db_all_uids()
        status = bot.send_message(
            uid, f"📣 Yuborilmoqda... Jami: <b>{len(users)}</b> ta foydalanuvchi."
        )
        src_chat, src_msg = message.chat.id, message.message_id
 
        def do_bc():
            sent, failed = 0, 0
            for target in users:
                for attempt in (1, 2):
                    try:
                        bot.copy_message(target, src_chat, src_msg)
                        sent += 1
                        break
                    except ApiTelegramException as e:
                        if e.error_code == 429 and attempt == 1:
                            wait = 5
                            try:
                                wait = int((e.result_json or {}).get('parameters', {}).get('retry_after', 5))
                            except Exception:
                                pass
                            time.sleep(min(wait, 30) + 1)
                            continue
                        failed += 1
                        break
                    except Exception:
                        failed += 1
                        break
                time.sleep(0.04)
 
            safe_edit(
                f"✅ <b>Tarqatish tugadi!</b>\n\n"
                f"✅ Yuborildi: <b>{sent}</b>\n"
                f"❌ Xatolik:   <b>{failed}</b>\n"
                f"📊 Jami:      <b>{len(users)}</b>",
                uid, status.message_id
            )
 
        threading.Thread(target=do_bc, daemon=True).start()
        return
 
    # Kanal qo'shish
    if st['state'] == 'wait_addch':
        parts = (message.text or '').strip().split(maxsplit=1)
        if not parts:
            bot.send_message(uid, "❌ Noto'g'ri format.")
            return
        ch    = parts[0] if parts[0].startswith('@') else '@' + parts[0]
        title = parts[1] if len(parts) > 1 else ch
        db_add_channel(ch, title)
        bot.send_message(uid, f"✅ Kanal qo'shildi: <b>{html.escape(ch)}</b> ({html.escape(title)})")
        send_admin_panel(uid)
        return
 
 
# ═══════════════════════════════════════════════════════════════
#  🔗  URL HANDLER — ASOSIY YUKLASH
# ═══════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: bool(extract_url(m.text or '')))
@require_sub
def handle_url(message):
    db_save_user(message.from_user)
    cleanup_old_tasks()
 
    uid      = message.from_user.id
    url      = extract_url(message.text)
    platform = get_platform(url)
 
    task_id = uuid.uuid4().hex[:12]
    task = new_task(url, uid, platform)
    tasks[task_id] = task
 
    # 🔥 Darhol orqa fonda yuklab olishni boshlash
    predownload(task_id, task)
 
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🎬 Video (HD)",   callback_data=f"dl:vid:{task_id}"),
        types.InlineKeyboardButton(f"🎵 Audio ({AUDIO_CODEC.upper()})", callback_data=f"dl:aud:{task_id}")
    )
    kb.add(types.InlineKeyboardButton("❌ Bekor qilish", callback_data=f"dl:cancel:{task_id}"))
 
    bot.reply_to(
        message,
        f"⚡ <b>{platform}</b> havolasi topildi!\n\n"
        f"⬇️ <i>Siz tanlov qilayotgan paytda yuklab olinmoqda...</i>\n\n"
        f"Formatni tanlang:",
        reply_markup=kb
    )
 
 
@bot.callback_query_handler(func=lambda c: c.data.startswith("dl:"))
def cb_download(c):
    # MUHIM: guruhlarda chat_id foydalanuvchi ID'si emas — xabar turgan chat ishlatiladi
    chat_id = c.message.chat.id
    msg_id  = c.message.message_id
    parts   = c.data.split(":")
    if len(parts) < 3:
        bot.answer_callback_query(c.id)
        return
    dtype, tid = parts[1], parts[2]      # dtype: "vid" | "aud" | "cancel"
    task = tasks.get(tid)
 
    # Bekor qilish
    if dtype == "cancel":
        bot.answer_callback_query(c.id)
        if task and not task.get('busy'):
            task['dead'] = True
            task['cancel_vid'] = task['cancel_aud'] = True
            tasks.pop(tid, None)
            clean_prefix(f"vid_{tid}")
            clean_prefix(f"aud_{tid}")
        safe_edit("❌ Bekor qilindi.", chat_id, msg_id)
        return
 
    if dtype not in ("vid", "aud"):
        bot.answer_callback_query(c.id)
        return
 
    if not task:
        bot.answer_callback_query(c.id, "⏰ Vaqt tugadi! Qaytadan havola tashlang.", show_alert=True)
        return
 
    if task.get('busy'):                  # ikki marta bosishdan himoya
        bot.answer_callback_query(c.id, "⏳ Allaqachon yuklanmoqda...")
        return
 
    task['busy'] = True
    other = 'aud' if dtype == 'vid' else 'vid'
    task[f'cancel_{other}'] = True        # ikkinchisini to'xtatamiz — resurs tejaladi
 
    bot.answer_callback_query(c.id, "⏳ Tayyorlanmoqda...")
    emoji = "🎬" if dtype == "vid" else "🎵"
    safe_edit(f"{emoji} Yuklanmoqda...\n⚡ Iltimos kuting, deyarli tayyor!", chat_id, msg_id)
 
    def fail(text: str):
        safe_edit(text, chat_id, msg_id)
 
    def finalize():
        try:
            if not task[f'{dtype}_evt'].wait(WAIT_TIMEOUT):
                fail("⏱ Yuklash juda uzoq davom etdi. Keyinroq qayta urinib ko'ring.\n"
                     f"📞 Yordam: {AUTHOR}")
                return
 
            err = task.get(f'{dtype}_err')
            if err:
                fail(
                    f"❌ <b>Yuklab olishda xatolik yuz berdi.</b>\n\n"
                    f"<code>{html.escape(err)}</code>\n\n"
                    f"💡 Sabab: yopiq profil, cheklangan kontent yoki\n"
                    f"    botda ushbu platforma qo'llab-quvvatlanmasligi.\n\n"
                    f"📞 Yordam: {AUTHOR}"
                )
                return
 
            file_path = task.get(f'{dtype}_path') or find_file(f"{dtype}_{tid}")
            if not file_path or not os.path.isfile(file_path):
                fail("❌ Fayl topilmadi. Qaytadan urinib ko'ring.")
                return
 
            file_size = os.path.getsize(file_path)
 
            # Video katta chiqsa — avtomatik past sifatda qayta yuklaymiz
            if dtype == 'vid' and file_size > MAX_BYTES:
                safe_edit(f"📦 Fayl katta ({file_size/1024/1024:.0f} MB) — past sifatda tayyorlanmoqda...",
                          chat_id, msg_id)
                try:
                    file_path = redownload_low(tid, task)
                except Exception as e:
                    logger.error(f"[{tid}] low-quality error: {e}")
                    file_path = None
                if not file_path or not os.path.isfile(file_path):
                    fail("❌ Videoni 50 MB ichiga sig'dirib bo'lmadi.\n"
                         "🎵 Audio variantini sinab ko'ring (havolani qayta yuboring).")
                    return
                file_size = os.path.getsize(file_path)
 
            if file_size > MAX_BYTES:
                size_mb = file_size / 1024 / 1024
                fail(
                    f"⚠️ <b>Fayl hajmi juda katta!</b>\n\n"
                    f"📦 Hajm: <b>{size_mb:.1f} MB</b>\n"
                    f"🚫 Telegram limiti: 50 MB\n\n"
                    f"💡 Qisqaroq video yoki audio variantini sinab ko'ring.\n"
                    f"📞 Yordam: {AUTHOR}"
                )
                return
 
            title = task.get('title', '') or ''
            short_title = html.escape((title[:60] + '…') if len(title) > 60 else title)
            caption = (
                f"{emoji} <b>{short_title}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📥 <b>@TezdaYuklaymanbot</b> orqali yuklab olindi"
            )
 
            try:
                safe_edit("📤 Yuborilmoqda...", chat_id, msg_id)
                dur = int(task.get('duration') or 0)
                with open(file_path, 'rb') as f:
                    if dtype == 'vid':
                        kw = {}
                        if task.get('width') and task.get('height'):
                            kw.update(width=int(task['width']), height=int(task['height']))
                        if dur:
                            kw['duration'] = dur
                        bot.send_video(
                            chat_id, f, caption=caption,
                            supports_streaming=True, timeout=180, **kw
                        )
                    else:
                        kw = {}
                        if dur:
                            kw['duration'] = dur
                        if task.get('uploader'):
                            kw['performer'] = task['uploader']
                        bot.send_audio(
                            chat_id, f, caption=caption,
                            title=title or 'Audio', timeout=180, **kw
                        )
                try:
                    bot.delete_message(chat_id, msg_id)
                except Exception:
                    pass
                db_inc_dl(c.from_user.id)
                logger.info(f"✅ Yuborildi: uid={c.from_user.id} tip={dtype} fayl={os.path.basename(file_path)}")
            except Exception as e:
                logger.error(f"[send error] {e}")
                fail(f"❌ Yuborishda xatolik:\n<code>{html.escape(str(e)[:200])}</code>\n\n"
                     f"📞 Yordam: {AUTHOR}")
        finally:
            # 🗑️  SERVERDA KESH QOLMASIN — DARHOL O'CHIRISH
            task['dead'] = True
            clean_prefix(f"vid_{tid}")
            clean_prefix(f"aud_{tid}")
            tasks.pop(tid, None)
 
    threading.Thread(target=finalize, daemon=True).start()
 
 
# ═══════════════════════════════════════════════════════════════
#  🎵  SHAZAM — QO'SHIQ ANIQLASH
# ═══════════════════════════════════════════════════════════════
def _prep_audio_for_shazam(src: str) -> str:
    """Birinchi 25 soniyani 16 kHz mono WAV'ga o'giradi: tezroq va ogg/opus ham ishlaydi."""
    if not FFMPEG_PATH:
        return src
    dst = src + ".wav"
    try:
        r = subprocess.run(
            [FFMPEG_PATH, '-y', '-v', 'error', '-i', src, '-t', '25',
             '-vn', '-ac', '1', '-ar', '16000', dst],
            capture_output=True, timeout=60
        )
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 1000:
            return dst
    except Exception:
        pass
    clean(dst)
    return src
 
 
@bot.message_handler(
    content_types=['audio', 'voice', 'video', 'video_note'],
    func=lambda m: m.chat.type == 'private'      # guruhlarda har bir videoga Shazam ishlamasin
)
@require_sub
def handle_media_shazam(message):
    db_save_user(message.from_user)
    uid = message.from_user.id
 
    if not SHAZAM_OK:
        bot.reply_to(message, "❌ Shazam moduli o'rnatilmagan.\nPip: <code>pip install shazamio</code>")
        return
 
    status_msg = bot.reply_to(message, "🔍 <i>Qo'shiq qidirilmoqda...</i>")
 
    ct = message.content_type
    if ct == 'audio':      file_id = message.audio.file_id
    elif ct == 'voice':    file_id = message.voice.file_id
    elif ct == 'video':    file_id = message.video.file_id
    else:                  file_id = message.video_note.file_id
 
    def process():
        tmp = os.path.join(TEMP_DIR, f"shazam_{uid}_{int(time.time())}.tmp")
        try:
            fi   = bot.get_file(file_id)
            data = bot.download_file(fi.file_path)
            with open(tmp, 'wb') as f:
                f.write(data)
 
            src  = _prep_audio_for_shazam(tmp)
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                out = loop.run_until_complete(shazam_engine.recognize(src))
            finally:
                loop.close()
 
            if 'track' in out:
                track  = out['track']
                title  = track.get('title', 'Noma\'lum')
                artist = track.get('subtitle', 'Noma\'lum')
                genre  = track.get('genres', {}).get('primary', '')
                img    = track.get('images', {}).get('coverart', '')
 
                # Qo'shiq matni (dastlabki 10 satr)
                lyrics_lines = []
                for section in track.get('sections', []):
                    if section.get('type') == 'LYRICS':
                        lyrics_lines = section.get('text', [])[:10]
                        break
 
                result = (
                    f"🎵 <b>Qo'shiq topildi!</b>\n\n"
                    f"🎤 <b>Ijrochi:</b> {html.escape(artist)}\n"
                    f"🎵 <b>Nomi:</b>    {html.escape(title)}\n"
                )
                if genre:
                    result += f"🎸 <b>Janr:</b>    {html.escape(genre)}\n"
                if lyrics_lines:
                    result += (
                        "\n📝 <b>Qo'shiq matni (bir qismi):</b>\n"
                        + "<i>" + html.escape("\n".join(lyrics_lines)) + "</i>"
                    )
 
                yt_q = urllib.parse.quote_plus(f"{artist} {title}")
                sp_q = urllib.parse.quote(f"{artist} {title}")
                kb = types.InlineKeyboardMarkup(row_width=1)
                kb.add(
                    types.InlineKeyboardButton(
                        "▶️ YouTube Music'da topish",
                        url=f"https://music.youtube.com/search?q={yt_q}"
                    ),
                    types.InlineKeyboardButton(
                        "🔍 Spotifyda qidirish",
                        url=f"https://open.spotify.com/search/{sp_q}"
                    )
                )
                bot.edit_message_text(
                    result, uid, status_msg.message_id, reply_markup=kb
                )
            else:
                bot.edit_message_text(
                    "❌ Qo'shiq aniqlanmadi.\n\n"
                    "💡 Sifatliroq audio yuboring yoki shovqinsiz joyda yozib ko'ring.",
                    uid, status_msg.message_id
                )
 
        except Exception as e:
            logger.error(f"Shazam error: {e}")
            too_big = 'too big' in str(e).lower()
            safe_edit(
                "❌ Fayl 20 MB dan katta — qisqaroq audio yuboring." if too_big
                else "❌ Qidirishda xatolik yuz berdi.",
                uid, status_msg.message_id
            )
        finally:
            clean(tmp, tmp + ".wav")
 
    threading.Thread(target=process, daemon=True).start()
 
 
# ═══════════════════════════════════════════════════════════════
#  👑  ADMIN PANEL
# ═══════════════════════════════════════════════════════════════
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS
 
 
def admin_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="adm:users"),
        types.InlineKeyboardButton("📊 Statistika",       callback_data="adm:stats")
    )
    kb.add(
        types.InlineKeyboardButton("📢 Reklama yuborish", callback_data="adm:broadcast"),
        types.InlineKeyboardButton("📺 Kanallar",         callback_data="adm:channels")
    )
    kb.add(
        types.InlineKeyboardButton("➕ Kanal qo'shish",   callback_data="adm:addch"),
        types.InlineKeyboardButton("➖ Kanal o'chirish",  callback_data="adm:delch")
    )
    return kb
 
 
def send_admin_panel(chat_id, message_id=None):
    users, dls = db_stats()
    channels   = db_channels()
    text = (
        f"👑 <b>ADMIN PANEL</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users}</b>\n"
        f"📥 Jami yuklamalar:  <b>{dls}</b>\n"
        f"📺 Majburiy kanallar: <b>{len(channels)}</b>\n"
        f"⏱ {datetime.now().strftime('%d.%m.%Y  %H:%M:%S')}"
    )
    kb = admin_keyboard()
    try:
        if message_id:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
        else:
            bot.send_message(chat_id, text, reply_markup=kb)
    except Exception:
        bot.send_message(chat_id, text, reply_markup=kb)
 
 
@bot.message_handler(commands=['admin', 'panel'])
def cmd_admin(message):
    if not is_admin(message.from_user.id):
        return
    send_admin_panel(message.chat.id)
 
 
@bot.message_handler(commands=['addchannel'])
def cmd_add_channel(message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Format: /addchannel @kanal_nomi [Sarlavha]")
        return
    ch    = parts[1] if parts[1].startswith('@') else '@' + parts[1]
    title = parts[2] if len(parts) > 2 else ch
    db_add_channel(ch, title)
    bot.send_message(message.chat.id, f"✅ Kanal qo'shildi: <b>{ch}</b> ({title})")
 
 
@bot.message_handler(commands=['delchannel'])
def cmd_del_channel(message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Format: /delchannel @kanal_nomi")
        return
    ch = parts[1] if parts[1].startswith('@') else '@' + parts[1]
    db_del_channel(ch)
    bot.send_message(message.chat.id, f"✅ Kanal o'chirildi: <b>{ch}</b>")
 
 
@bot.message_handler(commands=['channels'])
def cmd_channels(message):
    if not is_admin(message.from_user.id): return
    channels = db_channels()
    if not channels:
        bot.send_message(message.chat.id, "📭 Hech qanday kanal yo'q.")
        return
    text = "📺 <b>Majburiy kanallar:</b>\n\n"
    for ch, title in channels:
        text += f"• <code>{ch}</code> — {title}\n"
    bot.send_message(message.chat.id, text)
 
 
@bot.message_handler(commands=['stats'])
def cmd_stats(message):
    if not is_admin(message.from_user.id): return
    users, dls = db_stats()
    channels   = db_channels()
    bot.send_message(
        message.chat.id,
        f"📊 <b>Bot Statistikasi</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users}</b>\n"
        f"📥 Jami yuklamalar: <b>{dls}</b>\n"
        f"📺 Kanallar: <b>{len(channels)}</b>"
    )
 
 
@bot.message_handler(commands=['users'])
def cmd_users(message):
    if not is_admin(message.from_user.id): return
    rows = db_recent_users(30)
    text = "👥 <b>So'nggi 30 foydalanuvchi:</b>\n\n"
    for uid, uname, fname, dl, last in rows:
        name = f"@{uname}" if uname else fname or "—"
        text += f"• {name} | <code>{uid}</code> | 📥{dl}\n"
    # 4096 belgi chegarasi
    bot.send_message(message.chat.id, text[:4090])
 
 
@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if not is_admin(message.from_user.id): return
    adm_state[message.from_user.id] = {'state': 'wait_broadcast'}
    bot.send_message(
        message.chat.id,
        "📣 <b>Reklama yuborish</b>\n\n"
        "Barchaga yuboriladigan xabarni yuboring:\n"
        "(Matn, rasm, video yoki istalgan tur)\n\n"
        "Bekor qilish: /cancel"
    )
 
 
@bot.message_handler(commands=['cancel'])
def cmd_cancel(message):
    uid = message.from_user.id
    if uid in adm_state:
        del adm_state[uid]
    bot.send_message(message.chat.id, "❌ Bekor qilindi.")
 
 
# ─────────────────────────────────────────────────────────────
#  Admin inline callbacks
# ─────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("adm:"))
def cb_admin(c):
    uid = c.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(c.id, "❌ Ruxsat yo'q!")
        return
    bot.answer_callback_query(c.id)
 
    action = c.data[4:]   # after "adm:"
 
    if action == "stats":
        users, dls = db_stats()
        channels = db_channels()
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔄 Yangilash", callback_data="adm:stats"))
        kb.add(types.InlineKeyboardButton("◀️ Orqaga",   callback_data="adm:back"))
        bot.edit_message_text(
            f"📊 <b>Bot Statistikasi</b>\n\n"
            f"👥 Foydalanuvchilar: <b>{users}</b>\n"
            f"📥 Jami yuklamalar: <b>{dls}</b>\n"
            f"📺 Kanallar: <b>{len(channels)}</b>\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            uid, c.message.message_id, reply_markup=kb
        )
 
    elif action == "users":
        rows = db_recent_users(20)
        text = "👥 <b>So'nggi 20 foydalanuvchi:</b>\n\n"
        for u_uid, uname, fname, dl, last in rows:
            name = f"@{uname}" if uname else fname or "—"
            text += f"• {name} | <code>{u_uid}</code> | 📥{dl}\n"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("◀️ Orqaga", callback_data="adm:back"))
        bot.edit_message_text(text[:4090], uid, c.message.message_id, reply_markup=kb)
 
    elif action == "broadcast":
        adm_state[uid] = {'state': 'wait_broadcast', 'msg_id': c.message.message_id}
        bot.edit_message_text(
            "📣 <b>Reklama yuborish</b>\n\n"
            "Barchaga yuboriladigan xabarni yuboring:\n"
            "(Matn, rasm, video yoki istalgan tur)\n\n"
            "Bekor qilish: /cancel",
            uid, c.message.message_id
        )
 
    elif action == "channels":
        channels = db_channels()
        if not channels:
            text = "📭 Hech qanday faol kanal yo'q."
        else:
            text = "📺 <b>Majburiy kanallar:</b>\n\n"
            for ch, title in channels:
                text += f"• <code>{ch}</code> — {title}\n"
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("◀️ Orqaga", callback_data="adm:back"))
        bot.edit_message_text(text, uid, c.message.message_id, reply_markup=kb)
 
    elif action == "addch":
        adm_state[uid] = {'state': 'wait_addch', 'msg_id': c.message.message_id}
        bot.edit_message_text(
            "➕ <b>Kanal qo'shish</b>\n\n"
            "Formatda yuboring:\n"
            "<code>@kanal_nomi Kanal Sarlavhasi</code>\n\n"
            "Misol:\n<code>@texnologiya_uz Texnologiya UZ</code>\n\n"
            "Bekor qilish: /cancel",
            uid, c.message.message_id
        )
 
    elif action == "delch":
        channels = db_channels()
        if not channels:
            bot.answer_callback_query(c.id, "Hech qanday kanal yo'q!", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for ch, title in channels:
            kb.add(types.InlineKeyboardButton(
                f"❌ {title or ch}",
                callback_data=f"adm:confirm_del:{ch}"
            ))
        kb.add(types.InlineKeyboardButton("◀️ Orqaga", callback_data="adm:back"))
        bot.edit_message_text(
            "➖ <b>O'chiriladigan kanalni tanlang:</b>",
            uid, c.message.message_id, reply_markup=kb
        )
 
    elif action.startswith("confirm_del:"):
        ch = action.split(":", 1)[1]
        db_del_channel(ch)
        bot.answer_callback_query(c.id, f"✅ {ch} o'chirildi!", show_alert=True)
        send_admin_panel(uid, c.message.message_id)
 
    elif action == "back":
        send_admin_panel(uid, c.message.message_id)
 
 
# ─────────────────────────────────────────────────────────────
#  Public inline callbacks
# ─────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "chk_sub")
def cb_chk_sub(c):
    uid = c.from_user.id
    if is_subscribed(uid):
        bot.answer_callback_query(c.id, "✅ Obuna tasdiqlandi!", show_alert=True)
        try:
            bot.delete_message(uid, c.message.message_id)
        except Exception: pass
        bot.send_message(uid, "🚀 Yuklab olish uchun havola yuboring!")
    else:
        bot.answer_callback_query(
            c.id, "❌ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True
        )
 
 
@bot.callback_query_handler(func=lambda c: c.data == "cb_help")
def cb_help(c):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🏠 Bosh menyu", callback_data="cb_main"))
    bot.edit_message_text(HELP_TEXT, c.from_user.id, c.message.message_id, reply_markup=kb)
 
 
@bot.callback_query_handler(func=lambda c: c.data == "cb_main")
def cb_main(c):
    uid = c.from_user.id
    kb  = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("❓ Yordam",     callback_data="cb_help"),
        types.InlineKeyboardButton("📊 Statistika", callback_data="cb_stats")
    )
    kb.add(
        types.InlineKeyboardButton(
            "➕ Guruhga qo'shish",
            url="https://t.me/TezdaYuklaymanbot?startgroup=true"
        )
    )
    try:
        bot.edit_message_text(
            WELCOME.format(bot=BOT_NAME), uid, c.message.message_id,
            reply_markup=kb, disable_web_page_preview=True
        )
    except Exception: pass
 
 
@bot.callback_query_handler(func=lambda c: c.data == "cb_stats")
def cb_stats(c):
    users, dls = db_stats()
    bot.answer_callback_query(
        c.id,
        f"📊 Bot statistikasi\n\n"
        f"👥 Foydalanuvchilar: {users}\n"
        f"📥 Jami yuklamalar: {dls}",
        show_alert=True
    )
 
 
# ═══════════════════════════════════════════════════════════════
#  📣  BARCHA MATN XABARLARI HANDLER (broadcast state + URL)
# ═══════════════════════════════════════════════════════════════
@bot.message_handler(
    content_types=['text', 'photo', 'video', 'document', 'animation', 'sticker']
)
def handle_all(message):
    uid = message.from_user.id
    db_save_user(message.from_user)
 
    # ── URL tekshiruvi ──
    url = extract_url(message.text or '')
    if url:
        handle_url(message)
        return
 
    # ── Boshqa matnlar uchun hint ──
    if message.chat.type == 'private':
        bot.send_message(
            uid,
            "💡 Video havola yuboring yoki /help buyrug'ini bosing.",
        )
 
 
# ═══════════════════════════════════════════════════════════════
#  🚀  ISHGA TUSHIRISH
# ═══════════════════════════════════════════════════════════════
def bot_worker():
    """Telegram polling. Baza bo'sh bo'lsa avval zaxiradan tiklaydi; uzilsa o'zi qayta ulanadi."""
    try:
        restore_db_if_empty()
    except Exception as e:
        logger.warning(f"restore: {e}")
    while True:
        try:
            bot.infinity_polling(
                timeout=60,
                long_polling_timeout=60,
                skip_pending=True,          # restartdan keyin eski xabarlar to'planib qolmasin
                logger_level=logging.WARNING,
                allowed_updates=['message', 'callback_query'],
            )
        except Exception as e:
            logger.error(f"Polling xatosi: {e}")
            time.sleep(5)
 
 
def _on_sigterm(signum, frame):
    """Render deploy/restart paytida: bazani zaxiralab chiqamiz."""
    logger.info("🛑 SIGTERM — baza zaxirasi olinmoqda...")
    try:
        if DB_DIRTY.is_set():
            backup_db("shutdown")
    except Exception:
        pass
    os._exit(0)
 
 
if __name__ == '__main__':
    logger.info("═" * 60)
    logger.info("🚀  TEZDA YUKLAYMAN BOT  v2.0 — ishga tushmoqda...")
    logger.info("═" * 60)
 
    init_db()
    logger.info("✅ Ma'lumotlar bazasi tayyor.")
    sweep_orphans(max_age=0)          # oldingi ishga tushishdan qolgan fayllarni tozalash
 
    logger.info(f"🎞  ffmpeg: {FFMPEG_PATH or 'TOPILMADI — video/audio birlashtirish cheklangan!'}")
    if not shutil.which("deno"):
        logger.warning("⚠️ deno topilmadi — YouTube yuklashlari ishlamasligi mumkin "
                       "(requirements.txt da yt-dlp[default,deno] bo'lishi kerak)")
    logger.info(f"🤖 Bot: {BOT_NAME}")
    logger.info(f"👑 Admin ID: {ADMIN_IDS}")
    logger.info(f"📂 Temp papka: {TEMP_DIR}")
    logger.info("═" * 60)
 
    signal.signal(signal.SIGTERM, _on_sigterm)
 
    threading.Thread(target=bot_worker,      daemon=True, name="bot").start()
    threading.Thread(target=janitor_loop,    daemon=True, name="janitor").start()
    threading.Thread(target=keep_alive_loop, daemon=True, name="keepalive").start()
 
    run_web_server()      # Flask (waitress) — asosiy oqimda, Render shu portni tekshiradi
