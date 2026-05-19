import os
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ApplicationBuilder
from supabase import create_client

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфігурація
TOKEN = os.getenv("TELEGRAM_TOKEN")
BASE_URL = os.getenv("BASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(os.getenv("SUPABASE_URL"), SUPABASE_KEY)
bot = Bot(token=TOKEN)

@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = f"{BASE_URL}/webhook"
    await bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url}")
    yield
    await bot.delete_webhook()
    logger.info("Webhook deleted")

app = FastAPI(lifespan=lifespan)

application = ApplicationBuilder().token(TOKEN).build()

async def is_user_pro(user_id: int) -> bool:
    try:
        response = supabase.table("users").select("is_pro").eq("user_id", user_id).single().execute()
        return response.data.get("is_pro", False) if response.data else False
    except Exception as e:
        logger.error(f"Error checking Pro status: {e}")
        return False

async def check_and_send_post(chat_id: int, bot: Bot, user_id: int):
    count = await bot.get_chat_member_count(chat_id)
    players_count = count - 1
    is_pro_chat = await is_user_pro(user_id) 
    
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

async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await check_and_send_post(update.effective_chat.id, context.bot, update.effective_user.id)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"Фото отримано! Раунд оновлено для {user.first_name}")

async def stat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != 124303561:
        return
    await update.message.reply_text("Статистика: в розробці...")

async def check_chat_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if update.message and (update.message.sticker or (update.message.text and not update.message.text.startswith('/'))):
        return
    if chat.type == 'private' and user.id != 124303561:
        await update.message.reply_text("Бот призначений тільки для гри в групах.")
        return

application.add_handler(CommandHandler(["start", "play"], start_game))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
application.add_handler(CommandHandler("stat", stat_command))
application.add_handler(MessageHandler(filters.ALL, check_chat_type), group=-1)

@app.post("/webhook")
async def webhook(request: Request):
    update = Update.de_json(await request.json(), bot)
    await application.process_update(update)
    return {"status": "ok"}
