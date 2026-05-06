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

# ===== Русский язык (ЕГЭ) - локальные тексты заданий =====
RUS_EGE_TASK_TEXTS = {
    1: (
        "Прочитайте текст и выполните задание.\n\n"
        "Коррекция фигуры — это комплекс укрепляющих, оздоровительных, развивающих методов изменения пропорций тела, заключающийся в изменении объёма жировых отложений, нарушающих общий вид фигуры.\n\n"
        "Рассмотрим такой способ коррекции фигуры, как шейпинг. Слово «shaping» происходит от английского «shape», что означает «формировать». Шейпинг — это комплекс упражнений для коррекции и развития пропорций силуэта, формирования осанки и пластики тела. Шейпингом рекомендуется заниматься девушкам и женщинам в возрасте от 14 до 50 лет.\n\n"
        "<…> коррекция фигуры всегда включает не только упражнения, но и правильное питание, то специалисты, контролирующие изменения фигуры, должны обладать соответствующими знаниями, чтобы составить ежедневное меню, в которое входят продукты, содержащие всё необходимое для дополнительных эффективных, корректирующих и моделирующих программных мероприятий.\n\n"
        "Выделяют два типа шейпинга: анаболический и катаболический. Анаболический шейпинг направлен на укрепление и увеличение мышечной массы. Катаболический направлен на избавление от лишнего веса и уменьшение мышечной массы. Таким образом, можно видеть, что физические упражнения, направленные именно на формирование контуров тела, относятся к катаболическому моделированию.\n\n"
        "Самостоятельно подберите союз, который должен стоять на месте пропуска в третьем абзаце текста. Запишите этот союз."
    ),
    2: (
        "В тексте выделено пять слов. Укажите варианты ответов, в которых лексическое значение выделенного слова соответствует его значению в данном тексте. Запишите номера ответов.\n\n"
        "1) ФИГУРА. Положение, принимаемое кем-чем-нибудь при исполнении чего-нибудь в движении (в танце, при полёте, в воздухе). Фигуры высшего пилотажа.\n"
        "2) ВИД. Внешность, видимый облик; состояние. Здоровый вид.\n"
        "3) ВКЛЮЧАТЬ. Привести в действие. Включите свет.\n"
        "4) СОСТА́ВИТЬ. Собрав материал, сделав сводку какого-н. материала, образовать, создать (какое-н. пособие, книгу, доклад, список чего-н. и т.п.). С. список кандидатов. С. доклад. С. словарь. С. сводку. С. проект.\n"
        "5) ВЫДЕЛЯТЬ. Отмечать, отличать что-нибудь. Выделять главное особым шрифтом."
    ),
    3: (
        "Укажите варианты ответов, в которых даны верные характеристики фрагмента текста. Запишите номера этих ответов.\n\n"
        "1) Во фрагменте представлена классификация типов шейпинга, что характерно для текста официально-делового стиля.\n"
        "2) Основная задача текста — дать практические рекомендации девушкам и женщинам от 14 до 50 лет, которые хотят скорректировать фигуру при помощи комплекса упражнений.\n"
        "3) В последнем абзаце сделан вывод, не содержащий авторской эмоциональной оценки описываемого явления.\n"
        "4) Текст относится к научному стилю, о чём свидетельствует использование терминов (коррекция фигуры, анаболический шейпинг, катаболический шейпинг) и их определений.\n"
        "5) Текст содержит большое количество отглагольных существительных (коррекция, развитие, укрепление, увеличение, избавление и др.)."
    ),
    4: (
        "Укажите варианты ответов, в которых верно выделена буква, обозначающая ударный гласный звук. Запишите номера ответов.\n\n"
        "1) зАняло\n"
        "2) прибЫть\n"
        "3) сливОвый\n"
        "4) нОвостей\n"
        "5) прожИвший"
    ),
    5: (
        "В одном из приведённых ниже предложений НЕВЕРНО употреблено выделенное слово. Исправьте лексическую ошибку, подобрав к выделенному слову пароним. Запишите подобранное слово, соблюдая нормы современного русского литературного языка.\n\n"
        "На прилавках магазинов города лежат ОТБОРНЫЕ овощи и фрукты.\n"
        "Художественная гимнастика — один из самых ЭФФЕКТНЫХ и красивых видов спорта.\n"
        "Надо вырабатывать навыки ДИПЛОМАТИЧНОГО поведения.\n"
        "После просмотра фильма у меня сложилось ДВОЯКОЕ впечатление.\n"
        "ПРОДУКТИВНЫМ было творчество юных мастеров, которые работали под руководством известного художника-оформителя."
    ),
    6: (
        "Отредактируйте предложение: исправьте лексическую ошибку, заменив употреблённое неверно слово. Запишите подобранное слово, соблюдая нормы современного русского литературного языка и сохраняя смысл высказывания.\n\n"
        "Время от времени глава семьи менял расстановку сил в собственном доме, одних возносил, других лишал на время полномочий, держал в грязном теле, с тем чтобы потом снова одарить вниманием и заботой."
    ),
    7: (
        "В одном из выделенных ниже слов допущена грамматическая ошибка. Исправьте ошибку и запишите слово правильно.\n\n"
        "ЛАЖУ по крышам\n"
        "часовые ПОЯСА\n"
        "с СЕМЬЮСТАМИ метрами\n"
        "РАЗОЖГЁТ костёр\n"
        "несколько ГРАММОВ"
    ),
    8: (
        "Установите соответствие между грамматическими ошибками и предложениями, в которых они допущены: к каждой позиции первого столбца подберите соответствующую позицию из второго столбца.\n\n"
        "ГРАММАТИЧЕСКИЕ ОШИБКИ:\n"
        "А) нарушение связи между подлежащим и сказуемым\n"
        "Б) нарушение в построении предложения с однородными членами\n"
        "В) нарушение в построении сложного предложения\n"
        "Г) нарушение в построении предложения с причастным оборотом\n"
        "Д) неправильное употребление падежной (предложно-падежной) формы управляемого слова\n\n"
        "ПРЕДЛОЖЕНИЯ:\n"
        "1) Егорова расстраивало не столько всё происходящее, сколько настораживало.\n"
        "2) Отыскав Платона Васильевича и отведя его в сторону, генерал вполголоса расспрашивал о Прозорове и время от времени сосредоточенно покачивал своей большой головой, остриженной под гребёнку.\n"
        "3) Полученное утром известие Раисой Павловной начало циркулировать по всем заводам с изумительной быстротой, поднимая на всех ступеньках заводской иерархии страшнейший переполох.\n"
        "4) Прасковья Семёновна смотрела в даль улицы со слезами на глазах, точно сегодняшний день должен был оправдать её долголетние ожидания.\n"
        "5) Уже с юности, проведённой за кулисами театра, где служила мама, а отчим был заведующим музыкальной части, я стал завсегдатаем театра.\n"
        "6) Мы, забыв про ссоры, вместе пытались выяснить, что получил ли каждый участник ответное письмо.\n"
        "7) Старик с пожелтевшей от старости бородой поднёс большой каравай на серебряном блюде.\n"
        "8) Отец и дед Тетюева служил управителями в Кукарском заводе и прославились в тёмные времена крепостного права особенной жестокостью по отношению к рабочим.\n"
        "9) Родион Антоныч несколько раз просыпался в холодном поту, судорожно крестил своё толстое, заплывшее лицо, охал и долго ворочался с боку на бок.\n\n"
        "Запишите в ответ цифры, расположив их в порядке, соответствующем буквам."
    ),
    9: (
        "Укажите варианты ответов, в которых во всех словах одного ряда пропущена одна и та же буква. Запишите номера ответов.\n\n"
        "1) хр..зантема, с..зон, прим..рять (куртку)\n"
        "2) ун..верситет, заж..гать, соед..нение\n"
        "3) распор..диться, снар..жение, недос..гаемый\n"
        "4) укр..щать, з..рницы, спр..ведливый\n"
        "5) изж..га, капюш..н, (крепкая) беч..вка"
    ),
    10: (
        "Укажите варианты ответов, в которых во всех словах одного ряда пропущена одна и та же буква. Запишите номера ответов.\n\n"
        "1) по..пустить, о..бросить, о..стать;\n"
        "2) во..местить, не..добровать, ..дание;\n"
        "3) супер..гра, пред..юньский, без..скусный;\n"
        "4) пр..брежный, пр..давать (значение), пр..ставить (к стене);\n"
        "5) ад..ютант, с..ёжиться, меж..языковой."
    ),
    11: "Укажите варианты ответов, в которых в обоих словах одного ряда пропущена одна и та же буква. Запишите номера ответов.\n\n1) удоста..вать, масл..це\n2) отво..вав, плать..це\n3) локт..вой, ключ..к\n4) угр..ватый, досто..н\n5) дешев..нький, баш..нка",
    12: "Укажите варианты ответов, в которых в обоих словах одного ряда пропущена одна и та же буква. Запишите номера ответов.\n\n1) умо..шься, вид..мый\n2) кле..шь, будораж..вший (воображение)\n3) расстро..вшись, повад..шься\n4) колыш..щиеся (травы), (они) леч..т\n5) взлеле..вший, вер..щий (на слово)",
    13: "Укажите варианты ответов, в которых НЕ с выделенным словом пишется РАЗДЕЛЬНО.\n\n1) (НЕ)ДОБРАЯ улыбка моего собеседника и даже не его безупречные манеры покорили меня, а необыкновенный голос – бархатный и глубокий.\n2) Почерк был настолько (НЕ)РАЗБОРЧИВЫМ, что я едва смог понять содержание письма.\n3) (НЕ)РАСПУСТИВШИЙСЯ цветок уже источал тонкий аромат.\n4) Несмотря на то что костюм был очень стар, достался он мне (НЕ)ДАРОМ: я отдал за него остатки своих скудных сбережений.\n5) (НЕ)МЕДЛЯ ни минуты, Наташа выбежала из дома.",
    14: "Укажите варианты ответов, в которых все выделенные слова пишутся СЛИТНО. Запишите номера ответов.\n\n1) А небо в те дни льёт на землю ласкающий свет, и прозрачная голубизна его бывает притягательна; не ОТ(ТОГО) ли и стар и млад так (ПО)ДОЛГУ смотрят на небо?\n2) (ИЗ)ДАЛИ хищнику трудно понять, смотрит обезьяна в другую сторону или заметила его и сейчас стадо бросится (В)РАССЫПНУЮ.\n3) Во многих странах зоны отдыха расширяются (ЗА)СЧЁТ обширных территорий бывших карьеров: (НА)ПРИМЕР, в Греции планируется освоение нескольких карьеров, где будут расположены спортивные площадки, аттракционы и пляжи.\n4) Данте упоминает церковь Сан-Миниато и ведущую к ней лестницу ЗА(ТЕМ), ЧТО(БЫ) показать, как высоки и трудны были для людей лестницы, высеченные в склонах священной горы.\n5) (В)ВЕРХУ виднелся редкий для здешних мест большой камень почти правильной кубической формы, а на нём (В)ОБНИМКУ росли два корявых деревца, сосенка и берёзка.",
    15: "Укажите цифру(-ы), на месте которой(-ых) пишется одна буква Н.\n\nНа хозяине была тка(1)ая рубаха, подпояса(2)ая кожа(3)ым ремнём, и холсти(4)ые, давно не глаже(5)ые штаны.",
    16: "Укажите предложения, в которых нужно поставить ОДНУ запятую. Запишите номера этих предложений.\n\n1) Вечером Вадим ушёл в свою комнату и сел перечитывать письмо и писать ответ.\n2) Рано утром я вышел полюбоваться рассветом и подышать свежим прохладным воздухом.\n3) Он подошёл к окну и увидел одни трубы да крыши.\n4) Хорошо бы в нашем музее когда-нибудь увидеть картины Рембрандта или Тициана.\n5) Многие из участников литературного общества «Беседа» были последовательными классицистами и некоторые из них довели до совершенства традиционные классицистические жанры.",
    17: "Укажите цифру(-ы), на месте которой(-ых) должна(-ы) стоять запятая(-ые).\n\nЯ ухватился одной рукой за выбоину в стене, другой упёрся в дверную ручку и (1) подтянувшись (2) сунул ноги в дыру; обеспозвоноченный страхом (3) я некоторое время висел в воздухе (4) сильно изогнувшись (5) и (6) наконец нащупав пол (7) втащил в помещение и верхнюю часть своего туловища.",
    18: "Укажите цифру(-ы), на месте которой(-ых) должна(-ы) стоять запятая(-ые).\n\nМы все учились понемногу\nЧему-нибудь и как-нибудь,\nТак (1)воспитаньем(2) слава богу(3)\nУ нас немудрено блеснуть.\nОнегин был(4) по мненью многих(5)\n(Судей решительных и строгих)(6)\nУченый малый, но педант.\nИмел он счастливый талант\nБез принужденья в разговоре (7)\nКоснуться (8) до всего слегка,\nС ученым видом знатока\nХранить молчанье в важном споре\nИ возбуждать улыбку дам\nОгнем нежданных эпиграмм.\n\n(Александр Пушкин)",
    19: "Укажите цифру(-ы), на месте которой(-ых) должна(-ы) стоять запятая(-ые).\n\nМужество (1) похоже на добродетель (2) повинуясь (3) которой люди (4) совершают прекрасные дела.",
    20: "Укажите цифру(-ы), на месте которой(-ых) должна(-ы) стоять запятая(-ые).\n\nКогда Женя решила всё же принять предложение Александра Семёновича (1) и (2) письмо об этом решении уже было отправлено на его московский адрес (3) она собралась поехать попрощаться со своей тётушкой (4) дабы (5) несмотря на то что (6) отношения между ними были очень непростыми (7) получить от неё благословение.",
    21: "Найдите предложения, в которых тире ставится в соответствии с одним и тем же правилом пунктуации. Запишите номера этих предложений.\n\n1) На гербах разных стран нередко изображаются растения: на гербе Канады привычным стал кленовый лист, а на государственном гербе Мексики изображён кактус.\n2) Это неслучайно, ведь на Мексиканском плоскогорье, возвышающемся над уровнем моря до 2500 метров, находится настоящая страна кактусов.\n3) Некоторые кактусы густо покрыты желтыми и красноватыми колючками — такие растения напоминают птиц и зверей.\n4) Иногда можно увидеть кактус с длинными свисающими волосами — он похож на голову старика.\n5) Цветок кактуса — один из самых красивых в мире.\n6) Среди ночной темноты раскрывается большая бело-голубая звезда.\n7) Размером цветок с большую тарелку − до двадцати пяти сантиметров в диаметре.",
    22: "Установите соответствие между предложениями и названиями изобразительно-выразительных средств языка, которые употреблены в них: к каждой позиции первого столбца подберите соответствующую позицию из второго столбца.\n\nПРЕДЛОЖЕНИЯ:\nA) Стихает ветер, даль расчистив, Разлито солнце по земле. (Б.Л.Пастернак)\nБ) Словно я весенней гулкой ранью Проскакал на розовом коне. (С.А.Есенин)\nВ) Просвечивает зелень листьев, Как живопись в цветном стекле. (Б.Л.Пастернак)\nГ) Там, где калина цветёт, Нежная девушка в белом Нежную песню поёт. (С.А.Есенин)\nД) Я голос твой далёкий слышу (А.А.Тарковский)\n\nИЗОБРАЗИТЕЛЬНО-ВЫРАЗИТЕЛЬНЫЕ СРЕДСТВА ЯЗЫКА:\n1) многосоюзие\n2) метафора\n3) антитеза\n4) инверсия\n5) эпитет\n6) сравнение\n7) ассонанс\n8) анафора\n9) гипербола",
    23: "Какие из высказываний соответствуют содержанию текста? Укажите номера ответов.\n\n1) Когда рассказчик учился в гимназии, он никогда не готовил уроков.\n2) В детстве рассказчик не размышлял о своём возрасте.\n3) Первая мировая война помешала сбыться детским мечтам рассказчика.\n4) Работа воспоминаний удивительна: мы всегда можем вспомнить то, что захотим.\n5) Воспоминания всплывают в нашем воображении вне зависимости от волевых усилий.",
    24: "Какие из перечисленных утверждений являются верными? Укажите номера ответов.\n\n1) В предложениях 5, 6 представлено рассуждение.\n2) В предложении 10 представлено повествование.\n3) Предложения 16 и 17 противопоставлены по содержанию.\n4) В предложениях 30–32 представлено рассуждение.\n5) Предложение 35 содержит элементы описания.",
    25: "Из предложений 13–16 выпишите один фразеологизм.\n\n(13)Я думал, что после окончания гимназии я куплю велосипед и совершу на нём поездку по Европе. (14)Первая мировая война ещё не начиналась, ещё всё было очень старинно: солдаты в чёрных мундирах с красными погонами, зверинец на Куликовом поле с одним львом, говорящая голова в зеркальном ящике в балагане. (15)Ещё бывала первая любовь, когда девочка смотрела на тебя с балкона, и ты думал, не уродлив ли ты. (16)Ещё отец девочки, моряк в парадном мундире, гремя палашом*, шёл тебе навстречу и отвечал тебе на поклон, отчего ты бежал во весь дух, сам не зная куда, обезумевший от счастья.",
    26: "Среди предложений 24–32 найдите такое(-ие), которое(-ые) связано(-ы) с предыдущим при помощи лексического повтора, личного местоимения и однокоренных слов. Напишите номер(-а) этого(-их) предложения(-ий).\n\n(24)Удивительная работа воспоминания. (25)Мы вспоминаем нечто по совершенно неизвестной нам причине. (26)Скажите себе: «Вот сейчас я вспомню что-нибудь из детства», закройте глаза и скажите это. (27)Вспомнится нечто совершенно непредвиденное вами. (28)Участие воли здесь исключено. (29)Картина зажигается, включённая какими-то инженерами позади вашего сознания.\n(30)Какая чудесная вещь — свобода воспоминаний! (31)Какая прелесть в том, что они появляются, как им угодно, и никак мы не можем заставить себя вспомнить именно это, а не другое. (32)Разумеется, есть точная закономерность этого возникновения, но —дудки— мы её никогда не поймём.",
}

# Правильные ответы для русского языка
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
    if update.message and not update.message.text.startswith('/'):
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
        except Exception:
            pass

async def delete_bot_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_messages = context.user_data.get("bot_messages", [])
    for msg_id in bot_messages:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
        except Exception:
            pass
    context.user_data["bot_messages"] = []

async def send_and_track(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    msg = await update.message.reply_text(text, **kwargs)
    bot_messages = context.user_data.get("bot_messages", [])
    bot_messages.append(msg.message_id)
    context.user_data["bot_messages"] = bot_messages

async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, prefix: str = ""):
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

# Изображения для математики (оставляем как было)
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
    patterns = [r'<center><p><b>ИЛИ</b>', r'<b>ИЛИ</b>', r'<p><b>ИЛИ</b>']
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

# ---------- Парсинг заданий (математика) ----------
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
                original_html = str(pbody)
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

    # Русский язык (используем локальные тексты)
    elif subject == "Русский язык":
        if level_code != "ege":
            await send_and_track(update, context, "Для русского языка пока доступен только ЕГЭ.")
            return ConversationHandler.END

        task_number = random.randint(1, 26)
        task_text = RUS_EGE_TASK_TEXTS.get(task_number)
        correct_answer = RUS_EGE_CORRECT_ANSWERS.get(task_number)

        if not task_text or not correct_answer:
            await send_and_track(update, context, "Ошибка: задание не найдено.")
            return ConversationHandler.END

        user_id = update.effective_user.id
        user_tasks[user_id] = {"correct_answer": correct_answer}

        await send_long_text(update, context, task_text, f"📖 *Задание {task_number} ({subject}, ЕГЭ)*")
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