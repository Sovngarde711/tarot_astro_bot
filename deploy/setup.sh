#!/usr/bin/env bash
# Установка и запуск tarot_astro_bot на VPS (Ubuntu/Debian).
#
# Запускать на самом сервере, из корня проекта, с правами root:
#   sudo bash deploy/setup.sh
#
# Скрипт идемпотентен — его можно запускать повторно (например, после
# обновления кода) без вреда.
#
# Сам бот работает НЕ от root, а от системного пользователя tarotbot:
# привилегии ему не нужны, а любая уязвимость в его зависимостях под
# root означала бы компрометацию всего сервера.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOT_USER=tarotbot
cd "$PROJECT_DIR"

echo "==> Проект: $PROJECT_DIR"

echo "==> Обновляю список пакетов и ставлю системные зависимости..."
apt-get update -y
# build-essential и python3-dev нужны, если для свежего Python ещё нет
# готового колеса pyswisseph: тогда pip соберёт его из исходников
apt-get install -y python3 python3-venv python3-pip build-essential python3-dev

echo "==> Создаю системного пользователя $BOT_USER (если ещё нет)..."
if ! id -u "$BOT_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$BOT_USER"
fi

echo "==> Создаю виртуальное окружение (если ещё нет)..."
if [ ! -d venv ]; then
  python3 -m venv venv
fi

echo "==> Устанавливаю зависимости проекта (может занять пару минут — скачивается torch)..."
./venv/bin/pip install --upgrade pip
# PyTorch ставим отдельно и только для процессора. Иначе pip притащит
# версию с поддержкой видеокарт: это +3,2 ГБ библиотек CUDA, которые на
# сервере без видеокарты лежат мёртвым грузом и раздувают память процесса.
# Модель эмбеддингов считается на процессоре, разницы в работе нет.
./venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
# Остальное ставим из lock-файла с хешами, а не из requirements.txt с
# «не ниже такой-то версии». Две причины. Первая: бот собирается заново
# на новом сервере через месяцы, и «не ниже» означает другой набор
# библиотек, чем тот, на котором всё проверено. Вторая: хеш — это
# единственное, что отличает настоящий пакет от подменённого, если
# зеркало или учётную запись автора однажды взломают.
# Torch выше уже стоит и заново не скачивается: lock просит ту же
# версию, а сборка для процессора её условию удовлетворяет.
./venv/bin/pip install --require-hashes -r requirements.lock.txt

# Кеш моделей держим внутри проекта: у системного пользователя нет дома,
# а в юните ProtectHome=true всё равно закрывает /root и /home
export HF_HOME="$PROJECT_DIR/.cache/huggingface"
mkdir -p "$HF_HOME"

if [ ! -f vectorstore/documents.json ]; then
  echo "==> Векторная база не найдена — собираю (генерация комбинаций + embeddings)..."
  ./venv/bin/python scripts/generate_combinations.py
  ./venv/bin/python scripts/build_vectorstore.py
else
  echo "==> Векторная база уже на месте, пропускаю сборку."
fi

if [ ! -f .env ]; then
  echo "==> Файл .env не найден — создаю шаблон."
  # umask до создания: файл не должен даже на мгновение быть доступен
  # другим пользователям системы
  ( umask 177; echo "TELEGRAM_BOT_TOKEN=" > .env )
  echo "    !!! Впишите токен бота в $PROJECT_DIR/.env перед запуском (nano .env)"
fi

echo "==> Раздаю права: код только на чтение, запись — в базу, кеш и анкеты..."
# stats — обезличенная статистика, store — анкеты клиентов (персональные
# данные), поэтому их каталоги создаём заранее и закрываем от посторонних
mkdir -p "$PROJECT_DIR/stats" "$PROJECT_DIR/store"
chown -R root:"$BOT_USER" "$PROJECT_DIR"
chmod -R o-rwx "$PROJECT_DIR"
chown -R "$BOT_USER":"$BOT_USER" "$PROJECT_DIR/vectorstore" "$PROJECT_DIR/.cache"     "$PROJECT_DIR/stats" "$PROJECT_DIR/store"
chmod 700 "$PROJECT_DIR/stats" "$PROJECT_DIR/store"
# .env читает systemd (от root) до понижения привилегий, поэтому боту
# достаточно доступа на чтение, а остальным — никакого
chown root:"$BOT_USER" .env
chmod 640 .env

echo "==> Устанавливаю systemd-сервис..."
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" deploy/tarot-astro-bot.service > /etc/systemd/system/tarot-astro-bot.service
chmod 644 /etc/systemd/system/tarot-astro-bot.service
systemctl daemon-reload
systemctl enable tarot-astro-bot

echo
echo "Готово. Дальше:"
echo "  1. Убедитесь, что в $PROJECT_DIR/.env вписан TELEGRAM_BOT_TOKEN."
echo "  2. Запустите бота:   systemctl start tarot-astro-bot"
echo "  3. Проверьте статус: systemctl status tarot-astro-bot"
echo "  4. Логи в реальном времени: journalctl -u tarot-astro-bot -f"
echo
echo "Проверить, что бот работает не от root:"
echo "  systemctl show tarot-astro-bot -p User"
echo "  ps -o user= -C python | sort -u"
