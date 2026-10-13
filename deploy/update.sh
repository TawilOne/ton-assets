#!/usr/bin/env bash
# Обновление бота на сервере до последней версии с GitHub:  bash /opt/gubkin-bot/deploy/update.sh
set -euo pipefail
DIR=/opt/gubkin-bot
BRANCH=claude/rgu-gubkina-schedule-bot-snqhke
git -C "$DIR" fetch -q origin "$BRANCH"
git -C "$DIR" reset -q --hard "origin/$BRANCH"     # token.txt, admin.txt, users.json и т.п. не в git — сохраняются
"$DIR/venv/bin/pip" install -q -r "$DIR/gubkin_schedule_bot/requirements.txt"
chown -R gubkin: "$DIR/gubkin_schedule_bot"
install -m 644 "$DIR/deploy/gubkin-proxy.service" "$DIR/deploy/gubkin-bot.service" /etc/systemd/system/
systemctl daemon-reload
if [ -s /etc/gubkin-bot/vpnkey ] && python3 "$DIR/deploy/vpnkey2singbox.py" "$(cat /etc/gubkin-bot/vpnkey)" > /tmp/sb.json \
   && /usr/local/bin/sing-box check -c /tmp/sb.json; then      # подписка могла обновиться — пересобираем прокси
  install -m 644 /tmp/sb.json /etc/gubkin-bot/sing-box.json
  systemctl restart gubkin-proxy
fi
rm -f /tmp/sb.json
systemctl restart gubkin-bot
echo "Обновлено до: $(git -C "$DIR" log -1 --format='%h %s')"
