"""Чтение настроек из переменных окружения и файла .env (без внешних зависимостей).

Формат .env: строки `ИМЯ=значение`, комментарии с `#`, значения можно брать в кавычки.
Переменные, уже заданные в окружении (системой, Docker, Vercel), имеют приоритет над .env.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

TRUE_VALUES = {'1', 'true', 'yes', 'on', 'y', 'да'}
FALSE_VALUES = {'0', 'false', 'no', 'off', 'n', 'нет', ''}


def parse_dotenv(text: str) -> dict:
    """Разобрать содержимое .env в словарь."""
    result = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, sep, value = line.partition('=')
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = value.replace('\\n', '\n').replace('\\"', '"')
        else:
            # комментарий в конце строки: `KEY=value  # пояснение`
            for marker in (' #', '\t#'):
                if marker in value:
                    value = value.split(marker, 1)[0].rstrip()
        result[key] = value
    return result


def load_dotenv(path: Path, override: bool = False) -> dict:
    """Загрузить .env в os.environ (существующие переменные не перезаписываются). Нет файла — ничего не делает."""
    try:
        text = Path(path).read_text(encoding='utf-8-sig')
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return {}
    values = parse_dotenv(text)
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def env_str(name: str, default: str = '') -> str:
    value = os.environ.get(name)
    return default if value is None else value.strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ImproperlyConfigured(f'Переменная {name}: ожидается 1/0 (true/false), получено «{value}»')


def env_int(name: str, default: int = 0) -> int:
    value = os.environ.get(name, '').strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ImproperlyConfigured(f'Переменная {name}: ожидается целое число, получено «{value}»')


def env_list(name: str, default=()) -> list:
    """Список через запятую: `a, b ,c` -> ['a', 'b', 'c']. Переменная не задана — default."""
    value = os.environ.get(name)
    if value is None:
        return list(default)
    return [item.strip() for item in value.split(',') if item.strip()]


def parse_database_url(url: str, base_dir: Path) -> dict:
    """DATABASE_URL -> словарь для settings.DATABASES['default'].

    Поддерживаются:
      sqlite:///db.sqlite3          (путь относительно папки проекта)
      sqlite:////data/db.sqlite3    (абсолютный путь в Linux)
      sqlite:///C:/data/db.sqlite3  (абсолютный путь в Windows)
      postgres://user:password@host:5432/dbname?sslmode=require  (также postgresql://)
    """
    url = url.strip()
    scheme, sep, rest = url.partition('://')
    scheme = scheme.lower()
    if not sep:
        raise ImproperlyConfigured(f'DATABASE_URL: не удалось разобрать «{url}»')

    if scheme in ('sqlite', 'sqlite3'):
        path = rest[1:] if rest.startswith('/') else rest
        path = unquote(path.split('?', 1)[0])
        if not path or path == ':memory:':
            return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}
        if not (os.path.isabs(path) or path.startswith('/')):
            path = str(Path(base_dir) / path)
        return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': path}

    if scheme in ('postgres', 'postgresql', 'pgsql'):
        parts = urlsplit(url)
        config = {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': unquote(parts.path.lstrip('/')),
            'USER': unquote(parts.username or ''),
            'PASSWORD': unquote(parts.password or ''),
            'HOST': parts.hostname or '',
            'PORT': str(parts.port or ''),
        }
        options = dict(parse_qsl(parts.query))
        if options:
            config['OPTIONS'] = options
        return config

    raise ImproperlyConfigured(f'DATABASE_URL: схема «{scheme}» не поддерживается (используйте sqlite:// или postgres://)')
