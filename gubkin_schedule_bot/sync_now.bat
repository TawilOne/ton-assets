@echo off
rem Download the schedule now and send it to the server (asks for captcha if needed).
cd /d "%~dp0"
python sync.py --interactive
pause
