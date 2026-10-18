"""Проверка работоспособности для Docker HEALTHCHECK, мониторинга и балансировщиков: GET /healthz."""
import logging

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

logger = logging.getLogger(__name__)


@never_cache
def healthz(request):
    """200 {"status": "ok"} — приложение запущено и база данных отвечает; иначе 503."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:  # любая ошибка БД — сервис неработоспособен
        logger.exception('Проверка /healthz: база данных недоступна')
        return JsonResponse({'status': 'error', 'database': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok', 'database': 'ok'})
