# -*- coding: utf-8 -*-
"""Проверка прогностики: транзиты, прогрессии, синастрия.

Запуск: python scripts/test_forecast.py
"""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import forecast  # noqa: E402
import natal  # noqa: E402

TELEGRAM_LIMIT = 4000
ALLOWED_TAGS = {"b", "i", "code", "u", "s", "a", "pre"}


def check_html(text, where, errors):
    stack = []
    for match in re.finditer(r"<(/?)([a-z]+)[^>]*>", text):
        closing, tag = match.group(1), match.group(2)
        if tag not in ALLOWED_TAGS:
            errors.append("%s: недопустимый тег <%s>" % (where, tag))
            continue
        if closing:
            if not stack or stack.pop() != tag:
                errors.append("%s: непарный тег </%s>" % (where, tag))
        else:
            stack.append(tag)
    if stack:
        errors.append("%s: незакрытые теги %s" % (where, stack))
    if len(text) > TELEGRAM_LIMIT:
        errors.append("%s: %d символов, лимит %d" % (where, len(text), TELEGRAM_LIMIT))


def check_transits(errors, chart):
    report = forecast.transits(chart, dt.date(2026, 9, 12), include_fast=True)

    for item in report["aspects"]:
        limit = forecast.TRANSIT_ORBS[item["aspect"]]
        if item["moving"] in natal.LUMINARIES or item["fixed"] in natal.LUMINARIES:
            limit += 1.0
        if item["orb"] > limit + 1e-9:
            errors.append("транзит %s-%s вне орбиса: %.2f > %.2f"
                          % (item["moving"], item["fixed"], item["orb"], limit))
        if item["moving"] == "moon":
            errors.append("Луна попала в транзиты — она проходит знак за два дня")
        # угол действительно соответствует заявленному аспекту
        moving_lon = report["positions"][item["moving"]]["lon"]
        fixed_lon = chart["positions"][item["fixed"]]["lon"]
        gap = natal.separation(moving_lon, fixed_lon)
        expected = natal.ASPECT_ANGLES[item["aspect"]]
        if abs(abs(gap - expected) - item["orb"]) > 1e-6:
            errors.append("орб транзита %s-%s не сходится с реальным углом"
                          % (item["moving"], item["fixed"]))

    weights = [item["weight"] for item in report["aspects"]]
    if weights != sorted(weights, reverse=True):
        errors.append("транзиты не отсортированы по весу планеты")

    # медленные планеты не должны «прыгать» день ото дня
    today = forecast.transits(chart, dt.date(2026, 9, 12))
    tomorrow = forecast.transits(chart, dt.date(2026, 9, 13))
    for key in ("saturn", "pluto"):
        delta = abs(today["positions"][key]["lon"] - tomorrow["positions"][key]["lon"])
        if delta > 0.2:
            errors.append("%s сместился на %.3f° за сутки" % (key, delta))

    # возвращение планеты: Сатурн через 29.45 года после рождения
    saturn_return_date = chart["date"] + dt.timedelta(days=int(29.45 * 365.25))
    window = []
    for shift in range(-120, 121, 15):
        report_at = forecast.transits(chart, saturn_return_date + dt.timedelta(days=shift),
                                      limit=40)
        window += [i for i in report_at["aspects"]
                   if i["moving"] == "saturn" and i["fixed"] == "saturn"]
    if not any(item["exact_return"] for item in window):
        errors.append("возвращение Сатурна не поймано около 29.5 лет")

    text = forecast.format_transits(report, chart)
    check_html(text, "транзиты", errors)
    if "Транзиты на" not in text:
        errors.append("в выводе транзитов нет заголовка")

    houses = forecast.house_transits(chart, dt.date(2026, 9, 12))
    if len(houses) != 5:
        errors.append("медленных планет по домам %d вместо 5" % len(houses))
    if any(not 1 <= row["house"] <= 12 for row in houses):
        errors.append("транзитная планета попала в несуществующий дом")


def check_progressions(errors, chart):
    report = forecast.progressions(chart, dt.date(2026, 9, 12))

    years = (dt.date(2026, 9, 12) - chart["date"]).days / forecast.TROPICAL_YEAR
    if abs(report["years"] - years) > 1e-6:
        errors.append("неверно посчитан возраст в годах")

    # прогрессивное Солнце: примерно градус в год от натального
    natal_sun = chart["positions"]["sun"]["lon"]
    progressed_sun = report["positions"]["sun"]["lon"]
    drift = (progressed_sun - natal_sun) % 360.0
    if abs(drift - years) > 3.0:
        errors.append("прогрессивное Солнце ушло на %.2f° за %.1f лет "
                      "(ожидалось около градуса в год)" % (drift, years))

    # прогрессивная Луна проходит круг примерно за 27-28 лет
    early = forecast.progressions(chart, chart["date"] + dt.timedelta(days=365))
    late = forecast.progressions(chart, chart["date"] + dt.timedelta(days=365 * 15))
    moon_drift = (late["positions"]["moon"]["lon"]
                  - early["positions"]["moon"]["lon"]) % 360.0
    expected = 14 * 360.0 / 27.4
    if abs(moon_drift - expected % 360.0) > 25.0:
        errors.append("прогрессивная Луна за 14 лет прошла %.1f°, ожидалось ~%.1f°"
                      % (moon_drift, expected % 360.0))

    for item in report["aspects"]:
        limit = forecast.PROGRESSION_ORBS[item["aspect"]]
        if item["moving"] in natal.LUMINARIES or item["fixed"] in natal.LUMINARIES:
            limit += 0.5
        if item["orb"] > limit + 1e-9:
            errors.append("прогрессивный аспект %s-%s вне орбиса: %.2f > %.2f"
                          % (item["moving"], item["fixed"], item["orb"], limit))

    if chart["has_houses"] and not report["positions"]["moon"].get("house"):
        errors.append("прогрессивной Луне не проставлен дом")

    try:
        forecast.progressions(chart, chart["date"] - dt.timedelta(days=1))
        errors.append("прогрессии посчитались на дату раньше рождения")
    except forecast.ForecastError:
        pass

    text = forecast.format_progressions(report, chart)
    check_html(text, "прогрессии", errors)
    if "Прогрессии на" not in text:
        errors.append("в выводе прогрессий нет заголовка")


def check_synastry(errors, chart_a, chart_b):
    report = forecast.synastry(chart_a, chart_b)

    for item in report["aspects"]:
        limit = forecast.SYNASTRY_ORBS[item["aspect"]]
        if item["moving"] in natal.LUMINARIES or item["fixed"] in natal.LUMINARIES:
            limit += 1.0
        if item["orb"] > limit + 1e-9:
            errors.append("синастрический аспект %s-%s вне орбиса: %.2f > %.2f"
                          % (item["moving"], item["fixed"], item["orb"], limit))

    # синастрия симметрична по составу пар
    mirror = forecast.synastry(chart_b, chart_a)
    direct = sorted((i["moving"], i["fixed"], i["aspect"]) for i in report["aspects"])
    flipped = sorted((i["fixed"], i["moving"], i["aspect"]) for i in mirror["aspects"])
    if direct != flipped:
        errors.append("синастрия не симметрична при смене порядка карт")

    counted = report["harmonious"] + report["tense"] + report["conjunctions"]
    if counted > len(report["aspects"]):
        errors.append("сумма по типам аспектов больше их числа")

    if chart_a["has_houses"] and not report["b_in_a_houses"]:
        errors.append("планеты партнёра не разложены по домам")
    for row in report["b_in_a_houses"] + report["a_in_b_houses"]:
        if not 1 <= row["house"] <= 12:
            errors.append("планета попала в несуществующий дом партнёра")

    # своя карта с самой собой: всё в соединениях, орб нулевой
    self_report = forecast.synastry(chart_a, chart_a)
    same = [i for i in self_report["aspects"] if i["moving"] == i["fixed"]]
    if not all(i["aspect"] == "conjunction" and i["orb"] < 1e-6 for i in same):
        errors.append("карта с самой собой дала не соединения с нулевым орбом")

    text = forecast.format_synastry(report, "Ирина", "Пётр")
    check_html(text, "синастрия", errors)
    if "Совместимость" not in text:
        errors.append("в выводе синастрии нет заголовка")
    if "Ирина" not in text or "Пётр" not in text:
        errors.append("в выводе синастрии потерялись имена")


def main():
    errors = []
    if not natal.available():
        print("pyswisseph недоступен — прогностику проверить нельзя")
        return 1

    moscow = natal.find_places("Москва")[0]
    vladivostok = natal.find_places("Владивосток")[0]
    chart_a = natal.build_chart(dt.date(1990, 3, 15), "14:30", moscow)
    chart_b = natal.build_chart(dt.date(1988, 11, 2), "09:15", vladivostok)
    chart_no_time = natal.build_chart(dt.date(1990, 3, 15), None, moscow)

    check_transits(errors, chart_a)
    check_progressions(errors, chart_a)
    check_progressions(errors, chart_no_time)
    check_synastry(errors, chart_a, chart_b)

    print("Ошибок: %d" % len(errors))
    for error in errors[:40]:
        print(" -", error)

    if not errors:
        print("\n--- транзиты ---")
        print(forecast.format_transits(forecast.transits(chart_a, include_fast=True), chart_a))
        print("\n--- прогрессии ---")
        print(forecast.format_progressions(forecast.progressions(chart_a), chart_a))
        print("\n--- синастрия ---")
        print(forecast.format_synastry(forecast.synastry(chart_a, chart_b),
                                       "Ирина", "Пётр"))
    return len(errors)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
