# -*- coding: utf-8 -*-
"""Проверка русского языка в том, что бот выдаёт клиенту.

Запуск: python scripts/test_language.py

Текст собирается из кусков, и склейка легко даёт брак, который человек
замечает сразу, а тесты на логику — никогда: «Асцендент в Льве» вместо «во
Льве», «это про глубина, контроль», «отвечает за то, что перемены»,
перечисления без союза, заглавная буква после двоеточия, двойные пробелы.
Здесь ловится именно это — на всех темах, всех прогнозах и всех
двенадцати знаках, а не на одном показательном примере.
"""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import astrology_data as ad  # noqa: E402
import forecast  # noqa: E402
import natal  # noqa: E402
import periods  # noqa: E402
import plain  # noqa: E402
import readings  # noqa: E402

TAGS = re.compile(r"<[^>]+>")

# Механическая пунктуация: то, что выдаёт склейку шаблонов с головой.
TYPOGRAPHY = [
    (re.compile(r"  +"), "двойной пробел"),
    (re.compile(r"\s+[,.;:]"), "пробел перед знаком препинания"),
    (re.compile(r"[,;:]{2,}"), "сдвоенный знак препинания"),
    (re.compile(r"\.\s*\."), "две точки подряд"),
    (re.compile(r"—\s*—"), "два тире подряд"),
    (re.compile(r"\(\s|\s\)"), "пробел внутри скобок"),
    (re.compile(r"«\s|\s»"), "пробел внутри кавычек"),
    (re.compile(r"\s-\s"), "дефис вместо тире"),
    # слово, повторённое дважды подряд: обычно склейка шаблона с данными
    # («тему темы», «и и»), которую глазом в длинном тексте не видно
    (re.compile(r"\b([а-яёa-z]{2,})\s+\1\b", re.I), "слово повторяется дважды"),
    (re.compile(r"[а-яё]\.[А-ЯЁ]"), "нет пробела после точки"),
    (re.compile(r"«[^»]{0,400}$"), "незакрытая кавычка"),
]

AGREEMENT = [
    (re.compile(r"\bв Льве\b"), "«в Льве» вместо «во Льве»"),
    (re.compile(r"\(наоборот\)"), "техническая пометка «(наоборот)» в тексте"),
    # «образуют оппозиция»: названия аспектов в data/ лежат в именительном,
    # а после «образуют» нужен винительный (bot/plain.aspect_accusative).
    # Мужские названия (квадрат, трин, секстиль) в винительном не меняются,
    # поэтому ловим женское и множественное.
    (re.compile(r"образу(?:ют|ет)\s+(?:оппозиция|соединения)\b"),
     "именительный падеж после «образуют»"),
    # «Повешенный, выпавшая перевёрнутой» — половина названий карт
    # мужского рода, и женское причастие к ним не подходит
    (re.compile(r"(?:Повешенный|Маг|Мир|Шут|Император|Иерофант|Отшельник|"
                r"Суд|Туз|Паж|Рыцарь|Король)[^.]{0,25}выпавш(?:ая|ую)"),
     "женское причастие при названии карты мужского рода"),
]

# Предлоги, после которых именительный падеж невозможен. Проверяем не
# морфологию (регулярка её не знает и даёт ложные срабатывания на
# правильном «про режим, нагрузку и силы»), а факт: словарные формулировки
# записаны в именительном, и сразу за этими предлогами их быть не должно —
# для таких мест в plain.py лежат отдельные падежные словари.
PREPOSITIONS = ("про", "о", "об", "в", "во", "на", "за", "к", "ко", "из")


def proper_nouns():
    """Слова, которым заглавная буква положена и после двоеточия.

    Названия планет, знаков, карт, систем домов и имена клиентов — не
    ошибка в «дома: Плацидус» и «Совместимость: Ирина и Пётр».
    """
    import tarot_data as td

    words = {"Плацидус", "Порфирий", "Асцендент", "Солнце", "Луна",
             "Ирина", "Пётр", "Анна", "Тест", "Макс"}
    for planet in ad.PLANETS.values():
        words.update(planet["name_ru"].split())
    for sign_data in ad.SIGNS.values():
        words.update([sign_data["name_ru"], sign_data["prepositional"],
                      sign_data["genitive"]])
    for aspect in ad.ASPECTS.values():
        words.add(aspect["name_ru"])
    for card in td.ALL_CARDS:
        words.update(card["name_ru"].split())
    return {word for word in words if word[:1].isupper()}


def build_checks():
    """Проверки, которые нужно собрать из данных, а не записать константой."""
    checks = []

    # Именительный падеж сразу за предлогом — след механической склейки.
    nominative = list(plain.PLANET_PLAIN.values()) + list(plain.HOUSE_PLAIN.values())
    for phrase in nominative:
        checks.append((
            re.compile(r"\b(%s)\s+%s" % ("|".join(PREPOSITIONS), re.escape(phrase))),
            "именительный падеж после предлога: «%s»" % phrase,
        ))

    # Заглавная буква после двоеточия — но не у имён собственных.
    allowed = "|".join(sorted(proper_nouns(), key=len, reverse=True))
    checks.append((
        re.compile(r":\s+(?!(?:%s)\b)[А-ЯЁ][а-яё]+\s+[а-яё]+" % allowed),
        "заглавная буква после двоеточия",
    ))
    return checks


def visible(text):
    """Текст без HTML-тегов: проверяем то, что увидит клиент."""
    return TAGS.sub("", text)


CASE_CHECKS = None


def check_text(text, where, errors):
    global CASE_CHECKS
    if CASE_CHECKS is None:
        CASE_CHECKS = build_checks()

    check_punctuation(text, where, errors)

    body = visible(text)
    for pattern, description in TYPOGRAPHY + AGREEMENT + CASE_CHECKS:
        match = pattern.search(body)
        if match:
            snippet = body[max(0, match.start() - 40):match.end() + 40].replace("\n", " ")
            errors.append("%s: %s — «…%s…»" % (where, description, snippet.strip()))
            break


SENTENCE = re.compile(r"[^.!?]+[.!?]")


def check_punctuation(text, where, errors):
    """Два двоеточия в одном предложении — знак механической склейки.

    Так выглядит рамка «дальше будет так: …», в которую подставили фразу,
    у которой двоеточие уже есть: «дальше будет так: появляется реальная
    возможность: деньги, работа». Для таких мест в readings есть
    after_colon(), заменяющий внутреннее двоеточие на тире.
    """
    for line in visible(text).splitlines():
        # строки-сводки («Лёгких связок: 12 · напряжённых: 6 · слитых: 16»)
        # прозой не являются: двоеточия там разделяют колонки
        if "·" in line:
            continue
        for sentence in SENTENCE.findall(line):
            if sentence.count(":") >= 2:
                errors.append("%s: два двоеточия в предложении — «%s»"
                              % (where, sentence.strip()[:110]))
                return


def check_sentences(text, where, errors):
    """Развёрнутая строка должна оканчиваться знаком препинания.

    Заголовки («💞 Любовь и отношения»), подписи («Ирина, 36 лет · 13.09»)
    и строки навигации точки не требуют, поэтому смотрим только на то, что
    по длине и составу тянет на предложение.
    """
    for line in visible(text).splitlines():
        line = line.strip()
        if not line or line.startswith(("·", "/", "—")):
            continue
        # строки навигации («Другая тема — /menu · год — /year») — не проза
        if "·" in line or re.search(r"/[a-z]{3,}", line):
            continue
        if len(line) < 45 or len(line.split()) < 6:
            continue
        if line[-1] not in ".!?:…»)":
            errors.append("%s: строка без знака в конце — «…%s»" % (where, line[-60:]))
            break


def check_vocabulary(errors):
    """Словарь человеческого языка должен покрывать всё, к чему обращаемся."""
    for key in ad.PLANETS:
        if not plain.planet(key):
            errors.append("нет простого описания планеты %s" % key)
        if not plain.planet_about(key):
            errors.append("нет предложного падежа для планеты %s" % key)
    for key in ad.SIGNS:
        if not plain.sign(key):
            errors.append("нет простого описания знака %s" % key)
    for number in ad.HOUSES:
        if not plain.house(number):
            errors.append("нет простого описания дома %d" % number)
        if not plain.house_in(number):
            errors.append("нет предложного падежа для дома %d" % number)
    for key in ad.ASPECTS:
        if key not in plain.ASPECT_PREPOSITIONAL:
            errors.append("нет предложного падежа для аспекта %s" % key)

    # перечисления собираются с союзом, а не вереницей запятых
    if plain.join(["раз", "два", "три"]) != "раз, два и три":
        errors.append("plain.join собирает перечисление без союза")
    if plain.join(["раз"]) != "раз":
        errors.append("plain.join портит одиночный элемент")

    # предлог «во» только там, где он нужен
    if plain.sign_in("leo", ad.SIGNS["leo"]) != "во Льве":
        errors.append("«во Льве» собирается неправильно")
    if plain.sign_in("aries", ad.SIGNS["aries"]) != "в Овне":
        errors.append("«в Овне» собирается неправильно")


def check_all_signs(errors):
    """Каждый знак Солнца хотя бы раз прогоняется через разбор.

    Ошибка в согласовании часто сидит в одном знаке из двенадцати — на
    одном показательном примере её не видно.
    """
    for month, day in [(1, 10), (2, 10), (3, 25), (4, 25), (5, 25), (6, 25),
                       (7, 25), (8, 25), (9, 25), (10, 25), (11, 25), (12, 25)]:
        profile = {"name": "Тест", "birth": dt.date(1990, month, day),
                   "time": None, "question": ""}
        text = "\n".join(readings.build_reading("love", profile))
        check_text(text, "солярный разбор %02d.%02d" % (day, month), errors)
        check_sentences(text, "солярный разбор %02d.%02d" % (day, month), errors)


def main():
    errors = []
    check_vocabulary(errors)
    check_all_signs(errors)

    if natal.available():
        moscow = natal.find_places("Москва")[0]
        profile = {"name": "Ирина", "birth": dt.date(1990, 3, 15), "time": "14:30",
                   "place": moscow, "question": "почему не складывается с партнёром"}
        profile["chart"] = natal.build_chart(
            profile["birth"], profile["time"], profile["place"])
        chart = profile["chart"]

        for sphere_key in readings.SPHERES:
            text = "\n".join(readings.build_reading(sphere_key, profile))
            check_text(text, "разбор «%s»" % sphere_key, errors)
            check_sentences(text, "разбор «%s»" % sphere_key, errors)

        outputs = {
            "неделя": "\n".join(periods.build_week(profile)),
            "год": "\n".join(periods.build_year(profile)),
            "транзиты": forecast.format_transits(
                forecast.transits(chart, include_fast=True), chart),
            "прогрессии": forecast.format_progressions(
                forecast.progressions(chart), chart),
            "синастрия": forecast.format_synastry(
                forecast.synastry(chart, chart), "Ирина", "Пётр"),
            "карта": natal.format_chart(chart),
        }
        for where, text in outputs.items():
            check_text(text, where, errors)
            check_sentences(text, where, errors)
    else:
        errors.append("pyswisseph недоступен — натальные формулировки не проверены")

    print("Ошибок: %d" % len(errors))
    for error in errors[:30]:
        print(" -", error)
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
