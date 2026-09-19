# -*- coding: utf-8 -*-
"""Проверка мини-приложения: подпись Telegram, раздача файлов, API.

Запуск: python scripts/test_webapp.py

Главное здесь — подпись. Мини-апп живёт на публичном адресе, и без
проверки initData любой желающий открыл бы страницу в браузере и дёргал
API под чужим номером: читал бы чужие анкеты и писал в чужую статистику.
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

PROJECT = os.path.join(os.path.dirname(__file__), "..")
os.environ["STATS_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(PROJECT, "bot"))
sys.path.insert(0, os.path.join(PROJECT, "data"))

import webapp  # noqa: E402

# Нарочно не в формате настоящего токена: иначе сканер секретов из
# scripts/test_security.py справедливо примет его за утечку. Для HMAC
# годится любая строка.
TOKEN = "fake-token-for-tests-only"
PORT = 8791
BASE = "http://127.0.0.1:%d" % PORT


def make_init_data(user_id=42, token=TOKEN, auth_date=None, tamper=False):
    """Собирает initData так же, как это делает Telegram.

    Тонкость, на которой легко ошибиться: подпись считается по
    <b>раскодированным</b> значениям, а передаются они в URL-кодировке.
    """
    user = json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False)
    fields = {
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "query_id": "AAH",
        "user": user,
    }
    check = "\n".join("%s=%s" % (key, fields[key]) for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

    if tamper:  # подменяем данные, оставляя старую подпись
        fields["user"] = json.dumps({"id": 999, "first_name": "Чужой"},
                                    ensure_ascii=False)
    return "&".join(["%s=%s" % (key, quote(fields[key], safe=""))
                     for key in sorted(fields)] + ["hash=" + signature])


def check_signature(errors):
    good = make_init_data()
    user = webapp.check_signature(good, TOKEN)
    if not user or user.get("id") != 42:
        errors.append("правильная подпись не принята")

    cases = [
        ("подделанные данные", make_init_data(tamper=True), TOKEN),
        ("чужой токен", make_init_data(token="another-fake-token"), TOKEN),
        ("пустая строка", "", TOKEN),
        ("мусор", "user=x&hash=deadbeef", TOKEN),
        ("без подписи", "auth_date=1&user=%7B%22id%22%3A1%7D", TOKEN),
    ]
    for name, init_data, token in cases:
        if webapp.check_signature(init_data, token) is not None:
            errors.append("принята подпись, которую нельзя принимать: %s" % name)

    # просроченная подпись: перехваченную строку нельзя использовать сутками
    stale = make_init_data(auth_date=time.time() - 48 * 3600)
    if webapp.check_signature(stale, TOKEN) is not None:
        errors.append("принята просроченная подпись")
    if webapp.check_signature(stale, TOKEN, max_age=0) is None:
        errors.append("проверка срока не отключается параметром max_age")


def check_blocks(errors):
    messages = ["💞 <b>Любовь</b>\nСтася · 13.09\n\n"
                "<b>Коротко</b>\nПервая строка\nВторая строка\n\n"
                "<b>Карты</b>\n1. <b>Что сейчас</b> — Маг: воля."]
    blocks = webapp.to_blocks(messages)
    # три блока разбора плюс карточка с дисклеймером
    if len(blocks) != 4:
        errors.append("разбор на карточки дал %d блоков вместо четырёх"
                      % len(blocks))
        return
    notes = [block for block in blocks if block.get("note")]
    if len(notes) != 1:
        errors.append("карточек с дисклеймером %d, ожидалась одна" % len(notes))
    elif blocks[-1] is not notes[0]:
        errors.append("дисклеймер оказался не последней карточкой")
    if blocks[1]["title"] != "Коротко":
        errors.append("заголовок блока потерян: %r" % blocks[1]["title"])
    if "<br>" not in blocks[1]["body"]:
        errors.append("переносы строк не сохранены")
    if "<b>" not in blocks[2]["body"]:
        errors.append("выделение внутри текста потеряно")
    # опасные теги вырезаются, безопасные остаются
    dirty = webapp.to_blocks(['<b>Заголовок</b>\n<script>alert(1)</script>текст'])
    if "<script>" in dirty[0]["body"]:
        errors.append("тег <script> прошёл в карточку")


def check_server(errors):
    server = webapp.start(TOKEN, {}, None, port=PORT, dev_mode=False)
    time.sleep(0.4)
    try:
        # статика отдаётся
        with urllib.request.urlopen(BASE + "/", timeout=5) as response:
            body = response.read().decode("utf-8")
        if "Звёздный потенциал" not in body:
            errors.append("главная страница отдалась без содержимого")
        for name in ("app.css", "app.js"):
            with urllib.request.urlopen(BASE + "/" + name, timeout=5) as response:
                if not response.read():
                    errors.append("%s отдался пустым" % name)

        # выход за пределы папки закрыт
        for path in ("/../.env", "/..%2f.env", "/bot/bot.py"):
            try:
                urllib.request.urlopen(BASE + path, timeout=5)
                errors.append("сервер отдал файл за пределами webapp: %s" % path)
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    errors.append("%s -> %d вместо 404" % (path, exc.code))
            except urllib.error.URLError:
                pass  # путь не дошёл до сервера — тоже годится

        # API без подписи не пускает
        request = urllib.request.Request(
            BASE + "/api/spheres", data=b'{"initData": ""}',
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=5)
            errors.append("API пустил запрос без подписи Telegram")
        except urllib.error.HTTPError as exc:
            if exc.code != 403:
                errors.append("API без подписи ответил %d вместо 403" % exc.code)

        # API с подписью пускает
        payload = json.dumps({"initData": make_init_data()}).encode()
        request = urllib.request.Request(
            BASE + "/api/spheres", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
        if len(data.get("spheres", [])) < 10:
            errors.append("API вернул %d тем" % len(data.get("spheres", [])))
        hints = [sphere["hint"] for sphere in data["spheres"]]
        if len(hints) != len(set(hints)):
            errors.append("подписи тем повторяются — плитки неразличимы")

        # слишком большое тело отбивается
        request = urllib.request.Request(
            BASE + "/api/reading", data=b"x" * (webapp.MAX_BODY + 100),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=5)
            errors.append("сервер принял тело больше лимита")
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 403, 413):
                errors.append("на большое тело ответ %d" % exc.code)
        except OSError:
            # Сервер закрывает соединение, не дочитав тело, и клиент иногда
            # успевает увидеть только обрыв (на Windows — WinError 10053)
            # вместо ответа 413. Для нас оба исхода одинаковы: тело не
            # принято. Тест на этом падать не должен — он проверяет отказ,
            # а не то, какими именно байтами сервер успел о нём сообщить.
            pass
    finally:
        server.shutdown()
        server.server_close()


def check_dev_mode(errors):
    """В режиме разработки подпись не проверяется — это должно быть явно."""
    server = webapp.start(TOKEN, {}, None, port=PORT + 1, dev_mode=True)
    time.sleep(0.4)
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/spheres" % (PORT + 1),
            data=b'{"initData": ""}', headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                errors.append("режим разработки не пустил без подписи")
    except Exception as exc:
        errors.append("режим разработки сломан: %r" % exc)
    finally:
        server.shutdown()
        server.server_close()
        webapp.CONTEXT["dev_mode"] = False


def check_bot_wiring(errors):
    """Кнопка мини-аппа должна собираться в правильный формат Telegram."""
    import bot as B

    markup = B.keyboard([[("Обычная", "act:x")],
                         [("Приложение", None, "https://example.com")]])
    rows = markup["inline_keyboard"]
    if "callback_data" not in rows[0][0]:
        errors.append("обычная кнопка потеряла callback_data")
    if rows[1][0].get("web_app", {}).get("url") != "https://example.com":
        errors.append("кнопка мини-аппа собрана неверно: %s" % rows[1][0])
    if "callback_data" in rows[1][0]:
        errors.append("у кнопки мини-аппа остался callback_data — Telegram откажет")


def main():
    errors = []
    check_signature(errors)
    check_blocks(errors)
    check_server(errors)
    check_dev_mode(errors)
    check_bot_wiring(errors)

    print("Ошибок: %d" % len(errors))
    for error in errors[:30]:
        print(" -", error)
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
