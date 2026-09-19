# -*- coding: utf-8 -*-
"""Проверки безопасности: секреты, ввод пользователя, деплой.

Запуск: python scripts/test_security.py

Каждый тест закрепляет конкретное исправление, чтобы оно не отвалилось при
будущих правках: токен не должен попадать в логи, длинный ввод — доходить
до тяжёлых функций, разметка пользователя — в HTML-сообщения, а сервис —
запускаться от root.
"""
import io
import logging
import os
import re
import shutil
import sys
import tempfile

PROJECT = os.path.join(os.path.dirname(__file__), "..")
# события тестов не должны попадать в настоящую статистику
os.environ["STATS_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(PROJECT, "bot"))
sys.path.insert(0, os.path.join(PROJECT, "data"))

# Паттерн токена Telegram: 8-10 цифр, двоеточие, 35 символов
TOKEN_PATTERN = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}")


def read(*parts):
    path = os.path.join(PROJECT, *parts)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def check_secrets_on_disk(errors):
    gitignore = read(".gitignore")
    if gitignore is None:
        errors.append("нет .gitignore — .env уедет в репозиторий первым же коммитом")
    else:
        for entry in (".env", "venv/", "__pycache__/"):
            if entry not in gitignore:
                errors.append(".gitignore не закрывает %s" % entry)
        if ".env.example" not in gitignore:
            errors.append(".gitignore не пропускает .env.example как образец")

    example = read(".env.example")
    if example is None:
        errors.append("нет .env.example — непонятно, что вписывать при установке")
    elif TOKEN_PATTERN.search(example):
        errors.append("в .env.example лежит похожий на настоящий токен")

    # токен не должен быть зашит нигде в исходниках и документации
    for folder in ("bot", "scripts", "data", "deploy"):
        directory = os.path.join(PROJECT, folder)
        for name in sorted(os.listdir(directory)):
            if not name.endswith((".py", ".sh", ".md", ".service")):
                continue
            content = read(folder, name) or ""
            if TOKEN_PATTERN.search(content):
                errors.append("похоже на зашитый токен в %s/%s" % (folder, name))
    readme = read("README.md") or ""
    if TOKEN_PATTERN.search(readme):
        errors.append("похоже на зашитый токен в README.md")


def check_token_never_logged(errors):
    import bot as B

    if not B.TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN не загружен — проверка логов бессмысленна")
        return

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(B.TokenScrubbingFormatter("%(levelname)s %(message)s"))
    root = logging.getLogger()
    saved = root.handlers
    root.handlers = [handler]
    try:
        # 1. токен прямо в сообщении
        B.log.warning("url: %s", B.API_URL + "getUpdates")
        # 2. токен внутри исключения (так он и утекал: requests кладёт в
        #    текст ошибки полный URL)
        try:
            raise RuntimeError("Max retries exceeded with url: /bot%s/getUpdates" % B.TOKEN)
        except RuntimeError as exc:
            B.log.warning("API call failed: %s", exc)
        # 3. токен в трассировке
        try:
            raise ValueError("боль в /bot%s/sendMessage" % B.TOKEN)
        except ValueError:
            B.log.exception("Ошибка в основном цикле")
    finally:
        root.handlers = saved

    output = buffer.getvalue()
    if B.TOKEN in output:
        errors.append("токен попал в лог")
    if output.count("<TOKEN>") < 3:
        errors.append("подменено %d вхождений токена вместо трёх" % output.count("<TOKEN>"))


def check_input_limits(errors):
    import bot as B
    import natal

    if B.MAX_INPUT > 500:
        errors.append("MAX_INPUT = %d — слишком много для однопоточного бота" % B.MAX_INPUT)

    seen = {}
    original_places, original_search = natal.find_places, None

    class Retriever(object):
        def search(self, text, top_k=3):
            seen["search"] = len(text)
            return {"mode": "semantic", "results": []}

    def spy_places(query, limit=5):
        seen["places"] = len(query)
        return []

    natal.find_places = spy_places
    sent = []
    saved_api = B.api_call
    B.api_call = lambda method, **params: sent.append((method, params)) or {"ok": True}
    saved_rate = B.RATE_LIMIT
    B.RATE_LIMIT = 0  # антифлуд проверяем отдельно
    try:
        chat = 90001
        long_text = "я" * 4000

        # свободный вопрос уходит в семантический поиск
        B.DIALOGS.pop(chat, None)
        B.PROFILES.pop(chat, None)
        B.handle_message(chat, long_text, Retriever())
        if seen.get("search", 0) > B.MAX_INPUT:
            errors.append("в поиск ушло %d символов при лимите %d"
                          % (seen["search"], B.MAX_INPUT))

        # шаг «город» уходит в геокодер
        B.DIALOGS[chat] = {"sphere": "love", "step": "city", "data": {}}
        B.PROFILES[chat] = {"name": "", "birth": None, "time": None}
        B.handle_message(chat, long_text, Retriever())
        if seen.get("places", 0) > B.MAX_INPUT:
            errors.append("в геокодер ушло %d символов при лимите %d"
                          % (seen["places"], B.MAX_INPUT))

        # имя и вопрос обрезаются при сохранении
        B.DIALOGS[chat] = {"sphere": "love", "step": "name", "data": {}}
        B.handle_message(chat, long_text, Retriever())
        if len(B.DIALOGS[chat]["data"].get("name", "")) > 40:
            errors.append("имя сохранено длиннее 40 символов")
    finally:
        natal.find_places = original_places
        B.api_call = saved_api
        B.RATE_LIMIT = saved_rate
        B.DIALOGS.pop(90001, None)
        B.PROFILES.pop(90001, None)


def check_rate_limit(errors):
    import bot as B

    sent = []
    saved_api = B.api_call
    B.api_call = lambda method, **params: sent.append((method, params)) or {"ok": True}
    chat = 90002
    B.RECENT.pop(chat, None)
    B.WARNED.pop(chat, None)
    B.DIALOGS.pop(chat, None)
    B.PROFILES.pop(chat, None)

    class Retriever(object):
        def search(self, text, top_k=3):
            return {"mode": "semantic", "results": []}

    try:
        blocked = 0
        for _ in range(B.RATE_LIMIT + 10):
            before = len(sent)
            B.handle_message(chat, "Марс в Овне", Retriever())
            if len(sent) == before:
                blocked += 1
        if blocked < 5:
            errors.append("антифлуд пропустил почти всё: заблокировано %d из %d"
                          % (blocked, B.RATE_LIMIT + 10))
        warnings = [params for method, params in sent
                    if method == "sendMessage" and "Слишком много" in params.get("text", "")]
        if len(warnings) != 1:
            errors.append("предупреждений о флуде %d, ожидалось одно" % len(warnings))
        # обычный пользователь с несколькими сообщениями не должен страдать
        B.RECENT.pop(chat, None)
        B.WARNED.pop(chat, None)
        before = len(sent)
        for _ in range(5):
            B.handle_message(chat, "Марс в Овне", Retriever())
        if len(sent) - before < 5:
            errors.append("антифлуд сработал на пяти сообщениях подряд")
    finally:
        B.api_call = saved_api
        for store in (B.RECENT, B.WARNED, B.DIALOGS, B.PROFILES):
            store.pop(chat, None)


def check_memory_bounds(errors):
    import bot as B

    for name in ("DIALOGS", "PROFILES", "PARTNERS", "RECENT", "WARNED"):
        store = getattr(B, name)
        if not isinstance(store, B.BoundedDict):
            errors.append("%s не ограничен по размеру" % name)

    bounded = B.BoundedDict(3)
    for index in range(10):
        bounded[index] = index
    if len(bounded) != 3:
        errors.append("BoundedDict вырос до %d при лимите 3" % len(bounded))
    if list(bounded) != [7, 8, 9]:
        errors.append("BoundedDict вытеснил не самых давних: %s" % list(bounded))
    bounded[7] = "снова"
    bounded[100] = "новый"
    if 7 not in bounded:
        errors.append("BoundedDict вытеснил запись, к которой только что обращались")


def check_html_injection(errors):
    import datetime as dt
    import natal
    import periods
    import readings

    evil = '<b>жирный</b><a href="http://evil">клик</a><script>alert(1)</script>'
    profile = {"name": evil, "birth": dt.date(1990, 3, 15), "time": None,
               "question": evil}
    if natal.available():
        places = natal.find_places("Москва")
        if places:
            profile["place"] = places[0]
            profile["chart"] = natal.build_chart(profile["birth"], None, places[0])

    outputs = {
        "разбор": "\n".join(readings.build_reading("love", profile)),
        "неделя": "\n".join(periods.build_week(profile)),
        "год": "\n".join(periods.build_year(profile)),
    }
    for where, text in outputs.items():
        for raw in ("<b>жирный</b>", "<a href=", "<script>"):
            if raw in text:
                errors.append("%s: разметка пользователя прошла сырой (%s)" % (where, raw))
        if "&lt;b&gt;" not in text:
            errors.append("%s: имя клиента не экранировано" % where)


def check_message_truncation(errors):
    """Обрезка не должна ломать разметку: иначе Telegram вернёт 400."""
    import bot as B

    tags = re.compile(r"<(/?)([a-z]+)[^>]*>")

    def balanced(text):
        stack = []
        for match in tags.finditer(text):
            closing, tag = match.group(1), match.group(2)
            if closing:
                if not stack or stack.pop() != tag:
                    return False
            else:
                stack.append(tag)
        return not stack

    # длинный текст из блоков с разметкой — режем на всех границах подряд
    block = "<b>Заголовок</b>\nСтрока с <i>наклоном</i> и <code>кодом</code>.\n"
    long_text = block * 200
    for limit in (50, 137, 500, 1234, 4000):
        cut = B.cut_html(long_text, limit)
        if not balanced(cut):
            errors.append("обрезка до %d оставила незакрытые теги" % limit)
        if "<" in cut and ">" not in cut[cut.rfind("<"):]:
            errors.append("обрезка до %d разрезала тег пополам" % limit)
        # допускаем небольшой перебор за счёт дозакрытых тегов
        if len(cut) > limit + 40:
            errors.append("обрезка до %d вернула %d символов" % (limit, len(cut)))

    short = "<b>Коротко</b>"
    if B.cut_html(short, 4000) != short:
        errors.append("короткий текст изменён обрезкой")


def check_send_fallback(errors):
    """Если Telegram отверг разметку, текст всё равно должен дойти."""
    import bot as B

    calls = []

    def fake_api(method, **params):
        calls.append(params)
        if "parse_mode" in params:
            return {"ok": False, "error_code": 400,
                    "description": "Bad Request: can't parse entities"}
        return {"ok": True}

    saved = B.api_call
    B.api_call = fake_api
    try:
        B.send_message(777, "<b>текст с <плохой разметкой</b>")
    finally:
        B.api_call = saved

    if len(calls) != 2:
        errors.append("после отказа Telegram повтора не было (%d вызовов)" % len(calls))
        return
    if "parse_mode" in calls[1]:
        errors.append("повтор ушёл снова с разметкой")
    if "<b>" in calls[1]["text"]:
        errors.append("в повторе остались HTML-теги")
    if "текст" not in calls[1]["text"]:
        errors.append("в повторе потерялся текст сообщения")


def check_no_retry_on_client_error(errors):
    """Ошибку 4xx повторять бессмысленно — это задержит остальные чаты."""
    import bot as B
    import requests

    attempts = []

    class Response(object):
        status_code = 400

        def json(self):
            return {"ok": False, "description": "Bad Request: chat not found"}

    def fake_post(url, **kwargs):
        attempts.append(url)
        return Response()

    saved_post, saved_sleep = requests.post, B.time.sleep
    requests.post = fake_post
    B.time.sleep = lambda *a, **k: None
    try:
        result = B.api_call("sendMessage", chat_id=1, text="x")
    finally:
        requests.post = saved_post
        B.time.sleep = saved_sleep

    if len(attempts) != 1:
        errors.append("на ошибку 400 сделано %d попыток вместо одной" % len(attempts))
    if not result or result.get("ok") is not False:
        errors.append("api_call не вернул тело ошибки при 400")


def check_privacy_notice(errors):
    import bot as B

    for name, text in (("/start", B.WELCOME_TEXT), ("/about", B.ABOUT_METHOD),
                       ("/help", B.HELP_TEXT)):
        lowered = text.lower()
        if "/reset" not in lowered:
            errors.append("в тексте %s не сказано про удаление данных (/reset)" % name)
    # Анкеты теперь хранятся, поэтому проверяем обратное: что бот об этом
    # честно предупреждает и объясняет, как посмотреть и удалить сохранённое.
    welcome = B.WELCOME_TEXT.lower()
    if "запомина" not in welcome and "помню" not in welcome:
        errors.append("в /start не сказано, что бот запоминает анкету")
    if "/people" not in B.WELCOME_TEXT:
        errors.append("в /start не сказано, где посмотреть сохранённое (/people)")

    about = B.ABOUT_METHOD.lower()
    if "сохраняю" not in about:
        errors.append("в /about не сказано, что данные сохраняются")
    if "полгода" not in about and "180" not in about:
        errors.append("в /about не сказан срок хранения анкет")
    if "/people" not in B.ABOUT_METHOD:
        errors.append("в /about не сказано про просмотр и удаление анкет")
    for lie in ("только в памяти", "на диск не пишутся", "на диск не записываю",
                "только в оперативной памяти"):
        for name, text in (("/start", B.WELCOME_TEXT), ("/about", B.ABOUT_METHOD),
                           ("/help", B.HELP_TEXT)):
            if lie in text.lower():
                errors.append("в тексте %s осталось неверное обещание: %r"
                              % (name, lie))

    # и то, что действительно не хранится, — не хранится
    if "вопрос" not in about:
        errors.append("в /about не сказано, что вопросы клиента не сохраняются")


def check_dependency_lock(errors):
    lock = read("requirements.lock.txt")
    if lock is None:
        errors.append("нет requirements.lock.txt — версии зависимостей не зафиксированы")
        return
    if "--hash=sha256:" not in lock:
        errors.append("в lock-файле нет хешей пакетов")
    for package in ("chromadb", "sentence-transformers", "pyswisseph", "requests"):
        if not re.search(r"(?mi)^%s==" % re.escape(package), lock):
            errors.append("в lock-файле не закреплён %s" % package)
    if re.search(r"(?m)^\w[\w.-]*>=", lock):
        errors.append("в lock-файле остались нежёсткие версии (>=)")

    deploy = read("deploy", "DEPLOY.md") or ""
    if "--require-hashes" not in deploy:
        errors.append("в DEPLOY.md не описана установка из lock-файла")

    # Инструкция может сколько угодно рекомендовать хеши — важно, что
    # делает сам установщик. Он ставил из requirements.txt, где версии
    # заданы как «не ниже», то есть на новом сервере собирался другой
    # набор библиотек, чем тот, на котором всё проверено
    setup = read("deploy", "setup.sh") or ""
    if "--require-hashes" not in setup:
        errors.append("setup.sh ставит зависимости без проверки хешей")
    if re.search(r"(?m)^\s*\./venv/bin/pip install -r requirements\.txt",
                 setup):
        errors.append("setup.sh ставит из requirements.txt мимо lock-файла")
    if "MaxRetentionSec" not in deploy:
        errors.append("в DEPLOY.md нет настройки срока хранения журнала")


def check_webapp_binds_loopback(errors):
    """Мини-апп по умолчанию слушает петлю, а не весь мир.

    На сервере перед ним стоит Caddy с сертификатом. Если приложение
    слушает 0.0.0.0, то же самое открывается по http://IP:8080 — без
    шифрования и мимо всех настроек прокси, вместе с проверкой подписи
    Telegram, которая на http теряет смысл.
    """
    import webapp

    bound = []

    class FakeServer(object):
        def __init__(self, address, handler):
            bound.append(address)

        def serve_forever(self):
            pass

    saved = webapp.QuietServer
    saved_host = os.environ.pop("WEBAPP_HOST", None)
    webapp.QuietServer = FakeServer
    try:
        webapp.start("123:token", {}, None, port=8080, dev_mode=False)
        if not bound:
            errors.append("мини-апп не поднял сервер")
        elif bound[0][0] not in ("127.0.0.1", "localhost", "::1"):
            errors.append("мини-апп слушает %r — это открытый порт мимо HTTPS"
                          % (bound[0][0],))

        # открыть наружу можно, но только осознанно
        bound.clear()
        webapp.start("123:token", {}, None, port=8080, host="0.0.0.0")
        if not bound or bound[0][0] != "0.0.0.0":
            errors.append("явно заданный адрес не применился: %r" % (bound,))
    finally:
        webapp.QuietServer = saved
        if saved_host is not None:
            os.environ["WEBAPP_HOST"] = saved_host


def check_deployment(errors):
    service = read("deploy", "tarot-astro-bot.service") or ""
    if "User=root" in service:
        errors.append("systemd-юнит запускает бота от root")
    if not re.search(r"^User=\w+", service, re.M):
        errors.append("в юните не задан пользователь")
    for directive in ("NoNewPrivileges=true", "ProtectSystem=strict",
                      "ProtectHome=true", "MemoryMax="):
        if directive not in service:
            errors.append("в юните нет %s" % directive)

    setup = read("deploy", "setup.sh") or ""
    if "useradd" not in setup:
        errors.append("setup.sh не заводит непривилегированного пользователя")
    if "umask 177" not in setup and "chmod 600 .env" not in setup and "chmod 640 .env" not in setup:
        errors.append("setup.sh не ограничивает права на .env")
    if "chown" not in setup:
        errors.append("setup.sh не передаёт файлы пользователю бота")

    # Мини-апп ходит в интернет только через прокси с сертификатом —
    # инструкция обязана это объяснять, иначе порт откроют напрямую
    guide = read("deploy", "DEPLOY.md") or ""
    if "Caddy" not in guide and "nginx" not in guide:
        errors.append("в DEPLOY.md нет обратного прокси для мини-приложения")
    if "WEBAPP_URL" not in guide:
        errors.append("в DEPLOY.md не сказано, как задать адрес мини-приложения")


def check_ledger_survives_parallel_readings(errors):
    """Одновременные разборы не должны теряться в учёте.

    Мини-приложение отвечает в нескольких потоках. Пока учёт шёл без
    замка, тридцать одновременных разборов записывались как четыре:
    потоки читали одинаковое «израсходовано» и затирали друг друга. При
    включённой оплате это значит, что двадцать шесть платных разборов
    ушли даром.
    """
    import tempfile, threading
    import billing

    work = tempfile.mkdtemp()
    saved = (billing.STORE_DIR, billing.LEDGER_PATH,
             billing.ENABLED, billing.FREE_READINGS)
    billing.STORE_DIR = work
    billing.LEDGER_PATH = os.path.join(work, "usage.json")
    billing.ENABLED = True
    billing.FREE_READINGS = 1
    try:
        billing.grant(42, 50)
        count = 30
        start = threading.Barrier(count)

        def one():
            start.wait()
            if billing.can_read(42):
                billing.charge(42)

        threads = [threading.Thread(target=one) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        used = billing.state(42)["used"]
        if used != count:
            errors.append("учёт потерял %d списаний из %d при одновременных "
                          "запросах" % (count - used, count))
    finally:
        (billing.STORE_DIR, billing.LEDGER_PATH,
         billing.ENABLED, billing.FREE_READINGS) = saved
        shutil.rmtree(work, ignore_errors=True)


def check_unsaved_charge_stops_reading(errors):
    """Если расход не записался, разбор не выдаётся.

    Раньше неудачная запись проглатывалась молча: человек получал разбор,
    счётчик не двигался, и при включённой оплате бот раздавал платную
    работу бесплатно, пока кто-нибудь не заметит.
    """
    import tempfile
    import billing

    work = tempfile.mkdtemp()
    saved = (billing.STORE_DIR, billing.LEDGER_PATH)
    billing.STORE_DIR = os.path.join(work, "нет-такой-папки", "и-такой")
    billing.LEDGER_PATH = os.path.join(billing.STORE_DIR, "usage.json")
    try:
        # Папку создать можно, а вот файл на её месте — нет
        os.makedirs(os.path.dirname(billing.STORE_DIR), exist_ok=True)
        io.open(billing.STORE_DIR, "w", encoding="utf-8").write("занято")
        if billing.charge(1) is not None:
            errors.append("charge промолчал о том, что расход не сохранён")
    finally:
        (billing.STORE_DIR, billing.LEDGER_PATH) = saved
        shutil.rmtree(work, ignore_errors=True)

    source = read("bot", "bot.py") or ""
    if "kind is None" not in source:
        errors.append("бот не проверяет, записался ли расход перед выдачей")


def check_broken_ledger_is_kept(errors):
    """Испорченный файл учёта откладывается, а не затирается.

    Счётчики начинаются заново — иначе бот перестанет отвечать. Но если
    испорченный файл просто перезаписать, вместе с ним исчезнут все
    оплаченные разборы, и вернуть их будет неоткуда.
    """
    import tempfile, glob
    import billing

    work = tempfile.mkdtemp()
    saved = (billing.STORE_DIR, billing.LEDGER_PATH)
    billing.STORE_DIR = work
    billing.LEDGER_PATH = os.path.join(work, "usage.json")
    try:
        io.open(billing.LEDGER_PATH, "w", encoding="utf-8").write("{ не json")
        billing.load_all()
        kept = glob.glob(os.path.join(work, "usage.json.corrupt-*"))
        if not kept:
            errors.append("испорченный файл учёта пропал вместе с оплатами")
    finally:
        (billing.STORE_DIR, billing.LEDGER_PATH) = saved
        shutil.rmtree(work, ignore_errors=True)


def check_webapp_rate_limit(errors):
    """У приложения есть свой предел запросов.

    Разбор в приложении стоит дороже сообщения боту: считаются эфемериды
    и идёт поиск по базе. У бота предел был с самого начала, у
    приложения — не было вовсе.
    """
    import webapp

    if not getattr(webapp, "RATE_LIMIT", 0):
        errors.append("у мини-приложения нет предела частоты запросов")
        return

    webapp._recent.clear()
    blocked = sum(1 for _ in range(webapp.RATE_LIMIT * 3)
                  if webapp.rate_limited(777))
    if blocked == 0:
        errors.append("предел частоты в приложении не срабатывает")
    webapp._recent.clear()

    # Живого человека предел задевать не должен
    now = 1000.0
    calm = [webapp.rate_limited(778, now=now + step * 2.0)
            for step in range(10)]
    if any(calm):
        errors.append("предел частоты срабатывает на обычном темпе")
    webapp._recent.clear()


def check_frame_ancestors(errors):
    """Рамку приложению разрешает только Telegram.

    X-Frame-Options со значением ALLOWALL, стоявший здесь раньше,
    спецификацией не предусмотрен: браузеры его игнорируют, то есть
    страницу можно было положить в рамку на любом сайте и собирать
    нажатия. Обратное тоже плохо: SAMEORIGIN закрыл бы приложение в
    веб-версии Telegram, которая открывает его с другого домена.
    """
    source = read("bot", "webapp.py") or ""
    # Ищем именно установку заголовка: слово ALLOWALL встречается ещё и в
    # пояснении, почему от него отказались
    if re.search(r"send_header\(\s*[\"']X-Frame-Options", source):
        errors.append("приложение снова ставит X-Frame-Options — он умеет "
                      "только «нигде» и «на своём домене»")
    if "frame-ancestors" not in source:
        errors.append("приложение не ограничивает, кто может взять его в рамку")
    elif "telegram.org" not in source:
        errors.append("в frame-ancestors не указан Telegram")

    proxy = read("deploy", "Caddyfile") or ""
    if "X-Frame-Options" in proxy and "SAMEORIGIN" in proxy:
        errors.append("Caddyfile ставит SAMEORIGIN — приложение не откроется "
                      "в веб-версии Telegram")
    if "Strict-Transport-Security" not in proxy:
        errors.append("в Caddyfile нет HSTS")


def main():
    errors = []
    check_secrets_on_disk(errors)
    check_token_never_logged(errors)
    check_input_limits(errors)
    check_rate_limit(errors)
    check_memory_bounds(errors)
    check_html_injection(errors)
    check_message_truncation(errors)
    check_send_fallback(errors)
    check_no_retry_on_client_error(errors)
    check_privacy_notice(errors)
    check_dependency_lock(errors)
    check_webapp_binds_loopback(errors)
    check_deployment(errors)
    check_ledger_survives_parallel_readings(errors)
    check_unsaved_charge_stops_reading(errors)
    check_broken_ledger_is_kept(errors)
    check_webapp_rate_limit(errors)
    check_frame_ancestors(errors)

    print("Ошибок: %d" % len(errors))
    for error in errors[:40]:
        print(" -", error)
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
