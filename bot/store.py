# -*- coding: utf-8 -*-
"""
Долговременная память: анкеты клиентов между перезапусками.

До этого модуля бот держал анкеты только в оперативной памяти: перезапуск —
и человек заново диктует дату, время и город. Удобно для приватности,
неудобно для жизни, поэтому анкеты переезжают на диск — но с оговорками,
которые здесь же и реализованы.

<b>Что хранится.</b> Имя, дата, время и место рождения — то, без чего
нельзя построить карту. Вопросы клиента, тексты разборов и сами карты не
сохраняются. Натальная карта тоже не хранится: она выводится из анкеты и
пересчитывается за сотые доли секунды, а держать её на диске — значит
раздувать файл вчетверо без пользы.

<b>Сколько хранится.</b> Анкеты, к которым не обращались RETENTION_DAYS
дней, удаляются при первой же загрузке. Это не только вежливость: данные,
которые не нужны, не должны лежать «на всякий случай».

<b>Как хранится.</b> Один JSON-файл, запись атомарная — во временный файл
и переименованием. Если бот убьют посреди сохранения, старый файл
останется целым, а не превратится в половину записи.

Клиент управляет этим сам: /people показывает сохранённых людей, /reset
стирает всё без следа.
"""
import datetime as dt
import json
import logging
import os
import tempfile

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
# Папку можно переопределить переменной окружения — тесты пишут во временную
STORE_DIR = os.environ.get("STORE_DIR") or os.path.join(BASE_DIR, "store")
PROFILES_PATH = os.path.join(STORE_DIR, "profiles.json")
HISTORY_PATH = os.path.join(STORE_DIR, "history.json")

log = logging.getLogger("tarot_astro_bot.store")

# Сколько дней анкета живёт без обращений
RETENTION_DAYS = 180

# Сколько людей помним на один чат: себя, партнёра, пару близких —
# дальше список превращается в свалку, по которой неудобно выбирать
MAX_PEOPLE = 5

# Поля анкеты, которые попадают на диск. Всё остальное (вопрос клиента,
# рассчитанная карта, служебные пометки) не сохраняется.
SAVED_FIELDS = ("name", "birth", "time", "place")

# Сколько прошлых разборов помним. Больше пяти не нужно: дальше в глаза
# бросаются уже не совпадения, а случайности — в колоде 78 карт, и на
# длинной истории что-нибудь совпадает всегда.
MAX_HISTORY = 5


def _today():
    return dt.date.today().isoformat()


def load_all():
    """Весь файл целиком: {chat_id: [анкеты]}. Ошибки не выбрасываем."""
    if not os.path.exists(PROFILES_PATH):
        return {}
    try:
        with open(PROFILES_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        log.warning("файл анкет повреждён, начинаю с чистого", exc_info=True)
        return {}


def _write_json(path, data, what):
    """Атомарная запись: временный файл рядом и переименование.

    Если переименовать не удалось, временный файл убираем за собой.
    Без этого каждая неудачная запись оставляет в папке данных обрывок,
    и однажды их накапливается столько, что кончается место на диске.
    """
    temporary = None
    try:
        os.makedirs(STORE_DIR, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=STORE_DIR, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=1)
        os.replace(temporary, path)
        return True
    except OSError:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
        log.error("не удалось сохранить %s", what, exc_info=True)
        return False


def _write_all(data):
    return _write_json(PROFILES_PATH, data, "анкеты")


def _prune(data, today=None):
    """Убирает анкеты, к которым давно не обращались."""
    today = today or dt.date.today()
    limit = (today - dt.timedelta(days=RETENTION_DAYS)).isoformat()
    cleaned, removed = {}, 0
    for chat_id, people in data.items():
        alive = [person for person in people
                 if (person.get("used") or person.get("saved") or "") >= limit]
        removed += len(people) - len(alive)
        if alive:
            cleaned[chat_id] = alive
    if removed:
        log.info("удалено анкет по сроку хранения: %d", removed)
    return cleaned, removed


def _serialise(profile):
    saved = {}
    for field in SAVED_FIELDS:
        value = profile.get(field)
        if field == "birth" and value:
            saved[field] = value.isoformat()
        elif value:
            saved[field] = value
    return saved


def _deserialise(saved):
    profile = dict(saved)
    if isinstance(profile.get("birth"), str):
        try:
            profile["birth"] = dt.date.fromisoformat(profile["birth"])
        except ValueError:
            return None
    return profile if profile.get("birth") else None


def people(chat_id):
    """Сохранённые анкеты чата, свежие — первыми."""
    data, removed = _prune(load_all())
    if removed:
        _write_all(data)
    saved = data.get(str(chat_id), [])
    result = []
    for record in saved:
        profile = _deserialise(record)
        if profile:
            profile["_used"] = record.get("used", "")
            result.append(profile)
    result.sort(key=lambda item: item.get("_used", ""), reverse=True)
    return result


def remember(chat_id, profile):
    """Сохраняет анкету. Одноимённые перезаписываются, лишние вытесняются."""
    if not profile or not profile.get("birth"):
        return False

    data, _ = _prune(load_all())
    chat = str(chat_id)
    record = _serialise(profile)
    record["saved"] = _today()
    record["used"] = _today()

    others = [item for item in data.get(chat, [])
              if not _same_person(item, record)]
    data[chat] = ([record] + others)[:MAX_PEOPLE]
    return _write_all(data)


def touch(chat_id, profile):
    """Отмечает, что анкетой только что пользовались — она живёт дальше."""
    return remember(chat_id, profile)


def _same_person(saved, record):
    """Один и тот же человек — это совпадение имени и даты рождения."""
    return ((saved.get("name") or "").strip().lower()
            == (record.get("name") or "").strip().lower()
            and saved.get("birth") == record.get("birth"))


def forget(chat_id):
    """Удаляет всё, что помнил об этом чате: и анкеты, и историю разборов."""
    forget_history(chat_id)
    data = load_all()
    chat = str(chat_id)
    if chat not in data:
        return False
    data.pop(chat)
    return _write_all(data)


def forget_person(chat_id, index):
    """Удаляет одну анкету по её номеру в списке people()."""
    saved = people(chat_id)
    if not 0 <= index < len(saved):
        return False
    target = _serialise(saved[index])
    data = load_all()
    chat = str(chat_id)
    data[chat] = [item for item in data.get(chat, [])
                  if not _same_person(item, target)]
    if not data[chat]:
        data.pop(chat)
    return _write_all(data)


# ---------------------------------------------------------------------------
# История разборов
# ---------------------------------------------------------------------------

def _load_history():
    if not os.path.exists(HISTORY_PATH):
        return {}
    try:
        with open(HISTORY_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        log.warning("файл истории повреждён, начинаю с чистого", exc_info=True)
        return {}


def _write_history(data):
    return _write_json(HISTORY_PATH, data, "историю разборов")


def remember_reading(chat_id, sphere, cards, on_date=None):
    """Запоминает выданный разбор: тема, дата и номера карт.

    Карты храним парами «идентификатор, перевёрнута ли» — по ним потом
    видно, что человеку третий раз подряд выпадает одно и то же. Текстов
    здесь нет и не будет: они каждый раз собираются заново.
    """
    if not sphere or not cards:
        return False
    data = _load_history()
    chat = str(chat_id)
    record = {
        "sphere": sphere,
        "date": (on_date or dt.date.today()).isoformat(),
        "cards": [[item["card"]["id"], bool(item["reversed"])] for item in cards],
    }
    items = [item for item in data.get(chat, [])]
    # Расклад за день не меняется: если человек открыл ту же тему второй раз
    # за сегодня, это тот же самый расклад, а не новый визит
    if items and (items[0].get("sphere"), items[0].get("date"),
                  items[0].get("cards")) == (record["sphere"], record["date"],
                                             record["cards"]):
        return True
    data[chat] = ([record] + items)[:MAX_HISTORY]
    return _write_history(data)


def past_readings(chat_id, limit=MAX_HISTORY):
    """Прошлые разборы чата, свежие — первыми."""
    items = _load_history().get(str(chat_id), [])
    return [item for item in items if item.get("cards")][:limit]


def forget_history(chat_id):
    """Забывает историю разборов чата. Вызывается вместе с /reset."""
    data = _load_history()
    chat = str(chat_id)
    if chat not in data:
        return False
    data.pop(chat)
    return _write_history(data)


def label(profile):
    """Подпись для кнопки: «Анна · 15.03.1990 · Москва»."""
    parts = [(profile.get("name") or "Без имени").strip()]
    birth = profile.get("birth")
    if birth:
        parts.append(birth.strftime("%d.%m.%Y"))
    place = profile.get("place") or {}
    if place.get("name"):
        parts.append(place["name"])
    return " · ".join(parts)
