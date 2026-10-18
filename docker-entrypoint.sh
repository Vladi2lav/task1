#!/bin/sh
# Точка входа контейнера.
#   web        — дождаться БД, manage.py bootstrap (миграции, данные из дампа, админ), затем gunicorn
#   scheduler  — дождаться БД, затем планировщик ежедневного сбора
#   любая другая команда выполняется как есть: docker compose run --rm web python manage.py shell
set -e

# Секретный ключ: если не задан в .env — создаётся один раз и хранится в томе /app/data
if [ -z "$DJANGO_SECRET_KEY" ]; then
    KEY_FILE="${DJANGO_SECRET_KEY_FILE:-/app/data/secret_key}"
    if [ ! -s "$KEY_FILE" ]; then
        python -c "import secrets; print(secrets.token_urlsafe(50))" > "$KEY_FILE"
        echo "Создан секретный ключ: $KEY_FILE"
    fi
    export DJANGO_SECRET_KEY_FILE="$KEY_FILE"
fi

wait_for_db() {
    python - <<'PY'
import os, sys, time
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.db import connection
for attempt in range(60):
    try:
        connection.ensure_connection()
        print('База данных доступна')
        sys.exit(0)
    except Exception as exc:
        if attempt == 0:
            print(f'Ожидание базы данных: {exc}')
        time.sleep(2)
print('База данных недоступна 2 минуты — проверьте сервис db и DATABASE_URL', file=sys.stderr)
sys.exit(1)
PY
}

case "$1" in
    web)
        wait_for_db
        python manage.py bootstrap --skip-static
        exec gunicorn config.wsgi:application \
            --bind "0.0.0.0:${PORT:-8000}" \
            --workers "${WEB_CONCURRENCY:-3}" \
            --timeout "${GUNICORN_TIMEOUT:-120}" \
            --access-logfile - \
            --forwarded-allow-ips "*"
        ;;
    scheduler)
        wait_for_db
        exec python manage.py run_scheduler
        ;;
    *)
        exec "$@"
        ;;
esac
