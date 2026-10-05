@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

title STOP - Alur Cerita

echo ============================================================
echo   Menghentikan Alur Cerita Plan 1...
echo ============================================================
echo.

call :KILL_PORT 8012
call :KILL_PORT 5173
ping 127.0.0.1 -n 3 >nul 2>&1

netstat -ano | findstr /R /C:"TCP.*:8012 .*LISTENING" > "%TEMP%\alur_chk1.txt" 2>nul
netstat -ano | findstr /R /C:"TCP.*:5173 .*LISTENING" > "%TEMP%\alur_chk2.txt" 2>nul

set MASIH=0
for /f "tokens=1" %%A in ("%TEMP%\alur_chk1.txt") do set MASIH=1
for /f "tokens=1" %%A in ("%TEMP%\alur_chk2.txt") do set MASIH=1
del "%TEMP%\alur_chk1.txt" >nul 2>&1
del "%TEMP%\alur_chk2.txt" >nul 2>&1

if "%MASIH%"=="1" goto MASIH_AKTIF
echo.
echo   Semua service berhenti. Port 8012 dan 5173 bebas.
echo.
goto SELESAI

:MASIH_AKTIF
echo.
echo   PERINGATAN - masih ada proses di port 8012 atau 5173.
echo   Tutup jendela ALUR-BACKEND / ALUR-FRONTEND secara manual.
echo.

:SELESAI
endlocal
pause
exit /b 0

:KILL_PORT
netstat -ano | findstr /R /C:"TCP.*:%~1 .*LISTENING" > "%TEMP%\alur_k_%~1.txt" 2>nul
for /f "tokens=5" %%P in ("%TEMP%\alur_k_%~1.txt") do taskkill /F /T /PID %%P >nul 2>&1
del "%TEMP%\alur_k_%~1.txt" >nul 2>&1
goto :eof