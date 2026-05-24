@echo off
chcp 65001 >nul
cd /d "%~dp0"
python chinamobile.py --query
echo.
pause
