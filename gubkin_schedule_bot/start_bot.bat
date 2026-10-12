@echo off
cd /d "%~dp0"
title Gubkin schedule bot
echo Updating the bot (git pull)...
git pull
echo.
echo Starting the bot. To stop it, close this window or press Ctrl+C.
echo.
python bot.py
echo.
echo The bot has stopped.
pause
