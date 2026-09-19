# -*- coding: utf-8 -*-
"""
Сервер мини-приложения Telegram.

Отдаёт статику из webapp/ и небольшой JSON API поверх того же движка, что
и бот: те же темы, та же натальная карта, тот же расклад. Приложение и
чат делят анкеты клиентов — если человек назвал дату в чате, мини-апп её
уже знает, и наоборот.

<b>Про подпись.</b> Telegram передаёт в приложение initData — строку с
данными пользователя и подписью HMAC-SHA256 на ключе, выведенном из
токена бота. Сервер обязан её проверять: без этого любой желающий
откроет страницу в браузере и будет дёргать API под чужим номером.
Проверяется и свежесть (auth_date), чтобы перехваченную строку нельзя
было использовать сутками.

Зависимостей не добавляем: http.server из стандартной библиотеки для
одного экрана с формой более чем достаточно, а в бою перед ним всё равно
стоит nginx с TLS.
"""
import datetime as dt
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
WEBAPP_DIR = os.path.join(BASE_DIR, "webapp")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "data"))

import natal  # noqa: E402
import periods  # noqa: E402
import readings  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402

log = logging.getLogger("tarot_astro_bot.webapp")

# Сколько часов подпись считается свежей. Сутки — рекомендация Telegram.
AUTH_MAX_AGE = 24 * 3600

MAX_BODY = 16 * 1024  # тело запроса больше килобайта тут не нужно

# Антифлуд приложения: не больше RATE_LIMIT запросов за RATE_WINDOW секунд
# на одного пользователя Telegram. У бота такой предел есть с самого
# начала, а приложение принимало запросы без счёта — при том, что разбор
# считает эфемериды и ищет по базе, то есть стоит заметно дороже
# отправленного сообщения. Ограничение живого человека не задевает: даже
# быстро перебирая темы, он не делает и десяти запросов за десять секунд.
RATE_WINDOW = 10.0
RATE_LIMIT = 20
MAX_TRACKED = 10_000  # чтобы словарь отметок не рос без предела
_recent = {}
_recent_lock = threading.Lock()


def rate_limited(user_id, now=None):
    """True, если этот пользователь превысил предел запросов."""
    if RATE_LIMIT <= 0:
        return False
    now = now if now is not None else time.monotonic()
    with _recent_lock:
        if len(_recent) > MAX_TRACKED:
            # Чистим разом всех, кто давно замолчал: перебор словаря раз в
            # десять тысяч пользователей дешевле, чем хранить его вечно
            for key in [k for k, v in _recent.items()
                        if not v or now - v[-1] > RATE_WINDOW]:
                _recent.pop(key, None)
        stamps = [t for t in _recent.get(user_id, ()) if now - t < RATE_WINDOW]
        stamps.append(now)
        _recent[user_id] = stamps
        return len(stamps) > RATE_LIMIT

# Контекст, который передаёт бот при старте: общие анкеты и поиск по базе
CONTEXT = {"token": None, "profiles": {}, "retriever": None, "dev_mode": False}

TAGS = re.compile(r"<(?!/?(b|i|u|s|code|br)\b)[^>]*>")


def check_signature(init_data, token, max_age=AUTH_MAX_AGE, now=None):
    """Проверяет подпись Telegram. Возвращает данные пользователя или None."""
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except ValueError:
        return None

    received = pairs.pop("hash", None)
    if not received:
        return None

    check_string = "\n".join("%s=%s" % (key, pairs[key]) for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None

    try:
        issued = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age and abs((now or time.time()) - issued) > max_age:
        return None

    try:
        # parse_qsl уже раскодировал проценты — второй unquote испортил бы
        # значения, где встречается сам символ «%»
        user = json.loads(pairs.get("user", "{}"))
    except ValueError:
        return None
    if not user.get("id"):
        return None
    return user


# ---------------------------------------------------------------------------
# Преобразование разбора в блоки для экрана
# ---------------------------------------------------------------------------

def to_blocks(messages):
    """Текст бота -> карточки приложения.

    Бот собирает ответ блоками, разделёнными пустой строкой, и первая
    строка каждого блока — его заголовок в <b>. Здесь мы разбираем это
    обратно, чтобы каждый блок стал отдельной карточкой.
    """
    blocks = []
    for message in messages:
        for chunk in message.split("\n\n"):
            chunk = chunk.strip()
            if not chunk:
                continue
            lines = chunk.split("\n")
            head = lines[0].strip()
            title = None
            match = re.match(r"^<b>(.+?)</b>\s*$", head)
            if match and len(lines) > 1:
                title = match.group(1)
                lines = lines[1:]
            body = "<br>".join(TAGS.sub("", line) for line in lines)
            blocks.append({"title": title, "body": body})

    # В Telegram дисклеймер стоит в конце каждого сообщения — их бывает
    # два-три. В приложении это один экран, и три одинаковые карточки на
    # нём выглядят сбоем, поэтому оставляем последнюю.
    note = TAGS.sub("", readings.DISCLAIMER).strip()
    blocks = [block for block in blocks if block["body"].strip() != note]
    blocks.append({"title": None, "body": note, "note": True})
    return blocks


def hero_block(sphere, profile, today):
    age = readings.age_from(profile["birth"], today)
    meta = "%s · %s" % (profile.get("name") or "без имени",
                        today.strftime("%d.%m.%Y"))
    return {"hero": True, "emoji": sphere["emoji"], "title": sphere["title"],
            "body": "%d %s · %s" % (age, readings.years_word(age), meta)}


# ---------------------------------------------------------------------------
# Обработчик запросов
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "TarotAstroWebApp"
    # http.server по умолчанию отвечает по HTTP/1.0 и закрывает соединение;
    # обратные прокси (Cloudflare, nginx) считают такой ответ поломанным и
    # отдают клиенту 520. Длину тела мы ставим всегда, так что 1.1 безопасен.
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # гасим шумный лог http.server
        log.debug(fmt, *args)

    # -- инфраструктура ----------------------------------------------------

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        payload = body if isinstance(body, bytes) else json.dumps(
            body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        # Приложение живёт в рамке Telegram, и встраивать его куда-то ещё
        # незачем: чужая страница с такой рамкой — это готовый обман, где
        # человек думает, что нажимает у нас. X-Frame-Options тут не
        # годится: он умеет только «нигде» и «на своём же домене», а нам
        # нужен ровно телеграмовский. Значение ALLOWALL, стоявшее здесь
        # раньше, спецификацией не предусмотрено — браузеры его молча
        # пропускают, то есть защиты не было вовсе
        self.send_header("Content-Security-Policy",
                         "frame-ancestors https://telegram.org "
                         "https://*.telegram.org")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self):
        """Тело запроса или None. Слишком большое возвращает «too_large».

        На HTTP/1.1 соединение живёт между запросами, поэтому непрочитанное
        тело нельзя просто бросить: его остаток клиент увидит как ответ на
        следующий запрос, а соединение оборвётся. Поэтому на превышение
        лимита отвечаем и сразу закрываем соединение.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length > MAX_BODY:
            self.close_connection = True
            return "too_large"
        if length <= 0:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _authorise(self, payload):
        """Возвращает id пользователя Telegram или None."""
        if CONTEXT["dev_mode"]:
            return 0  # локальный просмотр без Telegram
        user = check_signature((payload or {}).get("initData"), CONTEXT["token"])
        return user["id"] if user else None

    # -- статика -----------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        if "/" in name or ".." in name:  # плоская раздача, без обхода вверх
            self._send(404, {"error": "not found"})
            return
        full = os.path.join(WEBAPP_DIR, name)
        if not os.path.isfile(full):
            self._send(404, {"error": "not found"})
            return
        kind = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as handle:
            self._send(200, handle.read(), kind + "; charset=utf-8")

    # -- API ---------------------------------------------------------------

    def do_POST(self):
        route = self.path.split("?", 1)[0]
        payload = self._read_json()
        if payload == "too_large":
            self._send(413, {"error": "Слишком большой запрос"})
            return
        if payload is None:
            self._send(400, {"error": "Некорректный запрос"})
            return

        user_id = self._authorise(payload)
        if user_id is None:
            self._send(403, {"error": "Откройте приложение из Telegram"})
            return

        if rate_limited(user_id):
            stats.track("rate_limited", user_id, source="webapp")
            self._send(429, {"error": "Слишком часто. Подождите несколько "
                                      "секунд и повторите."})
            return

        try:
            if route == "/api/spheres":
                self._send(200, self.spheres(user_id))
            elif route == "/api/places":
                self._send(200, self.places(payload))
            elif route == "/api/reading":
                self._send(200, self.reading(user_id, payload))
            else:
                self._send(404, {"error": "Неизвестный запрос"})
        except ValueError as exc:            # понятная пользователю ошибка
            self._send(400, {"error": str(exc)})
        except Exception:
            log.exception("Ошибка мини-приложения на %s", route)
            self._send(500, {"error": "Что-то сломалось на сервере"})

    # -- методы ------------------------------------------------------------

    def spheres(self, user_id):
        items = []
        for key, sphere in readings.SPHERES.items():
            # подпись берём из planet_label, а не из описания дома: у «работы»
            # и «предназначения» дом один и тот же (десятый), и подписи
            # получались бы одинаковыми
            items.append({
                "key": key, "emoji": sphere["emoji"], "title": sphere["title"],
                "hint": readings.lower_first(sphere["planet_label"]),
            })
        profile = CONTEXT["profiles"].get(user_id) or {}
        if not profile.get("birth"):
            # бота могли перезапустить — тогда анкета лежит на диске
            saved = store.people(user_id)
            if saved:
                profile = saved[0]
        known = None
        if profile.get("birth"):
            known = {
                "name": profile.get("name") or "",
                "birth": profile["birth"].strftime("%d.%m.%Y"),
                "time": profile.get("time") or "",
                "city": (profile.get("place") or {}).get("name") or "",
            }
        # дисклеймер приходит с сервера, а не лежит в вёрстке: иначе
        # приложение и бот со временем начнут говорить разное
        note = TAGS.sub("", readings.DISCLAIMER).strip()
        return {"spheres": items, "profile": known, "note": note}

    def places(self, payload):
        query = (payload.get("query") or "")[:60]
        found = natal.find_places(query, limit=4)
        return {"places": [{
            "label": natal.place_label(place), "timezone": place["timezone"],
            "name": place["name"], "country": place["country"],
            "lat": place["lat"], "lon": place["lon"],
        } for place in found]}

    def reading(self, user_id, payload):
        birth, time_from_text = readings.parse_birth(payload.get("birth") or "")
        if not birth:
            raise ValueError("Не разобрал дату рождения. Формат: 15.03.1990")

        clock = (payload.get("time") or "").strip() or time_from_text
        if clock:
            match = re.search(r"\b([01]?\d|2[0-3])[:.\s]([0-5]\d)\b", clock)
            clock = "%02d:%s" % (int(match.group(1)), match.group(2)) if match else None

        place = payload.get("place") or None
        if place:
            place = {"name": place.get("name"), "country": place.get("country"),
                     "lat": float(place.get("lat")), "lon": float(place.get("lon")),
                     "timezone": place.get("timezone")}

        profile = {
            "name": (payload.get("name") or "").strip()[:40],
            "birth": birth,
            "time": clock,
            "place": place,
            "question": (payload.get("question") or "").strip()[:200],
        }
        if place and natal.available():
            try:
                profile["chart"] = natal.build_chart(birth, clock, place)
            except Exception:
                log.exception("Не удалось построить карту в мини-приложении")
        CONTEXT["profiles"][user_id] = profile
        store.remember(user_id, profile)

        today = dt.date.today()
        key = payload.get("sphere")
        started = time.monotonic()

        if payload.get("tool"):
            blocks = self.tool_blocks(key, profile, today)
            event, sphere_key = "webapp_tool", None
        else:
            if key not in readings.SPHERES:
                raise ValueError("Неизвестная тема")
            messages = readings.build_reading(
                key, profile, CONTEXT["retriever"],
                history=store.past_readings(user_id))
            store.remember_reading(user_id, key,
                                   readings.draw_spread(key, profile))
            blocks = [hero_block(readings.SPHERES[key], profile, today)]
            blocks += to_blocks(messages)[1:]  # первый блок — та же шапка
            event, sphere_key = "webapp_reading", key

        stats.track(event, user_id, sphere=sphere_key, command=key,
                    ms=int((time.monotonic() - started) * 1000),
                    has_chart=bool(profile.get("chart")),
                    sign=readings.sun_sign(birth),
                    age_band=stats.age_band(readings.age_from(birth)),
                    country=(place or {}).get("country"))
        return {"blocks": blocks}

    def tool_blocks(self, tool, profile, today):
        chart = profile.get("chart")
        if tool in ("chart", "transits") and not chart:
            raise ValueError("Для этого нужны время и город рождения")

        if tool == "chart":
            return to_blocks([natal.format_chart(chart)])
        if tool == "transits":
            import forecast
            report = forecast.transits(chart, include_fast=True)
            return to_blocks([forecast.format_transits(report, chart)])
        if tool == "week":
            return to_blocks(periods.build_week(profile, CONTEXT["retriever"]))
        if tool == "year":
            return to_blocks(periods.build_year(profile, CONTEXT["retriever"]))
        raise ValueError("Неизвестный раздел")


# ---------------------------------------------------------------------------

class QuietServer(ThreadingHTTPServer):
    """Тот же сервер, но без traceback на каждый оборванный сокет.

    Клиент вправе уйти в любой момент: закрыть вкладку, свернуть
    приложение, потерять сеть. Это не ошибка сервера, и в журнале ей место
    одной строкой, а не десятью.
    """

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, ConnectionAbortedError,
                              BrokenPipeError, TimeoutError)):
            log.info("клиент разорвал соединение: %s", error.__class__.__name__)
            return
        log.exception("ошибка при обработке запроса мини-приложения")


def start(token, profiles, retriever, port=8080, dev_mode=False, host=None):
    """Поднимает сервер в фоновом потоке. Возвращает сам сервер.

    Слушаем петлевой адрес: на сервере перед приложением стоит Caddy или
    nginx, который держит сертификат. Если слушать 0.0.0.0, то же самое
    приложение открывается по http://IP:8080 — без шифрования и мимо всех
    настроек прокси. Открыть наружу можно осознанно, переменной
    WEBAPP_HOST (например, для проверки с телефона в домашней сети).
    """
    CONTEXT.update({"token": token, "profiles": profiles,
                    "retriever": retriever, "dev_mode": bool(dev_mode)})
    host = host or (os.environ.get("WEBAPP_HOST") or "127.0.0.1").strip()
    server = QuietServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="webapp")
    thread.start()
    log.info("Мини-приложение слушает %s:%d%s", host, port,
             " (режим разработки: подпись не проверяется)" if dev_mode else "")
    return server
