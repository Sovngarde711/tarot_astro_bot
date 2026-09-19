# -*- coding: utf-8 -*-
"""Проверка понятности ответов и обязательного дисклеймера.

Запуск: python scripts/test_plain.py

Две вещи, которые легко сломать незаметно:

1. <b>Дисклеймер.</b> Он обязан быть в конце каждого сообщения, а не
   только в разборе: сообщения пересылают по одному, и человек должен
   видеть, что это за метод, в том сообщении, которое читает.
2. <b>Понятность.</b> У каждой карты есть человеческая формулировка в
   data/tarot_plain.py. Если карта выпадет из таблицы, бот молча
   вернётся к языку таролога — а именно от него мы и уходили.
"""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import natal  # noqa: E402
import periods  # noqa: E402
import readings  # noqa: E402
import tarot_data as td  # noqa: E402
import tarot_plain as tp  # noqa: E402

PLACE = {"name": "Москва", "country": "RU", "lat": 55.7522, "lon": 37.6156,
         "timezone": "Europe/Moscow"}
TODAY = dt.date(2026, 9, 13)


def profile_with_chart():
    birth = dt.date(1990, 3, 15)
    chart = None
    if natal.available():
        try:
            chart = natal.build_chart(birth, "14:30", PLACE)
        except Exception:
            chart = None
    return {"name": "Анна", "birth": birth, "time": "14:30", "place": PLACE,
            "chart": chart}


def visible(text):
    return re.sub(r"<[^>]+>", "", text)


# ---------------------------------------------------------------------------
# Таблица понятных формулировок
# ---------------------------------------------------------------------------

def check_table_covers_deck(errors):
    """Ни одна карта не должна остаться без человеческой формулировки."""
    for card in td.ALL_CARDS:
        for upside_down in (False, True):
            phrase = tp.plain(card["id"], upside_down)
            side = "перевёрнутая" if upside_down else "прямая"
            if not phrase:
                errors.append("%s (%s): нет понятной формулировки"
                              % (card["name_ru"], side))
                continue
            if phrase[:1].isupper():
                errors.append("%s (%s): фраза начинается с заглавной — она "
                              "подставляется в середину предложения"
                              % (card["name_ru"], side))
            if phrase.endswith("."):
                errors.append("%s (%s): фраза с точкой на конце — точку "
                              "ставит шаблон" % (card["name_ru"], side))
            if len(phrase) > 130:
                errors.append("%s (%s): фраза длиной %d — в строку дня не "
                              "поместится" % (card["name_ru"], side, len(phrase)))


def check_table_speaks_plainly(errors):
    """В понятных фразах не должно быть слов, которые надо объяснять."""
    jargon = ("аркан", "масть", "транзит", "ретроград", "аспект", "асцендент",
              "секстиль", "тригон", "квадратур", "соединени", "архетип",
              "энергетик", "вибрац", "кармич", "эгрегор", "чакр")
    for card in td.ALL_CARDS:
        for upside_down in (False, True):
            phrase = tp.plain(card["id"], upside_down).lower()
            for word in jargon:
                if word in phrase:
                    errors.append("%s: в понятной фразе жаргон «%s»"
                                  % (card["name_ru"], word))


def check_weights(errors):
    """Вес карты — то, по чему выбирается удачный и трудный день."""
    for card in td.ALL_CARDS:
        for upside_down in (False, True):
            value = tp.weight(card["id"], upside_down)
            if value not in (-1, 0, 1):
                errors.append("%s: странный вес %r" % (card["name_ru"], value))

    # опорные точки: если кто-то переставит знаки, это заметит тест
    expectations = [
        ("major_19", False, 1, "Солнце прямое — благоприятная карта"),
        ("major_16", False, -1, "Башня прямая — трудная карта"),
        ("major_15", False, -1, "Дьявол прямой — трудная карта"),
        ("major_15", True, 1, "Дьявол перевёрнутый — освобождение"),
        ("minor_swords_8", True, 1, "Восьмёрка Мечей наоборот — освобождение"),
        ("minor_cups_10", True, -1, "Десятка Кубков наоборот — разлад дома"),
        ("minor_swords_10", False, -1, "Десятка Мечей прямая — тяжёлый конец"),
    ]
    for card_id, upside_down, expected, why in expectations:
        got = tp.weight(card_id, upside_down)
        if got != expected:
            errors.append("%s: вес %d, ожидался %d" % (why, got, expected))


# ---------------------------------------------------------------------------
# Дисклеймер
# ---------------------------------------------------------------------------

def check_disclaimer_text(errors):
    """Дисклеймер должен быть конкретным, а не «это просто развлечение»."""
    text = visible(readings.DISCLAIMER).lower()
    for must in ("астрология", "таро", "научн", "решение"):
        if must not in text:
            errors.append("в дисклеймере нет слова «%s»" % must)
    if len(visible(readings.DISCLAIMER)) < 120:
        errors.append("дисклеймер слишком короткий, чтобы что-то объяснить")


def check_disclaimer_in_every_message(errors):
    """Разбор, неделя и год — дисклеймер в каждом сообщении, не в последнем."""
    profile = profile_with_chart()
    batches = [
        ("разбор", readings.build_reading("love", profile, None, TODAY)),
        ("неделя", periods.build_week(profile, None, TODAY)),
        ("год", periods.build_year(profile, None, TODAY)),
    ]
    for where, messages in batches:
        if not messages:
            errors.append("%s: ни одного сообщения" % where)
            continue
        for number, message in enumerate(messages, 1):
            if readings.DISCLAIMER not in message:
                errors.append("%s: в сообщении %d нет дисклеймера"
                              % (where, number))
            if not message.rstrip().endswith("</i>"):
                errors.append("%s: сообщение %d кончается не дисклеймером: "
                              "…%s" % (where, number,
                                       visible(message).rstrip()[-60:]))
            if len(message) > 4096:
                errors.append("%s: сообщение %d длиной %d — Telegram обрежет"
                              % (where, number, len(message)))


def check_disclaimer_in_other_answers(errors):
    """Карта дня, натальная карта, транзиты и ответ из базы — тоже с оговоркой."""
    import bot as B

    sent = []
    saved_api = B.api_call
    B.api_call = lambda method, **params: sent.append((method, params)) or {"ok": True}
    try:
        profile = profile_with_chart()
        B.PROFILES[4242] = profile

        cases = [("карта дня", lambda: B.handle_random(4242))]
        if profile.get("chart"):
            cases.append(("натальная карта", lambda: B.deliver_chart(4242)))
            cases.append(("транзиты", lambda: B.deliver_transits(4242)))
            cases.append(("прогрессии", lambda: B.deliver_progressions(4242)))

        for where, call in cases:
            sent.clear()
            call()
            texts = [params.get("text", "") for method, params in sent
                     if method == "sendMessage"]
            if not texts:
                errors.append("%s: бот ничего не отправил" % where)
                continue
            if readings.DISCLAIMER not in texts[-1]:
                errors.append("%s: ответ без дисклеймера" % where)

        for name in ("WELCOME_TEXT", "HELP_TEXT", "ABOUT_METHOD"):
            if readings.DISCLAIMER not in getattr(B, name):
                errors.append("%s: нет дисклеймера" % name)
    finally:
        B.api_call = saved_api
        B.PROFILES.pop(4242, None)


# ---------------------------------------------------------------------------
# Понятность самих ответов
# ---------------------------------------------------------------------------

def check_reading_speaks_plainly(errors):
    """В разборе должны стоять человеческие фразы карт, а не только значения."""
    profile = profile_with_chart()
    for sphere_key in ("love", "work", "health"):
        text = visible("\n".join(
            readings.build_reading(sphere_key, profile, None, TODAY)))
        draw = readings.draw_spread(sphere_key, profile, TODAY)
        for item in draw:
            phrase = readings.plain_card(item)
            # в тексте фраза может стоять и с заглавной — она подаётся
            # и в середине предложения, и самостоятельным предложением
            shown = phrase in text or readings.as_sentence(phrase) in text
            if phrase and not shown:
                errors.append("%s: понятная фраза карты «%s» не попала в разбор"
                              % (sphere_key, item["card"]["name_ru"]))
        if "Коротко" not in text:
            errors.append("%s: в разборе нет блока «Коротко» — перевода на "
                          "обычный язык" % sphere_key)
        for block in ("Итог", "Ваш ход", "Чего не делать"):
            if block not in text:
                errors.append("%s: в выводе нет блока «%s»" % (sphere_key, block))


def check_no_repeats(errors):
    """Ни заголовок, ни значение карты не должны встречаться дважды.

    Так уже было: позиция расклада называлась «Что делать», и точно так
    же назывался блок вывода — с той же картой и тем же текстом. Человек
    читает это как сбой, и он прав.
    """
    profile = profile_with_chart()
    for sphere_key in readings.SPHERES:
        messages = readings.build_reading(sphere_key, profile, None, TODAY)
        text = "\n".join(messages)
        body = visible(text)
        draw = readings.draw_spread(sphere_key, profile, TODAY)

        # заголовки блоков: <b>…</b> в начале строки
        heads = re.findall(r"^<b>([^<]+)</b>$", text, re.M)
        seen = set()
        for head in heads:
            name = head.strip().rstrip(".:")
            if name in seen:
                errors.append("%s: заголовок «%s» встречается дважды"
                              % (sphere_key, name))
            seen.add(name)

        # названия позиций расклада не должны совпадать с заголовками блоков
        for position in readings.SPHERES[sphere_key]["positions"]:
            if position.strip().rstrip(".:") in seen:
                errors.append("%s: название позиции «%s» совпало с заголовком "
                              "блока" % (sphere_key, position))

        # понятная фраза карты и её классическое значение — по одному разу
        for item in draw:
            phrase = readings.plain_card(item)
            shown = body.count(phrase) + body.count(readings.as_sentence(phrase))
            if shown > 1:
                errors.append("%s: фраза карты «%s» повторена %d раза"
                              % (sphere_key, item["card"]["name_ru"], shown))

            meaning = readings.lower_first(readings._meaning(item, 200))
            head = readings.after_colon(meaning)[:60]
            if head and body.count(head) > 1:
                errors.append("%s: значение карты «%s» повторено"
                              % (sphere_key, item["card"]["name_ru"]))

        # и ни одна строка длиннее пятидесяти знаков не повторяется дословно
        lines = [line.strip() for line in body.splitlines()
                 if len(line.strip()) > 50]
        for line in lines:
            if lines.count(line) > 1 and readings.DISCLAIMER.find(line[:40]) < 0:
                errors.append("%s: строка повторяется дословно — «%s…»"
                              % (sphere_key, line[:60]))
                break


def check_forecasts_have_no_repeats(errors):
    """В неделе и году день (месяц) объясняется один раз.

    Раньше один и тот же день пересказывался трижды: в «Коротко», в
    списке дней и в совете на неделю.
    """
    profile = profile_with_chart()
    for where, messages in (
            ("неделя", periods.build_week(profile, None, TODAY)),
            ("год", periods.build_year(profile, None, TODAY))):
        body = visible("\n".join(messages))
        lines = [line.strip() for line in body.splitlines()
                 if len(line.strip()) > 45]
        for line in lines:
            if lines.count(line) > 1:
                errors.append("%s: строка повторяется дословно — «%s…»"
                              % (where, line[:60]))
                break

        for card_id in set(re.findall(r"[А-ЯЁ][а-яё]+ (?:Жезлов|Кубков|Мечей|"
                                      r"Пентаклей)", body)):
            if body.count(card_id) > 1:
                errors.append("%s: карта «%s» названа %d раза"
                              % (where, card_id, body.count(card_id)))
                break


def check_week_days_are_whole(errors):
    """Строки дней не обрываются многоточием и не теряют смысл."""
    profile = profile_with_chart()
    text = visible("\n".join(periods.build_week(profile, None, TODAY)))
    days = text.split("По дням")[1].split("Астрофон")[0]
    for line in days.splitlines():
        line = line.strip()
        if not line or "🔻" not in line and "—" not in line:
            continue
        if line.endswith("…"):
            errors.append("строка дня оборвана многоточием: %s" % line[-50:])


def check_best_day_is_really_good(errors):
    """День «для важного» не может выпасть на трудную карту.

    Регрессия из жизни: раньше лучшим днём назначалась первая
    неперевёрнутая карта, и однажды это оказался Дьявол.
    """
    profile = profile_with_chart()
    for offset in range(0, 120, 7):
        start = TODAY + dt.timedelta(days=offset)
        draw = readings.draw_cards(periods.week_positions(start),
                                   ["week"] + readings.client_seed(profile)
                                   + [start.isoformat()])
        advice = visible(periods._week_advice(draw))
        match = re.search(r"Для важного — ([А-Яа-я]+)\.", advice)
        if not match:
            continue
        day = match.group(1)
        # день в совете стоит со строчной буквы, в подписи позиции — с
        # заглавной, поэтому сравниваем без учёта регистра
        chosen = next((item for item in draw
                       if item["position"].lower().startswith(day.lower())), None)
        if chosen is None:
            errors.append("%s: день «%s» не из этой недели" % (start, day))
        elif readings.card_weight(chosen) <= 0:
            errors.append("%s: для важного предложен день с трудной картой — "
                          "%s" % (start, chosen["card"]["name_ru"]))

        careful = re.search(r"Осторожнее — ([А-Яа-я]+)\.", advice)
        if careful:
            day = careful.group(1)
            chosen = next((item for item in draw
                           if item["position"].lower().startswith(day.lower())),
                          None)
            if chosen is not None and readings.card_weight(chosen) >= 0:
                errors.append("%s: осторожничать предложено в день с хорошей "
                              "картой — %s" % (start, chosen["card"]["name_ru"]))


def check_explanations_present(errors):
    """Каждый блок один раз объясняет, что он вообще означает."""
    profile = profile_with_chart()
    reading = visible("\n".join(
        readings.build_reading("love", profile, None, TODAY)))
    if "классические значения карт" not in reading:
        errors.append("в блоке «Карты» не объяснено, что это за значения")
    if profile.get("chart") and "карте рождения" not in reading:
        errors.append("в блоке «Что в карте» нет объяснения, откуда он взялся")

    week = visible("\n".join(periods.build_week(profile, None, TODAY)))
    if "настрой дня" not in week:
        errors.append("в прогнозе на неделю не объяснены кружки у дней")
    if profile.get("chart") and "не карты, а небо" not in week:
        errors.append("в прогнозе не объяснено, что такое астрофон")


def main():
    errors = []
    for check in (check_table_covers_deck, check_table_speaks_plainly,
                  check_weights, check_disclaimer_text,
                  check_disclaimer_in_every_message,
                  check_disclaimer_in_other_answers,
                  check_reading_speaks_plainly, check_no_repeats,
                  check_forecasts_have_no_repeats,
                  check_week_days_are_whole,
                  check_best_day_is_really_good, check_explanations_present):
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
