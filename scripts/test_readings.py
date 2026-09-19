# -*- coding: utf-8 -*-
"""Прогон движка консультаций: разбор дат, знаки, сборка всех раскладов.

Запуск: python scripts/test_readings.py
Нужен собранный vectorstore (для подстановки текстов из базы знаний).
"""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import forecast  # noqa: E402
import natal  # noqa: E402
import readings  # noqa: E402

TELEGRAM_LIMIT = 4000
ALLOWED_TAGS = {"b", "i", "code", "u", "s", "a", "pre"}


def check_dates(errors):
    cases = [
        ("15.03.1990", dt.date(1990, 3, 15), None),
        ("15/03/1990", dt.date(1990, 3, 15), None),
        ("15-03-1990", dt.date(1990, 3, 15), None),
        ("1990-03-15", dt.date(1990, 3, 15), None),
        ("15 марта 1990", dt.date(1990, 3, 15), None),
        ("15.03.1990 14:30", dt.date(1990, 3, 15), "14:30"),
        ("5.7.2001 в 9:05", dt.date(2001, 7, 5), "09:05"),
        ("  01.01.2000  ", dt.date(2000, 1, 1), None),
    ]
    for text, expected_date, expected_time in cases:
        birth, time_str = readings.parse_birth(text)
        if birth != expected_date or time_str != expected_time:
            errors.append("parse_birth(%r) -> (%s, %s), ожидалось (%s, %s)"
                          % (text, birth, time_str, expected_date, expected_time))

    for bad in ["", "вчера", "32.13.1990", "15.03", "1990", "15.03.2099", "привет"]:
        birth, _ = readings.parse_birth(bad)
        if birth is not None:
            errors.append("parse_birth(%r) не должен был распознаться -> %s" % (bad, birth))


def check_signs(errors):
    # Середины знаков не плавают никогда — их держим константами
    cases = [
        ((1, 5), "capricorn"), ((1, 30), "aquarius"), ((2, 10), "aquarius"),
        ((3, 5), "pisces"), ((4, 5), "aries"), ((5, 5), "taurus"),
        ((6, 5), "gemini"), ((7, 5), "cancer"), ((8, 5), "leo"),
        ((9, 5), "virgo"), ((10, 5), "libra"), ((11, 5), "scorpio"),
        ((12, 5), "sagittarius"), ((12, 31), "capricorn"),
    ]
    for (month, day), expected in cases:
        got = readings.sun_sign(dt.date(2000, month, day))
        if got != expected:
            errors.append("sun_sign(%02d.%02d) -> %s, ожидалось %s"
                          % (day, month, got, expected))

    # А вот границы плавают на сутки от года к году: Солнце входит в Овен
    # то 20, то 21 марта. Поэтому у границ сверяемся не с таблицей, а с
    # настоящим положением Солнца — той же проверкой, что делает бот
    if natal.available():
        for year in (1985, 2000, 2016):
            for month in range(1, 13):
                for day in (19, 20, 21, 22, 23):
                    when = dt.date(year, month, day)
                    real = forecast.positions_for(when)["sun"]["sign"]
                    if readings.sun_sign(when) != real:
                        errors.append(
                            "%s: знак %s, а Солнце на самом деле в %s"
                            % (when, readings.sun_sign(when), real))

    # каждый день года попадает ровно в один знак из двенадцати
    seen = set()
    day = dt.date(2001, 1, 1)
    while day.year == 2001:
        seen.add(readings.sun_sign(day))
        day += dt.timedelta(days=1)
    if len(seen) != 12:
        errors.append("за год встретилось %d знаков вместо 12" % len(seen))

    # дома солярной карты: 1-й дом всегда сам знак, 12 домов — 12 разных знаков
    for sign in readings.SIGN_ORDER:
        if readings.solar_house_sign(sign, 1) != sign:
            errors.append("solar_house_sign(%s, 1) != %s" % (sign, sign))
        houses = {readings.solar_house_sign(sign, h) for h in range(1, 13)}
        if len(houses) != 12:
            errors.append("solar_house_sign(%s, 1..12) даёт %d знаков" % (sign, len(houses)))


def check_html(text, where, errors):
    stack = []
    for match in re.finditer(r"<(/?)([a-z]+)[^>]*>", text):
        closing, tag = match.group(1), match.group(2)
        if tag not in ALLOWED_TAGS:
            errors.append("%s: недопустимый для Telegram тег <%s>" % (where, tag))
            continue
        if closing:
            if not stack or stack.pop() != tag:
                errors.append("%s: непарный тег </%s>" % (where, tag))
        else:
            stack.append(tag)
    if stack:
        errors.append("%s: незакрытые теги %s" % (where, stack))


def build_profiles():
    """Три уровня данных: только дата; дата+город; дата+время+город."""
    import natal

    profiles = [
        {"name": "Анна", "birth": dt.date(1990, 3, 15), "time": "14:30",
         "question": "стоит ли ждать этого человека"},
        {"name": "", "birth": dt.date(1975, 12, 22), "time": None, "question": ""},
        {"name": "<Макс>", "birth": dt.date(2003, 7, 23), "time": None,
         "question": "менять ли работу"},
    ]
    if not natal.available():
        print("! pyswisseph недоступен — натальные карты в раскладах не проверяются")
        return profiles

    moscow = natal.find_places("Москва")[0]
    vladivostok = natal.find_places("Владивосток")[0]
    full = {"name": "Ирина", "birth": dt.date(1990, 3, 15), "time": "14:30",
            "place": moscow, "question": "почему не складывается с партнёром"}
    full["chart"] = natal.build_chart(full["birth"], full["time"], full["place"])
    no_time = {"name": "Пётр", "birth": dt.date(1968, 11, 2), "time": None,
               "place": vladivostok, "question": ""}
    no_time["chart"] = natal.build_chart(no_time["birth"], None, no_time["place"])
    return profiles + [full, no_time]


def check_readings(errors, retriever):
    profiles = build_profiles()
    for profile in profiles:
        for sphere_key in readings.SPHERES:
            where = "%s/%s%s" % (sphere_key, profile["birth"],
                                 "/карта" if profile.get("chart") else "")
            try:
                messages = readings.build_reading(sphere_key, profile, retriever)
            except Exception as exc:
                errors.append("%s: расклад упал -> %r" % (where, exc))
                continue
            # разбор подробный, но не бесконечный: два-три сообщения,
            # и каждое обязано влезать в жёсткий лимит Telegram
            if not 1 <= len(messages) <= 3:
                errors.append("%s: сообщений %d, ожидалось от одного до трёх"
                              % (where, len(messages)))
            for number, message in enumerate(messages, 1):
                if len(message) > 4096:
                    errors.append("%s: сообщение %d длиной %d — Telegram его "
                                  "обрежет" % (where, number, len(message)))
                if readings.DISCLAIMER not in message:
                    errors.append("%s: в сообщении %d нет дисклеймера"
                                  % (where, number))
            joined = "\n".join(messages)
            for block in ("<b>Коротко</b>", "<b>Что в карте</b>", "<b>Карты</b>",
                          "<b>Ваш ход</b>", "<b>Чего не делать</b>",
                          "<b>Итог</b>"):
                if block not in joined:
                    errors.append("%s: в разборе нет блока %s" % (where, block))
            for filler in ("Тема «", "проявляется здесь", "Положение:", "Аспект:",
                           "направлена на сферу жизни"):
                if filler in joined:
                    errors.append("%s: в разбор попал шаблонный текст базы (%r)"
                                  % (where, filler))
            if len(joined) > 6000:
                errors.append("%s: разбор разросся до %d символов" % (where, len(joined)))
            if profile.get("chart"):
                if "по знаку Солнца" in joined:
                    errors.append("%s: при готовой карте включился солярный режим" % where)
                # «это ваш Асцендент» встречается только в самой строке
                # позиции; в оговорке «дома и Асцендент посчитать нельзя»
                # слово тоже есть, и по нему проверять нельзя
                asc_shown = "это ваш Асцендент" in joined
                if profile["chart"]["has_houses"] and not asc_shown:
                    errors.append("%s: в разборе по карте нет Асцендента" % where)
                if not profile["chart"]["has_houses"]:
                    if asc_shown:
                        errors.append("%s: Асцендент показан без времени рождения" % where)
                    # позиция планеты не должна утверждать дом: «— 9°30′ Рыб, 4 дом»
                    if re.search(r"\d+°\d+′\s+\S+,\s*\d+ дом", joined):
                        errors.append("%s: положение планеты в доме показано "
                                      "без времени рождения" % where)
            elif "по знаку Солнца" not in joined:
                errors.append("%s: без карты не сказано, что разбор идёт "
                              "по знаку Солнца" % where)
            for i, message in enumerate(messages):
                if not message.strip():
                    errors.append("%s: сообщение %d пустое" % (where, i))
                if len(message) > TELEGRAM_LIMIT:
                    errors.append("%s: сообщение %d длиной %d > %d"
                                  % (where, i, len(message), TELEGRAM_LIMIT))
                check_html(message, "%s msg%d" % (where, i), errors)

    # один и тот же клиент в один день получает тот же расклад, назавтра — другой
    profile = profiles[0]
    today = dt.date(2026, 9, 12)
    first = readings.draw_spread("love", profile, today)
    again = readings.draw_spread("love", profile, today)
    tomorrow = readings.draw_spread("love", profile, today + dt.timedelta(days=1))
    ids = lambda draw: [(d["card"]["id"], d["reversed"]) for d in draw]
    if ids(first) != ids(again):
        errors.append("расклад не воспроизводится для того же дня")
    if ids(first) == ids(tomorrow):
        errors.append("расклад не меняется на следующий день")
    if len({d["card"]["id"] for d in first}) != len(first):
        errors.append("в раскладе повторяются карты")

    other = dict(profile, birth=dt.date(1991, 3, 15))
    if ids(first) == ids(readings.draw_spread("love", other, today)):
        errors.append("у разных клиентов совпал расклад")
    if ids(first) == ids(readings.draw_spread("work", profile, today)):
        errors.append("у разных сфер совпал расклад")

    # темы: набор, уникальность команд и кнопок, распознавание по словам
    if len(readings.SPHERES) < 10:
        errors.append("тем всего %d — мало для такого сервиса" % len(readings.SPHERES))
    commands = [s["command"] for s in readings.SPHERES.values()]
    if len(commands) != len(set(commands)):
        errors.append("команды тем повторяются")
    popular = [k for k, s in readings.SPHERES.items() if s.get("popular")]
    if not 4 <= len(popular) <= 6:
        errors.append("частых тем %d — меню либо пустое, либо не влезет" % len(popular))
    for sphere_key, sphere in readings.SPHERES.items():
        if len(sphere["positions"]) != 4:
            errors.append("%s: позиций %d, ожидалось 4" % (sphere_key, len(sphere["positions"])))
        if not 1 <= sphere["house"] <= 12:
            errors.append("%s: дом вне диапазона" % sphere_key)

    for text, expected in [("любовь", "love"), ("про деньги", "wealth"),
                           ("переезд", "move"), ("страхи", "fears"),
                           ("дети", "children"), ("здоровье", "health"),
                           ("учёба", "study")]:
        got = readings.match_sphere(text)
        if got != expected:
            errors.append("«%s» -> тема %s, ожидалось %s" % (text, got, expected))
    for text in ["что значит карта Влюблённые", "Марс в Овне",
                 "расскажи про седьмой дом подробно", ""]:
        if readings.match_sphere(text):
            errors.append("«%s» ошибочно принято за запрос темы" % text)


def main():
    errors = []
    check_dates(errors)
    check_signs(errors)

    retriever = None
    try:
        from retrieval import Retriever
        retriever = Retriever()
    except Exception as exc:
        print("! Retriever недоступен (%s) — тексты из базы знаний не проверяются" % exc)

    check_readings(errors, retriever)

    print("Ошибок: %d" % len(errors))
    for error in errors[:40]:
        print(" -", error)

    if not errors:
        profile = {"name": "Анна", "birth": dt.date(1990, 3, 15), "time": "14:30",
                   "question": "стоит ли ждать этого человека"}
        print("\n--- пример: love ---")
        for message in readings.build_reading("love", profile, retriever):
            print(message)
            print("-" * 60)
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
