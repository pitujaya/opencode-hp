@echo off
REM jalankan server - FastAPI service-hp-management
cd /d "%~dp0"

echo === Cek Python ===
python --version
if errorlevel 1 (
  echo Python tidak ditemukan! Install Python 3.10+ dulu.
  pause
  exit /b 1
)

echo.
echo === Install deps (fastapi + uvicorn) ===
pip install -r requirements.txt
if errorlevel 1 (
  echo pip install gagal, coba install manual: pip install fastapi uvicorn
  pause
  exit /b 1
)

echo.
echo === Menjalankan FastAPI di http://127.0.0.1:8000 ===
echo Docs: http://127.0.0.1:8000/docs
python -m uvicorn main:app --host 0.0.0.0 --reload --port 8000

pause
