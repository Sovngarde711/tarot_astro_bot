# -*- coding: utf-8 -*-
"""
Прогнозы на период: неделя и год.

Отличие от /transits и /progress в том, что там срез на одну дату, а
здесь — отрезок времени: какой аспект станет точным и <b>когда</b>, в
какие дни новолуние и полнолуние, что меняется по месяцам.

Неделя: карта на каждый день плюс астрофон — точные аспекты быстрых
планет с датой максимума и лунации.

Год: карта на каждый месяц плюс медленные транзиты (Юпитер и дальше) с
месяцем, когда аспект станет точным, ход Юпитера по домам и вторичные
прогрессии — смена знака прогрессивным Солнцем и Луной.

Астрология подключается, только если у клиента построена натальная
карта. Без неё остаётся расклад — это честно и всё равно полезно.
"""
import datetime as dt
import html
import os
import sys

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(BASE_DIR, "data"))
sys.path.insert(0, os.path.dirname(__file__))

import astrology_data as ad  # noqa: E402
import forecast  # noqa: E402
import natal  # noqa: E402
import plain  # noqa: E402
import readings  # noqa: E402
from textutil import trim as _trim  # noqa: E402

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг",
            "пятница", "суббота", "воскресенье"]

MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь",
          "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]

MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн",
                "июл", "авг", "сен", "окт", "ноя", "дек"]

# В годовом прогнозе имеют смысл только медленные планеты: Марс за год
# обойдёт полкруга и даст десятки аспектов, которые ничего не структурируют.
YEAR_PLANETS = ["jupiter", "saturn", "uranus", "neptune", "pluto"]


# Астрофон объясняется один раз в начале блока: человек видит непривычные
# слова «трин» и «оппозиция» и должен понимать хотя бы главное — какая
# планета сейчас задевает его карту и помогает это или мешает.
ASTRO_EXPLAINED = (
    "Здесь не карты, а небо: реальные положения планет и углы, под которыми "
    "они смотрят на вашу карту рождения. 🟢 — угол поддерживающий, 🔴 — "
    "напряжённый, 🟡 — усиливающий, сам по себе ни хороший, ни плохой. "
    "Дата или месяц рядом — когда угол становится точным: в это время тема "
    "слышна громче всего."
)


def _month_name(date, short=False):
    return (MONTHS_SHORT if short else MONTHS)[date.month - 1]


def _card_line(item):
    """Строка дня или месяца — понятной фразой и до конца.

    Раньше сюда подставлялось классическое значение, обрезанное по 95
    символам: половина дней недели заканчивалась многоточием на середине
    слова. Теперь берётся короткая человеческая формулировка карты — она
    умещается целиком, и обрезать её не нужно.
    """
    card = item["card"]
    mark = {1: "🟢", 0: "🟡", -1: "🔴"}[_sign(readings.card_weight(item))]
    # Название и фраза разделены точкой, а не тире: в самих фразах тире
    # встречается часто («пауза затянулась — вы терпите и ждёте»), и через
    # тире в строке оказывалось два разделителя подряд.
    return "%s <b>%s</b>%s. %s." % (
        mark, card["name_ru"], " 🔻" if item["reversed"] else "",
        readings.as_sentence(readings.plain_card(item)))


def _day_name(item):
    """День недели со строчной буквы — он стоит внутри предложения.

    В подписи позиции день с заглавной («Вторник, 15.09»), потому что там
    он начинает строку. В «Осторожнее — вторник» заглавная буква посреди
    фразы выглядит ошибкой, и это она и есть.
    """
    return item["position"].split(",")[0].lower()


def _sign(value):
    return (value > 0) - (value < 0)


def _best_and_worst(draw):
    """Самая удачная и самая трудная карта расклада.

    Выбор по весу карты, а не по её положению: перевёрнутая Восьмёрка
    Мечей — это освобождение, а прямой Дьявол — зависимость, и назначать
    «лучшим днём недели» день Дьявола только потому, что карта не
    перевёрнута, — значит вводить человека в заблуждение.
    """
    ranked = sorted(draw, key=lambda item: readings.card_weight(item))
    return ranked[-1], ranked[0]


# ---------------------------------------------------------------------------
# Неделя
# ---------------------------------------------------------------------------

def week_positions(start):
    return ["%s, %s" % (WEEKDAYS[(start + dt.timedelta(days=i)).weekday()].capitalize(),
                        (start + dt.timedelta(days=i)).strftime("%d.%m"))
            for i in range(7)]


def build_week(profile, retriever=None, start=None):
    start = start or dt.date.today()
    chart = profile.get("chart")
    name = html.escape((profile.get("name") or "").strip())
    positions = week_positions(start)
    draw = readings.draw_cards(
        positions, ["week"] + readings.client_seed(profile) + [start.isoformat()])

    end = start + dt.timedelta(days=6)
    blocks = ["📅 <b>Неделя %s — %s</b>%s" % (
        start.strftime("%d.%m"), end.strftime("%d.%m.%Y"),
        ("\n" + name) if name else "")]

    mood, weight = readings.tone(draw)
    blocks.append("<b>Коротко</b>\n%s %s" % (mood, weight))

    days = ["<b>По дням</b>",
            "<i>Кружок слева — настрой дня: 🟢 день помогает, 🟡 ровный день, "
            "🔴 день требует осторожности. 🔻 значит, что карта выпала "
            "перевёрнутой и читается наоборот.</i>"]
    for item in draw:
        days.append("<b>%s</b>\n%s" % (item["position"], _card_line(item)))
    blocks.append("\n".join(days))

    if chart:
        astro = _week_astro(chart, start)
        if astro:
            blocks.append("<b>Астрофон недели</b>\n<i>%s</i>\n%s"
                          % (ASTRO_EXPLAINED, "\n".join(astro)))
    else:
        blocks.append("<i>Натальной карты у меня нет, поэтому неделя "
                      "расписана только по картам Таро. Скажете время и "
                      "город рождения (/chart) — добавлю и небо: точные "
                      "аспекты по дням, новолуние и полнолуние.</i>")

    blocks.append("<b>Что делать на этой неделе</b>\n%s" % _week_advice(draw))
    blocks.append(
        "Это способ спланировать неделю, а не расписание событий: карты "
        "описывают настрой дня, а не то, что в этот день случится.\n"
        "Разбор по теме — /menu · год — /year"
    )
    return readings.pack(blocks)


def _week_astro(chart, start, limit=3):
    """Астрофон недели — это быстрые планеты.

    Юпитер и дальше за неделю почти не двигаются: их аспекты висят весь
    отрезок и ничего не говорят про конкретные дни. Поэтому берём Солнце,
    Меркурий, Венеру и Марс, а из медленных пускаем только по-настоящему
    точный аспект (орб меньше градуса) — такой и правда ощущается.
    """
    lines = []
    try:
        rows = forecast.window_transits(
            chart, start, days=6, step=1, limit=limit,
            moving_keys=forecast.FAST_TRANSIT_PLANETS + ["mars"])
        slow = [item for item in forecast.window_transits(
            chart, start, days=6, step=3, limit=2,
            moving_keys=["jupiter", "saturn", "uranus", "neptune", "pluto"])
            if item["orb"] < 1.0]
        rows = (rows + slow)[:limit + 1]
    except Exception:
        rows = []
    for item in rows:
        moving, fixed = ad.PLANETS[item["moving"]], ad.PLANETS[item["fixed"]]
        lines.append("%s <b>%s и %s %s</b> в %s — %s. Точнее всего %s." % (
            plain.aspect_mark(item["aspect"]), moving["name_ru"],
            plain.possessive(item["fixed"]), fixed["name_ru"],
            plain.ASPECT_PREPOSITIONAL[item["aspect"]],
            plain.aspect_text(item["aspect"]),
            item["peak"].strftime("%d.%m"),
        ))

    for event in forecast.lunations(start, 6):
        sign = ad.SIGNS[event["sign"]]
        if event["kind"] == "new":
            lines.append("🌑 <b>Новолуние %s</b> в %s — время начинать: %s." % (
                event["date"].strftime("%d.%m"), sign["prepositional"],
                plain.sign(event["sign"])))
        else:
            lines.append("🌕 <b>Полнолуние %s</b> в %s — время закрывать и "
                         "отпускать; эмоции громче обычного." % (
                             event["date"].strftime("%d.%m"), sign["prepositional"]))
    return lines


def _week_advice(draw):
    """Куда ставить важное, а куда лучше не ставить ничего.

    День выбирается по весу карты, а не по её положению: иначе «лучшим
    днём для важного» становился день Дьявола — карта прямая, но речь в
    ней о зависимости, а не об удаче.
    """
    best, worst = _best_and_worst(draw)
    lines = []
    if readings.card_weight(best) > 0:
        lines.append("<b>Для важного — %s.</b> Это самый поддерживающий день "
                     "недели: проще договариваться, начинать и просить. "
                     "Чем он хорош, сказано выше, в списке дней."
                     % _day_name(best))
    else:
        lines.append("Явно удачного дня на этой неделе карты не показывают — "
                     "ровных дней тоже хватает, просто рассчитывайте силы и "
                     "не назначайте всё важное на один день.")

    if readings.card_weight(worst) < 0:
        lines.append("<b>Осторожнее — %s.</b> Это самый трудный день недели: "
                     "разговор, от которого многое зависит, лучше перенести "
                     "на соседний."
                     % _day_name(worst))
    else:
        lines.append("Трудных дней на неделе не выпало: неприятности, если "
                     "они случатся, будут обычными рабочими, а не тем, к чему "
                     "надо готовиться заранее.")
    lines.append("Остальные дни — фон: на них удобно делать рутину и "
                 "готовиться к двум названным.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Год
# ---------------------------------------------------------------------------

def year_months(start):
    """Двенадцать месяцев начиная с текущего."""
    months = []
    year, month = start.year, start.month
    for _ in range(12):
        months.append(dt.date(year, month, 1))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return months


def build_year(profile, retriever=None, start=None):
    start = start or dt.date.today()
    chart = profile.get("chart")
    name = html.escape((profile.get("name") or "").strip())
    months = year_months(start)
    positions = ["%s %d" % (_month_name(date), date.year) for date in months]

    seed = ["year"] + readings.client_seed(profile) + [str(start.year), str(start.month)]
    theme = readings.draw_cards(["Тема года"], seed + ["theme"])[0]
    draw = readings.draw_cards(positions, seed)

    blocks = ["🗓 <b>Год: %s %d — %s %d</b>%s" % (
        _month_name(months[0]), months[0].year,
        _month_name(months[-1]), months[-1].year,
        ("\n" + name) if name else "")]

    mood, weight = readings.tone(draw)
    blocks.append(
        "<b>Коротко</b>\n<b>Тема года — %s.</b>\n%s.\n%s %s" % (
            theme["card"]["name_ru"] + (" 🔻" if theme["reversed"] else ""),
            readings.as_sentence(readings.plain_card(theme)),
            mood, weight,
        ))

    if chart:
        astro = _year_astro(chart, start)
        if astro:
            blocks.append("<b>Астрофон года</b>\n<i>%s</i>\n%s"
                          % (ASTRO_EXPLAINED, "\n".join(astro)))
    else:
        blocks.append("<i>Натальной карты у меня нет, поэтому год расписан "
                      "только по картам Таро, по одной на месяц. Скажете "
                      "время и город рождения (/chart) — добавлю и небо: "
                      "медленные планеты с месяцем пика и прогрессии.</i>")

    lines = ["<b>По месяцам</b>",
             "<i>Кружок — настрой месяца: 🟢 помогает, 🟡 ровно, 🔴 требует "
             "осторожности.</i>"]
    for item, date in zip(draw, months):
        lines.append("<b>%s.</b> %s" % (
            _month_name(date, short=True).capitalize(), _card_line(item)))
    blocks.append("\n".join(lines))

    blocks.append("<b>Что с этим делать</b>\n%s" % _year_advice(draw, months, theme))
    blocks.append(
        "Год — это не расписание событий, а карта нагрузки: видно, где будет "
        "плотно, а где свободно.\nНеделя — /week · тема — /menu"
    )
    return readings.pack(blocks)


def _year_astro(chart, start, limit=4):
    lines = []
    try:
        found = forecast.window_transits(chart, start, days=365, step=5,
                                         moving_keys=YEAR_PLANETS, limit=30)
    except Exception:
        found = []

    # по одному аспекту на планету: иначе Плутон как самый медленный
    # занимает весь блок, и год выглядит одинаковым от строки к строке
    rows, seen = [], set()
    for item in found:
        if item["moving"] in seen:
            continue
        seen.add(item["moving"])
        rows.append(item)
        if len(rows) >= limit:
            break

    for item in rows:
        moving, fixed = ad.PLANETS[item["moving"]], ad.PLANETS[item["fixed"]]
        line = "%s <b>%s и %s %s</b> в %s — %s. Пик — %s %d года." % (
            plain.aspect_mark(item["aspect"]), moving["name_ru"],
            plain.possessive(item["fixed"]), fixed["name_ru"],
            plain.ASPECT_PREPOSITIONAL[item["aspect"]],
            plain.aspect_text(item["aspect"]),
            _month_name(item["peak"]), item["peak"].year,
        )
        if item["exact_return"]:
            line += " Это возвращение %s — круг длиной в целый цикл замкнулся." % (
                moving["genitive"])
        lines.append(line)

    if chart["has_houses"]:
        houses = _jupiter_houses(chart, start)
        if houses:
            zones = ", а затем ".join(plain.house_in(number) for number in houses)
            lines.append("♃ <b>Юпитер — планета роста и удачи — весь год "
                         "идёт %s.</b> Именно в этой части жизни в этом году "
                         "легче всего расти: там меньше сопротивления и "
                         "охотнее открываются двери." % zones)

    lines += _year_progressions(chart, start)
    return lines


def _jupiter_houses(chart, start):
    """Дома, по которым Юпитер пройдёт за год (обычно один-два)."""
    seen = []
    for offset in range(0, 366, 15):
        date = start + dt.timedelta(days=offset)
        positions = forecast.positions_for(date)
        house = natal.house_of(positions["jupiter"]["lon"], chart["cusps"])
        if house not in seen:
            seen.append(house)
    return seen


def _year_progressions(chart, start):
    """Меняют ли прогрессивные Солнце и Луна знак в течение года."""
    lines = []
    try:
        now = forecast.progressions(chart, start)
        later = forecast.progressions(chart, start + dt.timedelta(days=365))
    except Exception:
        return lines

    if now["positions"]["sun"]["sign"] != later["positions"]["sun"]["sign"]:
        lines.append("☉ <b>Прогрессивное Солнце сменит знак</b> — с %s на %s. "
                     "Такое бывает раз-два за жизнь: меняется сама манера себя "
                     "проявлять." % (
                         ad.SIGNS[now["positions"]["sun"]["sign"]]["genitive"],
                         ad.SIGNS[later["positions"]["sun"]["sign"]]["genitive"]))
    if now["positions"]["moon"]["sign"] != later["positions"]["moon"]["sign"]:
        lines.append("☽ <b>Прогрессивная Луна перейдёт в %s</b> — сменится "
                     "эмоциональный фон на пару лет вперёд: %s." % (
                         ad.SIGNS[later["positions"]["moon"]["sign"]]["genitive"],
                         plain.sign(later["positions"]["moon"]["sign"])))
    return lines


def _year_advice(draw, months, theme):
    pairs = list(zip(draw, months))
    # ключевой месяц — сильная карта в прямом положении, неудобный —
    # перевёрнутая. Один и тот же месяц в обеих ролях выглядел бы
    # противоречием, поэтому второй выбираем из оставшихся
    ranked = sorted(pairs, key=lambda pair: readings.card_weight(pair[0]))
    hard, key = ranked[0], ranked[-1]

    parts = []
    item, date = key
    parts.append("<b>Самый удобный месяц — %s.</b> На него и стоит ставить "
                 "то, что решает многое: переезд, запуск, разговор."
                 % _month_name(date))
    item, date = hard
    if readings.card_weight(item) < 0:
        parts.append("<b>Самый трудный — %s.</b> Крупное на этот месяц лучше "
                     "не назначать — не потому, что «нельзя», а потому что "
                     "сил в нём будет меньше обычного."
                     % _month_name(date))
    parts.append("Тема года — %s. Это то, к чему сведётся большинство "
                 "решений: если сомневаетесь, выбирайте то, что ей отвечает."
                 % readings.card_title(theme).lower())
    return "\n".join(parts)
