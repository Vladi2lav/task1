"""Снимок демо-базы для Vercel: deploy/vercel_demo.sqlite3.

Запуск: python scripts/build_vercel_db.py
Создаёт пустую SQLite-базу, применяет миграции и загружает дампы docs/db/trades_fixture.json
и docs/db/news_fixture.json (если есть). Перед этим обновите дампы: python scripts/make_dump.py
Индивидуальные предприниматели и крестьянские хозяйства обезличиваются (setup_tools/anonymize.py):
серверы Vercel находятся за пределами РК, а персональные данные граждан РК должны храниться в РК.
Файл снимка нужно закоммитить — Vercel берёт его из репозитория.
"""
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TARGET = BASE_DIR / 'deploy' / 'vercel_demo.sqlite3'
FIXTURES = [BASE_DIR / 'docs' / 'db' / 'trades_fixture.json', BASE_DIR / 'docs' / 'db' / 'news_fixture.json']


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGET.with_suffix('.tmp')
    tmp.unlink(missing_ok=True)

    # отдельная база, режим разработки (ключ не нужен), без файла логов
    os.environ['DATABASE_URL'] = 'sqlite:///' + tmp.as_posix()
    os.environ['DJANGO_DEBUG'] = '1'
    os.environ['DJANGO_LOG_FILE'] = 'off'
    os.environ.pop('VERCEL', None)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    sys.path.insert(0, str(BASE_DIR))

    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connection

    from exchanges.models import News, Trade
    from setup_tools.anonymize import anonymize_individuals

    call_command('migrate', interactive=False, verbosity=0)
    for fixture in FIXTURES:
        if fixture.exists():
            call_command('loaddata', str(fixture), verbosity=0)
    hidden = anonymize_individuals()
    trades, news = Trade.objects.count(), News.objects.count()
    connection.close()

    con = sqlite3.connect(tmp)
    con.execute('VACUUM')
    con.close()
    tmp.replace(TARGET)
    print(f'Готово: {TARGET.relative_to(BASE_DIR)} — итогов торгов {trades}, новостей {news}, '
          f'обезличено ИП {hidden}, {TARGET.stat().st_size // 1024} КБ')
    return 0


if __name__ == '__main__':
    sys.exit(main())
