import os
import logging
import asyncpg

# Налаштування логування
logger = logging.getLogger(__name__)

async def init_db():
    """
    Ініціалізація підключення до бази даних Supabase.
    Використовує чистий DATABASE_URL зі змінних оточення без примусових замін.
    """
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url:
        logger.error("КРИТИЧНА ПОМИЛКА: DATABASE_URL не знайдено в змінних оточення!")
        return None

    try:
        # Підключаємося напряму за тим рядком, який вказано на Render
        conn = await asyncpg.connect(database_url)
        logger.info("База даних успішно підключена!")
        return conn
    except Exception as e:
        logger.error(f"Не вдалося підключитися до бази даних: {e}")
        return None
