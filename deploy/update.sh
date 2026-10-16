#!/usr/bin/env bash
# Обновление бота на сервере до последней версии с GitHub:  bash /opt/gubkin-bot/deploy/update.sh
set -euo pipefail
{   # скрипт читается целиком заранее: git ниже перезаписывает и этот файл
DIR=/opt/gubkin-bot
BRANCH=claude/rgu-gubkina-schedule-bot-snqhke
git -C "$DIR" fetch -q origin "$BRANCH"
git -C "$DIR" reset -q --hard "origin/$BRANCH"     # token.txt, admin.txt, users.json и т.п. не в git — сохраняются
"$DIR/venv/bin/pip" install -q -r "$DIR/gubkin_schedule_bot/requirements.txt"
chown -R gubkin: "$DIR/gubkin_schedule_bot"
install -m 644 "$DIR"/deploy/gubkin-{proxy,bot,reproxy}.service "$DIR/deploy/gubkin-reproxy.timer" /etc/systemd/system/
systemctl daemon-reload
D=/etc/systemd/system/gubkin-bot.service.d
# VPN-прокси нужен, только если бот не работает напрямую и не через пересыльщик (relay.sh)
if [ -s /etc/gubkin-bot/vpnkey ] && [ ! -e "$D/direct.conf" ] && [ ! -e "$D/relay.conf" ]; then
  systemctl enable -q --now gubkin-reproxy.timer
  bash "$DIR/deploy/reproxy.sh"   # заново проверить серверы подписки
fi
systemctl restart gubkin-bot
echo "Обновлено до: $(git -C "$DIR" log -1 --format='%h %s')"
exit
}
