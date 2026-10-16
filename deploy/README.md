# Бот на сервере (круглосуточно)

Сервер в России (Ubuntu 22.04/24.04, 1 ядро, 1 ГБ памяти — хватит самого дешёвого).
Сайт Губкина бот открывает напрямую, Telegram — через ваш VPN-ключ (sing-box, локальный прокси).

## Установка

1. Купите сервер с **Ubuntu 24.04**, получите **IP-адрес** и **пароль root**.
2. На компьютере откройте PowerShell и подключитесь: `ssh root@IP-адрес` (пароль при вводе не видно — это нормально).
3. Вставьте и выполните:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/TawilOne/ton-assets/claude/rgu-gubkina-schedule-bot-snqhke/deploy/install.sh | bash
   ```
4. Ответьте на вопросы: токен бота, ваш id (можно пропустить), VPN-ключ или ссылка-подписка.
5. **Остановите бота на ноутбуке** — два бота с одним токеном мешают друг другу.

## Команды на сервере

| Что | Команда |
|---|---|
| Обновить бота | `bash /opt/gubkin-bot/deploy/update.sh` |
| Защита сервера (брандмауэр, автообновления, песочница для бота) | `bash /opt/gubkin-bot/deploy/harden.sh` |
| Логи (выход — Ctrl+C) | `journalctl -u gubkin-bot -f` |
| Перезапустить | `systemctl restart gubkin-bot` |
| Сменить VPN-ключ | `rm /etc/gubkin-bot/sing-box.json` и снова команда установки |
