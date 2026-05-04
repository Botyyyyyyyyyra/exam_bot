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
    "Физика": {"ege": 3, "oge": 3},
}
LEVELS = {"ЕГЭ": "ege", "ОГЭ": "oge"}
BASE_URLS = {"ege": "https://ege.sdamgia.ru", "oge": "https://oge.sdamgia.ru"}

MATH_EGE_TEST_ID = 21621325
MATH_EGE_TEST_URL = f"https://mathb-ege.sdamgia.ru/test?id={MATH_EGE_TEST_ID}"

CORRECT_ANSWERS = {
    1: "7", 2: "3142", 3: "22", 4: "25", 5: "0.2", 6: "236", 7: "4321", 8: "24", 9: "6",
    10: "1500", 11: "24500", 12: "12", 13: "270", 14: "24.7", 15: "297", 16: "4", 17: "5",
    18: "4321", 19: "222", 20: "4", 21: "6",
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
        async with session.get(url, timeout=15, headers=headers) as resp:
            if resp.status == 200:
                content_type = resp.headers.get('content-type', '')
                if 'image' in content_type:
                    data = await resp.read()
                    if len(data) <= 10 * 1024 * 1024:
                        return BytesIO(data)
            return None
    except Exception as e:
        logger.error(f"Image download error for {url}: {e}")
        return None

def extract_image_urls_from_html(html: str, base_url: str) -> list:
    """Извлекает все URL изображений из фрагмента HTML."""
    soup = BeautifulSoup(html, "html.parser")
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
    """Возвращает HTML только первого подзадания (до первого ИЛИ)."""
    # Ищем любой маркер "ИЛИ" в жирном начертании
    patterns = [
        r'<center><p><b>ИЛИ</b>',
        r'<p><b>ИЛИ</b>',
        r'<b>ИЛИ</b>',
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

async def fetch_math_ege_task(task_number: int, update: Update) -> Optional[Dict]:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(MATH_EGE_TEST_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                # Ищем блок задания
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
                # Извлекаем текст
                task_text = format_html_to_text(first_html)
                # Извлекаем URL изображений из первого подзадания
                img_urls = extract_image_urls_from_html(first_html, MATH_EGE_TEST_URL)
                images_io = []
                for url in img_urls:
                    img_data = await download_image(session, url, referer=MATH_EGE_TEST_URL)
                    if img_data:
                        images_io.append(img_data)
                    else:
                        await debug_send(update, f"Не удалось скачать {url}")
                await debug_send(update, f"Текст получен, изображений первого варианта: {len(images_io)} из {len(img_urls)}")
                return {"text": task_text, "images": images_io}
        except Exception as e:
            logger.error(f"Error: {e}")
            return None

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
        correct_answer = CORRECT_ANSWERS.get(task_number)
        if not correct_answer:
            await update.message.reply_text("Нет правильного ответа для этого задания. Попробуйте другое /start")
            return ConversationHandler.END
        user_id = update.effective_user.id
        user_tasks[user_id] = {"correct_answer": correct_answer}
        await update.message.reply_text(
            f"📘 *Задание {task_number} (ЕГЭ, Математика)*\n\n{task['text']}",
            parse_mode="Markdown"
        )
        # Отправляем изображения как фото
        if task["images"]:
            await update.message.reply_text("📎 Пояснение к заданию (см. изображения ниже):")
            for idx, img_io in enumerate(task["images"]):
                try:
                    img_io.seek(0)
                    await update.message.reply_photo(photo=img_io, caption=f"Рисунок {idx+1}" if len(task["images"])>1 else "")
                except Exception as e:
                    logger.error(f"Ошибка отправки фото: {e}")
                    # Повторная попытка с другим методом (отправить как документ, но без ссылки)
                    try:
                        img_io.seek(0)
                        await update.message.reply_document(document=img_io, filename=f"image_{idx+1}.png")
                        await update.message.reply_text("⚠️ Изображение отправлено в виде файла, так как не удалось отправить как фото.")
                    except Exception as e2:
                        await debug_send(update, f"Не удалось отправить изображение: {e2}")
        await update.message.reply_text("✍️ Введи свой ответ (только число/набор цифр без пробелов):")
        return WAITING_ANSWER
    else:
        await update.message.reply_text("Пока поддерживается только математика ЕГЭ. /start")
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
        "Я присылаю задания из варианта №21621325 (математика ЕГЭ, база).\n"
        "Напиши /start и выбери Математика → ЕГЭ.\n"
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