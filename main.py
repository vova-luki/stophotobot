import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from supabase import create_client

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфігурація з Render
TOKEN = os.getenv("TELEGRAM_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DB_URL = os.getenv("DATABASE_URL")
# Примітка: Supabase key беремо з оточення
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Ініціалізація Supabase (використовуємо стандартні методи)
supabase = create_client(os.getenv("SUPABASE_URL"), SUPABASE_KEY)

# Створення об'єкта бота для вебхука
bot = Bot(token=TOKEN)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Реєстрація вебхука при старті
    webhook_url = f"{BASE_URL}/webhook"
    await bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url}")
    yield
    # Видалення вебхука при зупинці
    await bot.delete_webhook()
    logger.info("Webhook deleted")

app = FastAPI(lifespan=lifespan)

from telegram import Update

@app.post("/webhook")
async def webhook(request: Request):
    """Ендпоінт для отримання оновлень від Telegram"""
    update = Update.de_json(await request.json(), bot)
    # Передаємо оновлення в Application, яку ми налаштуємо далі
    await application.process_update(update)
    return {"status": "ok"}

from telegram.ext import ApplicationBuilder

# Створення об'єкта application
application = ApplicationBuilder().token(TOKEN).build()

async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник команд /start та /play"""
    # Тут буде логіка перевірки чату та ініціації стану гри
    await update.message.reply_text("Гра 100 PHOTO запущена!")

# Реєстрація хендлерів
application.add_handler(CommandHandler(["start", "play"], start_game))

async def check_chat_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Мідлвар для фільтрації контексту"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Ігноруємо стікери та текстові повідомлення, якщо гра не в стані введення
    if update.message and (update.message.sticker or (update.message.text and not update.message.text.startswith('/'))):
        return
        
    # Блокування ігор в приваті
    if chat.type == 'private' and user.id != 124303561:
        await update.message.reply_text("Бот призначений тільки для гри в групах. Додайте мене в чат!")
        return

# Додаємо фільтр до нашого application
application.add_handler(MessageHandler(filters.ALL, check_chat_type), group=-1)

async def check_and_send_post(chat_id: int, bot: Bot):
    """Перевірка кількості гравців і відправка відповідного посту"""
    count = await bot.get_chat_member_count(chat_id)
    # Реальний лічильник гравців (без бота) = count - 1
    players_count = count - 1
    
    # Логіка визначення статусу PRO (тут має бути запит до БД)
    is_pro_chat = False 
    
    if is_pro_chat:
        if players_count == 1:
            await bot.send_message(chat_id, "ПОСТ '1 ЛЮДИНА В ГРУПІ'")
        elif 2 <= players_count <= 10:
            await bot.send_message(chat_id, "ПОСТ 'ПРАВИЛА'")
        else:
            await bot.send_message(chat_id, "ПОСТ '11 ЛЮДЕЙ В ГРУПІ'")
    else:
        if players_count == 1:
            await bot.send_message(chat_id, "ПОСТ '1 ЛЮДИНА В ГРУПІ'")
        elif players_count == 2:
            await bot.send_message(chat_id, "ПОСТ 'ПРАВИЛА'")
        else:
            await bot.send_message(chat_id, "ПОСТ '3 ЛЮДИНИ В ГРУПІ'")

# Приклад виклику в команді /start
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await check_and_send_post(update.effective_chat.id, context.bot)

async def is_user_pro(user_id: int) -> bool:
    """Перевірка статусу Pro в Supabase"""
    try:
        response = supabase.table("users").select("is_pro").eq("user_id", user_id).single().execute()
        return response.data.get("is_pro", False) if response.data else False
    except Exception as e:
        logger.error(f"Error checking Pro status: {e}")
        return False

# Тепер оновимо частину перевірки в check_and_send_post
async def check_and_send_post(chat_id: int, bot: Bot, user_id: int):
    count = await bot.get_chat_member_count(chat_id)
    players_count = count - 1
    
    # Виклик функції перевірки
    is_pro_chat = await is_user_pro(user_id) 
    
    # ... (далі ваша логіка з попереднього блоку з використанням is_pro_chat)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка отриманого фото"""
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    # Тут буде логіка:
    # 1. Запис фото в БД (як відповіді на раунд)
    # 2. Оновлення рахунку гравця в БД
    # 3. Перевірка, чи це останній раунд (10 або 100)
    # 4. Відправка повідомлення з наступним завданням
    
    await update.message.reply_text(f"Фото отримано! Раунд оновлено для {user.first_name}")

# Додаємо хендлер для фото
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

import asyncio

async def get_stats_data():
    """Асинхронний збір статистики з БД"""
    # Тут будуть ваші запити типу:
    # supabase.table("users").select("*", count='exact').execute()
    # Використовуйте asyncio.gather для паралельності
    return "Статистика за весь час:\n- всі чати: 0\n- всі юзери: 0..."

async def stat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник команди /stat"""
    if update.effective_user.id != 124303561:
        return
    
    stats = await get_stats_data()
    await update.message.reply_text(stats)

# Додаємо команду
application.add_handler(CommandHandler("stat", stat_command))

