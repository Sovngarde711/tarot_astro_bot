# -*- coding: utf-8 -*-
"""
Генерирует связные текстовые интерпретации для:
  - планета в знаке зодиака (10 x 12 = 120 записей)
  - планета в доме (10 x 12 = 120 записей)
  - планета в аспекте к планете (10 x 9 / 2 = 45 пар x 5 аспектов = 225 записей)

Тексты собираются из атрибутов planets/signs/houses/aspects (data/astrology_data.py)
по фиксированным шаблонам предложений — это не копирование чужого текста, а
комбинаторика оригинально написанных смысловых блоков.

Результат сохраняется в data/astrology_combinations.json и используется при
построении векторной базы (scripts/build_vectorstore.py).
"""
import json
import sys
import os
import itertools

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))
import astrology_data as ad  # noqa: E402

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "astrology_combinations.json")


def planet_in_sign(planet_key, sign_key):
    p = ad.PLANETS[planet_key]
    s = ad.SIGNS[sign_key]
    text = (
        f"Положение: {p['name_ru']} в {s['prepositional']}. "
        f"Тема «{p['keyword_short']}» ({p['theme']}) проявляется здесь "
        f"{s['quality_phrase']}. {s['description']} "
        f"Стихия знака: {s['element_adj']} ({s['element']}); модальность: "
        f"{s['modality_adj']} ({s['modality']}) — это задаёт тон тому, как "
        f"человек проживает тему «{p['keyword_short']}»."
    )
    return dict(
        id=f"combo_sign_{planet_key}_{sign_key}",
        type="planet_in_sign",
        planet=p["name_ru"], sign=s["name_ru"],
        title=f"{p['name_ru']} в знаке {s['name_ru']}",
        text=text,
    )


def planet_in_house(planet_key, house_num):
    p = ad.PLANETS[planet_key]
    h = ad.HOUSES[house_num]
    text = (
        f"Положение: {p['name_ru']} в {house_num}-м доме. "
        f"Тема «{p['keyword_short']}» ({p['theme']}) направлена на сферу "
        f"жизни: {h['sphere']}. Это значит, что именно здесь особенно "
        f"заметно проявляются качества {p['genitive']} — область "
        f"«{h['keyword_short']}» становится одним из мест, где человек "
        f"наиболее интенсивно выражает эту планетарную тему."
    )
    return dict(
        id=f"combo_house_{planet_key}_{house_num}",
        type="planet_in_house",
        planet=p["name_ru"], house=house_num, house_name=h["name_ru"],
        title=f"{p['name_ru']} в {house_num}-м доме",
        text=text,
    )


def planet_aspect_planet(planet_a_key, planet_b_key, aspect_key):
    a = ad.PLANETS[planet_a_key]
    b = ad.PLANETS[planet_b_key]
    asp = ad.ASPECTS[aspect_key]
    text = (
        f"Аспект: {asp['name_ru']} ({asp['angle']}) между {a['instrumental']} "
        f"и {b['instrumental']}. Тип взаимодействия — {asp['nature']} между "
        f"темами «{a['keyword_short']}» и «{b['keyword_short']}». "
        f"{asp['description']} Применительно к этой паре это означает, что "
        f"«{a['theme']}» и «{b['theme']}» вступают в характерное для "
        f"{asp['genitive']} взаимодействие в жизни человека."
    )
    return dict(
        id=f"combo_aspect_{planet_a_key}_{aspect_key}_{planet_b_key}",
        type="planet_aspect_planet",
        planet_a=a["name_ru"], planet_b=b["name_ru"], aspect=asp["name_ru"],
        title=f"{a['name_ru']} — {asp['name_ru'].lower()} — {b['name_ru']}",
        text=text,
    )


def main():
    combos = []

    for planet_key in ad.PLANETS:
        for sign_key in ad.SIGNS:
            combos.append(planet_in_sign(planet_key, sign_key))
        for house_num in ad.HOUSES:
            combos.append(planet_in_house(planet_key, house_num))

    planet_keys = list(ad.PLANETS.keys())
    for pa, pb in itertools.combinations(planet_keys, 2):
        for aspect_key in ad.ASPECTS:
            combos.append(planet_aspect_planet(pa, pb, aspect_key))

    # Общие описания планет / знаков / домов / аспектов как отдельные записи
    for key, p in ad.PLANETS.items():
        combos.append(dict(
            id=f"planet_{key}", type="planet_overview", planet=p["name_ru"],
            title=f"Планета {p['name_ru']}", text=f"{p['name_ru']} ({p['symbol']}) — {p['description']}",
        ))
    for key, s in ad.SIGNS.items():
        combos.append(dict(
            id=f"sign_{key}", type="sign_overview", sign=s["name_ru"],
            title=f"Знак {s['name_ru']}",
            text=f"{s['name_ru']}. Стихия: {s['element_adj']} ({s['element']}). "
                 f"Модальность: {s['modality_adj']} ({s['modality']}). "
                 f"Управитель — {ad.PLANETS[s['ruler']]['name_ru']}. {s['description']}",
        ))
    for num, h in ad.HOUSES.items():
        combos.append(dict(
            id=f"house_{num}", type="house_overview", house=num,
            title=h["name_ru"],
            text=f"{h['name_ru']} отвечает за {h['sphere']}.",
        ))
    for key, asp in ad.ASPECTS.items():
        combos.append(dict(
            id=f"aspect_{key}", type="aspect_overview", aspect=asp["name_ru"],
            title=f"Аспект: {asp['name_ru']} ({asp['angle']})",
            text=f"{asp['name_ru']} ({asp['angle']}) — аспект типа \"{asp['nature']}\". {asp['description']}",
        ))

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(combos, f, ensure_ascii=False, indent=2)

    print(f"Сгенерировано {len(combos)} записей -> {OUT_PATH}")


if __name__ == "__main__":
    main()
