# -*- coding: utf-8 -*-
"""Проверка учёта разборов — основы будущей платной модели.

Запуск: python scripts/test_billing.py

Что здесь важнее арифметики:

1. <b>Выключенная модель ничего не меняет.</b> Пока PAYWALL не равен «1»,
   бот обязан вести себя ровно как раньше — считать, но никому не
   отказывать. Если это сломается, клиенты упрутся в платный барьер,
   которого никто не включал.
2. <b>Лимит не обходится.</b> Ни командой /reset, ни новой анкетой, ни
   перезапуском бота. Бесплатный разбор — один за всё время.
3. <b>Счётчик не теряется.</b> Оплаченное и израсходованное переживают
   перезапуск и повреждение файла не роняет бота.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import billing  # noqa: E402

PLACE = {"name": "Москва", "country": "RU", "lat": 55.7522, "lon": 37.6156,
         "timezone": "Europe/Moscow"}


def with_temp_ledger(function):
    """Каждая проверка работает со своим файлом учёта."""
    def wrapper(errors):
        folder = tempfile.mkdtemp()
        saved = (billing.STORE_DIR, billing.LEDGER_PATH, billing.ENABLED,
                 billing.FREE_READINGS)
        billing.STORE_DIR = folder
        billing.LEDGER_PATH = os.path.join(folder, "usage.json")
        try:
            function(errors)
        finally:
            (billing.STORE_DIR, billing.LEDGER_PATH, billing.ENABLED,
             billing.FREE_READINGS) = saved
            shutil.rmtree(folder, ignore_errors=True)
    wrapper.__name__ = function.__name__
    return wrapper


# ---------------------------------------------------------------------------
# Выключенная платная модель
# ---------------------------------------------------------------------------

@with_temp_ledger
def check_disabled_never_blocks(errors):
    """PAYWALL выключен — бот не отказывает никому, но считает."""
    billing.ENABLED = False
    for number in range(5):
        if not billing.can_read(42):
            errors.append("при выключенной модели отказано на %d-м разборе"
                          % (number + 1))
            break
        billing.charge(42)

    record = billing.state(42)
    if record["used"] != 5:
        errors.append("счётчик не ведётся при выключенной модели: %r" % record)


@with_temp_ledger
def check_enabled_blocks_after_free(errors):
    """PAYWALL включён — первый разбор бесплатный, второй уже нет."""
    billing.ENABLED = True
    billing.FREE_READINGS = 1

    if not billing.can_read(42):
        errors.append("новому чату отказано в бесплатном разборе")
    if billing.charge(42) != "free":
        errors.append("первый разбор списан не как бесплатный")
    if billing.can_read(42):
        errors.append("второй разбор разрешён без оплаты")
    if billing.free_left(42) != 0:
        errors.append("бесплатный лимит не израсходован: %d"
                      % billing.free_left(42))


@with_temp_ledger
def check_paid_readings(errors):
    """Оплаченные разборы тратятся после бесплатных и заканчиваются."""
    billing.ENABLED = True
    billing.FREE_READINGS = 1

    billing.charge(42)                       # бесплатный
    billing.grant(42, 2)

    for number in (1, 2):
        if not billing.can_read(42):
            errors.append("оплаченный разбор %d не выдан" % number)
        if billing.charge(42) != "paid":
            errors.append("разбор %d списан не с оплаченных" % number)

    if billing.can_read(42):
        errors.append("разбор выдан, когда оплаченные кончились")
    if billing.state(42)["used"] != 3:
        errors.append("израсходовано не три разбора: %r" % billing.state(42))

    if billing.grant(42, 0) or billing.grant(42, -5):
        errors.append("начисление нуля или минуса прошло как успешное")


@with_temp_ledger
def check_free_limit_is_configurable(errors):
    """FREE_READINGS задаёт, сколько разборов бесплатны."""
    billing.ENABLED = True
    billing.FREE_READINGS = 3

    for number in range(3):
        if not billing.can_read(42):
            errors.append("отказано на %d-м из трёх бесплатных" % (number + 1))
        billing.charge(42)
    if billing.can_read(42):
        errors.append("четвёртый разбор выдан при лимите в три")


@with_temp_ledger
def check_chats_are_independent(errors):
    """Израсходованный лимит одного чата не задевает другой."""
    billing.ENABLED = True
    billing.FREE_READINGS = 1

    billing.charge(42)
    if not billing.can_read(43):
        errors.append("соседний чат остался без бесплатного разбора")
    if billing.state(43)["used"] != 0:
        errors.append("чужой расход записался соседнему чату")


# ---------------------------------------------------------------------------
# Лимит не обходится
# ---------------------------------------------------------------------------

@with_temp_ledger
def check_reset_does_not_refill(errors):
    """Главное: /reset стирает анкеты, но не возвращает бесплатный разбор."""
    import bot as B
    import store

    saved_api = B.api_call
    saved_enabled = B.billing.ENABLED
    store_folder = tempfile.mkdtemp()
    saved_store = (store.STORE_DIR, store.PROFILES_PATH)
    store.STORE_DIR = store_folder
    store.PROFILES_PATH = os.path.join(store_folder, "profiles.json")
    sent = []
    B.api_call = lambda method, **params: sent.append((method, params)) or {"ok": True}
    try:
        billing.ENABLED = True
        billing.FREE_READINGS = 1
        billing.charge(42)

        store.remember(42, {"name": "Анна", "birth": dt.date(1990, 3, 15)})
        B.handle_message(42, "/reset", None)

        if store.people(42):
            errors.append("/reset не стёр анкеты")
        if billing.can_read(42):
            errors.append("/reset вернул израсходованный бесплатный разбор — "
                          "лимит обходится одной командой")
        if billing.state(42)["used"] != 1:
            errors.append("/reset обнулил счётчик разборов")

        # и человек об этом предупреждён
        text = [params.get("text", "") for method, params in sent
                if method == "sendMessage"][-1]
        if "счётчик" not in text.lower():
            errors.append("после /reset бот не сказал, что счётчик остаётся: "
                          "%r" % text[:120])
    finally:
        B.api_call = saved_api
        billing.ENABLED = saved_enabled
        store.STORE_DIR, store.PROFILES_PATH = saved_store
        shutil.rmtree(store_folder, ignore_errors=True)


@with_temp_ledger
def check_counter_survives_restart(errors):
    """Перезапуск бота не возвращает бесплатный разбор."""
    billing.ENABLED = True
    billing.FREE_READINGS = 1
    billing.charge(42)
    billing.grant(42, 1)

    # «перезапуск» — это чтение того же файла заново, состояние в памяти
    # модуль не держит
    record = json.loads(open(billing.LEDGER_PATH, encoding="utf-8").read())["42"]
    if record.get("used") != 1 or record.get("paid") != 1:
        errors.append("на диск записалось не то: %r" % record)
    if billing.state(42)["used"] != 1:
        errors.append("после чтения с диска счётчик другой")


@with_temp_ledger
def check_forget_wipes_everything(errors):
    """Полное удаление по требованию — отдельная операция, не /reset."""
    billing.ENABLED = True
    billing.FREE_READINGS = 1
    billing.charge(42)
    billing.grant(42, 3)

    if not billing.forget(42):
        errors.append("удаление чата не сработало")
    if billing.state(42)["used"] or billing.state(42)["paid"]:
        errors.append("после удаления остались счётчики")
    if billing.forget(42):
        errors.append("повторное удаление сообщило об успехе")


# ---------------------------------------------------------------------------
# Устойчивость файла
# ---------------------------------------------------------------------------

@with_temp_ledger
def check_corrupted_ledger(errors):
    """Битый файл не роняет бота и не мешает писать дальше."""
    os.makedirs(billing.STORE_DIR, exist_ok=True)
    with open(billing.LEDGER_PATH, "w", encoding="utf-8") as handle:
        handle.write('{"42": {"used": 1, "pai')

    try:
        record = billing.state(42)
    except Exception as failure:
        errors.append("повреждённый файл уронил учёт: %r" % failure)
        return
    if record["used"]:
        errors.append("из обрезанного файла прочитались счётчики: %r" % record)

    billing.charge(42)
    if billing.state(42)["used"] != 1:
        errors.append("после повреждения файл больше не пишется")

    leftovers = [name for name in os.listdir(billing.STORE_DIR)
                 if name.endswith(".tmp")]
    if leftovers:
        errors.append("остались временные файлы записи: %r" % leftovers)


@with_temp_ledger
def check_totals(errors):
    """Сводка для владельца считает то, что обещает."""
    billing.ENABLED = True
    billing.FREE_READINGS = 1

    billing.charge(42)               # чат в пределах бесплатного
    billing.grant(43, 2)
    billing.charge(43)
    billing.charge(43)               # чат вышел за бесплатный лимит

    totals = billing.totals()
    if totals["chats"] != 2:
        errors.append("чатов посчитано %d вместо двух" % totals["chats"])
    if totals["readings"] != 3:
        errors.append("разборов посчитано %d вместо трёх" % totals["readings"])
    if totals["over_free"] != 1:
        errors.append("вышедших за лимит %d вместо одного" % totals["over_free"])
    if totals["paid_left"] != 1:
        errors.append("остаток оплаченных %d вместо одного" % totals["paid_left"])


# ---------------------------------------------------------------------------
# Поведение бота
# ---------------------------------------------------------------------------

@with_temp_ledger
def check_bot_refuses_before_asking_anketa(errors):
    """Отказ приходит до анкеты, а не после сбора данных."""
    import bot as B

    sent = []
    saved_api = B.api_call
    B.api_call = lambda method, **params: sent.append((method, params)) or {"ok": True}
    try:
        billing.ENABLED = True
        billing.FREE_READINGS = 1
        billing.charge(42)
        B.PROFILES.pop(42, None)
        B.DIALOGS.pop(42, None)

        B.start_sphere(42, "love")
        text = [params.get("text", "") for method, params in sent
                if method == "sendMessage"][-1]
        if "Бесплатный разбор" not in text:
            errors.append("вместо объяснения пришло другое: %r" % text[:120])
        if "Как к вам обращаться" in text:
            errors.append("бот начал собирать анкету, хотя разбор платный")
        if 42 in B.DIALOGS:
            errors.append("после отказа остался открытый диалог")

        # бесплатные возможности названы: человек не должен уйти ни с чем
        for command in ("/week", "/chart", "/random"):
            if command not in text:
                errors.append("в отказе не назван бесплатный %s" % command)
    finally:
        B.api_call = saved_api
        B.PROFILES.pop(42, None)
        B.DIALOGS.pop(42, None)


@with_temp_ledger
def check_bot_allows_first_reading(errors):
    """Первый разбор проходит и списывается ровно один раз."""
    import bot as B
    import natal

    saved_api = B.api_call
    B.api_call = lambda method, **params: {"ok": True}
    try:
        billing.ENABLED = True
        billing.FREE_READINGS = 1

        chart = None
        if natal.available():
            try:
                chart = natal.build_chart(dt.date(1990, 3, 15), "14:30", PLACE)
            except Exception:
                chart = None
        B.PROFILES[42] = {"name": "Анна", "birth": dt.date(1990, 3, 15),
                          "time": "14:30", "place": PLACE, "chart": chart}

        B.deliver_reading(42, "love")
        if billing.state(42)["used"] != 1:
            errors.append("разбор списался %d раз вместо одного"
                          % billing.state(42)["used"])
        if billing.can_read(42):
            errors.append("после первого разбора лимит не закрылся")
    finally:
        B.api_call = saved_api
        B.PROFILES.pop(42, None)


@with_temp_ledger
def check_forecasts_are_free(errors):
    """Прогнозы, карта дня и натальная карта лимит не тратят."""
    import bot as B
    import natal

    saved_api = B.api_call
    B.api_call = lambda method, **params: {"ok": True}
    try:
        billing.ENABLED = True
        billing.FREE_READINGS = 1

        chart = None
        if natal.available():
            try:
                chart = natal.build_chart(dt.date(1990, 3, 15), "14:30", PLACE)
            except Exception:
                chart = None
        B.PROFILES[42] = {"name": "Анна", "birth": dt.date(1990, 3, 15),
                          "time": "14:30", "place": PLACE, "chart": chart}

        B.deliver_period(42, "week")
        B.deliver_period(42, "year")
        B.handle_random(42)
        if chart:
            B.deliver_chart(42)
            B.deliver_transits(42)

        if billing.state(42)["used"] != 0:
            errors.append("бесплатные разделы потратили лимит: %r"
                          % billing.state(42))
    finally:
        B.api_call = saved_api
        B.PROFILES.pop(42, None)


def main():
    errors = []
    for check in (check_disabled_never_blocks, check_enabled_blocks_after_free,
                  check_paid_readings, check_free_limit_is_configurable,
                  check_chats_are_independent, check_reset_does_not_refill,
                  check_counter_survives_restart, check_forget_wipes_everything,
                  check_corrupted_ledger, check_totals,
                  check_bot_refuses_before_asking_anketa,
                  check_bot_allows_first_reading, check_forecasts_are_free):
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
