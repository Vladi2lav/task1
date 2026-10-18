@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Итоги торгов - ручной сбор данных

rem Ручной сбор итогов торгов и новостей с сайтов бирж (нужен интернет).
rem Без параметров - за последние 7 дней. Параметры передаются в collect_trades, например:
rem   update_data.bat --days 92                  история за 3 месяца
rem   update_data.bat --exchange ets --since 2026-07-14 --until 2026-10-14
rem --no-pause или NO_PAUSE=1 - не ждать нажатия клавиши в конце.

set "ARGS="
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--no-pause" (set "NO_PAUSE=1") else set ARGS=%ARGS% %1
shift
goto parse_args
:args_done
if not defined ARGS set "ARGS=--days 7"

call "%~dp0deploy\windows\setup_env.bat"
if errorlevel 1 goto fail
"%VENV_PY%" deploy\windows\run.py init-env
if errorlevel 1 goto fail
set "DJANGO_LOG_FILE=logs\collect.log"
"%VENV_PY%" manage.py bootstrap --skip-static --no-fixture
if errorlevel 1 goto fail

echo.
echo === Сбор итогов торгов: %ARGS% ===
"%VENV_PY%" manage.py collect_trades %ARGS%
if errorlevel 1 goto fail

"%VENV_PY%" manage.py help collect_news >nul 2>&1
if errorlevel 1 goto done
echo.
echo === Сбор новостей бирж ===
"%VENV_PY%" manage.py collect_news
if errorlevel 1 goto fail

:done
echo.
echo Готово. Обновите страницу портала в браузере. Журнал - logs\collect.log
if not "%NO_PAUSE%"=="1" pause
exit /b 0

:fail
echo.
echo ОШИБКА: сбор данных не выполнен. Причина указана выше, подробности - logs\collect.log
if not "%NO_PAUSE%"=="1" pause
exit /b 1
