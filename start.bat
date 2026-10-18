@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Итоги торгов ETS и Каспий - портал

rem Запуск портала на Windows одной кнопкой (Python и Git не нужны).
rem Параметры:
rem   --no-browser  не открывать браузер            (или переменная NO_BROWSER=1)
rem   --no-pause    не ждать нажатия клавиши        (или переменная NO_PAUSE=1)
rem   --collect     при первом запуске собрать свежие данные с сайтов бирж
rem   --uv          использовать портативный Python через uv, даже если Python установлен

set "OPEN_BROWSER=1"
if /i "%NO_BROWSER%"=="1" set "OPEN_BROWSER=0"

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--no-browser" set "OPEN_BROWSER=0"
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"
if /i "%~1"=="--collect" set "BOOTSTRAP_COLLECT=1"
if /i "%~1"=="--uv" set "FORCE_UV=1"
shift
goto parse_args
:args_done

set "BROWSER_ARG="
if "%OPEN_BROWSER%"=="1" set "BROWSER_ARG=--open-browser"

echo ================================================================
echo   Итоги торгов бирж ETS и Каспий
echo ================================================================
echo.
echo [1/4] Проверка Python и зависимостей...
call "%~dp0deploy\windows\setup_env.bat"
if errorlevel 1 goto fail

echo [2/4] Файл настроек .env...
"%VENV_PY%" deploy\windows\run.py init-env
if errorlevel 1 goto fail

"%VENV_PY%" deploy\windows\run.py status %BROWSER_ARG%
if not errorlevel 1 goto already_running

echo [3/4] Подготовка базы данных - при первом запуске загружаются готовые данные...
"%VENV_PY%" manage.py bootstrap
if errorlevel 1 goto fail

echo [4/4] Запуск планировщика и веб-сервера...
start "Итоги торгов - планировщик" /min "%VENV_PY%" deploy\windows\run.py scheduler
echo.
echo   Планировщик ежедневного сбора работает в отдельном свёрнутом окне.
echo   Логи - папка logs. Остановить всё - stop.bat
echo.
"%VENV_PY%" deploy\windows\run.py web %BROWSER_ARG%
if errorlevel 2 goto port_busy

rem веб-сервер остановлен - останавливаем и планировщик
call "%~dp0stop.bat" --quiet
echo.
echo Портал остановлен. Чтобы запустить снова - start.bat
if not "%NO_PAUSE%"=="1" pause
exit /b 0

:already_running
echo   Второй экземпляр не нужен. Остановить портал - stop.bat
if not "%NO_PAUSE%"=="1" pause
exit /b 0

:port_busy
call "%~dp0stop.bat" --quiet

:fail
echo.
echo ================================================================
echo   ОШИБКА: запуск не удался. Причина указана выше.
echo   Подробные логи - в папке logs. Помощь - README.md, «Быстрый старт».
echo ================================================================
if not "%NO_PAUSE%"=="1" pause
exit /b 1
