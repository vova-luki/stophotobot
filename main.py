import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
import asyncpg

# Ініціалізація логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Зчитування змінних оточення
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not BASE_URL or not DATABASE_URL:
    raise ValueError("Критична помилка: Відсутні обов'язкові змінні оточення BOT_TOKEN, BASE_URL або DATABASE_URL!")

# Налаштування ендпоінту для Вебхука
WEBHOOK_PATH = f"/webhook"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

# Ініціалізація бота та диспетчера
bot = Bot(
    token=BOT_TOKEN, 
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

DB_POOL = None

async def init_db():
    global DB_POOL
    if DB_POOL is None:
        DB_POOL = await asyncpg.create_pool(DATABASE_URL)
        logger.info("Пул підключень до БД успішно створено.")
        
        async with DB_POOL.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS games (
                    chat_id BIGINT PRIMARY KEY,
                    status TEXT,
                    round_number INT,
                    players JSONB,
                    current_word_data JSONB
                );
                CREATE TABLE IF NOT EXISTS pro_users (
                    user_id BIGINT PRIMARY KEY,
                    is_pro BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            logger.info("Таблиці в БД перевірено/створено.")

async def init_or_get_game(chat_id):
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT status, round_number, players, current_word_data FROM games WHERE chat_id = $1", chat_id)
        if row:
            return {
                "status": row["status"],
                "round_number": row["round_number"],
                "players": json.loads(row["players"]),
                "current_word_data": json.loads(row["current_word_data"]) if row["current_word_data"] else None
            }
        else:
            default_players = {}
            await conn.execute(
                "INSERT INTO games (chat_id, status, round_number, players, current_word_data) VALUES ($1, $2, $3, $4, $5)",
                chat_id, "registration", 0, json.dumps(default_players), None
            )
            return {"status": "registration", "round_number": 0, "players": default_players, "current_word_data": None}

async def save_game(chat_id, status, round_number, players, current_word_data):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "UPDATE games SET status = $1, round_number = $2, players = $3, current_word_data = $4 WHERE chat_id = $5",
            status, round_number, json.dumps(players), json.dumps(current_word_data) if current_word_data else None, chat_id
        )

async def is_user_pro(user_id):
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT is_pro FROM pro_users WHERE user_id = $1", user_id)
        return row["is_pro"] if row else False

async def set_user_pro_status(user_id, status: bool):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO pro_users (user_id, is_pro, updated_at) VALUES ($1, $2, CURRENT_TIMESTAMP) "
            "ON CONFLICT (user_id) DO UPDATE SET is_pro = $2, updated_at = CURRENT_TIMESTAMP",
            user_id, status
        )

async def update_player_name_background(chat_id, user: types.User):
    if not user or user.is_bot:
        return
    game = await init_or_get_game(chat_id)
    players = game["players"]
    uid = str(user.id)
    
    current_name = f"@{user.username}" if user.username else user.first_name
    
    if uid in players:
        if players[uid].get("name") != current_name:
            players[uid]["name"] = current_name
            await save_game(chat_id, game["status"], game["round_number"], players, game["current_word_data"])
    else:
        # Якщо гравця немає, додаємо його з 0 балів (актуально для нових текстових повідомлень)
        players[uid] = {"name": current_name, "score": 0}
        await save_game(chat_id, game["status"], game["round_number"], players, game["current_word_data"])

async def send_welcome_rules(chat_id):
    text = (
        "<b>ПРАВИЛА ГРИ «СТОП-ХОТО» 🛑</b>\n\n"
        "1️⃣ Кожного раунду бот пише категорію (наприклад: <i>«Бренд авто»</i>) та випадкову літеру.\n"
        "2️⃣ Гравці повинні якнайшвидше написати відповідне слово у чат.\n"
        "3️⃣ Той, хто відповість першим — отримує бал, а раунд одразу завершується!\n\n"
        "Гравці, просто напишіть будь-яке повідомлення в цей чат, щоб бот вас зареєстрував, а потім натисніть кнопку нижче 👇"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ПОГНАЛИ 🏁", callback_data="start_game_rounds")]
    ])
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)

# --- АЛЬТЕРНАТИВНИЙ ХЕНДЛЕР ДЛЯ ОСОБИСТИХ ПОВІДОМЛЕНЬ ---
async def private_chat_handler(message: types.Message):
    is_pro = await is_user_pro(message.from_user.id)
    status_text = "<b>PRO ✨ (Безліміт)</b>" if is_pro else "<b>Безкоштовна версія (Обмежена)</b>"
    
    text = (
        f"Привіт, {message.from_user.first_name}! 🤖\n\n"
        f"Твій поточний статус: {status_text}\n\n"
        "Цей бот створений для гри в групах. Додай мене в будь-яку групу, зроби адміном (або вимкни Privacy Mode) і напиши команду /start або /play, щоб розпочати розваги! 🎉\n\n"
        "Бажаєш отримати PRO-версію? Натисни кнопку нижче."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отримати PRO 🚀", callback_data="buy_pro_version")]
    ])
    await message.answer(text=text, reply_markup=kb)


# =====================================================================
#                      ОБРОБНИКИ ПОВІДОМЛЕНЬ (АІОGRAM)
# =====================================================================

# 1. КОМАНДА /start АБО /play (УНІВЕРСАЛЬНА, НАЙВИЩИЙ ПРІОРИТЕТ)
@dp.message(Command("start", "play"))
async def reset_game_command(message: types.Message):
    chat_id = message.chat.id
    
    # Перевіряємо, чи це приватний чат
    if message.chat.type == "private":
        await private_chat_handler(message)
        return

    # Логіка для груп та супергруп
    await update_player_name_background(chat_id, message.from_user)
    
    game = await init_or_get_game(chat_id)
    players = game["players"]
    
    # Скидаємо бали всім наявним гравцям, якщо вони є
    if players:
        for uid in players:
            players[uid]["score"] = 0
        
    await save_game(chat_id, "registration", 0, players, None)
    await send_welcome_rules(chat_id)

# 2. МОНІТОРИНГ ДОДАВАННЯ БОТА В ГРУПУ
@dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def bot_added_to_group(event: types.ChatMemberUpdated):
    chat_id = event.chat.id
    if event.chat.type in ["group", "supergroup"]:
        # Очищуємо або ініціалізуємо гру під час входу в нову групу
        await save_game(chat_id, "registration", 0, {}, None)
        await send_welcome_rules(chat_id)

# 3. ФОНОВИЙ ХЕНДЛЕР ТЕКСТУ (ДЛЯ РЕЄСТРАЦІЇ ГРАВЦІВ)
# Стоїть НИЖЧЕ команд, тому не перехоплює і не "душить" їх
@dp.message(F.chat.type.in_({"group", "supergroup"}), F.text)
async def background_text_catcher(message: types.Message):
    # Якщо це якась інша команда (починається з /), ігноруємо, щоб дати спрацювати іншим хендлерам
    if message.text.startswith("/"):
        return
        
    # Просто оновлюємо або додаємо юзера в базу даних гри
    await update_player_name_background(message.chat.id, message.from_user)


# =====================================================================
#                         CALLBACK QUERY HANDLERS
# =====================================================================

@dp.callback_query(F.data == "buy_pro_version")
async def process_buy_pro(callback: types.CallbackQuery):
    pay_url = f"https://send.monobank.ua/jar/YOUR_JAR_ID?a=100&c={callback.from_user.id}"
    text = (
        "<b>💎 ПЕРЕВАГИ PRO ВЕРСІЇ:</b>\n"
        "• До 100 раундів в одній грі (замість 5)\n"
        "• Підтримка до 10 гравців у чаті\n"
        "• Пріоритетна генерація нових слів\n\n"
        "💰 Вартість: <b>100 грн</b>\n\n"
        f"Для оплати перейди за посиланням на Monobank: {pay_url}\n"
        "⚠️ <b>ВАЖЛИВО:</b> Не змінюй коментар до платежу! Там вказано твій ID, за яким бот автоматично видасть PRO-статус."
    )
    await callback.message.edit_text(text=text, disable_web_page_preview=True)
    await callback.answer()

@dp.callback_query(F.data == "start_game_rounds")
async def process_start_game_rounds(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    game = await init_or_get_game(chat_id)
    
    if not game["players"]:
        await callback.answer("❌ Жоден гравець ще не зареєструвався! Напишіть щось у чат.", show_alert=True)
        return
        
    await callback.message.answer("🎉 Гру розпочато! Готуйте пальчики, перший раунд зараз почнеться...")
    await callback.answer()


# =====================================================================
#                         WEBHOOK & FASTAPI
# =====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        await bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Вебхук підключено до: {WEBHOOK_URL}")
    else:
        logger.info(f"Вебхук вже налаштовано на: {WEBHOOK_URL}")
    yield
    if DB_POOL:
        await DB_POOL.close()
        logger.info("Пул підключень до БД закрито.")

app = FastAPI(lifespan=lifespan)

@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    try:
        update_dict = await request.json()
        tg_update = types.Update(**update_dict)
        await dp.feed_update(bot, tg_update)
    except Exception as e:
        logger.error(f"Помилка при обробці вебхука: {e}")
    return Response(status_code=200)

@app.post("/monobank-webhook")
async def monobank_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return Response(status_code=400)
        
    if data.get("type") == "StatementItem":
        statement = data.get("data", {}).get("statementItem", {})
        comment = statement.get("comment", "")
        
        user_id = None
        words = comment.split()
        for word in words:
            if word.isdigit() and len(word) >= 7:
                user_id = int(word)
                break
                
        if user_id:
            await set_user_pro_status(user_id, True)
            try:
                user_row = await bot.get_chat(user_id)
                u_name = f"@{user_row.username}" if user_row.username else user_row.first_name
                
                text = (
                    f"ОПЛАТА УСПІШНА!\n\n"
                    f"Дякую, оплата є!\n"
                    f"– {u_name} тепер Pro\n"
                    f"– відкрито 100 раундів\n"
                    f"– відкрито 10 гравців"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_new_game")]
                ])
                await bot.send_message(chat_id=user_id, text=text, reply_markup=kb)
            except TelegramAPIError:
                pass
                
    return Response(status_code=200)

@app.get("/")
async def root_route():
    return {"status": "working", "bot": "stophotobot"}
