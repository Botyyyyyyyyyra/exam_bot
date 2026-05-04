import asyncio
import logging
import os
import random
import re
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
DEBUG = True
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG if DEBUG else logging.INFO,
)
logger = logging.getLogger(__name__)

SUBJECT, LEVEL, WAITING_ANSWER = range(3)

# ==================== ДАННЫЕ ПРЕДМЕТОВ ====================
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

MATH_EGE_TEST_ID = 21621325
MATH_EGE_TEST_URL = f"https://mathb-ege.sdamgia.ru/test?id={MATH_EGE_TEST_ID}"
MATH_EGE_ANSWER_URL = f"https://mathb-ege.sdamgia.ru/test?id={MATH_EGE_TEST_ID}&answers=1"

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

def extract_image_urls_from_div(div, base_url: str) -> list:
    urls = []
    for img in div.find_all("img"):
        src = img.get("src")
        if src:
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = base_url + src
            urls.append(src)
    return urls

def format_html_to_text(html_content: str) -> str:
    """Преобразует HTML фрагмент в читаемый текст с отступами для таблиц."""
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Заменяем <br> на \n
    for br in soup.find_all("br"):
        br.replace_with("\n")
    
    # Заменяем <p> на \n\n
    for p in soup.find_all("p"):
        p.insert_before("\n")
        p.insert_after("\n")
        p.unwrap()
    
    # Обработка таблиц
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = []
            for td in tr.find_all(["td", "th"]):
                cell_text = td.get_text(strip=True)
                cells.append(cell_text)
            rows.append(" | ".join(cells))
        table_text = "\n" + "\n".join(rows) + "\n"
        table.replace_with(table_text)
    
    # Удаляем лишние пробелы и пустые строки
    text = soup.get_text()
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

# ==================== ПОЛУЧЕНИЕ ЗАДАНИЯ ====================
async def fetch_math_ege_task(task_number: int, update: Update) -> Optional[Dict]:
    url = MATH_EGE_TEST_URL
    await debug_send(update, f"Загружаем вариант: {url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    await debug_send(update, f"Ошибка HTTP {resp.status}")
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                
                # Ищем div с номером задания
                prob_num_div = None
                for div in soup.find_all("div", class_="prob_num"):
                    if div.get_text(strip=True) == str(task_number):
                        prob_num_div = div
                        break
                if not prob_num_div:
                    await debug_send(update, f"Не найден блок с номером {task_number}")
                    return None
                
                prob_view = prob_num_div.find_next_sibling("div", class_="prob_view")
                if not prob_view:
                    await debug_send(update, f"Не найден prob_view для {task_number}")
                    return None
                
                pbody = prob_view.find("div", class_="pbody")
                if not pbody:
                    await debug_send(update, f"Не найден pbody")
                    return None
                
                # Форматируем текст
                task_text = format_html_to_text(str(pbody))
                
                # Извлекаем изображения
                img_urls = extract_image_urls_from_div(pbody, url)
                images_io = []
                for img_url in img_urls:
                    img_data = await download_image(session, img_url)
                    if img_data:
                        images_io.append(img_data)
                
                await debug_send(update, f"Текст получен, изображений: {len(images_io)}")
                return {"text": task_text, "images": images_io}
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return None

# ==================== ПОЛУЧЕНИЕ ОТВЕТА ====================
async def fetch_math_ege_answer(task_number: int, update: Update) -> Optional[str]:
    """Парсит страницу с ответами и возвращает короткий ответ для задания."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(MATH_EGE_ANSWER_URL, timeout=15) as resp:
                if resp.status != 200:
                    await debug_send(update, f"Ответы не загружены, статус {resp.status}")
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                
                # На странице ответов задания идут в том же порядке, что и в варианте.
                # Ответы находятся в элементах с классом answer (например, <div class="answer">123</div>)
                # или в тегах <span id="answer">.
                # Также может быть таблица с ответами.
                answers = []
                
                # Способ 1: ищем все элементы с классом "answer"
                for ans in soup.find_all(["div", "span"], class_="answer"):
                    text = ans.get_text(strip=True)
                    if text and text not in answers:
                        answers.append(text)
                
                # Способ 2: ищем span с id="answer"
                if not answers:
                    for span in soup.find_all("span", id="answer"):
                        text = span.get_text(strip=True)
                        if text:
                            answers.append(text)
                
                # Способ 3: ищем текст после "Ответ:" внутри элементов
                if not answers:
                    for elem in soup.find_all(text=re.compile(r"Ответ:")):
                        parent = elem.find_parent()
                        if parent:
                            text = parent.get_text(strip=True).replace("Ответ:", "").strip()
                            if text:
                                answers.append(text)
                
                # Если нашли ответы, берём по номеру задания
                if len(answers) >= task_number:
                    correct = answers[task_number-1].strip()
                    await debug_send(update, f"Найден ответ для задания {task_number}: '{correct}'")
                    return correct
                else:
                    await debug_send(update, f"Найдено только {len(answers)} ответов, нужно {task_number}")
                    return None
        except Exception as e:
            logger.error(f"Ошибка получения ответа: {e}")
            return None

# ==================== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (без изменений, но с форматированием) ====================
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

    if subject == "Математика" and level_code == "ege":
        task_number = random.randint(1, 21)
        await debug_send(update, f"Выбрано задание №{task_number}")

        task = await fetch_math_ege_task(task_number, update)
        if not task:
            await update.message.reply_text("Не удалось загрузить задание. Попробуйте позже.")
            return ConversationHandler.END

        answer = await fetch_math_ege_answer(task_number, update)
        if not answer:
            await update.message.reply_text("Не удалось получить правильный ответ. Попробуйте другое задание /start")
            return ConversationHandler.END

        user_id = update.effective_user.id
        user_tasks[user_id] = {
            "task_text": task["text"],
            "correct_answer": answer,
        }

        await update.message.reply_text(
            f"📘 *Задание {task_number} (ЕГЭ, Математика)*\n\n{task['text']}",
            parse_mode="Markdown"
        )
        for img_io in task["images"]:
            try:
                img_io.seek(0)
                await update.message.reply_photo(photo=img_io)
            except Exception as e:
                await debug_send(update, f"Ошибка отправки картинки: {e}")
        await update.message.reply_text("✍️ Введи свой ответ:")
        return WAITING_ANSWER

    else:
        # Для других предметов (старый метод, можно потом доработать)
        subject_id = SUBJECTS[subject][level_code]
        task = await fetch_random_task_old(level_code, subject_id, update)
        if task is None:
            await update.message.reply_text("Не удалось получить задание.")
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
    base_url = BASE_URLS[level]
    async with aiohttp.ClientSession() as session:
        for _ in range(20):
            task_id = random.randint(1, 500000)
            problem_url = f"{base_url}/problem?id={task_id}"
            try:
                async with session.get(problem_url, timeout=10) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, "html.parser")
                        problem_div = soup.find("div", class_="pbody") or soup.find("div", {"id": "problem"})
                        if not problem_div:
                            continue
                        task_text = format_html_to_text(str(problem_div))
                        answer_elem = soup.find("div", class_="answer") or soup.find("div", class_="correct") or soup.find("span", id="answer")
                        if not answer_elem:
                            continue
                        correct_answer = answer_elem.get_text(strip=True)
                        img_urls = extract_image_urls_from_div(problem_div, base_url)
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
    await debug_send(update, f"Ответ пользователя: '{user_answer}', правильный: '{correct_answer}'")
    
    if user_answer == correct_answer:
        await update.message.reply_text("✅ Правильно! Молодец!\n\nХочешь решить ещё одно? /start")
    else:
        await update.message.reply_text(
            f"❌ Неправильно.\nПравильный ответ: `{correct_answer}`\n\nПопробуй ещё раз? /start",
            parse_mode="Markdown"
        )
    user_tasks.pop(user_id, None)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Диалог прерван. Чтобы начать заново, отправь /start")
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Я умею присылать задания с сайта Решу ЕГЭ.\n"
        "Для математики ЕГЭ используется фиксированный вариант с ответами.\n"
        "Напиши /start и выбери предмет и уровень."
    )

def main() -> None:
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        raise ValueError("Токен не задан")
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