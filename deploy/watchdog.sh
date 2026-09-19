#!/usr/bin/env bash
# Сторож: замечает, что бот перестал отвечать, и пишет об этом владельцу.
#
# Зачем он нужен. systemd поднимет упавший процесс сам — но есть случай,
# который он не видит: процесс жив, а Telegram недоступен. Именно так и
# случилось 14 сентября на домашней машине: отвалился VPN, бот четыре
# часа молча переспрашивал getUpdates, и никто об этом не узнал.
#
# Что проверяем:
#   1. служба запущена;
#   2. Telegram отвечает на getMe нашим токеном;
#   3. в журнале за последние минуты нет потока ошибок getUpdates.
#
# О чём сообщаем: только о смене состояния. Сторож, который пишет каждые
# пять минут «всё плохо», через час перестают читать — и тогда он
# бесполезен. Поэтому сообщение уходит один раз при поломке и один раз
# при восстановлении.
set -uo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/tarot_astro_bot}"
SERVICE="${SERVICE:-tarot-astro-bot}"
STATE_FILE="${STATE_FILE:-/var/lib/tarot-watchdog.state}"

# .env читается от root: там токен, и права на файл 640 root:tarotbot
# Переменные можно задать снаружи — это нужно, чтобы проверять
# сторожа, не рассылая тревожных сообщений живым людям
TOKEN="${TOKEN:-$(grep -E '^TELEGRAM_BOT_TOKEN=' "$PROJECT_DIR/.env" | cut -d= -f2- | tr -d '\r\n[:space:]')}"
ADMINS="${ADMINS-$(grep -E '^ADMIN_CHAT_ID=' "$PROJECT_DIR/.env" | cut -d= -f2- | tr -d '\r\n[:space:]')}"

problem=""

# --- 1. Служба -------------------------------------------------------------
if ! systemctl is-active --quiet "$SERVICE"; then
    problem="служба $SERVICE не запущена"
    # Пытаемся поднять сами: systemd сдаётся после серии быстрых падений
    # (StartLimitBurst), и тогда ручной restart — единственный способ
    systemctl reset-failed "$SERVICE" 2>/dev/null
    systemctl start "$SERVICE" 2>/dev/null
    sleep 10
    if systemctl is-active --quiet "$SERVICE"; then
        problem="$problem, перезапустил — поднялась"
    else
        problem="$problem, перезапуск не помог"
    fi
fi

# --- 2. Telegram отвечает? -------------------------------------------------
if [ -z "$problem" ] && [ -n "$TOKEN" ]; then
    answer="$(curl -s -m 15 "https://api.telegram.org/bot$TOKEN/getMe" || true)"
    case "$answer" in
        *'"ok":true'*) ;;
        "")  problem="Telegram не отвечает: сеть недоступна" ;;
        *)   problem="Telegram отклонил запрос: $(echo "$answer" | head -c 120)" ;;
    esac
fi

# --- 3. Поток ошибок опроса ------------------------------------------------
if [ -z "$problem" ]; then
    failures="$(journalctl -u "$SERVICE" --since '10 minutes ago' --no-pager 2>/dev/null \
                | grep -c 'getUpdates failed' || true)"
    # Одиночные обрывы связи бот переживает сам и повторяет запрос; поток
    # ошибок означает, что связи нет уже минуты
    if [ "${failures:-0}" -ge 5 ]; then
        problem="бот не может получить сообщения: $failures неудачных опросов за 10 минут"
    fi
fi

# --- Сообщаем только о смене состояния -------------------------------------
previous=""
[ -f "$STATE_FILE" ] && previous="$(cat "$STATE_FILE")"
current="ok"
[ -n "$problem" ] && current="fail"

notify() {
    local text="$1"
    [ -z "$TOKEN" ] && return
    local IFS=','
    for chat in $ADMINS; do
        [ -z "$chat" ] && continue
        curl -s -m 15 -o /dev/null \
             --data-urlencode "chat_id=$chat" \
             --data-urlencode "text=$text" \
             "https://api.telegram.org/bot$TOKEN/sendMessage" || true
    done
}

host="$(hostname)"
if [ "$current" = "fail" ] && [ "$previous" != "fail" ]; then
    echo "ПРОБЛЕМА: $problem"
    notify "🔴 Бот на сервере $host: $problem"$'\n\n'"Время: $(date '+%d.%m.%Y %H:%M %Z')"
elif [ "$current" = "ok" ] && [ "$previous" = "fail" ]; then
    echo "Восстановилось"
    notify "🟢 Бот на сервере $host снова отвечает."$'\n\n'"Время: $(date '+%d.%m.%Y %H:%M %Z')"
else
    echo "Состояние: $current${problem:+ ($problem)}"
fi

mkdir -p "$(dirname "$STATE_FILE")"
echo "$current" > "$STATE_FILE"
chmod 600 "$STATE_FILE"
