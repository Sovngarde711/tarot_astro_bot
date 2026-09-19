# -*- coding: utf-8 -*-
"""Быстрый прогон тестовых запросов по всей базе (используется вручную, не pytest)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

from retrieval import Retriever, format_answer  # noqa: E402
import tarot_data as td  # noqa: E402
import astrology_data as ad  # noqa: E402


def main():
    r = Retriever()
    errors = []

    # 1. Все 78 карт находятся по названию точным поиском
    for card in td.ALL_CARDS:
        res = r.search(card["name_ru"])
        if res["mode"] != "exact" or res["results"][0].get("payload", {}).get("id") != card["id"]:
            errors.append(f"Card not exact-matched: {card['name_ru']}")

    # 2. Все планеты x знаки
    for pk in ad.PLANETS:
        for sk in ad.SIGNS:
            q = f"{ad.PLANETS[pk]['name_ru']} в {ad.SIGNS[sk]['prepositional']}"
            res = r.search(q)
            expected = f"combo_sign_{pk}_{sk}"
            got = res["results"][0]["id"] if res["results"] else None
            if res["mode"] != "exact" or got != expected:
                errors.append(f"Sign combo failed: {q} -> mode={res['mode']} got={got} expected={expected}")

    # 3. Все планеты x дома (1..12)
    for pk in ad.PLANETS:
        for house_num in ad.HOUSES:
            q = f"{ad.PLANETS[pk]['name_ru']} в {house_num} доме"
            res = r.search(q)
            expected = f"combo_house_{pk}_{house_num}"
            got = res["results"][0]["id"] if res["results"] else None
            if res["mode"] != "exact" or got != expected:
                errors.append(f"House combo failed: {q} -> mode={res['mode']} got={got} expected={expected}")

    # 4. Аспекты между всеми парами планет (оба порядка)
    import itertools
    for pa, pb in itertools.combinations(ad.PLANETS.keys(), 2):
        for ak in ad.ASPECTS:
            name_a = ad.PLANETS[pa]["instrumental"]
            name_b = ad.PLANETS[pb]["instrumental"]
            aspect_word = ad.ASPECTS[ak]["name_ru"]
            q = f"{name_a} {aspect_word} {name_b}"
            res = r.search(q)
            expected1 = f"combo_aspect_{pa}_{ak}_{pb}"
            expected2 = f"combo_aspect_{pb}_{ak}_{pa}"
            got = res["results"][0]["id"] if res["results"] else None
            if res["mode"] != "exact" or got not in (expected1, expected2):
                errors.append(f"Aspect combo failed: {q} -> mode={res['mode']} got={got}")

    print(f"Всего ошибок: {len(errors)}")
    for e in errors[:40]:
        print(" -", e)

    # 5. Свободные вопросы — просто проверяем, что не падает и возвращает непустой ответ
    free_queries = [
        "Что значит карта Шут?",
        "Дьявол перевёрнутый",
        "туз мечей значение",
        "рыцарь кубков",
        "что означает Плутон в доме 8",
        "Меркурий в Близнецах",
        "оппозиция Солнца и Плутона",
        "расскажи про 12 дом",
        "что за знак Водолей",
        "секстиль Венеры и Юпитера",
        "случайный вопрос про жизнь",
        "",
    ]
    for q in free_queries:
        try:
            res = r.search(q) if q else dict(mode="none", results=[])
            ans = format_answer(res)
            assert ans
        except Exception as exc:
            errors.append(f"Free query crashed: {q!r} -> {exc}")

    print(f"Итого ошибок после всех тестов: {len(errors)}")
    return len(errors)


if __name__ == "__main__":
    n = main()
    sys.exit(1 if n else 0)
