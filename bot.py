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
    format="%(asctime)s - %name)s - %(levelname)s - %(message)s",
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

# Правильные ответы для варианта 21621325 (вручную, по порядку заданий 1..21)
# Взяты с официального сайта (https://mathb-ege.sdamgia.ru/test?id=21621325&answers=1)
CORRECT_ANSWERS = {
    1: "7",          # шоколадки
    2: "3142",       # соответствие величин (или 3142, но уточнить)
    3: "22",         # наибольшая среднемесячная температура
    4: "25",         # работа постоянного тока
    5: "0.2",        # вероятность (дробь)
    6: "236",        # экскурсии (набор номеров 2,3,6)
    7: "4321",       # производная (или 4321, уточнить)
    8: "24",         # печенье: утверждения 2 и 4
    9: "6",          # площадь озера
    10: "1500",      # участок минус дом
    11: "24500",     # объём детали
    12: "12",        # медиана
    13: "270",       # объём конуса
    14: "24.7",      # значение выражения
    15: "297",       # книга со скидкой
    16: "4",         # значение выражения
    17: "5",         # корень уравнения
    18: "4321",      # соответствие точек и чисел
    19: "222",       # трёхзначное число с чётными цифрами
    20: "4",         # встреча (расстояние)
    21: "6",         # верных ответов
}
user_tasks: Dict[int, Dict] = {}

async def debug_send(update: Update, text: str):
    if DEBUG:
        try:
            await update.message.reply_text(f"🛠 [DEBUG] {text}")
        except:
            pass

async def download_image(session: aiohttp.ClientSession, url: str) -> Optional[BytesIO]:
    try:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                content_type = resp.headers.get('content-type', '')
                if 'image' not in content_type:
                    return None
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
    """Преобразует HTML в читаемый текст, поддерживая таблицы, списки и переносы."""
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Заменяем <br> на \n
    for br in soup.find_all("br"):
        br.replace_with("\n")
    
    # Параграфы
    for p in soup.find_all("p"):
        p.insert_before("\n")
        p.insert_after("\n")
        p.unwrap()
    
    # Таблицы: преобразуем в моноширинный блок
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = []
            for td in tr.find_all(["td", "th"]):
                cell_text = td.get_text(strip=True)
                # Обрезаем длинные ячейки до 20 символов, чтобы таблица не растягивалась
                if len(cell_text) > 25:
                    cell_text = cell_text[:22] + ".."
                cells.append(cell_text)
            rows.append("\t| ".join(cells))
        table_text = "\n\t" + "\n\t".join(rows) + "\n"
        table.replace_with(table_text)
    
    # Убираем лишние пустые строки
    text = soup.get_text()
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

async def fetch_math_ege_task(task_number: int, update: Update) -> Optional[Dict]:
    url = MATH_EGE_TEST_URL
    await debug_send(update, f"Загружаем вариант")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                
                # Ищем блок с заданием по номеру
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
                
                task_text = format_html_to_text(str(pbody))
                img_urls = extract_image_urls_from_div(pbody, url)
                images_io = []
                for img_url in img_urls:
                    img_data = await download_image(session, img_url)
                    if img_data:
                        images_io.append(img_data)
                
                await debug_send(update, f"Текст получен, изображений: {len(images_io)}")
                return {"text": task_text, "images": images_io}
        except Exception as e:
            logger.error(f"Error: {e}")
            return None

# ==================== ОСНОВНАЯ ЛОГИКА ====================
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
        user_tasks[user_id] = {
            "task_text": task["text"],
            "correct_answer": correct_answer,
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
                # Пробуем отправить как документ
                try:
                    img_io.seek(0)
                    await update.message.reply_document(document=img_io, filename="image.png")
                except Exception as e2:
                    await debug_send(update, f"Не удалось отправить изображение ({e2})")
        await update.message.reply_text("✍️ Введи свой ответ:")
        return WAITING_ANSWER
    else:
        # Для других предметов – старый метод (можно оставить или удалить)
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
        "Напиши /start и выбери Математика → ЕГЭ."
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