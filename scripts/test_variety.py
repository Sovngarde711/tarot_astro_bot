# -*- coding: utf-8 -*-
"""Проверка, что ответы разные и по делу.

Запуск: python scripts/test_variety.py

Два риска у такого бота. Первый — <b>однообразие</b>: шаблон один, и
разным людям приходит примерно одно и то же, отличаясь парой слов. Второй,
более неприятный, — <b>несоответствие</b>: текст выглядит персональным, но
на самом деле не связан ни с картой клиента, ни с выбранной темой.

Здесь проверяется и то, и другое: сначала измеряется различность ответов
по многим сочетаниям «клиент × тема × дата», затем каждый ответ сверяется
с исходными данными — та ли планета, тот ли знак, тот ли дом, те ли
позиции расклада, тот ли совет.
"""
import datetime as dt
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import astrology_data as ad  # noqa: E402
import natal  # noqa: E402
import plain  # noqa: E402
import readings  # noqa: E402

TODAY = dt.date(2026, 9, 20)

# Клиенты подобраны так, чтобы отличались по всему, что влияет на разбор:
# знак Солнца, год рождения, город (а значит часовой пояс и координаты),
# наличие времени рождения.
CLIENTS = [
    ("Анна", dt.date(1990, 3, 15), "14:30", "Москва"),
    ("Борис", dt.date(1978, 7, 2), "06:05", "Владивосток"),
    ("Вера", dt.date(2001, 11, 23), "21:40", "Алматы"),
    ("Глеб", dt.date(1985, 1, 9), None, "Минск"),
    ("Дина", dt.date(1996, 5, 30), "12:00", "Екатеринбург"),
    ("Егор", dt.date(1969, 9, 8), "03:15", "Berlin"),
]


def build_clients():
    clients = []
    for name, birth, clock, city in CLIENTS:
        profile = {"name": name, "birth": birth, "time": clock, "question": ""}
        places = natal.find_places(city)
        if places and natal.available():
            profile["place"] = places[0]
            try:
                profile["chart"] = natal.build_chart(birth, clock, places[0])
            except Exception as exc:
                print("! карта для %s не построилась: %r" % (name, exc))
        clients.append(profile)
    # седьмой — вовсе без города: солярный режим тоже должен работать
    clients.append({"name": "Жанна", "birth": dt.date(1993, 2, 14),
                    "time": None, "question": ""})
    return clients


def text_of(profile, sphere_key, on_date=TODAY):
    return "\n".join(readings.build_reading(sphere_key, profile, None, on_date))


def cards_of(profile, sphere_key, on_date=TODAY):
    draw = readings.draw_spread(sphere_key, profile, on_date)
    return tuple((item["card"]["id"], item["reversed"]) for item in draw)


# ---------------------------------------------------------------------------
# 1. Различность
# ---------------------------------------------------------------------------

def check_variety(errors, clients, report):
    texts, cards = {}, {}
    for client in clients:
        for sphere_key in readings.SPHERES:
            key = (client["name"], sphere_key)
            texts[key] = text_of(client, sphere_key)
            cards[key] = cards_of(client, sphere_key)

    total = len(texts)
    report.append("Сочетаний «клиент × тема»: %d" % total)

    # полностью одинаковых ответов быть не должно
    duplicates = [text for text, count in Counter(texts.values()).items() if count > 1]
    if duplicates:
        errors.append("одинаковых ответов: %d пар" % len(duplicates))
    report.append("Уникальных ответов: %d из %d" % (len(set(texts.values())), total))

    # расклады тоже должны отличаться
    unique_cards = len(set(cards.values()))
    report.append("Уникальных раскладов: %d из %d" % (unique_cards, total))
    if unique_cards < total * 0.98:
        errors.append("расклады повторяются: уникальных %d из %d" % (unique_cards, total))

    # у разных клиентов одна тема не должна давать одни и те же карты
    for sphere_key in readings.SPHERES:
        same = [cards[(client["name"], sphere_key)] for client in clients]
        if len(set(same)) != len(same):
            errors.append("в теме «%s» у разных клиентов совпали расклады" % sphere_key)

    # у одного клиента разные темы не должны давать один и тот же расклад
    for client in clients:
        same = [cards[(client["name"], key)] for key in readings.SPHERES]
        if len(set(same)) != len(same):
            errors.append("у клиента %s совпали расклады разных тем" % client["name"])

    # смена даты меняет расклад, повтор в тот же день — нет
    client = clients[0]
    if cards_of(client, "love") != cards_of(client, "love"):
        errors.append("расклад не воспроизводится в пределах дня")
    if cards_of(client, "love") == cards_of(client, "love", TODAY + dt.timedelta(days=1)):
        errors.append("расклад не изменился на следующий день")

    # насколько ответы близки текстуально: считаем долю общих строк
    overlaps = []
    keys = sorted(texts)
    for i, first in enumerate(keys):
        for second in keys[i + 1:]:
            a = set(texts[first].split("\n"))
            b = set(texts[second].split("\n"))
            if a and b:
                overlaps.append(len(a & b) / float(len(a | b)))
    average = sum(overlaps) / len(overlaps)
    report.append("Средняя доля совпадающих строк между ответами: %.1f%%"
                  % (average * 100))
    if average > 0.35:
        errors.append("ответы слишком похожи друг на друга: %.1f%% общих строк"
                      % (average * 100))


# ---------------------------------------------------------------------------
# 2. Соответствие данным клиента
# ---------------------------------------------------------------------------

def check_relevance(errors, clients, report):
    checked = 0
    for client in clients:
        chart = client.get("chart")
        for sphere_key, sphere in readings.SPHERES.items():
            text = text_of(client, sphere_key)
            where = "%s/%s" % (client["name"], sphere_key)
            checked += 1

            # тема названа и позиции расклада — именно её
            if sphere["title"] not in text:
                errors.append("%s: в ответе нет названия темы" % where)
            for position in sphere["positions"]:
                if position not in text:
                    errors.append("%s: пропала позиция расклада «%s»"
                                  % (where, position))

            # поле темы — нужный дом, названный и номером, и по смыслу
            if ad.HOUSES[sphere["house"]]["name_ru"].lower() not in text:
                errors.append("%s: не назван дом темы" % where)
            if plain.house(sphere["house"]) not in text:
                errors.append("%s: не сказано, за что этот дом отвечает" % where)

            if chart:
                check_chart_match(errors, where, text, chart, sphere)
            else:
                sign_key = readings.sun_sign(client["birth"])
                phrase = "Солнце %s" % plain.sign_in(sign_key, ad.SIGNS[sign_key])
                if phrase not in text:
                    errors.append("%s: солнечный знак в тексте не тот" % where)
                if "по знаку Солнца" not in text:
                    errors.append("%s: не сказано, что разбор идёт по знаку "
                                  "Солнца" % where)

            check_action(errors, where, text, client, sphere_key)

    report.append("Проверено ответов на соответствие данным: %d" % checked)


def check_chart_match(errors, where, text, chart, sphere):
    """Планета темы в тексте должна стоять там же, где в карте.

    Проверяем не вхождение фразы целиком, а факты внутри нужной строки:
    формулировки меняются, а положение планеты — нет.
    """
    planet_key = sphere["planet"]
    data = chart["positions"][planet_key]

    # строка про планету темы узнаётся по подписи вроде «Как вы любите»
    line = next((row for row in text.splitlines()
                 if sphere["planet_label"] in row), None)
    if line is None:
        errors.append("%s: в разборе нет строки про планету темы" % where)
        return

    if ad.PLANETS[planet_key]["name_ru"] not in line:
        errors.append("%s: планета темы не названа" % where)
    if plain.sign_in(data["sign"], ad.SIGNS[data["sign"]]) not in line:
        errors.append("%s: знак планеты темы не совпадает с картой" % where)
    if data.get("house") and plain.house_ordinal_in(data["house"]) not in line:
        errors.append("%s: дом планеты темы (%d) не указан"
                      % (where, data["house"]))

    # чужие знаки в этой строке появляться не должны
    for sign_key, sign_data in ad.SIGNS.items():
        if sign_key == data["sign"]:
            continue
        if plain.sign_in(sign_key, sign_data) in line:
            errors.append("%s: в строке планеты темы указан чужой знак (%s)"
                          % (where, sign_data["name_ru"]))

    if chart["has_houses"]:
        asc_key = natal.sign_of(chart["asc"])
        asc_line = next((row for row in text.splitlines()
                         if "это ваш Асцендент" in row), None)
        if asc_line is None:
            errors.append("%s: Асцендент не показан" % where)
        elif ad.SIGNS[asc_key]["name_ru"] not in asc_line:
            errors.append("%s: знак Асцендента не совпадает с картой" % where)


def check_action(errors, where, text, client, sphere_key):
    """Совет «Конкретно» должен соответствовать теме и мастям расклада."""
    draw = readings.draw_spread(sphere_key, client, TODAY)
    suit = readings.dominant_suit(draw) or "major"
    expected = readings.ACTIONS[sphere_key][suit]
    if expected not in text:
        errors.append("%s: шаг «Конкретно» не соответствует раскладу "
                      "(ожидался вариант для масти «%s»)" % (where, suit))

    # шаг из другой темы попасть не должен
    for other_key, actions in readings.ACTIONS.items():
        if other_key == sphere_key:
            continue
        for other_step in actions.values():
            if other_step in text:
                errors.append("%s: попал совет из темы «%s»" % (where, other_key))
                return


# ---------------------------------------------------------------------------
# 3. Сроки: даты должны быть настоящими и в будущем
# ---------------------------------------------------------------------------

DATE = re.compile(r"(\d{1,2}) (январ|феврал|март|апрел|ма|июн|июл|август|сентябр"
                  r"|октябр|ноябр|декабр)\w*")
MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август",
          "сентябр", "октябр", "ноябр", "декабр"]


def check_timing(errors, clients, report):
    found = 0
    for client in clients:
        if not client.get("chart"):
            continue
        for sphere_key in readings.SPHERES:
            text = text_of(client, sphere_key)
            if "<b>Когда</b>" not in text:
                errors.append("%s/%s: нет блока «Когда»" % (client["name"], sphere_key))
                continue
            block = text.split("<b>Когда</b>")[1].split("\n\n")[0]
            for day, month in DATE.findall(block):
                found += 1
                number = MONTHS.index(month) + 1
                year = TODAY.year if number >= TODAY.month else TODAY.year + 1
                try:
                    date = dt.date(year, number, int(day))
                except ValueError:
                    errors.append("%s: несуществующая дата %s %s"
                                  % (client["name"], day, month))
                    continue
                if not TODAY <= date <= TODAY + dt.timedelta(days=120):
                    errors.append("%s/%s: дата %s вне разумного горизонта"
                                  % (client["name"], sphere_key, date))
    report.append("Проверено конкретных дат в блоке «Когда»: %d" % found)
    if found < 5:
        errors.append("дат в прогнозах почти нет — блок «Когда» пустой")


def astro_block(text):
    """Только блок «Что в карте» — астрологическая часть без карт Таро."""
    if "<b>Что в карте</b>\n" not in text:
        return ""
    return text.split("<b>Что в карте</b>\n")[1].split("\n\n")[0]


def check_astro_changes(errors, clients, report):
    """Астрологическая часть обязана зависеть от карты и от темы.

    Расклад Таро различается всегда — он тянется от seed. А вот если бы
    астрологический блок был декорацией, он выглядел бы одинаково у всех,
    и заметить это по одному примеру невозможно.
    """
    blocks = {}
    for client in clients:
        for sphere_key in readings.SPHERES:
            blocks[(client["name"], sphere_key)] = astro_block(text_of(client, sphere_key))

    # одна тема, разные клиенты — разные астро-блоки
    for sphere_key in readings.SPHERES:
        seen = [blocks[(client["name"], sphere_key)] for client in clients]
        if len(set(seen)) != len(seen):
            errors.append("тема «%s»: астрологический блок повторился у разных "
                          "клиентов" % sphere_key)

    # один клиент, разные темы — тоже разные, включая солярный режим:
    # у «работы» и «пути» общий десятый дом, и когда-то этого хватало,
    # чтобы блоки совпали слово в слово
    for client in clients:
        seen = [blocks[(client["name"], key)] for key in readings.SPHERES]
        if len(set(seen)) != len(seen):
            errors.append("клиент %s: астрологические блоки разных тем совпали"
                          % client["name"])

    if len(set(blocks.values())) != len(blocks):
        errors.append("астрологические блоки повторяются: уникальных %d из %d"
                      % (len(set(blocks.values())), len(blocks)))

    # сдвиг даты рождения меняет карту, а значит и разбор
    base = clients[0]
    shifted = dict(base, birth=dt.date(1986, 8, 4))
    if base.get("place") and natal.available():
        shifted["chart"] = natal.build_chart(shifted["birth"], base.get("time"),
                                             base["place"])
        if astro_block(text_of(shifted, "love")) == blocks[(base["name"], "love")]:
            errors.append("другая дата рождения дала тот же астрологический разбор")

    # а вот время суток обращения не должно менять саму карту
    same_day = text_of(base, "love", TODAY)
    if astro_block(same_day) != blocks[(base["name"], "love")]:
        errors.append("астрологический блок нестабилен в пределах одного дня")

    report.append("Астрологические блоки: уникальных %d из %d"
                  % (len(set(blocks.values())), len(blocks)))


def show_examples(clients):
    """Наглядно: одна тема у разных людей и разные темы у одного."""
    print("\n=== Одна тема «любовь» у трёх клиентов ===")
    for client in clients[:3]:
        text = text_of(client, "love")
        short = text.split("<b>Коротко</b>\n")[1].split("\n")[0]
        chart_line = text.split("<b>Что в карте</b>\n")[1].split("\n")[0]
        print("\n%s (%s):" % (client["name"], client["birth"]))
        print("  коротко: %s" % short[:150])
        print("  карта:   %s" % re.sub(r"<[^>]+>", "", chart_line)[:150])

    print("\n=== Три темы у одного клиента (%s) ===" % clients[0]["name"])
    for sphere_key in ("love", "wealth", "health"):
        text = text_of(clients[0], sphere_key)
        block = text.split("<b>Конкретно.</b> ")[1].split("\n")[0]
        print("\n%s: %s" % (readings.SPHERES[sphere_key]["title"], block[:150]))


def main():
    errors, report = [], []
    clients = build_clients()
    with_charts = sum(1 for client in clients if client.get("chart"))
    report.append("Клиентов: %d (с натальной картой: %d)" % (len(clients), with_charts))

    check_variety(errors, clients, report)
    check_relevance(errors, clients, report)
    check_astro_changes(errors, clients, report)
    check_timing(errors, clients, report)

    for line in report:
        print(line)
    print("\nОшибок: %d" % len(errors))
    for error in errors[:30]:
        print(" -", error)

    if not errors:
        show_examples(clients)
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
