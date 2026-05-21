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

# Жорстко зафіксовані змінні оточення (Render)
BASE_URL = os.getenv("BASE_URL", "https://stophotobot-1.onrender.com") [cite: 21]
BOT_TOKEN = os.getenv("BOT_TOKEN", "8115804787:AAHMz4sR8cH_AjKcZI8E8j1sPmug3BR_Ui8") [cite: 21]
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:stophotobot777@db.wfdgeuhdfluqccbunhiz.supabase.co:6543/postgres") [cite: 21]

# Лінк для QR-оплати Monobank (легко налаштовується в коді)
MONOBANK_PAY_URL = "https://send.monobank.ua/jar/example" [cite: 17, 97]
ADMIN_ID = 124303561 [cite: 8, 32, 41]

# Перевірка наявності критичних конфігів
if not BOT_TOKEN:
    raise ValueError("Критична помилка: BOT_TOKEN відсутній у змінних оточення!") [cite: 18]

# Ініціалізація компонентів aiogram (Працює суворо через Webhook)
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# Глобальний пул підключень до бази даних asyncpg
db_pool = None

# ==========================================
# 2. МЕНЕДЖЕР КОНТЕКСТУ LIFESPAN (СУВОРЕ ПРАВИЛО)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI): [cite: 12]
    global db_pool
    logger.info("Старт додатка: Ініціалізація пулу asyncpg...") [cite: 14]
    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL, 
            min_size=1, 
            max_size=10,
            command_timeout=60
        ) [cite: 14]
    except Exception as e:
        logger.error(f"Не вдалося підключитися до Supabase PostgreSQL: {e}") [cite: 14]
        raise e

    # Реєстрація вебхука при старті додатка
    webhook_url = f"{BASE_URL}/webhook" [cite: 28]
    logger.info(f"Встановлення вебхука Telegram: {webhook_url}") [cite: 12]
    await bot.set_webhook(url=webhook_url, drop_pending_updates=True) [cite: 12]
    
    yield
    
    # Видалення вебхука та закриття ресурсів при зупинці
    logger.info("Зупинка додатка: Видалення вебхука та закриття пулу...") [cite: 12]
    await bot.delete_webhook() [cite: 12]
    await bot.session.close() [cite: 12]
    if db_pool:
        await db_pool.close() [cite: 14]

# Створення FastAPI сервісу
app = FastAPI(lifespan=lifespan) [cite: 11, 12]

# ==========================================
# 3. ДОПОМІЖНІ ФУНКЦІЇ (БЕЗПЕКА ТА ЗАХИСТ ВІД 500)
# ==========================================
async def safe_send(chat_id: int, text: str, reply_markup=None, disable_web_page_preview=True): [cite: 22]
    """Захист від падіння сервера 500 при помилках Telegram API"""
    try:
        await bot.send_message(
            chat_id=chat_id, 
            text=text, 
            reply_markup=reply_markup, 
            disable_web_page_preview=disable_web_page_preview
        ) [cite: 22]
        return True
    except TelegramAPIError as e: [cite: 22]
        logger.error(f"Помилка відправки повідомлення в чат {chat_id}: {e}") [cite: 22]
        return False [cite: 22]

def get_user_display_name(user: types.User) -> str: [cite: 53]
    """Повертає повне ім'я профілю або @username, якщо імені немає"""
    if user.first_name: [cite: 53]
        name = user.first_name [cite: 53]
        if user.last_name: [cite: 53]
            name += f" {user.last_name}" [cite: 53]
        return name [cite: 53]
    return f"@{user.username}" if user.username else f"User_{user.id}" [cite: 54]

# ==========================================
# 4. РОБОТА З БАЗОЮ ДАНИХ (SUPABASE ASYNCPG)
# ==========================================
async def init_db_structure():
    """Створення таблиць за потреби, якщо вони ще відсутні у схемі"""
    async with db_pool.acquire() as conn: [cite: 14]
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users_pro (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        ''') [cite: 101]
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS chat_sessions (
                chat_id BIGINT PRIMARY KEY,
                chat_title TEXT,
                current_round INT DEFAULT 0,
                is_pro BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        ''') [cite: 39]
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS game_scores (
                chat_id BIGINT,
                user_id BIGINT,
                user_name TEXT,
                score INT DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );
        ''') [cite: 49]

async def check_chat_pro_status(chat_id: int) -> bool: [cite: 65]
    """Перевіряє, чи має хоча б один поточний учасник гри або сам чат статус PRO"""
    async with db_pool.acquire() as conn: [cite: 14]
        # Перевірка PRO по самому чату
        chat_pro = await conn.fetchval("SELECT is_pro FROM chat_sessions WHERE chat_id = $1", chat_id) [cite: 39]
        if chat_pro:
            return True
        # Перевірка, чи є серед активних гравців цього чату хоча б один PRO юзер
        users_in_chat = await conn.fetch("SELECT user_id FROM game_scores WHERE chat_id = $1", chat_id) [cite: 49]
        if users_in_chat:
            user_ids = [row['user_id'] for row in users_in_chat]
            pro_count = await conn.fetchval(
                "SELECT COUNT(*) FROM users_pro WHERE user_id = ANY($1::BIGINT[])", user_ids
            ) [cite: 101]
            if pro_count and pro_count > 0:
                # Автоматично оновлюємо сесію чату до PRO
                await conn.execute("UPDATE chat_sessions SET is_pro = TRUE WHERE chat_id = $1", chat_id) [cite: 39, 107]
                return True
        return False

async def reset_game_session(chat_id: int, chat_title: str): [cite: 55]
    """Повне скидання ігрового стану для конкретного групового чату"""
    async with db_pool.acquire() as conn: [cite: 14]
        # Зберігаємо статус PRO при перезапуску
        is_pro = await conn.fetchval("SELECT is_pro FROM chat_sessions WHERE chat_id = $1", chat_id) or False [cite: 39]
        await conn.execute('''
            INSERT INTO chat_sessions (chat_id, chat_title, current_round, is_pro)
            VALUES ($1, $2, 0, $3)
            ON CONFLICT (chat_id) DO UPDATE 
            SET current_round = 0, chat_title = $2, is_pro = EXCLUDED.is_pro
        ''', chat_id, chat_title, is_pro) [cite: 39]
        await conn.execute("DELETE FROM game_scores WHERE chat_id = $1", chat_id) [cite: 49]

# ==========================================
# 5. ГЕНЕРАЦІЯ ДИНАМІЧНИХ ПОСТІВ ЗГІДНО З ТЗ
# ==========================================
async def send_game_post(chat_id: int, chat_title: str, trigger_check=False): [cite: 39, 64]
    """Формує та надсилає потрібний пост відповідно до кількості людей та PRO-статусу"""
    try:
        member_count = await bot.get_chat_member_count(chat_id) [cite: 24, 64]
        # Кількість гравців без урахування самого бота
        players_count = member_count - 1 if member_count > 1 else 1 [cite: 56, 57]
    except Exception as e:
        logger.error(f"Не вдалося отримати кількість учасників чату {chat_id}: {e}") [cite: 24]
        players_count = 2  # Валідна дефолтна заглушка для безпеки рантайму

    # Перевіряємо PRO-статус чату
    is_pro = await check_chat_pro_status(chat_id) [cite: 65]

    async with db_pool.acquire() as conn: [cite: 14]
        current_round = await conn.fetchval("SELECT current_round FROM chat_sessions WHERE chat_id = $1", chat_id) or 0 [cite: 39]

    # --- ЛОГІКА ДЛЯ БЕЗПЛАТНОЇ ВЕРСІЇ ---
    if not is_pro: [cite: 67, 73]
        if players_count == 1: [cite: 67]
            text = (
                "Щоб грати, додайте в групу другого гравця.\n"
                "Щоб перезапустити бота, напишіть в чат команду /start або /play."
            ) [cite: 111, 112]
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="free_to_10")] [cite: 113]
            ])
            await safe_send(chat_id, text, markup) [cite: 22]
            return

        elif players_count >= 3: [cite: 69, 73]
            text = (
                "Щоб грати втрьох і більше, хоча б 1 gramець має бути Pro.\n\n"
                "Pro-версія гри:\n"
                "- до 10 гравців\n"
                "- до 100 раундів назавжди\n"
                "- у всіх чатах Pro-гравця"
            ) [cite: 113, 114]
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="КУПИТИ PRO-ВЕРСІЮ", callback_data="buy_pro")] [cite: 114]
            ])
            await safe_send(chat_id, text, markup) [cite: 22]
            return

        # Валідна FREE гра для 2 людей [cite: 68]
        if current_round == 0:
            # ПОСТ "ПРАВИЛА" [cite: 116]
            text = (
                "Вітаємо у грі 100 PHOTO!\n"
                "Правила гри:\n\n"
                "1. Завдання гравців – фотографувати числа (1, 2, 3) і надсилати у цей чат.\n"
                "2. Безоплатна гра триває 10 раундів, платна – 100 раундів. 1 раунд = 1 фото.\n"
                "За кожне фото гравець отримує 1 бал.\n\n"
                "3. Числа не можна створювати (викладати предметами) або писати самому.\n"
                "Лише фотографувати їх вдома, на вулиці тощо.\n\n"
                "4. Не можна повторювати двічі числа з однієї локації (номери сторінок у книзі, кнопки в ліфті тощо).\n"
                "Локації мають бути різними.\n\n"
                "5. Якщо надіслане фото не відповідає правилам, це photo можна відмінити і почати раунд заново.\n"
                "Щоб перезапустити бота, напишіть в чат команду /start або /play.\n\n"
                "За бажанням, придумайте приз переможцю.\n\n"
                "Натхнення!"
            ) [cite: 116, 117, 118, 119, 120, 121, 122]
            # За ТЗ назва гри "100 PHOTO" є лінком на бота [cite: 116]
            text = text.replace("Вітаємо у грі 100 PHOTO!", "Вітаємо у грі <a href='https://t.me/stophotobot'>100 PHOTO</a>!") [cite: 116]
            
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="start_free_game")], [cite: 123]
                [InlineKeyboardButton(text="НОВА ГРА ДО 100", callback_data="buy_pro")], [cite: 123]
                [InlineKeyboardButton(text="ДОДАТИ ГРАВЦІВ", callback_data="buy_pro")] [cite: 123]
            ])
            await safe_send(chat_id, text, markup) [cite: 22]

    # --- ЛОГІКА ДЛЯ PRO ВЕРСІЇ ---
    else: [cite: 70, 74]
        if players_count == 1: [cite: 70]
            text = (
                "Щоб грати, додайте в групу другого гравця.\n"
                "Щоб перезапустити бота, напишіть в чат команду /start або /play."
            ) [cite: 111, 112]
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="free_to_10")] [cite: 113]
            ])
            await safe_send(chat_id, text, markup) [cite: 22]
            return

        elif players_count > 10: [cite: 72, 74]
            text = (
                "На жаль, грати може максимум 10 гравців.\n"
                "Щоб перезапустити бота, напишіть в чат команду /start або /play."
            ) [cite: 114, 115]
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НАС ВЖЕ 10", callback_data="check_again")] [cite: 116]
            ])
            await safe_send(chat_id, text, markup) [cite: 22]
            return

        # Валідна PRO гра для 2-10 людей [cite: 71]
        if current_round == 0:
            # ПОСТ "ПРАВИЛА ГРИ" ДЛЯ PRO ВЕРСІЇ [cite: 110]
            text = (
                "Вітаємо у грі <a href='https://t.me/stophotobot'>100 PHOTO</a>!\n"
                "Правила гри:\n\n"
                "1. Завдання гравців – фотографувати числа (1, 2, 3) і надсилати у цей чат.\n"
                "2. Безоплатна гра триває 10 раундів, платна – 100 раундів. 1 раунд = 1 фото.\n"
                "За кожне фото гравець отримує 1 бал.\n\n"
                "3. Числа не можна створювати (викладати предметами) або писати самому.\n"
                "Лише фотографувати їх вдома, на вулиці тощо.\n\n"
                "4. Не можна повторювати двічі числа з однієї локації (номери сторінок у книзі, кнопки в ліфті тощо).\n"
                "Локації мають бути різними.\n\n"
                "5. Якщо надіслане фото не відповідає правилам, це photo можна відмінити і почати раунд заново.\n"
                "Щоб перезапустити бота, напишіть в чат команду /start або /play.\n\n"
                "За бажанням, придумайте приз переможцю.\n\n"
                "Натхнення!"
            ) [cite: 116, 117, 118, 119, 120, 121, 122]
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game")] [cite: 110]
            ])
            await safe_send(chat_id, text, markup) [cite: 22]

async def send_round_post(chat_id: int, round_num: int, is_pro: bool): [cite: 61, 85, 88]
    """Генерує та відправляє поточний раунд із таблицею рахунку активних гравців чату"""
    async with db_pool.acquire() as conn: [cite: 14]
        rows = await conn.fetch("SELECT user_name, score FROM game_scores WHERE chat_id = $1 ORDER BY score DESC", chat_id) [cite: 49]
    
    # Побудова тексту рахунку
    score_lines = []
    if round_num == 1 and not rows: [cite: 85, 110]
        score_lines = ["player 1: 0", "player 2: 0"] [cite: 85, 110]
        if is_pro:
            score_lines.append("…") [cite: 110]
            score_lines.append("player N: 0") [cite: 110]
    else:
        for r in rows:
            score_lines.append(f"{r['user_name']}: {r['score']}") [cite: 60, 62]
        if is_pro and len(rows) < 3: [cite: 110]
            score_lines.append("…") [cite: 110]
            score_lines.append("@userN: ...") [cite: 110]

    score_text = "\n".join(score_lines)
    
    text = (
        f"Рахунок\n{score_text}\n\n"
        f"Завдання: {round_num}\n\n"
        f"Знайди і сфотографуй число {round_num}."
    ) [cite: 61, 85, 110]

    # Кнопки для поточного раунду
    if round_num == 1:
        # Для Раунду 1 кнопок за ТЗ немає
        await safe_send(chat_id, text) [cite: 22, 85]
    else:
        if not is_pro: [cite: 88]
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"ОБНУЛИТИ РАУНД {round_num-1}", callback_data=f"rollback_{round_num-1}")], [cite: 34, 89]
                [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="free_to_10")], [cite: 34, 90]
                [InlineKeyboardButton(text="НОВА ГРА ДО 100", callback_data="buy_pro")], [cite: 34, 91]
                [InlineKeyboardButton(text="ДОДАТИ ГРАВЦІВ", callback_data="buy_pro")] [cite: 34, 91]
            ])
        else: [cite: 110]
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"ОБНУЛИТИ РАУНД {round_num-1}", callback_data=f"rollback_{round_num-1}")], [cite: 34, 110]
                [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game")] [cite: 34, 110]
            ])
        await safe_send(chat_id, text, markup) [cite: 22]

async def send_end_game_post(chat_id: int, is_pro: bool): [cite: 92, 110]
    """Фінальний пост завершення гри та оголошення переможця"""
    async with db_pool.acquire() as conn: [cite: 14]
        rows = await conn.fetch("SELECT user_name, score FROM game_scores WHERE chat_id = $1 ORDER BY score DESC", chat_id) [cite: 49]
    
    score_lines = []
    winner = "@user" [cite: 123, 126]
    if rows:
        winner = rows[0]['user_name'] [cite: 52]
        for r in rows:
            score_lines.append(f"{r['user_name']}: {r['score']}") [cite: 62]
    
    score_text = "\n".join(score_lines)
    max_round = 100 if is_pro else 10 [cite: 56, 57]

    text = (
        f"Рахунок\n{score_text}\n\n"
        f"Переможець: {winner}\n\n"
        f"Не забудь про свій приз!"
    ) [cite: 123, 126]

    if not is_pro: [cite: 92]
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ОБНУЛИТИ РАУНД 10", callback_data="rollback_10")], [cite: 34, 93, 123]
            [InlineKeyboardButton(text="НОВА ГРА ДО 10", callback_data="free_to_10")], [cite: 34, 94, 123]
            [InlineKeyboardButton(text="НОВА ГРА ДО 100", callback_data="buy_pro")], [cite: 34, 95, 123]
            [InlineKeyboardButton(text="ДОДАТИ ГРАВЦІВ", callback_data="buy_pro")] [cite: 34, 95, 123]
        ])
    else: [cite: 110]
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ОБНУЛИТИ РАУНД 100", callback_data="rollback_100")], [cite: 34, 110, 126]
            [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game")] [cite: 34, 110, 126]
        ])
    await safe_send(chat_id, text, markup) [cite: 22]

# ==========================================
# 6. ХЕНДЛЕРИ ДЛЯ ОБРОБКИ ХЕНДЛЕРІВ AIOGRAM 3.X
# ==========================================

# МИТТЄВА ПОДІЯ ДОДАВАННЯ БОТА В ГРУПУ (Сучасні фільтри aiogram 3.x)
@dp.my_chat_member(ChatMemberUpdatedFilter(member_change=IS_NOT_MEMBER >> IS_MEMBER)) [cite: 24, 25]
async def on_bot_added_to_chat(event: ChatMemberUpdated): [cite: 24]
    logger.info(f"Тригер: Бот доданий у чат {event.chat.title} (ID: {event.chat.id})") [cite: 38]
    await init_db_structure()
    await reset_game_session(event.chat.id, event.chat.title) [cite: 39]
    await send_game_post(event.chat.id, event.chat.title, trigger_check=True) [cite: 39]

# КОМАНДИ /start ТА /play
@dp.message(Command(commands=["start", "play"]))
async def cmd_start_play(message: types.Message): [cite: 23, 55]
    await init_db_structure()
    
    # Валідація типу чату (Приватні повідомлення)
    if message.chat.type == "private": [cite: 5, 23]
        if message.from_user.id == ADMIN_ID: [cite: 8, 31]
            # Для адміна доступна лише команда /stat, ігрові команди в лічці скидають інструкцію
            await message.answer("Вам доступна команда /stat для моніторингу.") [cite: 8, 31, 41]
        else:
            # Заглушка для звичайного користувача
            await message.answer(
                "Щоб грати, додай мене у групу з іншими людьми (не в особисті чати, а саме у групу). "
                "найдеш мене через пошук @stophotobot"
            ) [cite: 7, 30, 36]
        return [cite: 23]

    # Команди в групі ініціюють перезапуск ігрової сесії
    logger.info(f"Команда перезапуску {message.text} в групі {message.chat.id}") [cite: 55]
    await reset_game_session(message.chat.id, message.chat.title) [cite: 55]
    await send_game_post(message.chat.id, message.chat.title) [cite: 39]

# АДМІН-КОМАНДА /stat (ВИКЛЮЧНО В ПРИВАТІ ДЛЯ АДМІНІСТРАТОРА)
@dp.message(Command("stat"))
async def cmd_admin_stat(message: types.Message): [cite: 41]
    if message.chat.type != "private" or message.from_user.id != ADMIN_ID: [cite: 42]
        return  # Повністю ігноруємо, якщо викликано не адміном або у групі [cite: 42]

    now = datetime.utcnow()
    
    # Асинхронні паралельні агрегатні запити до Supabase для швидкодії (asyncio.gather)
    async with db_pool.acquire() as conn: [cite: 14]
        async def fetch_metrics(delta_days=None):
            if delta_days:
                date_limit = now - timedelta(days=delta_days)
                chats = await conn.fetchval("SELECT COUNT(*) FROM chat_sessions WHERE created_at >= $1", date_limit) [cite: 43]
                users_pro = await conn.fetchval("SELECT COUNT(*) FROM users_pro WHERE created_at >= $1", date_limit) [cite: 43]
                # Унікальні користувачі, що грали
                users_all = await conn.fetchval("SELECT COUNT(DISTINCT user_id) FROM game_scores JOIN chat_sessions USING (chat_id) WHERE chat_sessions.created_at >= $1", date_limit) [cite: 43]
                users_free = max(0, (users_all or 0) - (users_pro or 0))
                return chats or 0, users_all or 0, users_free, users_pro or 0
            else:
                chats = await conn.fetchval("SELECT COUNT(*) FROM chat_sessions") [cite: 43]
                users_pro = await conn.fetchval("SELECT COUNT(*) FROM users_pro") [cite: 43]
                users_all = await conn.fetchval("SELECT COUNT(DISTINCT user_id) FROM game_scores") [cite: 43]
                users_free = max(0, (users_all or 0) - (users_pro or 0))
                return chats or 0, users_all or 0, users_free, users_pro or 0

        # Виконання запитів паралельно
        all_time, y_365, y_30, y_7 = await asyncio.gather(
            fetch_metrics(),
            fetch_metrics(365),
            fetch_metrics(30),
            fetch_metrics(7)
        ) [cite: 44]
        
        # Для 24 годин рахуємо окремо
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
    ) [cite: 45, 126]
    
    await message.answer(stat_text)

# ОБРОБКА КЛІКІВ НА ІНЛАЙН КНОПКИ (Callback Queries)
@dp.callback_query()
async def handle_callbacks(callback: types.CallbackQuery):
    # В особистих повідомленнях будь-які ігрові кнопки ігноруються
    if callback.message.chat.type == "private": [cite: 8, 30]
        await callback.answer()
        return

    data = callback.data
    chat_id = callback.message.chat.id
    chat_title = callback.message.chat.title

    async with db_pool.acquire() as conn: [cite: 14]
        is_pro = await check_chat_pro_status(chat_id) [cite: 65]

    if data in ["free_to_10", "start_free_game", "check_again"]: [cite: 76, 83, 84]
        await reset_game_session(chat_id, chat_title) [cite: 55]
        async with db_pool.acquire() as conn: [cite: 14]
            await conn.execute("UPDATE chat_sessions SET current_round = 1 WHERE chat_id = $1", chat_id) [cite: 39]
        await callback.answer("Гру активовано!")
        await send_round_post(chat_id, 1, is_pro) [cite: 84]

    elif data == "start_pro_game": [cite: 103, 110]
        await reset_game_session(chat_id, chat_title) [cite: 55]
        async with db_pool.acquire() as conn: [cite: 14]
            await conn.execute("UPDATE chat_sessions SET current_round = 1, is_pro = TRUE WHERE chat_id = $1", chat_id) [cite: 39]
        await callback.answer("PRO-гру активовано!")
        await send_round_post(chat_id, 1, True) [cite: 103]

    elif data == "buy_pro": [cite: 79, 91, 95]
        # Зберігаємо факт кліку у логах (Telegram ID користувача та час)
        logger.info(f"Користувач {callback.from_user.id} клікнув Купити PRO в чаті {chat_id} о {datetime.utcnow()}") [cite: 98]
        
        text = (
            "Pro-версія гри:\n"
            "- до 10 гравців\n"
            "- до 100 раундів назавжди\n"
            "- у всіх чатах Pro-гравця"
        ) [cite: 123]
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="КУПИТИ PRO-ВЕРСІЮ", url=MONOBANK_PAY_URL)], [cite: 34, 96, 97, 123]
            [InlineKeyboardButton(text="ПРОДОВЖИТИ ГРУ УДВОХ", callback_data="free_to_10")] [cite: 34, 123]
        ])
        await callback.message.answer(text, reply_markup=markup) [cite: 96]
        await callback.answer()

    elif data.startswith("rollback_"): [cite: 89, 93]
        target_round = int(data.split("_")[1])
        async with db_pool.acquire() as conn: [cite: 14]
            await conn.execute("UPDATE chat_sessions SET current_round = $1 WHERE chat_id = $2", target_round, chat_id) [cite: 39]
            # Знімаємо 1 бал з того, хто останній відповідав (спрощена логіка ролбеку)
            await conn.execute("UPDATE game_scores SET score = GREATEST(0, score - 1) WHERE chat_id = $1 AND user_id = $2", chat_id, callback.from_user.id) [cite: 49, 89]
        await callback.answer(f"Раунд відмінено! Повернення до {target_round}")
        await send_round_post(chat_id, target_round, is_pro) [cite: 89]

    await callback.answer()

# ОБРОБКА ОТРИМАННЯ ФОТОГРАФІЙ (Головний ігровий тригер)
@dp.message(lambda message: message.photo is not None)
async def handle_game_photo(message: types.Message):
    if message.chat.type == "private": [cite: 5, 30]
        return  # Повністю ігноруємо фото в особистих повідомленнях [cite: 5]

    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = get_user_display_name(message.from_user) [cite: 53]

    async with db_pool.acquire() as conn: [cite: 14]
        session = await conn.fetchrow("SELECT current_round, is_pro FROM chat_sessions WHERE chat_id = $1", chat_id) [cite: 39]
    
    if not session or session['current_round'] == 0:
        return  # Гра ще не розпочата кнопкою, ігноруємо фото

    current_round = session['current_round']
    is_pro = session['is_pro']
    max_rounds = 100 if is_pro else 10 [cite: 118]

    # Перша отримана фотографія автоматично зараховується як відповідь
    async with db_pool.acquire() as conn: [cite: 14]
        # Заносимо юзера або оновлюємо його рахунок
        await conn.execute('''
            INSERT INTO game_scores (chat_id, user_id, user_name, score)
            VALUES ($1, $2, $3, 1)
            ON CONFLICT (chat_id, user_id) DO UPDATE
            SET score = game_scores.score + 1, user_name = EXCLUDED.user_name
        ''', chat_id, user_id, user_name) [cite: 49]

        next_round = current_round + 1
        await conn.execute("UPDATE chat_sessions SET current_round = $1 WHERE chat_id = $2", next_round, chat_id) [cite: 39]

    if current_round >= max_rounds: [cite: 92]
        await send_end_game_post(chat_id, is_pro) [cite: 92]
    else:
        await send_round_post(chat_id, next_round, is_pro) [cite: 88]

# Ігноруємо текстові повідомлення, стікери та емодзі, щоб люди могли вільно спілкуватися
@dp.message()
async def ignore_regular_text(message: types.Message): [cite: 48]
    pass

# ==========================================
# 7. ЕНДПОІНТИ ДЛЯ ВЕБХУКІВ СЕРВЕРА (FASTAPI)
# ==========================================

@app.post("/webhook")
async def telegram_webhook(request: Request): [cite: 28]
    """Приймає вебхуки оновлень від серверів Telegram"""
    try:
        update_data = await request.json()
        update = types.Update(**update_data)
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.error(f"Помилка обробки вебхука Telegram: {e}")
    return {"status": "ok"}

@app.post("/monobank-webhook")
async def monobank_webhook(request: Request): [cite: 16, 99]
    """Обробка інтернет-вебхуків від Monobank про зарахування коштів"""
    try:
        data = await request.json()
        # Спрощена логіка валідації суми від Monobank (сума передається в копійках)
        amount = data.get("amount", 0) / 100 [cite: 16]
        user_id = int(data.get("statementItem", {}).get("comment", 0)) # Передаємо user_id у коментарі
        
        if amount >= 100 and user_id: [cite: 16, 100]
            async with db_pool.acquire() as conn: [cite: 14]
                # Закріплюємо статус PRO назавжди в базі Supabase
                await conn.execute('''
                    INSERT INTO users_pro (user_id, created_at)
                    VALUES ($1, NOW())
                    ON CONFLICT (user_id) DO NOTHING
                ''', user_id) [cite: 101]
                
            # Надсилаємо успішний пост користувачу в приват або чат
            success_text = (
                "Дякую, оплата є!\n"
                "– @user тепер Pro\n"
                "– відкрито 100 раундів\n"
                "– відкрито 10 гравців"
            ) [cite: 104, 124, 125]
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="НОВА ГРА", callback_data="start_pro_game")] [cite: 125]
            ])
            await safe_send(user_id, success_text, markup) [cite: 22]
            
    except Exception as e:
        logger.error(f"Помилка еквайрингу Monobank: {e}")
    return {"status": "ok"}

@app.get("/")
async def root():
    """Health check для моніторингу Render"""
    return {"status": "ok", "service": "100 PHOTO Server API"}
