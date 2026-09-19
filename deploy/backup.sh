#!/usr/bin/env bash
# Ежедневный бэкап того, что нельзя потерять.
#
# Что спасаем:
#   store/profiles.json — анкеты клиентов;
#   store/usage.json    — счётчики выданных и оплаченных разборов;
#   store/history.json  — история разборов для блока «С прошлого раза»;
#   stats/              — обезличенные события и соль для хеширования.
#
# Почему это важнее кода: код лежит в репозитории и восстанавливается за
# минуту, а usage.json — единственное место, где записано, кто и за что
# заплатил. Потерять его значит потерять деньги клиентов.
#
# Что НЕ спасаем: venv (переставится), vectorstore (пересоберётся),
# .cache (перекачается), .env (там токен — его бэкап был бы лишней копией
# секрета; токен всегда можно перевыпустить у @BotFather).
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/tarot_astro_bot}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/tarot-astro-bot}"
KEEP_DAYS="${KEEP_DAYS:-14}"

STAMP="$(date +%Y-%m-%d_%H%M)"
ARCHIVE="$BACKUP_DIR/data-$STAMP.tar.gz"

# 700 и root: в архиве персональные данные клиентов, и читать их
# посторонним пользователям системы незачем
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

cd "$PROJECT_DIR"

# --warning=no-file-changed: бот может дописывать события прямо во время
# архивации, и tar честно об этом сообщает. Для наших данных это не
# страшно: файлы пишутся атомарно, через временный файл и переименование
tar czf "$ARCHIVE" --warning=no-file-changed store stats 2>/dev/null || true
chmod 600 "$ARCHIVE"

# Проверяем, что архив читается. Бэкап, который не открывается, — это не
# бэкап, а иллюзия бэкапа, и выясняться это должно сейчас, а не в день,
# когда он понадобится
if ! tar tzf "$ARCHIVE" >/dev/null 2>&1; then
    echo "ОШИБКА: архив $ARCHIVE повреждён" >&2
    exit 1
fi

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
FILES="$(tar tzf "$ARCHIVE" | wc -l)"
echo "Бэкап готов: $ARCHIVE ($SIZE, файлов: $FILES)"

# Старые архивы убираем: две недели — разумный запас, чтобы заметить
# порчу данных и откатиться, и при этом не копить персональные данные
# дольше нужного
DELETED="$(find "$BACKUP_DIR" -name 'data-*.tar.gz' -mtime "+$KEEP_DAYS" -print -delete | wc -l)"
if [ "$DELETED" -gt 0 ]; then
    echo "Удалено архивов старше $KEEP_DAYS дней: $DELETED"
fi

TOTAL="$(find "$BACKUP_DIR" -name 'data-*.tar.gz' | wc -l)"
echo "Всего архивов в $BACKUP_DIR: $TOTAL"
