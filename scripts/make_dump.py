"""Выгрузка БД для сдачи: SQL-скрипт и JSON-фикстура Django.

Запуск: python scripts/make_dump.py
Результат: docs/db/trades_dump.sql и docs/db/trades_fixture.json
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / 'docs' / 'db'
DB_PATH = Path(os.environ.get('SQLITE_PATH') or BASE_DIR / 'db.sqlite3')


def make_sql_dump():
    """Полный SQL-дамп (схема + данные) только таблиц приложения exchanges."""
    con = sqlite3.connect(DB_PATH)
    lines = [line for line in con.iterdump() if 'exchanges_' in line or line.startswith(('BEGIN', 'COMMIT'))]
    con.close()
    (OUT_DIR / 'trades_dump.sql').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def make_fixture():
    """JSON-фикстура для `manage.py loaddata`."""
    with open(OUT_DIR / 'trades_fixture.json', 'w', encoding='utf-8') as f:
        subprocess.run(
            [sys.executable, 'manage.py', 'dumpdata', 'exchanges', '--indent', '1'],
            cwd=BASE_DIR, stdout=f, check=True, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )


if __name__ == '__main__':
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_sql_dump()
    make_fixture()
    print('Готово:', *sorted(p.name for p in OUT_DIR.iterdir()))
