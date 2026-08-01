@echo off
setlocal EnableExtensions
chcp 65001 >nul
title TORGI

set "PROJECT_DIR=%~dp0"
set "APP_EXE=%~dp0app\BankrotAI.exe"

if /I "%~1"=="--check" goto CHECK
if exist "%APP_EXE%" goto START_EXE
goto START_SOURCE

:CHECK
if exist "%APP_EXE%" goto CHECK_EXE_OK
where python >nul 2>&1
if errorlevel 1 goto CHECK_FAILED
if exist "%PROJECT_DIR%src\bankrotai\gui.py" goto CHECK_SOURCE_OK

:CHECK_FAILED
echo ERROR: app\BankrotAI.exe and Python source fallback were not found.
exit /b 1

:CHECK_EXE_OK
"%APP_EXE%" --smoke-test
if errorlevel 1 goto CHECK_FAILED
echo OK: packaged desktop initialized successfully: %APP_EXE%
exit /b 0

:CHECK_SOURCE_OK
set "PYTHONPATH=%PROJECT_DIR%src"
pushd "%PROJECT_DIR%"
python -m bankrotai.cli run-desktop --smoke-test
set "RESULT=%errorlevel%"
popd
if not "%RESULT%"=="0" goto CHECK_FAILED
echo OK: source desktop initialized successfully.
exit /b 0

:START_EXE
start "TORGI" /D "%PROJECT_DIR%" "%APP_EXE%"
exit /b 0

:START_SOURCE
where python >nul 2>&1
if errorlevel 1 goto START_FAILED
set "PYTHONPATH=%PROJECT_DIR%src"
pushd "%PROJECT_DIR%"
python -m bankrotai.cli run-desktop
set "RESULT=%errorlevel%"
popd
exit /b %RESULT%

:START_FAILED
echo ERROR: app\BankrotAI.exe was not found and Python is unavailable.
echo Rebuild the application using the command from README.md.
pause
exit /b 1
