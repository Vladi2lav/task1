"""Парсер биржи «Каспий» (ccx.kz).

Источник — внутренний JSON сайта `/api/internal/trading-results`, которым пользуется
сама страница биржи. Он отдаёт только последние 20 лотов по секции и режиму торгов
(регулируемые / спот), параметр `max_results` принимает только значение 20. Историю
сайт не отдаёт, поэтому недостающие дни накапливаются ежедневными запусками.

Участников и брокеров сделок биржа не публикует, поля покупателя/продавца остаются пустыми.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Iterator, List, Optional
from urllib.parse import urlencode, urljoin

from django.conf import settings

from .base import BaseScraper, ScraperError, TradeRecord
from .fingerprint import assign_occurrences
from .http import HttpClient
from .normalize import clean_text, normalize_unit, parse_date, parse_decimal

logger = logging.getLogger(__name__)

API_PATH = '/api/internal/trading-results'
# значение, при котором сайт отдаёт данные (при любом другом приходит пустой список)
MAX_RESULTS = 20


class CaspyScraper(BaseScraper):
    exchange_code = 'caspy'
    exchange_name = 'Каспий'

    def __init__(self, http: Optional[HttpClient] = None, config: Optional[dict] = None):
        config = config or settings.SCRAPER_CONFIG['sources']['caspy']
        self.base_url = config['base_url']
        self.website = self.base_url
        self.api_url = urljoin(self.base_url, API_PATH)
        self.sections = config.get('sections', ['CL', 'OIL', 'CM'])
        self.http = http or HttpClient()
        self.errors: List[str] = []

    def fetch(self, date_from: date, date_to: date) -> Iterator[TradeRecord]:
        self.errors = []
        records: List[TradeRecord] = []
        for section in self.sections:
            for regulated in (True, False):
                try:
                    items = self._load_section(section, regulated)
                except ScraperError as error:
                    self._report_error(f'{section} ({"регулируемые" if regulated else "спот"}): {error}')
                    continue
                for item in items:
                    record = self.parse_item(item)
                    if record and date_from <= record.trade_date <= date_to:
                        records.append(record)
        yield from assign_occurrences(records)

    def _report_error(self, message: str) -> None:
        logger.error('Каспий: %s', message)
        self.errors.append(message)

    def _load_section(self, section: str, regulated: bool) -> list:
        params = {
            'section_code': section,
            'instrument_regulated': 'true' if regulated else 'false',
            'max_results': MAX_RESULTS,
        }
        url = f'{self.api_url}?{urlencode(params)}'
        try:
            payload = json.loads(self.http.get_text(url))
        except ValueError as error:
            raise ScraperError(f'ответ не похож на JSON: {error}')
        if not payload.get('success'):
            raise ScraperError(f'сайт ответил ошибкой: {payload.get("message", "без пояснения")}')
        return payload.get('data') or []

    # --- разбор одной записи -----------------------------------------------

    def parse_item(self, item: dict) -> Optional[TradeRecord]:
        """Одна запись JSON -> TradeRecord; None, если нет даты или названия лота."""
        trade_date = parse_date(item.get('day_of_trade'))
        name = clean_text(item.get('lot_name'))
        if not trade_date or not name:
            self._report_error(f'пропущена запись без даты или названия: {item.get("lot_code")}')
            return None

        quantity, unit = self._split_volume(item.get('volume_deals'))
        avg_price = parse_decimal(item.get('avg_price_deals'))
        with_vat = 'без' not in clean_text(item.get('vat')).lower()

        return TradeRecord(
            exchange_code=self.exchange_code,
            trade_date=trade_date,
            product_name=name,
            product_code=clean_text(item.get('lot_code')),
            deals_count=item.get('deal_count'),
            quantity=quantity,
            unit=unit,
            price_avg=avg_price,
            price_with_vat=avg_price if with_vat else None,
            price_without_vat=None if with_vat else avg_price,
            amount=parse_decimal(item.get('sum_deals')),
            delivery_basis=clean_text(item.get('delivery_term')),
            delivery_place=clean_text(item.get('basis')),
            source_url=self.api_url,
        )

    @staticmethod
    def _split_volume(value) -> tuple:
        """«6 300 тонна» -> (6300, 'т')."""
        text = clean_text(value)
        if not text:
            return None, ''
        number, _, unit = text.rpartition(' ')
        quantity = parse_decimal(number)
        if quantity is None:  # единицы нет, только число
            return parse_decimal(text), ''
        return quantity, normalize_unit(unit)
