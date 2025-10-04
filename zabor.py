import os
import io
import json
import asyncio
import tempfile
import traceback
import re
import urllib.parse
import hashlib
from PIL import Image
import imagehash
from typing import List, Optional, Iterable
from telethon import TelegramClient
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command
from antibayan import get_media_fingerprint, is_duplicate


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
SEEN_FILE = CONFIG["SEEN_FILE"]

_YT_URL_RE = re.compile(r"(https?://(?:www\.)?(?:youtube\.com|youtu\.be)[^\s\)\]\}]+)", flags=re.IGNORECASE)


# ========== Telethon ==========
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ========== Aiogram ==========
bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

# ========== База ==========
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

if not os.path.exists(SEEN_FILE):
    SEEN = {}
else:
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        try:
            SEEN = json.load(f)
        except:
            SEEN = {}

DB_LOCK = asyncio.Lock()
SEEN_LOCK = asyncio.Lock()

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
    


async def check_and_store_media(media_bytes: bytes, meta: dict) -> bool:
    """
    Проверяет медиа на баян и, если не найден, сохраняет fingerprint.
    Возвращает True, если файл новый.
    """
    fp = get_media_fingerprint(media_bytes)
    if not fp:
        print("[bayan] ❌ Не удалось получить fingerprint")
        return True

    if is_duplicate(fp, SEEN):
        print("[bayan] ⚠️ Баян, пропускаем")
        return False

    await store_seen(fp, meta)
    print(f"[bayan] ✅ Новый контент ({fp})")
    return True


# ========== Fingerprints ==========
async def store_seen(fp: str, meta: dict):
    async with SEEN_LOCK:
        SEEN[fp] = meta
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(SEEN, f, ensure_ascii=False, indent=2)
        print(f"[store_seen] {fp} сохранён")

def seen_fingerprint(fp: str) -> bool:
    return fp in SEEN

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
        username = username.lstrip("@")  # убираем собачку для URL
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
        # Обрезаем финальную пунктуацию, если она пристроилась в конце.
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
                    # /shorts/VIDEOID
                    parts = path.split("/")
                    # формат: ['', 'shorts', 'VIDEOID', ...]
                    if len(parts) >= 3:
                        video_id = parts[2]
                        qs = urllib.parse.parse_qs(parsed.query)
                        t_param = qs.get("t", [None])[0] if qs else None
                else:
                    # На случай других форм — попробуем вытянуть v из query или оставить raw
                    qs = urllib.parse.parse_qs(parsed.query)
                    video_id = qs.get("v", [None])[0] if qs else None
                    t_param = qs.get("t", [None])[0] if qs else None

            if video_id:
                # нормализуем в canonical watch URL, добавим t если есть
                url = f"https://www.youtube.com/watch?v={video_id}"
                if t_param:
                    # если время задано в виде 1m30s — оставляем как есть; если число — тоже ок
                    url = f"{url}&t={t_param}"
            else:
                # если не удалось распарсить id — оставляем оригинал (без лишних параметров)
                url = raw

        except Exception:
            url = raw

        if url not in seen:
            normalized.append(url)
            seen.add(url)

    return normalized


def contains_youtube_link(text: Optional[str]) -> bool:
    """
    Быстрая проверка — есть ли в тексте хотя бы одна ссылка на YouTube.
    """
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
    """
    Отправляет YouTube-ссылки:
      - В main_chat_id (например, ZABORISTOE) идут все ссылки + подпись + клавиатура.
      - В other_chat_ids (например, DOPAMINE) уходит только первая ссылка, без подписи и без клавы.
    """
    links = list(dict.fromkeys(links))  # сохранить порядок, убрать дубликаты
    if not links:
        return []

    # --- для основного канала (ZABORISTOE) ---
    text_lines = links[:]
    if caption:
        #text_lines.append("")
        text_lines.append(caption)
    full_text_main = "\n".join(text_lines)

    results = []
    res_main = await bot.send_message(main_chat_id, full_text_main, reply_markup=reply_markup)
    results.append(res_main)

    # --- для других каналов (DOPAMINE) ---
    if other_chat_ids and links:
        first_link = links[0]
        for cid in other_chat_ids:
            res = await bot.send_message(cid, first_link)
            results.append(res)

    return results
# ========== Process message ==========
async def process_message(msg):
    try:
        chat = await msg.get_chat()
        chat_id, username = get_chat_identifier(chat)
        text = msg.message or ""

        # Игнорируем посты по стоп-словам
        if any(word in text.lower() for word in ignore_words):
            print(f"[IGNORE] Пост {msg.id} пропущен (стоп-слово)")
            return

        # Игнорируем слишком длинные тексты
        if len(text) > 100:
            print(f"[IGNORE] Пост {msg.id} пропущен (слишком длинный оригинальный текст)")
            return

        # Игнорируем, если есть превью от телеги (link preview)
        if getattr(msg, "web_preview", None):
            print(f"[IGNORE] Пост {msg.id} пропущен (telegram preview)")
            return

        link = f"https://t.me/{username}/{msg.id}" if username else ""
        caption = text if text else ""
        if username:
            caption += f"\n\n🔎 Источник: @{username}\n{link}"

        # Проверка на YouTube ссылки
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
            return  # важно! дальше не идём, чтобы не постить превью как файл

        # Проверяем наличие других ссылок
        has_link = "http://" in caption or "https://" in caption

        # Если пост галерея, игнорим
        if getattr(msg, "grouped_id", None) is not None:
            print(f"[IGNORE] Пост {msg.id} пропущен (галерея)")
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Класс!", callback_data=f"like_post:{msg.id}:{chat_id}")]]
        )

        if msg.media:
            # Игнорируем, если это превью к ссылке (часто как документ/фото)
            if hasattr(msg, "web_preview") and msg.web_preview:
                print(f"[IGNORE] Пост {msg.id} пропущен (link preview media)")
                return

            # Создаём папку tmp если её нет
            os.makedirs("tmp", exist_ok=True)
            
            tmp_path = await client.download_media(msg.media, file=os.path.join("tmp", f"{msg.id}"))

            if not tmp_path or not os.path.exists(tmp_path):
                print(f"[media] ❌ Не удалось скачать медиа из {username}")
                return

            # Определяем тип медиа
            is_image = getattr(msg.media, 'photo', None) is not None
            is_document = getattr(msg.media, 'document', None) is not None
            mime_type = getattr(msg.media.document, 'mime_type', '') if is_document else ''
            
            # Правильная проверка на GIF/анимацию
            is_gif = False
            is_video = False
            
            if is_document:
                attributes = getattr(msg.media.document, 'attributes', [])
                
                # Проверяем наличие атрибута DocumentAttributeAnimated
                for attr in attributes:
                    attr_name = attr.__class__.__name__
                    if 'Animated' in attr_name:
                        is_gif = True
                        print(f"[media] Найден атрибут {attr_name} - это анимация")
                        break
                
                # Если не анимация, проверяем на обычное видео
                if not is_gif and mime_type.startswith("video/"):
                    is_video = True
                    print(f"[media] MIME: {mime_type} - это видео")
                elif is_gif:
                    print(f"[media] MIME: {mime_type} - это анимация/GIF")
            
            # === АНТИБАЯН ===
            # Проверяем картинки, гифки и видео
            should_check_bayan = is_image or is_gif or is_video
            
            if should_check_bayan:
                try:
                    fp = None
                    
                    # Для видео и анимаций всегда извлекаем кадр
                    if is_video or is_gif:
                        media_type = "анимацию" if is_gif else "видео"
                        print(f"[BAYAN CHECK] Проверяем {media_type} {msg.id}")
                        fp = get_media_fingerprint(file_path=tmp_path, is_video=True)
                    
                    # Для обычных фото читаем напрямую
                    else:
                        print(f"[BAYAN CHECK] Проверяем изображение {msg.id}")
                        with open(tmp_path, "rb") as f:
                            media_bytes = f.read()
                        fp = get_media_fingerprint(media_bytes=media_bytes)
                    
                    if fp:
                        if is_duplicate(fp, SEEN):
                            print(f"[BAYAN] ⚠️ Пост {msg.id} пропущен (дубликат контента)")
                            os.remove(tmp_path)
                            return
                        
                        # Сохраняем fingerprint
                        await store_seen(fp, {
                            "chat_id": chat_id,
                            "msg_id": msg.id,
                            "username": username
                        })
                        print(f"[bayan] ✅ Новый контент ({fp})")
                    else:
                        print(f"[BAYAN CHECK] ⚠️ Не удалось создать fingerprint, пропускаем проверку")
                        
                except Exception as e:
                    print(f"[BAYAN CHECK ERROR] {e} - пропускаем проверку")
                    traceback.print_exc()
            # === /АНТИБАЯН ===

            force_file = is_document and not (is_gif or is_video) and has_link

            if is_video:
                await bot.send_video(ZABORISTOE, FSInputFile(tmp_path), caption=caption, supports_streaming=True, reply_markup=keyboard)
                await bot.send_video(DOPAMINE, FSInputFile(tmp_path), supports_streaming=True)
            elif is_gif:
                await bot.send_animation(ZABORISTOE, FSInputFile(tmp_path), caption=caption, reply_markup=keyboard)
                await bot.send_animation(DOPAMINE, FSInputFile(tmp_path))
            elif is_image:
                await bot.send_photo(ZABORISTOE, FSInputFile(tmp_path), caption=caption, reply_markup=keyboard)
                await bot.send_photo(DOPAMINE, FSInputFile(tmp_path))
            else:
                await bot.send_document(ZABORISTOE, FSInputFile(tmp_path), caption=caption, reply_markup=keyboard)
                await bot.send_document(DOPAMINE, FSInputFile(tmp_path))

            os.remove(tmp_path)

        elif text.strip():
            await bot.send_message(ZABORISTOE, caption, reply_markup=keyboard)

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
    while True:
        try:
            for key in get_monitored_keys():
                last_id = DB["monitored"][key].get("last_id", 0)
                try:
                    msgs = await client.get_messages(key, limit=5)
                except Exception as e:
                    print(f"[Poll ERROR] {key} → {e}")
                    continue

                msgs = sorted(msgs, key=lambda m: m.id)
                for msg in msgs:
                    if msg.id > last_id:
                        print(f"[Poll] Новый пост {msg.id} из {key}")
                        await process_message(msg)
                        await set_last_id(key, msg.id)

            await asyncio.sleep(60)  # раз в минуту проверка
        except Exception as e:
            print(f"[Poller ERROR] {e}")
            await asyncio.sleep(5)

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
    msg = f"📊 Статистика:\n• Каналов: {len(get_monitored_keys())}\n• Уникальных постов: {len(SEEN)}"
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
