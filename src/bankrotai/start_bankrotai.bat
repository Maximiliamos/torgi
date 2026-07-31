@echo off
setlocal EnableExtensions
chcp 65001 >nul
title BankrotAI Desktop Launcher

for %%I in ("%~dp0..\..") do set "PROJECT_DIR=%%~fI"
set "APP_EXE="
set "APP_WORKDIR=%PROJECT_DIR%"

if defined BANKROTAI_EXE if exist "%BANKROTAI_EXE%" set "APP_EXE=%BANKROTAI_EXE%"
if defined BANKROTAI_WORKDIR if exist "%BANKROTAI_WORKDIR%" set "APP_WORKDIR=%BANKROTAI_WORKDIR%"

for %%F in (
    "%PROJECT_DIR%\dist_operating_model\BankrotAI.exe"
    "%PROJECT_DIR%\dist_audit_fixed\BankrotAI.exe"
    "%PROJECT_DIR%\dist_audit\BankrotAI.exe"
    "%PROJECT_DIR%\dist_mapfix\BankrotAI.exe"
    "%PROJECT_DIR%\dist_latest\BankrotAI.exe"
    "%PROJECT_DIR%\dist\BankrotAI.exe"
) do if not defined APP_EXE if exist "%%~fF" set "APP_EXE=%%~fF"

if /I "%~1"=="--check" (
    if defined APP_EXE (
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
    echo ERROR: BankrotAI.exe and Python source fallback were not found.
    exit /b 1
)

if defined APP_EXE (
    start "BankrotAI" /D "%APP_WORKDIR%" "%APP_EXE%"
    exit /b 0
)

where python >nul 2>&1
if errorlevel 1 (
    echo BankrotAI.exe не найден, Python недоступен.
    echo Соберите приложение командой: python -m PyInstaller BankrotAI.spec --noconfirm
    pause
    exit /b 1
)

set "PYTHONPATH=%PROJECT_DIR%\src"
pushd "%PROJECT_DIR%"
python -m bankrotai.cli run-desktop
set "RESULT=%errorlevel%"
popd
exit /b %RESULT%
