# Сторонние компоненты и их лицензии

Проект распространяется по лицензии MIT (см. [LICENSE](LICENSE)). Ниже — сторонние компоненты, которые используются при работе
и развёртывании. Версии — установленные на 18.10.2026 по `requirements.txt` (в `requirements.txt` указаны минимальные версии).
Лицензии проверены по метаданным пакетов (`importlib.metadata`) и сайтам проектов.

## Python-зависимости (requirements.txt)

| Компонент | Версия | Лицензия | Ссылка | Назначение |
|---|---|---|---|---|
| Django | 5.2.18 | BSD-3-Clause | https://www.djangoproject.com/ | веб-фреймворк |
| asgiref | 3.12.1 | BSD-3-Clause | https://github.com/django/asgiref | зависимость Django |
| sqlparse | 0.6.0 | BSD-3-Clause | https://github.com/andialbrecht/sqlparse | зависимость Django |
| tzdata | 2026.5 | Apache-2.0 | https://github.com/python/tzdata | часовые пояса (Windows) |
| requests | 2.34.2 | Apache-2.0 | https://requests.readthedocs.io | HTTP-запросы к сайтам бирж |
| urllib3 | 2.8.0 | MIT | https://github.com/urllib3/urllib3 | зависимость requests |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna | зависимость requests |
| charset-normalizer | 3.5.2 | MIT | https://github.com/jawah/charset_normalizer | зависимость requests |
| certifi | 2026.7.22 | MPL-2.0 | https://github.com/certifi/python-certifi | корневые сертификаты (зависимость requests) |
| beautifulsoup4 | 4.15.0 | MIT | https://www.crummy.com/software/BeautifulSoup/ | разбор HTML |
| soupsieve | 3.0.3 | MIT | https://github.com/facelessuser/soupsieve | зависимость beautifulsoup4 |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions | зависимость beautifulsoup4 |
| lxml | 6.1.3 | BSD-3-Clause (включает libxml2/libxslt — MIT) | https://lxml.de/ | быстрый парсер HTML |
| openpyxl | 3.1.5 | MIT | https://openpyxl.readthedocs.io | чтение XLSX бирж, экспорт в Excel |
| et_xmlfile | 2.0.0 | MIT | https://foss.heptapod.net/openpyxl/et_xmlfile | зависимость openpyxl |
| APScheduler | 3.11.3 | MIT | https://github.com/agronholm/apscheduler | ежедневный планировщик |
| tzlocal | 5.4.4 | MIT | https://github.com/regebro/tzlocal | зависимость APScheduler |
| whitenoise | 6.12.0 | MIT | https://github.com/evansd/whitenoise | раздача статики |
| psycopg / psycopg-binary | 3.3.6 | LGPL-3.0-only (бинарный пакет включает libpq — PostgreSQL License, OpenSSL — Apache-2.0) | https://www.psycopg.org/ | драйвер PostgreSQL |
| gunicorn | 26.2.0 | MIT | https://gunicorn.org/ | WSGI-сервер (Linux/Docker) |
| waitress | 3.0.2 | ZPL-2.1 | https://github.com/Pylons/waitress | WSGI-сервер (Windows) |

Только для разработки (`requirements-dev.txt`, в работающий сайт не входят):

| Компонент | Версия | Лицензия | Ссылка |
|---|---|---|---|
| pytest | 9.x | MIT | https://pytest.org/ |
| pytest-django | 4.x | BSD-3-Clause | https://github.com/pytest-dev/pytest-django |

## Фронтенд

| Компонент | Версия | Лицензия | Ссылка | Где |
|---|---|---|---|---|
| Chart.js | 4.4.7 | MIT | https://www.chartjs.org/ | `portal/static/portal/vendor/chart.umd.min.js` (заголовок с лицензией сохранён в файле) |

## Инструменты развёртывания (не входят в репозиторий)

| Компонент | Лицензия | Ссылка | Где используется |
|---|---|---|---|
| Python (CPython) | PSF-2.0 | https://www.python.org/ | среда выполнения |
| uv | MIT или Apache-2.0 | https://github.com/astral-sh/uv | `start.bat` скачивает его, если Python не установлен |
| python-build-standalone | MPL-2.0 (сборка CPython) | https://github.com/astral-sh/python-build-standalone | портативный Python, который скачивает uv |
| Docker-образ python:3.12-slim | PSF-2.0 + лицензии пакетов Debian | https://hub.docker.com/_/python | `Dockerfile` |
| Docker-образ postgres:16-alpine | PostgreSQL License | https://hub.docker.com/_/postgres | `docker-compose.yml` |

## Совместимость с лицензией MIT

- Все компоненты, кроме перечисленных ниже, — разрешительные (MIT, BSD, Apache-2.0, PSF, ZPL-2.1): их можно использовать
  в проекте под MIT при сохранении уведомлений об авторских правах.
- **psycopg (LGPL-3.0)** — используется как отдельная библиотека, которая подключается динамически (`import psycopg`)
  и не изменяется. Такое использование LGPL допускает для проекта под любой лицензией; при распространении
  пользователь должен иметь возможность заменить библиотеку (это так: она ставится из PyPI отдельно).
- **certifi, python-build-standalone (MPL-2.0)** — файловый copyleft затрагивает только изменения самих файлов этих
  пакетов; файлы не изменяются.
- Данные бирж ETS и Каспий не являются частью кода и не лицензируются проектом — см. страницу портала `/legal/sources/`.
