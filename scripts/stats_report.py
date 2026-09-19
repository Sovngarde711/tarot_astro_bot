# -*- coding: utf-8 -*-
"""Отчёт по статистике бота из консоли.

    python scripts/stats_report.py              # за 30 дней
    python scripts/stats_report.py --days 7     # за неделю
    python scripts/stats_report.py --json       # машиночитаемо, для дашборда
    python scripts/stats_report.py --file stats/events.jsonl

Читает stats/events.jsonl — поток событий, который пишет бот. Персональных
данных там нет: номера чатов хешированы с солью, из признаков только знак
Солнца, возрастная группа и код страны.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

import stats  # noqa: E402

TAGS = re.compile(r"<[^>]+>")


def main():
    parser = argparse.ArgumentParser(description="Статистика Telegram-бота")
    parser.add_argument("--days", type=int, default=30,
                        help="за сколько последних дней считать (по умолчанию 30)")
    parser.add_argument("--json", action="store_true",
                        help="выдать метрики в JSON вместо текста")
    parser.add_argument("--file", default=None,
                        help="путь к файлу событий (по умолчанию stats/events.jsonl)")
    args = parser.parse_args()

    path = args.file or stats.EVENTS_PATH
    events = stats.load_events(path)
    if not events:
        print("Событий пока нет: файл %s пуст или отсутствует.\n"
              "Статистика появится, как только бот начнёт отвечать клиентам." % path)
        return 0

    report = stats.report(events, days=args.days)

    if args.json:
        serialisable = dict(report)
        serialisable["hours"] = dict(report["hours"])
        serialisable["weekdays"] = dict(report["weekdays"])
        print(json.dumps(serialisable, ensure_ascii=False, indent=2))
        return 0

    # тот же отчёт, что приходит в Telegram, только без разметки
    print(TAGS.sub("", stats.format_report(report, "Статистика за %d дн." % args.days)))
    print("\nВсего событий в файле: %d · строк на диске: %s" % (
        len(events), os.path.getsize(path) if os.path.exists(path) else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
