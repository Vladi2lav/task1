"""Выгрузка БД для сдачи: SQL-скрипт и JSON-фикстуры Django.

Запуск: python scripts/make_dump.py
Результат:
  docs/db/trades_dump.sql     — схема и данные таблиц exchanges_* (SQLite)
  docs/db/trades_fixture.json — биржи, справочники, итоги торгов, журнал запусков (`manage.py loaddata`)
  docs/db/news_fixture.json   — новости бирж (если таблица новостей не пуста)
Загрузка в пустую базу: python manage.py bootstrap (или loaddata обоих файлов по очереди).
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / 'docs' / 'db'
DB_PATH = Path(os.environ.get('SQLITE_PATH') or BASE_DIR / 'db.sqlite3')
ENV = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'DJANGO_LOG_FILE': 'off'}


def make_sql_dump():
    """Полный SQL-дамп (схема + данные) только таблиц приложения exchanges."""
    con = sqlite3.connect(DB_PATH)
    lines = [line for line in con.iterdump() if 'exchanges_' in line or line.startswith(('BEGIN', 'COMMIT'))]
    con.close()
    (OUT_DIR / 'trades_dump.sql').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _dumpdata(out_name: str, *args: str):
    with open(OUT_DIR / out_name, 'w', encoding='utf-8') as f:
        subprocess.run([sys.executable, 'manage.py', 'dumpdata', *args, '--indent', '1'],
                       cwd=BASE_DIR, stdout=f, check=True, env=ENV)


def news_count() -> int:
    con = sqlite3.connect(DB_PATH)
    try:
        return con.execute('SELECT COUNT(*) FROM exchanges_news').fetchone()[0]
    except sqlite3.OperationalError:  # таблицы новостей ещё нет (миграции не применены)
        return 0
    finally:
        con.close()


def make_fixtures():
    """JSON-фикстуры для `manage.py loaddata`: итоги торгов отдельно, новости отдельно."""
    _dumpdata('trades_fixture.json', 'exchanges', '--exclude', 'exchanges.news')
    news_fixture = OUT_DIR / 'news_fixture.json'
    if news_count():
        _dumpdata('news_fixture.json', 'exchanges.news')
    elif news_fixture.exists():
        news_fixture.unlink()


if __name__ == '__main__':
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_sql_dump()
    make_fixtures()
    print('Готово:', *sorted(p.name for p in OUT_DIR.iterdir()))
