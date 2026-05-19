import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

PHOTO_RULES = "AgACAgIAAxkBAAE..."  

# Глобальна змінна для апликейшна бота
bot_app = None

# Імітація бази даних
async def get_active_game(chat_id: int):
    return {"chat_id": chat_id, "current_round": 1}

# Пост "ПРАВИЛА"
async def send_welcome_rules(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    rules_text = (
        "Вітаємо у грі <a href='https://t.me/stophotobot'>100 PHOTO</a>!\n\n"
        "Правила гри:\n\n"
        "1. Завдання гравців – фотографувати числа (1, 2, 3) і надсилати у цей чат.\n\n"
        "2. Безоплатна гра триває 10 раундів, платна – 100 раундів. 1 раунд = 1 фото. За кожне фото гравець отримує 1 бал.\n\n"
        "3. Числа не можна створювати (викладати предметами) або писати самому. Лише фотографувати їх вдома, на вулиці тощо.\n\n"
        "4. Не можна повторювати двічі числа з однієї локації (номери сторінок у книзі, кнопки в ліфті тощо). Локації мають бути різними.\n\n"
        "5. Якщо надіслане фото не відповідає правилам, це фото можна відмінити і почати раунд заново.\n\n"
        "Щоб перезапустити бота, напишіть в чат команду /start або /play.\n\n"
        "За бажанням, придумайте приз переможцю.\n\n"
        "Натхнення!"
    )
    
    keyboard = [
        [InlineKeyboardButton("[ НОВА ГРА ДО 10 ]", callback_data="new_game_10")],
        [InlineKeyboardButton("[ НОВА ГРА ДО 100 ]", callback_data="buy_pro")],
        [InlineKeyboardButton("[ ДОДАТИ ГРАВЦІВ ]", callback_data="add_players")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_photo(chat_id=chat_id, photo=PHOTO_RULES, caption=rules_text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Помилка відправки правил з фото: {e}")
        await context.bot.send_message(chat_id=chat_id, text=rules_text, parse_mode="HTML", reply_markup=reply_markup)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_welcome_rules(context, update.effective_chat.id)

async def stat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Статистика готується...")

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "new_game_10":
        await query.message.reply_text("<b>Завдання 1</b>\n\nЗнайди і сфотографуй число 1.", parse_mode="HTML")

async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name
    
    game = await get_active_game(chat_id)
    if not game:
        await update.message.reply_text("Зараз немає активної гри. Натисніть /start або /play, щоб почати!")
        return

    try:
        current_round = game.get("current_round", 1)
        next_round = current_round + 1
        response_text = f"<b>Завдання {next_round}</b>\n\n@{username} зараховано фото для раунду {current_round}!\n\nТепер знайди і сфотографуй число {next_round}."
        await update.message.reply_text(response_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Помилка обробки фото: {e}")
        await update.message.reply_text("Сталася помилка при збереженні фото. Спробуйте ще раз.")

# Керування життєвим циклом FastAPI (Lifespan)
@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    BASE_URL = os.environ.get("BASE_URL") # Наприклад: https://stophotobot-1.onrender.com
    
    if not TOKEN or not BASE_URL:
        logger.critical("КРИТИЧНА ПОМИЛКА: Відсутні TELEGRAM_TOKEN або BASE_URL!")
        yield
        return

    # Ініціалізація бота
    bot_app = Application.builder().token(TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("play", start_command))
    bot_app.add_handler(CommandHandler("stat", stat_command))
    bot_app.add_handler(CallbackQueryHandler(button_click))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    
    # Стартуємо внутрішні компоненти апликейшна
    await bot_app.initialize()
    await bot_app.start()
    
    # Встановлюємо вебхук в Telegram
    webhook_url = f"{BASE_URL}/webhook"
    logger.info(f"Встановлюємо вебхук на: {webhook_url}")
    await bot_app.bot.set_webhook(url=webhook_url)
    
    yield
    
    # Зупинка бота при вимкненні сервера
    logger.info("Зупиняємо вебхук та додаток...")
    await bot_app.stop()
    await bot_app.shutdown()

# Створюємо FastAPI додаток
app = FastAPI(lifespan=lifespan)

# Хелсчек для Render (щоб він бачив, що сервіс живий)
@app.get("/")
async def root():
    return {"status": "ok", "message": "StopPhotoBot є живим!"}

# Ендпоінт, куди Telegram надсилатиме апдейти
@app.post("/webhook")
async def telegram_webhook(request: Request):
    global bot_app
    if bot_app:
        json_data = await request.json()
        update = Update.de_json(json_data, bot_app.bot)
        await bot_app.process_update(update)
    return Response(status_code=200)
