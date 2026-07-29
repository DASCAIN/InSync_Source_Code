@echo off
echo Starting InSync Django Server...
cd /d "%~dp0"
.\venv\Scripts\python.exe manage.py runserver 9000
pause
