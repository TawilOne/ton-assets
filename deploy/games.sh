#!/usr/bin/env bash
# Один раз: включает сервер мини-игр (шахматы) на VPS.
#   ssh root@IP "bash /opt/gubkin-bot/deploy/games.sh"
# Игры слушают 127.0.0.1:8090, снаружи — HTTPS через Caddy на адресе вида 153-76-167-68.sslip.io
# (sslip.io сам превращает такое имя в IP сервера, сертификат Caddy получает у Let's Encrypt).
set -euo pipefail
{   # скрипт читается целиком заранее
DIR=/opt/gubkin-bot
IP=$(curl -4 -fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')
HOST="${IP//./-}.sslip.io"
echo "Адрес игр: https://$HOST"

"$DIR/venv/bin/pip" install -q -r "$DIR/gubkin_schedule_bot/requirements.txt"
need=""
command -v caddy >/dev/null || need="$need caddy"           # веб-сервер с автоматическим HTTPS
[ -x /usr/games/stockfish ] || need="$need stockfish"       # шахматный движок для игры с ботом
if [ -n "$need" ]; then
  echo "Ставлю:$need"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need >/dev/null
fi
printf '%s {\n\tencode gzip\n\treverse_proxy 127.0.0.1:8090\n}\n' "$HOST" > /etc/caddy/Caddyfile
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
fi
install -m 644 "$DIR/deploy/gubkin-games.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q --now gubkin-games
systemctl restart gubkin-games
systemctl enable -q caddy
systemctl restart caddy

echo "Проверяю (первый сертификат может получаться до минуты)..."
for i in $(seq 1 12); do
  if curl -fs --max-time 10 "https://$HOST/api/ping" 2>/dev/null | grep -q '"ok"'; then
    echo "✅ Игры работают: https://$HOST"
    exit 0
  fi
  sleep 5
done
echo "❌ Игры пока не отвечают по https://$HOST"
echo "Журналы: journalctl -u gubkin-games -n 20 --no-pager ; journalctl -u caddy -n 20 --no-pager"
exit 1
}
