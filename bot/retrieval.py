# -*- coding: utf-8 -*-
"""
Модуль поиска и формирования ответа.

Стратегия:
  1. Пытаемся распознать в запросе конкретные сущности (карта Таро; планета +
     знак; планета + дом; планета + аспект + планета; планета/знак/дом/аспект
     по отдельности) через словари словоформ и регулярные выражения. Если
     распознавание однозначно — отдаём точную, заранее составленную запись
     из базы (детерминированный, "авторитетный" ответ).
  2. Если однозначно распознать не удалось — используем нейросетевые
     embeddings (sentence-transformers) + векторную базу ChromaDB по всей
     базе (582 документа) и возвращаем несколько наиболее релевантных
     записей по косинусному сходству.
"""
import json
import os
import re
import sys

import chromadb
from sentence_transformers import SentenceTransformer

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(BASE_DIR, "data"))
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))

import tarot_data as td  # noqa: E402
import astrology_data as ad  # noqa: E402

VECTORSTORE_DIR = os.path.join(BASE_DIR, "vectorstore")
CHROMA_DIR = os.path.join(VECTORSTORE_DIR, "chroma")
COLLECTION_NAME = "tarot_astro_kb"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# ---------------------------------------------------------------------------
# Словоформы для распознавания сущностей в свободном тексте
# ---------------------------------------------------------------------------

PLANET_FORMS = {
    "sun": ["солнце", "солнца", "солнцу", "солнцем"],
    "moon": ["луна", "луны", "луне", "луну", "луной"],
    "mercury": ["меркурий", "меркурия", "меркурию", "меркурием"],
    "venus": ["венера", "венеры", "венере", "венеру", "венерой"],
    "mars": ["марс", "марса", "марсу", "марсом"],
    "jupiter": ["юпитер", "юпитера", "юпитеру", "юпитером"],
    "saturn": ["сатурн", "сатурна", "сатурну", "сатурном"],
    "uranus": ["уран", "урана", "урану", "ураном"],
    "neptune": ["нептун", "нептуна", "нептуну", "нептуном"],
    "pluto": ["плутон", "плутона", "плутону", "плутоном"],
}
# слова, однозначно относящиеся и к планете, и к карте Таро
AMBIGUOUS_WORDS = {"солнце", "солнца", "солнцу", "солнцем", "луна", "луны", "луне", "луну", "луной"}

SIGN_FORMS = {
    "aries": ["овен", "овна", "овну", "овном", "овне"],
    "taurus": ["телец", "тельца", "тельцу", "тельцом", "тельце"],
    "gemini": ["близнецы", "близнецов", "близнецам", "близнецами", "близнецах"],
    "cancer": ["рак", "рака", "раку", "раком", "раке"],
    "leo": ["лев", "льва", "льву", "львом", "льве"],
    "virgo": ["дева", "девы", "деве", "деву", "девой"],
    "libra": ["весы", "весов", "весам", "весами", "весах"],
    "scorpio": ["скорпион", "скорпиона", "скорпиону", "скорпионом", "скорпионе"],
    "sagittarius": ["стрелец", "стрельца", "стрельцу", "стрельцом", "стрельце"],
    "capricorn": ["козерог", "козерога", "козерогу", "козерогом", "козероге"],
    "aquarius": ["водолей", "водолея", "водолею", "водолеем", "водолее"],
    "pisces": ["рыбы", "рыб", "рыбам", "рыбами", "рыбах"],
}

ASPECT_FORMS = {
    "conjunction": ["соединение", "соединения", "соединении", "соединением"],
    "sextile": ["секстиль", "секстиля", "секстилю", "секстилем"],
    "square": ["квадрат", "квадрата", "квадрату", "квадратом"],
    "trine": ["трин", "трина", "трину", "трином"],
    "opposition": ["оппозиция", "оппозиции", "оппозицией", "оппозицию"],
}

ORDINAL_HOUSE_WORDS = {
    "первый": 1, "первом": 1, "первого": 1,
    "второй": 2, "втором": 2, "второго": 2,
    "третий": 3, "третьем": 3, "третьего": 3,
    "четвертый": 4, "четвёртый": 4, "четвертом": 4, "четвёртом": 4,
    "пятый": 5, "пятом": 5,
    "шестой": 6, "шестом": 6,
    "седьмой": 7, "седьмом": 7,
    "восьмой": 8, "восьмом": 8,
    "девятый": 9, "девятом": 9,
    "десятый": 10, "десятом": 10,
    "одиннадцатый": 11, "одиннадцатом": 11,
    "двенадцатый": 12, "двенадцатом": 12,
}

TAROT_CONTEXT_WORDS = {"карта", "карты", "карту", "картой", "таро", "расклад", "аркан", "арканы", "колода", "выпала", "вытянул", "вытянула"}
ASTRO_CONTEXT_WORDS = {"натальная", "натальной", "гороскоп", "дом", "доме", "знак", "знаке", "планета", "планеты", "астрология", "асцендент", "транзит", "аспект"}


def normalize(text):
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _find_all(forms_dict, tokens_set):
    found = []
    for key, forms in forms_dict.items():
        for form in forms:
            if form in tokens_set:
                found.append(key)
                break
    return found


def find_house_number(norm_text, tokens):
    # числовая форма: "10 дом", "дом 10", "10-й дом", "в 10 доме" — ищем любое
    # число 1-12 среди токенов, если где-то в тексте вообще есть слово "дом*"
    house_word_present = any(t.startswith("дом") for t in tokens)
    if house_word_present:
        for t in tokens:
            if t.isdigit() and 1 <= int(t) <= 12:
                return int(t)
    # словесная форма: "в десятом доме", "десятый дом"
    for word, num in ORDINAL_HOUSE_WORDS.items():
        if word in tokens:
            return num
    return None


def find_card(norm_text):
    """Ищет точное совпадение названия карты Таро (по подстроке)."""
    best = None
    for card in td.ALL_CARDS:
        name = card["name_ru"].lower().replace("ё", "е")
        if name in norm_text:
            if best is None or len(name) > len(best["name_ru"]):
                best = card
    return best


class Retriever:
    def __init__(self):
        documents_path = os.path.join(VECTORSTORE_DIR, "documents.json")
        if not os.path.exists(documents_path):
            raise RuntimeError(
                "Не найдена векторная база. Сначала выполните: "
                "python3 scripts/build_vectorstore.py"
            )
        with open(documents_path, encoding="utf-8") as f:
            self.documents = json.load(f)
        self.by_id = {d["id"]: d for d in self.documents}

        model_name = DEFAULT_EMBEDDING_MODEL
        model_name_path = os.path.join(VECTORSTORE_DIR, "embedding_model.txt")
        if os.path.exists(model_name_path):
            with open(model_name_path, encoding="utf-8") as f:
                model_name = f.read().strip() or model_name
        self.model = SentenceTransformer(model_name)

        client = chromadb.PersistentClient(path=CHROMA_DIR)
        try:
            self.collection = client.get_collection(COLLECTION_NAME)
        except Exception as exc:
            raise RuntimeError(
                "Не найдена коллекция ChromaDB. Сначала выполните: "
                "python3 scripts/build_vectorstore.py"
            ) from exc

    # -- детерминированный поиск -------------------------------------------------

    def _structured_lookup(self, norm_text):
        tokens = set(norm_text.split())
        planets = _find_all(PLANET_FORMS, tokens)
        signs = _find_all(SIGN_FORMS, tokens)
        aspects = _find_all(ASPECT_FORMS, tokens)
        house_num = find_house_number(norm_text, tokens)
        card = find_card(norm_text)

        has_tarot_ctx = bool(tokens & TAROT_CONTEXT_WORDS)
        has_astro_ctx = bool(tokens & ASTRO_CONTEXT_WORDS)

        # 1. планета + аспект + планета
        if len(planets) == 2 and aspects:
            a, b = planets
            for order in ((a, b), (b, a)):
                doc_id = f"combo_aspect_{order[0]}_{aspects[0]}_{order[1]}"
                if doc_id in self.by_id:
                    return [self.by_id[doc_id]]

        # 2. планета + знак
        if len(planets) == 1 and len(signs) == 1 and not house_num:
            doc_id = f"combo_sign_{planets[0]}_{signs[0]}"
            if doc_id in self.by_id:
                return [self.by_id[doc_id]]

        # 3. планета + дом
        if len(planets) == 1 and house_num and not signs:
            doc_id = f"combo_house_{planets[0]}_{house_num}"
            if doc_id in self.by_id:
                return [self.by_id[doc_id]]

        # 4. карта Таро (учитываем неоднозначность Солнце/Луна)
        if card:
            word = card["name_ru"].lower().replace("ё", "е")
            is_ambiguous = word in AMBIGUOUS_WORDS
            if not is_ambiguous or has_tarot_ctx or not has_astro_ctx:
                return [dict(id=card["id"], domain="tarot", title=card["name_ru"], payload=card)]

        # 5. планета отдельно
        if len(planets) == 1 and not signs and not house_num and not aspects:
            doc_id = f"planet_{planets[0]}"
            if doc_id in self.by_id and (has_astro_ctx or not card):
                return [self.by_id[doc_id]]

        # 6. знак отдельно
        if len(signs) == 1 and not planets and not house_num:
            doc_id = f"sign_{signs[0]}"
            if doc_id in self.by_id:
                return [self.by_id[doc_id]]

        # 7. дом отдельно
        if house_num and not planets and not signs:
            doc_id = f"house_{house_num}"
            if doc_id in self.by_id:
                return [self.by_id[doc_id]]

        # 8. аспект отдельно
        if len(aspects) == 1 and not planets:
            doc_id = f"aspect_{aspects[0]}"
            if doc_id in self.by_id:
                return [self.by_id[doc_id]]

        return None

    # -- семантический поиск (embeddings + ChromaDB) -------------------------

    def _semantic_search(self, query_text, top_k=3):
        # Для эмбеддингов используем исходный текст запроса (а не "мешок
        # слов" из normalize()) — трансформерная модель учитывает порядок
        # слов и связи между ними лучше, чем TF-IDF.
        query_emb = self.model.encode([query_text], normalize_embeddings=True).tolist()
        n_results = min(top_k, len(self.documents)) or 1
        res = self.collection.query(
            query_embeddings=query_emb,
            n_results=n_results,
            include=["distances"],
        )
        ids = res["ids"][0] if res["ids"] else []
        distances = res["distances"][0] if res["distances"] else []
        results = []
        for doc_id, dist in zip(ids, distances):
            score = 1.0 - dist  # косинусное расстояние -> сходство (0..1)
            if score <= 0 or doc_id not in self.by_id:
                continue
            doc = dict(self.by_id[doc_id])
            doc["score"] = float(score)
            results.append(doc)
        return results

    def search(self, query, top_k=3):
        norm_text = normalize(query)
        structured = self._structured_lookup(norm_text)
        if structured:
            return dict(mode="exact", results=structured)
        results = self._semantic_search(query, top_k=top_k)
        return dict(mode="semantic", results=results)


# ---------------------------------------------------------------------------
# Форматирование ответа
# ---------------------------------------------------------------------------

def format_tarot_card(card):
    lines = [
        f"🔮 <b>{card['name_ru']}</b> ({card['name_en']})",
        f"<i>Ключевые слова:</i> {', '.join(card['keywords'])}",
        "",
        f"<b>Прямое положение:</b> {card['upright']}",
        f"<b>Перевёрнутое положение:</b> {card['reversed']}",
        "",
        f"<b>Символика:</b> {card['symbolism']}",
    ]
    if card.get("further_reading"):
        lines.append("")
        lines.append("<i>Для дальнейшего изучения:</i> " + "; ".join(card["further_reading"]))
    return "\n".join(lines)


def format_astro_entry(entry):
    payload = entry.get("payload", entry)
    title = payload.get("title", entry.get("title", ""))
    text = payload.get("text", "")
    return f"✨ <b>{title}</b>\n\n{text}"


def format_result_doc(doc):
    if doc["domain"] == "tarot":
        return format_tarot_card(doc["payload"])
    return format_astro_entry(doc)


def format_answer(search_result):
    mode = search_result["mode"]
    results = search_result["results"]
    if not results:
        return (
            "Не удалось найти точный ответ в базе. Попробуйте переформулировать "
            "запрос — например: «Маг перевёрнутый», «Марс в Овне», «Венера в 7 доме», "
            "«Луна квадрат Сатурн»."
        )
    if mode == "exact":
        return format_result_doc(results[0])

    # semantic: показываем лучший результат + краткий список альтернатив
    parts = [format_result_doc(results[0])]
    if len(results) > 1:
        alt_titles = [r.get("title", "") for r in results[1:]]
        parts.append("\n<i>Возможно, вы также имели в виду:</i> " + "; ".join(alt_titles))
    return "\n".join(parts)
