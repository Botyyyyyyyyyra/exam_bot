import asyncio
import logging
import os
import random
import re
from typing import Dict, Optional, List
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

SUBJECT, LEVEL, WAITING_ANSWER, ACTION = range(4)

SUBJECTS = {
    "Математика": {"ege": 2, "oge": 2},
    "Русский язык": {"ege": 1, "oge": 1},
}
LEVELS = {"ЕГЭ": "ege", "ОГЭ": "oge"}

# ===== Математика =====
MATH_EGE_TEST_ID = 21621325
MATH_EGE_TEST_URL = f"https://mathb-ege.sdamgia.ru/test?id={MATH_EGE_TEST_ID}"
MATH_OGE_TEST_ID = 79386761
MATH_OGE_TEST_URL = f"https://math-oge.sdamgia.ru/test?id={MATH_OGE_TEST_ID}"

CORRECT_ANSWERS_EGE = {
    1: "7", 2: "3142", 3: "22", 4: "25", 5: "0.2", 6: "236", 7: "4321", 8: "24", 9: "6",
    10: "1500", 11: "24500", 12: "12", 13: "270", 14: "24.7", 15: "297", 16: "4", 17: "5",
    18: "4321", 19: "222", 20: "4", 21: "6",
}
CORRECT_ANSWERS_OGE = {
    1: "213", 2: "56", 3: "40", 4: "168", 5: "1134", 6: "1.5", 7: "2", 8: "64", 9: "0.5",
    10: "0.9", 11: "132", 12: "15", 13: "2", 14: "155", 15: "112", 16: "15", 17: "8",
    18: "14", 19: "123",
}

# ===== Русский язык =====
RUS_EGE_TEST_ID = 55373666
RUS_EGE_TEST_URL = f"https://rus-ege.sdamgia.ru/test?id={RUS_EGE_TEST_ID}"
RUS_EGE_CORRECT_ANSWERS = {
    1: "таккак", 2: "24", 3: "345", 4: "125", 5: "двойственное",
    6: "черном", 7: "разожжёт", 8: "81635", 9: "23", 10: "45",
    11: "15", 12: "235", 13: "145", 14: "124", 15: "13",
    16: "25", 17: "1234567", 18: "2346", 19: "2", 20: "3457",
    21: "34", 22: "25684", 23: "235", 24: "145", 25: "вовесьдух",
    26: "31",
}

user_tasks: Dict[int, Dict] = {}

# ---------- Вспомогательные функции ----------
async def debug_send(update: Update, text: str):
    if DEBUG:
        try:
            await update.message.reply_text(f"🛠 [DEBUG] {text}")
        except:
            pass

async def delete_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет сообщение пользователя, если оно не является командой."""
    if update.message and not update.message.text.startswith('/'):
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
        except Exception as e:
            logger.debug(f"Не удалось удалить сообщение пользователя: {e}")

async def delete_bot_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет все сохранённые сообщения бота."""
    bot_messages = context.user_data.get("bot_messages", [])
    for msg_id in bot_messages:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
        except Exception as e:
            logger.debug(f"Не удалось удалить сообщение {msg_id}: {e}")
    context.user_data["bot_messages"] = []

async def send_and_track(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs) -> None:
    """Отправляет сообщение и сохраняет его ID."""
    msg = await update.message.reply_text(text, **kwargs)
    bot_messages = context.user_data.get("bot_messages", [])
    bot_messages.append(msg.message_id)
    context.user_data["bot_messages"] = bot_messages

async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, prefix: str = ""):
    """Разбивает длинный текст на части и сохраняет ID отправленных сообщений."""
    MAX_LEN = 4096
    bot_messages = context.user_data.get("bot_messages", [])
    if len(text) <= MAX_LEN:
        msg = await update.message.reply_text(f"{prefix}\n\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n{text}", parse_mode="Markdown")
        bot_messages.append(msg.message_id)
        context.user_data["bot_messages"] = bot_messages
        return
    parts = []
    current = ""
    for line in text.split('\n'):
        if len(current) + len(line) + 1 > MAX_LEN:
            parts.append(current)
            current = line
        else:
            current += "\n" + line if current else line
    if current:
        parts.append(current)
    for i, part in enumerate(parts, 1):
        header = f"{prefix} (часть {i}/{len(parts)})\n\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n" if len(parts) > 1 else f"{prefix}\n\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        msg = await update.message.reply_text(f"{header}{part}", parse_mode="Markdown")
        bot_messages.append(msg.message_id)
    context.user_data["bot_messages"] = bot_messages

def clean_task_soup(soup):
    """Удаляет из супа все элементы, которые не относятся к самому заданию (условию)."""
    # 1. Удаляем элементы, скрытые через style="display:none"
    for elem in soup.find_all(attrs={"style": re.compile(r"display:\s*none", re.I)}):
        elem.decompose()
    # 2. Удаляем элементы с классами, которые обычно содержат пояснения/правила
    classes_to_remove = ["align-left", "nocopy", "expand", "nodraw", "minor", 
                         "Test-TimerBox", "right_switch", "skipped_probs", "DeskList", 
                         "prob_nums", "new_header", "col_name", "wrap_flex_table", "wrap_flex_table_col"]
    for cls in classes_to_remove:
        for elem in soup.find_all(class_=cls):
            elem.decompose()
    # 3. Удаляем теги details (в них прячут правила и пояснения)
    for details in soup.find_all("details"):
        details.decompose()
    # 4. Удаляем блоки с правилами (часто имеют класс "rus_rule" или находятся внутри div с border)
    for rule in soup.find_all("div", class_=re.compile(r"rus_rule|handbook|example|prob_view")):
        # Не удаляем сам pbody, который является родительским
        if rule.get("class") and "prob_view" not in rule.get("class", []):
            rule.decompose()
    # 5. Удаляем любые iframe, скрипты, стили, формы
    for tag in soup(["script", "style", "iframe", "form", "input", "button", "select", "textarea"]):
        tag.decompose()
    # 6. Удаляем пустые элементы
    for elem in soup.find_all():
        if not elem.get_text(strip=True) and not elem.find_all(recursive=False):
            elem.decompose()
    return soup

def get_image_extension(data: bytes) -> str:
    if data[:4] == b'\x89PNG':
        return '.png'
    if data[:2] == b'\xff\xd8':
        return '.jpg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    if data.startswith(b'<svg') or b'<svg' in data[:100]:
        return '.svg'
    return '.bin'

async def download_image(session: aiohttp.ClientSession, url: str, referer: str) -> Optional[BytesIO]:
    headers = {
        "Referer": referer,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        async with session.get(url, timeout=15, headers=headers) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
            if not data or len(data) > 10 * 1024 * 1024:
                return None
            return BytesIO(data)
    except Exception as e:
        logger.error(f"Image download error: {e}")
        return None

async def send_image(update: Update, context: ContextTypes.DEFAULT_TYPE, image_bytes: BytesIO) -> bool:
    """Отправляет изображение и сохраняет его ID."""
    try:
        image_bytes.seek(0)
        data = image_bytes.read()
        if not data:
            return False
        ext = get_image_extension(data)
        filename = f"image{ext}"
        image_bytes.seek(0)
        msg = await update.message.reply_document(document=image_bytes, filename=filename)
        bot_messages = context.user_data.get("bot_messages", [])
        bot_messages.append(msg.message_id)
        context.user_data["bot_messages"] = bot_messages
        return True
    except Exception as e:
        logger.error(f"Send document failed: {e}")
        return False

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

# ---------- Парсинг заданий ----------
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
                    return None
                prob_view = prob_num_div.find_next_sibling("div", class_="prob_view")
                if not prob_view:
                    return None
                pbody = prob_view.find("div", class_="pbody")
                if not pbody:
                    return None
                # Очистка от лишних элементов
                cleaned_pbody = clean_task_soup(pbody)
                original_html = str(cleaned_pbody)
                first_html = get_first_subquestion_html(original_html)
                first_soup = BeautifulSoup(first_html, "html.parser")
                img_urls = extract_image_urls_from_soup(first_soup, test_url)
                images_io = []
                for url in img_urls:
                    img_data = await download_image(session, url, referer=test_url)
                    if img_data:
                        images_io.append(img_data)
                task_text = format_html_to_text(first_html)
                return {"text": task_text, "images": images_io}
        except Exception as e:
            logger.error(f"Math error: {e}")
            return None

async def fetch_russian_task(test_url: str, task_number: int, update: Update) -> Optional[Dict]:
    await debug_send(update, f"fetch_russian_task: начало загрузки задания №{task_number}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(test_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status != 200:
                    await debug_send(update, f"Ошибка HTTP {resp.status}")
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
                    await debug_send(update, "Не найден prob_view")
                    return None
                pbody = prob_view.find("div", class_="pbody")
                if not pbody:
                    await debug_send(update, "Не найден pbody")
                    return None
                # Очистка от лишних элементов
                cleaned_pbody = clean_task_soup(pbody)
                original_html = str(cleaned_pbody)
                first_html = get_first_subquestion_html(original_html)
                first_soup = BeautifulSoup(first_html, "html.parser")
                img_urls = extract_image_urls_from_soup(first_soup, test_url)
                await debug_send(update, f"Найдено URL изображений: {len(img_urls)} (после очистки)")
                images_io = []
                for url in img_urls:
                    img_data = await download_image(session, url, referer=test_url)
                    if img_data:
                        images_io.append(img_data)
                task_text = format_html_to_text(first_html)
                await debug_send(update, f"Русский: текст получен, изображений: {len(images_io)} из {len(img_urls)}")
                return {"text": task_text, "images": images_io}
        except Exception as e:
            logger.error(f"Russian error: {e}")
            await debug_send(update, f"Исключение в fetch_russian_task: {e}")
            return None

# ---------- Обработчики диалога ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await delete_bot_messages(update, context)
    await delete_user_message(update, context)
    reply_keyboard = [[subject] for subject in SUBJECTS.keys()]
    msg = await update.message.reply_text(
        "📚 *Добро пожаловать в бот для подготовки к ЕГЭ/ОГЭ!*\n\n"
        "Я умею присылать реальные задания с сайта Решу ЕГЭ/ОГЭ.\n"
        "Выбери предмет:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
        parse_mode="Markdown"
    )
    context.user_data["bot_messages"] = [msg.message_id]
    return SUBJECT

async def subject_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await delete_user_message(update, context)
    text = update.message.text
    if text == '🏠 Главное меню':
        await delete_bot_messages(update, context)
        return await start(update, context)

    if text not in SUBJECTS:
        msg = await update.message.reply_text(
            "Пожалуйста, выбери предмет из списка.",
            reply_markup=ReplyKeyboardMarkup([[s] for s in SUBJECTS.keys()], one_time_keyboard=True)
        )
        bot_messages = context.user_data.get("bot_messages", [])
        bot_messages.append(msg.message_id)
        context.user_data["bot_messages"] = bot_messages
        return SUBJECT
    context.user_data["subject"] = text
    reply_keyboard = [[level] for level in LEVELS.keys()]
    msg = await update.message.reply_text(
        f"Отлично! Предмет: {text}\nТеперь выбери уровень:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
    )
    bot_messages = context.user_data.get("bot_messages", [])
    bot_messages.append(msg.message_id)
    context.user_data["bot_messages"] = bot_messages
    return LEVEL

async def level_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await delete_user_message(update, context)
    level_name = update.message.text
    if level_name == '🏠 Главное меню':
        await delete_bot_messages(update, context)
        return await start(update, context)

    if level_name not in LEVELS:
        msg = await update.message.reply_text(
            "Пожалуйста, выбери уровень из списка.",
            reply_markup=ReplyKeyboardMarkup([[l] for l in LEVELS.keys()], one_time_keyboard=True)
        )
        bot_messages = context.user_data.get("bot_messages", [])
        bot_messages.append(msg.message_id)
        context.user_data["bot_messages"] = bot_messages
        return LEVEL

    subject = context.user_data.get("subject")
    level_code = LEVELS[level_name]

    # Математика
    if subject == "Математика":
        if level_code == "ege":
            task_number = random.randint(1, 21)
            task_url = MATH_EGE_TEST_URL
            correct_answers = CORRECT_ANSWERS_EGE
        else:
            task_number = random.randint(1, 19)
            task_url = MATH_OGE_TEST_URL
            correct_answers = CORRECT_ANSWERS_OGE

        task = await fetch_math_task(task_url, task_number, update)
        if not task:
            await send_and_track(update, context, "Не удалось загрузить задание. Попробуйте позже.")
            return ConversationHandler.END

        correct_answer = correct_answers.get(task_number)
        if not correct_answer:
            await send_and_track(update, context, "Нет правильного ответа для этого задания. Попробуйте другое /start")
            return ConversationHandler.END

        user_id = update.effective_user.id
        user_tasks[user_id] = {"correct_answer": correct_answer}

        await send_and_track(update, context,
            f"📘 *Задание {task_number} ({subject}, {level_name})*\n\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n{task['text']}",
            parse_mode="Markdown"
        )
        if task["images"]:
            await send_and_track(update, context, "📎 Пояснение к заданию (изображения ниже):")
            for img_io in task["images"]:
                await send_image(update, context, img_io)

        await send_and_track(update, context, "✍️ Введи свой ответ (только число/набор цифр без пробелов):")
        return WAITING_ANSWER

    # Русский язык
    elif subject == "Русский язык":
        if level_code != "ege":
            await send_and_track(update, context, "Для русского языка пока доступен только ЕГЭ.")
            return ConversationHandler.END

        task_number = random.randint(1, 26)
        task_url = RUS_EGE_TEST_URL
        correct_answers = RUS_EGE_CORRECT_ANSWERS

        task = await fetch_russian_task(task_url, task_number, update)
        if not task:
            await send_and_track(update, context, "Не удалось загрузить задание. Попробуйте позже.")
            return ConversationHandler.END

        correct_answer = correct_answers.get(task_number)
        if not correct_answer:
            await send_and_track(update, context, "Нет правильного ответа для этого задания. Попробуйте другое /start")
            return ConversationHandler.END

        user_id = update.effective_user.id
        user_tasks[user_id] = {"correct_answer": correct_answer}

        await send_long_text(update, context, task['text'], f"📖 *Задание {task_number} ({subject}, ЕГЭ)*")
        if task["images"]:
            await send_and_track(update, context, "📎 Пояснение к заданию (изображения ниже):")
            for img_io in task["images"]:
                await send_image(update, context, img_io)

        await send_and_track(update, context, "✍️ Введи свой ответ (слово, число или последовательность цифр без пробелов):")
        return WAITING_ANSWER

    else:
        await send_and_track(update, context, "Предмет пока не поддерживается. Выберите Математику или Русский язык.")
        return ConversationHandler.END

async def check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_data = user_tasks.get(user_id)
    if not user_data:
        await send_and_track(update, context, "Что-то пошло не так. Начнём заново? /start")
        return ConversationHandler.END

    user_answer = update.message.text.strip()
    correct_answer = user_data["correct_answer"]

    # Удаляем сообщение пользователя с ответом
    await delete_user_message(update, context)

    action_keyboard = [['🏠 Главное меню']]
    action_markup = ReplyKeyboardMarkup(action_keyboard, one_time_keyboard=False, resize_keyboard=True)

    if user_answer == correct_answer:
        await send_and_track(update, context,
            "✅ *Правильно! Молодец!*\n\nЧто хочешь сделать дальше?",
            parse_mode="Markdown",
            reply_markup=action_markup
        )
    else:
        await send_and_track(update, context,
            f"❌ *Неправильно.*\nПравильный ответ: `{correct_answer}`\n\nЧто хочешь сделать дальше?",
            parse_mode="Markdown",
            reply_markup=action_markup
        )

    user_tasks.pop(user_id, None)
    return ACTION

async def action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await delete_user_message(update, context)
    text = update.message.text
    if text == '🏠 Главное меню':
        await delete_bot_messages(update, context)
        return await start(update, context)
    else:
        # Если пользователь ввёл что-то другое – повторяем предложение
        action_keyboard = [['🏠 Главное меню']]
        action_markup = ReplyKeyboardMarkup(action_keyboard, one_time_keyboard=False, resize_keyboard=True)
        await send_and_track(update, context,
            "Пожалуйста, выбери действие с помощью кнопки:",
            reply_markup=action_markup
        )
        return ACTION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await delete_bot_messages(update, context)
    await update.message.reply_text("Диалог прерван. Чтобы начать заново, отправь /start")
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📘 *Справка*\n\n"
        "Я присылаю задания из реальных вариантов:\n"
        "- Математика ЕГЭ (база) №21621325 (задания 1–21)\n"
        "- Математика ОГЭ №79386761 (задания 1–19)\n"
        "- Русский язык ЕГЭ №55373666 (задания 1–26)\n\n"
        "Управление:\n"
        "• /start – начать диалог\n"
        "• /help – эта справка\n"
        "• после ответа используй кнопку «Главное меню» для возврата",
        parse_mode="Markdown"
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
            ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, action_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()