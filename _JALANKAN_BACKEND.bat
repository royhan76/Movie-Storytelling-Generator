@echo off
chcp 65001 >nul
title ALUR-BACKEND
cd /d "%~dp0\backend"

echo Backend Alur Cerita - Plan 1
echo Port 8012 - Ctrl+C untuk stop
echo.

if not exist "%~dp0venv\Scripts\python.exe" goto NO_VENV

"%~dp0venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8012
echo.
echo Backend berhenti.
pause
exit /b 0

:NO_VENV
echo [ERROR] venv tidak ditemukan: %~dp0venv\Scripts\python.exe
pause
exit /b 1