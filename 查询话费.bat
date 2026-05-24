@echo off
REM 查询所有已配置手机号的话费/流量/语音
cd /d "%~dp0"
python chinamobile.py --query
pause
