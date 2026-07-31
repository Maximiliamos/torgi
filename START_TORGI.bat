@echo off
setlocal EnableExtensions
chcp 65001 >nul
title TORGI Desktop Launcher

for %%I in ("%~dp0.") do set "PROJECT_DIR=%%~fI"
set "APP_EXE=%PROJECT_DIR%\app\BankrotAI.exe"

if /I "%~1"=="--check" (
    if exist "%APP_EXE%" (
        echo OK: %APP_EXE%
        exit /b 0
    )
    if exist "%PROJECT_DIR%\src\bankrotai\gui.py" (
        where python >nul 2>&1
        if not errorlevel 1 (
            echo OK: source fallback is available
            exit /b 0
        )
    )
    echo ERROR: app\BankrotAI.exe and Python source fallback were not found.
    exit /b 1
)

if exist "%APP_EXE%" (
    start "TORGI" /D "%PROJECT_DIR%" "%APP_EXE%"
    exit /b 0
)

where python >nul 2>&1
if errorlevel 1 (
    echo app\BankrotAI.exe не найден, Python недоступен.
    echo Пересоберите приложение по инструкции из README.md.
    pause
    exit /b 1
)

set "PYTHONPATH=%PROJECT_DIR%\src"
pushd "%PROJECT_DIR%"
python -m bankrotai.cli run-desktop
set "RESULT=%errorlevel%"
popd
exit /b %RESULT%
