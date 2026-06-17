@echo off
REM ===== Media Downloader launcher (Windows) =====
cd /d "%~dp0"

REM Call the venv's Python directly by relative path. Do NOT rely on
REM activate.bat: it bakes in the absolute path from when the venv was
REM created, so renaming/moving the project folder breaks activation.
set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo Creating virtual environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)

if not exist "%VENV_PY%" (
  echo.
  echo ERROR: Could not create the virtual environment.
  echo Make sure Python 3 is installed from python.org and try again.
  pause
  exit /b 1
)

echo Installing/updating dependencies...
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r requirements.txt

echo.
echo Starting Media Downloader...
"%VENV_PY%" app.py

pause
