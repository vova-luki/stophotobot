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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

DB_POOL = None
ADMIN_ID = 124303561  # Твій ID розробника

# ==========================================
# РОБОТА З БАЗОЮ ДАНИХ (asyncpg)
# ==========================================

async def init_db():
    global DB_POOL
    try:
        DB_POOL = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        async with DB_POOL.acquire() as conn:
            # Таблиця користувачів (статус pro)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    is_pro BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Таблиця активних ігор у чатах
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS games (
                    chat_id BIGINT PRIMARY KEY,
                    current_round INT DEFAULT 0,
                    scores JSONB DEFAULT '{}'::jsonb,
                    game_type TEXT DEFAULT 'free', -- 'free' або 'pro'
                    is_active BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Таблиця глобальної статистики (за весь час)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS global_stats (
                    key TEXT PRIMARY KEY,
                    value INT DEFAULT 0
                )
            ''')
            # Ініціалізація лічильників статистики, якщо їх немає
            await conn.execute("INSERT INTO global_stats (key, value) VALUES ('total_chats', 0) ON CONFLICT DO NOTHING")
            await conn.execute("INSERT INTO global_stats (key, value) VALUES ('total_free_games', 0) ON CONFLICT DO NOTHING")
            await conn.execute("INSERT INTO global_stats (key, value) VALUES ('total_pro_games', 0) ON CONFLICT DO NOTHING")
            
        logger.info("Базу даних та таблиці успішно ініціалізовано.")
    except Exception as e:
        logger.error(f"Помилка ініціалізації бази даних: {e}")
        raise e

async def get_user_pro_status(user_id: BIGINT) -> bool:
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT is_pro FROM users WHERE user_id = $1", user_id)
        return row['is_pro'] if row else False

async def set_user_pro_status(user_id: BIGINT, username: str, is_pro: bool):
    async with DB_POOL.acquire() as conn:
        await conn.execute('''
            INSERT INTO users (user_id, username, is_pro, updated_at)
            VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE 
            SET username = $2, is_pro = $3, updated_at = CURRENT_TIMESTAMP
        ''', user_id, username, is_pro)

async def increment_stat(key: str):
    async with DB_POOL.acquire() as conn:
        await conn.execute("UPDATE global_stats SET value = value + 1 WHERE key = $1", key)

async def get_all_stats():
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM global_stats")
        return {row['key']: row['value'] for row in rows}

# ==========================================
# ХЕНДЛЕРИ ТА ЛОГІКА AIOGRAM
# ==========================================

# Команда /stat для розробника
@dp.message(Command("stat"), F.chat.type == "private")
async def cmd_stat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    stats = await get_all_stats()
    text = (
        "ЗА ВЕСЬ ЧАС:\n"
        f"- всі чати: {stats.get('total_chats', 0)}\n"
        f"- всі ігри до 10: {stats.get('total_free_games', 0)}\n"
        f"- всі ігри до 100: {stats.get('total_pro_games', 0)}"
    )
    await message.answer(text)

# Хендлер додавання бота в нову групу
@dp.my_chat_member(ChatMemberUpdatedFilter(member_change=JOIN_TRANSITION))
async def bot_added_to_group(event: types.ChatMemberUpdated):
    if event.chat.type in ["group", "supergroup"]:
        await increment_stat("total_chats")
        logger.info(f"Бота додано в нову групу: {event.chat.title} ({event.chat.id})")

# Команда /start або /play у групах та приватних чатах
@dp.message(Command("start", "play"))
async def cmd_start_or_play(message: types.Message):
    chat_id = message.chat.id
    
    if message.chat.type == "private":
        # Перевірка реферального посилання на оплату (якщо прийшов від платіжки)
        args = message.text.split()
        if len(args) > 1 and args[1] == "success_payment":
            await set_user_pro_status(message.from_user.id, message.from_user.username, True)
            text = (
                "Дякую, оплата є!\n\n"
                f"– {message.from_user.first_name} тепер pro\n"
                "– відкрито 100 раундів\n"
                "– відкрито 10 гравців"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game_active")]
            ])
            await message.answer(text, reply_markup=kb)
            return

        text = "Щоб грати, додай мене у групу з іншими людьми (не в особисті чати, а саме у групу). Знайдеш мене по пошуку @stofotobot"
        await message.answer(text)
        
    else:
        # Логіка для груп
        members_count = await message.chat.get_member_count()
        # Кількість людей (включаючи бота). Якщо всього 2 (1 людина + 1 бот)
        if members_count <= 2:
            text = (
                "Щоб грати, додайте в групу другого гравця.\n\n"
                "Щоб перезапустити бота, напишіть в чат команду /start або /play."
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="start_free_game")]
            ])
            await message.answer(text, reply_markup=kb)
        else:
            # Більше 2 людей у групі — пропонуємо вибір або показуємо Pro-умови
            text = (
                "Щоб грати втрьох і більше, хоч 1 гравець має бути pro.\n\n"
                "Pro-версія гри:\n"
                "- до 10 гравців\n"
                "- до 100 раундів назавжди\n"
                "- у всіх чатах pro-гравця"
            )
            pay_url = f"https://t.me/stofotobot?start=pay"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="КУПИТИ PRO-ВЕРСІЮ", url=pay_url)],
                [InlineKeyboardButton(text="НАС ВЖЕ ДВОЄ", callback_data="start_free_game")]
            ])
            await message.answer(text, reply_markup=kb)

# Старт безкоштовної гри (до 10 раундів)
@dp.callback_query(F.data == "start_free_game")
async def start_free_game_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    
    async with DB_POOL.acquire() as conn:
        await conn.execute('''
            INSERT INTO games (chat_id, current_round, scores, game_type, is_active, updated_at)
            VALUES ($1, 1, '{}'::jsonb, 'free', TRUE, CURRENT_TIMESTAMP)
            ON CONFLICT (chat_id) DO UPDATE
            SET current_round = 1, scores = '{}'::jsonb, game_type = 'free', is_active = TRUE, updated_at = CURRENT_TIMESTAMP
        ''')
    
    await increment_stat("total_free_games")
    
    text = (
        "Раунд 1\n\n"
        "Рахунок\n"
        "player 1: 0\n"
        "player 2: 0\n"
        "…\n"
        "player N: 0\n\n"
        "Завдання: cфотографуй число 1"
    )
    # У безкоштовній версії немає кнопки "Додати гравців"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_free_game")]
    ])
    
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

# Старт Pro-гри (до 100 раундів)
@dp.callback_query(F.data == "start_pro_game_active")
async def start_pro_game_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    
    async with DB_POOL.acquire() as conn:
        await conn.execute('''
            INSERT INTO games (chat_id, current_round, scores, game_type, is_active, updated_at)
            VALUES ($1, 1, '{}'::jsonb, 'pro', TRUE, CURRENT_TIMESTAMP)
            ON CONFLICT (chat_id) DO UPDATE
            SET current_round = 1, scores = '{}'::jsonb, game_type = 'pro', is_active = TRUE, updated_at = CURRENT_TIMESTAMP
        ''')
        
    await increment_stat("total_pro_games")
    
    text = (
        "Раунд 1\n\n"
        "Рахунок\n"
        "player 1: 0\n"
        "player 2: 0\n"
        "…\n"
        "player N: 0\n\n"
        "Завдання: cфотографуй число 1"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game_active")]
    ])
    
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

# Загальний хендлер для динамічного оновлення раундів та обнулення
@dp.callback_query(F.data.startswith("clear_round_"))
async def clear_round_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    target_round = int(callback.data.split("_")[-1])
    
    async with DB_POOL.acquire() as conn:
        game = await conn.fetchrow("SELECT game_type, scores FROM games WHERE chat_id = $1", chat_id)
        if not game:
            await callback.answer("Гру не знайдено", show_alert=True)
            return
            
        # Скидаємо поточний раунд до цільового
        await conn.execute('''
            UPDATE games 
            SET current_round = $2, updated_at = CURRENT_TIMESTAMP 
            WHERE chat_id = $1
        ''', chat_id, target_round)
        
        round_num = target_round
        game_type = game['game_type']
        
        # Генеруємо текст раунду (без крапок наприкінці речень раундів та завдань)
        text = (
            f"Раунд {round_num}\n\n"
            "Рахунок\n"
            "@user1: ...\n"
            "@user2: ...\n"
            "…\n"
            "@userN: ...\n\n"
            f"Завдання: число {round_num}"
        )
        
        # Створення кнопок. Виправлено синтаксичну помилку з дужками:
        buttons = []
        if round_num > 1:
            buttons.append([InlineKeyboardButton(text=f"ОБНУЛИТИ РАУНД {round_num - 1}", callback_data=f"clear_round_{round_num - 1}")])
        
        next_game_cb = "start_pro_game_active" if game_type == "pro" else "start_free_game"
        buttons.append([InlineKeyboardButton(text="НОВА ГРА", callback_data=next_game_cb)])
        
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer(f"Раунд {round_num} обнулено")

# Хендлер обробки фотографій від користувачів
@dp.message(F.photo)
async def handle_game_photo(message: types.Message):
    chat_id = message.chat.id
    if message.chat.type == "private":
        return
        
    async with DB_POOL.acquire() as conn:
        game = await conn.fetchrow("SELECT current_round, game_type, is_active FROM games WHERE chat_id = $1", chat_id)
        
        if not game or not game['is_active']:
            return
            
        current_round = game['current_round']
        game_type = game['game_type']
        max_rounds = 100 if game_type == "pro" else 10
        
        # Логіка перевірки та збільшення раунду
        next_round = current_round + 1
        
        if next_round > max_rounds:
            # Кінець гри
            await conn.execute("UPDATE games SET is_active = FALSE, current_round = 0 WHERE chat_id = $1", chat_id)
            text = (
                f"Переможець: @{message.from_user.username or message.from_user.first_name}\n\n"
                "Рахунок\n"
                "@user1: ...\n"
                "@user2: ...\n"
                "…\n"
                "@userN: ...\n\n"
                "Не забудь про свій приз!"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"ОБНУЛИТИ РАУНД {max_rounds}", callback_data=f"clear_round_{max_rounds}")],
                [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game_active" if game_type == "pro" else "start_free_game")]
            ])
            await message.answer(text, reply_markup=kb)
        else:
            # Перехід на наступний раунд
            await conn.execute("UPDATE games SET current_round = $2, updated_at = CURRENT_TIMESTAMP WHERE chat_id = $1", chat_id, next_round)
            
            text = (
                f"Раунд {next_round}\n\n"
                "Рахунок\n"
                "@user1: ...\n"
                "@user2: ...\n"
                "…\n"
                "@userN: ...\n\n"
                f"Завдання: число {next_round}"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"ОБНУЛИТИ РАУНД {next_round - 1}", callback_data=f"clear_round_{next_round - 1}")],
                [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game_active" if game_type == "pro" else "start_free_game")]
            ])
            await message.answer(text, reply_markup=kb)

# ==========================================
# ВЕБХУКИ ТА FASTAPI ЛОГІКА
# ==========================================

app = FastAPI()

@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    try:
        update_json = await request.json()
        update = types.Update.model_validate(update_json, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.error(f"Помилка обробки оновлення: {e}")
    return Response(status_code=200)

@app.get("/")
async def root():
    return {"status": "working", "bot": "100_photo_bot"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    logger.info("Вебхук успішно встановлено!")
    
    yield
    
    logger.info("Закриття додатка, очищення ресурсів...")
    await dp.storage.close()
    if bot.session:
        await bot.session.close()
    if DB_POOL:
        await DB_POOL.close()
        logger.info("Пул підключень до БД закрито.")

app.router.lifespan_context = lifespan
