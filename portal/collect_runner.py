"""Ручной запуск сбора данных из портала в фоновом потоке.

Страница не должна «висеть», пока парсер ходит по сайтам бирж, поэтому сбор
запускается в отдельном потоке. Состояние запуска хранится в памяти процесса
под блокировкой; повторный запуск, пока предыдущий не завершён, отклоняется.

Ограничение: защита работает в пределах одного процесса сервера. От запуска,
идущего в другом процессе (планировщик, management-команда), дополнительно
защищает проверка журнала ParseRun (см. ``есть_активный_запуск_в_журнале``).
"""
import logging
import threading
from datetime import timedelta
from typing import Callable, Sequence

from django.db import connection
from django.utils import timezone

from exchanges.models import ParseRun

from .serializers import дата_время_для_показа

logger = logging.getLogger(__name__)

# Запись ParseRun в статусе «Выполняется» старше этого срока считается «зависшей»
# (процесс упал) и не блокирует новый запуск.
ЗАВИСШИЙ_ЗАПУСК = timedelta(minutes=30)

_блокировка = threading.Lock()
_состояние = {
    'running': False,
    'started_at': None,
    'finished_at': None,
    'exchanges': [],
    'date_from': None,
    'date_to': None,
    'error': '',
}
_поток = None


def получить_состояние() -> dict:
    """Копия текущего состояния ручного запуска (для /api/status/)."""
    with _блокировка:
        состояние = dict(_состояние)
        состояние['exchanges'] = list(состояние['exchanges'])
    for ключ in ('started_at', 'finished_at'):
        значение = состояние[ключ]
        состояние[ключ] = timezone.localtime(значение).isoformat() if значение else None
        состояние[ключ + '_display'] = дата_время_для_показа(значение)
    состояние['date_from'] = состояние['date_from'].isoformat() if состояние['date_from'] else None
    состояние['date_to'] = состояние['date_to'].isoformat() if состояние['date_to'] else None
    return состояние


def есть_активный_запуск_в_журнале() -> bool:
    """Есть ли свежая запись журнала в статусе «Выполняется» (запуск из другого процесса)."""
    граница = timezone.now() - ЗАВИСШИЙ_ЗАПУСК
    return ParseRun.objects.filter(status=ParseRun.Status.RUNNING, started_at__gte=граница).exists()


def запущен() -> bool:
    """Идёт ли ручной запуск из этого процесса."""
    with _блокировка:
        return _состояние['running']


def _выполнить(функция_сбора: Callable, коды, дата_с, дата_по):
    ошибка = ''
    try:
        функция_сбора(коды, дата_с, дата_по, trigger='manual')
    except Exception as exc:  # поток не должен падать молча
        logger.exception('Ручной запуск сбора завершился ошибкой')
        ошибка = f'{type(exc).__name__}: {exc}'[:500]
    finally:
        # Соединение с БД принадлежит потоку — закрываем, чтобы не оставлять «висящих»
        try:
            connection.close()
        except Exception:  # pragma: no cover
            pass
        with _блокировка:
            _состояние['running'] = False
            _состояние['finished_at'] = timezone.now()
            _состояние['error'] = ошибка


def запустить(функция_сбора: Callable, коды: Sequence[str], дата_с, дата_по) -> bool:
    """Запускает сбор в потоке. Возвращает False, если предыдущий запуск ещё идёт."""
    global _поток
    with _блокировка:
        if _состояние['running']:
            return False
        _состояние.update(
            running=True,
            started_at=timezone.now(),
            finished_at=None,
            exchanges=list(коды),
            date_from=дата_с,
            date_to=дата_по,
            error='',
        )
        _поток = threading.Thread(
            target=_выполнить,
            args=(функция_сбора, list(коды), дата_с, дата_по),
            name='portal-collect',
            daemon=True,
        )
        _поток.start()
    return True


def дождаться_завершения(таймаут: float = 5.0) -> None:
    """Ждёт завершения потока (используется в тестах)."""
    поток = _поток
    if поток is not None:
        поток.join(таймаут)


def сбросить_состояние() -> None:
    """Возвращает состояние в исходное (используется в тестах)."""
    global _поток
    with _блокировка:
        _состояние.update(
            running=False, started_at=None, finished_at=None, exchanges=[],
            date_from=None, date_to=None, error='',
        )
        _поток = None
