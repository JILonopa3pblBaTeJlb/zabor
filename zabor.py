import os
import io
import json
import hashlib
import asyncio
import tempfile
import traceback
import imagehash
from PIL import Image
from telethon import TelegramClient
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command


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

def sha256_image_bytes(b: bytes) -> str:
    img = Image.open(io.BytesIO(b)).convert("RGB")
    return hashlib.sha256(img.tobytes()).hexdigest()
    
def media_fingerprint(media_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(media_bytes))
    phash = imagehash.phash(img)
    return f"media:{str(phash)}"

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
        username = f"@{username}" if not username.startswith("@") else username
    return chat_id, username

# ========== Process message ==========
async def process_message(msg):
    try:
        chat = await msg.get_chat()
        chat_id, username = get_chat_identifier(chat)
        text = msg.message or ""

        link = f"https://t.me/{username}/{msg.id}" if username else ""
        caption = text if text else ""
        if username:
            caption += f"\n\n📎 Источник: {username}\n{link}"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Класс!", callback_data=f"like_post:{msg.id}:{chat_id}")]]
        )

        if msg.media:
            tmp_path = await client.download_media(msg.media, file=os.path.join("tmp", f"{msg.id}"))

            is_image = getattr(msg.media, 'photo', None) is not None
            is_document = getattr(msg.media, 'document', None) is not None
            is_gif = is_document and getattr(msg.media.document, 'mime_type', '') == 'video/mp4' and getattr(msg.media.document, 'attributes', [])
            is_video = is_document and not is_gif and getattr(msg.media.document, 'mime_type', '').startswith("video/")

            # отправляем в ZABORISTOE
            if is_image:
                await bot.send_photo(chat_id=ZABORISTOE, photo=FSInputFile(tmp_path), caption=caption, reply_markup=keyboard)
                await bot.send_photo(chat_id=DOPAMINE, photo=FSInputFile(tmp_path))
            elif is_gif:
                await bot.send_animation(chat_id=ZABORISTOE, animation=FSInputFile(tmp_path), caption=caption, reply_markup=keyboard)
                await bot.send_animation(chat_id=DOPAMINE, animation=FSInputFile(tmp_path))
            elif is_video:
                await bot.send_video(chat_id=ZABORISTOE, video=FSInputFile(tmp_path), caption=caption, supports_streaming=True, reply_markup=keyboard)
                await bot.send_video(chat_id=DOPAMINE, video=FSInputFile(tmp_path), supports_streaming=True)
            else:
                await bot.send_document(chat_id=ZABORISTOE, document=FSInputFile(tmp_path), caption=caption, reply_markup=keyboard)
                await bot.send_document(chat_id=DOPAMINE, document=FSInputFile(tmp_path))

            os.remove(tmp_path)

        elif text.strip():
            # в Zaboristoe кидаем текст, а в Dopamine не трогаем
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
