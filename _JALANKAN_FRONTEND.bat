@echo off
chcp 65001 >nul
title ALUR-FRONTEND
cd /d "%~dp0\frontend"

echo Frontend Alur Cerita - Plan 1
echo Port 5173 - Ctrl+C untuk stop
echo.

if exist "%~dp0frontend\node_modules" goto JALANKAN
echo [INFO] node_modules belum ada, install sekarang...
call npm install
if errorlevel 1 goto INSTALL_GAGAL

:JALANKAN
call npm run dev
echo.
echo Frontend berhenti.
pause
exit /b 0

:INSTALL_GAGAL
echo [ERROR] npm install gagal.
pause
exit /b 1