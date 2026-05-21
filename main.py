import os
import logging
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import Command
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError

import asyncpg

# ==========================================
# 1. НАЛАШТУВАННЯ ТА ЛОГУВАННЯ
# ==========================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = os.getenv("BASE_URL", "https://stophotobot-1.onrender.com")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8115804787:AAHMz4sR8cH_AjKcZI8E8j1sPmug3BR_Ui8")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:stophotobot777@db.wfdgeuhdfluqccbunhiz.supabase.co:6543/postgres")

MONOBANK_PAY_URL = "https://send.monobank.ua/jar/example"
ADMIN_ID = 124303561

if not BOT_TOKEN:
    raise ValueError("Критична помилка: BOT_TOKEN відсутній у змінних оточення!")

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

db_pool = None

# ==========================================
# 2. МЕНЕДЖЕР КОНТЕКСТУ LIFESPAN
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    logger.info("Старт додатка: Ініціалізація пулу asyncpg...")
    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL, 
            min_size=1, 
            max_size=10,
            command_timeout=60
        )
    except Exception as e:
        logger.error(f"Не вдалося підключитися до Supabase PostgreSQL: {e}")
        raise e

    webhook_url = f"{BASE_URL}/webhook"
    logger.info(f"Встановлення вебхука Telegram: {webhook_url}")
    await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    
    yield
    
    logger.info("Зупинка додатка: Видалення вебхука та закриття пулу...")
    await bot.delete_webhook()
    await bot.session.close()
    if db_pool:
        await db_pool.close()

app = FastAPI(lifespan=lifespan)

# ==========================================
# 3. ДОПОМІЖНІ ФУНКЦІЇ БЕЗПЕКИ
# ==========================================
async def safe_send(chat_id: int, text: str, reply_markup=None, disable_web_page_preview=True):
    try:
        await bot.send_message(
            chat_id=chat_id, 
            text=text, 
            reply_markup=reply_markup, 
            disable_web_page_preview=disable_web_page_preview
        )
        return True
    except TelegramAPIError as e:
        logger.error(f"Помилка відправки повідомлення в чат {chat_id}: {e}")
        return False

def get_user_display_name(user: types.User) -> str:
    if user.username:
        return f"@{user.username}"
    if user.first_name:
        name = user.first_name
        if user.last_name:
            name += f" {user.last_name}"
        return name
    return f"User_{user.id}"

# ==========================================
# 4. РОБОТА З БАЗОЮ ДАНИХ (SUPABASE ASYNCPG)
# ==========================================
async def init_db_structure():
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users_pro (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS chat_sessions (
                chat_id BIGINT PRIMARY KEY,
                chat_title TEXT,
                current_round INT DEFAULT 0,
                is_pro BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS game_scores (
                chat_id BIGINT,
                user_id BIGINT,
                user_name TEXT,
                score INT DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );
        ''')

async def check_chat_pro_status(chat_id: int) -> bool:
    async with db_pool.acquire() as conn:
        chat_pro = await conn.fetchval("SELECT is_pro FROM chat_sessions WHERE chat_id = $1", chat_id)
        if chat_pro:
            return True
        users_in_chat = await conn.fetch("SELECT user_id FROM game_scores WHERE chat_id = $1", chat_id)
        if users_in_chat:
            user_ids = [row['user_id'] for row in users_in_chat]
            pro_count = await conn.fetchval(
                "SELECT COUNT(*) FROM users_pro WHERE user_id = ANY($1::BIGINT[])", user_ids
            )
            if pro_count and pro_count > 0:
                await conn.execute("UPDATE chat_sessions SET is_pro = TRUE WHERE chat_id = $1", chat_id)
                return True
        return False

async def reset_game_session(chat_id: int, chat_title: str):
    async with db_pool.acquire() as conn:
        is_pro = await conn.fetchval("SELECT is_pro FROM chat_sessions WHERE chat_id = $1", chat_id) or False
        await conn.execute('''
            INSERT INTO chat_sessions (chat_id, chat_title, current_round, is_pro)
            VALUES ($1, $2, 0, $3)
            ON CONFLICT (chat_id) DO UPDATE 
            SET current_round = 0, chat_title = $2, is_pro = EXCLUDED.is_pro
        ''', chat_id, chat_title, is_pro)
        await conn.execute("DELETE FROM game_scores WHERE chat_id = $1", chat_id)

# ==========================================
# 5. ГЕНЕРАЦІЯ ДИНАМІЧНИХ ПОСТІВ ЗГІДНО З ТЗ
# ==========================================
async def send_game_post(chat_id: int, chat_title: str):
    try:
        member_count = await bot.get_chat_member_count(chat_id)
        players_count = member_count - 1 if member_count > 1 else 1
    except Exception as e:
        logger.error(f"Не вдалося отримати кількість учасників чату {chat_id}: {e}")
        players_count = 2

    is_pro = await check_chat_pro_status(chat_id)

    # --- ЛОГІКА ДЛЯ БЕЗПЛАТНОЇ ВЕРСІЇ ---
    if not is_pro:
        if players_count == 1:
            text = (
                "Щоб грати, додайте в групу другого гравця.\n\n"
                "Щоб перезапустити бота, напишіть в чат команду /start або /play."
            )
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="free_to_10")]
            ])
            await safe_send(chat_id, text, markup)
            return

        elif players_count >= 3:
            text = (
                "Щоб грати втрьох і більше, хоча б 1 gramець має бути Pro.\n\n"
                "Pro-версія гри:\n"
                "- до 10 гравців\n"
                "- до 100 раундів назавжди\n"
                "- у всіх чатах Pro-гравця"
            )
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="КУПИТИ PRO-ВЕРСІЮ", callback_data="buy_pro")]
            ])
            await safe_send(chat_id, text, markup)
            return

        # Гра для 2 людей (FREE)
        text = (
            "Вітаємо у грі <a href='https://t.me/stophotobot'>100 PHOTO</a>!\n"
            "Правила гри:\n\n"
            "1. Завдання гравців – фотографувати числа (1, 2, 3) і надсилати у цей чат.\n"
            "2. Безоплатна гра триває 10 раундів, платна – 100 раундів. 1 раунд = 1 photo.\n"
            "За кожне фото гравець отримує 1 бал.\n\n"
            "3. Числа не можна створювати (викладати предметами) або писати самому.\n"
            "Лише фотографувати їх вдома, на вулиці тощо.\n\n"
            "4. Не можна повторювати двічі числа з однієї локації (номери сторінок у книзі, кнопки в ліфті тощо).\n"
            "Локації мають бути різними.\n\n"
            "5. Якщо надіслане фото не відповідає правилам, це photo можна відмінити і почати раунд заново.\n"
            "Щоб перезапустити бота, напишіть в чат команду /start або /play.\n\n"
            "За бажанням, придумайте приз переможцю.\n\n"
            "Натхнення!"
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="start_free_game")],
            [InlineKeyboardButton(text="НОВА ГРА ДО 100", callback_data="buy_pro")],
            [InlineKeyboardButton(text="ДОДАТИ ГРАВЦІВ", callback_data="buy_pro")]
        ])
        await safe_send(chat_id, text, markup)

    # --- ЛОГІКА ДЛЯ PRO ВЕРСІЇ ---
    else:
        if players_count == 1:
            text = (
                "Щоб грати, додайте в групу другого гравця.\n\n"
                "Щоб перезапустити бота, напишіть в чат команду /start або /play."
            )
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="free_to_10")]
            ])
            await safe_send(chat_id, text, markup)
            return

        elif players_count > 10:
            text = (
                "На жаль, грати може максимум 10 гравців.\n\n"
                "Щоб перезапустити бота, напишіть в чат команду /start або /play."
            )
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НАС ВЖЕ 10", callback_data="check_again")]
            ])
            await safe_send(chat_id, text, markup)
            return

        # Гра для 2-10 людей (PRO)
        text = (
            "Вітаємо у грі <a href='https://t.me/stophotobot'>100 PHOTO</a>!\n"
            "Правила гри:\n\n"
            "1. Завдання гравців – фотографувати числа (1, 2, 3) і надсилати у цей чат.\n"
            "2. Безоплатна гра триває 10 раундів, платна – 100 раундів. 1 раунд = 1 photo.\n"
            "За кожне photo гравець отримує 1 бал.\n\n"
            "3. Числа не можна створювати (викладати предметами) або писати самому.\n"
            "Лише фотографувати їх вдома, на вулиці тощо.\n\n"
            "4. Не можна повторювати двічі числа з однієї локації (номери сторінок у книзі, кнопки в ліфті тощо).\n"
            "Локації мають бути різними.\n\n"
            "5. Якщо надіслане фото не відповідає правилам, це photo можна відмінити і почати раунд заново.\n"
            "Щоб перезапустити бота, напишіть в чат команду /start або /play.\n\n"
            "За бажанням, придумайте приз переможцю.\n\n"
            "Натхнення!"
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game")]
        ])
        await safe_send(chat_id, text, markup)

async def send_round_post(chat_id: int, round_num: int, is_pro: bool):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_name, score FROM game_scores WHERE chat_id = $1 ORDER BY score DESC", chat_id)
    
    score_lines = []
    if round_num == 1 and not rows:
        if not is_pro:
            score_lines = ["player 1: 0", "player 2: 0"]
        else:
            score_lines = ["player 1: 0", "player 2: 0", "…", "player N: 0"]
    else:
        for r in rows:
            score_lines.append(f"{r['user_name']}: {r['score']}")
        if is_pro and len(rows) < 3:
            score_lines.append("…")
            score_lines.append("@userN: ...")

    score_text = "\n".join(score_lines)
    
    text = (
        f"Рахунок\n{score_text}\n\n"
        f"Завдання: {round_num}\n\n"
        f"Знайди і сфотографуй число {round_num}."
    )

    if round_num == 1:
        # Для Раунду 1 кнопок під постом за ТЗ немає
        await safe_send(chat_id, text)
    else:
        if not is_pro:
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"ОБНУЛИТИ РАУНД {round_num-1}", callback_data=f"rollback_{round_num-1}")],
                [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="free_to_10")],
                [InlineKeyboardButton(text="НОВА ГРА ДО 100", callback_data="buy_pro")],
                [InlineKeyboardButton(text="ДОДАТИ ГРАВЦІВ", callback_data="buy_pro")]
            ])
        else:
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"ОБНУЛИТИ РАУНД {round_num-1}", callback_data=f"rollback_{round_num-1}")],
                [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game")]
            ])
        await safe_send(chat_id, text, markup)

async def send_end_game_post(chat_id: int, is_pro: bool):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_name, score FROM game_scores WHERE chat_id = $1 ORDER BY score DESC", chat_id)
    
    score_lines = []
    winner = "@user"
    if rows:
        winner = rows[0]['user_name']
        for r in rows:
            score_lines.append(f"{r['user_name']}: {r['score']}")
    
    score_text = "\n".join(score_lines)
    max_round = 100 if is_pro else 10

    text = (
        f"Рахунок\n{score_text}\n\n"
        f"Переможець: {winner}\n\n"
        f"Не забудь про свій приз!"
    )

    if not is_pro:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"ОБНУЛИТИ РАУНД {max_round}", callback_data=f"rollback_{max_round}")],
            [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="free_to_10")],
            [InlineKeyboardButton(text="НОВА ГРА ДО 100", callback_data="buy_pro")],
            [InlineKeyboardButton(text="ДОДАТИ ГРАВЦІВ", callback_data="buy_pro")]
        ])
    else:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"ОБНУЛИТИ РАУНД {max_round}", callback_data=f"rollback_{max_round}")],
            [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game")]
        ])
    await safe_send(chat_id, text, markup)

# ==========================================
# 6. ХЕНДЛЕРИ СЕРВІСУ AIOGRAM 3.X
# ==========================================

@dp.my_chat_member(ChatMemberUpdatedFilter(member_change=IS_NOT_MEMBER >> IS_MEMBER))
async def on_bot_added_to_chat(event: ChatMemberUpdated):
    logger.info(f"Тригер: Бот доданий у чат {event.chat.title} (ID: {event.chat.id})")
    await init_db_structure()
    await reset_game_session(event.chat.id, event.chat.title)
    await send_game_post(event.chat.id, event.chat.title)

@dp.message(Command(commands=["start", "play"]))
async def cmd_start_play(message: types.Message):
    await init_db_structure()
    
    if message.chat.type == "private":
        if message.from_user.id == ADMIN_ID:
            await message.answer("Вам доступна команда /stat для моніторингу.")
        else:
            await message.answer(
                "Щоб грати, додай мене у групу з іншими людьми (не в особисті чати, а саме у групу). "
                "найдеш мене через пошук @stophotobot"
            )
        return

    logger.info(f"Команда перезапуску {message.text} в групі {message.chat.id}")
    await reset_game_session(message.chat.id, message.chat.title)
    await send_game_post(message.chat.id, message.chat.title)

@dp.message(Command("stat"))
async def cmd_admin_stat(message: types.Message):
    if message.chat.type != "private" or message.from_user.id != ADMIN_ID:
        return

    now = datetime.utcnow()
    async with db_pool.acquire() as conn:
        async def fetch_metrics(delta_days=None):
            if delta_days:
                date_limit = now - timedelta(days=delta_days)
                chats = await conn.fetchval("SELECT COUNT(*) FROM chat_sessions WHERE created_at >= $1", date_limit)
                users_pro = await conn.fetchval("SELECT COUNT(*) FROM users_pro WHERE created_at >= $1", date_limit)
                users_all = await conn.fetchval("SELECT COUNT(DISTINCT user_id) FROM game_scores JOIN chat_sessions USING (chat_id) WHERE chat_sessions.created_at >= $1", date_limit)
                users_free = max(0, (users_all or 0) - (users_pro or 0))
                return chats or 0, users_all or 0, users_free, users_pro or 0
            else:
                chats = await conn.fetchval("SELECT COUNT(*) FROM chat_sessions")
                users_pro = await conn.fetchval("SELECT COUNT(*) FROM users_pro")
                users_all = await conn.fetchval("SELECT COUNT(DISTINCT user_id) FROM game_scores")
                users_free = max(0, (users_all or 0) - (users_pro or 0))
                return chats or 0, users_all or 0, users_free, users_pro or 0

        all_time, y_365, y_30, y_7 = await asyncio.gather(
            fetch_metrics(),
            fetch_metrics(365),
            fetch_metrics(30),
            fetch_metrics(7)
        )
        y_1 = await fetch_metrics(1)

    stat_text = (
        "ЗА ВЕСЬ ЧАС:\n"
        f"- всі чати: {all_time[0]}\n"
        f"- всі юзери: {all_time[1]}\n"
        f"- free-юзери: {all_time[2]}\n"
        f"- pro-юзери: {all_time[3]}\n\n"
        "ПРИРІСТ ЗА РІК:\n"
        f"- всі чати: +{y_365[0]}\n"
        f"- всі юзери: +{y_365[1]}\n"
        f"- free-юзери: +{y_365[2]}\n"
        f"- pro-юзери: +{y_365[3]}\n\n"
        "ПРИРІСТ ЗА 30 ДНІВ:\n"
        f"- всі чати: +{y_30[0]}\n"
        f"- всі юзери: +{y_30[1]}\n"
        f"- free-юзери: +{y_30[2]}\n"
        f"- pro-юзери: +{y_30[3]}\n\n"
        "ПРИРІСТ ЗА 7 ДНІВ:\n"
        f"- всі чати: +{y_7[0]}\n"
        f"- всі юзери: +{y_7[1]}\n"
        f"- free-юзери: +{y_7[2]}\n"
        f"- pro-юзери: +{y_7[3]}\n\n"
        "ПРИРІСТ ЗА 24 ГОД:\n"
        f"- всі чати: +{y_1[0]}\n"
        f"- всі юзери: +{y_1[1]}\n"
        f"- free-юзери: +{y_1[2]}\n"
        f"- pro-юзери: +{y_1[3]}"
    )
    await message.answer(stat_text)

@dp.callback_query()
async def handle_callbacks(callback: types.CallbackQuery):
    if callback.message.chat.type == "private":
        await callback.answer()
        return

    data = callback.data
    chat_id = callback.message.chat.id
    chat_title = callback.message.chat.title

    async with db_pool.acquire() as conn:
        is_pro = await check_chat_pro_status(chat_id)

    if data in ["free_to_10", "start_free_game", "check_again"]:
        await reset_game_session(chat_id, chat_title)
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE chat_sessions SET current_round = 1 WHERE chat_id = $1", chat_id)
        await callback.answer("Гру активовано!")
        await send_round_post(chat_id, 1, is_pro)

    elif data == "start_pro_game":
        await reset_game_session(chat_id, chat_title)
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE chat_sessions SET current_round = 1, is_pro = TRUE WHERE chat_id = $1", chat_id)
        await callback.answer("PRO-гру активовано!")
        await send_round_post(chat_id, 1, True)

    elif data == "buy_pro":
        logger.info(f"Користувач {callback.from_user.id} клікнув Купити PRO в чаті {chat_id} о {datetime.utcnow()}")
        text = (
            "Pro-версія гри:\n"
            "- до 10 гравців\n"
            "- до 100 раундів назавжди\n"
            "- у всіх чатах Pro-гравця"
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="КУПИТИ PRO-ВЕРСІЮ", url=MONOBANK_PAY_URL)],
            [InlineKeyboardButton(text="ПРОДОВЖИТИ ГРУ УДВОХ", callback_data="free_to_10")]
        ])
        await callback.message.answer(text, reply_markup=markup)
        await callback.answer()

    elif data.startswith("rollback_"):
        target_round = int(data.split("_")[1])
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE chat_sessions SET current_round = $1 WHERE chat_id = $2", target_round, chat_id)
            await conn.execute("UPDATE game_scores SET score = GREATEST(0, score - 1) WHERE chat_id = $1 AND user_id = $2", chat_id, callback.from_user.id)
        await callback.answer(f"Раунд відмінено! Повернення до {target_round}")
        await send_round_post(chat_id, target_round, is_pro)

    await callback.answer()

@dp.message(lambda message: message.photo is not None)
async def handle_game_photo(message: types.Message):
    if message.chat.type == "private":
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = get_user_display_name(message.from_user)

    async with db_pool.acquire() as conn:
        session = await conn.fetchrow("SELECT current_round, is_pro FROM chat_sessions WHERE chat_id = $1", chat_id)
    
    if not session or session['current_round'] == 0:
        return

    current_round = session['current_round']
    is_pro = session['is_pro']
    max_rounds = 100 if is_pro else 10

    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO game_scores (chat_id, user_id, user_name, score)
            VALUES ($1, $2, $3, 1)
            ON CONFLICT (chat_id, user_id) DO UPDATE
            SET score = game_scores.score + 1, user_name = EXCLUDED.user_name
        ''', chat_id, user_id, user_name)

        next_round = current_round + 1
        await conn.execute("UPDATE chat_sessions SET current_round = $1 WHERE chat_id = $2", next_round, chat_id)

    if current_round >= max_rounds:
        await send_end_game_post(chat_id, is_pro)
    else:
        await send_round_post(chat_id, next_round, is_pro)

@dp.message()
async def ignore_regular_text(message: types.Message):
    pass

# ==========================================
# 7. ЕНДПОІНТИ ДЛЯ ВЕБХУКІВ FASTAPI
# ==========================================

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        update_data = await request.json()
        update = types.Update(**update_data)
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.error(f"Помилка обробки вебхука Telegram: {e}")
    return {"status": "ok"}

@app.post("/monobank-webhook")
async def monobank_webhook(request: Request):
    try:
        data = await request.json()
        amount = data.get("amount", 0) / 100
        user_id = int(data.get("statementItem", {}).get("comment", 0))
        
        if amount >= 100 and user_id:
            async with db_pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO users_pro (user_id, created_at)
                    VALUES ($1, NOW())
                    ON CONFLICT (user_id) DO NOTHING
                ''', user_id)
                
            success_text = (
                "Дякую, оплата є!\n"
                "– @user тепер Pro\n"
                "– відкрито 100 раундів\n"
                "– відкрито 10 гравців"
            )
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game")]
            ])
            await safe_send(user_id, success_text, markup)
            
    except Exception as e:
        logger.error(f"Помилка еквайрингу Monobank: {e}")
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "ok", "service": "100 PHOTO Server API"}
