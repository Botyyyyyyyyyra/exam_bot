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
        async with session.get(url, timeout=10, headers=headers) as resp:
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
    """Возвращает HTML только первого подзадания (до первого ИЛИ)."""
    # Ищем маркер ИЛИ (может быть в разных форматах)
    patterns = [
        r'<center><p><b>ИЛИ</b>',
        r'<b>ИЛИ</b>',
        r'<p><b>ИЛИ</b>',
    ]
    for pattern in patterns:
        match = re.search(pattern, pbody_html, re.IGNORECASE)
        if match:
            return pbody_html[:match.start()]
    return pbody_html  # если ИЛИ не найден, вернуть всё

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
                
                # Ищем блок задания по номеру
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
                # Оставляем только первый подзапрос
                first_html = get_first_subquestion_html(original_html)
                # Извлекаем изображения из первого подзапроса
                first_soup = BeautifulSoup(first_html, "html.parser")
                img_urls = extract_image_urls_from_soup(first_soup, MATH_EGE_TEST_URL)
                
                images_io = []
                for url in img_urls:
                    img_data = await download_image(session, url, referer=MATH_EGE_TEST_URL)
                    if img_data:
                        images_io.append(img_data)
                    else:
                        await debug_send(update, f"Не удалось скачать {url}")
                
                task_text = format_html_to_text(first_html)
                await debug_send(update, f"Текст получен, изображений первого варианта: {len(images_io)}")
                return {"text": task_text, "images": images_io, "image_urls": img_urls}
        except Exception as e:
            logger.error(f"Error: {e}")
            return None

# Обработчики start, subject_selected, level_selected, check_answer, cancel, help_command
# (они такие же, как в предыдущем сообщении, но с улучшенной отправкой изображений)

# Я приведу их здесь с доработкой отправки изображений (отправка ссылок, если файл не загрузился)

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
        user_tasks[user_id] = {
            "correct_answer": correct_answer,
        }
        
        await update.message.reply_text(
            f"📘 *Задание {task_number} (ЕГЭ, Математика)*\n\n{task['text']}",
            parse_mode="Markdown"
        )
        
        if task["images"]:
            await update.message.reply_text("📎 Пояснение к заданию (см. изображения ниже):")
            for idx, img_io in enumerate(task["images"]):
                try:
                    img_io.seek(0)
                    await update.message.reply_document(document=img_io, filename=f"image_{idx+1}.png")
                except Exception as e:
                    await debug_send(update, f"Ошибка отправки документа: {e}")
                    if idx < len(task.get("image_urls", [])):
                        await update.message.reply_text(f"⚠️ Не удалось отправить изображение. Посмотрите его по ссылке: {task['image_urls'][idx]}")
        else:
            # Если изображений нет, но есть ссылки (например, если не скачались)
            if task.get("image_urls"):
                await update.message.reply_text("📎 Изображения к заданию (ссылки):")
                for url in task["image_urls"]:
                    await update.message.reply_text(f"• {url}")
        
        await update.message.reply_text("✍️ Введи свой ответ (только число/набор цифр без пробелов):")
        return WAITING_ANSWER
    else:
        await update.message.reply_text("Пока поддерживается только математика ЕГЭ. /start")
        return ConversationHandler.END

# Остальные функции (start, subject_selected, check_answer, cancel, help_command) без изменений
# (они есть в предыдущем ответе, просто скопируйте их оттуда, чтобы не повторяться)

def main():
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