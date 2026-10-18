"""Настройки Django проекта «Итоги торгов бирж ETS и Каспий».

Все параметры развёртывания задаются переменными окружения или файлом .env в корне проекта
(образец с пояснениями — .env.example). Параметры сбора данных и расписания — в scraper.toml.

Режимы:
  * разработка — без .env: DEBUG включён, SQLite db.sqlite3;
  * Windows (start.bat) — .env создаётся автоматически, DEBUG=0, waitress, SQLite;
  * Docker — PostgreSQL через DATABASE_URL, gunicorn (см. docker-compose.yml);
  * Vercel (переменная VERCEL=1) — демо только для чтения на снимке SQLite deploy/vercel_demo.sqlite3.
"""

import os
import secrets
import shutil
import sys
import tomllib
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .env import env_bool, env_int, env_list, env_str, load_dotenv, parse_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')

# Vercel сам выставляет VERCEL=1 в окружении функции, Render — RENDER=true
IS_VERCEL = env_bool('VERCEL', False)
IS_RENDER = env_bool('RENDER', False)

DEBUG = False if IS_VERCEL else env_bool('DJANGO_DEBUG', True)


# --- Секретный ключ ---
SECRET_KEY = env_str('DJANGO_SECRET_KEY')
if not SECRET_KEY and env_str('DJANGO_SECRET_KEY_FILE'):
    # Docker: ключ генерируется при первом запуске и хранится в томе (см. docker-entrypoint.sh)
    try:
        SECRET_KEY = Path(env_str('DJANGO_SECRET_KEY_FILE')).read_text(encoding='utf-8').strip()
    except OSError:
        SECRET_KEY = ''
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-only-ryzpt466gqvjh9y8f0qp8dhbotv1zd5l7q81qer'
    elif IS_VERCEL:
        # демо без настроенного ключа: случайный ключ на экземпляр функции (сессии админки не переживут перезапуск)
        SECRET_KEY = secrets.token_urlsafe(50)
    else:
        raise ImproperlyConfigured(
            'Не задан DJANGO_SECRET_KEY. Укажите его в .env (образец — .env.example) '
            'или включите режим разработки DJANGO_DEBUG=1.'
        )


# --- Хосты и HTTPS ---
ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', ['localhost', '127.0.0.1', '[::1]'] + (['*'] if DEBUG else []))
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS', [])

if IS_VERCEL:
    ALLOWED_HOSTS += ['.vercel.app']
    for _name in ('VERCEL_URL', 'VERCEL_BRANCH_URL', 'VERCEL_PROJECT_PRODUCTION_URL'):
        if env_str(_name):
            ALLOWED_HOSTS.append(env_str(_name))
    CSRF_TRUSTED_ORIGINS += ['https://*.vercel.app']

if IS_RENDER:
    ALLOWED_HOSTS += ['.onrender.com']
    if env_str('RENDER_EXTERNAL_HOSTNAME'):
        ALLOWED_HOSTS.append(env_str('RENDER_EXTERNAL_HOSTNAME'))
    CSRF_TRUSTED_ORIGINS += ['https://*.onrender.com']

# Сайт открыт по HTTPS (обычно за прокси: nginx, Traefik, Vercel)
HTTPS = IS_VERCEL or IS_RENDER or env_bool('DJANGO_HTTPS', False)
if HTTPS:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int('DJANGO_HSTS_SECONDS', 60 * 60 * 24 * 30)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('DJANGO_HSTS_INCLUDE_SUBDOMAINS', False)
    # перенаправление http -> https: на Vercel его делает сама платформа
    SECURE_SSL_REDIRECT = env_bool('DJANGO_SSL_REDIRECT', False)
    SECURE_REDIRECT_EXEMPT = [r'^healthz$']
    SECURE_HSTS_PRELOAD = env_bool('DJANGO_HSTS_PRELOAD', False)
    # осознанные решения, а не ошибки: HSTS для поддоменов и preload включаются отдельно,
    # на Vercel http -> https перенаправляет сама платформа
    SILENCED_SYSTEM_CHECKS = (
        ([] if SECURE_HSTS_INCLUDE_SUBDOMAINS else ['security.W005'])
        + ([] if SECURE_HSTS_PRELOAD else ['security.W021'])
        + (['security.W008'] if IS_VERCEL and not SECURE_SSL_REDIRECT else [])
    )
else:
    # Локальный запуск и Docker в локальной сети работают по HTTP: проверки, требующие HTTPS, не применимы.
    # Включите DJANGO_HTTPS=1, когда сайт будет открыт по HTTPS.
    SILENCED_SYSTEM_CHECKS = ['security.W004', 'security.W008', 'security.W012', 'security.W016']

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'


# --- Приложения ---
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'exchanges',
    'scrapers',
    'portal',
    'setup_tools',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# --- База данных ---
# Приоритет: DATABASE_URL -> POSTGRES_HOST + POSTGRES_DB (+POSTGRES_*) -> SQLITE_PATH -> db.sqlite3 в корне проекта.
# POSTGRES_DB/USER/PASSWORD без POSTGRES_HOST не переключают на PostgreSQL: в .env они могут быть
# заданы только для сервиса db в docker-compose.yml.
DATABASE_URL = env_str('DATABASE_URL')

if IS_VERCEL and not DATABASE_URL:
    # Файловая система Vercel доступна для записи только в /tmp: копируем туда снимок демо-базы
    _demo_src = BASE_DIR / 'deploy' / 'vercel_demo.sqlite3'
    _demo_db = Path(env_str('VERCEL_SQLITE_PATH', '/tmp/trades_demo.sqlite3'))
    if not _demo_db.exists() and _demo_src.exists():
        shutil.copyfile(_demo_src, _demo_db)
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(_demo_db)}}
elif DATABASE_URL:
    DATABASES = {'default': parse_database_url(DATABASE_URL, BASE_DIR)}
elif env_str('POSTGRES_DB') and env_str('POSTGRES_HOST'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': env_str('POSTGRES_DB'),
            'USER': env_str('POSTGRES_USER', 'postgres'),
            'PASSWORD': env_str('POSTGRES_PASSWORD'),
            'HOST': env_str('POSTGRES_HOST', 'localhost'),
            'PORT': env_str('POSTGRES_PORT', '5432'),
        }
    }
else:
    _sqlite_path = env_str('SQLITE_PATH')
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            # SQLITE_PATH — путь к другому файлу БД (например, для демо-данных)
            'NAME': _sqlite_path or BASE_DIR / 'db.sqlite3',
        }
    }

if DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql':
    DATABASES['default']['CONN_MAX_AGE'] = env_int('DJANGO_CONN_MAX_AGE', 60)
    DATABASES['default']['CONN_HEALTH_CHECKS'] = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- Пароли ---
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# --- Язык и время ---
LANGUAGE_CODE = 'ru'
TIME_ZONE = 'Asia/Almaty'
USE_I18N = True
USE_TZ = True


# --- Статика (WhiteNoise) ---
STATIC_URL = 'static/'
# на Vercel collectstatic не выполняется: статика берётся из папок приложений (см. ниже)
STATIC_ROOT = None if IS_VERCEL else BASE_DIR / 'staticfiles'
# статика портала лежит в portal/static и находится автоматически (AppDirectoriesFinder)

# В продакшене (Windows/Docker) — collectstatic в staticfiles/ со сжатием и хешами в именах.
# В разработке и на Vercel статика отдаётся прямо из папок приложений (WHITENOISE_USE_FINDERS),
# поэтому collectstatic не нужен.
# Тесты всегда работают с обычными именами файлов (не зависят от .env и ранее выполненного collectstatic).
TESTING = 'pytest' in sys.modules or sys.argv[1:2] == ['test']
_hashed_static = not DEBUG and not IS_VERCEL and not TESTING
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': 'config.storage.StaticStorage' if _hashed_static
        else 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}
WHITENOISE_USE_FINDERS = DEBUG or IS_VERCEL
WHITENOISE_AUTOREFRESH = DEBUG
# если collectstatic ещё не запускали, страницы всё равно открываются (ссылки на статику без хеша)
WHITENOISE_MANIFEST_STRICT = False


# --- Логи: консоль + файл logs/app.log с ротацией ---
LOG_LEVEL = env_str('DJANGO_LOG_LEVEL', 'INFO').upper() or 'INFO'
_log_file = '' if IS_VERCEL else env_str('DJANGO_LOG_FILE', 'logs/app.log')
if _log_file.lower() in ('0', 'off', 'none', 'no', 'false', '-'):
    _log_file = ''
if _log_file:
    _log_path = Path(_log_file)
    if not _log_path.is_absolute():
        _log_path = BASE_DIR / _log_path
    try:
        _log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:  # папка недоступна для записи — пишем только в консоль
        _log_file = ''

LOG_FILE = str(_log_path) if _log_file else ''

_log_handlers = ['console'] + (['file'] if LOG_FILE else [])
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s %(levelname)s %(name)s: %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
            'formatter': 'standard',
        },
        **({'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOG_FILE,
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 5,
            'encoding': 'utf-8',
            'delay': True,
            'formatter': 'standard',
        }} if LOG_FILE else {}),
    },
    'root': {'handlers': _log_handlers, 'level': LOG_LEVEL},
    'loggers': {
        # сообщения Django идут в общие обработчики (без дублей из стандартной конфигурации)
        'django': {'handlers': [], 'level': LOG_LEVEL, 'propagate': True},
        'django.server': {'handlers': [], 'level': LOG_LEVEL, 'propagate': True},
        'django.db.backends': {'handlers': [], 'level': 'WARNING', 'propagate': True},
    },
}

# Вывод в UTF-8 при перенаправлении в файл/канал (в Windows иначе падает на казахских буквах)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure') and (getattr(_stream, 'encoding', '') or '').lower() not in ('utf-8', 'utf8'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (ValueError, OSError):
            pass


# --- Портал ---
# Режим только для чтения: без ручного запуска сбора (на Vercel включён всегда)
PORTAL_READ_ONLY = IS_VERCEL or env_bool('PORTAL_READ_ONLY', False)
# Реквизиты для юридических страниц /legal/ (пусто — плейсхолдеры по умолчанию из portal/legal.py)
SITE_OWNER = env_str('SITE_OWNER')
SITE_CONTACT_EMAIL = env_str('SITE_CONTACT_EMAIL')
SITE_HOSTING_PROVIDER = env_str('SITE_HOSTING_PROVIDER') or (
    'Vercel Inc. (США)' if IS_VERCEL else 'Render Services, Inc. (США)' if IS_RENDER else '[хостинг-провайдер]')
# Обезличивать индивидуальных предпринимателей сразу при сборе (размещение за пределами РК:
# по Закону РК № 94-V персональные данные граждан РК хранятся в РК). На Render включено по умолчанию.
ANONYMIZE_INDIVIDUALS = env_bool('ANONYMIZE_INDIVIDUALS', IS_RENDER)
LEGAL_EDITION_DATE = env_str('LEGAL_EDITION_DATE')
PROJECT_REPO_URL = env_str('PROJECT_REPO_URL') or 'https://github.com/Vladi2lav/task1'
# Не больше стольких строк в выгрузке Excel за один раз
PORTAL_EXPORT_MAX_ROWS = env_int('PORTAL_EXPORT_MAX_ROWS', 50_000)


# --- Настройки парсеров и расписания (scraper.toml) ---
with open(BASE_DIR / 'scraper.toml', 'rb') as _f:
    SCRAPER_CONFIG = tomllib.load(_f)
