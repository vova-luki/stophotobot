import os
import logging
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

# Константи (заміни ID фото на свої актуальні, якщо потрібно)
PHOTO_RULES = "AgACAgIAAxkBAAE..."  

# Імітація бази даних або стану гри (для демонстрації)
# У реальному проекті тут використовуються запити до твоєї БД (Supabase/PostgreSQL)
async def get_active_game(chat_id: int):
    # Твоя логіка перевірки: чи є активна гра для цього chat_id
    # Повертає об'єкт гри або Dict, якщо гра триває, інакше None
    return {"chat_id": chat_id, "current_round": 1}

# ==========================================
# ПОСТ "ПРАВИЛА" (З ВИПРАВЛЕНИМИ АБЗАЦАМИ)
# ==========================================
async def send_welcome_rules(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    rules_text = (
        "Вітаємо у грі <a href='https://t.me/stophotobot'>100 PHOTO</a>!\n\n"
        "Правила гри:\n\n"
        "1. Завдання гравців – фотографувати числа (1, 2, 3) і надсилати у цей чат.\n\n"
        "2. Безоплатна гра триває 10 раундів, платна – 100 раундів. 1 раунд = 1 фото. "
        "За кожне фото гравець отримує 1 бал.\n\n"
        "3. Числа не можна створювати (викладати предметами) або писати самому. "
        "Лише фотографувати їх вдома, на вулиці тощо.\n\n"
        "4. Не можна повторювати двічі числа з однієї локації (номери сторінок у книзі, кнопки в ліфті тощо). "
        "Локації мають бути різними.\n\n"
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
        await context.bot.send_photo(
            chat_id=chat_id, 
            photo=PHOTO_RULES, 
            caption=rules_text, 
            parse_mode="HTML", 
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Помилка відправки правил з фото: {e}")
        await context.bot.send_message(
            chat_id=chat_id, 
            text=rules_text, 
            parse_mode="HTML", 
            reply_markup=reply_markup
        )

# ==========================================
# ОБРОБКА КОМАНД
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await send_welcome_rules(context, chat_id)

async def stat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Статистика готується...")

# ==========================================
# ОБРОБКА КНОПОК (CALLBACK)
# ==========================================
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "new_game_10":
        # Логіка старту гри до 10 раундів
        await query.message.reply_text("<b>Завдання 1</b>\n\nЗнайди і сфотографуй число 1.", parse_mode="HTML")

# ==========================================
# КРИТИЧНИЙ ФІКС: ОБРОБНИК НАДІСЛАНИХ ФОТО
# ==========================================
async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    logger.info(f"Отримано фото від {username} ({user_id}) в чаті {chat_id}")

    # Перевіряємо статус активної гри
    game = await get_active_game(chat_id)
    if not game:
        await update.message.reply_text("Зараз немає активної гри. Натисніть /start або /play, щоб почати!")
        return

    try:
        # Тут йде твоя логіка перевірки фото / додавання балів у БД
        # Наприклад, отримання file_id найбільшого розміру:
        # file_id = update.message.photo[-1].file_id
        
        current_round = game.get("current_round", 1)
        next_round = current_round + 1

        # Формуємо відповідь та перемикаємо на наступне завдання
        response_text = (
            f"<b>Завдання {next_round}</b>\n\n"
            f"@{username} зараховано фото для раунду {current_round}!\n\n"
            f"Тепер знайди і сфотографуй число {next_round}."
        )
        
        await update.message.reply_text(response_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Помилка обробки фото: {e}")
        await update.message.reply_text("Сталася помилка при збереженні фото. Спробуйте ще раз.")

# ==========================================
# ОБРОБКА ЗВИЧАЙНОГО ТЕКСТУ
# ==========================================
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Бот просто ігнорує звичайний текст під час гри або дає підказку
    pass

# ==========================================
# ГОЛОВНА ФУНКЦІЯ ЗАПУСКУ БОТА
# ==========================================
def main():
    # Беремо токен з потрібної змінної, яку налаштували в Render (TELEGRAM_TOKEN)
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    
    if not TOKEN:
        logger.critical("КРИТИЧНА ПОМИЛКА: Змінну TELEGRAM_TOKEN не знайдено в оточенні Render!")
        return

    # Ініціалізація додатку telegram-bot
    application = Application.builder().token(TOKEN).build()

    # Порядок додавання хендлерів є важливим!
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("play", start_command))
    application.add_handler(CommandHandler("stat", stat_command))
    
    # Реєструємо кліки по кнопках
    application.add_handler(CallbackQueryHandler(button_click))

    # ВАЖЛИВО: Хендлер для фото має йти ПЕРЕД хендлером загального тексту
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    
    # Хендлер для тексту (ігнорує команди)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Запуск процесу опитування (Polling)
    logger.info("Бот успішно запускається у режимі Polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
