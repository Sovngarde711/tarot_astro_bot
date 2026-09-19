# -*- coding: utf-8 -*-
"""Проверка долговременной памяти: анкеты между перезапусками.

Запуск: python scripts/test_store.py

Здесь проверяется не только «сохранилось и прочиталось». Анкета — это
персональные данные, поэтому отдельно проверяем обратное: что на диск не
попадает лишнее, что срок хранения действительно истекает, что /reset
стирает файл, а повреждённый файл не роняет бота и не возвращает чужие
данные.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import store  # noqa: E402


def with_temp_storage(function):
    """Каждая проверка работает в своей папке, не трогая настоящие анкеты."""
    def wrapper(errors):
        folder = tempfile.mkdtemp()
        saved = (store.STORE_DIR, store.PROFILES_PATH)
        store.STORE_DIR = folder
        store.PROFILES_PATH = os.path.join(folder, "profiles.json")
        try:
            function(errors)
        finally:
            store.STORE_DIR, store.PROFILES_PATH = saved
            shutil.rmtree(folder, ignore_errors=True)
    wrapper.__name__ = function.__name__
    return wrapper


def anketa(name="Ирина", year=1990, month=3, day=15, time_="14:30",
           city="Москва"):
    return {"name": name, "birth": dt.date(year, month, day), "time": time_,
            "place": {"name": city, "country": "RU", "lat": 55.75,
                      "lon": 37.62, "timezone": "Europe/Moscow"}}


# ---------------------------------------------------------------------------
# Что и как ложится на диск
# ---------------------------------------------------------------------------

@with_temp_storage
def check_roundtrip(errors):
    profile = anketa()
    if not store.remember(42, profile):
        errors.append("анкета не сохранилась")

    back = store.people(42)
    if len(back) != 1:
        errors.append("после сохранения ожидалась одна анкета, получено %d"
                      % len(back))
        return
    restored = back[0]
    if restored.get("name") != "Ирина":
        errors.append("имя не пережило сохранение: %r" % restored.get("name"))
    if restored.get("birth") != dt.date(1990, 3, 15):
        errors.append("дата вернулась не датой, а %r" % (restored.get("birth"),))
    if restored.get("time") != "14:30":
        errors.append("время рождения потерялось: %r" % restored.get("time"))
    if (restored.get("place") or {}).get("lat") != 55.75:
        errors.append("координаты города потерялись: %r" % (restored.get("place"),))

    # чужой чат не видит эту анкету
    if store.people(43):
        errors.append("анкета видна из чужого чата — утечка данных")


@with_temp_storage
def check_extra_fields_not_saved(errors):
    """Вопрос клиента и рассчитанная карта на диск не попадают."""
    profile = anketa()
    profile["question"] = "почему не складывается с партнёром"
    profile["chart"] = {"planets": {"sun": {"lon": 354.5}}, "has_houses": True}
    store.remember(42, profile)

    raw = open(store.PROFILES_PATH, encoding="utf-8").read()
    for secret in ("не складывается", "question", "chart", "has_houses"):
        if secret in raw:
            errors.append("на диск попало лишнее: %r" % secret)

    record = json.loads(raw)["42"][0]
    allowed = set(store.SAVED_FIELDS) | {"saved", "used"}
    for field in record:
        if field not in allowed:
            errors.append("поле %r сохранено вопреки белому списку" % field)


@with_temp_storage
def check_partial_anketa(errors):
    """Только дата — тоже анкета; без даты сохранять нечего."""
    store.remember(42, {"name": "", "birth": dt.date(1985, 7, 1)})
    saved = store.people(42)
    if len(saved) != 1:
        errors.append("анкета из одной даты не сохранилась")
    elif saved[0].get("time") or saved[0].get("place"):
        errors.append("пустые поля сохранились как значения: %r" % (saved[0],))

    if store.remember(43, {"name": "Никто"}):
        errors.append("анкета без даты рождения принята к сохранению")
    if store.people(43):
        errors.append("анкета без даты всё-таки оказалась на диске")


# ---------------------------------------------------------------------------
# Несколько людей на один чат
# ---------------------------------------------------------------------------

@with_temp_storage
def check_multiple_people(errors):
    store.remember(42, anketa("Ирина", 1990, 3, 15))
    store.remember(42, anketa("Пётр", 1988, 11, 2, city="Казань"))

    saved = store.people(42)
    if len(saved) != 2:
        errors.append("ожидались два человека, получено %d" % len(saved))
        return
    if saved[0].get("name") != "Пётр":
        errors.append("последний сохранённый человек не оказался первым: %r"
                      % saved[0].get("name"))


@with_temp_storage
def check_same_person_updated(errors):
    """Тот же человек с уточнённым временем — не второй пункт в списке."""
    store.remember(42, anketa("Ирина", time_=None, city="Москва"))
    store.remember(42, anketa("Ирина", time_="14:30", city="Москва"))

    saved = store.people(42)
    if len(saved) != 1:
        errors.append("одна и та же анкета задвоилась: %d записей" % len(saved))
    elif saved[0].get("time") != "14:30":
        errors.append("уточнённое время не перезаписало старую анкету")


@with_temp_storage
def check_limit(errors):
    for index in range(store.MAX_PEOPLE + 3):
        store.remember(42, anketa("Человек %d" % index, 1980 + index, 1, 1))
    saved = store.people(42)
    if len(saved) != store.MAX_PEOPLE:
        errors.append("список людей не ограничен: %d при лимите %d"
                      % (len(saved), store.MAX_PEOPLE))
    names = [person.get("name") for person in saved]
    if "Человек 0" in names:
        errors.append("вытеснился не самый старый человек: %r" % names)
    if "Человек %d" % (store.MAX_PEOPLE + 2) not in names:
        errors.append("последний добавленный человек не сохранился: %r" % names)


# ---------------------------------------------------------------------------
# Срок хранения
# ---------------------------------------------------------------------------

@with_temp_storage
def check_retention(errors):
    long_ago = (dt.date.today()
                - dt.timedelta(days=store.RETENTION_DAYS + 5)).isoformat()
    recently = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    handmade = {
        "42": [
            {"name": "Забытая", "birth": "1990-03-15", "used": long_ago},
            {"name": "Свежая", "birth": "1991-04-16", "used": recently},
        ],
        "43": [{"name": "Только старая", "birth": "1992-05-17", "used": long_ago}],
    }
    store._write_all(handmade)

    saved = store.people(42)
    names = [person.get("name") for person in saved]
    if "Забытая" in names:
        errors.append("анкета старше срока хранения не удалена: %r" % names)
    if "Свежая" not in names:
        errors.append("свежая анкета удалена вместе со старой: %r" % names)
    if store.people(43):
        errors.append("чат, где всё просрочено, остался в файле")

    on_disk = json.loads(open(store.PROFILES_PATH, encoding="utf-8").read())
    if "43" in on_disk:
        errors.append("просроченные данные остались в файле, хотя из ответа ушли")


@with_temp_storage
def check_touch_extends_life(errors):
    """Обращение к анкете продлевает её срок хранения."""
    long_ago = (dt.date.today()
                - dt.timedelta(days=store.RETENTION_DAYS - 1)).isoformat()
    store._write_all({"42": [{"name": "Ирина", "birth": "1990-03-15",
                              "used": long_ago}]})
    saved = store.people(42)
    if not saved:
        errors.append("анкета исчезла раньше срока")
        return
    store.touch(42, saved[0])

    record = json.loads(open(store.PROFILES_PATH, encoding="utf-8").read())["42"][0]
    if record.get("used") != dt.date.today().isoformat():
        errors.append("обращение не обновило дату последнего использования: %r"
                      % record.get("used"))


# ---------------------------------------------------------------------------
# Удаление
# ---------------------------------------------------------------------------

@with_temp_storage
def check_forget(errors):
    store.remember(42, anketa("Ирина"))
    store.remember(42, anketa("Пётр", 1988, 11, 2))
    store.remember(43, anketa("Соседний чат", 1975, 6, 6))

    store.forget(42)
    if store.people(42):
        errors.append("после /reset анкеты остались в памяти бота")
    raw = open(store.PROFILES_PATH, encoding="utf-8").read()
    for secret in ("Ирина", "Пётр", "1990-03-15"):
        if secret in raw:
            errors.append("после /reset данные остались в файле: %r" % secret)
    if not store.people(43):
        errors.append("/reset стёр данные соседнего чата")


@with_temp_storage
def check_forget_person(errors):
    store.remember(42, anketa("Ирина", 1990, 3, 15))
    store.remember(42, anketa("Пётр", 1988, 11, 2))

    saved = store.people(42)
    target = saved[0].get("name")
    store.forget_person(42, 0)

    left = [person.get("name") for person in store.people(42)]
    if target in left:
        errors.append("выбранный человек не удалился: %r" % left)
    if len(left) != 1:
        errors.append("удаление одного человека затронуло остальных: %r" % left)

    if store.forget_person(42, 7):
        errors.append("удаление несуществующего номера сообщило об успехе")


# ---------------------------------------------------------------------------
# Устойчивость файла
# ---------------------------------------------------------------------------

@with_temp_storage
def check_corrupted_file(errors):
    with open(store.PROFILES_PATH, "w", encoding="utf-8") as handle:
        handle.write('{"42": [{"name": "Ирина", "birth": "1990-0')

    try:
        saved = store.people(42)
    except Exception as failure:
        errors.append("повреждённый файл уронил бота: %r" % failure)
        return
    if saved:
        errors.append("из обрезанного файла прочитались данные: %r" % saved)

    # бот должен уметь писать поверх испорченного файла
    if not store.remember(42, anketa()):
        errors.append("после повреждения файл больше не пишется")
    if len(store.people(42)) != 1:
        errors.append("новая анкета не сохранилась после повреждения файла")


@with_temp_storage
def check_bad_date_ignored(errors):
    store._write_all({"42": [
        {"name": "Битая", "birth": "не дата", "used": dt.date.today().isoformat()},
        {"name": "Целая", "birth": "1990-03-15", "used": dt.date.today().isoformat()},
    ]})
    names = [person.get("name") for person in store.people(42)]
    if "Битая" in names:
        errors.append("запись с непарсящейся датой попала в список: %r" % names)
    if "Целая" not in names:
        errors.append("одна битая запись похоронила соседнюю целую: %r" % names)


@with_temp_storage
def check_atomic_write(errors):
    """После записи в папке не остаётся временных файлов."""
    for index in range(3):
        store.remember(42, anketa("Человек %d" % index, 1980 + index, 1, 1))
    leftovers = [name for name in os.listdir(store.STORE_DIR)
                 if name.endswith(".tmp")]
    if leftovers:
        errors.append("остались временные файлы записи: %r" % leftovers)
    json.loads(open(store.PROFILES_PATH, encoding="utf-8").read())


# ---------------------------------------------------------------------------
# Поведение бота: предложить сохранённое или взять новое
# ---------------------------------------------------------------------------

@with_temp_storage
def check_bot_offers_saved(errors):
    import bot as B

    sent = []
    saved_api = B.api_call
    B.api_call = lambda method, **params: sent.append((method, params)) or {"ok": True}
    try:
        B.PROFILES.pop(42, None)
        B.DIALOGS.pop(42, None)

        # первый раз бот ничего не помнит и спрашивает имя
        B.start_sphere(42, "love")
        first = [params for method, params in sent if method == "sendMessage"]
        if not first or "Как к вам обращаться" not in first[-1].get("text", ""):
            errors.append("новому клиенту бот не задал первый вопрос анкеты")

        # клиент оставил данные
        store.remember(42, anketa())
        B.PROFILES.pop(42, None)
        B.DIALOGS.pop(42, None)
        sent.clear()

        B.start_sphere(42, "work")
        message = [params for method, params in sent if method == "sendMessage"][-1]
        text = message.get("text", "")
        if "Ирина" not in text:
            errors.append("бот не предложил сохранённые данные: %r" % text[:120])
        buttons = message["reply_markup"]["inline_keyboard"]
        actions = [button["callback_data"] for row in buttons for button in row]
        if "person:0" not in actions:
            errors.append("нет кнопки «использовать сохранённые данные»: %r" % actions)
        if "person:new" not in actions:
            errors.append("нет кнопки «другой человек или дата»: %r" % actions)

        # выбираем сохранённого человека — анкету заново не спрашивают
        sent.clear()
        B.handle_callback({"id": "1", "data": "person:0",
                           "message": {"chat": {"id": 42}}}, None)
        text = [params for method, params in sent
                if method == "sendMessage"][-1].get("text", "")
        if "Как к вам обращаться" in text or "Дата рождения" in text:
            errors.append("после выбора сохранённых данных бот снова спросил анкету")
        if B.PROFILES.get(42, {}).get("birth") != dt.date(1990, 3, 15):
            errors.append("сохранённая дата не подставилась в текущий сеанс")

        # «другой человек» — анкета с нуля, но старая запись остаётся на диске
        sent.clear()
        B.DIALOGS[42] = {"sphere": "love", "step": "confirm", "data": {},
                         "people": store.people(42)}
        B.handle_callback({"id": "2", "data": "person:new",
                           "message": {"chat": {"id": 42}}}, None)
        text = [params for method, params in sent
                if method == "sendMessage"][-1].get("text", "")
        if "Как к вам обращаться" not in text:
            errors.append("кнопка «другой человек» не начала новую анкету: %r"
                          % text[:120])
        if not store.people(42):
            errors.append("«другой человек» стёр ранее сохранённую анкету")
    finally:
        B.api_call = saved_api
        B.PROFILES.pop(42, None)
        B.DIALOGS.pop(42, None)


@with_temp_storage
def check_bot_restores_after_restart(errors):
    """После перезапуска /chart и /week не требуют вводить дату заново."""
    import bot as B

    saved_api = B.api_call
    B.api_call = lambda method, **params: {"ok": True}
    try:
        store.remember(42, anketa())
        B.PROFILES.pop(42, None)  # как будто бота перезапустили

        profile = B.restore_profile(42)
        if not profile or profile.get("birth") != dt.date(1990, 3, 15):
            errors.append("анкета не поднялась с диска после перезапуска: %r"
                          % (profile,))
        if profile and "_used" in profile:
            errors.append("служебная пометка _used утекла в рабочую анкету")

        B.PROFILES.pop(42, None)
        if B.restore_profile(99) is not None:
            errors.append("для незнакомого чата подставилась чужая анкета")
    finally:
        B.api_call = saved_api
        B.PROFILES.pop(42, None)


@with_temp_storage
def check_bot_reset_wipes_disk(errors):
    import bot as B

    sent = []
    saved_api = B.api_call
    B.api_call = lambda method, **params: sent.append((method, params)) or {"ok": True}
    try:
        store.remember(42, anketa())
        B.PROFILES[42] = anketa()
        B.handle_message(42, "/reset", None)

        if store.people(42):
            errors.append("/reset не стёр анкеты с диска")
        if 42 in B.PROFILES:
            errors.append("/reset не стёр анкету из памяти")

        # и бот честно сказал об этом
        text = [params for method, params in sent
                if method == "sendMessage"][-1].get("text", "")
        if "диск" not in text.lower():
            errors.append("после /reset бот не сказал, что стёр данные с диска: %r"
                          % text[:120])
    finally:
        B.api_call = saved_api
        B.PROFILES.pop(42, None)


@with_temp_storage
def check_bot_people_command(errors):
    import bot as B

    sent = []
    saved_api = B.api_call
    B.api_call = lambda method, **params: sent.append((method, params)) or {"ok": True}
    try:
        B.PROFILES.pop(42, None)
        B.handle_message(42, "/people", None)
        text = [params for method, params in sent
                if method == "sendMessage"][-1].get("text", "")
        if "ничего не помню" not in text:
            errors.append("на пустой список /people ответил невнятно: %r" % text[:120])

        store.remember(42, anketa("Ирина"))
        store.remember(42, anketa("Пётр", 1988, 11, 2))
        sent.clear()
        B.handle_message(42, "/people", None)
        message = [params for method, params in sent if method == "sendMessage"][-1]
        if "Ирина" not in message.get("text", "") or "Пётр" not in message.get("text", ""):
            errors.append("/people показал не всех: %r" % message.get("text", "")[:160])

        buttons = message["reply_markup"]["inline_keyboard"]
        actions = [button["callback_data"] for row in buttons for button in row]
        if "forget:0" not in actions or "forget:all" not in actions:
            errors.append("в /people нет кнопок удаления: %r" % actions)

        sent.clear()
        B.handle_callback({"id": "1", "data": "forget:0",
                           "message": {"chat": {"id": 42}}}, None)
        if len(store.people(42)) != 1:
            errors.append("кнопка удаления не убрала человека с диска")

        sent.clear()
        B.handle_callback({"id": "2", "data": "forget:all",
                           "message": {"chat": {"id": 42}}}, None)
        if store.people(42):
            errors.append("кнопка «удалить всё» оставила данные на диске")
    finally:
        B.api_call = saved_api
        B.PROFILES.pop(42, None)


@with_temp_storage
def check_promises_match_behaviour(errors):
    """Тексты бота не обещают того, чего он больше не делает."""
    import bot as B

    for name in ("WELCOME_TEXT", "HELP_TEXT", "ABOUT_METHOD"):
        text = getattr(B, name)
        for lie in ("только в памяти", "На диск не записываю",
                    "на диск не пишутся", "только в оперативной памяти"):
            if lie in text:
                errors.append("%s обещает клиенту то, что уже неправда: %r"
                              % (name, lie))
    if "/people" not in B.HELP_TEXT:
        errors.append("в справке нет команды /people")
    if "полгода" not in B.ABOUT_METHOD and "180" not in B.ABOUT_METHOD:
        errors.append("в «О методе» не сказано, сколько хранятся анкеты")


def main():
    errors = []
    for check in (check_roundtrip, check_extra_fields_not_saved,
                  check_partial_anketa, check_multiple_people,
                  check_same_person_updated, check_limit,
                  check_retention, check_touch_extends_life,
                  check_forget, check_forget_person,
                  check_corrupted_file, check_bad_date_ignored,
                  check_atomic_write,
                  check_bot_offers_saved, check_bot_restores_after_restart,
                  check_bot_reset_wipes_disk, check_bot_people_command,
                  check_promises_match_behaviour):
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
