"""Общий интерфейс парсеров и единый формат записи."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import Iterable, Optional


@dataclass
class TradeRecord:
    """Одна строка итогов торгов в едином формате (на выходе любого парсера)."""

    exchange_code: str            # 'ets' | 'caspy'
    trade_date: date
    product_name: str
    trade_time: Optional[time] = None
    product_code: str = ''
    tnved: str = ''
    buyer_name: str = ''
    buyer_bin: str = ''
    seller_name: str = ''
    seller_bin: str = ''
    buyer_broker: str = ''
    seller_broker: str = ''
    deals_count: Optional[int] = None
    quantity: Optional[Decimal] = None
    unit: str = ''
    price_open: Optional[Decimal] = None
    price_close: Optional[Decimal] = None
    price_min: Optional[Decimal] = None
    price_max: Optional[Decimal] = None
    price_avg: Optional[Decimal] = None
    price_with_vat: Optional[Decimal] = None
    price_without_vat: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    delivery_basis: str = ''
    delivery_place: str = ''
    delivery_term: str = ''
    source_url: str = ''
    # порядковый номер одинаковой строки внутри одного источника (чтобы совпавшие
    # по значениям строки не слипались при расчёте отпечатка)
    occurrence: int = 0


class ScraperError(Exception):
    """Ошибка источника (недоступен, изменилась структура)."""


class BaseScraper:
    exchange_code: str = ''
    exchange_name: str = ''
    website: str = ''

    def fetch(self, date_from: date, date_to: date) -> Iterable[TradeRecord]:
        """Вернуть записи за период [date_from, date_to].

        Генератор: записи отдаются по мере разбора. Ошибки одного файла/страницы
        не должны останавливать остальные — их логируют и собирают в self.errors.
        """
        raise NotImplementedError

    errors: list
