import os
import logging
import json
import httpx
from fastapi import FastAPI, Request, Response, status
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

# Налаштування логів
logging.basicConfig(level=logging.INFO)

# Токени та константи з Render Environment
TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
# Сюди встав свій токен еквайрингу з кабінету ФОП Монобанку (або додай в Env на Render)
MONO_TOKEN = os.getenv("MONO_TOKEN", "ТВІЙ_MONO_TOKEN") 

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

# --- ЕМУЛЯЦІЯ БАЗИ ДАНИХ (для безкоштовного хостингу збережемо в пам'яті / JSON) ---
# В реальному продакшені краще підключити PostgreSQL
GAMES_DATA = {}  # chat_id: { status: "free/pro", round: X, scores: {user_id: score}, history: [] }
PRO_USERS = set() # Сет з Telegram ID PRO-користувачів

# Функція для отримання імені користувача
def get_user_name(user: types.User) -> str:
    if user.first_name:
        return user.first_name
    return f"@{user.username}" if user.username else f"ID: {user.id}"

# --- ЛОГІКА MONOBANK API ---
async def create_mono_invoice(chat_id: int, user_id: int, amount_uah: int = 100):
    """Створення рахунку в Монобанку"""
    url = "https://api.monobank.ua/api/merchant/invoice/create"
    headers = {"X-Token": MONO_TOKEN}
    
    payload = {
        "amount": amount_uah * 100, # Монобанк приймає в копійках (100 грн = 10000 копійок)
        "ccy": 980, # Код гривні
        "merchantPaymInfo": {
            "reference": f"{chat_id}_{user_id}", # Передаємо метадані для ідентифікації при вебхуці
            "destination": "Активація PRO-версії гри 100 PHOTO"
        },
        "redirectUrl": f"https://t.me/stophotobot",
        "webHookUrl": f"{BASE_URL}/mono-webhook"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return response.json().get("pageUrl") # Посилання на сторінку оплати
        else:
            logging.error(f"Помилка створення інвойсу Моно: {response.text}")
            return None

# --- ВЕБХУК ДЛЯ MONOBANK ---
@app.post("/mono-webhook")
async def mono_webhook(request: Request):
    payload = await request.json()
    logging.info(f"Отримано сповіщення від Monobank: {payload}")
    
    # Монобанк надсилає статус "success" при успішній оплаті
    if payload.get("status") == "success":
        amount = payload.get("amount", 0)
        # Перевірка умови ТЗ: якщо сума менша або дорівнює 99 грн - Pro не активується
        if amount <= 9900:
            return Response(status_code=status.HTTP_200_OK)
            
        reference = payload.get("reference", "")
        if "_" in reference:
            chat_id_str, user_id_str = reference.split("_")
            chat_id = int(chat_id_str)
            user_id = int(user_id_str)
            
            # Закріплюємо статус за користувачем
            PRO_USERS.add(user_id)
            
            # Оновлюємо поточну гру чату до PRO
            if chat_id in GAMES_DATA:
                GAMES_DATA[chat_id]["status"] = "pro"
            
            # Відправляємо пост "ОПЛАТА УСПІШНА"
            text_success = (
                f"Дякую, оплата є!\n\n"
                f"– користувач отримав статус Pro-гравця.\n"
                f"– Нові ігри в чаті тепер до 100.\n"
                f"– Кількість гравців необмежена."
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="[ НОВА ГРА ]", callback_data="new_game_pro")]
            ])
            await bot.send_message(chat_id=chat_id, text=text_success, reply_markup=kb)
            
    return Response(status_code=status.HTTP_200_OK)

# --- ВЕБХУК ДЛЯ TELEGRAM ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    json_str = await request.json()
    update = Update.model_validate(json_str, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "working", "game": "100 PHOTO"}

# --- ЛОГІКА ТЕЛЕГРАМ БОТА ---

# Тригер: Підключення бота в чат (Надсилає правила)
@dp.message(F.new_chat_members)
async def on_bot_join(message: types.Message):
    for member in message.new_chat_members:
        if member.id == bot.id:
            # Текст "ПРАВИЛА ГРИ" з ТЗ
            rules_text = (
                "Вітаємо у грі <a href='https://t.me/100photobot'>100PHOTO</a>!\n\n"
                "Правила гри:\n\n"
                "1. Завдання гравців – фотографувати числа (1, 2, 3...) і надсилати у цей чат.\n\n"
                "2. Безоплатна гра триває 10 раундів, платна – 100 раундів. 1 раунд = 1 фото. За кожне фото гравець отримує 1 бал.\n\n"
                "3. Числа не можна створювати (викладати предметами) або писати самому. Лише фотографувати числа в кімнаті, в магазині, на вулиці тощо.\n\n"
                "4. Не можна повторювати двічі числа з однієї локації (номери сторінок у книзі, номери паркомісць тощо). Локації мають бути різними.\n\n"
                "5. Якщо надіслане фото не відповідає правилам, це фото можна відмінити і почати раунд заново.\n\n"
                "За бажанням, придумайте приз переможцю.\n\nНатхнення!"
            )
            
            # Якщо в чаті є PRO юзер, кнопки інші за ТЗ
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="[ НОВА ГРА ДО 10 ]", callback_data="start_free")],
                [InlineKeyboardButton(text="[ НОВА ГРА ДО 100 ]", callback_data="trigger_pay")],
                [InlineKeyboardButton(text="[ ДОДАТИ ГРАВЦІВ ]", callback_data="trigger_pay")]
            ])
            
            # Надсилаємо Пост 1 (сюди можна додати photo=..., якщо завантажиш картинку в інтернет)
            await message.answer(rules_text, parse_mode="HTML", reply_markup=kb)

# Обробка натискання кнопок
@dp.callback_query()
async def process_callbacks(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    
    # Кнопка: Почати безкоштовну гру до 10
    if callback.data == "start_free":
        # Перевірка кількості учасників (ТЗ: безкоштовно лише для 2 людей)
        chat_member_count = await bot.get_chat_member_count(chat_id)
        if chat_member_count > 2:
            await callback.answer("Безкоштовна гра доступна лише для 2 людей!", show_alert=True)
            # Викликаємо пост оплати
            await show_payment_post(chat_id)
            return
            
        GAMES_DATA[chat_id] = {
            "status": "free",
            "round": 1,
            "scores": {},
            "history": [] # Для скасування раундів
        }
        
        text = "Завдання: 1\n\nРахунок\nГравець 1: 0\nГравець 2: 0\n\nЗнайди і сфотографуй число 1."
        await callback.message.answer(text)
        await callback.answer()

    # Кнопка: Виклик вікна оплати
    elif callback.data == "trigger_pay":
        await show_payment_post(chat_id, user_id)
        await callback.answer()
        
    # Кнопка: Створення платіжного лінку в Монобанку
    elif callback.data == "buy_pro":
        pay_url = await create_mono_invoice(chat_id, user_id, amount_uah=100)
        if pay_url:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 ПЕРЕЙТИ ДО ОПЛАТИ (100 грн)", url=pay_url)]
            ])
            await callback.message.answer("Посилання для оплати готове! Натисніть кнопку нижче:", reply_markup=kb)
        else:
            await callback.message.answer("Сталася помилка при генерації рахунку Монобанку. Спробуйте пізніше.")
        await callback.answer()

async def show_payment_post(chat_id: int, user_id: int):
    text = "Pro-версія гри:\n- безлімітна к-сть гравців\n- гра до 100 раундів назавжди\n- у всіх чатах Pro-гравця"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="[ КУПИТИ PRO-ВЕРСІЮ ]", callback_data="buy_pro")],
        [InlineKeyboardButton(text="[ ПРОДОВЖИТИ ГРУ УДВОХ ]", callback_data="start_free")]
    ])
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)

# Обробник фотографій (Головна механіка гри)
@dp.message(F.photo)
async def handle_game_photo(message: types.Message):
    chat_id = message.chat.id
    
    # Якщо гра в цьому чаті не активна — ігноруємо
    if chat_id not in GAMES_DATA:
        return
        
    game = GAMES_DATA[chat_id]
    user_id = message.from_user.id
    user_name = get_user_name(message.from_user)
    
    # Перевірка ліміту людей під час безкоштовної гри
    if game["status"] == "free":
        chat_member_count = await bot.get_chat_member_count(chat_id)
        if chat_member_count > 2:
            await show_payment_post(chat_id, user_id)
            return

    current_round = game["round"]
    max_rounds = 100 if game["status"] == "pro" else 10
    
    # Зараховуємо бал
    game["scores"][user_name] = game["scores"].get(user_name, 0) + 1
    # Записуємо в історію, хто закрив раунд (для кнопки скасування)
    game["history"].append((user_name, current_round))
    
    # Фінал гри
    if current_round >= max_rounds:
        winner = max(game["scores"], key=game["scores"].get)
        scores_text = "\n".join([f"{u}: {s}" for u, s in game["scores"].items()])
        
        fin_text = f"Переможець: {winner}\n\nРахунок:\n{scores_text}\n\nНе забудь про свій приз!"
        
        # Кнопки залежно від статусу
        if game["status"] == "pro":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"[ ОБНУЛИТИ РАУНД {max_rounds} ]", callback_data="cancel_last")],
                [InlineKeyboardButton(text="[ НОВА ГРА ]", callback_data="start_pro_game")]
            ])
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="[ ОБНУЛИТИ РАУНД 10 ]", callback_data="cancel_last")],
                [InlineKeyboardButton(text="[ НОВА ГРА ДО 10 ]", callback_data="start_free")],
                [InlineKeyboardButton(text="[ НОВА ГРА ДО 100 ]", callback_data="trigger_pay")],
                [InlineKeyboardButton(text="[ ДОДАТИ ГРАВЦІВ ]", callback_data="trigger_pay")]
            ])
        await message.answer(fin_text, reply_markup=kb)
        # Очищуємо гру після фіналу
        GAMES_DATA.pop(chat_id, None)
        return

    # Наступний раунд
    game["round"] += 1
    next_round = game["round"]
    scores_text = "\n".join([f"{u}: {s}" for u, s in game["scores"].items()])
    
    task_text = f"Завдання: {next_round}\n\n{scores_text}\n\nЗнайди і сфотографуй число {next_round}."
    
    # Формуємо стандартні кнопки управління раундом за ТЗ
    if game["status"] == "pro":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"[ ОБНУЛИТИ РАУНД {next_round-1} ]", callback_data="cancel_last")],
            [InlineKeyboardButton(text="[ ПОЧАТИ ЗАНОВО ]", callback_data="new_game_pro")]
        ])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"[ ОБНУЛИТИ РАУНД {next_round-1} ]", callback_data="cancel_last")],
            [InlineKeyboardButton(text="[ НОВА ГРА ДО 10 ]", callback_data="start_free")],
            [InlineKeyboardButton(text="[ НОВА ГРА ДО 100 ]", callback_data="trigger_pay")],
            [InlineKeyboardButton(text="[ ДОДАТИ ГРАВЦІВ ]", callback_data="trigger_pay")]
        ])
        
    await message.answer(task_text, reply_markup=kb)

# Реалізація кнопки [ ОБНУЛИТИ РАУНД ]
@dp.callback_query(F.data == "cancel_last")
async def cancel_last_round(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    if chat_id not in GAMES_DATA or not GAMES_DATA[chat_id]["history"]:
        await callback.answer("Немає раундів для скасування!", show_alert=True)
        return
        
    game = GAMES_DATA[chat_id]
    last_user, last_round = game["history"].pop()
    
    # Забираємо бал
    if last_user in game["scores"] and game["scores"][last_user] > 0:
        game["scores"][last_user] -= 1
        
    game["round"] = last_round
    
    scores_text = "\n".join([f"{u}: {s}" for u, s in game["scores"].items()])
    task_text = f"Раунд скасовано!\n\nЗавдання: {last_round}\n\n{scores_text}\n\nЗнайди і сфотографуй число {last_round}."
    
    await callback.message.answer(task_text)
    await callback.answer("Останній раунд скасовано!")

# Текстові повідомлення повністю ігноруємо за ТЗ
@dp.message()
async def ignore_text(message: types.Message):
    pass

@app.on_event("startup")
async def on_startup():
    if BASE_URL:
        await bot.set_webhook(f"{BASE_URL}/webhook")
