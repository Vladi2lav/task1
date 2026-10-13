"""Сбор данных по списку бирж за период — общая точка входа для команд, планировщика и портала."""
from __future__ import annotations

import logging
import sys
from datetime import date
from typing import Iterable, List

from exchanges.models import ParseRun

from .registry import SCRAPERS
from .runner import run_scraper

logger = logging.getLogger(__name__)


def collect(exchange_codes: Iterable[str], date_from: date, date_to: date, trigger: str = 'manual') -> List[ParseRun]:
    """Запустить сбор по каждой бирже по очереди; сбой одной биржи не останавливает остальные.

    Неизвестный код биржи — ValueError (до начала сбора).
    """
    codes = list(exchange_codes)
    unknown = [code for code in codes if code not in SCRAPERS]
    if unknown:
        raise ValueError(f'Неизвестные биржи: {", ".join(unknown)}. Доступны: {", ".join(SCRAPERS)}')

    runs = []
    for code in codes:
        try:
            runs.append(run_scraper(SCRAPERS[code](), date_from, date_to, trigger))
        except Exception:  # запасная защита: run_scraper сам ошибки источника не пробрасывает
            logger.exception('Сбор по бирже %s завершился аварийно', code)
    return runs


def setup_console_logging() -> None:
    """Вывод логов в консоль в UTF-8 (в Windows-консоли иначе падает на казахских буквах)."""
    if logging.getLogger().handlers:
        return
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s', datefmt='%H:%M:%S')
