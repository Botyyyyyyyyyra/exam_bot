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

DEBUG = True
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG if DEBUG else logging.INFO,
)
logger = logging.getLogger(__name__)

SUBJECT, LEVEL, WAITING_ANSWER = range(3)

SUBJECTS = {
    "Математика": {"ege": 2, "oge": 2},
    "Русский язык": {"ege": 1, "oge": 1},
}
LEVELS = {"ЕГЭ": "ege", "ОГЭ": "oge"}

# Базовая структура для парсинга
# Для математики ЕГЭ используем mathb-ege.sdamgia.ru
# Для математики ОГЭ используем math-oge.sdamgia.ru
# Для русского – позже

# Настройки для математики ЕГЭ (базовый уровень)
MATH_EGE_TEST_ID = 21621325
MATH_EGE_TEST_URL = f"https://mathb-ege.sdamgia.ru/test?id={MATH_EGE_TEST_ID}"

# Настройки для математики ОГЭ
MATH_OGE_TEST_ID = 79386761
MATH_OGE_TEST_URL = f"https://math-oge.sdamgia.ru/test?id={MATH_OGE_TEST_ID}"

# Правильные ответы для ЕГЭ (1–21)
CORRECT_ANSWERS_EGE = {
    1: "7", 2: "3142", 3: "22", 4: "25", 5: "0.2", 6: "236", 7: "4321", 8: "24", 9: "6",
    10: "1500", 11: "24500", 12: "12", 13: "270", 14: "24.7", 15: "297", 16: "4", 17: "5",
    18: "4321", 19: "222", 20: "4", 21: "6",
}

# Правильные ответы для ОГЭ (1–19)
CORRECT_ANSWERS_OGE = {
    1: "213", 2: "56", 3: "40", 4: "168", 5: "1134", 6: "1.5", 7: "2", 8: "64", 9: "0.5",
    10: "0.9", 11: "132", 12: "15", 13: "2", 14: "155", 15: "112", 16: "15", 17: "8",
    18: "14", 19: "123",
}

user_tasks: Dict[int, Dict] = {}

async def debug_send(update: Update, text: str):
    if DEBUG:
        try:
            await update.message.reply_text(f"🛠 [DEBUG] {text}")
        except:
            pass

async def download_image(session: aiohttp.ClientSession, url: str, referer: str) -> Optional[BytesIO]:
    headers = {
        "Referer": referer,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        async with session.get(url, timeout=10, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.read()
                if len(data) <= 10 * 1024 * 1024:
                    return BytesIO(data)
            return None
    except Exception as e:
        logger.error(f"Image download error: {e}")
        return None

def extract_image_urls_from_soup(soup, base_url: str) -> list:
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

def get_first_subquestion_html(pbody_html: str) -> str:
    patterns = [
        r'<center><p><b>ИЛИ</b>',
        r'<b>ИЛИ</b>',
        r'<p><b>ИЛИ</b>',
    ]
    for pattern in patterns:
        match = re.search(pattern, pbody_html, re.IGNORECASE)
        if match:
            return pbody_html[:match.start()]
    return pbody_html

def format_html_to_text(html_content: str) -> str:
    soup = BeautifulSoup(html_content, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all("p"):
        p.insert_before("\n")
        p.insert_after("\n")
        p.unwrap()
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True)[:25] for td in tr.find_all(["td", "th"])]
            rows.append("\t| ".join(cells))
        table.replace_with("\n\t" + "\n\t".join(rows) + "\n")
    text = soup.get_text()
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

async def fetch_math_task(test_url: str, task_number: int, update: Update) -> Optional[Dict]:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(test_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                prob_num_div = None
                for div in soup.find_all("div", class_="prob_num"):
                    if div.get_text(strip=True) == str(task_number):
                        prob_num_div = div
                        break
                if not prob_num_div:
                    await debug_send(update, f"Не найден номер {task_number}")
                    return None
                prob_view = prob_num_div.find_next_sibling("div", class_="prob_view")
                if not prob_view:
                    return None
                pbody = prob_view.find("div", class_="pbody")
                if not pbody:
                    return None
                original_html = str(pbody)
                first_html = get_first_subquestion_html(original_html)
                first_soup = BeautifulSoup(first_html, "html.parser")
                img_urls = extract_image_urls_from_soup(first_soup, test_url)
                images_io = []
                for url in img_urls:
                    img_data = await download_image(session, url, referer=test_url)
                    if img_data:
                        images_io.append(img_data)
                    else:
                        await debug_send(update, f"Не удалось скачать {url}")
                task_text = format_html_to_text(first_html)
                await debug_send(update, f"Текст получен, изображений: {len(images_io)} из {len(img_urls)}")
                return {"text": task_text, "images": images_io, "image_urls": img_urls}
        except Exception as e:
            logger.error(f"Error: {e}")
            return None

async def send_image(update: Update, image_bytes: BytesIO) -> bool:
    image_bytes.seek(0)
    header = image_bytes.read(10)
    image_bytes.seek(0)
    is_svg = (header.startswith(b'<svg') or b'<svg' in header)
    if is_svg:
        await update.message.reply_document(document=image_bytes, filename="image.svg")
        await update.message.reply_text("⚠️ Изображение отправлено как файл (SVG).")
        return False
    try:
        await update.message.reply_photo(photo=image_bytes)
        return True
    except Exception as e:
        logger.error(f"Photo send failed: {e}")
        image_bytes.seek(0)
        await update.message.reply_document(document=image_bytes, filename="image.png")
        await update.message.reply_text("⚠️ Изображение отправлено как файл.")
        return False

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

    # Обработка математики
    if subject == "Математика":
        if level_code == "ege":
            # ЕГЭ по математике (база)
            task_number = random.randint(1, 21)
            task_url = MATH_EGE_TEST_URL
            correct_answers = CORRECT_ANSWERS_EGE
            await debug_send(update, f"Выбрано задание №{task_number} (ЕГЭ)")
        elif level_code == "oge":
            # ОГЭ по математике
            task_number = random.randint(1, 19)   # только тестовая часть
            task_url = MATH_OGE_TEST_URL
            correct_answers = CORRECT_ANSWERS_OGE
            await debug_send(update, f"Выбрано задание №{task_number} (ОГЭ)")
        else:
            await update.message.reply_text("Неизвестный уровень.")
            return ConversationHandler.END

        task = await fetch_math_task(task_url, task_number, update)
        if not task:
            await update.message.reply_text("Не удалось загрузить задание. Попробуйте позже.")
            return ConversationHandler.END

        correct_answer = correct_answers.get(task_number)
        if not correct_answer:
            await update.message.reply_text("Нет правильного ответа для этого задания. Попробуйте другое /start")
            return ConversationHandler.END

        user_id = update.effective_user.id
        user_tasks[user_id] = {"correct_answer": correct_answer}

        await update.message.reply_text(
            f"📘 *Задание {task_number} ({subject}, {level_name})*\n\n{task['text']}",
            parse_mode="Markdown"
        )
        if task["images"]:
            await update.message.reply_text("📎 Пояснение к заданию (см. изображения ниже):")
            for img_io in task["images"]:
                await send_image(update, img_io)
        elif task.get("image_urls"):
            await update.message.reply_text("📎 Изображения к заданию (ссылки):")
            for url in task["image_urls"]:
                await update.message.reply_text(f"• {url}")

        await update.message.reply_text("✍️ Введи свой ответ (только число/набор цифр без пробелов):")
        return WAITING_ANSWER

    else:
        # Русский язык (пока не реализован)
        await update.message.reply_text("Предмет пока не поддерживается. Выберите Математику.")
        return ConversationHandler.END

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
        "Я присылаю задания из вариантов:\n"
        "- Математика ЕГЭ (база) №21621325 (задания 1–21)\n"
        "- Математика ОГЭ №79386761 (задания 1–19)\n"
        "Напиши /start и выбери предмет и уровень.\n"
        "В ответ вводи число или комбинацию цифр без пробелов."
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