@echo off
chcp 65001 >nul
call "%~dp0src\bankrotai\start_bankrotai.bat" %*
exit /b %errorlevel%
