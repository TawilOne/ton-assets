@echo off
rem One-time setup: key for the server, server offline mode, Windows autostart.
cd /d "%~dp0"
git pull
python sync.py --setup
pause
