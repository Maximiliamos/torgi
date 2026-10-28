@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\src"
if not exist "src\bankrotai\connectors\registry\direct_etp_catalogue.py" (
  echo [ERROR] This collector requires the direct-ETP development branch.
  exit /b 1
)
python -m playwright --version >nul 2>&1
if errorlevel 1 (
  echo Install requirements first: python -m pip install playwright requests beautifulsoup4
  echo Then: python -m playwright install chromium
  exit /b 1
)
python scripts\etp_capture_v6.py --out etp-evidence %*
exit /b %errorlevel%
