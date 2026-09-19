# -*- coding: utf-8 -*-
"""Проверка того, что делает бота консультантом, а не автоматом.

Запуск: python scripts/test_personal.py

Три вещи, которые легко потерять при следующей правке текстов:

1. <b>Вопрос услышан.</b> Человек написал, что его волнует, — и разбор
   начинается с этого, а не с таблицы.
2. <b>Прошлый разговор помнится.</b> Второй визит не выглядит как первый.
3. <b>Память не врёт.</b> Тот же расклад в тот же день не выдаётся за
   «карта вернулась», повтор карты подаётся с оговоркой, а на диск не
   попадают ни тексты разборов, ни вопросы клиента.
"""
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import natal  # noqa: E402
import readings  # noqa: E402
import store  # noqa: E402

PLACE = {"name": "Москва", "country": "RU", "lat": 55.7522, "lon": 37.6156,
         "timezone": "Europe/Moscow"}
TODAY = dt.date(2026, 9, 15)
QUESTION = "стоит ли ждать от него предложения"


def with_temp_store(function):
    def wrapper(errors):
        folder = tempfile.mkdtemp()
        saved = (store.STORE_DIR, store.PROFILES_PATH, store.HISTORY_PATH)
        store.STORE_DIR = folder
        store.PROFILES_PATH = os.path.join(folder, "profiles.json")
        store.HISTORY_PATH = os.path.join(folder, "history.json")
        try:
            function(errors)
        finally:
            (store.STORE_DIR, store.PROFILES_PATH,
             store.HISTORY_PATH) = saved
            shutil.rmtree(folder, ignore_errors=True)
    wrapper.__name__ = function.__name__
    return wrapper


def profile(question=QUESTION):
    chart = None
    if natal.available():
        try:
            chart = natal.build_chart(dt.date(1990, 3, 15), "14:30", PLACE)
        except Exception:
            chart = None
    return {"name": "Анна", "birth": dt.date(1990, 3, 15), "time": "14:30",
            "place": PLACE, "chart": chart, "question": question}


def visible(text):
    return re.sub(r"<[^>]+>", "", text)


def reading(sphere, person, day, history=None):
    return visible("\n".join(readings.build_reading(
        sphere, person, None, day, history=history)))


# ---------------------------------------------------------------------------
# Вопрос услышан
# ---------------------------------------------------------------------------

@with_temp_store
def check_question_is_heard(errors):
    text = reading("love", profile(), TODAY)
    opening = text.split("Коротко")[0]

    if QUESTION not in opening:
        errors.append("вопрос клиента не назван в начале разбора: %r"
                      % opening[-160:])
    if "Здравствуйте" not in opening:
        errors.append("разбор начинается без приветствия")

    # вопрос цитируется один раз: второй раз это выглядит как заедание
    if text.count(QUESTION) > 1:
        errors.append("вопрос процитирован %d раза" % text.count(QUESTION))


@with_temp_store
def check_no_question_is_explained(errors):
    """Если вопроса нет, бот говорит, что разбирает тему целиком."""
    text = reading("love", profile(question=""), TODAY)
    opening = text.split("Коротко")[0]
    if "тему целиком" not in opening:
        errors.append("без вопроса бот не объяснил, что разбирает: %r"
                      % opening[-160:])


@with_temp_store
def check_no_name_in_greeting(errors):
    """Здороваемся без имени: расклад бывает и на другого человека.

    Имя в анкете принадлежит тому, чью карту считаем. «Пётр,
    здравствуйте» в ответ Анне, которая заказала разбор на партнёра, —
    это выглядит как перепутанный собеседник.
    """
    person = profile()
    person["name"] = "Пётр"
    opening = reading("love", person, TODAY).split("Коротко")[0]
    if "Пётр, здравствуйте" in opening or "Пётр, снова" in opening:
        errors.append("бот поздоровался именем из анкеты: %r" % opening[:120])


# ---------------------------------------------------------------------------
# Совет зависит от вопроса и от карты
# ---------------------------------------------------------------------------

@with_temp_store
def check_step_follows_the_question(errors):
    """Разные вопросы — разные шаги, и бот помечает, что отвечает на вопрос."""
    steps = {}
    for question in ("он не пишет уже неделю",
                     "стоит ли ждать от него предложения",
                     "он мне изменил, что делать"):
        person = profile(question)
        text = reading("love", person, TODAY)
        line = next((line for line in text.splitlines()
                     if line.startswith("Конкретно")), "")
        if "по вашему вопросу" not in line:
            errors.append("шаг не помечен как ответ на вопрос: %r" % line[:90])
        steps[question] = line

    if len(set(steps.values())) != len(steps):
        errors.append("на разные вопросы пришёл один и тот же шаг")

    # без вопроса работает прежний способ — по преобладающей масти
    plain_line = next((line for line in reading("love", profile(""), TODAY).splitlines()
                       if line.startswith("Конкретно")), "")
    if "по вашему вопросу" in plain_line:
        errors.append("без вопроса шаг помечен как ответ на вопрос")
    if not plain_line:
        errors.append("без вопроса пропал конкретный шаг")


def check_intent_respects_the_theme(errors):
    """Примета срабатывает только там, где она осмысленна.

    «Думаю уволиться», написанное в теме про любовь, не должно приносить
    совет сходить на три собеседования.
    """
    if readings._question_step("думаю уволиться, стоит ли", "love"):
        errors.append("совет про увольнение выдан в теме «любовь»")
    if not readings._question_step("думаю уволиться, стоит ли", "work"):
        errors.append("совет про увольнение не сработал в теме «работа»")
    if readings._question_step("он не пишет уже неделю", "wealth"):
        errors.append("совет про молчание выдан в теме «деньги»")
    if not readings._question_step("он не пишет уже неделю", "feelings"):
        errors.append("совет про молчание не сработал в теме «чувства»")
    # универсальные приметы работают в любой теме
    if not readings._question_step("что выбрать", "move"):
        errors.append("универсальная примета не сработала")


def check_chart_changes_how_to_act(errors):
    """Уточнение берётся из реальных фактов карты — и только одно."""
    import natal

    sphere = readings.SPHERES["love"]

    # без карты уточнения нет и ничего не падает
    if readings._chart_caveat(None, sphere) is not None:
        errors.append("без натальной карты появилось уточнение по карте")

    # планета темы в двенадцатом доме — действовать тихо
    hidden = {"has_houses": True, "cusps": [],
              "positions": {"venus": {"house": 12, "retro": False}}}
    saved = natal.house_sign
    natal.house_sign = lambda chart, house: None
    try:
        text = readings._chart_caveat(hidden, sphere) or ""
        if "тихо" not in text:
            errors.append("двенадцатый дом не дал уточнения: %r" % text[:80])

        first = {"has_houses": True, "cusps": [],
                 "positions": {"venus": {"house": 1, "retro": False}}}
        text = readings._chart_caveat(first, sphere) or ""
        if "лично на вас" not in text:
            errors.append("первый дом не дал уточнения: %r" % text[:80])

        retro = {"has_houses": True, "cusps": [],
                 "positions": {"venus": {"house": 6, "retro": True}}}
        text = readings._chart_caveat(retro, sphere) or ""
        if "ретроградно" not in text:
            errors.append("ретроградная планета темы не дала уточнения")

        # ретроградный управитель темы важнее положения планеты
        natal.house_sign = lambda chart, house: "capricorn"   # управитель Сатурн
        ruled = {"has_houses": True, "cusps": [],
                 "positions": {"venus": {"house": 6, "retro": False},
                               "saturn": {"house": 3, "retro": True}}}
        text = readings._chart_caveat(ruled, sphere) or ""
        if "управляет этой темой" not in text:
            errors.append("ретроградный управитель не дал уточнения: %r"
                          % text[:80])
        if text.count("Одно уточнение") > 1:
            errors.append("уточнений по карте больше одного")
    finally:
        natal.house_sign = saved


def check_caveat_agrees_in_gender(errors):
    """«Венера, которая управляет» — не «который»."""
    import natal
    import astrology_data as ad

    sphere = readings.SPHERES["love"]
    saved = natal.house_sign
    try:
        for sign_key, ruler in (("taurus", "venus"), ("capricorn", "saturn"),
                                ("cancer", "moon"), ("leo", "sun")):
            natal.house_sign = lambda chart, house, key=sign_key: key
            chart = {"has_houses": True, "cusps": [],
                     "positions": {"venus": {"house": 6, "retro": False},
                                   ruler: {"house": 3, "retro": True}}}
            text = readings._chart_caveat(chart, sphere) or ""
            name = ad.PLANETS[ruler]["name_ru"]
            if name not in text:
                continue
            import plain as plain_words
            expected = "%s, %s управляет" % (name, plain_words.which(ruler))
            if expected not in text:
                errors.append("род не согласован: ожидалось %r" % expected)
    finally:
        natal.house_sign = saved


# ---------------------------------------------------------------------------
# Прошлый разговор помнится
# ---------------------------------------------------------------------------

@with_temp_store
def check_first_visit_has_no_memory(errors):
    text = reading("love", profile(), TODAY)
    if "С прошлого раза" in text:
        errors.append("первому обращению показан блок про прошлые разборы")
    if "Снова здравствуйте" in text:
        errors.append("первого клиента бот встретил как знакомого")


@with_temp_store
def check_second_visit_remembers(errors):
    chat = 42
    earlier = TODAY - dt.timedelta(days=5)
    store.remember_reading(chat, "love",
                           readings.draw_spread("love", profile(), earlier),
                           earlier)

    text = reading("love", profile(), TODAY, history=store.past_readings(chat))
    if "Снова здравствуйте" not in text:
        errors.append("вернувшегося клиента бот встретил как нового")
    if "С прошлого раза" not in text:
        errors.append("нет блока про прошлый разбор")
    if "уже разбирали" not in text:
        errors.append("бот не сказал, что эту тему уже разбирали")


@with_temp_store
def check_other_theme_is_named(errors):
    chat = 42
    earlier = TODAY - dt.timedelta(days=3)
    store.remember_reading(chat, "work",
                           readings.draw_spread("work", profile(), earlier),
                           earlier)

    text = reading("love", profile(), TODAY, history=store.past_readings(chat))
    if "работа и карьера" not in text.lower():
        errors.append("бот не вспомнил, с какой темой человек приходил")


@with_temp_store
def check_same_day_is_not_a_sign(errors):
    """Тот же расклад в тот же день — правило, а не «карта вернулась»."""
    chat = 42
    person = profile()
    store.remember_reading(chat, "love",
                           readings.draw_spread("love", person, TODAY), TODAY)

    text = reading("love", person, TODAY, history=store.past_readings(chat))
    if "уже смотрели сегодня" not in text:
        errors.append("повтор в тот же день не объяснён")
    if "Снова выпал" in text:
        errors.append("тот же самый расклад подан как вернувшаяся карта")


@with_temp_store
def check_repeat_card_has_a_caveat(errors):
    """Повтор карты подаётся с оговоркой, а не как знак судьбы."""
    chat = 42
    person = profile()
    earlier = TODAY - dt.timedelta(days=4)
    # кладём в историю ровно те карты, которые выпадут сегодня
    today_draw = readings.draw_spread("love", person, TODAY)
    store.remember_reading(chat, "love", today_draw, earlier)

    text = reading("love", person, TODAY, history=store.past_readings(chat))
    if "Снова выпал" not in text:
        errors.append("повтор карты не замечен")
    if "семьдесят восемь" not in text:
        errors.append("повтор подан без оговорки про случайность совпадений")
    for wrong in ("знак судьбы", "судьба повторяет", "неслучайно"):
        if wrong in text.lower():
            errors.append("повтор подан как предопределённость: %r" % wrong)


@with_temp_store
def check_plural_agreement(errors):
    """«Снова выпали две карты» — не «снова выпала»."""
    chat = 42
    person = profile()
    earlier = TODAY - dt.timedelta(days=4)
    store.remember_reading(chat, "love",
                           readings.draw_spread("love", person, TODAY), earlier)

    text = reading("love", person, TODAY, history=store.past_readings(chat))
    line = next((line for line in text.splitlines()
                 if line.startswith("Снова выпал")), "")
    if " и " in line and line.startswith("Снова выпала "):
        errors.append("две карты названы в единственном числе: %r" % line[:90])


# ---------------------------------------------------------------------------
# Память не врёт и не хранит лишнего
# ---------------------------------------------------------------------------

@with_temp_store
def check_history_stores_no_texts(errors):
    """На диск уходят тема, дата и номера карт — и ничего больше."""
    chat = 42
    person = profile()
    store.remember_reading(chat, "love",
                           readings.draw_spread("love", person, TODAY), TODAY)

    raw = open(store.HISTORY_PATH, encoding="utf-8").read()
    for secret in (QUESTION, "Анна", "1990-03-15", "Москва", "Проще говоря"):
        if secret in raw:
            errors.append("в историю попало лишнее: %r" % secret)

    record = json.loads(raw)["42"][0]
    for field in record:
        if field not in ("sphere", "date", "cards"):
            errors.append("в истории лишнее поле %r" % field)


@with_temp_store
def check_same_reading_is_not_doubled(errors):
    chat = 42
    person = profile()
    draw = readings.draw_spread("love", person, TODAY)
    for _ in range(3):
        store.remember_reading(chat, "love", draw, TODAY)
    if len(store.past_readings(chat)) != 1:
        errors.append("один и тот же расклад записан %d раз"
                      % len(store.past_readings(chat)))


@with_temp_store
def check_history_is_bounded(errors):
    chat = 42
    person = profile()
    for offset in range(store.MAX_HISTORY + 4):
        day = TODAY - dt.timedelta(days=offset)
        store.remember_reading(chat, "love",
                               readings.draw_spread("love", person, day), day)
    if len(store.past_readings(chat)) != store.MAX_HISTORY:
        errors.append("история не ограничена: %d записей"
                      % len(store.past_readings(chat)))


@with_temp_store
def check_reset_wipes_history(errors):
    """/reset стирает и историю разборов: это «всё, что я о вас помню»."""
    import bot as B

    chat = 42
    person = profile()
    store.remember_reading(chat, "love",
                           readings.draw_spread("love", person, TODAY), TODAY)
    store.remember(chat, person)

    saved_api = B.api_call
    B.api_call = lambda method, **params: {"ok": True}
    try:
        B.handle_message(chat, "/reset", None)
    finally:
        B.api_call = saved_api

    if store.past_readings(chat):
        errors.append("/reset не стёр историю разборов")
    if store.people(chat):
        errors.append("/reset не стёр анкеты")


@with_temp_store
def check_corrupted_history(errors):
    os.makedirs(store.STORE_DIR, exist_ok=True)
    with open(store.HISTORY_PATH, "w", encoding="utf-8") as handle:
        handle.write('{"42": [{"sphere": "love", "car')

    try:
        past = store.past_readings(42)
    except Exception as failure:
        errors.append("битая история уронила бота: %r" % failure)
        return
    if past:
        errors.append("из обрезанного файла прочиталась история")

    text = reading("love", profile(), TODAY, history=past)
    if "Коротко" not in text:
        errors.append("после битой истории разбор не собрался")


def main():
    errors = []
    for check in (check_question_is_heard, check_no_question_is_explained,
                  check_step_follows_the_question,
                  check_intent_respects_the_theme,
                  check_chart_changes_how_to_act,
                  check_caveat_agrees_in_gender,
                  check_no_name_in_greeting, check_first_visit_has_no_memory,
                  check_second_visit_remembers, check_other_theme_is_named,
                  check_same_day_is_not_a_sign,
                  check_repeat_card_has_a_caveat, check_plural_agreement,
                  check_history_stores_no_texts,
                  check_same_reading_is_not_doubled, check_history_is_bounded,
                  check_reset_wipes_history, check_corrupted_history):
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
