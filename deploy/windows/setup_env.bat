@echo off
rem Подготовка окружения для bat-файлов: Python, виртуальное окружение .venv, зависимости.
rem Вызывается из start.bat, update_data.bat, run_tests.bat; текущая папка — корень проекта.
rem Если Python 3.11-3.13 не установлен, скачивается uv (https://docs.astral.sh/uv/) в папку .tools,
rem а он скачивает портативный Python 3.12. Система и PATH пользователя не меняются.
rem FORCE_UV=1 — использовать uv даже при установленном Python.

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
set "TOOLS=%CD%\.tools"
set "UV=%CD%\.tools\uv.exe"
set "UV_VERSION=0.9.17"
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "UV_INSTALL_DIR=%CD%\.tools"
set "UV_NO_MODIFY_PATH=1"
set "UV_PYTHON_INSTALL_DIR=%CD%\.tools\python"
set "UV_CACHE_DIR=%CD%\.tools\cache"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

if not exist "%VENV_PY%" goto create_venv
"%VENV_PY%" deploy\windows\run.py check-python >nul 2>&1
if not errorlevel 1 goto venv_ready
echo   Виртуальное окружение .venv повреждено - например, папку проекта переместили. Создаю заново...
rmdir /s /q .venv

:create_venv
set "SYS_PY="
if /i "%FORCE_UV%"=="1" goto use_uv
call :find_python
if not defined SYS_PY goto use_uv
echo   Найден Python: %SYS_PY%. Создаю виртуальное окружение .venv...
%SYS_PY% -m venv .venv
if errorlevel 1 goto err_venv
goto venv_ready

:use_uv
echo   Подходящий Python 3.11-3.13 не найден - будет скачан портативный Python 3.12.
echo   Это делается один раз, нужен интернет, около 50 МБ. Всё хранится в папке проекта .tools
call :ensure_uv
if errorlevel 1 exit /b 1
"%UV%" venv --seed --python 3.12 --python-preference only-managed .venv
if errorlevel 1 goto err_venv

:venv_ready
"%VENV_PY%" deploy\windows\run.py deps-ok >nul 2>&1
if not errorlevel 1 exit /b 0
echo   Устанавливаю зависимости из requirements.txt - в первый раз это 1-3 минуты...
if exist "%UV%" goto install_uv
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto err_deps
goto deps_done
:install_uv
"%UV%" pip install --python "%VENV_PY%" -r requirements.txt
if errorlevel 1 goto err_deps
:deps_done
"%VENV_PY%" deploy\windows\run.py deps-mark
exit /b 0


:find_python
for %%V in (3.13 3.12 3.11) do call :try_py_launcher %%V
if defined SYS_PY exit /b 0
python deploy\windows\run.py check-python >nul 2>&1
if not errorlevel 1 set "SYS_PY=python"
exit /b 0

:try_py_launcher
if defined SYS_PY exit /b 0
py -%1 deploy\windows\run.py check-python >nul 2>&1
if not errorlevel 1 set "SYS_PY=py -%1"
exit /b 0

:ensure_uv
if exist "%UV%" exit /b 0
if exist "%TOOLS%\bin\uv.exe" set "UV=%TOOLS%\bin\uv.exe"
if exist "%UV%" exit /b 0
if not exist "%TOOLS%" mkdir "%TOOLS%"
echo   Скачиваю uv %UV_VERSION% с https://astral.sh ...
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm https://astral.sh/uv/%UV_VERSION%/install.ps1 | iex"
if exist "%TOOLS%\bin\uv.exe" set "UV=%TOOLS%\bin\uv.exe"
if exist "%UV%" exit /b 0
echo.
echo ОШИБКА: не удалось скачать uv. Проверьте подключение к интернету и повторите запуск.
echo Другой вариант: установите Python 3.12 с https://www.python.org/downloads/ - галочка «Add python.exe to PATH» -
echo и снова запустите start.bat.
exit /b 1

:err_venv
echo.
echo ОШИБКА: не удалось создать виртуальное окружение .venv.
echo Удалите папки .venv и .tools и запустите start.bat ещё раз.
exit /b 1

:err_deps
echo.
echo ОШИБКА: не удалось установить зависимости. Проверьте подключение к интернету и повторите запуск.
echo Если ошибка повторяется - удалите папку .venv и запустите start.bat снова.
exit /b 1
