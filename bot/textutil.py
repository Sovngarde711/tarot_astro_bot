# -*- coding: utf-8 -*-
"""Мелкие текстовые помощники, общие для движка консультаций и прогностики."""
import re


def normalize(text):
    """Нижний регистр, ё -> е, без знаков препинания. Для сравнения слов."""
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def doc_text(retriever, doc_id):
    """Текст записи из базы знаний по id (или None, если записи нет)."""
    if retriever is None:
        return None
    doc = retriever.by_id.get(doc_id)
    if not doc:
        return None
    return doc.get("payload", {}).get("text") or doc.get("search_text")


def trim(text, limit=600):
    """Обрезает до лимита по границе предложения, в крайнем случае — слова."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sentence_end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if sentence_end > limit * 0.4:
        return cut[:sentence_end + 1]
    space = cut.rfind(" ")
    if space > limit * 0.4:
        cut = cut[:space]
    return cut.rstrip(" ,;:—-") + "…"
