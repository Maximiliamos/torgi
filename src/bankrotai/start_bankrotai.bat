@echo off
chcp 65001 >nul
title BankrotAI Pro - One Console Launcher

set "PROJECT_DIR=D:\8\Coding\TORGI_NEW"
set "OMNI_DIR=D:\8\Coding\omniroute"
set "SRC_DIR=D:\8\Coding\TORGI_NEW\src"
set "GUI_DIR=D:\8\Coding\TORGI_NEW\src\bankrotai"
set "HINDSIGHT_DIR=D:\8\Coding\hindsight"
set "LOG_DIR=D:\8\Coding\TORGI_NEW\logs"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

if exist "%PROJECT_DIR%\.venv\Scripts\python.exe" (
    set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo ========================================
echo        Запуск BankrotAI Pro
echo        режим: одна консоль
echo ========================================
echo.

echo [1/4] Запуск OmniRoute в фоне...
start /B "" cmd /c "cd /d "%OMNI_DIR%" && npm run dev > "%LOG_DIR%\omniroute.log" 2>&1"

echo [2/4] Запуск Backend API в фоне...
start /B "" cmd /c "cd /d "%PROJECT_DIR%" && set PYTHONPATH=%SRC_DIR% && "%PYTHON%" -m uvicorn bankrotai.api:app --port 8000 > "%LOG_DIR%\backend.log" 2>&1"

echo.
set /p START_HINDSIGHT="Запустить Hindsight через Docker? (y/n): "

if /i "%START_HINDSIGHT%"=="y" (
    echo [3/4] Запуск Hindsight в фоне...
    start /B "" cmd /c "cd /d "%HINDSIGHT_DIR%" && docker compose up > "%LOG_DIR%\hindsight.log" 2>&1"
) else (
    echo [3/4] Hindsight пропущен.
)

echo.
echo [4/4] Запуск основной программы GUI...
timeout /t 3 /nobreak >nul

cd /d "%PROJECT_DIR%"
set PYTHONPATH=%SRC_DIR%
"%PYTHON%" "%GUI_DIR%\gui.py"

echo.
echo GUI закрыт.
echo.
echo Логи сохранены здесь:
echo %LOG_DIR%
echo.
pause
