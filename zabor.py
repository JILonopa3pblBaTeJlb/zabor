import os
import io
import json
import asyncio
import tempfile
import traceback
import re
import urllib.parse
import hashlib
import sqlite3
from PIL import Image
from typing import List, Optional, Iterable
from telethon import TelegramClient
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command
from antibayan import get_media_fingerprint, hamming_distance, extract_video_frame, quick_fingerprint  # Импорт из antibayan


with open("config.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

API_ID = CONFIG["API_ID"]
API_HASH = CONFIG["API_HASH"]
SESSION_NAME = CONFIG["SESSION_NAME"]

BOT_TOKEN = CONFIG["BOT_TOKEN"]

ZABORISTOE = CONFIG["ZABORISTOE"]
IPNTZ = CONFIG["IPNTZ"]
DOPAMINE = CONFIG["DOPAMINE"]

ADMINS_FILE = CONFIG["ADMINS_FILE"]
DB_FILE = CONFIG["DB_FILE"]
SEEN_DB_FILE = CONFIG.get("SEEN_DB_FILE", "seen.db")  # SQLite для seen

_YT_URL_RE = re.compile(r"(https?://(?:www\.)?(?:youtube\.com|youtu\.be)[^\s\)\]\}]+)", flags=re.IGNORECASE)


# ========== Telethon ==========
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ========== Aiogram ==========
bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()


# ========== База мониторинга каналов (JSON) ==========
if not os.path.exists(DB_FILE):
    DB = {"monitored": {}}
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(DB, f, ensure_ascii=False, indent=2)
else:
    with open(DB_FILE, "r", encoding="utf-8") as f:
        try:
            DB = json.load(f)
        except:
            DB = {"monitored": {}}

DB_LOCK = asyncio.Lock()

# ========== SQLite для seen fingerprints ==========
def init_seen_database():
    """Инициализирует SQLite базу для seen fingerprints"""
    conn = sqlite3.connect(SEEN_DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    
    # Таблица с fingerprint как hex (64 chars)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS seen_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE NOT NULL,
            chat_id INTEGER,
            msg_id INTEGER,
            username TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT
        )
    """)
    
    # Индексы
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fingerprint ON seen_media(fingerprint)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg ON seen_media(chat_id, msg_id)")
    
    conn.commit()
    conn.close()
    print("[SQLite] ✅ База данных инициализирована")


init_seen_database()

SEEN_DB_LOCK = asyncio.Lock()


async def store_seen(fp: str, meta: dict):
    """Сохраняет fingerprint в SQLite"""
    async with SEEN_DB_LOCK:
        try:
            conn = sqlite3.connect(SEEN_DB_FILE)
            cursor = conn.cursor()
            
            chat_id = meta.get('chat_id')
            msg_id = meta.get('msg_id')
            username = meta.get('username')
            metadata_json = json.dumps(meta, ensure_ascii=False)
            
            cursor.execute("""
                INSERT OR IGNORE INTO seen_media 
                (fingerprint, chat_id, msg_id, username, metadata)
                VALUES (?, ?, ?, ?, ?)
            """, (fp, chat_id, msg_id, username, metadata_json))
            
            conn.commit()
            conn.close()
            print(f"[store_seen] {fp[:16]}... сохранён в SQLite")
        except Exception as e:
            print(f"[store_seen ERROR] {e}")
            traceback.print_exc()


def seen_fingerprint(fp: str) -> bool:
    """Проверяет точное совпадение fingerprint в SQLite"""
    try:
        conn = sqlite3.connect(SEEN_DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT id FROM seen_media WHERE fingerprint = ? LIMIT 1",
            (fp,)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        return result is not None
    except Exception as e:
        print(f"[seen_fingerprint ERROR] {e}")
        return False


def seen_fingerprint_similar(fp: str, threshold: int = 15) -> bool:
    """
    Проверяет, есть ли похожий fingerprint в базе (Hamming <= threshold).
    Линейный скан — для большой базы добавить ANN (annoy/faiss).
    """
    try:
        conn = sqlite3.connect(SEEN_DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT fingerprint FROM seen_media")
        all_hashes = [row[0] for row in cursor.fetchall()]
        conn.close()

        for old_fp in all_hashes:
            dist = hamming_distance(fp, old_fp)
            if dist <= threshold:
                print(f"[bayan] ⚠️ Найден похожий хэш ({old_fp[:16]}...) с расстоянием {dist}")
                return True
        return False
    except Exception as e:
        print(f"[seen_fingerprint_similar ERROR] {e}")
        return False


async def check_and_store_media(media_bytes: bytes = None, file_path: str = None, is_video: bool = False, meta: dict = None) -> bool:
    """
    Проверяет на баян с использованием antibayan и SQL.
    Возвращает True, если новый.
    """
    fp = get_media_fingerprint(media_bytes=media_bytes, file_path=file_path, is_video=is_video)
    if not fp:
        print("[bayan] ❌ Не удалось получить fingerprint")
        return True

    if seen_fingerprint(fp) or seen_fingerprint_similar(fp, threshold=15):
        print("[bayan] ⚠️ Баян, пропускаем")
        return False

    await store_seen(fp, meta or {})
    print(f"[bayan] ✅ Новый контент ({fp[:16]}...)")
    return True


def get_seen_stats() -> dict:
    """Статистика по seen базе"""
    try:
        conn = sqlite3.connect(SEEN_DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM seen_media")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM seen_media WHERE created_at >= datetime('now', '-1 day')")
        last_24h = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM seen_media WHERE created_at >= datetime('now', '-7 days')")
        last_7d = cursor.fetchone()[0]
        
        conn.close()
        
        return {'total': total, 'last_24h': last_24h, 'last_7d': last_7d}
    except Exception as e:
        print(f"[get_seen_stats ERROR] {e}")
        return {'total': 0, 'last_24h': 0, 'last_7d': 0}


async def cleanup_old_seen(days: int = 90):
    """Удаляет старые записи"""
    async with SEEN_DB_LOCK:
        try:
            conn = sqlite3.connect(SEEN_DB_FILE)
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM seen_media WHERE created_at < datetime('now', '-' || ? || ' days')", (days,))
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            
            print(f"[cleanup] 🗑️ Удалено {deleted} старых записей")
            return deleted
        except Exception as e:
            print(f"[cleanup ERROR] {e}")
            return 0


# ========== Админы ==========
if os.path.exists(ADMINS_FILE):
    with open(ADMINS_FILE, "r", encoding="utf-8") as f:
        ADMINS = set(int(line.strip()) for line in f if line.strip())
else:
    ADMINS = set()

def is_admin(user_id):
    return user_id in ADMINS

# ========== Хеширование ==========
def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# ========== Мониторинг каналов ==========
async def add_monitored(channel):
    async with DB_LOCK:
        if channel not in DB["monitored"]:
            DB["monitored"][channel] = {"last_id": 0, "channel_id": None, "username": None}
            with open(DB_FILE, "w", encoding="utf-8") as f:
                json.dump(DB, f, ensure_ascii=False, indent=2)
            print(f"[DB] Добавлен канал: {channel}")
            return True
        print(f"[DB] Канал уже в списке: {channel}")
        return False

async def remove_monitored(channel):
    async with DB_LOCK:
        if channel in DB["monitored"]:
            del DB["monitored"][channel]
            with open(DB_FILE, "w", encoding="utf-8") as f:
                json.dump(DB, f, ensure_ascii=False, indent=2)
            print(f"[DB] Удалён канал: {channel}")
            return True
        print(f"[DB] Канал не найден: {channel}")
        return False

def get_monitored_keys():
    return list(DB["monitored"].keys())

async def set_last_id(channel, msg_id):
    async with DB_LOCK:
        if channel in DB["monitored"]:
            DB["monitored"][channel]["last_id"] = msg_id
            with open(DB_FILE, "w", encoding="utf-8") as f:
                json.dump(DB, f, ensure_ascii=False, indent=2)

# ========== Helpers ==========
def get_chat_identifier(chat):
    chat_id = getattr(chat, "id", None)
    username = getattr(chat, "username", None)
    if username:
        username = username.lstrip("@")
    return chat_id, username
    

def load_ignore_words():
    if not os.path.exists("ignored.txt"):
        return []
    with open("ignored.txt", "r", encoding="utf-8") as f:
        return [line.strip().lower() for line in f if line.strip()]

def add_ignore_word(word: str) -> bool:
    word = word.strip().lower()
    if not word:
        return False
    words = load_ignore_words()
    if word in words:
        return False
    with open("ignored.txt", "a", encoding="utf-8") as f:
        f.write(word + "\n")
    return True

ignore_words = load_ignore_words()

def extract_youtube_links(text: str) -> List[str]:

    if not text:
        return []

    found = _YT_URL_RE.findall(text)
    normalized = []
    seen = set()

    for raw in found:
        raw = raw.rstrip(".,;:!?)]}")

        try:
            parsed = urllib.parse.urlparse(raw)
            netloc = parsed.netloc.lower()
            video_id = None
            t_param = None

            if "youtu.be" in netloc:
                video_id = parsed.path.lstrip("/")
                qs = urllib.parse.parse_qs(parsed.query)
                t_param = qs.get("t", qs.get("start", [None]))[0] if qs else None

            elif "youtube.com" in netloc:
                path = parsed.path or ""
                if path.startswith("/watch"):
                    qs = urllib.parse.parse_qs(parsed.query)
                    video_id = qs.get("v", [None])[0]
                    t_param = qs.get("t", qs.get("start", [None]))[0] if qs else None
                elif path.startswith("/shorts/"):
                    parts = path.split("/")
                    if len(parts) >= 3:
                        video_id = parts[2]
                        qs = urllib.parse.parse_qs(parsed.query)
                        t_param = qs.get("t", [None])[0] if qs else None
                else:
                    qs = urllib.parse.parse_qs(parsed.query)
                    video_id = qs.get("v", [None])[0] if qs else None
                    t_param = qs.get("t", [None])[0] if qs else None

            if video_id:
                url = f"https://www.youtube.com/watch?v={video_id}"
                if t_param:
                    url = f"{url}&t={t_param}"
            else:
                url = raw

        except Exception:
            url = raw

        if url not in seen:
            normalized.append(url)
            seen.add(url)

    return normalized


def contains_youtube_link(text: Optional[str]) -> bool:
    if not text:
        return False
    return bool(_YT_URL_RE.search(text))


async def post_youtube_links_as_text(
    bot,
    main_chat_id,
    links: Iterable[str],
    caption: Optional[str] = None,
    other_chat_ids: Optional[Iterable] = None,
    reply_markup=None,
):
    links = list(dict.fromkeys(links))
    if not links:
        return []

    text_lines = links[:]
    if caption:
        text_lines.append(caption)
    full_text_main = "\n".join(text_lines)

    results = []
    
    await rate_limiter.wait_if_needed(main_chat_id)
    res_main = await safe_send(bot.send_message, main_chat_id, full_text_main, reply_markup=reply_markup)
    results.append(res_main)

    if other_chat_ids and links:
        first_link = links[0]
        for cid in other_chat_ids:
            await rate_limiter.wait_if_needed(cid)
            res = await safe_send(bot.send_message, cid, first_link)
            results.append(res)

    return results

# ========== Telegram Rate Limiter ==========
class TelegramRateLimiter:
    def __init__(self):
        self.last_send = {}
        self.min_interval = 0.5
        self.lock = asyncio.Lock()
    
    async def wait_if_needed(self, chat_id):
        async with self.lock:
            now = asyncio.get_event_loop().time()
            if chat_id in self.last_send:
                elapsed = now - self.last_send[chat_id]
                if elapsed < self.min_interval:
                    wait_time = self.min_interval - elapsed
                    await asyncio.sleep(wait_time)
            
            self.last_send[chat_id] = asyncio.get_event_loop().time()

rate_limiter = TelegramRateLimiter()


async def safe_send(send_func, *args, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            return await send_func(*args, **kwargs)
        except Exception as e:
            error_msg = str(e).lower()
            
            if "too many requests" in error_msg or "retry after" in error_msg:
                import re
                match = re.search(r'retry after (\d+)', error_msg)
                retry_after = int(match.group(1)) if match else 5
                
                print(f"[RATE LIMIT] Ждем {retry_after} сек (попытка {attempt + 1}/{max_retries})")
                await asyncio.sleep(retry_after + 1)
                continue
            
            if attempt == max_retries - 1:
                raise
            
            print(f"[SEND ERROR] {e}, повтор через 2 сек")
            await asyncio.sleep(2)
    
    raise Exception(f"Не удалось отправить после {max_retries} попыток")


# ========== Process message ==========
async def process_message(msg):
    try:
        chat = await msg.get_chat()
        chat_id, username = get_chat_identifier(chat)
        text = msg.message or ""

        if any(word in text.lower() for word in ignore_words):
            print(f"[IGNORE] Пост {msg.id} пропущен (стоп-слово)")
            return

        if len(text) > 100:
            print(f"[IGNORE] Пост {msg.id} пропущен (слишком длинный оригинальный текст)")
            return

        if getattr(msg, "web_preview", None):
            print(f"[IGNORE] Пост {msg.id} пропущен (telegram preview)")
            return

        link = f"https://t.me/{username}/{msg.id}" if username else ""
        caption = text if text else ""
        if username:
            caption += f"\n\n🔎 Источник: @{username}\n{link}"

        yt_links = extract_youtube_links(text)
        if yt_links:
            await post_youtube_links_as_text(
                bot,
                ZABORISTOE,
                yt_links,
                caption=caption,
                other_chat_ids=[DOPAMINE],
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="Класс!", callback_data=f"like_post:{msg.id}:{chat_id}")]]
                ),
            )
            return

        has_link = "http://" in caption or "https://" in caption

        if getattr(msg, "grouped_id", None) is not None:
            print(f"[IGNORE] Пост {msg.id} пропущен (галерея)")
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Класс!", callback_data=f"like_post:{msg.id}:{chat_id}")]]
        )

        if msg.media:
            if hasattr(msg, "web_preview") and msg.web_preview:
                print(f"[IGNORE] Пост {msg.id} пропущен (link preview media)")
                return

            os.makedirs("tmp", exist_ok=True)
            
            tmp_path = await client.download_media(msg.media, file=os.path.join("tmp", f"{msg.id}"))

            if not tmp_path or not os.path.exists(tmp_path):
                print(f"[media] ❌ Не удалось скачать медиа из {username}")
                return

            is_image = getattr(msg.media, 'photo', None) is not None
            is_document = getattr(msg.media, 'document', None) is not None
            mime_type = getattr(msg.media.document, 'mime_type', '') if is_document else ''
            
            is_gif = False
            is_video = False
            
            if is_document:
                attributes = getattr(msg.media.document, 'attributes', [])
                
                for attr in attributes:
                    attr_name = attr.__class__.__name__
                    if 'Animated' in attr_name:
                        is_gif = True
                        print(f"[media] Найден атрибут {attr_name} - это анимация")
                        break
                
                if not is_gif and mime_type.startswith("video/"):
                    is_video = True
                    print(f"[media] MIME: {mime_type} - это видео")
                elif is_gif:
                    print(f"[media] MIME: {mime_type} - это анимация/GIF")
            
            # === АНТИБАЯН с antibayan и SQL ===
            should_check_bayan = is_image or is_gif or is_video
            
            if should_check_bayan:
                meta = {"chat_id": chat_id, "msg_id": msg.id, "username": username}
                if is_video or is_gif:
                    is_new = await check_and_store_media(file_path=tmp_path, is_video=True, meta=meta)
                else:
                    with open(tmp_path, "rb") as f:
                        media_bytes = f.read()
                    is_new = await check_and_store_media(media_bytes=media_bytes, meta=meta)
                
                if not is_new:
                    os.remove(tmp_path)
                    return
            # === /АНТИБАЯН ===

            force_file = is_document and not (is_gif or is_video) and has_link

            try:
                if is_video:
                    await rate_limiter.wait_if_needed(ZABORISTOE)
                    await safe_send(bot.send_video, ZABORISTOE, FSInputFile(tmp_path), caption=caption, supports_streaming=True, reply_markup=keyboard)
                    
                    await rate_limiter.wait_if_needed(DOPAMINE)
                    await safe_send(bot.send_video, DOPAMINE, FSInputFile(tmp_path), supports_streaming=True)
                    
                elif is_gif:
                    await rate_limiter.wait_if_needed(ZABORISTOE)
                    await safe_send(bot.send_animation, ZABORISTOE, FSInputFile(tmp_path), caption=caption, reply_markup=keyboard)
                    
                    await rate_limiter.wait_if_needed(DOPAMINE)
                    await safe_send(bot.send_animation, DOPAMINE, FSInputFile(tmp_path))
                    
                elif is_image:
                    await rate_limiter.wait_if_needed(ZABORISTOE)
                    await safe_send(bot.send_photo, ZABORISTOE, FSInputFile(tmp_path), caption=caption, reply_markup=keyboard)
                    
                    await rate_limiter.wait_if_needed(DOPAMINE)
                    await safe_send(bot.send_photo, DOPAMINE, FSInputFile(tmp_path))
                    
                else:
                    await rate_limiter.wait_if_needed(ZABORISTOE)
                    await safe_send(bot.send_document, ZABORISTOE, FSInputFile(tmp_path), caption=caption, reply_markup=keyboard)
                    
                    await rate_limiter.wait_if_needed(DOPAMINE)
                    await safe_send(bot.send_document, DOPAMINE, FSInputFile(tmp_path))
                    
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        elif text.strip():
            await rate_limiter.wait_if_needed(ZABORISTOE)
            await safe_send(bot.send_message, ZABORISTOE, caption, reply_markup=keyboard)

        await asyncio.sleep(3)

    except Exception as e:
        print(f"[PROCESS ERROR] {msg.id} → {e}")
        traceback.print_exc()
        
@dp.callback_query(lambda c: c.data and c.data.startswith("like_post:"))
async def callback_like_post(query: types.CallbackQuery):
    try:
        parts = query.data.split(":")
        if len(parts) != 3:
            await query.answer("❌ Некорректные данные")
            return

        message_id = int(parts[1])
        chat_id = int(parts[2])

        msg = await client.get_messages(chat_id, ids=message_id)

        if msg.media:
            tmp_path = await client.download_media(msg.media, file=os.path.join("tmp", f"like_{msg.id}"))

            is_image = getattr(msg.media, 'photo', None) is not None
            is_document = getattr(msg.media, 'document', None) is not None
            is_gif = is_document and getattr(msg.media.document, 'mime_type', '') == 'video/mp4' and getattr(msg.media.document, 'attributes', [])
            is_video = is_document and not is_gif and getattr(msg.media.document, 'mime_type', '').startswith("video/")

            if is_image:
                await bot.send_photo(chat_id=IPNTZ, photo=FSInputFile(tmp_path))
            elif is_gif:
                await bot.send_animation(chat_id=IPNTZ, animation=FSInputFile(tmp_path))
            elif is_video:
                await bot.send_video(chat_id=IPNTZ, video=FSInputFile(tmp_path), supports_streaming=True)
            else:
                await bot.send_document(chat_id=IPNTZ, document=FSInputFile(tmp_path))

            os.remove(tmp_path)
        elif msg.message:
            await bot.send_message(IPNTZ, msg.message)

        await query.message.edit_reply_markup(None)
        await query.answer("✓ Отправлено в IPNTZ")

        await asyncio.sleep(1)

    except Exception as e:
        print(f"[LIKE CALLBACK ERROR] {e}")
        await query.answer("❌ Ошибка")
        
# ========== Периодический опрос каналов ==========
async def poll_monitored_channels():
    await client.start()
    print("[Poller] ✓ Цикл мониторинга запущен")
    
    channel_index = 0
    
    while True:
        try:
            monitored_keys = get_monitored_keys()
            
            if not monitored_keys:
                await asyncio.sleep(60)
                continue
            
            total_channels = len(monitored_keys)
            
            if total_channels < 60:
                print(f"[Poller] Режим: проверка всех {total_channels} каналов")
                for key in monitored_keys:
                    await check_channel(key)
                await asyncio.sleep(60)
            else:
                if channel_index >= total_channels:
                    channel_index = 0
                    print(f"[Poller] Карусель: круг завершен, начинаем заново")
                
                key = monitored_keys[channel_index]
                print(f"[Poller] Карусель [{channel_index + 1}/{total_channels}]: {key}")
                await check_channel(key)
                
                channel_index += 1
                await asyncio.sleep(1)
                
        except Exception as e:
            print(f"[Poller ERROR] {e}")
            await asyncio.sleep(5)


async def check_channel(key):
    last_id = DB["monitored"][key].get("last_id", 0)
    try:
        msgs = await client.get_messages(key, limit=10)
    except Exception as e:
        print(f"[Poll ERROR] {key} → {e}")
        return
    
    msgs = sorted(msgs, key=lambda m: m.id)
    for msg in msgs:
        if msg.id > last_id:
            print(f"[Poll] Новый пост {msg.id} из {key}")
            await process_message(msg)
            await set_last_id(key, msg.id)

# ========== Aiogram команды ==========
@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Только админы.")
        return
    mon = get_monitored_keys()
    msg = "📋 Мониторим:\n" + "\n".join(f"• {ch}" for ch in mon) if mon else "📋 Список пуст."
    await message.reply(msg)
    
@dp.message(Command("stopword"))
async def cmd_stopword(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Только админы.")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Использование: /stopword слово")
        return
    word = args[1].strip()
    if add_ignore_word(word):
        global ignore_words
        ignore_words = load_ignore_words()
        await message.reply(f"✓ Стоп-слово «{word}» добавлено.")
    else:
        await message.reply(f"⚠️ Слово «{word}» уже есть в списке.")

@dp.message(Command("remove"))
async def cmd_remove(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Только админы.")
        return
    args = message.text.split()[1:]
    if not args:
        await message.reply("Использование: /remove @channel или /remove -100xxxxx")
        return
    channel = args[0]
    removed = await remove_monitored(channel)
    await message.reply(f"✓ Канал {channel} удалён." if removed else f"⚠️ Канал {channel} не найден.")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Только админы.")
        return

    seen_stats = get_seen_stats()

    msg = (
        f"📊 Статистика:\n"
        f"• Каналов: {len(get_monitored_keys())}\n"
        f"• Уникальных постов: {seen_stats['total']}\n"
        f"• За 24ч: {seen_stats['last_24h']}\n"
        f"• За 7дн: {seen_stats['last_7d']}"
    )
    await message.reply(msg)

@dp.message()
async def handle_text(message: types.Message):
    if message.chat.type != "private" or not is_admin(message.from_user.id):
        return
    lines = [line.strip() for line in message.text.splitlines() if line.strip()]
    added, skipped = [], []
    for line in lines:
        ch = line.split()[0]
        if ch.startswith("@") or ch.startswith("-100"):
            added.append(ch) if await add_monitored(ch) else skipped.append(ch)
    reply = ""
    if added: reply += "✓ Добавлены:\n" + "\n".join(f"• {c}" for c in added) + "\n"
    if skipped: reply += "ℹ️ Уже в списке:\n" + "\n".join(f"• {c}" for c in skipped)
    if not reply: reply = "🤖 Отправьте @channel или -100xxxxx — добавить"
    await message.reply(reply)

# ========== Main ==========
async def main():
    await client.start()
    print("[Userbot] ✓ Запущен")
    
    # Запускаем polling Aiogram бота параллельно с циклом проверки каналов
    await asyncio.gather(
        dp.start_polling(bot),
        poll_monitored_channels()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Shutdown] Остановка бота...")
    except Exception as e:
        print(f"[Error] Критическая ошибка: {e}")
