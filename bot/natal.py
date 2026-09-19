# -*- coding: utf-8 -*-
"""
Настоящая натальная карта: эфемериды Swiss Ephemeris + геокодер + таймзоны.

Что считается:
  * долготы десяти планет и Северного узла на момент рождения (UT);
  * знак, градус в знаке и ретроградность каждой планеты;
  * куспиды двенадцати домов, Асцендент и MC (Плацидус, с откатом на
    Порфирия за полярным кругом, где Плацидус не определён);
  * аспекты между планетами с раздельными орбисами и пометкой
    сходящийся/расходящийся.

Три уровня точности — сколько данных дал клиент, столько и считаем:
  * дата + время + город  -> полная карта: планеты, дома, ASC/MC, аспекты;
  * дата + город          -> планеты на полдень по местному времени, без
                             домов и ASC (их без времени не существует);
  * только дата           -> карта не строится, работает солярный режим
                             из readings.py.

Часовой пояс берётся из базы городов и разворачивается через zoneinfo,
то есть с исторически верным смещением: для Москвы 1980 года это UTC+3
с декретным временем, а не сегодняшние +3 по умолчанию.
"""
import datetime as dt
import os
import sys

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(BASE_DIR, "data"))

import astrology_data as ad  # noqa: E402

try:
    import swisseph as swe
except ImportError:  # без эфемерид модуль остаётся импортируемым
    swe = None

try:
    import geonamescache
    _GEO = geonamescache.GeonamesCache()
except ImportError:
    _GEO = None

SIGN_ORDER = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]

# Ключи планет в порядке, в котором их принято перечислять в карте.
PLANET_SEQUENCE = [
    "sun", "moon", "mercury", "venus", "mars",
    "jupiter", "saturn", "uranus", "neptune", "pluto",
]

ASPECT_ANGLES = {
    "conjunction": 0.0,
    "sextile": 60.0,
    "square": 90.0,
    "trine": 120.0,
    "opposition": 180.0,
}

# Орбисы (градусы). Для аспектов с участием Солнца или Луны — шире.
ASPECT_ORBS = {
    "conjunction": 8.0,
    "sextile": 4.0,
    "square": 7.0,
    "trine": 7.0,
    "opposition": 8.0,
}
LUMINARY_BONUS = 2.0
LUMINARIES = {"sun", "moon"}

_CALC_FLAGS = None


class NatalError(Exception):
    """Не хватает данных или библиотек, чтобы построить карту."""


def available():
    """Готов ли модуль считать карты (стоит ли pyswisseph)."""
    return swe is not None


def geocoder_available():
    return _GEO is not None


# ---------------------------------------------------------------------------
# Геокодер: город -> координаты и часовой пояс
# ---------------------------------------------------------------------------

def find_places(query, limit=5):
    """Ищет город по названию (русскому или латинскому). Крупные — выше."""
    if _GEO is None:
        return []
    query = (query or "").strip()
    if len(query) < 2:
        return []
    # «Москва, Россия» -> ищем по первой части
    head = query.split(",")[0].strip()
    found = _GEO.search_cities(head, case_sensitive=False)
    if not found:
        found = _GEO.search_cities(query, case_sensitive=False)

    lowered = head.lower()

    def rank(city):
        names = [city["name"].lower()] + [n.lower() for n in city.get("alternatenames", [])]
        exact = 0 if lowered in names else 1
        return (exact, -int(city.get("population") or 0))

    places = []
    for city in sorted(found, key=rank)[:limit]:
        places.append({
            "name": city["name"],
            "country": city["countrycode"],
            "lat": float(city["latitude"]),
            "lon": float(city["longitude"]),
            "timezone": city["timezone"],
            "population": int(city.get("population") or 0),
        })
    return places


def place_label(place):
    return "%s (%s)" % (place["name"], place["country"])


# ---------------------------------------------------------------------------
# Время: местное -> UT
# ---------------------------------------------------------------------------

def local_to_utc(birth_date, birth_time, timezone_name):
    """Местное время рождения -> (utc_datetime, строка смещения).

    birth_time — "ЧЧ:ММ" или None (тогда берётся полдень по месту).
    Смещение историческое: его даёт база tzdata через zoneinfo.
    """
    if birth_time:
        hour, minute = (int(part) for part in birth_time.split(":"))
    else:
        hour, minute = 12, 0
    naive = dt.datetime(birth_date.year, birth_date.month, birth_date.day, hour, minute)
    if ZoneInfo is None:
        return naive, "UTC+0 (zoneinfo недоступен)"
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        return naive, "UTC+0 (неизвестный часовой пояс %s)" % timezone_name
    # fold=0: при переводе часов назад берём первое из двух одинаковых времён
    aware = naive.replace(tzinfo=zone, fold=0)
    offset = aware.utcoffset() or dt.timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "−"
    hours, minutes = divmod(abs(total_minutes), 60)
    label = "UTC%s%d" % (sign, hours) + (":%02d" % minutes if minutes else "")
    return aware.astimezone(dt.timezone.utc).replace(tzinfo=None), label


def julian_day(utc_datetime):
    if swe is None:
        raise NatalError("pyswisseph не установлен")
    hours = (utc_datetime.hour
             + utc_datetime.minute / 60.0
             + utc_datetime.second / 3600.0)
    return swe.julday(utc_datetime.year, utc_datetime.month, utc_datetime.day,
                      hours, swe.GREG_CAL)


# ---------------------------------------------------------------------------
# Положения планет
# ---------------------------------------------------------------------------

def _flags():
    """Swiss Ephemeris с файлами эфемерид, иначе встроенный Moshier."""
    global _CALC_FLAGS
    if _CALC_FLAGS is None:
        test_jd = swe.julday(2000, 1, 1, 12.0, swe.GREG_CAL)
        for flag in (swe.FLG_SWIEPH | swe.FLG_SPEED, swe.FLG_MOSEPH | swe.FLG_SPEED):
            try:
                swe.calc_ut(test_jd, swe.SUN, flag)
                _CALC_FLAGS = flag
                break
            except Exception:
                continue
        if _CALC_FLAGS is None:
            raise NatalError("Swiss Ephemeris не может посчитать положения планет")
    return _CALC_FLAGS


def sign_of(longitude):
    return SIGN_ORDER[int(longitude % 360.0 // 30)]


def degree_in_sign(longitude):
    return longitude % 30.0


def format_degree(longitude):
    """15°23′ Рыб"""
    deg_float = degree_in_sign(longitude)
    degrees = int(deg_float)
    minutes = int(round((deg_float - degrees) * 60))
    if minutes == 60:
        degrees, minutes = degrees + 1, 0
    sign = ad.SIGNS[sign_of(longitude)]
    return "%d°%02d′ %s" % (degrees, minutes, sign["genitive"])


def compute_positions(jd):
    """{ключ планеты: {lon, speed, sign, retro}} + узел."""
    if swe is None:
        raise NatalError("pyswisseph не установлен")
    flags = _flags()
    codes = {
        "sun": swe.SUN, "moon": swe.MOON, "mercury": swe.MERCURY,
        "venus": swe.VENUS, "mars": swe.MARS, "jupiter": swe.JUPITER,
        "saturn": swe.SATURN, "uranus": swe.URANUS, "neptune": swe.NEPTUNE,
        "pluto": swe.PLUTO, "node": swe.MEAN_NODE,
    }
    positions = {}
    for key, code in codes.items():
        values = swe.calc_ut(jd, code, flags)[0]
        longitude, speed = values[0] % 360.0, values[3]
        positions[key] = {
            "lon": longitude,
            "speed": speed,
            "sign": sign_of(longitude),
            "retro": speed < 0,
        }
    return positions


def moon_sign_is_ambiguous(jd):
    """Меняет ли Луна знак в пределах суток вокруг момента (важно без времени)."""
    try:
        start = compute_positions(jd - 0.5)["moon"]["sign"]
        end = compute_positions(jd + 0.5)["moon"]["sign"]
    except NatalError:
        return False
    return start != end


# ---------------------------------------------------------------------------
# Дома
# ---------------------------------------------------------------------------

def compute_houses(jd, latitude, longitude):
    """(куспиды 1..12, ASC, MC, система). Плацидус, за полярным кругом — Порфирий."""
    if swe is None:
        raise NatalError("pyswisseph не установлен")
    system, system_name = b"P", "Плацидус"
    if abs(latitude) > 66.0:
        system, system_name = b"O", "Порфирий (Плацидус не определён за полярным кругом)"
    cusps, ascmc = swe.houses(jd, latitude, longitude, system)
    cusps = list(cusps)
    if len(cusps) == 13:  # некоторые сборки отдают куспиды с 1-го индекса
        cusps = cusps[1:]
    return cusps[:12], ascmc[0] % 360.0, ascmc[1] % 360.0, system_name


def house_of(longitude, cusps):
    """Номер дома для долготы: дом от своего куспида до следующего."""
    longitude %= 360.0
    for index in range(12):
        start = cusps[index] % 360.0
        end = cusps[(index + 1) % 12] % 360.0
        span = (end - start) % 360.0
        offset = (longitude - start) % 360.0
        if offset < span:
            return index + 1
    return 12


# ---------------------------------------------------------------------------
# Аспекты
# ---------------------------------------------------------------------------

def compute_aspects(positions, planets=None):
    """Аспекты между планетами: [{p1, p2, aspect, orb, applying}]."""
    keys = [k for k in (planets or PLANET_SEQUENCE) if k in positions]
    found = []
    for i, first in enumerate(keys):
        for second in keys[i + 1:]:
            a, b = positions[first], positions[second]
            separation = abs(a["lon"] - b["lon"]) % 360.0
            if separation > 180.0:
                separation = 360.0 - separation
            for aspect, angle in ASPECT_ANGLES.items():
                orb_limit = ASPECT_ORBS[aspect]
                if first in LUMINARIES or second in LUMINARIES:
                    orb_limit += LUMINARY_BONUS
                orb = abs(separation - angle)
                if orb <= orb_limit:
                    # сходящийся аспект: орб уменьшается со временем
                    step = 0.02  # ~30 минут
                    future_a = a["lon"] + a["speed"] * step
                    future_b = b["lon"] + b["speed"] * step
                    future = abs(future_a - future_b) % 360.0
                    if future > 180.0:
                        future = 360.0 - future
                    applying = abs(future - angle) < orb
                    found.append({
                        "p1": first, "p2": second, "aspect": aspect,
                        "orb": orb, "applying": applying,
                    })
                    break
    found.sort(key=lambda item: item["orb"])
    return found


# ---------------------------------------------------------------------------
# Сборка карты
# ---------------------------------------------------------------------------

def build_chart(birth_date, birth_time, place):
    """Натальная карта. place — из find_places(); birth_time может быть None."""
    if swe is None:
        raise NatalError("pyswisseph не установлен")
    if not place:
        raise NatalError("не задано место рождения")

    utc, offset_label = local_to_utc(birth_date, birth_time, place["timezone"])
    jd = julian_day(utc)
    positions = compute_positions(jd)

    chart = {
        "date": birth_date,
        "time": birth_time,
        "place": place,
        "utc": utc,
        "offset": offset_label,
        "jd": jd,
        "positions": positions,
        "aspects": compute_aspects(positions),
        "has_houses": False,
        "moon_ambiguous": False,
    }

    if birth_time:
        try:
            cusps, asc, mc, system = compute_houses(jd, place["lat"], place["lon"])
        except Exception:
            cusps = asc = mc = system = None
        if cusps:
            chart.update({"cusps": cusps, "asc": asc, "mc": mc,
                          "house_system": system, "has_houses": True})
            for data in positions.values():
                data["house"] = house_of(data["lon"], cusps)
    else:
        chart["moon_ambiguous"] = moon_sign_is_ambiguous(jd)

    return chart


def house_sign(chart, house_number):
    """Знак на куспиде дома (для домов нужна карта со временем)."""
    if not chart.get("has_houses"):
        return None
    return sign_of(chart["cusps"][house_number - 1])


def planet_house(chart, planet_key):
    return chart["positions"].get(planet_key, {}).get("house")


def separation(lon_a, lon_b):
    """Угловое расстояние между двумя долготами, 0..180°."""
    delta = abs(lon_a - lon_b) % 360.0
    return 360.0 - delta if delta > 180.0 else delta


def cross_aspects(moving, fixed, orbs=None, bonus=None,
                  moving_keys=None, fixed_keys=None, static_fixed=True):
    """Аспекты между двумя наборами позиций.

    Для транзитов, прогрессий и синастрии: moving — то, что движется
    (транзитные или прогрессивные планеты, планеты партнёра), fixed —
    натальная карта. В отличие от натальных аспектов, планета обязательно
    сравнивается и сама с собой: транзитный Сатурн в соединении с
    натальным Сатурном — это возвращение Сатурна, ключевое событие.

    static_fixed=True означает, что второй набор неподвижен (натальная
    карта), и сходящийся/расходящийся считается по скорости первого.
    """
    orbs = orbs or ASPECT_ORBS
    bonus = LUMINARY_BONUS if bonus is None else bonus
    moving_keys = [k for k in (moving_keys or PLANET_SEQUENCE) if k in moving]
    fixed_keys = [k for k in (fixed_keys or PLANET_SEQUENCE) if k in fixed]

    found = []
    for m_key in moving_keys:
        m = moving[m_key]
        for f_key in fixed_keys:
            f = fixed[f_key]
            gap = separation(m["lon"], f["lon"])
            for aspect, angle in ASPECT_ANGLES.items():
                limit = orbs[aspect]
                if m_key in LUMINARIES or f_key in LUMINARIES:
                    limit += bonus
                orb = abs(gap - angle)
                if orb > limit:
                    continue
                step = 0.02
                future_m = m["lon"] + m.get("speed", 0.0) * step
                future_f = f["lon"] + (0.0 if static_fixed else f.get("speed", 0.0)) * step
                applying = abs(separation(future_m, future_f) - angle) < orb
                found.append({
                    "moving": m_key, "fixed": f_key, "aspect": aspect,
                    "orb": orb, "applying": applying,
                    "exact_return": m_key == f_key and aspect == "conjunction",
                })
                break
    return found


def aspects_of(chart, planet_key, limit=None):
    found = [a for a in chart["aspects"] if planet_key in (a["p1"], a["p2"])]
    return found[:limit] if limit else found


def aspect_doc_ids(aspect):
    """Оба возможных id записи в базе знаний для аспекта."""
    return (
        "combo_aspect_%s_%s_%s" % (aspect["p1"], aspect["aspect"], aspect["p2"]),
        "combo_aspect_%s_%s_%s" % (aspect["p2"], aspect["aspect"], aspect["p1"]),
    )


def describe_aspect(aspect):
    first = ad.PLANETS[aspect["p1"]]
    second = ad.PLANETS[aspect["p2"]]
    name = ad.ASPECTS[aspect["aspect"]]
    return "%s %s %s %s (орб %.1f°, %s)" % (
        first["symbol"], first["name_ru"], name["name_ru"].lower(),
        second["name_ru"], aspect["orb"],
        "сходящийся" if aspect["applying"] else "расходящийся",
    )


# ---------------------------------------------------------------------------
# Вывод карты в Telegram
# ---------------------------------------------------------------------------

def format_chart(chart, max_aspects=8):
    place = chart["place"]
    lines = [
        "🪐 <b>НАТАЛЬНАЯ КАРТА</b>",
        "%s%s · %s" % (
            chart["date"].strftime("%d.%m.%Y"),
            (" " + chart["time"]) if chart["time"] else " (время неизвестно)",
            place_label(place),
        ),
        "%.2f°, %.2f° · %s · %s" % (
            place["lat"], place["lon"], place["timezone"], chart["offset"],
        ),
        "",
    ]

    if chart["has_houses"]:
        lines += [
            "<b>ASC %s</b> · <b>MC %s</b> · дома: %s" % (
                format_degree(chart["asc"]), format_degree(chart["mc"]),
                chart["house_system"],
            ),
            "",
        ]
    else:
        lines += [
            "<i>Без времени рождения дома, Асцендент и MC не считаются — "
            "они меняются каждые четыре минуты. Планеты даны на полдень "
            "по месту рождения.</i>",
            "",
        ]

    lines.append("<b>Планеты</b>")
    for key in PLANET_SEQUENCE:
        data = chart["positions"][key]
        planet = ad.PLANETS[key]
        row = "%s %s — %s" % (planet["symbol"], planet["name_ru"], format_degree(data["lon"]))
        if data.get("house"):
            row += ", %d дом" % data["house"]
        if data["retro"]:
            row += " ℞"
        lines.append(row)
    node = chart["positions"].get("node")
    if node:
        row = "☊ Северный узел — %s" % format_degree(node["lon"])
        if node.get("house"):
            row += ", %d дом" % node["house"]
        lines.append(row)

    if chart["moon_ambiguous"]:
        lines.append(
            "<i>⚠️ В этот день Луна меняет знак — без времени рождения её "
            "положение определить нельзя. Уточните время.</i>"
        )

    aspects = chart["aspects"][:max_aspects]
    if aspects:
        lines += ["", "<b>Главные аспекты</b>"]
        lines += [describe_aspect(aspect) for aspect in aspects]
    return "\n".join(lines)
