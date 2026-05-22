import os
import logging
import asyncio
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ChatType
from aiogram.filters import Command, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncpg

# Ініціалізація логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Зчитування змінних оточення (Суворе правило: без хардкоду)
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not BASE_URL or not DATABASE_URL:
    raise ValueError("Критична помилка: Відсутні обов'язкові змінні оточення BOT_TOKEN, BASE_URL або DATABASE_URL у панелі Render.")

ADMIN_ID = 124303561
MONOBANK_PAY_URL = "https://send.monobank.ua/jar/example"  # Лінк на QR-еквайринг (легко налаштовується)

# Ініціалізація бота та диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Пул підключень до БД
db_pool: Optional[asyncpg.Pool] = None

# --- Робота з Базою Даних ---
async def init_db_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=10)

async def check_user_pro_status(user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        val = await conn.fetchval("SELECT is_pro FROM users WHERE user_id = $1", user_id)
        return bool(val)

async def check_chat_pro_status(chat_id: int) -> bool:
    async with db_pool.acquire() as conn:
        val = await conn.fetchval("SELECT is_pro FROM chats WHERE chat_id = $1", chat_id)
        if val:
            return True
        # Перевірка: чи є в групі хоча б один PRO користувач
        # Для спрощення, статус PRO фіксується за чатом під час перевірки учасників
        return False

async def set_chat_pro(chat_id: int, is_pro: bool):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO chats (chat_id, is_pro) VALUES ($1, $2) ON CONFLICT (chat_id) DO UPDATE SET is_pro = $2",
            chat_id, is_pro
        )

async def save_user(user_id: int, username: str):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET username = $2",
            user_id, username
        )

async def get_game_state(chat_id: int) -> Optional[dict]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT current_round, scores FROM games WHERE chat_id = $1", chat_id)
        if row:
            import json
            return {"current_round": row["current_round"], "scores": json.loads(row["scores"])}
        return None

async def save_game_state(chat_id: int, current_round: int, scores: dict):
    async with db_pool.acquire() as conn:
        import json
        await conn.execute(
            "INSERT INTO games (chat_id, current_round, scores, updated_at) VALUES ($1, $2, $3, NOW()) "
            "ON CONFLICT (chat_id) DO UPDATE SET current_round = $2, scores = $3, updated_at = NOW()",
            chat_id, current_round, json.dumps(scores)
        )

# --- Допоміжні функції логіки ---
async def get_clean_member_count(chat_id: int) -> int:
    try:
        count = await bot.get_chat_member_count(chat_id)
        return count - 1  # Віднімаємо бота
    except TelegramAPIError as e:
        logger.error(f"Помилка отримання кількості учасників у чаті {chat_id}: {e}")
        return 0

async def format_rules_post(is_pro: bool) -> tuple[str, types.InlineKeyboardMarkup]:
    text = (
        "Вітаємо у грі 100 PHOTO!\n"
        "Правила гри:\n\n"
        "1. Завдання гравців – фотографувати числа (1, 2, 3) і надсилати у цей чат.\n"
        f"2. Безоплатна гра триває 10 раундів, платна – 100 раундів. 1 раунд = 1 фото.\n"
        "За кожне photo гравець отримує 1 бал.\n\n"
        "3. Числа не можна створювати (викладати предметами) або писати самому.\n"
        "Лише фотографувати їх вдома, на вулиці тощо.\n\n"
        "4. Не можна брати двічі числа з однієї локації (номери сторінок у книзі, кнопки в ліфті тощо).\n"
        "Локації мають бути різними.\n\n"
        "5. Якщо надіслане фото не відповідає правилам, це фото можна відмінити і почати раунд заново.\n"
        "Щоб перезапустити бота, напишіть у чат команду /start або /play.\n\n"
        "За бажанням, придумайте приз переможцю.\n\n"
        "Натхнення!"
    )
    # Заміна назви на гіперпосилання
    text = text.replace("100 PHOTO", '<a href="https://t.me/stophotobot">100 PHOTO</a>')
    
    builder = InlineKeyboardBuilder()
    if not is_pro:
        builder.button(text="НОВА ГРА ДО 10", callback_data="start_game_10")
        builder.button(text="НОВА ГРА ДО 100 (PRO)", callback_data="go_pay")
        builder.button(text="ДОДАТИ ГРАВЦІВ (PRO)", callback_data="go_pay")
        builder.adjust(1)
    else:
        builder.button(text="НОВА ГРА", callback_data="start_game_100")
    return text, builder.as_markup()

async def process_welcome_rules(chat_id: int, trigger_user_id: Optional[int] = None):
    try:
        players_count = await get_clean_member_count(chat_id)
        is_pro_chat = await check_chat_pro_status(chat_id)
        
        if trigger_user_id and not is_pro_chat:
            if await check_user_pro_status(trigger_user_id):
                is_pro_chat = True
                await set_chat_pro(chat_id, True)

        if not is_pro_chat:
            if players_count == 1:
                builder = InlineKeyboardBuilder().button(text="НОВА ГРА ДО 10", callback_data="start_game_10")
                await bot.send_message(chat_id, "Щоб грати, додайте в групу другого гравця.\nЩоб перезапустити бота, напишіть в чат команду /start або /play.", reply_markup=builder.as_markup())
                return
            elif players_count == 2:
                text, markup = await format_rules_post(is_pro=False)
                await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
            elif players_count >= 3:
                builder = InlineKeyboardBuilder().button(text="КУПИТИ PRO-ВЕРСІЮ", url=MONOBANK_PAY_URL)
                await bot.send_message(chat_id, "Щоб грати втрьох і більше, хоча б 1 гравець має бути Pro.\nPro-версія гри:\n- до 10 гравців\n- до 100 раундів назавжди\n- у всіх чатах Pro-гравця", reply_markup=builder.as_markup())
        else:
            if players_count == 1:
                builder = InlineKeyboardBuilder().button(text="НОВА ГРА", callback_data="start_game_100")
                await bot.send_message(chat_id, "Щоб грати, додайте в групу другого гравця.\nЩоб перезапустити бота, напишіть в чат команду /start або /play.", reply_markup=builder.as_markup())
            elif 2 <= players_count <= 10:
                text, markup = await format_rules_post(is_pro=True)
                await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
            elif players_count >= 11:
                builder = InlineKeyboardBuilder().button(text="НАС ВЖЕ 10", callback_data="check_10_players")
                await bot.send_message(chat_id, "На жаль, грати може максимум 10 гравців.\nЩоб перезапустити бота, напишіть в чат команду /start або /play.", reply_markup=builder.as_markup())
    except TelegramAPIError as e:
        logger.error(f"Помилка при відправці повідомлення в чат {chat_id}: {e}")

# --- Обробники aiogram (Групові чати) ---

@dp.my_chat_member(ChatMemberUpdatedFilter(member_change=JOIN_TRANSITION))
async def on_bot_added_to_chat(event: types.ChatMemberUpdated):
    if event.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await process_welcome_rules(event.chat.id, trigger_user_id=event.from_user.id)

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), Command("start", "play"))
async def group_start_play(message: types.Message):
    await process_welcome_rules(message.chat.id, trigger_user_id=message.from_user.id)

@dp.callback_query(F.message.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.data == "check_10_players")
async def check_10_players_callback(callback: types.CallbackQuery):
    await process_welcome_rules(callback.message.chat.id, trigger_user_id=callback.from_user.id)
    await callback.answer()

@dp.callback_query(F.message.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.data == "go_pay")
async def go_pay_callback(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="КУПИТИ PRO-ВЕРСІЮ", url=MONOBANK_PAY_URL)
    builder.button(text="ПРОДОВЖИТИ ГРУ УДВОХ", callback_data="start_game_10")
    builder.adjust(1)
    
    text = (
        "Pro-версія гри:\n"
        "- до 10 гравців\n"
        "- до 100 раундів назавжди\n"
        "- у всіх чатах Pro-гравця"
    )
    await callback.message.answer(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.message.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.data.in_({"start_game_10", "start_game_100"}))
async def start_game_logic(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    is_pro = (callback.data == "start_game_100")
    
    await save_game_state(chat_id, 1, {})
    
    text = (
        "Рахунок\n"
        "player 1: 0\n"
        "player 2: 0\n"
        "…\n"
        "player N: 0\n\n"
        "Завдання: 1\n\n"
        "Знайди і сфотографуй число 1."
    ) if is_pro else (
        "Рахунок\n"
        "player 1: 0\n"
        "player 2: 0\n\n"
        "Завдання: 1\n\n"
        "Знайди і сфотографуй число 1."
    )
    await callback.message.answer(text)
    await callback.answer()

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.photo)
async def handle_game_photo(message: types.Message):
    chat_id = message.chat.id
    game = await get_game_state(chat_id)
    if not game or game["current_round"] == 0:
        return

    is_pro_chat = await check_chat_pro_status(chat_id)
    max_rounds = 100 if is_pro_chat else 10
    max_players = 10 if is_pro_chat else 2
    
    players_count = await get_clean_member_count(chat_id)
    if players_count > max_players:
        await process_welcome_rules(chat_id)
        return

    current_round = game["current_round"]
    scores = game["scores"]
    
    user_key = message.from_user.full_name if message.from_user.full_name else f"@{message.from_user.username}"
    scores[user_key] = scores.get(user_key, 0) + 1
    
    # Побудова тексту рахунку
    score_lines = ["Рахунок"]
    for usr, scr in scores.items():
        score_lines.append(f"{usr}: {scr}")
    score_text = "\n".join(score_lines)

    if current_round >= max_rounds:
        winner = max(scores, key=scores.get) if scores else "@user"
        text = f"{score_text}\n\nПереможець: {winner}\n\nНе забудь про свій приз!"
        builder = InlineKeyboardBuilder()
        builder.button(text=f"ОБНУЛИТИ РАУНД {current_round}", callback_data=f"undo_{current_round}")
        if is_pro_chat:
            builder.button(text="НОВА ГРА", callback_data="start_game_100")
        else:
            builder.button(text="НОВА ГРА ДО 10", callback_data="start_game_10")
            builder.button(text="НОВА ГРА ДО 100 (PRO)", callback_data="go_pay")
            builder.button(text="ДОДАТИ ГРАВЦІВ (PRO)", callback_data="go_pay")
        builder.adjust(1)
        await save_game_state(chat_id, 0, {})  # Завершуємо сесію
        await message.answer(text, reply_markup=builder.as_markup())
    else:
        next_round = current_round + 1
        await save_game_state(chat_id, next_round, scores)
        
        text = f"{score_text}\n\nЗавдання: {next_round}\n\nЗнайди і сфотографуй число {next_round}."
        builder = InlineKeyboardBuilder()
        builder.button(text=f"ОБНУЛИТИ РАУНД {next_round-1}", callback_data=f"undo_{next_round-1}")
        builder.button(text="НОВА ГРА" if is_pro_chat else "НОВА ГРА ДО 10", callback_data="start_game_100" if is_pro_chat else "start_game_10")
        builder.adjust(1)
        await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.message.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.data.startswith("undo_"))
async def handle_undo_round(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    target_round = int(callback.data.split("_")[1])
    game = await get_game_state(chat_id)
    
    # Якщо гра закінчилась, відновлюємо стан для відкату останнього раунду
    if not game or game["current_round"] == 0:
         async with db_pool.acquire() as conn:
             row = await conn.fetchrow("SELECT scores FROM games WHERE chat_id = $1", chat_id)
             if row:
                 import json
                 game = {"current_round": target_round + 1, "scores": json.loads(row["scores"])}

    if not game:
        await callback.answer("Сесію не знайдено.")
        return

    scores = game["scores"]
    user_key = callback.from_user.full_name if callback.from_user.full_name else f"@{callback.from_user.username}"
    
    if user_key in scores and scores[user_key] > 0:
        scores[user_key] -= 1
        
    await save_game_state(chat_id, target_round, scores)
    is_pro_chat = await check_chat_pro_status(chat_id)
    
    score_lines = ["Рахунок"]
    for usr, scr in scores.items():
        score_lines.append(f"{usr}: {scr}")
    score_text = "\n".join(score_lines)
    
    text = f"{score_text}\n\nЗавдання: {target_round}\n\nЗнайди і сфотографуй число {target_round}."
    builder = InlineKeyboardBuilder()
    if target_round > 1:
        builder.button(text=f"ОБНУЛИТИ РАУНД {target_round-1}", callback_data=f"undo_{target_round-1}")
    builder.button(text="НОВА ГРА" if is_pro_chat else "НОВА ГРА ДО 10", callback_data="start_game_100" if is_pro_chat else "start_game_10")
    builder.adjust(1)
    
    await callback.message.answer(text, reply_markup=builder.as_markup())
    await callback.answer()

# --- Логіка приватного чату (Заглушка та Адмінка) ---

@dp.message(F.chat.type == ChatType.PRIVATE, Command("stat"))
async def admin_stat_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    async with db_pool.acquire() as conn:
        all_chats = await conn.fetchval("SELECT COUNT(*) FROM chats")
        all_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        pro_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_pro = true")
        free_users = all_users - pro_users
        
        # Запит приросту (Приклад логіки інтервалів PostgreSQL)
        async def get_stats_for_interval(interval: str):
            c = await conn.fetchval(f"SELECT COUNT(*) FROM chats WHERE created_at >= NOW() - INTERVAL '{interval}'")
            u = await conn.fetchval(f"SELECT COUNT(*) FROM users WHERE created_at >= NOW() - INTERVAL '{interval}'")
            p = await conn.fetchval(f"SELECT COUNT(*) FROM users WHERE is_pro = true AND created_at >= NOW() - INTERVAL '{interval}'")
            return c, u, p

        c_24h, u_24h, p_24h = await get_stats_for_interval("24 hours")
        c_7d, u_7d, p_7d = await get_stats_for_interval("7 days")
        c_30d, u_30d, p_30d = await get_stats_for_interval("30 days")
        c_1y, u_1y, p_1y = await get_stats_for_interval("1 year")

    stat_text = (
        f"ЗА ВЕСЬ ЧАС:\n"
        f"- всі чати: {all_chats}\n"
        f"- всі юзери: {all_users}\n"
        f"- free-юзери: {free_users}\n"
        f"- pro-юзери: {pro_users}\n\n"
        f"ПРИРІСТ ЗА РІК:\n"
        f"- всі чати: +{c_1y}\n"
        f"- всі юзери: +{u_1y}\n"
        f"- free-юзери: +{u_1y - p_1y}\n"
        f"- pro-юзери: +{p_1y}\n\n"
        f"ПРИРІСТ ЗА 30 ДНІВ:\n"
        f"- всі чати: +{c_30d}\n"
        f"- всі юзери: +{u_30d}\n"
        f"- free-юзери: +{u_30d - p_30d}\n"
        f"- pro-юзери: +{p_30d}\n\n"
        f"ПРИРІСТ ЗА 7 ДНІВ:\n"
        f"- всі чати: +{c_7d}\n"
        f"- всі юзери: +{u_7d}\n"
        f"- free-юзери: +{u_7d - p_7d}\n"
        f"- pro-юзери: +{p_7d}\n\n"
        f"ПРИРІСТ ЗА 24 ГОД:\n"
        f"- всі чати: +{c_24h}\n"
        f"- всі юзери: +{u_24h}\n"
        f"- free-юзери: +{u_24h - p_24h}\n"
        f"- pro-юзери: +{p_24h}"
    )
    await message.answer(stat_text)

@dp.message(F.chat.type == ChatType.PRIVATE)
async def private_fallback_stub(message: types.Message):
    # Зберігаємо юзера в БД для актуальності статистики
    await save_user(message.from_user.id, message.from_user.username or "")
    if message.from_user.id == ADMIN_ID:
        # Для адміна ігноруємо текстові повідомлення, не заважаючи виклику /stat
        return
    await message.answer("Щоб грати, додай мене у групу з іншими людьми (не в особисті чати, а саме у групу).\nЗнайдеш мене через пошук – @stophotobot")

# --- FastAPI та Lifespan Вебхуку (Суворе правило Render) ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_pool()
    webhook_url = f"{BASE_URL}/webhook"
    await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    logger.info(f"Вебхук успішно встановлено на: {webhook_url}")
    yield
    await bot.delete_webhook()
    if db_pool:
        await db_pool.close()
    logger.info("Вебхук видалено, пул БД закрито.")

app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        update_data = await request.json()
        update = types.Update.model_validate(update_data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Помилка обробки оновлення: {e}")
        return Response(status_code=status.HTTP_200_OK)  # Повертаємо 200, щоб уникнути 500 та циклічних перезапусків

# --- Ендпоінт еквайрингу Monobank ---

@app.post("/monobank-webhook")
async def monobank_webhook(request: Request):
    try:
        data = await request.json()
        # Приклад структури моно: data = {"status": "success", "amount": 10000, "ccy": 980, "custom_data": {"user_id": "123456"}}
        # Сума передається в копійках (100 грн = 10000 копійок)
        if data.get("amount", 0) >= 10000:
            custom_data = data.get("custom_data", {})
            user_id = int(custom_data.get("user_id", 0))
            
            if user_id:
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE users SET is_pro = true WHERE user_id = $1", user_id)
                
                # Відправка успішного повідомлення в приват користувачу
                try:
                    builder = InlineKeyboardBuilder().button(text="НОВА ГРА", callback_data="start_game_100")
                    await bot.send_message(
                        user_id, 
                        "Дякую, оплата є!\n– @user тепер Pro\n– відкрито 100 раундів\n– відкрито 10 гравців", 
                        reply_markup=builder.as_markup()
                    )
                except TelegramAPIError as e:
                    logger.error(f"Не вдалося сповістити PRO користувача {user_id}: {e}")
                    
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Помилка вебхуку Monobank: {e}")
        return Response(status_code=status.HTTP_200_OK)
