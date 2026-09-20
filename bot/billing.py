# -*- coding: utf-8 -*-
"""Учёт разборов: сколько человек уже получил и сколько ему положено.

Модуль готовит бота к платной модели, но сам ничего не продаёт. Он знает
только две вещи: сколько разборов по темам чат уже израсходовал и сколько
у него оплаченных. Как именно приходят оплаченные — звёздами Telegram,
картой через провайдера или вручную от владельца — решается отдельно и
этого файла не касается.

<b>Пока выключено.</b> Без переменной PAYWALL=1 функции ведут учёт, но
никого не ограничивают: бот работает ровно как раньше. Это сделано
нарочно — счётчик должен накопить данные и быть проверенным до того, как
им начнут кому-то отказывать.

<b>Почему счётчик переживает /reset.</b> Команда /reset стирает анкеты —
это персональные данные, и человек вправе их удалить. Но если вместе с
ними обнулять и счётчик бесплатных разборов, лимит обходится одной
командой, и платная модель не работает вовсе. Поэтому счётчик лежит
отдельным файлом, а в тексте /about это сказано прямо.

<b>Что здесь не хранится.</b> Ни имён, ни дат рождения, ни вопросов —
только номер чата, счётчики и даты. Для отчётов эти данные не нужны: их
собирает stats.py, и там вместо номера чата стоит необратимый хеш.
"""
import datetime as dt
import json
import logging
import os
import tempfile
import threading

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
# Папку можно переопределить — тесты пишут во временную
STORE_DIR = (os.environ.get("BILLING_DIR") or os.environ.get("STORE_DIR")
             or os.path.join(BASE_DIR, "store"))
LEDGER_PATH = os.path.join(STORE_DIR, "usage.json")

log = logging.getLogger("tarot_astro_bot.billing")

# Платная модель включается переменной окружения, а не правкой кода:
# выключить её на работающем боте нужно уметь за одну перезагрузку.
ENABLED = (os.environ.get("PAYWALL") or "").strip() == "1"

# Сколько разборов по темам человек получает бесплатно за всё время.
FREE_READINGS = int(os.environ.get("FREE_READINGS") or 1)

# Замок на «прочитать файл — изменить — записать». Мини-приложение отвечает
# в нескольких потоках сразу, и без замка два одновременных разбора читают
# одинаковое «израсходовано», а записывают одно и то же значение: второе
# списание пропадает. На проверке 30 одновременных разборов так терялось 26
# из 30 — то есть человек получал их бесплатно. Бот и приложение живут в
# одном процессе, поэтому обычного замка достаточно.
_lock = threading.RLock()


def _today():
    return dt.date.today().isoformat()


def load_all():
    """Весь файл: {chat_id: {used, paid, first, last}}. Ошибки не выбрасываем."""
    if not os.path.exists(LEDGER_PATH):
        return {}
    try:
        with open(LEDGER_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        # Файл повреждён. Падать нельзя — бот должен отвечать, поэтому
        # счётчики начинаются заново. Но сначала откладываем испорченный
        # файл в сторону: иначе первое же списание запишет поверх него, и
        # оплаченные разборы всех клиентов исчезнут безвозвратно. С копией
        # их можно вернуть руками или из бэкапа.
        _set_aside()
        log.error("файл учёта повреждён, счётчики начинаются заново",
                  exc_info=True)
        return {}


def _set_aside():
    """Сохраняет испорченный файл учёта под другим именем."""
    spoiled = "%s.corrupt-%s" % (LEDGER_PATH,
                                 dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    try:
        os.replace(LEDGER_PATH, spoiled)
        log.error("испорченный файл учёта сохранён как %s", spoiled)
    except OSError:
        log.exception("не удалось отложить испорченный файл учёта")


def _write_all(data):
    """Атомарная запись: временный файл рядом и переименование."""
    temporary = None
    try:
        os.makedirs(STORE_DIR, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=STORE_DIR, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=1)
        os.replace(temporary, LEDGER_PATH)
        return True
    except OSError:
        # Не переименовался — значит остался лежать в папке данных. Без
        # уборки такие обрывки копятся молча, пока не кончится место на
        # диске, и тогда перестаёт записываться уже всё остальное
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
        log.error("не удалось сохранить счётчики", exc_info=True)
        return False


def _record(data, chat_id):
    record = data.get(str(chat_id)) or {}
    payments = record.get("payments")
    return {
        "used": int(record.get("used") or 0),
        "paid": int(record.get("paid") or 0),
        "first": record.get("first") or "",
        "last": record.get("last") or "",
        # Номера оплат: без них звёзды нельзя вернуть, а вернуть их
        # Telegram требует уметь
        "payments": list(payments) if isinstance(payments, list) else [],
    }


def state(chat_id):
    """Что известно про чат: сколько израсходовано и сколько оплачено."""
    return _record(load_all(), chat_id)


def free_left(chat_id):
    """Сколько бесплатных разборов осталось (не меньше нуля)."""
    return max(0, FREE_READINGS - state(chat_id)["used"])


def can_read(chat_id):
    """Положен ли этому чату ещё один разбор по теме.

    При выключенной платной модели — всегда да; счётчик при этом всё равно
    ведётся, чтобы ко дню запуска было видно реальное распределение.
    """
    if not ENABLED:
        return True
    record = state(chat_id)
    return record["used"] < FREE_READINGS or record["paid"] > 0


def charge(chat_id):
    """Отмечает выданный разбор. Возвращает «free», «paid», «over» или None.

    Сначала тратится бесплатный лимит, потом оплаченные разборы. Если не
    осталось ни того, ни другого, расход всё равно записывается: при
    выключенной платной модели это и есть нормальный режим, а при
    включённой — сигнал, что кто-то прошёл мимо проверки.

    None означает, что записать расход не удалось — кончилось место,
    слетели права. Раньше эта неудача молча проглатывалась: разбор
    уходил человеку, а списание не сохранялось, и при включённой оплате
    бот раздавал платные разборы даром, пока кто-нибудь не заметит.
    Теперь о ней узнаёт вызывающий и решает, выдавать ли разбор.
    """
    with _lock:
        data = load_all()
        chat = str(chat_id)
        record = _record(data, chat_id)

        if not ENABLED:
            # Платная модель выключена — разборы бесплатны для всех, и
            # тратить чей-то оплаченный запас в это время нельзя: человек
            # заплатил за то, что сейчас раздаётся даром. Расход всё равно
            # считаем, иначе пропадёт статистика.
            kind = "free"
        elif record["used"] < FREE_READINGS:
            kind = "free"
        elif record["paid"] > 0:
            record["paid"] -= 1
            kind = "paid"
        else:
            kind = "over"

        record["used"] += 1
        record["first"] = record["first"] or _today()
        record["last"] = _today()
        data[chat] = record
        if not _write_all(data):
            return None
        return kind


def pay(chat_id, charge_id, count, stars):
    """Начисляет разборы за оплату звёздами. Возвращает True, если начислил.

    Повторный платёж с тем же номером не начисляется ничего. Это не
    перестраховка: Telegram повторяет подтверждение, пока бот не примет
    обновление, и при неудачном перезапуске человек получил бы вдвое
    больше оплаченного — а заметили бы мы это по расходящимся деньгам.
    """
    if not charge_id or count <= 0:
        return False
    with _lock:
        data = load_all()
        chat = str(chat_id)
        record = _record(data, chat_id)
        if any(item.get("id") == charge_id for item in record["payments"]):
            log.info("платёж %s уже начислен, пропускаю", charge_id)
            return False

        record["paid"] += int(count)
        record["first"] = record["first"] or _today()
        record["payments"].append({
            "id": charge_id,
            "count": int(count),
            "stars": int(stars),
            "date": _today(),
        })
        # Храним последние полсотни: этого с запасом хватает на возвраты,
        # а файл не растёт без предела у постоянных клиентов
        record["payments"] = record["payments"][-50:]
        data[chat] = record
        return _write_all(data)


def payments(chat_id):
    """Оплаты этого чата, свежие в конце."""
    return state(chat_id)["payments"]


def find_payment(charge_id):
    """Ищет оплату по номеру: (номер чата, запись) или (None, None)."""
    for chat, record in load_all().items():
        for item in (record.get("payments") or []):
            if item.get("id") == charge_id:
                return chat, item
    return None, None


def mark_refunded(chat_id, charge_id):
    """Отмечает возврат: снимает неиспользованные разборы и помечает оплату.

    Если человек уже потратил оплаченное, отнимать нечего — уводить
    счётчик в минус нельзя, иначе он потом получит разборы, за которые
    никто не платил.
    """
    with _lock:
        data = load_all()
        chat = str(chat_id)
        record = _record(data, chat_id)
        found = None
        for item in record["payments"]:
            if item.get("id") == charge_id:
                found = item
                break
        if found is None or found.get("refunded"):
            return False
        found["refunded"] = _today()
        record["paid"] = max(0, record["paid"] - int(found.get("count") or 0))
        data[chat] = record
        return _write_all(data)


def grant(chat_id, count=1):
    """Начисляет оплаченные разборы. Сюда придёт код платежей, когда будет."""
    if count <= 0:
        return False
    with _lock:
        data = load_all()
        chat = str(chat_id)
        record = _record(data, chat_id)
        record["paid"] += int(count)
        record["first"] = record["first"] or _today()
        data[chat] = record
        return _write_all(data)


def forget(chat_id):
    """Полностью забывает чат — и счётчик, и оплаченное.

    Нужна для настоящего удаления данных по требованию человека. Обычная
    команда /reset её не вызывает: иначе бесплатный лимит обнулялся бы по
    желанию, и платная модель не работала бы.
    """
    with _lock:
        data = load_all()
        chat = str(chat_id)
        if chat not in data:
            return False
        data.pop(chat)
        return _write_all(data)


def totals():
    """Сводка для владельца: сколько чатов, разборов и оплаченного остатка."""
    data = load_all()
    return {
        "chats": len(data),
        "readings": sum(int(item.get("used") or 0) for item in data.values()),
        "paid_left": sum(int(item.get("paid") or 0) for item in data.values()),
        "over_free": sum(1 for item in data.values()
                         if int(item.get("used") or 0) > FREE_READINGS),
    }
