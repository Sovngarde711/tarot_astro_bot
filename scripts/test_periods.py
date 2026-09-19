# -*- coding: utf-8 -*-
"""Проверка прогнозов на неделю и на год.

Запуск: python scripts/test_periods.py
"""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import forecast  # noqa: E402
import natal  # noqa: E402
import periods  # noqa: E402

TELEGRAM_LIMIT = 4000
ALLOWED_TAGS = {"b", "i", "code", "u", "s", "a", "pre"}


def check_messages(messages, where, errors, max_total=3600):
    for index, message in enumerate(messages):
        if len(message) > TELEGRAM_LIMIT:
            errors.append("%s: сообщение %d длиной %d" % (where, index, len(message)))
        stack = []
        for match in re.finditer(r"<(/?)([a-z]+)[^>]*>", message):
            closing, tag = match.group(1), match.group(2)
            if tag not in ALLOWED_TAGS:
                errors.append("%s: недопустимый тег <%s>" % (where, tag))
                continue
            if closing:
                if not stack or stack.pop() != tag:
                    errors.append("%s: непарный тег </%s>" % (where, tag))
            else:
                stack.append(tag)
        if stack:
            errors.append("%s: незакрытые теги %s" % (where, stack))
    joined = "\n".join(messages)
    if len(joined) > max_total:
        errors.append("%s: прогноз разросся до %d символов" % (where, len(joined)))
    for filler in ("Положение:", "Тема «", "проявляется здесь"):
        if filler in joined:
            errors.append("%s: шаблонный текст базы (%r)" % (where, filler))
    return joined


def check_week(errors, profile, with_chart):
    start = dt.date(2026, 9, 14)  # понедельник
    messages = periods.build_week(profile, None, start)
    where = "неделя/%s" % ("карта" if with_chart else "без карты")
    joined = check_messages(messages, where, errors)

    for block in ("<b>Коротко</b>", "<b>По дням</b>", "<b>Что делать на этой неделе</b>"):
        if block not in joined:
            errors.append("%s: нет блока %s" % (where, block))

    # ровно семь дней, в правильном порядке и с правильными датами
    for offset in range(7):
        date = start + dt.timedelta(days=offset)
        label = "%s, %s" % (periods.WEEKDAYS[date.weekday()].capitalize(),
                            date.strftime("%d.%m"))
        if label not in joined:
            errors.append("%s: нет дня %s" % (where, label))
    days_block = joined.split("По дням")[1]
    names = "|".join(day.capitalize() for day in periods.WEEKDAYS)
    found = re.search(names, days_block)
    if not found or found.group(0) != "Понедельник":
        errors.append("%s: неделя начинается не с первого дня, а с «%s»"
                      % (where, found.group(0) if found else "ничего"))

    if with_chart:
        if "Астрофон недели" not in joined:
            errors.append("%s: нет астрофона" % where)
    elif "Натальной карты у меня нет" not in joined:
        errors.append("%s: не сказано, что натальной карты нет" % where)

    # расклад держится за клиента и за неделю
    again = periods.build_week(profile, None, start)
    if again != messages:
        errors.append("%s: неделя не воспроизводится" % where)
    next_week = periods.build_week(profile, None, start + dt.timedelta(days=7))
    if next_week == messages:
        errors.append("%s: следующая неделя совпала с текущей" % where)


def check_year(errors, profile, with_chart):
    start = dt.date(2026, 9, 12)
    messages = periods.build_year(profile, None, start)
    where = "год/%s" % ("карта" if with_chart else "без карты")
    joined = check_messages(messages, where, errors, max_total=4200)

    for block in ("<b>Коротко</b>", "Тема года", "<b>По месяцам</b>",
                  "<b>Что с этим делать</b>"):
        if block not in joined:
            errors.append("%s: нет блока %s" % (where, block))

    months = periods.year_months(start)
    if len(months) != 12:
        errors.append("%s: месяцев %d вместо 12" % (where, len(months)))
    if months[0].month != start.month or months[0].year != start.year:
        errors.append("%s: год начинается не с текущего месяца" % where)
    if (months[-1].year, months[-1].month) != (2027, 8):
        errors.append("%s: последний месяц %s, ожидался август 2027" % (where, months[-1]))
    for date in months:
        if periods.MONTHS_SHORT[date.month - 1].capitalize() not in joined:
            errors.append("%s: нет месяца %s" % (where, periods.MONTHS[date.month - 1]))

    # ключевой и неудобный месяц не должны совпадать — это противоречие
    advice = joined.split("Что с этим делать")[-1]
    key = re.search(r"Ключевой месяц — (\w+)", advice)
    hard = re.search(r"Самый неудобный — (\w+)", advice)
    if key and hard and key.group(1) == hard.group(1):
        errors.append("%s: ключевой и неудобный месяц совпали (%s)"
                      % (where, key.group(1)))

    if with_chart:
        if "Астрофон года" not in joined:
            errors.append("%s: нет астрофона" % where)
        # в астрофоне не должно быть одной планеты во всех строках
        block = joined.split("Астрофон года")[1].split("<b>По месяцам")[0]
        planets = re.findall(r"<b>(\w+) и ", block)
        if len(planets) != len(set(planets)):
            errors.append("%s: одна планета занимает несколько строк астрофона" % where)
    elif "Натальной карты у меня нет" not in joined:
        errors.append("%s: не сказано, что натальной карты нет" % where)


def check_window(errors, chart):
    start = dt.date(2026, 9, 14)
    rows = forecast.window_transits(chart, start, days=6, step=1, limit=10)
    for item in rows:
        if not start <= item["peak"] <= start + dt.timedelta(days=6):
            errors.append("день максимума %s вне окна" % item["peak"])
        limit = forecast.TRANSIT_ORBS[item["aspect"]]
        if item["moving"] in natal.LUMINARIES or item["fixed"] in natal.LUMINARIES:
            limit += 1.0
        if item["orb"] > limit + 1e-9:
            errors.append("транзит окна вне орбиса: %.2f > %.2f" % (item["orb"], limit))
    pairs = [(item["moving"], item["fixed"], item["aspect"]) for item in rows]
    if len(pairs) != len(set(pairs)):
        errors.append("одна и та же пара попала в окно дважды")

    # лунации: за месяц ровно одно новолуние и одно полнолуние
    events = forecast.lunations(dt.date(2026, 9, 1), 29)
    kinds = [event["kind"] for event in events]
    if kinds.count("new") != 1 or kinds.count("full") != 1:
        errors.append("за 29 дней найдено %d новолуний и %d полнолуний"
                      % (kinds.count("new"), kinds.count("full")))
    for event in events:
        positions = forecast.positions_for(event["date"])
        phase = (positions["moon"]["lon"] - positions["sun"]["lon"]) % 360.0
        target = 0.0 if event["kind"] == "new" else 180.0
        delta = min(abs(phase - target), 360.0 - abs(phase - target))
        if delta > 13.0:  # Луна проходит ~13° в сутки, шаг поиска — сутки
            errors.append("%s %s: фаза %.1f°, ожидалась около %.0f°"
                          % (event["kind"], event["date"], phase, target))


def main():
    errors = []
    if not natal.available():
        print("pyswisseph недоступен — прогнозы проверить нельзя")
        return 1

    moscow = natal.find_places("Москва")[0]
    with_chart = {"name": "Ирина", "birth": dt.date(1990, 3, 15), "time": "14:30",
                  "place": moscow}
    with_chart["chart"] = natal.build_chart(
        with_chart["birth"], with_chart["time"], with_chart["place"])
    without = {"name": "", "birth": dt.date(1975, 12, 22), "time": None}

    check_week(errors, with_chart, True)
    check_week(errors, without, False)
    check_year(errors, with_chart, True)
    check_year(errors, without, False)
    check_window(errors, with_chart["chart"])

    print("Ошибок: %d" % len(errors))
    for error in errors[:40]:
        print(" -", error)

    if not errors:
        print("\n--- неделя ---")
        for message in periods.build_week(with_chart, None, dt.date(2026, 9, 14)):
            print(message)
        print("\n--- год ---")
        for message in periods.build_year(with_chart, None, dt.date(2026, 9, 12)):
            print(message)
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
