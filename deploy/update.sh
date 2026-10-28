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
  bash "$DIR/deploy/reproxy.sh"   # заново проверить серверы подписки (настройка — только для Telegram)
else  # прокси не нужен — выключаем, чтобы не крутилась старая настройка
  systemctl disable -q --now gubkin-proxy gubkin-reproxy.timer 2>/dev/null || true
fi
# расписание и всё, что когда-то скачивалось с сайта университета, на сервере не храним
rm -f "$DIR"/gubkin_schedule_bot/{cache.json,cache.json.tmp,groups.json,group.json,user_groups.json,cookies.json} \
      "$DIR"/gubkin_schedule_bot/{subscribers.json,changes_seen.json,sync_status.json,captcha.png,admin_stats.json}
systemctl restart gubkin-bot
# сервер мини-игр — если включён (deploy/games.sh)
if systemctl is-enabled -q gubkin-games 2>/dev/null; then
  install -m 644 "$DIR/deploy/gubkin-games.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl restart gubkin-games
fi
echo "Обновлено до: $(git -C "$DIR" log -1 --format='%h %s')"
exit
}
