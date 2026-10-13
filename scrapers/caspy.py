"""Парсер биржи «Каспий» (ccx.kz) — заглушка до получения образца данных."""
from __future__ import annotations

from datetime import date
from typing import Iterator, List

from .base import BaseScraper, TradeRecord


class CaspyScraper(BaseScraper):
    exchange_code = 'caspy'
    exchange_name = 'Каспий'
    website = 'https://ccx.kz'

    def __init__(self):
        self.errors: List[str] = []

    def fetch(self, date_from: date, date_to: date) -> Iterator[TradeRecord]:
        raise NotImplementedError(
            'Парсер Каспия ещё не написан: страница итогов торгов ccx.kz из тестовой среды '
            'отдаёт пустую таблицу, нужен образец реальных данных (см. docs/SOURCES.md).'
        )
