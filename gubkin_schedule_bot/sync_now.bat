@echo off
rem Download the schedule now and send it to the server (asks for captcha if needed).
cd /d "%~dp0"
git pull -q && echo Code is up to date.
python sync.py --interactive
pause
