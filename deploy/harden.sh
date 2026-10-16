#!/usr/bin/env bash
# Защита сервера. Запуск (из PowerShell):
#   ssh root@153.76.167.68 "bash /opt/gubkin-bot/deploy/harden.sh"
# Вход на сервер (пароль, ключи) не меняется. Можно запускать повторно.
set -euo pipefail
{   # скрипт читается целиком заранее
DIR=/opt/gubkin-bot
BOT=$DIR/gubkin_schedule_bot
export DEBIAN_FRONTEND=noninteractive

echo "1) Устанавливаю брандмауэр и автообновления..."
apt-get update -qq
apt-get install -y -qq ufw unattended-upgrades >/dev/null

echo "2) Брандмауэр: снаружи открыт только вход по SSH (боту входящие соединения не нужны)"
PORT=$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}')
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null                       # стандартный порт SSH — всегда открыт
if [ -n "$PORT" ] && [ "$PORT" != 22 ]; then
  ufw allow "$PORT/tcp" >/dev/null                # и тот, что указан в настройках SSH, если другой
fi
ufw --force enable >/dev/null

echo "3) Автоматические обновления безопасности Ubuntu (без перезагрузок)"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

echo "4) Песочница для бота: он может писать только в свою папку"
D=/etc/systemd/system/gubkin-bot.service.d
mkdir -p "$D"
install -m 644 "$DIR/deploy/hardening.conf" "$D/hardening.conf"
systemctl daemon-reload
systemctl restart gubkin-bot
sleep 15
if ! systemctl is-active -q gubkin-bot; then
  echo "   ⚠️ с песочницей бот не запустился — убираю её, бот работает как раньше"
  journalctl -u gubkin-bot -n 15 --no-pager || true
  rm -f "$D/hardening.conf"
  systemctl daemon-reload
  systemctl restart gubkin-bot
fi

echo
echo "✅ Готово. Бот: $(systemctl is-active gubkin-bot), брандмауэр: $(ufw status | head -1)"
echo "Совет: смените пароль сервера на длинный (от 16 символов) командой passwd"
exit
}
