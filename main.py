import os
import logging
import asyncio
from fastapi import FastAPI
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ініціалізація FastAPI
app = FastAPI()

# Токени та налаштування з Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

# Ініціалізація бота та диспетчера aiogram
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальна змінна для зберігання пулу з'єднань баз даних
db_pool = None

async def init_db():
    """ Ініціалізація підключення до БД Supabase без жодних замін префіксів """
    global db_pool
    if not DATABASE_URL:
        logger.error("DATABASE_URL не знайдено в змінних оточення!")
        return

    try:
        # Створюємо надійний пул підключень прямо по рядку з Render
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        logger.info("База даних успішно підключена та пул створено!")
    except Exception as e:
        logger.error(f"Не вдалося підключитися до бази даних: {e}")

@app.on_event("startup")
async def on_startup():
    """ Дії при старті сервера """
    await init_db()
    # Встановлюємо вебхук для телеграм-бота
    webhook_url = f"{BASE_URL}/webhook"
    await bot.set_webhook(webhook_url)
    logger.info(f"Вебхук встановлено на: {webhook_url}")

@app.on_event("shutdown")
async def on_shutdown():
    """ Дії при вимкненні сервера """
    global db_pool
    if db_pool:
        await db_pool.close()
        logger.info("Пул підключень до БД закрито.")
    await bot.session.close()

@dp.message()
async def echo_handler(message: types.Message):
    """ Простий ехо-хендлер для перевірки працездатності бота """
    try:
        await message.answer(f"Привіт! Я отримав твоє повідомлення: {message.text}")
    except Exception as e:
        logger.error(f"Помилка відправки повідомлення: {e}")

@app.post("/webhook")
async def webhook(update: dict):
    """ Обробник вебхуків від Telegram """
    telegram_update = Update(**update)
    await dp.feed_update(bot, telegram_update)
    return {"status": "ok"}

@app.get("/")
async def root():
    """ Головна сторінка для перевірки сервісу """
    return {"message": "StopHotobot працює!"}
