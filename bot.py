import asyncio
import logging
import os
import random
from typing import Dict, Optional
from io import BytesIO

import aiohttp
from bs4 import BeautifulSoup
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==================== НАСТРОЙКИ ====================
DEBUG = True          # Включить отладку (вывод в чат)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG if DEBUG else logging.INFO,
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
BASE_URLS = {"ege": "https://ege.sdamgia.ru", "oge": "https://oge.sdamgia.ru"}
user_tasks: Dict[int, Dict] = {}

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def debug_send(update: Update, text: str):
    if DEBUG:
        try:
            await update.message.reply_text(f"🛠 [DEBUG] {text}")
        except:
            pass

async def download_image(session: aiohttp.ClientSession, url: str) -> Optional[BytesIO]:
    try:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200 and 'image' in resp.headers.get('content-type', ''):
                data = await resp.read()
                if len(data) <= 10 * 1024 * 1024:
                    return BytesIO(data)
            return None
    except Exception as e:
        logger.error(f"Image download error: {e}")
        return None

def extract_image_urls(soup: BeautifulSoup, base_url: str) -> list:
    urls = []
    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = base_url + src
            urls.append(src)
    return urls

async def fetch_random_task(level: str, subject_id: int, update: Update) -> Optional[Dict]:
    base_url = BASE_URLS[level]
    async with aiohttp.ClientSession() as session:
        for attempt in range(20):
            task_id = random.randint(1, 500000)
            problem_url = f"{base_url}/problem?id={task_id}"
            await debug_send(update, f"Попытка {attempt+1}: проверяю {problem_url}")
            try:
                async with session.get(problem_url, timeout=10) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        # Логируем первые 500 символов HTML для отладки (не в чат)
                        logger.debug(f"HTML snippet: {html[:500]}")
                        task_data = await parse_problem_page(html, base_url, session, update, problem_url)
                        if task_data:
                            return task_data
                    else:
                        await debug_send(update, f"HTTP {resp.status} для {problem_url}")
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Request error: {e}")
                await debug_send(update, f"Ошибка: {e}")
    return None

async def parse_problem_page(html: str, base_url: str, session: aiohttp.ClientSession, update: Update, problem_url: str) -> Optional[Dict]:
    soup = BeautifulSoup(html, "html.parser")

    # === 1. Текст задания ===
    problem_div = soup.find("div", class_="pbody") or soup.find("div", {"id": "problem"})
    if not problem_div:
        await debug_send(update, "Не найден div с заданием (pbody или problem)")
        return None
    task_text = problem_div.get_text(separator="\n", strip=True)

    # === 2. Правильный ответ (улучшенный поиск) ===
    # Ищем любой элемент, содержащий ответ – часто это div.answer, span.answer, div.correct
    answer_elem = None
    for selector in ["div.answer", "span.answer", "div.correct", "span.correct", ".answer", ".correct"]:
        answer_elem = soup.select_one(selector)
        if answer_elem:
            break
    if not answer_elem:
        # Иногда ответ лежит в теге <div> с текстом "Ответ: ..."
        for div in soup.find_all("div"):
            if div.get_text(strip=True).startswith("Ответ:"):
                answer_elem = div
                break
    if not answer_elem:
        await debug_send(update, f"Не найден ответ на странице {problem_url}")
        return None

    correct_answer = answer_elem.get_text(strip=True)
    # Убираем префикс "Ответ:" если он есть
    if correct_answer.lower().startswith("ответ:"):
        correct_answer = correct_answer[5:].strip()
    # Убираем точки, лишние пробелы
    correct_answer = correct_answer.strip().rstrip('.')

    await debug_send(update, f"Найден ответ: '{correct_answer}' из элемента {answer_elem.name}.{answer_elem.get('class', '')}")

    # === 3. Изображения ===
    img_urls = extract_image_urls(problem_div, base_url)
    images_io = []
    for url in img_urls:
        img = await download_image(session, url)
        if img:
            images_io.append(img)
    await debug_send(update, f"Найдено изображений: {len(images_io)} из {len(img_urls)} URL")

    return {
        "text": task_text,
        "answer": correct_answer,
        "images": images_io,
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

    # Отладочное сообщение
    await debug_send(update, f"Запрашиваю задание (уровень={level_code}, subject_id={subject_id})")

    task = await fetch_random_task(level_code, subject_id, update)
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

    await update.message.reply_text(
        f"Вот задание ({level_name}, {subject}):\n\n{task['text']}"
    )
    for idx, img_io in enumerate(task["images"]):
        try:
            img_io.seek(0)
            await update.message.reply_photo(photo=img_io)
        except Exception as e:
            await debug_send(update, f"Ошибка отправки фото: {e}")
            await update.message.reply_text("⚠️ Изображение не отправилось, но задание продолжается.")
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

    # Отладочный вывод
    await debug_send(update, f"Ответ пользователя: '{user_answer}', правильный ответ: '{correct_answer}'")

    if user_answer.lower() == correct_answer.lower():
        await update.message.reply_text("✅ Правильно! Молодец!\n\nХочешь решить ещё одно? /start")
    else:
        await update.message.reply_text(
            f"❌ Неправильно.\nПравильный ответ: {correct_answer}\n\nПопробуй ещё раз? /start"
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