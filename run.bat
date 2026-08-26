@echo off
REM =====================================================
REM FinSight AI - Quick start (Windows / cmd.exe)
REM =====================================================

echo.
echo ============================================
echo   FinSight AI - Starting up...
echo ============================================
echo.

REM 1. Make sure .env exists
if not exist ".env" (
    echo [setup] No .env found - copying from .env.example
    copy /Y .env.example .env >nul
)

REM 2. Install / upgrade dependencies
echo [setup] Installing Python dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] pip install failed. Make sure Python 3.9+ is installed.
    pause
    exit /b 1
)

REM 3. Initialize the database (creates finsightai + tables)
echo.
echo [setup] Initializing MySQL database...
python init_db.py
if errorlevel 1 (
    echo.
    echo [ERROR] Database initialization failed.
    echo Make sure MySQL is running and that the credentials in .env are correct.
    pause
    exit /b 1
)

REM 4. Start Flask
echo.
echo [start] Launching Flask on http://127.0.0.1:5000 ...
python app.py

pause
