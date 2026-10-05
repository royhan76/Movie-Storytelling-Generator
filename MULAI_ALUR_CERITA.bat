@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

title ALUR CERITA - Plan 1

set "ROOT=%CD%"
set "API_PORT=8012"
set "WEB_PORT=5173"

echo ============================================================
echo   ALUR CERITA - PLAN 1
echo ============================================================
echo.

REM ---------- 1. Bersihkan port lama ----------
echo [1/4] Membersihkan proses lama di port %API_PORT% dan %WEB_PORT%...
call :KILL_PORT %API_PORT%
call :KILL_PORT %WEB_PORT%

REM ---------- 2. Prasyarat ----------
if not exist "%ROOT%\venv\Scripts\python.exe" goto NO_VENV

if exist "%ROOT%\.env" goto HAVE_ENV
echo [PERINGATAN] .env belum ada di root project.
echo               Salin .env.example menjadi .env lalu isi GEMINI_API_KEY.
echo.
:HAVE_ENV

if exist "%ROOT%\frontend\node_modules" goto HAVE_MODULES
echo [2/4] npm install (frontend)...
pushd "%ROOT%\frontend"
call npm install
popd
goto RUN_TESTS
:HAVE_MODULES
echo [2/4] Dependency frontend sudah ada.

REM ---------- 3. Test backend ----------
:RUN_TESTS
echo [3/4] Menjalankan test backend...
pushd "%ROOT%\backend"
call "%ROOT%\venv\Scripts\python.exe" -m tests.run_all 2>&1 | findstr /C:"TEST MODULES PASS" >nul
popd
if errorlevel 1 goto TEST_GAGAL
echo    OK - 4 modul test hijau.
goto START_API
:TEST_GAGAL
echo    PERINGATAN - ada test gagal. Cek manual:
echo    cd backend ^&^& ..\venv\Scripts\python.exe -m tests.run_all

REM ---------- 4. Backend ----------
:START_API
echo [4/4] Menyalakan backend pada port %API_PORT%...
start "ALUR-BACKEND" "%ComSpec%" /k call "%ROOT%\_JALANKAN_BACKEND.bat"

set "API_OK=0"
for /L %%I in (1,1,40) do call :WAIT_API
if "%API_OK%"=="1" goto API_SIAP

echo    PERINGATAN - backend belum merespons, cek jendela ALUR-BACKEND.
goto START_WEB
:API_SIAP
echo    backend SIAP - http://127.0.0.1:%API_PORT%/docs

REM ---------- 5. Frontend ----------
:START_WEB
echo     Menyalakan frontend pada port %WEB_PORT%...
start "ALUR-FRONTEND" "%ComSpec%" /k call "%ROOT%\_JALANKAN_FRONTEND.bat"
ping 127.0.0.1 -n 8 >nul 2>&1

echo.
echo ============================================================
echo   SIAP.
echo.
echo   UI      : http://localhost:%WEB_PORT%
echo   Backend : http://127.0.0.1:%API_PORT%/docs
echo   Output  : %ROOT%\projects
echo.
echo   Jangan tutup jendela ALUR-BACKEND / ALUR-FRONTEND.
echo   Stop dengan STOP_ALUR_CERITA.bat
echo ============================================================
echo.
start "" "http://localhost:%WEB_PORT%"
endlocal
exit /b 0

rem ---------- subroutine ----------
:KILL_PORT
netstat -ano | findstr /R /C:"TCP.*:%~1 .*LISTENING" > "%TEMP%\alur_p_%~1.txt" 2>nul
for /f "tokens=5" %%P in ("%TEMP%\alur_p_%~1.txt") do taskkill /F /T /PID %%P >nul 2>&1
del "%TEMP%\alur_p_%~1.txt" >nul 2>&1
goto :eof

:WAIT_API
if "%API_OK%"=="1" goto :eof
ping 127.0.0.1 -n 2 >nul 2>&1
curl -s "http://127.0.0.1:%API_PORT%/api/health" 2>nul | findstr /C:"status" >nul
if not errorlevel 1 set "API_OK=1"
goto :eof

:NO_VENV
echo [ERROR] venv tidak ditemukan: %ROOT%\venv\Scripts\python.exe
echo         Jalankan: python -m venv venv
pause
exit /b 1