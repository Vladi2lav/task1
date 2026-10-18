@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Итоги торгов - тесты

rem Автотесты (pytest). Параметры передаются в pytest, например: run_tests.bat -k ets -q
rem Сеть не используется. NO_PAUSE=1 - не ждать нажатия клавиши в конце.

call "%~dp0deploy\windows\setup_env.bat"
if errorlevel 1 goto fail

"%VENV_PY%" -c "import pytest, pytest_django" >nul 2>&1
if not errorlevel 1 goto run
echo Устанавливаю pytest из requirements-dev.txt...
if exist "%UV%" (
    "%UV%" pip install --python "%VENV_PY%" -r requirements-dev.txt
) else (
    "%VENV_PY%" -m pip install -r requirements-dev.txt
)
if errorlevel 1 goto fail

:run
set "DJANGO_LOG_FILE=off"
"%VENV_PY%" -m pytest %*
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (echo Все тесты пройдены.) else (echo Есть упавшие тесты - подробности выше.)
if not "%NO_PAUSE%"=="1" pause
exit /b %RESULT%

:fail
echo.
echo ОШИБКА: не удалось подготовить окружение для тестов.
if not "%NO_PAUSE%"=="1" pause
exit /b 1
