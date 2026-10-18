"""Помощник для bat-файлов Windows (start.bat, stop.bat, update_data.bat).

    python deploy/windows/run.py check-python   код 0, если версия Python подходит (3.11-3.13)
    python deploy/windows/run.py deps-ok        код 0, если зависимости из requirements.txt уже установлены
    python deploy/windows/run.py deps-mark      отметить, что зависимости установлены (маркер .venv/.requirements.sha256)
    python deploy/windows/run.py init-env       создать .env из .env.example со случайным секретным ключом
    python deploy/windows/run.py web [--open-browser]   веб-сервер waitress (адрес и порт из .env)
    python deploy/windows/run.py scheduler      планировщик ежедневного сбора
    python deploy/windows/run.py status [--open-browser]  код 0, если портал уже запущен

Процессы web и scheduler записывают свой PID в .run/<имя>.pid — по нему их останавливает stop.bat.
"""
import argparse
import hashlib
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
RUN_DIR = BASE_DIR / '.run'
MIN_PYTHON, MAX_PYTHON = (3, 11), (3, 13)


def check_python() -> int:
    return 0 if MIN_PYTHON <= sys.version_info[:2] <= MAX_PYTHON else 1


DEPS_MARKER = BASE_DIR / '.venv' / '.requirements.sha256'


def _requirements_hash() -> str:
    digest = hashlib.sha256((BASE_DIR / 'requirements.txt').read_bytes())
    digest.update(f'{sys.version_info[:2]}'.encode())
    return digest.hexdigest()


def deps_ok() -> int:
    """0 — requirements.txt не менялся с последней установки, 1 — нужно (пере)установить зависимости."""
    try:
        return 0 if DEPS_MARKER.read_text(encoding='ascii').strip() == _requirements_hash() else 1
    except OSError:
        return 1


def deps_mark() -> int:
    DEPS_MARKER.write_text(_requirements_hash(), encoding='ascii')
    return 0


def init_env() -> int:
    env_file = BASE_DIR / '.env'
    if env_file.exists():
        return 0
    example = BASE_DIR / '.env.example'
    text = example.read_text(encoding='utf-8') if example.exists() else 'DJANGO_SECRET_KEY=\nDJANGO_DEBUG=0\n'
    key = secrets.token_urlsafe(50)
    text, count = re.subn(r'^DJANGO_SECRET_KEY=.*$', f'DJANGO_SECRET_KEY={key}', text, count=1, flags=re.M)
    if not count:
        text = f'DJANGO_SECRET_KEY={key}\n' + text
    env_file.write_text(text, encoding='utf-8')
    print('Создан файл настроек .env (секретный ключ сгенерирован автоматически)')
    return 0


def _setup_django():
    sys.path.insert(0, str(BASE_DIR))
    os.chdir(BASE_DIR)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()


def _write_pid(name: str) -> Path:
    RUN_DIR.mkdir(exist_ok=True)
    pid_file = RUN_DIR / f'{name}.pid'
    pid_file.write_text(str(os.getpid()), encoding='ascii')
    return pid_file


def _pid_alive(pid_file: Path) -> bool:
    """Процесс из PID-файла ещё работает (и это python, а не случайный процесс с тем же номером)."""
    try:
        pid = int(pid_file.read_text(encoding='ascii').strip())
    except (OSError, ValueError):
        return False
    try:
        out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return 'python' in out.lower()


def _web_address():
    host = os.environ.get('WEB_HOST', '').strip() or '127.0.0.1'
    port = int(os.environ.get('WEB_PORT', '').strip() or 8000)
    url = f'http://{"127.0.0.1" if host == "0.0.0.0" else host}:{port}/'
    return host, port, url


def _load_env():
    sys.path.insert(0, str(BASE_DIR))
    from config.env import load_dotenv  # noqa: E402  (импорт Django-модуля не нужен)
    load_dotenv(BASE_DIR / '.env')


def status(open_browser: bool) -> int:
    if not _pid_alive(RUN_DIR / 'web.pid'):
        return 1
    _load_env()
    _host, _port, url = _web_address()
    print(f'Портал уже запущен: {url}')
    if open_browser:
        webbrowser.open(url)
    return 0


def _port_busy(host: str, port: int) -> bool:
    probe_host = '127.0.0.1' if host in ('0.0.0.0', '') else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((probe_host, port)) == 0


def _open_browser_when_ready(host: str, port: int, url: str):
    def wait_and_open():
        for _ in range(120):
            if _port_busy(host, port):
                webbrowser.open(url)
                return
            time.sleep(0.5)
    threading.Thread(target=wait_and_open, daemon=True).start()


def web(open_browser: bool) -> int:
    _setup_django()
    host, port, url = _web_address()
    if _port_busy(host, port):
        print(f'ОШИБКА: порт {port} уже занят. Возможно, портал уже запущен (откройте {url}) '
              f'или порт занят другой программой — тогда укажите другой WEB_PORT в файле .env.')
        return 2

    from waitress import serve

    from config.wsgi import application

    pid_file = _write_pid('web')
    print(f'Портал запущен: {url}')
    print('Остановить: закройте это окно или запустите stop.bat')
    if open_browser:
        _open_browser_when_ready(host, port, url)
    try:
        serve(application, host=host, port=port, threads=8, ident='trades-portal')
    except KeyboardInterrupt:
        pass
    finally:
        pid_file.unlink(missing_ok=True)
    return 0


def scheduler() -> int:
    # отдельный файл логов, чтобы два процесса не писали в один (ротация в Windows этого не любит)
    os.environ['DJANGO_LOG_FILE'] = str(BASE_DIR / 'logs' / 'scheduler.log')
    if _pid_alive(RUN_DIR / 'scheduler.pid'):
        print('Планировщик уже запущен')
        return 0
    _setup_django()
    from django.core.management import call_command

    pid_file = _write_pid('scheduler')
    try:
        call_command('run_scheduler')
    except KeyboardInterrupt:
        pass
    finally:
        pid_file.unlink(missing_ok=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['check-python', 'deps-ok', 'deps-mark', 'init-env', 'web', 'scheduler', 'status'])
    parser.add_argument('--open-browser', action='store_true')
    args = parser.parse_args()
    if args.command == 'check-python':
        return check_python()
    if args.command == 'deps-ok':
        return deps_ok()
    if args.command == 'deps-mark':
        return deps_mark()
    if args.command == 'init-env':
        return init_env()
    if args.command == 'status':
        return status(args.open_browser)
    if args.command == 'web':
        return web(args.open_browser)
    return scheduler()


if __name__ == '__main__':
    sys.exit(main())
