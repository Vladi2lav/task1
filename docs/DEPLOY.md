# Развёртывание

Три способа запуска одного и того же кода:

| | Windows (start.bat) | Docker | Vercel |
|---|---|---|---|
| Для чего | показать проект на любом компьютере | постоянная работа на сервере | публичное демо по ссылке |
| Что нужно | Windows 10/11, интернет при первом запуске | Docker (Desktop или Engine + Compose v2) | аккаунт Vercel |
| База данных | SQLite `db.sqlite3` | PostgreSQL 16 (том `pgdata`) | снимок SQLite, только чтение |
| Веб-сервер | waitress | gunicorn | Vercel Python Runtime |
| Ежедневный сбор | да (свёрнутое окно) | да (сервис `scheduler`) | нет |
| Ручной запуск сбора | да | да | нет (режим только для чтения) |

Все настройки задаются переменными окружения или файлом `.env` (образец с пояснениями — [`.env.example`](../.env.example)),
параметры сбора — в [`scraper.toml`](../scraper.toml). Таблица переменных — в [README](../README.md#настройка).

---

## 1. Windows: start.bat

1. Скачайте проект: на GitHub **Code → Download ZIP** и **распакуйте** архив (из архива без распаковки bat не запустится).
2. Дважды щёлкните `start.bat`. Если Windows покажет «Windows защитила ваш компьютер» — «Подробнее» → «Выполнить в любом случае».
3. Через 1–3 минуты (первый раз) откроется браузер с порталом http://127.0.0.1:8000/.

Что делает `start.bat`:

1. Ищет Python 3.11–3.13 (`py -3.13/-3.12/-3.11`, затем `python`). Если не нашёл — скачивает
   [uv](https://docs.astral.sh/uv/) фиксированной версии в папку `.tools` (официальный установщик
   `https://astral.sh/uv/0.9.17/install.ps1`, PATH пользователя не меняется), и uv скачивает портативный Python 3.12
   туда же. В систему ничего не устанавливается: удалите папку проекта — и следов не останется.
2. Создаёт виртуальное окружение `.venv` и ставит зависимости. При повторных запусках ничего не переустанавливается,
   пока не изменился `requirements.txt` (маркер `.venv\.requirements.sha256`).
3. При первом запуске создаёт `.env` со случайным `DJANGO_SECRET_KEY`.
4. `manage.py bootstrap` — миграции, статика, загрузка готовых данных из `docs/db/` (если база пуста).
5. Запускает планировщик в свёрнутом окне «Итоги торгов - планировщик» и веб-сервер waitress в текущем окне,
   открывает браузер.

Остальные файлы:

| Файл | Что делает |
|---|---|
| `stop.bat` | останавливает веб-сервер и планировщик (по PID-файлам в `.run\`) |
| `update_data.bat` | ручной сбор итогов торгов и новостей за 7 дней; `update_data.bat --days 92` — за 3 месяца |
| `run_tests.bat` | автотесты (доустанавливает pytest из `requirements-dev.txt`) |

Параметры `start.bat`: `--no-browser`, `--no-pause` (для автоматического запуска), `--collect` (при первом запуске
сразу собрать свежие данные с сайтов бирж), `--uv` (портативный Python, даже если установлен свой).

Доступ из локальной сети: в `.env` укажите `WEB_HOST=0.0.0.0` и добавьте IP компьютера в `DJANGO_ALLOWED_HOSTS`
(например `localhost,127.0.0.1,192.168.1.10`), разрешите порт 8000 в брандмауэре.

Логи: `logs\app.log` (портал), `logs\scheduler.log` (планировщик), `logs\collect.log` (update_data.bat).

Если что-то пошло не так: удалите папки `.venv` и `.tools` и запустите `start.bat` снова. Порт 8000 занят —
укажите другой `WEB_PORT` в `.env`.

Автозапуск при входе в Windows: ярлык на `start.bat --no-browser` в папке `shell:startup`.

---

## 2. Docker (полное развёртывание)

Состав (`docker-compose.yml`):

- **db** — PostgreSQL 16 (`postgres:16-alpine`), данные в томе `pgdata`, проверка готовности `pg_isready`;
- **web** — портал (gunicorn, 3 процесса), порт `WEB_PORT` (8000); при старте ждёт БД и выполняет
  `manage.py bootstrap` (миграции, данные из дампа при пустой базе, администратор из `DJANGO_SUPERUSER_*`);
  проверка `/healthz`;
- **scheduler** — планировщик ежедневного сбора (тот же образ), стартует, когда web готов.

Все сервисы — `restart: unless-stopped`, процессы в контейнере — от непривилегированного пользователя `app`.

### Запуск

```bash
cp .env.example .env        # по желанию: поменяйте POSTGRES_PASSWORD, DJANGO_ALLOWED_HOSTS и т.д.
docker compose up -d --build
```

Портал: http://localhost:8000. Без `.env` используются значения по умолчанию; если `DJANGO_SECRET_KEY` не задан,
ключ создаётся при первом запуске и хранится в томе `appdata`.

### Обслуживание

```bash
docker compose ps                         # состояние (web должен быть healthy)
docker compose logs -f web scheduler      # логи
docker compose exec web python manage.py collect_trades --exchange all --days 92   # ручной сбор торгов
docker compose exec web python manage.py collect_news --exchange all --days 92     # ручной сбор новостей
docker compose exec web python manage.py createsuperuser                          # администратор
docker compose restart web scheduler      # применить изменения scraper.toml (файл подключён в контейнеры)
```

Обновление до новой версии кода:

```bash
git pull
docker compose up -d --build              # миграции применятся автоматически при старте web
```

Резервная копия и восстановление БД:

```bash
docker compose exec -T db pg_dump -U trades -d trades -Fc > backup_$(date +%F).dump
docker compose exec -T db pg_restore -U trades -d trades --clean --if-exists < backup_2026-10-18.dump
```

(в PowerShell вместо `$(date +%F)` укажите дату вручную). Остановка: `docker compose down`; удалить вместе с данными:
`docker compose down -v`.

### Публикация в интернете

Поставьте перед контейнером обратный прокси с HTTPS (nginx, Caddy, Traefik) и задайте в `.env`:

```
DJANGO_ALLOWED_HOSTS=trades.example.kz
DJANGO_CSRF_TRUSTED_ORIGINS=https://trades.example.kz
DJANGO_HTTPS=1
```

`DJANGO_HTTPS=1` включает защищённые cookie, HSTS и доверие к заголовку `X-Forwarded-Proto`. Для публичного демо
без ручного запуска сбора — `PORTAL_READ_ONLY=1`.

---

## 3. Vercel (демо только для чтения)

На Vercel нет постоянного процесса и записываемого диска (кроме `/tmp`), поэтому там работает демо-режим:

- точка входа — `api/index.py` (WSGI-приложение `app`), все адреса перенаправляются на неё (`vercel.json`);
- Vercel выставляет `VERCEL=1` — настройки включают `DEBUG=False`, `PORTAL_READ_ONLY=True`, разрешают хосты
  `*.vercel.app`, доверяют HTTPS-заголовку прокси, отключают файловый лог;
- база — снимок `deploy/vercel_demo.sqlite3` (итоги торгов и новости из `docs/db/`), при старте функции копируется
  в `/tmp`; сбор данных и планировщик не работают;
- **персональные данные обезличены**: серверы Vercel находятся за пределами РК, а базы с персональными данными
  граждан РК должны храниться в РК (Закон РК № 94-V, ст. 12 п. 2). Скрипт `scripts/build_vercel_db.py` заменяет
  индивидуальных предпринимателей и крестьянские хозяйства (по названию «ИП …», «КХ …» и т.п. или по ИИН вместо БИН)
  на «ИП (данные скрыты) №N» без ИИН (`setup_tools/anonymize.py`); компании (ТОО, АО) не меняются.
  Полная версия с настоящими данными (start.bat, Docker) предназначена для размещения на серверах в РК;
- статика отдаётся WhiteNoise прямо из папок приложений (collectstatic не нужен);
- лишнее не загружается (`.vercelignore`: тесты, `.venv`, документы, дампы).

### Обновить снимок данных

```bash
python scripts/make_dump.py         # docs/db/trades_fixture.json, news_fixture.json, trades_dump.sql из db.sqlite3
python scripts/build_vercel_db.py   # deploy/vercel_demo.sqlite3 (с обезличиванием ИП)
git add docs/db deploy/vercel_demo.sqlite3 && git commit -m "Обновлён снимок демо"
```

### Способ А: из командной строки

Нужен Node.js (для `npx`).

```bash
npx vercel login          # вход в аккаунт Vercel (один раз)
npx vercel                # первый раз: создать проект (Framework Preset: Other), получить preview-адрес
npx vercel --prod         # опубликовать на основной адрес https://<проект>.vercel.app
```

### Способ Б: импорт GitHub-репозитория

1. https://vercel.com/new → **Import Git Repository** → выбрать репозиторий.
2. Framework Preset: **Other**; Build Command, Output Directory — пустые (не переопределять).
3. Environment Variables (по желанию): `DJANGO_SECRET_KEY` — случайная строка (без неё ключ генерируется на каждый
   запуск функции, это допустимо для демо); `SITE_OWNER`, `SITE_CONTACT_EMAIL`, `LEGAL_EDITION_DATE` — для юридических
   страниц (`SITE_HOSTING_PROVIDER` на Vercel по умолчанию «Vercel Inc. (США)»).
4. **Deploy**. Каждый push в основную ветку публикуется автоматически.

Проверка после публикации: `https://<проект>.vercel.app/healthz` → `{"status": "ok", ...}`; на `/status/` нет кнопки
ручного сбора.

Проверка локально (без Vercel):

```bash
VERCEL=1 python -c "from api.index import app; from django.test import Client; print(Client().get('/', HTTP_HOST='demo.vercel.app').status_code)"
```

---

## Проверка работоспособности

`GET /healthz` — `200 {"status": "ok", "database": "ok"}`, если приложение запущено и БД отвечает; `503` — если БД
недоступна. Используется в `HEALTHCHECK` Docker; подходит для мониторинга (UptimeRobot и т.п.).

`python manage.py check --deploy` при `DJANGO_DEBUG=0` проходит без замечаний. Проверки, требующие HTTPS
(HSTS, secure-cookie, редирект), отключены, пока не задан `DJANGO_HTTPS=1`, — локальный запуск и Docker в локальной
сети работают по HTTP.
