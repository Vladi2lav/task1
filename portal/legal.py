"""Сведения для юридических страниц и подвала: владелец сайта, источники данных, лицензии.

Значения владельца и контакта задаются в settings.py (необязательно):
SITE_OWNER, SITE_CONTACT_EMAIL, LEGAL_EDITION_DATE, PROJECT_REPO_URL, SITE_HOSTING_PROVIDER.
Пока они не заданы, на страницах выводятся плейсхолдеры.
"""
import re
from pathlib import Path

from django.conf import settings

DEFAULT_SITE_OWNER = 'ИМЯ ФАМИЛИЯ'
DEFAULT_CONTACT_EMAIL = 'ПОЧТА'
DEFAULT_EDITION_DATE = '14 октября 2026 г.'
DEFAULT_REPO_URL = 'https://github.com/Vladi2lav/task1'
DEFAULT_HOSTING_PROVIDER = '[наименование хостинг-провайдера и страна размещения серверов]'

# Биржи — источники данных
DATA_SOURCES = [
    {
        'code': 'ets',
        'name': 'АО «Товарная биржа «Евразийская Торговая Система» (ETS)',
        'short': 'ETS',
        'site': 'https://ets.kz/',
        'page': 'https://ets.kz/documents/trading-results/',
        'how': 'ежедневные файлы XLSX с итогами торгов, опубликованные в разделе «Итоги торгов»',
    },
    {
        'code': 'caspy',
        'name': 'АО «Товарная биржа «Каспий» (Caspian Commodity Exchange)',
        'short': 'Каспий',
        'site': 'https://ccx.kz/',
        'page': 'https://ccx.kz/trading-and-analytics/trading-results',
        'how': 'данные публичной страницы «Итоги торгов», которые сайт биржи загружает для показа посетителям',
    },
]

# Библиотеки, без которых проект не работает (всегда в составе)
CORE_LIBRARIES = [
    {'name': 'Django', 'purpose': 'веб-фреймворк, ORM, шаблоны', 'license': 'BSD-3-Clause',
     'url': 'https://github.com/django/django/blob/main/LICENSE'},
    {'name': 'requests', 'purpose': 'загрузка страниц и файлов бирж', 'license': 'Apache-2.0',
     'url': 'https://github.com/psf/requests/blob/main/LICENSE'},
    {'name': 'Beautiful Soup 4', 'purpose': 'разбор HTML-страниц', 'license': 'MIT',
     'url': 'https://git.launchpad.net/beautifulsoup/tree/LICENSE'},
    {'name': 'lxml', 'purpose': 'быстрый HTML/XML-парсер', 'license': 'BSD-3-Clause',
     'url': 'https://github.com/lxml/lxml/blob/master/LICENSE.txt'},
    {'name': 'openpyxl', 'purpose': 'чтение файлов ETS и экспорт в Excel', 'license': 'MIT',
     'url': 'https://foss.heptapod.net/openpyxl/openpyxl/-/blob/branch/default/LICENCE.rst'},
    {'name': 'APScheduler', 'purpose': 'ежедневный запуск сбора по расписанию', 'license': 'MIT',
     'url': 'https://github.com/agronholm/apscheduler/blob/master/LICENSE.txt'},
    {'name': 'Chart.js', 'purpose': 'графики на странице «Аналитика» (локальная копия)', 'license': 'MIT',
     'url': 'https://github.com/chartjs/Chart.js/blob/master/LICENSE.md'},
]

# Библиотеки, которые показываются, только если они указаны в requirements.txt
# (ключ — имя пакета в requirements.txt в нижнем регистре)
OPTIONAL_LIBRARIES = {
    'gunicorn': {'name': 'Gunicorn', 'purpose': 'WSGI-сервер для развёртывания', 'license': 'MIT',
                 'url': 'https://github.com/benoitc/gunicorn/blob/master/LICENSE'},
    'waitress': {'name': 'Waitress', 'purpose': 'WSGI-сервер для запуска в Windows', 'license': 'ZPL-2.1',
                 'url': 'https://github.com/Pylons/waitress/blob/main/LICENSE.txt'},
    'whitenoise': {'name': 'WhiteNoise', 'purpose': 'раздача статических файлов', 'license': 'MIT',
                   'url': 'https://github.com/evansd/whitenoise/blob/main/LICENSE'},
    'psycopg': {'name': 'psycopg 3', 'purpose': 'драйвер PostgreSQL', 'license': 'LGPL-3.0',
                'url': 'https://github.com/psycopg/psycopg/blob/master/LICENSE.txt'},
    'psycopg-binary': {'name': 'psycopg 3 (binary)', 'purpose': 'драйвер PostgreSQL', 'license': 'LGPL-3.0',
                       'url': 'https://github.com/psycopg/psycopg/blob/master/LICENSE.txt'},
    'psycopg2': {'name': 'psycopg2', 'purpose': 'драйвер PostgreSQL', 'license': 'LGPL-3.0',
                 'url': 'https://github.com/psycopg/psycopg2/blob/master/LICENSE'},
    'psycopg2-binary': {'name': 'psycopg2-binary', 'purpose': 'драйвер PostgreSQL', 'license': 'LGPL-3.0',
                        'url': 'https://github.com/psycopg/psycopg2/blob/master/LICENSE'},
    'dj-database-url': {'name': 'dj-database-url', 'purpose': 'настройка БД через DATABASE_URL',
                        'license': 'BSD-2-Clause',
                        'url': 'https://github.com/jazzband/dj-database-url/blob/master/LICENSE'},
    'python-dotenv': {'name': 'python-dotenv', 'purpose': 'чтение переменных окружения из .env',
                      'license': 'BSD-3-Clause',
                      'url': 'https://github.com/theskumar/python-dotenv/blob/main/LICENSE'},
    'tzdata': {'name': 'tzdata', 'purpose': 'база часовых поясов', 'license': 'Apache-2.0',
               'url': 'https://github.com/python/tzdata/blob/master/LICENSE'},
    'pytest': {'name': 'pytest', 'purpose': 'автотесты (не входит в работающий сайт)', 'license': 'MIT',
               'url': 'https://github.com/pytest-dev/pytest/blob/main/LICENSE'},
    'pytest-django': {'name': 'pytest-django', 'purpose': 'автотесты Django (не входит в работающий сайт)',
                      'license': 'BSD-3-Clause',
                      'url': 'https://github.com/pytest-dev/pytest-django/blob/main/LICENSE'},
}

_REQUIREMENT_NAME = re.compile(r'^\s*([A-Za-z0-9][A-Za-z0-9._-]*)')


def site_owner() -> str:
    return getattr(settings, 'SITE_OWNER', DEFAULT_SITE_OWNER) or DEFAULT_SITE_OWNER


def contact_email() -> str:
    return getattr(settings, 'SITE_CONTACT_EMAIL', DEFAULT_CONTACT_EMAIL) or DEFAULT_CONTACT_EMAIL


def edition_date() -> str:
    return getattr(settings, 'LEGAL_EDITION_DATE', DEFAULT_EDITION_DATE) or DEFAULT_EDITION_DATE


def hosting_provider() -> str:
    return getattr(settings, 'SITE_HOSTING_PROVIDER', DEFAULT_HOSTING_PROVIDER) or DEFAULT_HOSTING_PROVIDER


def repo_url() -> str:
    return (getattr(settings, 'PROJECT_REPO_URL', DEFAULT_REPO_URL) or DEFAULT_REPO_URL).rstrip('/')


def requirement_names(path=None) -> set:
    """Имена пакетов из requirements.txt (нижний регистр, '_' заменено на '-')."""
    path = Path(path) if path else Path(settings.BASE_DIR) / 'requirements.txt'
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return set()
    names = set()
    for line in lines:
        line = line.split('#', 1)[0]
        match = _REQUIREMENT_NAME.match(line)
        if match:
            names.add(match.group(1).lower().replace('_', '-'))
    return names


def used_libraries(path=None) -> list:
    """Основные библиотеки и дополнительные, найденные в requirements.txt."""
    names = requirement_names(path)
    extra = [lib for key, lib in OPTIONAL_LIBRARIES.items() if key in names]
    return CORE_LIBRARIES + extra


def legal_context() -> dict:
    """Общий контекст юридических страниц."""
    base_repo = repo_url()
    return {
        'site_owner': site_owner(),
        'contact_email': contact_email(),
        'edition_date': edition_date(),
        'hosting_provider': hosting_provider(),
        'data_sources': DATA_SOURCES,
        'repo_url': base_repo,
        'third_party_url': base_repo + '/blob/main/THIRD_PARTY_LICENSES.md',
    }
