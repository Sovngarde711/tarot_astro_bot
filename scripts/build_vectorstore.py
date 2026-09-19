# -*- coding: utf-8 -*-
"""
Строит локальную векторную базу поверх всей базы знаний бота: 78 карт Таро +
504 записи по натальной астрологии.

Использует sentence-transformers (модель paraphrase-multilingual-MiniLM-L12-v2,
хорошо понимает русский язык) для получения embeddings и ChromaDB — для их
хранения и поиска (персистентная коллекция на диске, без внешнего сервера).

При первом запуске модель (~470 МБ) скачивается с Hugging Face и кешируется
локально (обычно в ~/.cache/huggingface) — повторные запуски интернета не
требуют. Индекс сохраняется в vectorstore/ и загружается модулем
bot/retrieval.py.

Запуск:
    python3 scripts/build_vectorstore.py
"""
import json
import os
import sys

import chromadb
from sentence_transformers import SentenceTransformer

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(BASE_DIR, "data"))

import tarot_data as td  # noqa: E402

VECTORSTORE_DIR = os.path.join(BASE_DIR, "vectorstore")
CHROMA_DIR = os.path.join(VECTORSTORE_DIR, "chroma")
ASTRO_COMBOS_PATH = os.path.join(BASE_DIR, "data", "astrology_combinations.json")

# Многоязычная модель (в т.ч. русский), компактная и быстрая на CPU.
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
COLLECTION_NAME = "tarot_astro_kb"


def build_corpus():
    documents = []

    for card in td.ALL_CARDS:
        search_text = " ".join([
            card["name_ru"], card["name_en"],
            " ".join(card["keywords"]),
            card["upright"], card["reversed"], card["symbolism"],
        ])
        documents.append(dict(
            id=card["id"],
            domain="tarot",
            title=card["name_ru"],
            search_text=search_text,
            payload=card,
        ))

    with open(ASTRO_COMBOS_PATH, encoding="utf-8") as f:
        combos = json.load(f)

    for combo in combos:
        search_text = " ".join([combo["title"], combo["text"]])
        documents.append(dict(
            id=combo["id"],
            domain="astrology",
            title=combo["title"],
            search_text=search_text,
            payload=combo,
        ))

    return documents


def main():
    os.makedirs(VECTORSTORE_DIR, exist_ok=True)
    documents = build_corpus()

    print(f"Загружаю модель эмбеддингов: {EMBEDDING_MODEL_NAME} ...")
    model = SentenceTransformer(EMBEDDING_MODEL_NAME)

    texts = [d["search_text"] for d in documents]
    print(f"Считаю эмбеддинги для {len(texts)} документов...")
    embeddings = model.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,
    )

    # Пересобираем коллекцию с нуля, чтобы не осталось "мусора" от прошлых сборок
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    collection.add(
        ids=[d["id"] for d in documents],
        embeddings=embeddings.tolist(),
        metadatas=[{"domain": d["domain"], "title": d["title"]} for d in documents],
        documents=texts,
    )

    with open(os.path.join(VECTORSTORE_DIR, "documents.json"), "w", encoding="utf-8") as f:
        json.dump(documents, f, ensure_ascii=False, indent=2)

    # Фиксируем имя модели рядом с индексом — retrieval.py должен считать запрос
    # той же моделью, которой считались эмбеддинги документов.
    with open(os.path.join(VECTORSTORE_DIR, "embedding_model.txt"), "w", encoding="utf-8") as f:
        f.write(EMBEDDING_MODEL_NAME)

    print(f"Векторная база собрана: {len(documents)} документов "
          f"({sum(1 for d in documents if d['domain'] == 'tarot')} таро, "
          f"{sum(1 for d in documents if d['domain'] == 'astrology')} астрология).")
    print(f"ChromaDB: {CHROMA_DIR} (коллекция '{COLLECTION_NAME}')")
    print(f"Документы: {os.path.join(VECTORSTORE_DIR, 'documents.json')}")


if __name__ == "__main__":
    main()
