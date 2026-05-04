import asyncio
import logging
import os
import random
from typing import Dict, Optional

import aiohttp
from bs4 import BeautifulSoup
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==================== СОСТОЯНИЯ ДИАЛОГА ====================
SUBJECT, LEVEL, WAITING_ANSWER = range(3)

# ==================== ДАННЫЕ ПРЕДМЕТОВ И УРОВНЕЙ ====================
SUBJECTS = {
    "Математика": {"ege": 2, "oge": 2},
    "Русский язык": {"ege": 1, "oge": 1},
    "Физика": {"ege": 3, "oge": 3},
}

LEVELS = {"ЕГЭ": "ege", "ОГЭ": "oge"}

BASE_URLS = {
    "ege": "https://ege.sdamgia.ru",
    "oge": "https://oge.sdamgia.ru",
}

user_tasks: Dict[int, Dict] = {}

# ==================== ФУНКЦИИ ПАРСИНГА И ИЗОБРАЖЕНИЙ ====================
async def download_image(session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
    """Скачивает изображение по URL и возвращает байты."""
    try:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                return await resp.read()
            else:
                logger.warning(f"Не удалось загрузить изображение {url}, статус {resp.status}")
                return None
    except Exception as e:
        logger.error(f"Ошибка при загрузке изображения {url}: {e}")
        return None


def extract_images(soup: BeautifulSoup, base_url: str) -> list:
    """Извлекает все ссылки на изображения из условия задания."""
    img_urls = []
    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = base_url + src
            img_urls.append(src)
    return img_urls


async def fetch_random_task(level: str, subject_id: int) -> Optional[Dict]:
    """
    Получает случайное задание, включая текст и изображения.
    """
    base_url = BASE_URLS[level]
    async with aiohttp.ClientSession() as session:
        for _ in range(20):
            task_id = random.randint(1, 500000)
            problem_url = f"{base_url}/problem?id={task_id}"
            try:
                async with session.get(problem_url, timeout=10) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        task_data = await parse_problem_page(html, base_url, session)
                        if task_data:
                            return task_data
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Ошибка при запросе {problem_url}: {e}")
                continue
    return None


async def parse_problem_page(html: str, base_url: str, session: aiohttp.ClientSession) -> Optional[Dict]:
    """
    Парсит HTML, извлекает текст задания, ответ и изображения.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Текст задания
    problem_div = soup.find("div", class_="pbody") or soup.find("div", {"id": "problem"})
    if not problem_div:
        return None
    task_text = problem_div.get_text(separator="\n", strip=True)

    # Правильный ответ
    answer_elem = (
        soup.find("div", class_="answer")
        or soup.find("div", class_="correct")
        or soup.find("span", id="answer")
    )
    if not answer_elem:
        return None
    correct_answer = answer_elem.get_text(strip=True)

    # Изображения
    img_urls = extract_images(problem_div, base_url)
    images_bytes = []
    for url in img_urls:
        img_data = await download_image(session, url)
        if img_data:
            images_bytes.append(img_data)

    return {
        "text": task_text,
        "answer": correct_answer,
        "images": images_bytes,
    }


# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reply_keyboard = [[subject] for subject in SUBJECTS.keys()]
    await update.message.reply_text(
        "Привет! Я помогу тебе подготовиться к ЕГЭ/ОГЭ.\nВыбери предмет:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
    )
    return SUBJECT


async def subject_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    subject = update.message.text
    if subject not in SUBJECTS:
        await update.message.reply_text("Пожалуйста, выбери предмет из списка.")
        return SUBJECT

    context.user_data["subject"] = subject
    reply_keyboard = [[level] for level in LEVELS.keys()]
    await update.message.reply_text(
        f"Отлично! Предмет: {subject}\nТеперь выбери уровень:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
    )
    return LEVEL


async def level_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    level_name = update.message.text
    if level_name not in LEVELS:
        await update.message.reply_text("Пожалуйста, выбери уровень из списка.")
        return LEVEL

    level_code = LEVELS[level_name]
    subject = context.user_data["subject"]
    subject_id = SUBJECTS[subject][level_code]

    task = await fetch_random_task(level_code, subject_id)
    if task is None:
        await update.message.reply_text(
            "Не удалось получить задание. Попробуй позже.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    user_id = update.effective_user.id
    user_tasks[user_id] = {
        "task_text": task["text"],
        "correct_answer": task["answer"],
    }

    # Отправляем текст задания
    await update.message.reply_text(
        f"Вот задание ({level_name}, {subject}):\n\n{task['text']}",
    )

    # Отправляем изображения (если есть)
    for img_bytes in task["images"]:
        try:
            await update.message.reply_photo(photo=img_bytes)
        except Exception as e:
            logger.error(f"Ошибка отправки изображения: {e}")
            await update.message.reply_text("⚠️ Не удалось отправить изображение к заданию.")

    await update.message.reply_text("Введи свой ответ:")
    return WAITING_ANSWER


async def check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_data = user_tasks.get(user_id)

    if not user_data:
        await update.message.reply_text("Что-то пошло не так. Начнём заново? /start")
        return ConversationHandler.END

    user_answer = update.message.text.strip()
    correct_answer = user_data["correct_answer"]

    if user_answer.lower() == correct_answer.lower():
        await update.message.reply_text("✅ Правильно! Молодец!\n\nХочешь решить ещё одно? /start")
    else:
        await update.message.reply_text(
            f"❌ Неправильно.\nПравильный ответ: {correct_answer}\n\n"
            "Попробуй ещё раз? /start"
        )

    user_tasks.pop(user_id, None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Диалог прерван. Чтобы начать заново, отправь /start",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Я умею присылать случайные задания с сайта Решу ЕГЭ/ОГЭ.\n"
        "Просто напиши /start и следуй инструкциям."
    )


# ==================== ЗАПУСК ====================
def main() -> None:
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        raise ValueError("Переменная окружения TELEGRAM_TOKEN не установлена!")

    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, subject_selected)],
            LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, level_selected)],
            WAITING_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_answer)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()