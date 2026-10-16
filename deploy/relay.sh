#!/usr/bin/env bash
# Переключает бота на сервере на пересыльщик (Cloudflare Worker и т.п.) вместо VPN-прокси.
#   ssh root@IP "curl -fsSL .../deploy/relay.sh | bash -s https://ИМЯ.ПОДДОМЕН.workers.dev"
set -uo pipefail
URL="${1%/}"
[ -n "$URL" ] || { echo "Укажите адрес пересыльщика: ... | bash -s https://....workers.dev"; exit 1; }
TOKEN=$(cat /opt/gubkin-bot/gubkin_schedule_bot/token.txt 2>/dev/null)
[ -n "$TOKEN" ] || { echo "Нет токена бота на сервере"; exit 1; }

echo "Проверяю пересыльщик..."
ans=$(curl -s --max-time 20 "$URL/bot$TOKEN/getMe")
if echo "$ans" | grep -q '"ok":true'; then
  echo "✅ Telegram отвечает через пересыльщик: $(echo "$ans" | grep -o '"username":"[^"]*"')"
else
  echo "❌ Через пересыльщик Telegram не ответил: ${ans:-нет ответа}" | sed -E 's/bot[0-9]+:[A-Za-z0-9_-]+/bot***/g'
  exit 1
fi

mkdir -p /etc/systemd/system/gubkin-bot.service.d
printf '[Service]\nEnvironment=TG_API_BASE=%s\nEnvironment=TG_PROXY=\n' "$URL" \
  > /etc/systemd/system/gubkin-bot.service.d/relay.conf
systemctl disable -q --now gubkin-proxy gubkin-reproxy.timer 2>/dev/null || true
git -C /opt/gubkin-bot fetch -q origin claude/rgu-gubkina-schedule-bot-snqhke \
  && git -C /opt/gubkin-bot reset -q --hard origin/claude/rgu-gubkina-schedule-bot-snqhke
chown -R gubkin: /opt/gubkin-bot/gubkin_schedule_bot
systemctl daemon-reload
systemctl enable -q gubkin-bot
systemctl restart gubkin-bot
sleep 8
journalctl -u gubkin-bot -n 6 --no-pager -o cat | sed -E 's/bot[0-9]+:[A-Za-z0-9_-]+/bot***/g' | cut -c1-150
echo "Готово: бот ходит в Telegram через $URL"
