"""Ручной запуск сбора данных из портала в фоновом потоке.

Страница не должна «висеть», пока парсер ходит по сайтам бирж, поэтому сбор
запускается в отдельном потоке. Состояние запуска хранится в памяти процесса
под блокировкой; повторный запуск, пока предыдущий не завершён, отклоняется.

Ограничение: защита работает в пределах одного процесса сервера. От запуска,
идущего в другом процессе (планировщик, management-команда), дополнительно
защищает проверка журнала ParseRun (см. ``has_active_run_in_log``).
"""
import logging
import threading
from datetime import timedelta
from typing import Callable, Sequence

from django.db import connection
from django.utils import timezone

from exchanges.models import ParseRun

from .serializers import format_datetime

logger = logging.getLogger(__name__)

# Запись ParseRun в статусе «Выполняется» старше этого срока считается «зависшей»
# (процесс упал) и не блокирует новый запуск.
STALE_RUN_AGE = timedelta(minutes=30)

_lock = threading.Lock()
_state = {
    'running': False,
    'started_at': None,
    'finished_at': None,
    'exchanges': [],
    'date_from': None,
    'date_to': None,
    'error': '',
}
_thread = None


def get_state() -> dict:
    """Копия текущего состояния ручного запуска (для /api/status/)."""
    with _lock:
        state = dict(_state)
        state['exchanges'] = list(state['exchanges'])
    for key in ('started_at', 'finished_at'):
        value = state[key]
        state[key] = timezone.localtime(value).isoformat() if value else None
        state[key + '_display'] = format_datetime(value)
    state['date_from'] = state['date_from'].isoformat() if state['date_from'] else None
    state['date_to'] = state['date_to'].isoformat() if state['date_to'] else None
    return state


def has_active_run_in_log() -> bool:
    """Есть ли свежая запись журнала в статусе «Выполняется» (запуск из другого процесса)."""
    since = timezone.now() - STALE_RUN_AGE
    return ParseRun.objects.filter(status=ParseRun.Status.RUNNING, started_at__gte=since).exists()


def is_running() -> bool:
    """Идёт ли ручной запуск из этого процесса."""
    with _lock:
        return _state['running']


def _run(collect_func: Callable, codes, date_from, date_to):
    error = ''
    try:
        collect_func(codes, date_from, date_to, trigger='manual')
    except Exception as exc:  # поток не должен падать молча
        logger.exception('Ручной запуск сбора завершился ошибкой')
        error = f'{type(exc).__name__}: {exc}'[:500]
    finally:
        # Соединение с БД принадлежит потоку — закрываем, чтобы не оставлять «висящих»
        try:
            connection.close()
        except Exception:  # pragma: no cover
            pass
        with _lock:
            _state['running'] = False
            _state['finished_at'] = timezone.now()
            _state['error'] = error


def start(collect_func: Callable, codes: Sequence[str], date_from, date_to) -> bool:
    """Запускает сбор в потоке. Возвращает False, если предыдущий запуск ещё идёт."""
    global _thread
    with _lock:
        if _state['running']:
            return False
        _state.update(
            running=True,
            started_at=timezone.now(),
            finished_at=None,
            exchanges=list(codes),
            date_from=date_from,
            date_to=date_to,
            error='',
        )
        _thread = threading.Thread(
            target=_run,
            args=(collect_func, list(codes), date_from, date_to),
            name='portal-collect',
            daemon=True,
        )
        _thread.start()
    return True


def wait_finished(timeout: float = 5.0) -> None:
    """Ждёт завершения потока (используется в тестах)."""
    thread = _thread
    if thread is not None:
        thread.join(timeout)


def reset_state() -> None:
    """Возвращает состояние в исходное (используется в тестах)."""
    global _thread
    with _lock:
        _state.update(
            running=False, started_at=None, finished_at=None, exchanges=[],
            date_from=None, date_to=None, error='',
        )
        _thread = None
