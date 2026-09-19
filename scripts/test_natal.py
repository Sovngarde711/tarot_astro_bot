# -*- coding: utf-8 -*-
"""Проверка натального модуля: геокодер, часовые пояса, эфемериды, дома.

Запуск: python scripts/test_natal.py

Положения планет сверяются с независимой библиотекой PyEphem, если она
установлена (`pip install ephem`) — две разные реализации не ошибутся
одинаково. Без неё эти проверки пропускаются.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import natal  # noqa: E402
import astrology_data as ad  # noqa: E402


def check_geocoder(errors):
    if not natal.geocoder_available():
        errors.append("геокодер недоступен (нет geonamescache)")
        return
    cases = [
        ("Москва", "Moscow", "RU", "Europe/Moscow"),
        ("москва", "Moscow", "RU", "Europe/Moscow"),
        ("Москва, Россия", "Moscow", "RU", "Europe/Moscow"),
        ("Санкт-Петербург", "Saint Petersburg", "RU", "Europe/Moscow"),
        ("Новосибирск", "Novosibirsk", "RU", "Asia/Novosibirsk"),
        ("Владивосток", "Vladivostok", "RU", "Asia/Vladivostok"),
        ("Алматы", "Almaty", "KZ", "Asia/Almaty"),
        ("Минск", "Minsk", "BY", "Europe/Minsk"),
        ("Berlin", "Berlin", "DE", "Europe/Berlin"),
    ]
    for query, name, country, timezone in cases:
        places = natal.find_places(query, limit=3)
        if not places:
            errors.append("геокодер не нашёл %r" % query)
            continue
        best = places[0]
        if best["name"] != name or best["country"] != country:
            errors.append("%r -> %s (%s), ожидалось %s (%s)"
                          % (query, best["name"], best["country"], name, country))
        elif best["timezone"] != timezone:
            errors.append("%r -> пояс %s, ожидалось %s"
                          % (query, best["timezone"], timezone))

    if natal.find_places("йцукенгшщз"):
        errors.append("геокодер нашёл несуществующий город")
    if natal.find_places("м"):
        errors.append("геокодер отвечает на слишком короткий запрос")


def check_timezones(errors):
    """Смещения должны быть историческими, а не сегодняшними."""
    moscow = natal.find_places("Москва")[0]
    novosibirsk = natal.find_places("Новосибирск")[0]
    cases = [
        (moscow, dt.date(1980, 7, 15), "14:30", "UTC+3"),   # декретное, СССР без DST
        (moscow, dt.date(1985, 1, 10), "03:00", "UTC+3"),   # зима
        (moscow, dt.date(1985, 7, 10), "03:00", "UTC+4"),   # летнее время введено в 1981
        (moscow, dt.date(2010, 7, 1), "12:00", "UTC+4"),    # последние годы с переводом
        (moscow, dt.date(2015, 7, 1), "12:00", "UTC+3"),    # постоянное зимнее
        (novosibirsk, dt.date(2020, 1, 1), "12:00", "UTC+7"),
    ]
    for place, date, time_str, expected in cases:
        _, label = natal.local_to_utc(date, time_str, place["timezone"])
        if label != expected:
            errors.append("%s %s %s -> %s, ожидалось %s"
                          % (place["name"], date, time_str, label, expected))

    # без времени берётся местный полдень
    utc, _ = natal.local_to_utc(dt.date(2015, 7, 1), None, moscow["timezone"])
    if utc.hour != 9:
        errors.append("полдень в Москве 2015 -> %s UTC, ожидалось 09:00" % utc.time())


def check_ephemeris(errors):
    if not natal.available():
        errors.append("pyswisseph недоступен — эфемериды не проверены")
        return

    # Солнце в стандартную эпоху J2000.0: тропическая долгота ~280.46°
    jd = natal.julian_day(dt.datetime(2000, 1, 1, 12, 0))
    sun = natal.compute_positions(jd)["sun"]
    if abs(sun["lon"] - 280.46) > 0.1:
        errors.append("Солнце на J2000.0 -> %.3f°, ожидалось ~280.46°" % sun["lon"])

    # Момент весеннего равноденствия 2000 года: Солнце в 0° Овна
    jd = natal.julian_day(dt.datetime(2000, 3, 20, 7, 35))
    sun = natal.compute_positions(jd)["sun"]
    if min(sun["lon"], 360.0 - sun["lon"]) > 0.05:
        errors.append("Солнце в равноденствие -> %.3f°, ожидалось ~0°" % sun["lon"])

    # Знаки и градусы согласованы между собой
    for longitude, sign, degrees in [(0.0, "aries", 0), (29.99, "aries", 29),
                                     (30.0, "taurus", 0), (185.5, "libra", 5),
                                     (359.9, "pisces", 29)]:
        if natal.sign_of(longitude) != sign:
            errors.append("sign_of(%.2f) -> %s, ожидалось %s"
                          % (longitude, natal.sign_of(longitude), sign))
        if int(natal.degree_in_sign(longitude)) != degrees:
            errors.append("degree_in_sign(%.2f) -> %.2f, ожидалось %d"
                          % (longitude, natal.degree_in_sign(longitude), degrees))

    # Ретроградность: Меркурий ретрограден примерно пятую часть года
    jd0 = natal.julian_day(dt.datetime(2020, 1, 1, 12, 0))
    retro_days = sum(1 for day in range(365)
                     if natal.compute_positions(jd0 + day)["mercury"]["retro"])
    if not 40 <= retro_days <= 100:
        errors.append("Меркурий ретрограден %d дней за 2020 год — неправдоподобно"
                      % retro_days)
    # Солнце и Луна не бывают ретроградными
    for key in ("sun", "moon"):
        if any(natal.compute_positions(jd0 + day * 30)[key]["retro"] for day in range(12)):
            errors.append("%s оказалось ретроградным" % key)


def check_cross_ephem(errors):
    """Сверка с независимой реализацией (PyEphem)."""
    try:
        import ephem
    except ImportError:
        print("  (ephem не установлен — перекрёстная сверка пропущена)")
        return
    if not natal.available():
        return

    bodies = {"sun": ephem.Sun, "moon": ephem.Moon, "mercury": ephem.Mercury,
              "venus": ephem.Venus, "mars": ephem.Mars, "jupiter": ephem.Jupiter,
              "saturn": ephem.Saturn, "uranus": ephem.Uranus,
              "neptune": ephem.Neptune, "pluto": ephem.Pluto}
    moments = [dt.datetime(1969, 7, 20, 20, 17), dt.datetime(1990, 3, 15, 11, 30),
               dt.datetime(2026, 9, 12, 0, 0)]
    for moment in moments:
        positions = natal.compute_positions(natal.julian_day(moment))
        when = moment.strftime("%Y/%m/%d %H:%M:%S")
        for key, body in bodies.items():
            obj = body()
            # эпоха даты, а не J2000: астрология работает в эклиптике момента,
            # иначе вся карта уезжает на величину прецессии
            obj.compute(when, when)
            reference = float(ephem.Ecliptic(obj, epoch=when).lon) * 180.0 / 3.141592653589793
            got = positions[key]["lon"]
            delta = abs((got - reference + 180.0) % 360.0 - 180.0)
            # Луна быстрая, у неё допуск шире; остальные — доли градуса
            tolerance = 0.15 if key == "moon" else 0.05
            if delta > tolerance:
                errors.append("%s на %s: swisseph %.3f°, ephem %.3f°, расхождение %.3f°"
                              % (key, moment, got, reference, delta))


def check_houses(errors):
    if not natal.available():
        return
    moscow = natal.find_places("Москва")[0]
    utc, _ = natal.local_to_utc(dt.date(1990, 3, 15), "14:30", moscow["timezone"])
    jd = natal.julian_day(utc)
    cusps, asc, mc, system = natal.compute_houses(jd, moscow["lat"], moscow["lon"])

    if len(cusps) != 12:
        errors.append("куспидов %d вместо 12" % len(cusps))
    if abs((cusps[0] - asc + 180.0) % 360.0 - 180.0) > 0.001:
        errors.append("куспид 1-го дома (%.3f°) не совпал с ASC (%.3f°)" % (cusps[0], asc))
    if abs((cusps[9] - mc + 180.0) % 360.0 - 180.0) > 0.001:
        errors.append("куспид 10-го дома (%.3f°) не совпал с MC (%.3f°)" % (cusps[9], mc))
    if "Плацидус" not in system:
        errors.append("для Москвы выбрана система %s вместо Плацидуса" % system)

    # противоположные дома ровно напротив друг друга
    for index in range(6):
        opposite = abs((cusps[index] - cusps[index + 6] + 180.0) % 360.0 - 180.0)
        if abs(opposite - 180.0) > 0.001:
            errors.append("дома %d и %d не оппозиционны (%.3f°)"
                          % (index + 1, index + 7, opposite))

    # куспиды идут по кругу без разрывов, каждая точка попадает ровно в один дом
    for step in range(0, 360, 7):
        house = natal.house_of(float(step), cusps)
        if not 1 <= house <= 12:
            errors.append("house_of(%d) -> %s" % (step, house))
    for index, cusp in enumerate(cusps):
        house = natal.house_of(cusp + 0.01, cusps)
        if house != index + 1:
            errors.append("точка сразу за куспидом %d попала в дом %d" % (index + 1, house))

    # за полярным кругом Плацидус не определён — должен включиться Порфирий
    _, _, _, polar_system = natal.compute_houses(jd, 78.2, 15.6)  # Лонгйир
    if "Порфирий" not in polar_system:
        errors.append("на 78° широты выбран %s вместо Порфирия" % polar_system)


def check_aspects(errors):
    if not natal.available():
        return
    moscow = natal.find_places("Москва")[0]
    chart = natal.build_chart(dt.date(1990, 3, 15), "14:30", moscow)

    for aspect in chart["aspects"]:
        limit = natal.ASPECT_ORBS[aspect["aspect"]]
        if aspect["p1"] in natal.LUMINARIES or aspect["p2"] in natal.LUMINARIES:
            limit += natal.LUMINARY_BONUS
        if aspect["orb"] > limit + 1e-9:
            errors.append("аспект %s-%s вне орбиса: %.2f > %.2f"
                          % (aspect["p1"], aspect["p2"], aspect["orb"], limit))
        if aspect["p1"] == aspect["p2"]:
            errors.append("планета в аспекте сама с собой: %s" % aspect["p1"])
        # у каждого аспекта должна быть запись в базе знаний
        first, second = natal.aspect_doc_ids(aspect)
        if not (first.startswith("combo_aspect_") and second.startswith("combo_aspect_")):
            errors.append("неверные id записей для аспекта %s" % aspect)

    pairs = [(a["p1"], a["p2"]) for a in chart["aspects"]]
    if len(pairs) != len(set(pairs)):
        errors.append("одна пара планет попала в аспекты дважды")
    orbs = [a["orb"] for a in chart["aspects"]]
    if orbs != sorted(orbs):
        errors.append("аспекты не отсортированы по точности")


def check_chart_levels(errors):
    if not natal.available():
        return
    moscow = natal.find_places("Москва")[0]

    full = natal.build_chart(dt.date(1990, 3, 15), "14:30", moscow)
    if not full["has_houses"]:
        errors.append("карта со временем не получила дома")
    if not all(full["positions"][key].get("house") for key in natal.PLANET_SEQUENCE):
        errors.append("не всем планетам проставлен дом")

    partial = natal.build_chart(dt.date(1990, 3, 15), None, moscow)
    if partial["has_houses"]:
        errors.append("карта без времени получила дома")
    if any(partial["positions"][key].get("house") for key in natal.PLANET_SEQUENCE):
        errors.append("в карте без времени проставлены дома")

    # медленные планеты от времени суток почти не зависят, Луна — зависит
    for key in ("jupiter", "saturn", "pluto"):
        delta = abs(full["positions"][key]["lon"] - partial["positions"][key]["lon"])
        if delta > 0.05:
            errors.append("%s сместился на %.3f° из-за времени суток" % (key, delta))
    moon_delta = abs(full["positions"]["moon"]["lon"] - partial["positions"]["moon"]["lon"])
    if moon_delta < 0.1:
        errors.append("Луна не сдвинулась при смене времени (%.3f°)" % moon_delta)

    # день, когда Луна меняет знак, должен помечаться как неоднозначный
    flagged = 0
    for day in range(28):
        chart = natal.build_chart(dt.date(1990, 3, 1) + dt.timedelta(days=day), None, moscow)
        flagged += bool(chart["moon_ambiguous"])
    if not 8 <= flagged <= 16:
        errors.append("за месяц Луна сменила знак %d раз — ожидалось около 12" % flagged)

    # вывод карты не должен разваливаться и превышать лимит Telegram
    text = natal.format_chart(full)
    if len(text) > 4000:
        errors.append("карта длиной %d символов не влезает в сообщение" % len(text))
    for key in natal.PLANET_SEQUENCE:
        if ad.PLANETS[key]["name_ru"] not in text:
            errors.append("в выводе карты нет планеты %s" % key)


def main():
    errors = []
    check_geocoder(errors)
    check_timezones(errors)
    check_ephemeris(errors)
    check_cross_ephem(errors)
    check_houses(errors)
    check_aspects(errors)
    check_chart_levels(errors)

    print("Ошибок: %d" % len(errors))
    for error in errors[:40]:
        print(" -", error)

    if natal.available() and not errors:
        moscow = natal.find_places("Москва")[0]
        chart = natal.build_chart(dt.date(1990, 3, 15), "14:30", moscow)
        print("\n--- пример карты ---")
        print(natal.format_chart(chart))
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
