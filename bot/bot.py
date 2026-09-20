# -*- coding: utf-8 -*-
"""
Telegram-бот-астролог на "чистом" Telegram Bot API (long polling через
requests, без aiogram — чтобы не требовать лишних зависимостей).

Бот работает как консультант, а не как справочник: клиент выбирает сферу
(любовь, работа, семья, бизнес, деньги), называет имя, дату рождения и
вопрос, после чего получает разбор — астрологическая основа по дате
рождения, расклад Таро на позициях этой сферы, синтез и совет
(см. bot/readings.py). Свободные вопросы по картам и астрологии
по-прежнему отвечаются из базы знаний (см. bot/retrieval.py).

Запуск:
    export TELEGRAM_BOT_TOKEN=...   # токен от @BotFather
    python3 bot/bot.py

Требуется заранее собранная векторная база:
    python3 scripts/generate_combinations.py
    python3 scripts/build_vectorstore.py
"""
import html
import logging
import os
import random
import re
import sys
import time
from collections import OrderedDict

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import billing  # noqa: E402
import forecast  # noqa: E402
import natal  # noqa: E402
import payments  # noqa: E402
import privacy  # noqa: E402
import periods  # noqa: E402
import readings  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import webapp  # noqa: E402
from retrieval import Retriever, format_answer, format_tarot_card  # noqa: E402
import astrology_data as ad  # noqa: E402
import tarot_data as td  # noqa: E402

load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{TOKEN}/"

# Чаты, которым доступна команда /stats: номера через запятую. Обычно это
# чат владельца, но их может быть несколько — например, владелец и тот, кто
# ведёт рекламу. Без этой настройки статистику из бота получить нельзя —
# читайте её отчётом из консоли (scripts/stats_report.py).
ADMIN_CHAT_ID = (os.environ.get("ADMIN_CHAT_ID") or "").strip()

# Мини-приложение. WEBAPP_URL — публичный адрес по HTTPS (Telegram иначе
# кнопку не примет). Пусто — бот работает как раньше, без мини-аппа.
WEBAPP_URL = (os.environ.get("WEBAPP_URL") or "").strip()
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT") or 8080)
WEBAPP_DEV = (os.environ.get("WEBAPP_DEV") or "").strip() == "1"


class TokenScrubbingFormatter(logging.Formatter):
    """Вырезает токен из всего, что пишется в лог.

    Токен входит в URL Telegram API, поэтому исключения requests содержат
    его целиком («Max retries exceeded with url: /bot123:ABC.../getUpdates»).
    Достаточно одного обрыва связи, чтобы токен оказался в journald, в
    файле лога и в системе сбора логов. Чистим в форматтере, а не на месте
    вызова: так под защиту попадают и трассировки, и сообщения сторонних
    библиотек (requests, httpx, urllib3), где своих вызовов у нас нет.
    """

    def format(self, record):
        text = super().format(record)
        return text.replace(TOKEN, "<TOKEN>") if TOKEN else text


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
for _handler in logging.getLogger().handlers:
    _handler.setFormatter(TokenScrubbingFormatter(
        "%(asctime)s [%(levelname)s] %(message)s"))

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
log = logging.getLogger("tarot_astro_bot")

# Сколько символов принимаем из одного сообщения. Telegram пропускает до
# 4096, а разбор длинной строки (поиск города по базе на 34 тысячи записей,
# эмбеддинг запроса) занимает доли секунды. Бот однопоточный, поэтому поток
# длинных сообщений блокировал бы обслуживание всех остальных.
MAX_INPUT = 200

# Сколько анкет держим в памяти. Одна анкета с картой весит около 1,6 КБ,
# так что лимит ограничивает расход памяти и не даёт «раздуть» бота флудом
# из новых чатов.
MAX_PROFILES = 10_000

# Антифлуд: не больше RATE_LIMIT сообщений за RATE_WINDOW секунд на чат.
# Запас на живого человека, который быстро жмёт кнопки, остаётся.
RATE_WINDOW = 10.0
RATE_LIMIT = 15


class BoundedDict(OrderedDict):
    """Словарь с вытеснением самых давних записей.

    Обычный dict рос бы бесконечно: каждый новый чат добавляет анкету, и
    ничто её не удаляет. Здесь при переполнении уходит тот, к кому дольше
    всего не обращались.
    """

    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.limit:
            self.popitem(last=False)


# Состояние диалогов и анкеты клиентов. В памяти — активный сеанс; анкеты
# (имя, дата, время, город) дублируются на диск модулем store, чтобы человек
# не диктовал их заново после перезапуска. Вопросы и тексты разборов на
# диск не попадают; в store/history.json уходят только тема, дата и номера
# выпавших карт — из них собирается блок «С прошлого раза».
DIALOGS = BoundedDict(MAX_PROFILES)
PROFILES = BoundedDict(MAX_PROFILES)
PARTNERS = BoundedDict(MAX_PROFILES)  # вторая карта для синастрии
RECENT = BoundedDict(MAX_PROFILES)  # отметки времени для антифлуда
WARNED = BoundedDict(MAX_PROFILES)  # когда чат последний раз предупреждали
RETRIEVER = None  # заполняется в main(); нужен обработчикам кнопок


def rate_limited(chat_id):
    """True, если чат превысил лимит сообщений в окне."""
    if RATE_LIMIT <= 0:
        return False
    now = time.monotonic()
    stamps = [moment for moment in RECENT.get(chat_id, ())
              if now - moment < RATE_WINDOW]
    stamps.append(now)
    RECENT[chat_id] = stamps
    return len(stamps) > RATE_LIMIT


def with_note(text):
    """Приписывает дисклеймер к ответу.

    Разборы и прогнозы получают его внутри readings.pack — там он ложится
    на каждое сообщение длинного текста. Всё остальное, что бот говорит по
    существу метода (карта, транзиты, прогрессии, синастрия, карта дня,
    ответ из справочника), проходит через эту функцию.
    """
    return text.rstrip() + "\n\n" + readings.DISCLAIMER


def admin_chats():
    """Номера чатов из ADMIN_CHAT_ID. Разделитель — запятая, точка с запятой
    или пробел: человек, правящий .env руками, напишет как привычнее."""
    return {part for part in re.split(r"[,;\s]+", ADMIN_CHAT_ID) if part}


def is_admin(chat_id):
    return str(chat_id) in admin_chats()


def store_for(chat_id):
    """Куда писать ответы анкеты: своя карта или карта партнёра."""
    dialog = DIALOGS.get(chat_id) or {}
    return PARTNERS if dialog.get("target") == "partner" else PROFILES

# Что бот отвечает, когда бесплатные разборы кончились. Текст нужен уже
# сейчас: включать платную модель, не имея готового объяснения, значит
# однажды оставить человека перед молчаливым отказом.
PAYWALL_TEXT = (
    "🔒 <b>Бесплатный разбор вы уже получили</b>\n\n"
    "Разборы по темам дальше платные — это ручная работа: карта считается "
    "по эфемеридам, а текст собирается под ваши данные.\n\n"
    "Бесплатным остаётся многое:\n"
    "• /week и /year — прогнозы на неделю и год\n"
    "• /chart — вся натальная карта\n"
    "• /transits и /progress — что происходит сейчас\n"
    "• /random — карта дня\n"
    "• вопрос словами («Башня перевёрнутая», «Марс в Овне») — отвечу из "
    "справочника\n\n"
    "Разбор можно купить за звёзды Telegram — кнопка ниже. Звёзды "
    "покупаются прямо в Telegram, реквизиты карты я не вижу и не храню."
)


def buy_keyboard():
    """Кнопки наборов: сначала поштучно, потом со скидкой."""
    rows = []
    for count, stars, discount in payments.packs():
        label = "⭐ %d — %s" % (stars, payments.title_for(count))
        if discount:
            label += " (−%d%%)" % discount
        rows.append([(label, "buy:%d" % count)])
    rows.append([("Назад", "act:menu")])
    return keyboard(rows)


BUY_TEXT = (
    "⭐ <b>Разборы за звёзды Telegram</b>\n\n"
    "Звёзды — внутренняя валюта Telegram: они покупаются в самом "
    "приложении, а я вижу только факт оплаты. Реквизиты карты ко мне не "
    "попадают.\n\n"
    "Оплаченные разборы не сгорают: берите их когда захотите и по любым "
    "темам.\n\n"
    "<i>Если разбор не пришёл или пришёл не тот — напишите мне, верну "
    "звёзды. Условия целиком: /terms</i>"
)

# Кто оказывает услугу. Для продаж по российскому праву здесь должен
# стоять статус продавца — самозанятый или ИП с номером. Пока переменной
# нет, оферта говорит только то, что заведомо правда: услугу оказывает
# владелец бота, вот контакт для обращений.
SELLER = (os.environ.get("SELLER_NAME") or "").strip()
SELLER_CONTACT = (os.environ.get("SELLER_CONTACT") or "@stasia_77").strip()

OFFER_TEXT = (
    "📄 <b>Условия оплаты и возврата</b>\n\n"

    "<b>1. Что продаётся.</b> Разбор по выбранной теме: натальная карта, "
    "рассчитанная по эфемеридам на ваши дату, время и место рождения, "
    "расклад Таро на позициях этой темы и связный текст с выводами. Это "
    "цифровая услуга: она оказывается сразу, файлов и вещей вы не "
    "получаете.\n\n"

    "<b>2. Кто оказывает.</b> %s Связь по любым вопросам — %s.\n\n"

    "<b>3. Цена и оплата.</b> Один разбор — %d ⭐. Наборы дешевле, цены "
    "показывает /buy. Оплата идёт звёздами Telegram, внутри Telegram. "
    "Реквизиты вашей карты ко мне не попадают: я вижу только факт оплаты "
    "и её номер.\n\n"

    "<b>4. Когда услуга считается оказанной.</b> В момент, когда бот "
    "прислал вам текст разбора. Оплаченные разборы не сгорают и не имеют "
    "срока — берите их когда захотите.\n\n"

    "<b>5. Возврат.</b> Неиспользованные разборы возвращаю по первой "
    "просьбе, без объяснений — напишите %s. Если разбор не пришёл, "
    "пришёл с ошибкой или списалось лишнее, верну звёзды и за него. "
    "Возврат приходит на ваш счёт в Telegram тем же путём, что и "
    "оплата.\n\n"

    "<b>6. Чем это не является.</b> Разбор — не медицинская, не "
    "юридическая и не финансовая консультация, и не замена им. Решения "
    "вы принимаете сами и отвечаете за них тоже сами. Точность "
    "астрономических расчётов я обеспечиваю; сбываемость толкований — "
    "нет, и обещать её было бы обманом.\n\n"

    "<b>7. Возраст.</b> Покупка — для совершеннолетних.\n\n"

    "<b>8. Ваши данные.</b> Храню имя, дату, время и место рождения — "
    "только чтобы не спрашивать их заново. Вопросы и тексты разборов на "
    "диск не попадают. Удалить всё разом: /reset. Счётчик разборов "
    "остаётся: иначе бесплатный лимит обходился бы одной командой. "
    "Подробно — /privacy.\n\n"

    "<b>9. Изменения.</b> Условия могут меняться; к уже оплаченным "
    "разборам применяются те, что действовали в день оплаты.\n\n"

    "<i>Нажимая кнопку оплаты, вы соглашаетесь с этими условиями.</i>"
)


def privacy_messages():
    """Политика обработки данных с настоящими сроками хранения."""
    return privacy.policy(SELLER, SELLER_CONTACT,
                          retention_days=store.RETENTION_DAYS,
                          max_people=store.MAX_PEOPLE)


def deliver_privacy(chat_id):
    """Политика не помещается в одно сообщение — отправляем частями."""
    for part in privacy_messages():
        send_message(chat_id, part)


def offer_text():
    """Оферта с подставленными реквизитами и текущей ценой."""
    who = ("Услугу оказывает %s, владелец бота @Star_potential_bot."
           % SELLER) if SELLER else \
          "Услугу оказывает владелец бота @Star_potential_bot."
    return OFFER_TEXT % (who, SELLER_CONTACT, payments.PRICE, SELLER_CONTACT)

# Оговорка про счётчик. Показывается только при включённой платной модели:
# пока разборы бесплатны, объяснять нечего.
PAYWALL_NOTE = (
    "<b>Про бесплатный разбор.</b> Сколько разборов вы получили, я считаю "
    "отдельно от анкеты — по номеру чата, без имён и дат. Этот счётчик "
    "команда /reset не стирает: иначе бесплатный лимит обходился бы одной "
    "командой. Всё остальное /reset удаляет полностью.\n\n"
    if billing.ENABLED else ""
)

WELCOME_TEXT = (
    "Здравствуйте. Я — астролог и таролог.\n\n"
    "Спрошу дату, время и город рождения — построю вашу натальную карту, "
    "сделаю расклад по выбранной теме и скажу коротко: что происходит, что "
    "делать и чего не делать.\n\n"
    "<i>Анкету я запоминаю, чтобы в следующий раз не спрашивать то же "
    "самое. Кого я помню, покажет команда /people; удалить всё сразу — "
    "/reset. Никому ваши данные не передаю.</i>\n\n"
    "<b>О чём говорим?</b>\n\n"
    + readings.DISCLAIMER
)

HELP_TEXT = (
    "<b>Как со мной работать</b>\n\n"
    "<b>Разбор по теме</b> — /menu или сразу командой:\n"
    "/love — любовь · /feelings — что он чувствует\n"
    "/single — одиночество и встреча · /money — деньги\n"
    "/debts — долги и кредиты · /work — работа\n"
    "/family — семья · /health — силы · /purpose — путь\n"
    "/business — своё дело · /children — дети · /study — учёба\n"
    "/move — переезд · /fears — страхи и блоки\n\n"
    "Спрошу имя, дату, время и город рождения, а также ваш вопрос. "
    "Обязательна только дата. Время и город — это разница между гороскопом по знаку и "
    "настоящей натальной картой.\n\n"
    "<b>Что придёт в ответ.</b> Каждый блок говорит своё, ничего не повторяется дважды:\n"
    "• <b>Коротко</b> — весь расклад обычным языком, по строке на каждую из четырёх позиций;\n"
    "• <b>Что в карте</b> — что об этой теме говорит ваша карта рождения, без жаргона;\n"
    "• <b>Карты</b> — те же четыре номера, но классическими значениями из колоды;\n"
    "• <b>Ваш ход</b> — образ с карты совета и конкретный шаг;\n"
    "• <b>Чего не делать</b> и <b>Когда</b> — что не поддерживать и ближайшие даты;\n"
    "• <b>Итог</b> — из чего сделан вывод и от чего всё зависит.\n\n"
    "<b>Прогнозы</b>\n"
    "/week — на неделю: карта на каждый день, точные аспекты по датам, "
    "новолуние и полнолуние\n"
    "/year — на год: тема года, карта на каждый месяц, медленные транзиты "
    "с месяцем пика\n\n"
    "<b>Астрология отдельно</b>\n"
    "/chart — вся натальная карта: планеты, дома, ASC/MC, аспекты\n"
    "/transits — транзиты: что происходит прямо сейчас\n"
    "/progress — прогрессии «день за год»: тема нынешнего периода\n"
    "/synastry — синастрия: совместимость с партнёром по двум картам\n\n"
    "<b>Другие команды</b>\n"
    "/random — карта дня\n"
    "/people — кого я помню; оттуда же можно удалить\n"
    "/reset — удалить все мои данные\n"
    "/cancel — прервать диалог\n"
    "/help — эта справка\n\n"
    "Можно и просто спросить словами: «Башня перевёрнутая», «Марс в Овне», "
    "«Венера в 7 доме», «Луна квадрат Сатурн» — отвечу из базы знаний.\n\n"
    "<i>Расклад в один и тот же день для одного человека не меняется — "
    "перетасовать колоду до завтра нельзя, как и на живой консультации.</i>\n\n"
    + readings.DISCLAIMER
)

ABOUT_METHOD = (
    "🔭 <b>О методе — честно</b>\n\n"
    "<b>Дата, время и город рождения.</b> Положения планет считаю по "
    "эфемеридам Swiss Ephemeris — тем же, на которых работают профессиональные "
    "астропрограммы. Дома — по Плацидусу (за полярным кругом он не определён, "
    "там перехожу на Порфирия). Управители знаков беру современные: "
    "Скорпионом правит Плутон, Водолеем — Уран, Рыбами — Нептун. В "
    "традиционной астрологии это Марс, Сатурн и Юпитер; обе школы живы, и "
    "я говорю, какой держусь, чтобы вы не удивлялись расхождению с другим "
    "астрологом. Координаты и часовой пояс беру из базы "
    "городов, смещение — историческое: для Москвы 1980 года это декретное "
    "время, а не сегодняшнее.\n\n"
    "<b>Дата и город, без времени.</b> Планеты посчитаю на полдень; дома и "
    "Асцендент — нет, они меняются каждые четыре минуты. Если в этот день "
    "Луна меняет знак, я об этом предупрежу.\n\n"
    "<b>Только дата.</b> Работает солярная карта: дома отсчитываются от знака "
    "Солнца. Это честная классическая техника, но это не ваша натальная карта.\n\n"
    "<b>Прогностика.</b> Транзиты — реальные положения планет на сегодня в "
    "аспектах к вашей карте, орбисы узкие (1,5–3°): транзит работает, пока "
    "аспект точен, а не весь год. Прогрессии — вторичные, «день за год». "
    "Прогрессивные ASC и MC не показываю: для них есть несколько "
    "несовместимых методов, и выбирать один молча — обман.\n\n"
    "<b>Синастрия</b> считается по двум настоящим картам: взаимные аспекты "
    "и планеты каждого в домах другого.\n\n"
    "Чего я не делаю: не выдумываю положения планет, которых не посчитал, и "
    "не подгоняю трактовки под ответ, который хочется услышать.\n\n"
    "Карты Таро тянутся детерминированно от ваших данных и даты обращения.\n\n"
    "<b>Что я делаю с вашими данными.</b> Имя, дату, время и город рождения "
    "я сохраняю — иначе при каждом разговоре пришлось бы диктовать их "
    "заново. Лежат они в файле на том же сервере, где работает бот, и "
    "нужны ровно для одного: построить вашу карту. Ваши вопросы, тексты "
    "разборов не сохраняются. Отдельно я помню последние пять разборов — "
    "только тему, дату и то, какие карты выпали: без этого нельзя сказать "
    "«эту тему мы уже разбирали» и заметить, что карта повторяется. "
    "Анкета, к которой не "
    "обращались полгода, удаляется сама; /people показывает, кого я помню, "
    "и позволяет удалить любого; /reset стирает всё сразу. Никому ваши "
    "данные не передаю.\n\n"
    "<b>Что я считаю для статистики.</b> Чтобы понимать, какие темы нужны и "
    "что в боте работает плохо, я веду обезличенный счётчик: какая тема "
    "выбрана, сколько времени собирался ответ, а из признаков — знак Солнца, "
    "возрастная группа и страна. Вместо номера чата пишется его "
    "необратимый хеш: посчитать, сколько людей вернулось, по нему можно, "
    "а узнать, кто это, — нет. Ни имён, ни дат рождения, ни городов, ни "
    "ваших вопросов в статистике нет.\n\n"
    + PAYWALL_NOTE
    + readings.DISCLAIMER
)


# ---------------------------------------------------------------------------
# Telegram API
# ---------------------------------------------------------------------------

def api_call(method, **params):
    for attempt in range(3):
        try:
            resp = requests.post(API_URL + method, json=params, timeout=40)

            if resp.status_code == 429:
                # превышен лимит Telegram — ждём ровно столько, сколько просят
                wait = min(int(resp.json().get("parameters", {})
                               .get("retry_after", 5)), 30)
                log.warning("Telegram просит подождать %d с (метод %s)", wait, method)
                time.sleep(wait)
                continue

            if 400 <= resp.status_code < 500:
                # ошибка в самом запросе: повторять бессмысленно, три
                # попытки только задержат обслуживание остальных чатов
                payload = resp.json()
                log.warning("API call %s отклонён (%s): %s", method,
                            resp.status_code, payload.get("description"))
                return payload

            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            log.warning("API call %s failed (attempt %d): %s", method, attempt + 1, exc)
            time.sleep(2 * (attempt + 1))
    return None


def keyboard(rows):
    """rows: [[(текст, callback_data[, web_app_url]), ...], ...] -> клавиатура.

    Третий элемент превращает кнопку в запуск мини-приложения: Telegram
    требует для таких кнопок поле web_app вместо callback_data.
    """
    markup = []
    for row in rows:
        buttons = []
        for item in row:
            text, data = item[0], item[1]
            url = item[2] if len(item) > 2 else None
            if url:
                buttons.append({"text": text, "web_app": {"url": url}})
            else:
                buttons.append({"text": text, "callback_data": data})
        markup.append(buttons)
    return {"inline_keyboard": markup}


def sphere_rows(popular_only=True):
    items = [(s["button"], "sph:" + key) for key, s in readings.SPHERES.items()
             if s.get("popular") or not popular_only]
    return [items[i:i + 2] for i in range(0, len(items), 2)]


def menu_keyboard():
    """Шесть частых тем, остальные — за кнопкой «Ещё темы», чтобы не листать."""
    rows = sphere_rows(popular_only=True)
    if WEBAPP_URL:
        rows.insert(0, [("✨ Открыть приложение", None, WEBAPP_URL)])
    rows.append([("➕ Ещё темы", "act:more")])
    rows.append([("📅 На неделю", "cmd:week"), ("🗓 На год", "cmd:year")])
    rows.append([("🪐 Моя карта", "cmd:chart"), ("⏳ Транзиты", "cmd:transits")])
    rows.append([("🌱 Прогрессии", "cmd:progress"), ("💫 Совместимость", "cmd:synastry")])
    rows.append([("🃏 Карта дня", "act:random"), ("🔭 О методе", "act:about")])
    return keyboard(rows)


def more_keyboard():
    items = [(s["button"], "sph:" + key) for key, s in readings.SPHERES.items()
             if not s.get("popular")]
    rows = [items[i:i + 2] for i in range(0, len(items), 2)]
    rows.append([("⬅️ Назад", "act:menu")])
    return keyboard(rows)


TELEGRAM_TEXT_LIMIT = 4000
HTML_TAGS = ("b", "i", "u", "s", "code", "pre", "a")


def cut_html(text, limit=TELEGRAM_TEXT_LIMIT):
    """Обрезает текст, не разрывая HTML-теги.

    Простое text[:limit] может разрезать «<b>» пополам или оставить тег
    незакрытым — Telegram ответит 400, и сообщение не дойдёт вовсе.
    Режем по границе строки и дозакрываем всё, что осталось открытым.
    """
    if len(text) <= limit:
        return text

    cut = text[:limit]
    # не рвём тег, начавшийся у самой границы
    last_open = cut.rfind("<")
    if last_open > cut.rfind(">"):
        cut = cut[:last_open]
    newline = cut.rfind("\n")
    if newline > limit * 0.5:
        cut = cut[:newline]
    cut = cut.rstrip() + "…"

    stack = []
    for match in re.finditer(r"<(/?)([a-z]+)[^>]*>", cut):
        closing, tag = match.group(1), match.group(2)
        if tag not in HTML_TAGS:
            continue
        if closing:
            if stack and stack[-1] == tag:
                stack.pop()
        else:
            stack.append(tag)
    return cut + "".join("</%s>" % tag for tag in reversed(stack))


def send_message(chat_id, text, reply_markup=None):
    # Telegram ограничивает длину сообщения 4096 символами
    params = dict(chat_id=chat_id, text=cut_html(text), parse_mode="HTML")
    if reply_markup:
        params["reply_markup"] = reply_markup
    result = api_call("sendMessage", **params)

    if result is not None and not result.get("ok"):
        # Разметка не понравилась Telegram — отправляем тем же текстом, но
        # без тегов. Лучше сообщение без оформления, чем никакого.
        log.warning("sendMessage отклонён (%s), повторяю без разметки",
                    result.get("description"))
        params.pop("parse_mode", None)
        params["text"] = re.sub(r"<[^>]+>", "", text)[:TELEGRAM_TEXT_LIMIT]
        result = api_call("sendMessage", **params)
    return result


def send_typing(chat_id):
    api_call("sendChatAction", chat_id=chat_id, action="typing")


# ---------------------------------------------------------------------------
# Анкета клиента
# ---------------------------------------------------------------------------

def profile_line(profile):
    name = html.escape(profile.get("name") or "без имени")
    parts = [name, profile["birth"].strftime("%d.%m.%Y")]
    if profile.get("time"):
        parts.append(profile["time"])
    if profile.get("place"):
        parts.append(natal.place_label(profile["place"]))
    sign = ad.SIGNS[readings.sun_sign(profile["birth"])]
    return "%s (%s)" % (", ".join(parts), sign["name_ru"])


def rebuild_chart(chat_id, target=None):
    """Считает натальную карту, если есть место рождения. Молча — если нет."""
    profile = (target if target is not None else PROFILES).get(chat_id)
    if not profile:
        return None
    profile["chart"] = None
    if not (profile.get("place") and natal.available()):
        return None
    try:
        profile["chart"] = natal.build_chart(
            profile["birth"], profile.get("time"), profile["place"])
    except Exception:
        log.exception("Не удалось построить натальную карту")
        profile["chart"] = None
    return profile["chart"]


def restore_profile(chat_id):
    """Анкета текущего сеанса, а если её нет — самая свежая с диска.

    Благодаря этому после перезапуска бота человеку не нужно заново
    диктовать дату рождения ради /chart или /week: анкета поднимается
    сама, карта пересчитывается на месте.
    """
    profile = PROFILES.get(chat_id)
    if profile:
        return profile
    saved = store.people(chat_id)
    if not saved:
        return None
    profile = dict(saved[0])
    profile.pop("_used", None)
    PROFILES[chat_id] = profile
    rebuild_chart(chat_id)
    stats.track("profile_restored", chat_id, **profile_facts(profile))
    return PROFILES[chat_id]


def apply_place(chat_id, place):
    """Записывает место рождения, строит карту и отчитывается о результате."""
    target = store_for(chat_id)
    profile = target.get(chat_id)
    if not profile:
        send_message(chat_id, "Данные потерялись. Начнём заново: /menu")
        DIALOGS.pop(chat_id, None)
        return
    profile["place"] = place
    chart = rebuild_chart(chat_id, target)
    stats.track("profile_place", chat_id, step="city", ok=True,
                country=place.get("country"), has_chart=bool(chart))
    if chart:
        stats.track("chart_built", chat_id, country=place.get("country"),
                    has_chart=chart["has_houses"])

    if chart:
        sun = chart["positions"]["sun"]
        moon = chart["positions"]["moon"]
        lines = [
            "Принято: <b>%s</b>, %s, местное время %s." % (
                natal.place_label(place), place["timezone"], chart["offset"]),
            "",
            "Карта построена:",
            "%s Солнце — %s" % (ad.PLANETS["sun"]["symbol"], natal.format_degree(sun["lon"])),
            "%s Луна — %s" % (ad.PLANETS["moon"]["symbol"], natal.format_degree(moon["lon"])),
        ]
        if chart["has_houses"]:
            lines.append("🌅 Асцендент — %s" % natal.format_degree(chart["asc"]))
        else:
            lines.append("<i>Без времени рождения дома и Асцендент не считаются.</i>")
        if store_for(chat_id) is PROFILES:
            lines.append("\nВся карта целиком — /chart")
        send_message(chat_id, "\n".join(lines))
    elif not natal.available():
        send_message(
            chat_id,
            "Город принял, но движок эфемерид на сервере недоступен — "
            "разберу по солярной карте.",
        )
    else:
        send_message(chat_id, "Город принял, но карту построить не удалось — "
                              "разберу по солярной карте.")
    if target is PROFILES:
        store.remember(chat_id, profile)
    finish_collection(chat_id)


def ask_name(chat_id, sphere_key):
    DIALOGS[chat_id] = {"sphere": sphere_key, "step": "name", "data": {}}
    sphere = readings.SPHERES[sphere_key]
    send_message(
        chat_id,
        "%s <b>%s</b>\n\nКак к вам обращаться?" % (sphere["emoji"], sphere["title"]),
        keyboard([[("Пропустить", "skip:name"), ("Отмена", "act:cancel")]]),
    )


def ask_birth(chat_id):
    DIALOGS[chat_id]["step"] = "birth"
    send_message(
        chat_id,
        "Назовите <b>дату рождения</b> — без неё астрологической части не будет.\n\n"
        "Формат: <code>15.03.1990</code>. Если знаете время рождения, можно так: "
        "<code>15.03.1990 14:30</code>.",
        keyboard([[("Отмена", "act:cancel")]]),
    )


def ask_time(chat_id):
    DIALOGS[chat_id]["step"] = "time"
    send_message(
        chat_id,
        "<b>Время рождения?</b> Формат <code>14:30</code>.\n\n"
        "Оно решает многое: Асцендент и дома сдвигаются каждые четыре минуты. "
        "Без него посчитаю положения планет, но дома и Асцендент — нет. "
        "Посмотрите в свидетельстве о рождении, там время обычно указано.",
        keyboard([[("Не знаю время", "skip:time")], [("Отмена", "act:cancel")]]),
    )


def ask_city(chat_id):
    dialog = DIALOGS[chat_id]
    dialog["step"] = "city"
    partner = dialog.get("target") == "partner"
    whose = "партнёра" if partner else "рождения"
    tail = ("Для синастрии город обязателен: без него не определить "
            "часовой пояс, а значит и положение Луны."
            if partner else
            "Нужен для координат и часового пояса — без него натальную "
            "карту построить нельзя, разберу по солярной.")
    rows = [] if partner else [[("Пропустить", "skip:city")]]
    send_message(
        chat_id,
        "<b>Город %s?</b> Например: <code>Москва</code>, "
        "<code>Нижний Тагил</code>, <code>Алматы</code>.\n\n%s" % (whose, tail),
        keyboard(rows + [[("Отмена", "act:cancel")]]),
    )


def finish_collection(chat_id):
    """Анкета заполнена — решаем, что с ней делать дальше."""
    dialog = DIALOGS.get(chat_id) or {}

    if dialog.get("target") == "partner":
        DIALOGS.pop(chat_id, None)
        deliver_synastry(chat_id)
        return

    if dialog.get("after") == "synastry":
        DIALOGS.pop(chat_id, None)
        start_partner_dialog(chat_id)
        return

    if dialog.get("after") == "period":
        kind = dialog.get("period", "week")
        DIALOGS.pop(chat_id, None)
        deliver_period(chat_id, kind)
        return

    if dialog.get("sphere") is None:  # режим /chart — вопрос не нужен
        DIALOGS.pop(chat_id, None)
        deliver_chart(chat_id)
        return

    ask_question(chat_id)


def ask_question(chat_id):
    dialog = DIALOGS[chat_id]
    dialog["step"] = "question"
    send_message(
        chat_id,
        "И последнее: <b>что именно вас волнует?</b> Опишите ситуацию своими "
        "словами — одной-двумя фразами. Я учту это в разборе.",
        keyboard([[("Вопроса нет, просто расклад", "skip:question"),
                   ("Отмена", "act:cancel")]]),
    )


def known_people(chat_id):
    """Кого бот помнит по этому чату: сначала текущий сеанс, затем диск."""
    saved = store.people(chat_id)
    current = PROFILES.get(chat_id)
    if current and current.get("birth"):
        # анкета этого сеанса впереди и без дубля
        saved = [person for person in saved
                 if not (person.get("birth") == current.get("birth")
                         and (person.get("name") or "").lower()
                         == (current.get("name") or "").lower())]
        saved.insert(0, current)
    return saved[:store.MAX_PEOPLE]


def start_sphere(chat_id, sphere_key):
    """Начало разбора: предлагаем выбрать из знакомых людей или ввести нового."""
    stats.track("sphere_chosen", chat_id, sphere=sphere_key)

    # Отказ — до анкеты, а не после: спросить дату рождения, время и город,
    # а потом сказать «платно» — самый верный способ разозлить человека
    if not billing.can_read(chat_id):
        stats.track("paywall_hit", chat_id, sphere=sphere_key)
        log.info("Чат %s исчерпал бесплатные разборы", chat_id)
        send_message(chat_id, PAYWALL_TEXT, buy_keyboard())
        return

    sphere = readings.SPHERES[sphere_key]
    saved = known_people(chat_id)

    if not saved:
        ask_name(chat_id, sphere_key)
        return

    DIALOGS[chat_id] = {"sphere": sphere_key, "step": "confirm", "data": {},
                        "people": saved}
    rows = [[("%s" % store.label(person), "person:%d" % index)]
            for index, person in enumerate(saved)]
    rows.append([("➕ Другой человек или дата", "person:new")])
    rows.append([("Отмена", "act:cancel")])

    if len(saved) == 1:
        text = ("%s <b>%s</b>\n\nЯ помню ваши данные: <b>%s</b>.\n"
                "Считаем по ним или возьмём другого человека и другую дату?"
                % (sphere["emoji"], sphere["title"],
                   html.escape(store.label(saved[0]))))
    else:
        text = ("%s <b>%s</b>\n\nКого разбираем?"
                % (sphere["emoji"], sphere["title"]))
    send_message(chat_id, text, keyboard(rows))


def deliver_chart(chat_id):
    """Полная натальная карта отдельным сообщением (/chart)."""
    profile = restore_profile(chat_id)
    if not profile or not profile.get("chart"):
        send_message(
            chat_id,
            "Натальной карты пока нет — для неё нужны город и время рождения. "
            "Введите данные заново: /chart",
            menu_keyboard(),
        )
        return
    send_typing(chat_id)
    send_message(chat_id, with_note(natal.format_chart(profile["chart"])),
                 menu_keyboard())


def need_chart(chat_id, what):
    """Проверяет, есть ли натальная карта; если нет — объясняет, что нужно."""
    profile = restore_profile(chat_id)
    if profile and profile.get("chart"):
        return profile
    send_message(
        chat_id,
        "Для %s нужна натальная карта — то есть дата, время и город "
        "рождения. Введём данные: /chart" % what,
        menu_keyboard(),
    )
    return None


def deliver_period(chat_id, kind):
    """Прогноз на неделю или год. Карты — всем, астрология — с картой."""
    profile = restore_profile(chat_id)
    if not profile or not profile.get("birth"):
        DIALOGS[chat_id] = {"sphere": None, "step": "name", "data": {},
                            "after": "period", "period": kind}
        send_message(
            chat_id,
            "%s\n\nСпрошу имя, дату, время и город рождения — тогда добавлю "
            "к раскладу астрологию.\n\nКак к вам обращаться?" % (
                "📅 <b>Прогноз на неделю</b>" if kind == "week"
                else "🗓 <b>Прогноз на год</b>"),
            keyboard([[("Пропустить", "skip:name"), ("Отмена", "act:cancel")]]),
        )
        return

    send_typing(chat_id)
    started = time.monotonic()
    try:
        builder = periods.build_week if kind == "week" else periods.build_year
        messages = builder(profile, RETRIEVER)
    except Exception:
        log.exception("Ошибка при сборке прогноза на %s", kind)
        stats.track("error", chat_id, command=kind)
        send_message(chat_id, "Не смог собрать прогноз. Попробуйте ещё раз.")
        return
    for index, text in enumerate(messages):
        markup = menu_keyboard() if index == len(messages) - 1 else None
        send_message(chat_id, text, markup)
        time.sleep(0.3)
    log.info("Прогноз '%s' отправлен в чат %s", kind, chat_id)


def deliver_transits(chat_id):
    profile = need_chart(chat_id, "транзитов")
    if not profile:
        return
    send_typing(chat_id)
    try:
        report = forecast.transits(profile["chart"], include_fast=True)
        text = forecast.format_transits(report, profile["chart"])
    except Exception:
        log.exception("Ошибка при расчёте транзитов")
        send_message(chat_id, "Не смог посчитать транзиты. Попробуйте ещё раз.")
        return
    send_message(chat_id, with_note(text), menu_keyboard())


def deliver_progressions(chat_id):
    profile = need_chart(chat_id, "прогрессий")
    if not profile:
        return
    send_typing(chat_id)
    try:
        report = forecast.progressions(profile["chart"])
        text = forecast.format_progressions(report, profile["chart"])
    except Exception:
        log.exception("Ошибка при расчёте прогрессий")
        send_message(chat_id, "Не смог посчитать прогрессии. Попробуйте ещё раз.")
        return
    send_message(chat_id, with_note(text), menu_keyboard())


def start_partner_dialog(chat_id):
    """Вторая часть синастрии: данные партнёра."""
    PARTNERS.pop(chat_id, None)
    DIALOGS[chat_id] = {"sphere": None, "target": "partner", "step": "name", "data": {}}
    send_message(
        chat_id,
        "💫 <b>Синастрия</b>\n\nВаша карта есть. Теперь данные партнёра — "
        "имя, дата, время и город рождения.\n\nКак зовут партнёра?",
        keyboard([[("Пропустить", "skip:name"), ("Отмена", "act:cancel")]]),
    )


def deliver_synastry(chat_id):
    profile = PROFILES.get(chat_id)
    partner = PARTNERS.get(chat_id)
    if not (profile and profile.get("chart")):
        send_message(chat_id, "Потерялась ваша карта. Начнём заново: /synastry")
        return
    if not (partner and partner.get("chart")):
        send_message(
            chat_id,
            "Карту партнёра построить не удалось — для синастрии нужны его "
            "дата и город рождения. Попробуем снова: /synastry",
            menu_keyboard(),
        )
        return
    send_typing(chat_id)
    try:
        report = forecast.synastry(profile["chart"], partner["chart"])
        text = forecast.format_synastry(
            report,
            html.escape(profile.get("name") or "") or "вы",
            html.escape(partner.get("name") or "") or "партнёр",
        )
    except Exception:
        log.exception("Ошибка при расчёте синастрии")
        send_message(chat_id, "Не смог посчитать синастрию. Попробуйте ещё раз.")
        return
    send_message(chat_id, with_note(text), menu_keyboard())


def deliver_stats(chat_id, text):
    """Статистика владельцу бота. Посторонним — ни цифры.

    Доступ по ADMIN_CHAT_ID из .env (можно несколько номеров через
    запятую): цифры посещаемости и воронки — это коммерческие данные,
    которым незачем утекать к случайному человеку, написавшему боту
    /stats.
    """
    if not admin_chats():
        send_message(chat_id, "Статистика не настроена: укажите ADMIN_CHAT_ID "
                              "в .env и перезапустите бота.")
        return
    if not is_admin(chat_id):
        # номер чата пишем в лог: без него владелец не узнает, какой номер
        # вписывать в .env, — а спросить его больше негде
        log.info("Чат %s просил статистику, но его нет в ADMIN_CHAT_ID (%r)",
                 chat_id, ADMIN_CHAT_ID)
        send_message(chat_id, "Не знаю такой команды. Что умею — /help")
        return

    days = 30
    parts = text.split()
    if len(parts) > 1 and parts[1].isdigit():
        days = max(1, min(int(parts[1]), 365))

    send_typing(chat_id)
    try:
        events = stats.load_events()
        report = stats.report(events, days=days)
        totals = billing.totals()
        # Деньги смотрят там же, где посещаемость: владельцу нужно видеть
        # не только трафик, но и сколько людей упёрлось в бесплатный лимит —
        # это и есть верхняя граница возможных продаж
        money = ("\n\n💳 <b>Разборы</b>\n"
                 "Чатов с разборами: %d · выдано разборов: %d\n"
                 "Исчерпали бесплатный лимит: %d · оплаченных разборов в "
                 "запасе: %d\n"
                 "<i>Платная модель %s.</i>"
                 % (totals["chats"], totals["readings"], totals["over_free"],
                    totals["paid_left"],
                    "включена" if billing.ENABLED else "выключена"))
        money += recent_payments_block()
        send_message(chat_id, stats.format_report(
            report, "Статистика за %d дн." % days) + money)
    except Exception:
        log.exception("Ошибка при сборке статистики")
        send_message(chat_id, "Не смог собрать статистику — подробности в логе.")


def deliver_people(chat_id):
    """Показывает сохранённые анкеты и даёт удалить любую из них."""
    saved = known_people(chat_id)
    if not saved:
        send_message(chat_id, "Пока я о вас ничего не помню. Начнём разбор — "
                              "и анкета сохранится: /menu", menu_keyboard())
        return
    lines = ["👤 <b>Кого я помню</b>", ""]
    for person in saved:
        lines.append("• %s" % html.escape(store.label(person)))
    lines.append("")
    lines.append("Любого можно удалить — кнопкой ниже. Данные о человеке, "
                 "к которому не обращались полгода, я удаляю сам.")
    rows = [[("🗑 %s" % store.label(person), "forget:%d" % index)]
            for index, person in enumerate(saved)]
    rows.append([("Удалить всё", "forget:all")])
    send_message(chat_id, "\n".join(lines), keyboard(rows))


def profile_facts(profile):
    """Огрублённые признаки анкеты для статистики — без персональных данных."""
    facts = {"has_chart": bool(profile.get("chart"))}
    birth = profile.get("birth")
    if birth:
        facts["sign"] = readings.sun_sign(birth)
        facts["age_band"] = stats.age_band(readings.age_from(birth))
    if profile.get("place"):
        facts["country"] = profile["place"].get("country")
    return facts


def deliver_reading(chat_id, sphere_key, retriever=None):
    profile = PROFILES.get(chat_id)
    if not profile or not profile.get("birth"):
        send_message(chat_id, "Сначала нужна дата рождения. Начнём заново: /menu")
        return
    send_typing(chat_id)
    started = time.monotonic()
    # Прошлые разборы читаем до сборки: с ними разбор здоровается иначе и
    # замечает, что карта повторяется
    history = store.past_readings(chat_id)
    try:
        messages = readings.build_reading(sphere_key, profile,
                                          retriever or RETRIEVER,
                                          history=history)
    except Exception:
        log.exception("Ошибка при сборке расклада")
        stats.track("error", chat_id, command="reading", sphere=sphere_key)
        send_message(chat_id, "Не смог собрать разбор. Попробуйте ещё раз: /menu")
        return
    # Расход записываем здесь: разбор уже собран, и человек его получит.
    # Списывать на входе нельзя — при ошибке сборки лимит сгорел бы зря.
    # В историю пишем только тему, дату и номера карт — тексты разбора и
    # вопрос клиента на диск не попадают (см. store.remember_reading)
    store.remember_reading(chat_id, sphere_key,
                           readings.draw_spread(sphere_key, profile))
    kind = billing.charge(chat_id)
    if kind is None and billing.ENABLED:
        # Расход не записался — кончилось место или слетели права. Выдать
        # разбор сейчас значит отдать платную работу даром и не узнать об
        # этом: следующий запрос спишется так же, то есть никак. Честнее
        # отказать здесь, пока человек ничего не получил
        log.error("разбор не выдан: счётчики не сохраняются")
        send_message(chat_id, with_note(
            "Не могу записать, что разбор выдан, — это сбой на моей стороне. "
            "Чтобы не списать с вас лишнего, я остановился здесь. "
            "Попробуйте через несколько минут."))
        return
    stats.track("reading", chat_id, sphere=sphere_key, reason=kind,
                ms=int((time.monotonic() - started) * 1000),
                **profile_facts(profile))
    for i, text in enumerate(messages):
        if i:
            send_typing(chat_id)
        markup = menu_keyboard() if i == len(messages) - 1 else None
        send_message(chat_id, text, markup)
        time.sleep(0.4)
    log.info("Расклад '%s' отправлен в чат %s", sphere_key, chat_id)


# ---------------------------------------------------------------------------
# Обработка шагов диалога
# ---------------------------------------------------------------------------

def handle_dialog_step(chat_id, text, retriever):
    dialog = DIALOGS[chat_id]
    step = dialog["step"]

    if step == "name":
        name = text.strip()[:40]
        dialog["data"]["name"] = name
        ask_birth(chat_id)
        return

    if step == "birth":
        birth, time_str = readings.parse_birth(text)
        if not birth:
            send_message(
                chat_id,
                "Не разобрал дату. Напишите в формате <code>15.03.1990</code> "
                "(можно «15 марта 1990» или со временем «15.03.1990 14:30»).",
                keyboard([[("Отмена", "act:cancel")]]),
            )
            return
        store_for(chat_id)[chat_id] = {
            "name": dialog["data"].get("name", ""),
            "birth": birth,
            "time": time_str,
        }
        stats.track("profile_birth", chat_id, step="birth",
                    sign=readings.sun_sign(birth),
                    age_band=stats.age_band(readings.age_from(birth)))
        sign = ad.SIGNS[readings.sun_sign(birth)]
        send_message(
            chat_id,
            "Записал: <b>%s</b> — Солнце в %s." % (birth.strftime("%d.%m.%Y"),
                                                   sign["prepositional"]),
        )
        # время могли назвать сразу вместе с датой
        if time_str:
            ask_city(chat_id)
        else:
            ask_time(chat_id)
        return

    if step == "time":
        parsed = re.search(r"\b([01]?\d|2[0-3])[:.\s]([0-5]\d)\b", text)
        if not parsed:
            send_message(
                chat_id,
                "Не разобрал время. Напишите как <code>14:30</code> — или "
                "нажмите «Не знаю время».",
                keyboard([[("Не знаю время", "skip:time")], [("Отмена", "act:cancel")]]),
            )
            return
        store_for(chat_id)[chat_id]["time"] = "%02d:%s" % (
            int(parsed.group(1)), parsed.group(2))
        ask_city(chat_id)
        return

    if step == "city":
        places = natal.find_places(text, limit=4)
        if not places:
            stats.track("profile_place", chat_id, step="city", ok=False,
                        reason="city_not_found")
            send_message(
                chat_id,
                "Такого города не нашёл. Попробуйте написать иначе (например, "
                "<code>Санкт-Петербург</code>) или пропустите — тогда разберу "
                "по солярной карте.",
                keyboard([[("Пропустить", "skip:city")], [("Отмена", "act:cancel")]]),
            )
            return
        if len(places) == 1:
            apply_place(chat_id, places[0])
            return
        dialog["places"] = places
        send_message(
            chat_id,
            "Уточните, какой именно — от этого зависят координаты и часовой пояс:",
            keyboard([[("%s — %s" % (natal.place_label(p), p["timezone"]), "city:%d" % i)]
                      for i, p in enumerate(places)]
                     + [[("Пропустить", "skip:city")]]),
        )
        return

    if step == "question":
        PROFILES[chat_id]["question"] = text.strip()[:500]
        sphere_key = dialog["sphere"]
        DIALOGS.pop(chat_id, None)
        deliver_reading(chat_id, sphere_key, retriever)
        return

    # confirm ждёт нажатия кнопки; текст трактуем как «другие данные»
    if step == "confirm":
        ask_name(chat_id, dialog["sphere"])


# ---------------------------------------------------------------------------
# Оплата звёздами Telegram
# ---------------------------------------------------------------------------

def recent_payments_block(limit=5):
    """Последние оплаты с номерами — их спрашивает команда возврата."""
    found = []
    for chat, record in billing.load_all().items():
        for payment in (record.get("payments") or []):
            found.append((payment.get("date") or "", chat, payment))
    if not found:
        return ""

    found.sort(reverse=True)
    lines = ["\n\n⭐ <b>Последние оплаты</b>"]
    for date, chat, payment in found[:limit]:
        lines.append("%s · чат %s · %s ⭐ за %s разб. · <code>%s</code>%s"
                     % (date, chat, payment.get("stars"),
                        payment.get("count"), payment.get("id"),
                        " · возвращено" if payment.get("refunded") else ""))
    lines.append("<i>Вернуть: /refund НОМЕР</i>")
    return "\n".join(lines)


def offer_purchase(chat_id):
    """Показывает наборы разборов и их цену.

    Пока платная модель выключена, продавать нечего: разборы и так
    бесплатны, и взять за них деньги значило бы продать воздух, а потом
    возвращать. Владельцу покупка остаётся доступной — иначе цепочку
    оплаты не проверить.
    """
    if not billing.ENABLED and not is_admin(chat_id):
        send_message(chat_id, "Сейчас все разборы бесплатны — платить не "
                              "нужно. Выбирайте тему: /menu")
        return
    stats.track("buy_opened", chat_id)
    send_message(chat_id, BUY_TEXT, buy_keyboard())


def send_invoice(chat_id, count):
    """Выставляет счёт на набор из count разборов."""
    if not billing.ENABLED and not is_admin(chat_id):
        # Кнопка покупки могла остаться в старом сообщении: Telegram
        # хранит их вечно, и нажать её можно через месяц после того, как
        # оплату выключили
        log.info("счёт не выставлен: платная модель выключена")
        send_message(chat_id, "Сейчас все разборы бесплатны — платить не "
                              "нужно. Выбирайте тему: /menu")
        return
    order = payments.invoice(count)
    if not order:
        log.warning("запрошен набор %r, которого нет", count)
        send_message(chat_id, "Такого набора нет — выберите из списка: /buy")
        return
    result = api_call("sendInvoice", chat_id=chat_id, **order)
    if result is None or not result.get("ok"):
        reason = (result or {}).get("description") or "нет связи с Telegram"
        log.warning("счёт на %d разборов не ушёл: %s", count, reason)
        send_message(chat_id, "Не смог выставить счёт. Попробуйте через "
                              "минуту или напишите мне, если повторится.")
        return
    stats.track("invoice_sent", chat_id, count=count,
                stars=order["prices"][0]["amount"])


def handle_pre_checkout(query):
    """Последняя проверка перед тем, как Telegram спишет звёзды.

    Ответить нужно за десять секунд, иначе платёж отменяется. Поэтому
    здесь только сверка, что счёт наш, — ничего долгого.
    """
    count = payments.count_from_payload(query.get("invoice_payload"))
    params = {"pre_checkout_query_id": query["id"], "ok": count is not None}
    if count is None:
        log.warning("незнакомый счёт в pre_checkout: %r",
                    query.get("invoice_payload"))
        params["error_message"] = ("Этот счёт устарел. Откройте /buy и "
                                   "выберите набор заново.")
    api_call("answerPreCheckoutQuery", **params)


def handle_successful_payment(chat_id, paid):
    """Звёзды списаны — начисляем оплаченные разборы."""
    charge_id = paid.get("telegram_payment_charge_id")
    stars = paid.get("total_amount") or 0
    count = payments.granted_count(paid.get("invoice_payload"), stars)

    if not count:
        # Деньги списаны, а понять, за что, не получается. Молчать здесь
        # нельзя: человек заплатил и вправе знать, что делать дальше
        log.error("оплата %s на %s звёзд не разобрана: payload=%r",
                  charge_id, stars, paid.get("invoice_payload"))
        send_message(chat_id, with_note(
            "Оплата прошла, но я не смог разобрать, за какой набор. "
            "Напишите мне — разберусь вручную и либо начислю разборы, "
            "либо верну звёзды."))
        return

    if not billing.pay(chat_id, charge_id, count, stars):
        # Либо это повтор того же платежа (Telegram шлёт подтверждение,
        # пока бот его не примет), либо счётчики не записались. Первое —
        # норма и человеку сообщать не о чем, второе — беда, о которой
        # он узнает при первом же разборе
        log.warning("платёж %s не начислен (повтор или сбой записи)",
                    charge_id)
        return

    stats.track("payment", chat_id, count=count, stars=stars)
    log.info("чат %s оплатил %d разборов за %s звёзд", chat_id, count, stars)
    left = billing.state(chat_id)["paid"]
    send_message(chat_id, with_note(
        "⭐ <b>Спасибо, оплата прошла.</b>\n\n"
        "Начислено разборов: %d. Всего доступно: %d.\n\n"
        "Выбирайте тему — и поехали." % (count, left)), menu_keyboard())


def deliver_refund(chat_id, text):
    """Возврат звёзд по номеру платежа. Только для владельца.

    Возвращать умеет сам Telegram, но команду должен отдать бот. Делать
    это по кнопке у клиента нельзя: тогда разбор можно получить и тут же
    забрать деньги. Поэтому возврат — ручное решение владельца.
    """
    if not is_admin(chat_id):
        send_message(chat_id, "Эта команда не для общего пользования. "
                              "Если нужен возврат — напишите мне словами.")
        return

    parts = text.split()
    if len(parts) < 2:
        send_message(chat_id,
                     "Как пользоваться: <code>/refund НОМЕР_ПЛАТЕЖА</code>\n\n"
                     "Номер приходит в подтверждении оплаты и хранится в "
                     "учёте. Посмотреть последние: /stats")
        return

    charge_id = parts[1]
    owner, payment = billing.find_payment(charge_id)
    if not owner:
        send_message(chat_id, "Такого платежа в учёте нет. Проверьте номер.")
        return
    if payment.get("refunded"):
        send_message(chat_id, "Этот платёж уже возвращён %s."
                     % payment["refunded"])
        return

    result = api_call("refundStarPayment", user_id=int(owner),
                      telegram_payment_charge_id=charge_id)
    if result is None or not result.get("ok"):
        reason = (result or {}).get("description") or "нет связи с Telegram"
        send_message(chat_id, "Telegram отказал в возврате: %s"
                     % html.escape(str(reason)))
        return

    billing.mark_refunded(owner, charge_id)
    stats.track("refund", chat_id, stars=payment.get("stars"))
    send_message(chat_id, "Возвращено: %s звёзд чату %s."
                 % (payment.get("stars"), owner))
    send_message(int(owner), with_note(
        "Звёзды за этот разбор вернулись на ваш счёт в Telegram. "
        "Если захотите попробовать снова — я на месте."))


def handle_callback(callback, retriever):
    chat_id = callback["message"]["chat"]["id"]
    # callback_data формируем мы сами, но клиент может прислать любую
    # строку, поэтому длину ограничиваем наравне с обычными сообщениями
    data = (callback.get("data") or "")[:MAX_INPUT]
    api_call("answerCallbackQuery", callback_query_id=callback["id"])

    if rate_limited(chat_id):
        log.info("Чат %s превысил лимит нажатий", chat_id)
        return

    if data.startswith("buy:"):
        try:
            count = int(data.split(":", 1)[1])
        except ValueError:
            offer_purchase(chat_id)
            return
        send_invoice(chat_id, count)
        return

    if data.startswith("sph:"):
        start_sphere(chat_id, data.split(":", 1)[1])
        return

    if data.startswith("cmd:"):  # кнопка меню = та же команда
        DIALOGS.pop(chat_id, None)
        handle_message(chat_id, "/" + data.split(":", 1)[1], retriever)
        return

    if data.startswith("person:"):
        dialog = DIALOGS.get(chat_id)
        if not dialog:
            send_message(chat_id, "Диалог уже закрыт. Открыть меню: /menu")
            return
        choice = data.split(":", 1)[1]
        sphere_key = dialog.get("sphere")

        if choice == "new":
            # «другой человек» — начинаем анкету с чистого листа, но
            # сохранённых людей не трогаем: они пригодятся в следующий раз
            PROFILES.pop(chat_id, None)
            ask_name(chat_id, sphere_key)
            return

        saved = dialog.get("people") or []
        if not choice.isdigit() or int(choice) >= len(saved):
            send_message(chat_id, "Не понял выбор. Откроем меню заново: /menu")
            return

        profile = dict(saved[int(choice)])
        profile.pop("_used", None)
        PROFILES[chat_id] = profile
        rebuild_chart(chat_id)
        store.touch(chat_id, profile)
        stats.track("profile_reused", chat_id, **profile_facts(profile))
        ask_question(chat_id)
        return

    if data.startswith("forget:"):
        choice = data.split(":", 1)[1]
        if choice == "all":
            DIALOGS.pop(chat_id, None)
            PROFILES.pop(chat_id, None)
            PARTNERS.pop(chat_id, None)
            store.forget(chat_id)
            send_message(chat_id, "Удалил всё, что помнил о вас. "
                                  "Начать заново: /menu", menu_keyboard())
            return
        if not choice.isdigit():
            send_message(chat_id, "Не понял выбор. Список заново: /people")
            return
        index = int(choice)
        saved = known_people(chat_id)
        if index >= len(saved):
            send_message(chat_id, "Этой анкеты уже нет. Список заново: /people")
            return
        gone = saved[index]
        store.forget_person(chat_id, index)
        current = PROFILES.get(chat_id)
        if current and current.get("birth") == gone.get("birth"):
            PROFILES.pop(chat_id, None)
        send_message(chat_id, "Удалил: %s" % html.escape(store.label(gone)))
        deliver_people(chat_id)
        return

    if data == "skip:name":
        dialog = DIALOGS.get(chat_id)
        if dialog:
            dialog["data"]["name"] = ""
            ask_birth(chat_id)
        return

    if data == "skip:time":
        target = store_for(chat_id)
        if chat_id in target:
            target[chat_id]["time"] = None
        if chat_id in DIALOGS:
            ask_city(chat_id)
        return

    if data == "skip:city":
        target = store_for(chat_id)
        if chat_id in target:
            target[chat_id]["place"] = None
            target[chat_id]["chart"] = None
            if target is PROFILES:
                store.remember(chat_id, target[chat_id])
        if chat_id in DIALOGS:
            finish_collection(chat_id)
        return

    if data.startswith("city:"):
        dialog = DIALOGS.get(chat_id)
        places = (dialog or {}).get("places") or []
        index = int(data.split(":", 1)[1])
        if 0 <= index < len(places):
            apply_place(chat_id, places[index])
        return

    if data == "skip:question":
        dialog = DIALOGS.get(chat_id)
        if not dialog:
            return
        sphere_key = dialog["sphere"]
        DIALOGS.pop(chat_id, None)
        if chat_id in PROFILES:
            PROFILES[chat_id]["question"] = ""
        deliver_reading(chat_id, sphere_key, retriever)
        return

    if data == "act:more":
        send_message(chat_id, "Что ещё разбираем:", more_keyboard())
        return

    if data == "act:menu":
        send_message(chat_id, "Выберите тему:", menu_keyboard())
        return

    if data == "act:random":
        handle_random(chat_id)
        return

    if data == "act:about":
        send_message(chat_id, ABOUT_METHOD, menu_keyboard())
        return

    if data == "act:cancel":
        DIALOGS.pop(chat_id, None)
        send_message(chat_id, "Хорошо, остановились. Вернуться к выбору: /menu")


# ---------------------------------------------------------------------------
# Команды и свободные вопросы
# ---------------------------------------------------------------------------

def handle_random(chat_id):
    card = random.choice(td.ALL_CARDS)
    upside_down = random.choice([True, False])
    item = {"card": card, "reversed": upside_down}
    text = "🃏 <b>Карта дня — %s</b>\n\n<b>Проще говоря.</b> %s.\n\n%s" % (
        readings.card_title(item),
        readings.as_sentence(readings.plain_card(item)),
        format_tarot_card(card),
    )
    send_message(chat_id, with_note(text), menu_keyboard())


def handle_message(chat_id, text, retriever):
    # Обрезаем на входе: дальше текст уходит в поиск по базе городов и в
    # эмбеддинги, а бот однопоточный — длинные строки тормозят всех
    text = (text or "").strip()[:MAX_INPUT]
    if not text:
        return

    if rate_limited(chat_id):
        # предупреждаем не чаще раза в окно, дальше молчим: переписываться
        # с флудером — значит тратить на него те же ресурсы
        now = time.monotonic()
        if now - WARNED.get(chat_id, 0.0) > RATE_WINDOW:
            WARNED[chat_id] = now
            send_message(chat_id, "Слишком много сообщений подряд. "
                                  "Подождите несколько секунд.")
        log.info("Чат %s превысил лимит сообщений", chat_id)
        stats.track("rate_limited", chat_id)
        return

    if text.startswith("/"):
        command = text.split()[0].lstrip("/").split("@")[0].lower()
        # /start и /menu ведут собственные события для воронки, остальные
        # команды считаем здесь — иначе в отчёте виден только запуск
        if command not in ("start", "menu"):
            stats.track("command", chat_id, command=command)

        if command in ("start", "menu"):
            stats.track("start" if command == "start" else "menu", chat_id,
                        command=command)
            DIALOGS.pop(chat_id, None)
            send_message(chat_id, WELCOME_TEXT, menu_keyboard())
            return
        if command == "help":
            send_message(chat_id, HELP_TEXT, menu_keyboard())
            return
        if command == "about":
            send_message(chat_id, ABOUT_METHOD, menu_keyboard())
            return
        if command in ("random", "card"):
            handle_random(chat_id)
            return
        if command == "stats":
            deliver_stats(chat_id, text)
            return
        if command in ("buy", "pay"):
            offer_purchase(chat_id)
            return
        if command in ("terms", "offer"):
            send_message(chat_id, offer_text(), menu_keyboard())
            return
        if command in ("privacy", "data"):
            deliver_privacy(chat_id)
            return
        if command == "refund":
            deliver_refund(chat_id, text)
            return
        if command in ("week", "nedelya"):
            deliver_period(chat_id, "week")
            return
        if command in ("year", "god"):
            deliver_period(chat_id, "year")
            return
        if command in ("transits", "transit"):
            deliver_transits(chat_id)
            return
        if command in ("progress", "progressions"):
            deliver_progressions(chat_id)
            return
        if command in ("synastry", "sinastry"):
            profile = restore_profile(chat_id)
            if profile and profile.get("chart"):
                start_partner_dialog(chat_id)
            else:
                DIALOGS[chat_id] = {"sphere": None, "step": "name",
                                    "after": "synastry", "data": {}}
                send_message(
                    chat_id,
                    "💫 <b>Синастрия</b>\n\nСравню две карты. Сначала ваша — "
                    "имя, дата, время и город рождения, потом то же про "
                    "партнёра.\n\nКак к вам обращаться?",
                    keyboard([[("Пропустить", "skip:name"), ("Отмена", "act:cancel")]]),
                )
            return
        if command == "chart":
            profile = restore_profile(chat_id)
            if profile and profile.get("chart"):
                deliver_chart(chat_id)
            elif profile and profile.get("birth") and not profile.get("place"):
                DIALOGS[chat_id] = {"sphere": None, "step": "city", "data": {}}
                ask_city(chat_id)
            else:
                DIALOGS[chat_id] = {"sphere": None, "step": "name", "data": {}}
                send_message(
                    chat_id,
                    "🪐 <b>Натальная карта</b>\n\nСпрошу четыре вещи: имя, дату, "
                    "время и город рождения.\n\nКак к вам обращаться?",
                    keyboard([[("Пропустить", "skip:name"), ("Отмена", "act:cancel")]]),
                )
            return
        if command in ("cancel", "stop"):
            DIALOGS.pop(chat_id, None)
            send_message(chat_id, "Диалог прерван. Вернуться к выбору: /menu")
            return
        if command == "reset":
            DIALOGS.pop(chat_id, None)
            PROFILES.pop(chat_id, None)
            PARTNERS.pop(chat_id, None)
            store.forget(chat_id)
            answer = ("Удалил всё, что помнил о вас: анкеты стёрты и из "
                      "памяти, и с диска. ")
            if billing.ENABLED:
                answer += ("Счётчик выданных разборов остаётся — иначе "
                           "бесплатный лимит обходился бы этой командой. ")
            send_message(chat_id, answer + "Начать заново: /menu")
            return
        if command in ("people", "profiles"):
            deliver_people(chat_id)
            return
        if command in readings.SPHERE_BY_COMMAND:
            start_sphere(chat_id, readings.SPHERE_BY_COMMAND[command])
            return

        send_message(chat_id, "Не знаю такой команды. Что умею — /help")
        return

    # Текст в открытом диалоге — это ответ на мой вопрос
    if chat_id in DIALOGS:
        handle_dialog_step(chat_id, text, retriever)
        return

    # Короткая реплика с названием темы: «любовь», «про деньги», «переезд»
    sphere_key = readings.match_sphere(text)
    if sphere_key:
        start_sphere(chat_id, sphere_key)
        return

    # Иначе — свободный вопрос по базе знаний
    send_typing(chat_id)
    result = retriever.search(text)
    answer = format_answer(result)
    send_message(
        chat_id,
        with_note(answer + "\n\n<i>Нужен разбор по вашей дате рождения? "
                           "Выберите сферу:</i>"),
        menu_keyboard(),
    )


# ---------------------------------------------------------------------------

def main():
    if not TOKEN:
        raise SystemExit(
            "Не задан TELEGRAM_BOT_TOKEN. Получите токен у @BotFather в "
            "Telegram и передайте его через переменную окружения "
            "TELEGRAM_BOT_TOKEN (например, в файле .env)."
        )

    global RETRIEVER

    log.info("Загружаю базу знаний...")
    retriever = Retriever()
    RETRIEVER = retriever
    log.info("База загружена: %d документов.", len(retriever.documents))
    log.info("Эфемериды: %s · геокодер: %s",
             "есть" if natal.available() else "НЕТ (только солярные карты)",
             "есть" if natal.geocoder_available() else "НЕТ")

    if WEBAPP_URL or WEBAPP_DEV:
        webapp.start(TOKEN, PROFILES, retriever, port=WEBAPP_PORT,
                     dev_mode=WEBAPP_DEV)
        if WEBAPP_URL:
            # кнопка рядом со строкой ввода — самый заметный вход в приложение
            api_call("setChatMenuButton", menu_button={
                "type": "web_app", "text": "✨ Приложение",
                "web_app": {"url": WEBAPP_URL}})
            log.info("Мини-приложение: %s", WEBAPP_URL)
    else:
        log.info("Мини-приложение выключено (не задан WEBAPP_URL)")

    me = api_call("getMe")
    if not me or not me.get("ok"):
        raise SystemExit(f"Не удалось авторизоваться в Telegram API: {me}")
    log.info("Бот запущен как @%s", me["result"]["username"])

    api_call("setMyCommands", commands=[
        {"command": "menu", "description": "Выбрать тему"},
    ] + [
        {"command": sphere["command"], "description": sphere["title"]}
        for sphere in readings.SPHERES.values()
    ] + [
        {"command": "week", "description": "Прогноз на неделю"},
        {"command": "year", "description": "Прогноз на год"},
        {"command": "chart", "description": "Моя натальная карта"},
        {"command": "transits", "description": "Транзиты: что происходит сейчас"},
        {"command": "progress", "description": "Прогрессии: тема этого года"},
        {"command": "synastry", "description": "Синастрия: совместимость с партнёром"},
        {"command": "random", "description": "Карта дня"},
        {"command": "about", "description": "О методе"},
        {"command": "people", "description": "Кого я помню"},
        {"command": "privacy", "description": "Что я храню и как это удалить"},
    ] + ([
        # Пока разборы бесплатны, предлагать их купить незачем: команда
        # работает, но в меню не мозолит глаза
        {"command": "buy", "description": "Купить разборы за звёзды"},
        {"command": "terms", "description": "Условия оплаты и возврата"},
    ] if billing.ENABLED else []) + [
        {"command": "reset", "description": "Удалить мои данные"},
        {"command": "help", "description": "Справка"},
    ])

    offset = None
    log.info("Начинаю long polling...")
    while True:
        try:
            # pre_checkout_query нужен обязательно: не ответив на него за
            # десять секунд, бот отменяет оплату. Без этой строки Telegram
            # такие обновления просто не присылает, и платежи не проходят
            params = dict(timeout=30, allowed_updates=[
                "message", "callback_query", "pre_checkout_query"])
            if offset is not None:
                params["offset"] = offset
            resp = api_call("getUpdates", **params)
            if not resp or not resp.get("ok"):
                time.sleep(3)
                continue
            for update in resp["result"]:
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    try:
                        handle_callback(update["callback_query"], retriever)
                    except Exception:
                        log.exception("Ошибка при обработке кнопки")
                    continue
                if "pre_checkout_query" in update:
                    try:
                        handle_pre_checkout(update["pre_checkout_query"])
                    except Exception:
                        log.exception("Ошибка перед списанием звёзд")
                    continue
                message = update.get("message")
                if not message:
                    continue
                # Подтверждение оплаты приходит сообщением без текста —
                # до проверки на текст, иначе оплата потеряется молча
                if "successful_payment" in message:
                    try:
                        handle_successful_payment(message["chat"]["id"],
                                                  message["successful_payment"])
                    except Exception:
                        log.exception("Ошибка при зачислении оплаты")
                    continue
                if "text" not in message:
                    continue
                chat_id = message["chat"]["id"]
                try:
                    handle_message(chat_id, message["text"], retriever)
                except Exception:
                    log.exception("Ошибка при обработке сообщения")
                    send_message(chat_id, "Произошла внутренняя ошибка, попробуйте ещё раз.")
        except KeyboardInterrupt:
            log.info("Остановка бота.")
            break
        except Exception:
            log.exception("Ошибка в основном цикле, повтор через 5 секунд")
            time.sleep(5)


if __name__ == "__main__":
    main()
