@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

rem Остановка портала и планировщика, запущенных через start.bat.
rem Процессы находятся по PID-файлам в папке .run; --quiet - без сообщений и паузы.

set "QUIET="
if /i "%~1"=="--quiet" set "QUIET=1"
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"
set "STOPPED=0"

call :stop_one web
call :stop_one scheduler

if defined QUIET exit /b 0
if "%STOPPED%"=="1" (
    echo Портал и планировщик остановлены.
) else (
    echo Запущенных процессов портала не найдено.
)
if not "%NO_PAUSE%"=="1" pause
exit /b 0

:stop_one
set "PID_FILE=.run\%~1.pid"
if not exist "%PID_FILE%" exit /b 0
set "PID="
set /p PID=<"%PID_FILE%"
if not defined PID goto remove_pid
rem проверяем, что под этим номером работает именно python, а не другой процесс
tasklist /FI "PID eq %PID%" /FI "IMAGENAME eq python.exe" /NH 2>nul | find "%PID%" >nul
if errorlevel 1 goto remove_pid
taskkill /PID %PID% /T /F >nul 2>&1
if not errorlevel 1 set "STOPPED=1"
:remove_pid
del /q "%PID_FILE%" >nul 2>&1
exit /b 0
