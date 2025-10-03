# zabor
Бот-вороватор контента из телеграм-каналов (с кнопкой ворования на свой канал)

Обзор архитектуры

Telethon (userbot) — отвечает за чтение сообщений из целевых каналов (polling через client.get_messages) и за скачивание оригинального медиа (client.download_media). Telethon использует API_ID и API_HASH (аккаунт пользователя).
	
Aiogram (бот-агент) — отвечает за взаимодействие с админами (команды /list, /remove, /stats, приём списка каналов в личке) и за публикацию сообщений в целевые каналы (bot.send_photo/send_video/send_animation/send_document/send_message). Aiogram использует BOT_TOKEN.
	
Файлы на диске — db.json (список мониторимых каналов с last_id), seen.json (хранение «увиденных» отпечатков/ID), admins.txt (список админов). Locks — DB_LOCK и SEEN_LOCK (asyncio.Lock) чтобы безопасно писать/читать JSON в асинхронном окружении.
	
Файловая временная папка — tmp (в коде скачанные медиа сохраняются туда и потом удаляются).

 Жизненный цикл (что происходит при запуске)

 1.	Скрипт читает config.json — подставляет API_ID, API_HASH, BOT_TOKEN, id каналов (ZABORISTOE, DOPAMINE, IPNTZ) и имена файлов (DB_FILE, SEEN_FILE, ADMINS_FILE).

2.	Инициализируются TelegramClient (Telethon) и Bot + Dispatcher (Aiogram).

3.	Загружаются/создаются файлы db.json, seen.json, читается admins.txt.

4.	При asyncio.run(main()) запускается:

•	client.start() (Telethon)

•	asyncio.gather(dp.start_polling(bot), poll_monitored_channels()) — параллельно запускаются бот (обработка входящих команд от админов) и цикл опроса каналов.

Как работает опрос каналов (poll_monitored_channels)
	
•	Цикл: бесконечный while True с await asyncio.sleep(60) (проверка раз в минуту).

•	Для каждого ключа (канала) из DB["monitored"] берётся last_id.

•	Выполняется msgs = await client.get_messages(key, limit=5) — последние 5 сообщений.

•	Сообщения сортируются по m.id и для каждого сообщения с msg.id > last_id вызывается process_message(msg) и затем set_last_id(channel, msg.id).

Обработка сообщения (process_message)
	
1.	Получаем chat через await msg.get_chat() и строим chat_id и username.
	
2.	Формируем caption — текст сообщения + ссылка на источник (https://t.me/{username}/{msg.id}) если есть username.
	
3.	Создаём inline-кнопку Класс! с callback_data: like_post:{msg.id}:{chat_id}.
	
4.	Если есть msg.media:
	
•	Скачиваем файл: tmp_path = await client.download_media(msg.media, file=os.path.join("tmp", f"{msg.id}")).
	
•	Определяем тип: is_image, is_document, is_gif, is_video (на основе полей photo, document, mime_type, attributes).
	
•	Отправляем:
	
•	в ZABORISTOE — с подписью caption и с inline-кнопкой;
	
•	в DOPAMINE — тот же файл, но без кнопки/подписи.
	
•	Удаляем временный файл os.remove(tmp_path).
	
5.	Если нет медиа, но есть текст — отправляем текст в ZABORISTOE с кнопкой.
	
6.	После отправки делаем await asyncio.sleep(3) (пауза между отправками).


Обработка нажатия «Класс!» (callback)

•	Декоратор ловит callback_query, где c.data.startswith("like_post:").
	
•	Разбирает message_id и chat_id из callback_data.
	
•	Берёт оригинальное сообщение: msg = await client.get_messages(chat_id, ids=message_id).
	
•	Скачивает медиа (аналогично process_message) и отправляет его в IPNTZ.
	
•	Убирает inline-кнопку у исходного сообщения (await query.message.edit_reply_markup(None)) и отправляет ответ пользователю await query.answer("✓ Отправлено в IPNTZ").

Настройка

1) Получаем API_ID и API_HASH (Telethon — userbot)
	
	1.	Открой: https://my.telegram.org
	
	2.	Войди под тем номером телефона, который будет юзать userbot (тот аккаунт будет читать каналы).
	
	3.	Перейди API Development tools → Create new application.
	
	4.	Заполни name/short name — можно любые.
	
	5.	После создания скопируй API_ID (целое число) и API_HASH (строка).

2) Создаём бота-агента в BotFather и получаем BOT_TOKEN (Aiogram)
	
	1.	В Telegram открой @BotFather.
	
	2.	/start → /newbot.
	
	3.	Придумай имя (например ZABORNY) и username (оканчивается на bot, например zabornybot).
	
	4.	Получишь токен вида 123456789:ABC-... — это BOT_TOKEN.
	
	5.	Сохрани токен в config.json.

3)  Как получить ID канала / юзера — через @ChatIdInfoBot

	1.	В Telegram открой бота: https://t.me/ChatIdInfoBot
	
	2.	Три способа:
	
	•	Переслать сообщение из канала в @ChatIdInfoBot — бот вернёт Chat ID: -100....
	
	•	Отправить @username (в некоторых случаях бот ответит с ID).
	
	•	Отправить самопись (в личном чате) — получишь User ID.
	
	3.	Скопируй ID канала (вид -100xxxxxxxxxx) и вставь в config.json в соответствующее поле.


Чеклист перед запуском:

До запуска у тебя уже должен быть настроен бот-агент, которым ты всем управляешь, а еще должно быть три канала: 

первый буферный приватный с кнопкой только для тебя и твоих админов (ZABORISTOE)

второй публичный который постит все что видит автоматом (DOPAMINE)

третий - твой канал-цель (IPNTZ), куда отправляются посты из первого по нажатию кнопки Класс! в первом приватном канале 

во всех троих должен быть админом твой бот-агент с правами постить сообщения (остальные не нужны)

также нужно получить API hash и API ID

в файле admins.txt должны быть валидные userid админов, хотя бы один (получи свой у @ChatIdInfoBot)

db.json может быть пустым или отсутствовать 

config.json ДОЛЖЕН БЫТЬ ЗАПОЛНЕН ПРАВИЛЬНО, ВАЛИДНЫМИ ДАННЫМИ

Примечания: 

1) В коде есть рудименты проверки картинки на баянность - пока нереализовано, задел на будущее

2) Понадобится ОЧЕНЬ толстый канал

3) Репосты с указанием авторов подразумевают, что эккаунт, от имени которого действует юзербот, должен быть подписан на этих авторов, а у этого бота задача собирать апдейты, не подписываясь на каналы

4) Бот пока не умеет в галереи, их он разбирает на отдельные картинки

5) Удаление caption при репостах - не баг, а фича (все текстовые подписи либо тупые до ужаса, либо раздражающе маркетинговые, либо относятся к чему-то происходящему на канале, неинтересное никому, кроме их админов) 

Как использовать бота-агента: 

Админские команды и добавление каналов

/list — выводит список текущих каналов из DB["monitored"].
	
/remove @channel_or_id — удаляет канал из DB.
	
/stats — небольшая статистика: количество каналов и размер SEEN.
	
Если админ шлёт в личке список строк (по одному на строку) с @channel или -100..., handle_text их парсит и вызывает add_monitored, который добавляет ключ в db.json с начальным last_id: 0. Поддерживает массивы имен, если каждое начинается с собачки.
