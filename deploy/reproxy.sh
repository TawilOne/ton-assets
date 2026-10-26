#!/usr/bin/env bash
# Пересобирает прокси из сохранённого VPN-ключа: заново проверяет все серверы подписки
# и оставляет только рабочие. Запускается по таймеру раз в 6 часов и из update.sh.
set -uo pipefail
KEY=/etc/gubkin-bot/vpnkey
CFG=/etc/gubkin-bot/sing-box.json
[ -s "$KEY" ] || { echo "Нет сохранённого VPN-ключа ($KEY)"; exit 0; }
TMP=$(mktemp)
if python3 /opt/gubkin-bot/deploy/vpnkey2singbox.py --probe "$(cat "$KEY")" > "$TMP" \
   && /usr/local/bin/sing-box check -c "$TMP"; then
  if ! cmp -s "$TMP" "$CFG"; then
    install -m 644 "$TMP" "$CFG"
    systemctl restart gubkin-proxy
    echo "Прокси обновлён."
  fi
fi
rm -f "$TMP"
