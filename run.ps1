# =====================================================
# FinSight AI - Quick start (Windows / PowerShell)
# =====================================================

Write-Host ""
Write-Host "============================================"
Write-Host "  FinSight AI - Starting up..."
Write-Host "============================================"
Write-Host ""

# 1. .env file
if (-not (Test-Path ".env")) {
    Write-Host "[setup] No .env found - copying from .env.example"
    Copy-Item -Force ".env.example" ".env"
}

# 2. Dependencies
Write-Host "[setup] Installing Python dependencies..."
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] pip install failed." -ForegroundColor Red
    pause
    exit 1
}

# 3. Database
Write-Host ""
Write-Host "[setup] Initializing MySQL database..."
python init_db.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Database initialization failed." -ForegroundColor Red
    Write-Host "Make sure MySQL is running and that the credentials in .env are correct."
    pause
    exit 1
}

# 4. Flask
Write-Host ""
Write-Host "[start] Launching Flask on http://127.0.0.1:5000 ..."
python app.py

pause
