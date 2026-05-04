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
DEBUG = True   # пока оставим, потом выключим
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
BASE_URLS = {
    "ege": "https://ege.sdamgia.ru",
    "oge": "https://oge.sdamgia.ru",
}

# Специально для математики ЕГЭ: фиксированный вариант
MATH_EGE_TEST_ID = 21621325
MATH_EGE_ANSWER_URL = f"https://mathb-ege.sdamgia.ru/test?id={MATH_EGE_TEST_ID}&answers=1"
MATH_EGE_TASK_URL_TEMPLATE = "https://mathb-ege.sdamgia.ru/test?id={}&task_id={}"

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

# ==================== ПАРСИНГ ЗАДАНИЯ ИЗ ФИКСИРОВАННОГО ВАРИАНТА ====================
async def fetch_math_ege_task(task_number: int, update: Update) -> Optional[Dict]:
    """Получает задание номер task_number из варианта MATH_EGE_TEST_ID"""
    url = MATH_EGE_TASK_URL_TEMPLATE.format(MATH_EGE_TEST_ID, task_number)
    await debug_send(update, f"Загружаем задание {task_number}: {url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    await debug_send(update, f"Ошибка HTTP {resp.status}")
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                # Ищем блок с заданием. На странице test?task_id=N основное задание находится в div с class="problem" или "pbody"
                problem_div = soup.find("div", class_="problem") or soup.find("div", class_="pbody")
                if not problem_div:
                    await debug_send(update, "Не найден блок задания")
                    return None
                # Текст
                task_text = problem_div.get_text(separator="\n", strip=True)
                # Изображения
                img_urls = extract_image_urls(problem_div, url)
                images_io = []
                for img_url in img_urls:
                    img_data = await download_image(session, img_url)
                    if img_data:
                        images_io.append(img_data)
                await debug_send(update, f"Текст задания получен, изображений:{len(images_io)}")
                return {"text": task_text, "images": images_io}
        except Exception as e:
            logger.error(f"Ошибка при получении задания {task_number}: {e}")
            return None

async def fetch_math_ege_answer(task_number: int, update: Update) -> Optional[str]:
    """Парсит страницу с ответами варианта, извлекает ответ для задания task_number"""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(MATH_EGE_ANSWER_URL, timeout=15) as resp:
                if resp.status != 200:
                    await debug_send(update, f"Не удалось получить ответы, статус {resp.status}")
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                # Ищем элементы, содержащие ответы. Обычно это div.answer, либо td с ответами.
                # На странице ответов задания нумерованы. Ищем по номеру.
                # Способ: найти все блоки с номером задания, затем рядом ответ.
                # Проще: найти элемент, содержащий текст "Задание № X" и взять следующий блок с ответом.
                tasks = soup.find_all("div", class_="problem")
                if not tasks:
                    # Альтернативный поиск
                    await debug_send(update, "Не найден список заданий на странице ответов")
                    return None
                # Обычно на странице answers=1 задания идут по порядку, но с разметкой.
                # Попробуем найти все просмотром: ищем теги с текстом "Ответ:".
                # В реальности на странице есть строка "Ответ:" и ниже число.
                # Можно найти все элементы, содержащие "Ответ:" и взять следующий за ним span/div.
                for elem in soup.find_all(text=lambda t: t and "Ответ:" in t):
                    parent = elem.find_parent("div", class_="answer") or elem.find_parent("td")
                    if parent:
                        answer_text = parent.get_text(strip=True).replace("Ответ:", "").strip()
                        if answer_text:
                            # Проверяем, что ответ похож на число/слово
                            await debug_send(update, f"Найден ответ: {answer_text}")
                            return answer_text
                # Если не нашли, попробуем достать ответ из таблицы ответов (иногда они в div с class="answers")
                answers_div = soup.find("div", class_="answers")
                if answers_div:
                    # Нужно сопоставить номеру задания – просто берём по индексу
                    all_answers = answers_div.find_all("div", class_="answer")
                    if 1 <= task_number <= len(all_answers):
                        ans = all_answers[task_number-1].get_text(strip=True)
                        await debug_send(update, f"Ответ из таблицы: {ans}")
                        return ans
                await debug_send(update, "Не удалось извлечь ответ со страницы")
                return None
        except Exception as e:
            logger.error(f"Ошибка получения ответа: {e}")
            return None

# ==================== ОБРАБОТЧИКИ ДИАЛОГА ====================
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
    subject = context.user_data.get("subject")
    level_code = LEVELS[level_name]

    # Если предмет математика и уровень ЕГЭ – используем специальный вариант
    if subject == "Математика" and level_code == "ege":
        # Выбираем случайный номер задания от 1 до 21
        task_number = random.randint(1, 21)
        await debug_send(update, f"Выбрано задание №{task_number} из варианта {MATH_EGE_TEST_ID}")

        # Получаем условие и ответ
        task = await fetch_math_ege_task(task_number, update)
        if not task:
            await update.message.reply_text("Не удалось загрузить задание. Попробуйте позже.")
            return ConversationHandler.END

        answer = await fetch_math_ege_answer(task_number, update)
        if not answer:
            await update.message.reply_text("Не удалось получить правильный ответ для проверки. Попробуйте другое задание /start")
            return ConversationHandler.END

        # Сохраняем в память
        user_id = update.effective_user.id
        user_tasks[user_id] = {
            "task_text": task["text"],
            "correct_answer": answer,
        }

        # Отправляем текст задания
        await update.message.reply_text(
            f"Вот задание (ЕГЭ, Математика, вариант {MATH_EGE_TEST_ID}, задание {task_number}):\n\n{task['text']}"
        )
        # Отправляем изображения
        for img_io in task["images"]:
            try:
                img_io.seek(0)
                await update.message.reply_photo(photo=img_io)
            except Exception as e:
                await debug_send(update, f"Ошибка отправки картинки: {e}")
        await update.message.reply_text("Введи свой ответ:")
        return WAITING_ANSWER

    else:
        # Старый метод для других предметов/уровней (случайный перебор problem?id)
        subject_id = SUBJECTS[subject][level_code]
        task = await fetch_random_task_old(level_code, subject_id, update)
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
        await update.message.reply_text(f"Вот задание ({level_name}, {subject}):\n\n{task['text']}")
        for img_io in task.get("images", []):
            try:
                img_io.seek(0)
                await update.message.reply_photo(photo=img_io)
            except:
                pass
        await update.message.reply_text("Введи свой ответ:")
        return WAITING_ANSWER

async def fetch_random_task_old(level: str, subject_id: int, update: Update) -> Optional[Dict]:
    """Старый метод (для других предметов)"""
    base_url = BASE_URLS[level]
    async with aiohttp.ClientSession() as session:
        for _ in range(20):
            task_id = random.randint(1, 500000)
            problem_url = f"{base_url}/problem?id={task_id}"
            await debug_send(update, f"Попытка {_+1}: {problem_url}")
            try:
                async with session.get(problem_url, timeout=10) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, "html.parser")
                        problem_div = soup.find("div", class_="pbody") or soup.find("div", {"id": "problem"})
                        if not problem_div:
                            continue
                        task_text = problem_div.get_text(separator="\n", strip=True)
                        answer_elem = soup.find("div", class_="answer") or soup.find("div", class_="correct") or soup.find("span", id="answer")
                        if not answer_elem:
                            continue
                        correct_answer = answer_elem.get_text(strip=True)
                        # Изображения
                        img_urls = extract_image_urls(problem_div, base_url)
                        images_io = []
                        for url in img_urls:
                            img = await download_image(session, url)
                            if img:
                                images_io.append(img)
                        return {"text": task_text, "answer": correct_answer, "images": images_io}
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(e)
    return None

async def check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_data = user_tasks.get(user_id)
    if not user_data:
        await update.message.reply_text("Что-то пошло не так. Начнём заново? /start")
        return ConversationHandler.END

    user_answer = update.message.text.strip()
    correct_answer = user_data["correct_answer"]
    await debug_send(update, f"Пользователь: '{user_answer}', Правильный: '{correct_answer}'")
    if user_answer.lower() == correct_answer.lower():
        await update.message.reply_text("✅ Правильно! Молодец!\n\nХочешь решить ещё одно? /start")
    else:
        await update.message.reply_text(f"❌ Неправильно.\nПравильный ответ: {correct_answer}\n\nПопробуй ещё раз? /start")
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
        "Для математики ЕГЭ используется фиксированный вариант с ответами.\n"
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