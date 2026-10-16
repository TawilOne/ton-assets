#!/usr/bin/env bash
# Ставит Cloudflare WARP в режиме прокси (127.0.0.1:40000) и проверяет, открывается ли через него Telegram.
#   ssh root@IP "curl -fsSL https://raw.githubusercontent.com/TawilOne/ton-assets/claude/rgu-gubkina-schedule-bot-snqhke/deploy/warp.sh | bash"
set -uo pipefail
say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
W() { warp-cli --accept-tos "$@"; }

if ! command -v warp-cli >/dev/null; then
  say "Ставлю Cloudflare WARP"
  . /etc/os-release
  curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
    | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg || { echo "❌ Не скачался ключ Cloudflare"; exit 1; }
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ ${VERSION_CODENAME} main" \
    > /etc/apt/sources.list.d/cloudflare-client.list
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cloudflare-warp >/dev/null \
    || { echo "❌ WARP не установился (сайт Cloudflare недоступен?)"; exit 1; }
fi

say "Включаю WARP"
systemctl enable -q --now warp-svc 2>/dev/null; sleep 3
W registration show >/dev/null 2>&1 || W registration new
W mode proxy
W connect
for i in 1 2 3 4 5 6; do sleep 5; W status | grep -qi connected && break; done
W status

say "Проверка"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -x socks5h://127.0.0.1:40000 https://api.telegram.org || true)
echo "TELEGRAM $code"
if [ "$code" != "000" ]; then echo "✅ Telegram через WARP открывается"; else echo "❌ Telegram через WARP не открывается"; fi
