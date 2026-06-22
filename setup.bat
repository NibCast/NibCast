@echo off
setlocal enabledelayedexpansion

title NibCast - Setup

echo.
echo =====================================================
echo   NibCast  ^|  One-Click Setup
echo =====================================================
echo.

:: ── Check Python ─────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python 3.10+ is required but was not found.
    echo.
    echo  Download Python from: https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo  Python found: %PY_VER%

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo.
    echo  ERROR: Python 3.10+ is required ^(found %PY_VER%^).
    echo.
    echo  Download a newer Python from: https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

:: ── Create venv ───────────────────────────────────────────────
if not exist "venv\" (
    echo.
    echo  Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo  ERROR: Could not create virtual environment.
        pause
        exit /b 1
    )
    echo  Virtual environment created.
) else (
    echo  Virtual environment already exists — skipping.
)

:: ── Install / upgrade dependencies ────────────────────────────
echo.
echo  Installing dependencies (this may take a minute)...
venv\Scripts\pip install --quiet --upgrade pip
venv\Scripts\pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo  ERROR: Dependency installation failed.
    echo  Check your internet connection and try again.
    pause
    exit /b 1
)
echo  Dependencies installed.

:: ── Create shortcuts and (optionally) autostart ───────────────
echo.
echo  Creating desktop shortcut...
venv\Scripts\python install.py

:: ── Ask about autostart ───────────────────────────────────────
echo.
set /p AUTOSTART="  Start NibCast automatically when Windows boots? [y/N]: "
if /i "!AUTOSTART!"=="y" (
    venv\Scripts\python install.py --autostart
)

:: ── Done ──────────────────────────────────────────────────────
echo.
echo =====================================================
echo   Setup complete!
echo =====================================================
echo.
echo  Next steps:
echo.
echo    1. Double-click the NibCast icon on your Desktop
echo    2. A setup wizard will guide you through the rest
echo       (takes about 2 minutes, no technical knowledge needed)
echo.
echo    Quick cheat sheet once it's running:
echo      Hold Ctrl+Alt+V  ^>  speak  ^>  release  ^>  text appears
echo      Ctrl+Alt+Z       ^>  undo the last dictation
echo      Say "Hey Flow"   ^>  hands-free wake word (set in Config)
echo.
echo    Need help? Open the dashboard at http://localhost:7171
echo.
set /p LAUNCH="  Launch NibCast now? [Y/n]: "
if /i not "!LAUNCH!"=="n" (
    echo  Starting NibCast...
    start "" venv\Scripts\pythonw main.py
)

echo.
pause
