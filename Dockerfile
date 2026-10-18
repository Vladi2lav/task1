# Образ портала «Итоги торгов бирж ETS и Каспий».
# Один образ для веб-сервера (gunicorn) и планировщика (run_scheduler) — см. docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_DEBUG=0 \
    DJANGO_LOG_FILE=off \
    PORT=8000

WORKDIR /app

# непривилегированный пользователь
RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .

# статика собирается при сборке образа (ключ нужен только для запуска команды)
RUN DJANGO_SECRET_KEY=build-only-not-secret python manage.py collectstatic --noinput -v 0 \
    && sed -i 's/\r$//' docker-entrypoint.sh \
    && chmod +x docker-entrypoint.sh \
    && mkdir -p /app/data \
    && chown -R app:app /app/data /app/staticfiles

USER app
EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8000'), timeout=4)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["web"]
