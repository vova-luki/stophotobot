import os
import logging
import asyncio
import asyncpg
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton

# Налаштування логів для Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

# Якщо в Render рядок підключення починається з postgresql://, міняємо на postgres:// для asyncpg
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgres://", 1)

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

# Тимчасові дані для ігрового процесу в оперативці (щоб гра йшла швидко)
GAMES_DATA = {} 
ADMIN_ID = 124303561 

# --- БЛОК РОБОТИ З БАЗОЮ ДАНИХ (SUPABASE) ---
async def init_db():
    """Створює таблиці в Supabase, якщо їх немає"""
    if not DATABASE_URL:
        logger.error("DATABASE_URL не знайдено в змінних оточення!")
        return
    
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Таблиця для унікальних чатів
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                chat_id BIGINT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''');
        # Таблиця для унікальних користувачів
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''');
        logger.info("База даних успішно ініціалізована, таблиці створені!")
    except Exception as e:
        logger.error(f"Помилка ініціалізації БД: {e}")
    finally:
        await conn.close()

async def log_chat_to_db(chat_id: int):
    """Записує чат в базу, якщо його там немає"""
    if not DATABASE_URL: return
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('INSERT INTO chats (chat_id) VALUES ($1) ON CONFLICT (chat_id) DO NOTHING', chat_id)
        await conn.close()
    except Exception as e:
        logger.error(f"Помилка запису чату в БД: {e}")

async def log_user_to_db(user_id: int):
    """Записує користувача в базу, якщо він новий"""
    if not DATABASE_URL: return
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING', user_id)
        await conn.close()
    except Exception as e:
        logger.error(f"Помилка запису користувача в БД: {e}")

async def get_db_stats():
    """Рахує унікальні дані з Supabase"""
    if not DATABASE_URL: return 0, 0
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        chats_count = await conn.fetchval('SELECT COUNT(*) FROM chats')
        users_count = await conn.fetchval('SELECT COUNT(*) FROM users')
        await conn.close()
        return chats_count, users_count
    except Exception as e:
        logger.error(f"Помилка отримання статистики з БД: {e}")
        return 0, 0

def get_user_name(user: types.User) -> str:
    if user.first_name:
        return user.first_name
    return f"@{user.username}" if user.username else f"ID: {user.id}"

# --- ТЕКСТ ПРАВИЛ ТА КНОПКИ ---
RULES_TEXT = (
    "Вітаємо у грі <a href='https://t.me/100photobot'>100 PHOTO</a>!\n\n"
    "Правила гри:\n\n"
    "1. Завдання гравців – фотографувати числа (1, 2, 3) i надсилати у цей чат.\n\n"
    "2. Безоплатна гра триває 10 раундів, платна – 100 раундів. 1 раунд = 1 photo. За кожне photo гравець отримує 1 бал.\n\n"
    "3. Числа не можна створювати (викладати предметами) або писати самому. Лише фотографувати їх вдома, на вулиці тощо.\n\n"
    "4. Не можна повторювати двічі числа з однієї локації (номери сторінок у книзі, номери паркомісць тощо). Локації мають бути різними.\n\n"
    "5. Якщо надіслане фото не відповідає правилам, це фото можна відмінити і почати раунд заново.\n\n"
    "Щоб перезапустити бота, напишіть в чат команду /start або /play.\n\n"
    "За бажанням, придумайте приз переможцю.\n\n"
    "Натхнення!"
)

def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="[ НОВА ГРА ДО 10 ]", callback_data="start_free")],
        [InlineKeyboardButton(text="[ НОВА ГРА ДО 100 ]", callback_data="trigger_pay")],
        [InlineKeyboardButton(text="[ ДОДАТИ ГРАВЦІВ ]", callback_data="trigger_pay")]
    ])

# --- ЛОГІКА ТЕЛЕГРАМ БОТА ---

# 1. КОМАНДА СТАТИСТИКИ ДЛЯ ВОВА (/stat або /admin_stats)
@dp.message(F.text.in_({"/stat", "/stats", "/admin_stats", "Стат"}))
async def show_admin_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    # Беремо вічні дані з Supabase
    db_chats, db_users = await get_db_stats()

    # Рахуємо активні ігри в оперативці прямо зараз
    active_chats = len(GAMES_DATA)
    active_users = sum(len(game["scores"]) for game in GAMES_DATA.values())

    stats_text = (
        "📊 **АКТУАЛЬНА СТАТИСТИКА БОТА (Supabase)**\n\n"
        "🗄️ **Збережено в базі даних назавжди:**\n"
        f"├ Всього підключено чатів: {db_chats}\n"
        f"└ Всього унікальних людей: {db_users}\n\n"
        "🚀 **Прямо зараз в реальному часі:**\n"
        f"├ Активних сесій (ігор): {active_chats}\n"
        f"└ Грає людей в цих чатах: {active_users}"
    )
    await message.answer(stats_text, parse_mode="Markdown")

# 2. АВТО-ЗАПУСК: КОЛИ БОТА ДОДАЮТЬ В ГРУПУ
@dp.message(F.new_chat_members)
async def on_bot_join(message: types.Message):
    bot_added = any(member.id == bot.id for member in message.new_chat_members)
    if bot_added:
        await log_chat_to_db(message.chat.id)
        await log_user_to_db(message.from_user.id)
        await message.answer(RULES_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard(), disable_web_page_preview=True)

# 3. ЗАПУСК ПО КОМАНДАХ В ЧАТІ
@dp.message(F.text.in_({"/start", "/game", "/play", "Старт", "game", "play", "Start"}))
async def on_command_start(message: types.Message):
    await log_chat_to_db(
