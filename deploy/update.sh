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
systemctl restart gubkin-bot
echo "Обновлено до: $(git -C "$DIR" log -1 --format='%h %s')"
