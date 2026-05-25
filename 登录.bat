@echo off
cd /d "%~dp0"

set /p MOBILE_PHONE=请输入要登录的手机号: 

echo.
echo 正在为号码 %MOBILE_PHONE% 登录...
echo 浏览器将打开，请在浏览器中输入收到的验证码。
echo.

python chinamobile.py --login %MOBILE_PHONE%

echo.
pause
