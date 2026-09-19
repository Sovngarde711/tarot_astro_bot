# -*- coding: utf-8 -*-
"""
Статистика: что происходит в боте и как это читать.

Пишем поток событий в stats/events.jsonl — по строке JSON на событие.
Формат выбран намеренно простой: файл дописывается в конец (ничего не
блокируется и не портится при падении), читается любым скриптом, грепается
глазами и в любой момент переносится в нормальную БД, если вырастет объём.

<b>Персональных данных в событиях нет.</b> Вместо номера чата пишется его
необратимый хеш с солью, которая лежит рядом и в репозиторий не попадает:
по такому идентификатору можно посчитать уникальных пользователей и
удержание, но нельзя узнать, кто это. Имена, даты рождения, города и
вопросы клиентов не пишутся вообще. Записывается только огрублённое:
знак Солнца (12 корзин), возрастная группа (6 корзин) и код страны —
то, что нужно для продуктовых решений и разговора с рекламодателями,
и по чему человека не найти.

Аналитика не имеет права ломать бота: любая ошибка записи гасится и
уходит в лог отладки, диалог с клиентом продолжается как ни в чём не бывало.
"""
import datetime as dt
import hashlib
import json
import logging
import os
import secrets
from collections import Counter, defaultdict

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
# Папку можно переопределить переменной окружения: тесты и симуляции пишут
# события во временный каталог, чтобы не мешать их со статистикой бота.
STATS_DIR = os.environ.get("STATS_DIR") or os.path.join(BASE_DIR, "stats")
EVENTS_PATH = os.path.join(STATS_DIR, "events.jsonl")
SALT_PATH = os.path.join(STATS_DIR, "salt")

log = logging.getLogger("tarot_astro_bot.stats")

# Белый список полей. Всё, что не перечислено, в файл не попадёт — это
# страховка от того, что кто-нибудь однажды передаст сюда имя клиента
# «просто чтобы посмотреть».
ALLOWED_FIELDS = {
    "sphere",      # ключ темы: love, wealth, work…
    "command",     # команда без слеша
    "kind",        # week / year, тип прогноза
    "step",        # шаг анкеты: name, birth, time, city, question
    "ok",          # успех или отказ
    "ms",          # сколько миллисекунд собирался ответ
    "reason",      # почему не получилось: city_not_found, bad_date…
    "sign",        # знак Солнца — 12 корзин
    "age_band",    # возрастная группа — 6 корзин
    "country",     # код страны из геокодера
    "has_chart",   # построена ли натальная карта
}

AGE_BANDS = [(0, 17, "до 18"), (18, 24, "18-24"), (25, 34, "25-34"),
             (35, 44, "35-44"), (45, 54, "45-54"), (55, 200, "55+")]

_salt = None


def age_band(age):
    for low, high, label in AGE_BANDS:
        if low <= age <= high:
            return label
    return None


def salt():
    """Соль для хеширования номеров чатов. Создаётся один раз и хранится рядом."""
    global _salt
    if _salt is None:
        try:
            os.makedirs(STATS_DIR, exist_ok=True)
            if os.path.exists(SALT_PATH):
                with open(SALT_PATH, encoding="utf-8") as handle:
                    _salt = handle.read().strip()
            if not _salt:
                _salt = secrets.token_hex(32)
                with open(SALT_PATH, "w", encoding="utf-8") as handle:
                    handle.write(_salt)
        except OSError:
            # без соли статистику не пишем: хешировать нечем, а писать
            # настоящие номера чатов нельзя
            _salt = ""
    return _salt


def user_hash(chat_id):
    key = salt()
    if not key:
        return None
    digest = hashlib.sha256(("%s|%s" % (key, chat_id)).encode("utf-8"))
    return digest.hexdigest()[:16]


def track(event, chat_id=None, **fields):
    """Записывает событие. Никогда не бросает исключений наружу."""
    try:
        record = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                  "event": event}
        if chat_id is not None:
            user = user_hash(chat_id)
            if user is None:
                return
            record["user"] = user
        for name, value in fields.items():
            if name in ALLOWED_FIELDS and value is not None:
                record[name] = value

        os.makedirs(STATS_DIR, exist_ok=True)
        with open(EVENTS_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        log.debug("не удалось записать событие %s", event, exc_info=True)


# ---------------------------------------------------------------------------
# Чтение и агрегация
# ---------------------------------------------------------------------------

def load_events(path=None):
    path = path or EVENTS_PATH
    events = []
    if not os.path.exists(path):
        return events
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue  # битую строку пропускаем, а не роняем отчёт
    return events


def _date_of(event):
    return event["ts"][:10]


def report(events, days=30, today=None):
    """Считает метрики за последние `days` дней."""
    today = today or dt.date.today()
    since = today - dt.timedelta(days=days - 1)
    since_text = since.isoformat()

    first_seen = {}
    for event in events:
        user = event.get("user")
        if not user:
            continue
        date = _date_of(event)
        if user not in first_seen or date < first_seen[user]:
            first_seen[user] = date

    period = [e for e in events if _date_of(e) >= since_text]
    users = {e["user"] for e in period if e.get("user")}
    new_users = {u for u in users if first_seen.get(u, "") >= since_text}

    by_day = defaultdict(set)
    for event in period:
        if event.get("user"):
            by_day[_date_of(event)].add(event["user"])

    # удержание: вернулся ли пользователь на следующий день после первого
    # появления и хотя бы раз на второй неделе
    user_days = defaultdict(set)
    for event in events:
        if event.get("user"):
            user_days[event["user"]].add(_date_of(event))

    returned_next_day = cohort = returned_week = 0
    for user, first in first_seen.items():
        if first < since_text:
            continue
        first_date = dt.date.fromisoformat(first)
        if first_date >= today:  # сегодняшним когортам ещё рано
            continue
        cohort += 1
        if (first_date + dt.timedelta(days=1)).isoformat() in user_days[user]:
            returned_next_day += 1
        if any((first_date + dt.timedelta(days=offset)).isoformat() in user_days[user]
               for offset in range(2, 8)):
            returned_week += 1

    counts = Counter(e["event"] for e in period)
    spheres = Counter(e["sphere"] for e in period
                      if e["event"] == "reading" and e.get("sphere"))
    commands = Counter(e["command"] for e in period if e.get("command"))
    signs = Counter(e["sign"] for e in period if e.get("sign"))
    ages = Counter(e["age_band"] for e in period if e.get("age_band"))
    countries = Counter(e["country"] for e in period if e.get("country"))
    hours = Counter(int(e["ts"][11:13]) for e in period if len(e["ts"]) > 12)
    weekdays = Counter(dt.date.fromisoformat(_date_of(e)).weekday() for e in period)

    durations = sorted(e["ms"] for e in period if isinstance(e.get("ms"), (int, float)))

    started = counts.get("start", 0)
    chose = counts.get("sphere_chosen", 0)
    gave_birth = counts.get("profile_birth", 0)
    gave_city = counts.get("profile_place", 0)
    readings = counts.get("reading", 0)

    active_days = len([day for day in by_day if by_day[day]])
    return {
        "since": since.isoformat(),
        "until": today.isoformat(),
        "days": days,
        "events": len(period),
        "users": len(users),
        "new_users": len(new_users),
        "returning_users": len(users) - len(new_users),
        "avg_dau": round(sum(len(v) for v in by_day.values()) / active_days, 1)
                   if active_days else 0,
        "events_per_user": round(len(period) / len(users), 1) if users else 0,
        "retention_d1": round(100.0 * returned_next_day / cohort, 1) if cohort else None,
        "retention_w1": round(100.0 * returned_week / cohort, 1) if cohort else None,
        "cohort": cohort,
        "funnel": [
            ("Запустили бота", started),
            ("Выбрали тему", chose),
            ("Назвали дату рождения", gave_birth),
            ("Назвали город", gave_city),
            ("Получили разбор", readings),
        ],
        "spheres": spheres.most_common(),
        "commands": commands.most_common(10),
        "signs": signs.most_common(),
        "ages": ages.most_common(),
        "countries": countries.most_common(5),
        "hours": hours,
        "weekdays": weekdays,
        "readings": readings,
        "charts_built": counts.get("chart_built", 0),
        "errors": counts.get("error", 0),
        "flood": counts.get("rate_limited", 0),
        "median_ms": durations[len(durations) // 2] if durations else None,
        "slowest_ms": durations[-1] if durations else None,
    }


# ---------------------------------------------------------------------------
# Вывод
# ---------------------------------------------------------------------------

WEEKDAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _bar(value, peak, width=10):
    filled = int(round(width * value / peak)) if peak else 0
    return "▇" * max(filled, 1 if value else 0)


def format_report(data, title="Статистика"):
    """Отчёт для Telegram: плотно, но читаемо."""
    lines = ["📊 <b>%s</b> · %s — %s" % (title, data["since"], data["until"]), ""]

    lines += [
        "<b>Аудитория</b>",
        "Пользователей за период: <b>%d</b> (новых %d, вернувшихся %d)"
        % (data["users"], data["new_users"], data["returning_users"]),
        "В среднем за день: %s · событий на человека: %s"
        % (data["avg_dau"], data["events_per_user"]),
    ]
    if data["retention_d1"] is not None:
        lines.append("Возвращаются на следующий день: %s%% · в течение недели: %s%% "
                     "(когорта %d)" % (data["retention_d1"], data["retention_w1"],
                                       data["cohort"]))

    lines += ["", "<b>Воронка</b>"]
    first = data["funnel"][0][1] or 0
    for label, value in data["funnel"]:
        share = " (%d%%)" % round(100.0 * value / first) if first else ""
        lines.append("%s — <b>%d</b>%s" % (label, value, share))

    if data["spheres"]:
        lines += ["", "<b>Темы разборов</b>"]
        total = sum(count for _, count in data["spheres"])
        peak = data["spheres"][0][1]
        for sphere, count in data["spheres"][:8]:
            lines.append("%-12s %s %d (%d%%)" % (
                sphere, _bar(count, peak), count, round(100.0 * count / total)))

    if data["commands"]:
        lines += ["", "<b>Команды</b>: " + ", ".join(
            "/%s %d" % (command, count) for command, count in data["commands"][:8])]

    if data["signs"]:
        lines += ["", "<b>Знаки клиентов</b>: " + ", ".join(
            "%s %d" % (sign, count) for sign, count in data["signs"][:6])]
    if data["ages"]:
        lines.append("<b>Возраст</b>: " + ", ".join(
            "%s — %d" % (band, count) for band, count in data["ages"]))
    if data["countries"]:
        lines.append("<b>Страны</b>: " + ", ".join(
            "%s %d" % (country, count) for country, count in data["countries"]))

    if data["hours"]:
        peak_hour, peak_count = data["hours"].most_common(1)[0]
        busy = sorted(data["hours"].items(), key=lambda kv: -kv[1])[:3]
        lines += ["", "<b>Когда приходят</b>: пик в %02d:00, активнее всего %s"
                  % (peak_hour, ", ".join("%02d:00" % hour for hour, _ in busy))]
    if data["weekdays"]:
        peak_day = data["weekdays"].most_common(1)[0][0]
        lines.append("Самый активный день недели: %s" % WEEKDAY_NAMES[peak_day])

    lines += ["", "<b>Качество</b>"]
    lines.append("Разборов выдано: %d · карт построено: %d"
                 % (data["readings"], data["charts_built"]))
    if data["median_ms"] is not None:
        lines.append("Сборка ответа: медиана %d мс, худшая %d мс"
                     % (data["median_ms"], data["slowest_ms"]))
    if data["errors"] or data["flood"]:
        lines.append("Ошибок: %d · срабатываний антифлуда: %d"
                     % (data["errors"], data["flood"]))

    return "\n".join(lines)
