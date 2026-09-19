# -*- coding: utf-8 -*-
"""
Прогностика и совместимость: транзиты, вторичные прогрессии, синастрия.

Всё считается поверх натальной карты из bot/natal.py.

Транзиты — где планеты идут сегодня относительно натальной карты. Берутся
аспекты транзитных планет к натальным и дом натальной карты, по которому
сейчас идёт транзитная планета. Орбисы узкие (1.5-3°): транзит работает,
пока аспект точен, а не весь год.

Вторичные прогрессии — классика «день за год»: карта на N-й день после
рождения описывает N-й год жизни. Прогрессивное Солнце сдвигается примерно
на градус в год (смена знака — заметный перелом), прогрессивная Луна
проходит знак за два с половиной года и показывает эмоциональный цикл.
Прогрессивные планеты кладём в натальные дома — стандартная практика;
прогрессивные углы (ASC/MC) не считаем: для них существует несколько
несовместимых методов, и честнее их не показывать.

Синастрия — аспекты между планетами двух людей плюс планеты каждого в
домах другого. Считается по двум натальным картам.
"""
import datetime as dt
import os
import sys

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(BASE_DIR, "data"))
sys.path.insert(0, os.path.dirname(__file__))

import astrology_data as ad  # noqa: E402
import natal  # noqa: E402
import plain  # noqa: E402

# Тропический год: столько суток отделяют прогрессивный «день» от «года».
TROPICAL_YEAR = 365.242190

# Кто в транзитах вообще интересен. Луна проходит знак за два дня —
# в личном прогнозе это шум, поэтому её не берём.
TRANSIT_PLANETS = ["mars", "jupiter", "saturn", "uranus", "neptune", "pluto"]
FAST_TRANSIT_PLANETS = ["sun", "mercury", "venus"]

# Орбисы транзитов заметно уже натальных.
TRANSIT_ORBS = {
    "conjunction": 3.0,
    "opposition": 3.0,
    "square": 2.5,
    "trine": 2.5,
    "sextile": 1.5,
}

# Прогрессии — ещё точнее: прогрессивное Солнце идёт градус в год.
PROGRESSION_ORBS = {
    "conjunction": 1.5,
    "opposition": 1.5,
    "square": 1.2,
    "trine": 1.2,
    "sextile": 1.0,
}

SYNASTRY_ORBS = {
    "conjunction": 7.0,
    "opposition": 7.0,
    "square": 6.0,
    "trine": 6.0,
    "sextile": 4.0,
}

# Чем медленнее планета, тем весомее её транзит.
PLANET_WEIGHT = {
    "pluto": 10, "neptune": 9, "uranus": 8, "saturn": 7, "jupiter": 6,
    "mars": 4, "sun": 3, "venus": 2, "mercury": 2, "moon": 1,
}

# В прогрессиях всё наоборот: за «день на год» медленные планеты стоят на
# месте, а смысл несут светила и быстрые планеты.
PROGRESSED_PLANETS = ["sun", "moon", "mercury", "venus", "mars"]
PROGRESSION_WEIGHT = {"sun": 10, "moon": 9, "mercury": 5, "venus": 5, "mars": 4}

HARMONIOUS = {"trine", "sextile"}
TENSE = {"square", "opposition"}

# Планеты, по которым читается совместимость в первую очередь.
SYNASTRY_CORE = {"sun", "moon", "venus", "mars"}


class ForecastError(Exception):
    pass


def _require(chart):
    if not chart:
        raise ForecastError("нужна натальная карта")
    if not natal.available():
        raise ForecastError("эфемериды недоступны")


def positions_for(date, hour=12.0):
    """Положения планет на указанную дату (UT)."""
    moment = dt.datetime(date.year, date.month, date.day,
                         int(hour), int(round((hour % 1) * 60)))
    return natal.compute_positions(natal.julian_day(moment))


# ---------------------------------------------------------------------------
# Транзиты
# ---------------------------------------------------------------------------

def transits(chart, on_date=None, include_fast=False, limit=10):
    """Активные транзиты к натальной карте на указанную дату."""
    _require(chart)
    on_date = on_date or dt.date.today()
    moving_keys = list(TRANSIT_PLANETS)
    if include_fast:
        moving_keys = FAST_TRANSIT_PLANETS + moving_keys

    positions = positions_for(on_date)
    found = natal.cross_aspects(
        positions, chart["positions"], orbs=TRANSIT_ORBS, bonus=1.0,
        moving_keys=moving_keys,
    )
    for item in found:
        item["weight"] = PLANET_WEIGHT.get(item["moving"], 1)
        item["lon"] = positions[item["moving"]]["lon"]
        item["retro"] = positions[item["moving"]]["retro"]
        item["house"] = (natal.house_of(item["lon"], chart["cusps"])
                         if chart["has_houses"] else None)
    found.sort(key=lambda item: (-item["weight"], item["orb"]))
    return {"date": on_date, "positions": positions, "aspects": found[:limit]}


def house_transits(chart, on_date=None):
    """По каким натальным домам идут медленные планеты."""
    _require(chart)
    if not chart["has_houses"]:
        return []
    on_date = on_date or dt.date.today()
    positions = positions_for(on_date)
    rows = []
    for key in ["jupiter", "saturn", "uranus", "neptune", "pluto"]:
        data = positions[key]
        rows.append({
            "planet": key,
            "lon": data["lon"],
            "retro": data["retro"],
            "house": natal.house_of(data["lon"], chart["cusps"]),
        })
    return rows


def window_transits(chart, start, days, step=1, moving_keys=None, limit=8):
    """Транзиты, действующие на отрезке времени, с днём максимума.

    Для одной даты хватает transits(), но в прогнозе на неделю или год
    важно другое: какой аспект станет точным и когда. Поэтому идём по
    отрезку с шагом step и для каждой тройки «планета-планета-аспект»
    запоминаем день, когда орб минимален.
    """
    _require(chart)
    best = {}
    for offset in range(0, days + 1, step):
        date = start + dt.timedelta(days=offset)
        report = transits(chart, date, include_fast=True, limit=60)
        for item in report["aspects"]:
            if moving_keys and item["moving"] not in moving_keys:
                continue
            key = (item["moving"], item["fixed"], item["aspect"])
            if key not in best or item["orb"] < best[key]["orb"]:
                item = dict(item)
                item["peak"] = date
                best[key] = item
    rows = sorted(best.values(), key=lambda item: (-item["weight"], item["orb"]))
    return rows[:limit]


def _elongation(moment):
    """Угол Луна-Солнце в момент времени: 0° — новолуние, 180° — полнолуние."""
    positions = natal.compute_positions(natal.julian_day(moment))
    return (positions["moon"]["lon"] - positions["sun"]["lon"]) % 360.0


def _refine_lunation(before, after, target):
    """Момент, когда угол Луна-Солнце проходит через target.

    Отрезок между двумя замерами делим пополам, пока не останется минута.
    Без этого событие приписывалось тем суткам, в которые его заметили:
    новолуние в 19:51 обнаруживалось на следующий полдень и попадало в
    календарь на сутки позже, чем случилось. На 2026 годе так съезжали
    11 лунаций из 25.
    """
    for _ in range(40):
        middle = before + (after - before) / 2
        if (after - before) <= dt.timedelta(minutes=1):
            break
        # приводим к отрезку [-180°, 180°) вокруг искомого значения
        shifted = (_elongation(middle) - target + 540.0) % 360.0 - 180.0
        if shifted < 0:
            before = middle
        else:
            after = middle
    return before + (after - before) / 2


def lunations(start, days):
    """Новолуния и полнолуния на отрезке: дата, вид, знак.

    Дата — это дата самого события по всемирному времени, а не те сутки,
    в которые наш перебор его заметил.
    """
    if not natal.available():
        return []
    events = []
    previous = None
    for offset in range(days + 1):
        date = start + dt.timedelta(days=offset)
        positions = positions_for(date)
        phase = (positions["moon"]["lon"] - positions["sun"]["lon"]) % 360.0
        if previous is not None:
            noon = dt.datetime(date.year, date.month, date.day, 12)
            kind = None
            if previous > 270.0 and phase < 90.0:
                kind, target = "new", 0.0
            elif previous < 180.0 <= phase:
                kind, target = "full", 180.0
            if kind:
                moment = _refine_lunation(noon - dt.timedelta(days=1), noon,
                                          target)
                exact = natal.compute_positions(natal.julian_day(moment))
                events.append({
                    "date": moment.date(),
                    "time": moment.strftime("%H:%M"),
                    "kind": kind,
                    "sign": exact["sun" if kind == "new" else "moon"]["sign"],
                })
        previous = phase
    return events


def format_transits(report, chart=None, limit=5):
    lines = ["⏳ <b>Транзиты на %s</b>" % report["date"].strftime("%d.%m.%Y"), ""]

    if not report["aspects"]:
        lines.append(
            "Точных транзитов сейчас нет. Это не «ничего не происходит» — "
            "просто внешнее давление минимально, и события зависят от вас."
        )
    for item in report["aspects"][:limit]:
        moving, fixed = ad.PLANETS[item["moving"]], ad.PLANETS[item["fixed"]]
        line = "%s <b>%s и %s %s</b> в %s — %s; речь %s." % (
            plain.aspect_mark(item["aspect"]), moving["name_ru"],
            plain.possessive(item["fixed"]), fixed["name_ru"],
            plain.ASPECT_PREPOSITIONAL[item["aspect"]],
            plain.aspect_text(item["aspect"]),
            plain.planet_about(item["moving"]),
        )
        if item["house"]:
            line += " Разворачивается это %s." % plain.house_in(item["house"])
        if item["exact_return"]:
            line += (" Это возвращение %s: круг замкнулся, начинается новый."
                     % moving["genitive"])
        lines.append(line)

    if chart is not None and chart.get("has_houses"):
        rows = house_transits(chart, report["date"])
        if rows:
            lines += ["", "<b>Где сейчас медленные планеты</b>"]
            for row in rows:
                lines.append("%s %s%s — %s." % (
                    ad.PLANETS[row["planet"]]["symbol"],
                    ad.PLANETS[row["planet"]]["name_ru"],
                    " ℞" if row["retro"] else "", plain.house_in(row["house"]),
                ))
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Вторичные прогрессии
# ---------------------------------------------------------------------------

def progressions(chart, on_date=None):
    """Вторичные прогрессии «день за год» на указанную дату."""
    _require(chart)
    on_date = on_date or dt.date.today()
    years = (on_date - chart["date"]).days / TROPICAL_YEAR
    if years < 0:
        raise ForecastError("дата раньше рождения")

    progressed_jd = chart["jd"] + years
    positions = natal.compute_positions(progressed_jd)
    if chart["has_houses"]:
        for data in positions.values():
            data["house"] = natal.house_of(data["lon"], chart["cusps"])

    # За «день на год» медленные планеты не сдвигаются, и их соединения с
    # собственными натальными позициями — пустой шум. Работают только
    # быстрые: прогрессивные Солнце, Луна, Меркурий, Венера и Марс.
    aspects = natal.cross_aspects(
        positions, chart["positions"], orbs=PROGRESSION_ORBS, bonus=0.5,
        moving_keys=PROGRESSED_PLANETS,
    )
    for item in aspects:
        item["weight"] = PROGRESSION_WEIGHT.get(item["moving"], 1)
    aspects.sort(key=lambda item: (-item["weight"], item["orb"]))

    natal_sun_sign = chart["positions"]["sun"]["sign"]
    return {
        "date": on_date,
        "years": years,
        "positions": positions,
        "aspects": aspects,
        "sun_changed": positions["sun"]["sign"] != natal_sun_sign,
        "natal_sun_sign": natal_sun_sign,
    }


def format_progressions(report, chart, limit=4):
    sun = report["positions"]["sun"]
    moon = report["positions"]["moon"]
    lines = [
        "🌱 <b>Прогрессии на %s</b>" % report["date"].strftime("%d.%m.%Y"),
        "«День за год»: %d-й день после рождения описывает нынешний год."
        % round(report["years"]),
        "",
        "%s <b>Солнце — %s</b>%s: %s." % (
            ad.PLANETS["sun"]["symbol"], natal.format_degree(sun["lon"]),
            (", %d дом" % sun["house"]) if sun.get("house") else "",
            plain.sign(sun["sign"]),
        ),
    ]
    if report["sun_changed"]:
        lines.append(
            "Знак сменился — с %s на %s. Это бывает раз-два за жизнь: меняется "
            "сама манера себя проявлять, привычное вдруг требует усилий, а "
            "трудное идёт легче." % (
                ad.SIGNS[report["natal_sun_sign"]]["genitive"],
                ad.SIGNS[sun["sign"]]["genitive"],
            )
        )
    else:
        lines.append("Знак тот же, что при рождении — резких переломов нет, "
                     "идёт доработка своего.")

    lines += [
        "",
        "%s <b>Луна — %s</b>%s: %s." % (
            ad.PLANETS["moon"]["symbol"], natal.format_degree(moon["lon"]),
            (", %d дом" % moon["house"]) if moon.get("house") else "",
            plain.sign(moon["sign"]),
        ),
        "Это настроение ближайших лет%s." % (
            ", и разворачивается оно %s" % plain.house_in(moon["house"])
            if moon.get("house") else ""),
    ]

    active = report["aspects"][:limit]
    if active:
        lines += ["", "<b>Что включилось в этом году</b>"]
        for item in active:
            moving = ad.PLANETS[item["moving"]]
            fixed = ad.PLANETS[item["fixed"]]
            lines.append("%s %s %s и %s %s в %s — %s." % (
                plain.aspect_mark(item["aspect"]), moving["symbol"],
                moving["name_ru"], plain.possessive(item["fixed"]),
                fixed["name_ru"], plain.ASPECT_PREPOSITIONAL[item["aspect"]],
                plain.aspect_text(item["aspect"]),
            ))
    else:
        lines += ["", "Резких внутренних переломов в этом году не видно."]

    if not chart["has_houses"]:
        lines += ["", "<i>Без времени рождения дома не считаются — только "
                      "знаки и аспекты.</i>"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Синастрия
# ---------------------------------------------------------------------------

def synastry(chart_a, chart_b):
    """Сравнение двух карт: взаимные аспекты и планеты в домах партнёра."""
    _require(chart_a)
    _require(chart_b)

    aspects = natal.cross_aspects(
        chart_b["positions"], chart_a["positions"],
        orbs=SYNASTRY_ORBS, bonus=1.0, static_fixed=False,
    )
    for item in aspects:
        core = (item["moving"] in SYNASTRY_CORE) + (item["fixed"] in SYNASTRY_CORE)
        item["weight"] = core * 10 + PLANET_WEIGHT.get(item["moving"], 1)
    aspects.sort(key=lambda item: (-item["weight"], item["orb"]))

    def in_houses(source, target):
        if not target["has_houses"]:
            return []
        rows = []
        for key in natal.PLANET_SEQUENCE:
            if key not in SYNASTRY_CORE:
                continue
            data = source["positions"][key]
            rows.append({"planet": key,
                         "house": natal.house_of(data["lon"], target["cusps"])})
        return rows

    harmonious = sum(1 for a in aspects if a["aspect"] in HARMONIOUS)
    tense = sum(1 for a in aspects if a["aspect"] in TENSE)
    conjunctions = sum(1 for a in aspects if a["aspect"] == "conjunction")

    return {
        "aspects": aspects,
        "b_in_a_houses": in_houses(chart_b, chart_a),
        "a_in_b_houses": in_houses(chart_a, chart_b),
        "harmonious": harmonious,
        "tense": tense,
        "conjunctions": conjunctions,
    }


def format_synastry(report, name_a, name_b, limit=5):
    name_a = name_a or "вы"
    name_b = name_b or "партнёр"
    lines = [
        "💫 <b>Совместимость: %s и %s</b>" % (name_a, name_b),
        "",
        "Связок, где планеты помогают друг другу, — %d; где спорят — %d; "
        "где сливаются в одну тему — %d."
        % (report["harmonious"], report["tense"], report["conjunctions"]),
    ]

    total = report["harmonious"] + report["tense"]
    if total:
        share = report["harmonious"] / total
        if share >= 0.65:
            verdict = ("Связь идёт легко: много поддержки и мало трения. "
                       "Риск такой пары — не в конфликтах, а в том, что "
                       "развитие останавливается, когда всё и так удобно.")
        elif share <= 0.35:
            verdict = ("Связь напряжённая: много трения. Это не приговор — "
                       "такие пары часто самые живые и меняющие обоих, но "
                       "лёгкой она не будет, и договариваться придётся вслух.")
        else:
            verdict = ("Баланс поддержки и трения — рабочее сочетание: есть "
                       "на чём держаться и есть из-за чего расти.")
        lines += ["", verdict]

    if report["aspects"]:
        lines += ["", "<b>Что между вами работает</b>"]
        for item in report["aspects"][:limit]:
            moving = ad.PLANETS[item["moving"]]
            fixed = ad.PLANETS[item["fixed"]]
            lines.append("%s %s (%s) и %s (%s) — %s." % (
                plain.aspect_mark(item["aspect"]), moving["name_ru"], name_b,
                fixed["name_ru"], name_a, plain.aspect_text(item["aspect"]),
            ))

    # имена не склоняем — «планеты Пётр в домах Ирина» читается плохо,
    # а правила склонения имён в общем виде не работают
    sections = (
        (report["b_in_a_houses"], "Партнёр (%s) задевает у вас" % name_b),
        (report["a_in_b_houses"], "Вы (%s) задеваете у партнёра" % name_a),
    )
    for rows, title in sections:
        if not rows:
            continue
        # Один дом может быть задет несколькими планетами — в перечислении
        # он тогда повторялся («…друзья, команда и планы и уединение,
        # страхи и подсознание»). И список из таких групп через запятую
        # нечитаем: каждая группа сама состоит из трёх слов через запятую,
        # поэтому выносим их в строки.
        seen, zones = set(), []
        for row in rows:
            if row["house"] in seen:
                continue
            seen.add(row["house"])
            zones.append(plain.house(row["house"]))
        lines += ["", "<b>%s:</b>" % title]
        lines += ["• %s" % zone for zone in zones]

    lines += [
        "",
        "<i>Это механика, а не приговор: пары с трудной картой живут вместе "
        "десятилетиями, а с лёгкой — расходятся.</i>",
    ]
    return "\n".join(lines)


