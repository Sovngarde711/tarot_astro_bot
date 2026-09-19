# -*- coding: utf-8 -*-
"""Проверка статистики: приватность событий и правильность метрик.

Запуск: python scripts/test_stats.py

Главное здесь — не арифметика, а то, что в файл событий не попадают
персональные данные. Бот спрашивает имя, дату рождения и город; если
что-нибудь из этого просочится в аналитику, получится база персональных
данных, о существовании которой никто не предупреждал клиента.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import stats  # noqa: E402


def with_temp_storage(function):
    """Каждая проверка работает в своей папке, не трогая настоящие события."""
    def wrapper(errors):
        folder = tempfile.mkdtemp()
        saved = (stats.STATS_DIR, stats.EVENTS_PATH, stats.SALT_PATH, stats._salt)
        stats.STATS_DIR = folder
        stats.EVENTS_PATH = os.path.join(folder, "events.jsonl")
        stats.SALT_PATH = os.path.join(folder, "salt")
        stats._salt = None
        try:
            function(errors)
        finally:
            (stats.STATS_DIR, stats.EVENTS_PATH,
             stats.SALT_PATH, stats._salt) = saved
            shutil.rmtree(folder, ignore_errors=True)
    return wrapper


@with_temp_storage
def check_privacy(errors):
    # пробуем записать всё, что записывать нельзя
    stats.track("reading", 419135931,
                sphere="love", sign="pisces", age_band="35-44", country="RU",
                name="Ирина", birth="1990-03-15", city="Москва",
                question="почему не складывается с партнёром")

    raw = open(stats.EVENTS_PATH, encoding="utf-8").read()
    for secret in ("Ирина", "1990-03-15", "Москва", "не складывается", "419135931"):
        if secret in raw:
            errors.append("в событиях оказались персональные данные: %r" % secret)

    record = json.loads(raw.strip())
    for field in ("name", "birth", "city", "question"):
        if field in record:
            errors.append("поле %r не отфильтровано белым списком" % field)
    for field in ("sphere", "sign", "age_band", "country"):
        if field not in record:
            errors.append("разрешённое поле %r потерялось" % field)
    if not record.get("user"):
        errors.append("событие без идентификатора пользователя — метрики не посчитать")
    if len(record["user"]) != 16:
        errors.append("хеш пользователя неожиданной длины: %s" % record["user"])


@with_temp_storage
def check_hashing(errors):
    first = stats.user_hash(111)
    again = stats.user_hash(111)
    other = stats.user_hash(222)
    if first != again:
        errors.append("хеш одного и того же чата не воспроизводится")
    if first == other:
        errors.append("разные чаты дали одинаковый хеш")
    if str(111) in first:
        errors.append("номер чата виден в хеше")

    # соль сохраняется и отличается между установками
    saved_salt = stats.salt()
    stats._salt = None
    if stats.salt() != saved_salt:
        errors.append("соль не сохраняется между запусками")
    if len(saved_salt) < 32:
        errors.append("соль слишком короткая: %d символов" % len(saved_salt))


@with_temp_storage
def check_resilience(errors):
    """Аналитика не имеет права ронять бота."""
    stats.EVENTS_PATH = os.path.join(stats.STATS_DIR, "нет", "такой", "папки", "x.jsonl")
    try:
        stats.track("reading", 1, sphere="love")
    except Exception as exc:
        errors.append("track упал при недоступном файле: %r" % exc)

    # битая строка в файле не должна ломать отчёт
    stats.EVENTS_PATH = os.path.join(stats.STATS_DIR, "events.jsonl")
    with open(stats.EVENTS_PATH, "w", encoding="utf-8") as handle:
        handle.write('{"ts": "2026-09-01T10:00:00", "event": "start", "user": "a"}\n')
        handle.write("это не json\n")
        handle.write('{"ts": "2026-09-01T11:00:00", "event": "reading", "user": "a"}\n')
    events = stats.load_events()
    if len(events) != 2:
        errors.append("битая строка сломала чтение: получено %d событий" % len(events))


def check_metrics(errors):
    """Метрики считаются на синтетическом потоке с известным ответом."""
    today = dt.date(2026, 9, 30)
    events = []

    def add(day_offset, hour, event, user, **fields):
        date = today - dt.timedelta(days=day_offset)
        record = {"ts": "%sT%02d:00:00" % (date.isoformat(), hour),
                  "event": event, "user": user}
        record.update(fields)
        events.append(record)

    # три пользователя: A пришёл давно и вернулся, B новый и вернулся
    # на следующий день, C новый и больше не появлялся
    add(40, 12, "start", "A")
    add(5, 12, "start", "A")
    add(5, 12, "sphere_chosen", "A", sphere="love")
    add(5, 12, "reading", "A", sphere="love", sign="pisces", age_band="35-44",
        country="RU", ms=40)
    add(3, 20, "start", "B")
    add(3, 20, "sphere_chosen", "B", sphere="wealth")
    add(3, 20, "profile_birth", "B", step="birth")
    add(2, 20, "reading", "B", sphere="wealth", sign="leo", age_band="25-34",
        country="KZ", ms=60)
    add(1, 9, "start", "C")

    data = stats.report(events, days=30, today=today)

    checks = [
        (data["users"], 3, "уникальных пользователей"),
        (data["new_users"], 2, "новых пользователей"),
        (data["returning_users"], 1, "вернувшихся пользователей"),
        (data["readings"], 2, "разборов"),
        (data["events"], 8, "событий за период"),
    ]
    for got, expected, what in checks:
        if got != expected:
            errors.append("%s: %s, ожидалось %s" % (what, got, expected))

    if data["retention_d1"] != 50.0:
        errors.append("удержание на следующий день: %s, ожидалось 50.0"
                      % data["retention_d1"])
    if dict(data["spheres"]) != {"love": 1, "wealth": 1}:
        errors.append("темы посчитаны неверно: %s" % data["spheres"])
    if dict(data["signs"]) != {"pisces": 1, "leo": 1}:
        errors.append("знаки посчитаны неверно: %s" % data["signs"])
    if dict(data["countries"]) != {"RU": 1, "KZ": 1}:
        errors.append("страны посчитаны неверно: %s" % data["countries"])
    if data["median_ms"] not in (40, 60):
        errors.append("медиана времени сборки: %s" % data["median_ms"])

    funnel = dict(data["funnel"])
    if funnel["Запустили бота"] != 3 or funnel["Получили разбор"] != 2:
        errors.append("воронка посчитана неверно: %s" % data["funnel"])

    # старое событие (40 дней назад) не должно попадать в недельный отчёт
    week = stats.report(events, days=7, today=today)
    if week["users"] != 3:
        errors.append("за неделю пользователей %s, ожидалось 3" % week["users"])
    day = stats.report(events, days=1, today=today)
    if day["users"] != 0:
        errors.append("за сегодня пользователей %s, ожидалось 0" % day["users"])

    text = stats.format_report(data)
    for block in ("Аудитория", "Воронка", "Темы разборов", "Качество"):
        if block not in text:
            errors.append("в отчёте нет блока «%s»" % block)
    if len(text) > 4000:
        errors.append("отчёт не влезает в сообщение Telegram: %d символов" % len(text))


def check_age_bands(errors):
    cases = [(15, "до 18"), (18, "18-24"), (30, "25-34"), (40, "35-44"),
             (50, "45-54"), (70, "55+")]
    for age, expected in cases:
        got = stats.age_band(age)
        if got != expected:
            errors.append("возраст %d отнесён к «%s», ожидалось «%s»"
                          % (age, got, expected))


def check_admin_only(errors):
    """Статистику отдаём только владельцу бота."""
    import bot as B

    sent = []
    saved_api, saved_admin = B.api_call, B.ADMIN_CHAT_ID
    B.api_call = lambda method, **params: sent.append(params) or {"ok": True}
    try:
        B.ADMIN_CHAT_ID = "777"
        B.deliver_stats(999, "/stats")
        if not sent or "Не знаю такой команды" not in sent[-1].get("text", ""):
            errors.append("посторонний чат получил статистику")

        sent.clear()
        B.deliver_stats(777, "/stats 7")
        text = sent[-1].get("text", "") if sent else ""
        if "Статистика" not in text and "Событий" not in text:
            errors.append("владелец не получил статистику: %r" % text[:80])

        # несколько чатов через запятую: владелец и, скажем, тот, кто ведёт
        # рекламу. Раньше номер был один, и /stats из чата владельца молчал,
        # если в .env стоял чужой номер
        for value in ("777,888", "777, 888", "777 888", "777;888"):
            for chat in (777, 888):
                sent.clear()
                B.ADMIN_CHAT_ID = value
                B.deliver_stats(chat, "/stats 7")
                text = sent[-1].get("text", "") if sent else ""
                if "Статистика" not in text and "Событий" not in text:
                    errors.append("при ADMIN_CHAT_ID=%r чат %d не получил "
                                  "статистику: %r" % (value, chat, text[:60]))
            sent.clear()
            B.deliver_stats(999, "/stats")
            if not sent or "Не знаю такой команды" not in sent[-1].get("text", ""):
                errors.append("при ADMIN_CHAT_ID=%r посторонний чат получил "
                              "статистику" % value)

        # номер, который лишь начинается так же, доступа не даёт
        sent.clear()
        B.ADMIN_CHAT_ID = "777"
        B.deliver_stats(7777, "/stats")
        if not sent or "Не знаю такой команды" not in sent[-1].get("text", ""):
            errors.append("чат 7777 получил статистику, настроенную для 777")

        sent.clear()
        B.ADMIN_CHAT_ID = ""
        B.deliver_stats(777, "/stats")
        if not sent or "не настроена" not in sent[-1].get("text", ""):
            errors.append("без ADMIN_CHAT_ID бот не объяснил, что статистика выключена")

        # пробелы и пустые элементы не должны открывать доступ всем подряд
        sent.clear()
        B.ADMIN_CHAT_ID = " , ; "
        B.deliver_stats(777, "/stats")
        if not sent or "не настроена" not in sent[-1].get("text", ""):
            errors.append("ADMIN_CHAT_ID из одних разделителей не считается "
                          "пустым")
    finally:
        B.api_call, B.ADMIN_CHAT_ID = saved_api, saved_admin


def main():
    errors = []
    check_privacy(errors)
    check_hashing(errors)
    check_resilience(errors)
    check_metrics(errors)
    check_age_bands(errors)
    check_admin_only(errors)

    print("Ошибок: %d" % len(errors))
    for error in errors[:30]:
        print(" -", error)
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
