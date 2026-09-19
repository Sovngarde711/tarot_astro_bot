# -*- coding: utf-8 -*-
"""Точность расчётов — против внешних авторитетных источников.

Запуск: python scripts/test_accuracy.py

Эталоны в этом файле взяты не из нашего же кода, иначе проверка ничего не
значила бы. Источники:

* <b>NASA/JPL Horizons</b> (ssd.jpl.nasa.gov) — видимые геоцентрические
  эклиптические долготы на эпоху даты. Это первоисточник, по которому
  сверяют эфемериды все остальные;
* <b>Fred Espenak, astropixels.com</b> — таблицы новолуний и полнолуний,
  посчитанные по эфемеридам JPL; бывший сотрудник NASA GSFC, его таблицы
  затмений и лунаций — общепринятая справка;
* <b>A. E. Waite, The Pictorial Key to the Tarot (1910)</b> — первоисточник
  колоды Уэйта-Смита: состав, нумерация, расположение Силы и
  Справедливости;
* <b>IANA tzdata</b> — история часовых поясов;
* <b>GeoNames</b> — координаты городов.

Если после правок эти проверки разойдутся, значит, разошлись с реальностью,
а не с нашим представлением о ней.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import astrology_data as ad  # noqa: E402
import forecast  # noqa: E402
import natal  # noqa: E402
import readings  # noqa: E402
import tarot_data as td  # noqa: E402

GREENWICH = {"name": "Гринвич", "country": "GB", "lat": 51.4779, "lon": 0.0,
             "timezone": "Europe/London"}


def gap(first, second):
    """Разница долгот с учётом перехода через 360°."""
    difference = abs(first - second) % 360.0
    return min(difference, 360.0 - difference)


# ---------------------------------------------------------------------------
# Положения планет: NASA/JPL Horizons
# ---------------------------------------------------------------------------

# Видимые геоцентрические эклиптические долготы на эпоху даты, градусы.
# Запрошены у JPL Horizons (CENTER='500@399', QUANTITIES='31').
JPL = {
    dt.datetime(2000, 1, 1, 12, 0): {
        "sun": 280.3689092, "moon": 223.3237860,
        "mars": 327.9632921, "saturn": 40.3956366,
    },
}


def check_planets_against_jpl(errors):
    """Наши долготы против первоисточника — NASA/JPL Horizons."""
    if not natal.available():
        errors.append("эфемериды недоступны — проверять нечего")
        return
    for moment, reference in JPL.items():
        positions = natal.compute_positions(natal.julian_day(moment))
        for key, expected in reference.items():
            difference = gap(positions[key]["lon"], expected)
            # секунда дуги — заведомо больше, чем расходятся эфемеридные
            # модели между собой, и заведомо меньше, чем любая ошибка в
            # часовых поясах, юлианской дате или системе координат
            if difference > 1.0 / 3600.0:
                errors.append(
                    "%s на %s: у нас %.6f°, у JPL %.6f° — расхождение %.1f\""
                    % (key, moment, positions[key]["lon"], expected,
                       difference * 3600))


# ---------------------------------------------------------------------------
# Лунации: таблицы Эспенака
# ---------------------------------------------------------------------------

# Новолуния и полнолуния 2026 года, время всемирное (astropixels.com).
LUNATIONS = [
    ("new", dt.date(2026, 9, 11), "03:27"),
    ("full", dt.date(2026, 9, 26), "16:49"),
    ("new", dt.date(2026, 10, 10), "15:50"),
    ("full", dt.date(2026, 10, 26), "04:12"),
    ("new", dt.date(2026, 11, 9), "07:02"),
    ("full", dt.date(2026, 11, 24), "14:53"),
]


def check_lunations(errors):
    """Даты и время лунаций против опубликованных таблиц.

    Раньше событие записывалось на те сутки, в которые перебор его
    заметил: новолуние в 19:51 попадало в календарь следующим днём. Из
    25 лунаций 2026 года так съезжали 11.
    """
    if not natal.available():
        return
    found = forecast.lunations(dt.date(2026, 9, 1), days=95)
    ours = {(item["kind"], item["date"]): item.get("time")
            for item in found}
    for kind, day, clock in LUNATIONS:
        if (kind, day) not in ours:
            errors.append("%s %s не найдено (есть: %s)"
                          % (kind, day, sorted(key[1] for key in ours)))
            continue
        got = ours[(kind, day)]
        if not got:
            errors.append("%s %s: время не посчитано" % (kind, day))
            continue
        difference = abs(
            (dt.datetime.strptime(got, "%H:%M")
             - dt.datetime.strptime(clock, "%H:%M")).total_seconds()) / 60
        if difference > 5:
            errors.append("%s %s: у нас %s, в таблице %s — разница %.0f минут"
                          % (kind, day, got, clock, difference))


# ---------------------------------------------------------------------------
# Знак Солнца и ретроградность — против самих эфемерид
# ---------------------------------------------------------------------------

def check_sun_sign_on_cusps(errors):
    """Знак на стыке берётся из расчёта, а не из усреднённой таблицы.

    Границы знаков плавают на сутки: Солнце входит в Овен то 20, то 21
    марта. Проверка по ста годам когда-то дала 47 расхождений.
    """
    if not natal.available():
        return
    problems = 0
    for year in (1900, 1950, 1985, 2000, 2016, 2030):
        for month in range(1, 13):
            for day in range(18, 25):
                when = dt.date(year, month, day)
                real = forecast.positions_for(when)["sun"]["sign"]
                if readings.sun_sign(when) != real:
                    problems += 1
                    if problems <= 5:
                        errors.append(
                            "%s: знак %s, а Солнце в %s"
                            % (when, readings.sun_sign(when), real))
    if problems > 5:
        errors.append("...всего расхождений по знаку Солнца: %d" % problems)


def check_retrograde(errors):
    """Пометка «ретроградна» против фактического движения планеты."""
    if not natal.available():
        return
    for birth in (dt.date(1985, 7, 23), dt.date(2000, 1, 1),
                  dt.date(2026, 9, 19)):
        chart = natal.build_chart(birth, "12:00", GREENWICH)
        before = natal.build_chart(birth - dt.timedelta(days=2), "12:00",
                                   GREENWICH)
        after = natal.build_chart(birth + dt.timedelta(days=2), "12:00",
                                  GREENWICH)
        for key in chart["positions"]:
            if key in ("sun", "moon") or key not in before["positions"]:
                continue
            moved = ((after["positions"][key]["lon"]
                      - before["positions"][key]["lon"] + 540) % 360) - 180
            if bool(chart["positions"][key]["retro"]) != (moved < 0):
                errors.append("%s %s: пометка ретроградности не совпала с "
                              "движением (%+.3f°)" % (birth, key, moved))


# ---------------------------------------------------------------------------
# Часовые пояса и координаты
# ---------------------------------------------------------------------------

# Задокументированные факты истории времяисчисления (база IANA tzdata)
TIMEZONES = [
    ("Europe/Moscow", dt.date(1990, 3, 15), "14:30", 3,
     "Москва зимой 1990 — декретное время"),
    ("Europe/Moscow", dt.date(1990, 7, 15), "14:30", 4,
     "Москва летом 1990 — летнее время"),
    ("Europe/Moscow", dt.date(2012, 1, 15), "12:00", 4,
     "Россия 2011-2014 — круглогодичное летнее"),
    ("Europe/Moscow", dt.date(2015, 1, 15), "12:00", 3,
     "после отмены в октябре 2014"),
    ("Asia/Novosibirsk", dt.date(2015, 1, 15), "12:00", 6,
     "Новосибирск до июля 2016"),
    ("Asia/Novosibirsk", dt.date(2017, 1, 15), "12:00", 7,
     "Новосибирск после июля 2016"),
    ("America/New_York", dt.date(2006, 3, 12), "12:00", -5,
     "США: до 2007 в середине марта ещё зимнее время"),
    ("America/New_York", dt.date(2007, 3, 12), "12:00", -4,
     "США: с 2007 летнее время начинается раньше"),
    ("Europe/Lisbon", dt.date(1994, 1, 15), "12:00", 1,
     "Португалия 1992-1996 жила по среднеевропейскому времени"),
]


def check_timezones(errors):
    if not natal.available():
        return
    for zone, day, clock, expected, why in TIMEZONES:
        place = {"name": "тест", "country": "XX", "lat": 55.0, "lon": 37.0,
                 "timezone": zone}
        got = natal.build_chart(day, clock, place)["offset"]
        # бот печатает типографский минус, сравниваем по числу
        number = int(got.replace("UTC", "").replace("−", "-").replace("+", "")
                     or 0)
        if number != expected:
            errors.append("%s: получено %s, ожидалось UTC%+d"
                          % (why, got, expected))


# Координаты из GeoNames
CITIES = [
    ("Москва", 55.75, 37.62), ("Санкт-Петербург", 59.94, 30.31),
    ("Новосибирск", 55.03, 82.92), ("Владивосток", 43.13, 131.91),
    ("Алматы", 43.25, 76.95), ("Киев", 50.45, 30.52),
]


def check_cities(errors):
    if not natal.geocoder_available():
        return
    for name, lat, lon in CITIES:
        found = natal.find_places(name, limit=1)
        if not found:
            errors.append("город %s не найден" % name)
            continue
        place = found[0]
        if abs(place["lat"] - lat) > 0.2 or abs(place["lon"] - lon) > 0.2:
            errors.append("%s: у нас %.2f, %.2f — в GeoNames %.2f, %.2f"
                          % (name, place["lat"], place["lon"], lat, lon))


# ---------------------------------------------------------------------------
# Дома
# ---------------------------------------------------------------------------

def check_houses(errors):
    """Согласованность домов: Асцендент, MC и противоположные куспиды."""
    if not natal.available():
        return
    place = {"name": "Москва", "country": "RU", "lat": 55.75, "lon": 37.62,
             "timezone": "Europe/Moscow"}
    chart = natal.build_chart(dt.date(1990, 3, 15), "14:30", place)
    cusps = chart["cusps"]
    if len(cusps) != 12:
        errors.append("куспидов %d вместо двенадцати" % len(cusps))
        return
    if gap(chart["asc"], cusps[0]) > 0.01:
        errors.append("Асцендент не совпал с куспидом первого дома")
    if gap(chart["mc"], cusps[9]) > 0.01:
        errors.append("MC не совпал с куспидом десятого дома")
    for index in range(6):
        if abs(gap(cusps[index], cusps[index + 6]) - 180.0) > 0.01:
            errors.append("дома %d и %d расходятся не на 180°"
                          % (index + 1, index + 7))
    # за полярным кругом Плацидус не определён — там другая система
    polar = {"name": "Мурманск", "country": "RU", "lat": 68.97, "lon": 33.08,
             "timezone": "Europe/Moscow"}
    arctic = natal.build_chart(dt.date(1990, 3, 15), "14:30", polar)
    if not arctic or not arctic.get("has_houses"):
        errors.append("за полярным кругом карта не построилась")
    elif "орфири" not in str(arctic.get("house_system", "")):
        errors.append("за полярным кругом не помечен переход на Порфирия: %r"
                      % arctic.get("house_system"))


# ---------------------------------------------------------------------------
# Колода: Уэйт, 1910
# ---------------------------------------------------------------------------

def check_deck_matches_waite(errors):
    cards = td.ALL_CARDS
    majors = [card for card in cards if card["arcana"] == "major"]
    minors = [card for card in cards if card["arcana"] != "major"]

    if len(cards) != 78:
        errors.append("в колоде %d карт вместо 78" % len(cards))
    if len(majors) != 22:
        errors.append("старших арканов %d вместо 22" % len(majors))
    if len(minors) != 56:
        errors.append("младших арканов %d вместо 56" % len(minors))
    if sorted(card["number"] for card in majors) != list(range(22)):
        errors.append("старшие арканы пронумерованы не от 0 до 21")

    by_number = {card["number"]: card["name_ru"] for card in majors}
    # У Уэйта Сила — восьмая, Справедливость — одиннадцатая (в марсельской
    # традиции наоборот). Это опознавательный знак именно этой колоды
    if by_number.get(8) != "Сила":
        errors.append("VIII у Уэйта — Сила, а у нас %r" % by_number.get(8))
    if by_number.get(11) != "Справедливость":
        errors.append("XI у Уэйта — Справедливость, а у нас %r"
                      % by_number.get(11))
    if by_number.get(0) != "Шут" or by_number.get(21) != "Мир":
        errors.append("0 должен быть Шут, XXI — Мир")

    suits = {}
    for card in minors:
        suits.setdefault(card.get("suit"), []).append(card)
    if sorted(suits) != ["cups", "pentacles", "swords", "wands"]:
        errors.append("масти: %s" % sorted(suits))
    for suit, group in suits.items():
        if len(group) != 14:
            errors.append("в масти %s %d карт вместо 14" % (suit, len(group)))
        names = " ".join(card["name_ru"] for card in group)
        for rank in ("Туз", "Паж", "Рыцарь", "Королева", "Король"):
            if rank not in names:
                errors.append("в масти %s нет ранга %s" % (suit, rank))


# ---------------------------------------------------------------------------
# Соответствие текстов бота его же расчётам
# ---------------------------------------------------------------------------

def check_promises_match_settings(errors):
    """Бот обещает в /about узкие орбисы — проверяем, что так и есть."""
    import bot as B

    values = list(forecast.TRANSIT_ORBS.values())
    if min(values) < 1.4 or max(values) > 3.1:
        errors.append("в /about обещаны орбисы 1,5-3°, а в коде %.1f-%.1f°"
                      % (min(values), max(values)))

    about = B.ABOUT_METHOD
    if "Плацидус" not in about:
        errors.append("в /about не названа система домов")
    if "Порфири" not in about:
        errors.append("в /about не сказано про замену системы за полярным кругом")
    # прогрессивные ASC и MC мы обещали не показывать
    chart = None
    if natal.available():
        place = {"name": "Москва", "country": "RU", "lat": 55.75,
                 "lon": 37.62, "timezone": "Europe/Moscow"}
        chart = natal.build_chart(dt.date(1990, 3, 15), "14:30", place)
    if chart:
        report = forecast.progressions(chart)
        text = forecast.format_progressions(report, chart)
        for forbidden in ("прогрессивный Асцендент", "прогрессивный MC",
                          "прогрессивного Асцендента"):
            if forbidden in text:
                errors.append("в прогрессиях показан %s, хотя обещано "
                              "обратное" % forbidden)


def check_rulership_is_consistent(errors):
    """Система управителей должна быть одна, а не смесь двух."""
    rulers = {key: sign["ruler"] for key, sign in ad.SIGNS.items()}
    modern = {"scorpio": "pluto", "aquarius": "uranus", "pisces": "neptune"}
    classic = {"scorpio": "mars", "aquarius": "saturn", "pisces": "jupiter"}
    if not (all(rulers.get(k) == v for k, v in modern.items())
            or all(rulers.get(k) == v for k, v in classic.items())):
        errors.append("управители — смесь традиционной и современной систем: %s"
                      % {k: rulers.get(k) for k in modern})
    for key, sign in ad.SIGNS.items():
        if sign["ruler"] not in ad.PLANETS:
            errors.append("у знака %s управитель вне списка планет: %s"
                          % (key, sign["ruler"]))
    # выбранная система должна быть названа клиенту
    import bot as B
    if all(rulers.get(k) == v for k, v in modern.items()):
        if "современн" not in B.ABOUT_METHOD:
            errors.append("используются современные управители, но в /about "
                          "об этом не сказано")


def main():
    errors = []
    for check in (check_planets_against_jpl, check_lunations,
                  check_sun_sign_on_cusps, check_retrograde,
                  check_timezones, check_cities, check_houses,
                  check_deck_matches_waite, check_promises_match_settings,
                  check_rulership_is_consistent):
        try:
            check(errors)
        except Exception as failure:
            errors.append("%s упала: %r" % (check.__name__, failure))

    print("Ошибок: %d" % len(errors))
    for error in errors[:40]:
        print(" -", error)
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
