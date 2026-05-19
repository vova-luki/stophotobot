import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Змінні оточення
TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or "8115804787:AAHuPshoaLVtAbblD9pi09qb5CCCXgC7xI4"
BASE_URL = os.getenv("BASE_URL") or "https://stophotobot-1.onrender.com"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

# Дані адміністратора
ADMIN_ID = 124303561

# Ініціалізація бота та додатку python-telegram-bot
bot = Bot(token=TOKEN)
telegram_app = Application.builder().token(TOKEN).build()

# Текстова заглушка для звичайних користувачів у приваті
PRIVATE_WELCOME_TEXT = "Привіт! Додай мене в груповий чат, щоб почати гру 100 PHOTO! Цей бот створений для роботи у групах."

# --- ОБРОБНИКИ КОМАНД ТА ПОДІЙ ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка команди /start або /play"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Якщо це приватний чат з ботом
    if chat.type == "private":
        # Звичайному юзеру показуємо заглушку, адміну просто не даємо ігрових кнопок
        if user.id != ADMIN_ID:
            await update.message.reply_text(PRIVATE_WELCOME_TEXT)
        else:
            await update.message.reply_text("Привіт, Адмін! Для перегляду метрик використовуй команду /stat")
        return

    # ЛОГІКА ДЛЯ ГРУПОВИХ ЧАТІВ
    # (Тут надсилається пост ПРАВИЛА, картинка 1 тощо. Поки що базова заглушка для тесту чатів)
    keyboard = [
        [InlineKeyboardButton("НОВА ГРА ДО 10", callback_data="new_game_10")],
        [InlineKeyboardButton("КУПИТИ PRO-ВЕРСІЮ", callback_data="buy_pro")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=chat.id, 
        text=f"Бот успішно запущений у групі: {chat.title}\n\n[Пост ПРАВИЛА]", 
        reply_markup=reply_markup
    )

async def stat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stat виключно для адміна і тільки в приватних повідомленнях"""
    chat = update.effective_chat
    user = update.effective_user

    # Сувора перевірка: тільки приватний чат і тільки твій ID
    if chat.type == "private" and user.id == ADMIN_ID:
        # Шаблон статистики (в майбутньому додамо запити до Supabase)
        stat_text = (
            "📊 **СТАТИСТИКА ПРОЄКТУ**\n\n"
            "ЗА ВЕСЬ ЧАС:\n"
            "- всі чати: 0\n"
            "- всі юзери: 0\n"
            "- free-юзери: 0\n"
            "- pro-юзери: 0\n\n"
            "ПРИРІСТ ЗА 24 ГОД:\n"
            "- всі чати: +0\n"
            "- всі юзери: +0"
        )
        # Надсилаємо суворо в поточний приватний чат адміна
        await context.bot.send_message(chat_id=chat.id, text=stat_text, parse_mode="Markdown")
    else:
        # Для всіх інших або якщо викликано в групі — повний ігнор
        return

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка натискань на кнопки"""
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user

    # Безпека: повністю ігноруємо будь-які ігрові кнопки, якщо вони натиснуті у приваті
    if chat.type == "private":
        await query.answer(text="Гра доступна тільки в групах!", show_alert=True)
        return

    await query.answer()
    
    # Логіка кнопок працює строго в межах того chat.id, де кнопку натиснули
    if query.data == "new_game_10":
        await context.bot.send_message(chat_id=chat.id, text=f"Гра активована користувачем @{user.username or user.first_name}. Завдання 1! Чекаю фото.")
    elif query.data == "buy_pro":
        await context.bot.send_message(chat_id=chat.id, text="Посилання на оплату (99 грн): https://send.monobank.ua/...")

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка надісланих фотографій у групах"""
    chat = update.effective_chat
    
    # Ігноруємо фото у приватних повідомленнях
    if chat.type == "private":
        return

    # Логіка гри: фіксуємо фото в конкретному чаті
    await update.message.reply_text(f"Фото зафіксовано в чаті {chat.id}! +1 бал гравцю.")

# Реєстрація обробників у додатку Telegram
telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(CommandHandler("play", start_command))
telegram_app.add_handler(CommandHandler("stat", stat_command))
telegram_app.add_handler(CallbackQueryHandler(button_click))
telegram_app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

# --- ІНІЦІАЛІЗАЦІЯ FASTAPI ТА LIFESPAN ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Керування стартом та зупинкою додатка згідно з правилами Render"""
    logger.info("Ініціалізація Telegram бота...")
    await telegram_app.initialize()
    await
