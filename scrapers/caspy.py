"""Парсер биржи «Каспий» (ccx.kz).

Основной источник — страница итогов торгов `/trading-and-analytics/trading-results`:
таблица сделок с покупателем, продавцом (БИН в конце названия), брокерами, ТН ВЭД, ценами
и суммой. Фильтр по датам (`date_from`, `date_to`, ГГГГ-ММ-ДД) и постраничный вывод
(`per_page` до 1000, `page`). История на сайте — с февраля 2025.
Период обходится по месяцам, внутри месяца — по страницам.

Запасной источник — внутренний JSON `/api/internal/trading-results` (последние до 100 лотов
по секции, без участников). Используется, только если страница итогов за весь период
пуста, а JSON что-то отдаёт: так было 14.10.2026, когда страница временно не работала.
"""
from __future__ import annotations

import calendar
import json
import logging
import re
from datetime import date, timedelta
from typing import Iterator, List, Optional
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup
from django.conf import settings

from .base import BaseScraper, ScraperError, TradeRecord
from .fingerprint import assign_occurrences
from .http import HttpClient
from .normalize import (
    clean_text, normalize_bin, normalize_company_name, normalize_unit, parse_date, parse_decimal,
)

logger = logging.getLogger(__name__)

API_PATH = '/api/internal/trading-results'
API_MAX_RESULTS = 100  # больше сайт не отдаёт (при 101 и выше — пустой список)
PAGE_SIZE = 1000       # максимум строк на странице итогов
TOTAL_ROW = 'итого по дате'

# колонка таблицы -> поле; ищем по началу текста заголовка, а не по номеру колонки
COLUMNS = {
    'дата': 'trade_date',
    'код тн вэд': 'tnved',
    'наименование товара': 'product_name',
    'цена открытия': 'price_open',
    'цена закрытия': 'price_close',
    'макс. цена': 'price_max',
    'мин. цена': 'price_min',
    'средневзвеш': 'price_avg',
    'сумма сделки': 'amount',
    'брокер покупателя': 'buyer_broker',
    'наименование покупателя': 'buyer',
    'брокер продавца': 'seller_broker',
    'наименование продавца': 'seller',
    'кол-во заключенных сделок': 'deals_count',
    'условие поставки': 'delivery_basis',
}
REQUIRED = {'trade_date', 'product_name', 'buyer', 'seller'}

_NAME_WITH_BIN = re.compile(r'^(?P<name>.*?)[\s,]+(?P<bin>\d{12})$')


class CaspyScraper(BaseScraper):
    exchange_code = 'caspy'
    exchange_name = 'Каспий'

    def __init__(self, http: Optional[HttpClient] = None, config: Optional[dict] = None):
        config = config or settings.SCRAPER_CONFIG['sources']['caspy']
        self.base_url = config['base_url']
        self.website = self.base_url
        self.results_url = urljoin(self.base_url, config.get('results_path', '/trading-and-analytics/trading-results'))
        self.api_url = urljoin(self.base_url, API_PATH)
        self.sections = config.get('sections', ['CL', 'OIL', 'CM', 'SG'])
        self.page_size = int(config.get('page_size', PAGE_SIZE))
        self.http = http or HttpClient()
        self.errors: List[str] = []

    def fetch(self, date_from: date, date_to: date) -> Iterator[TradeRecord]:
        self.errors = []
        records: List[TradeRecord] = []
        for month_start, month_end in month_windows(date_from, date_to):
            try:
                records.extend(self._load_period(month_start, month_end))
            except ScraperError as error:
                self._report_error(f'итоги за {month_start:%m.%Y}: {error}')

        if not records:
            backup = self._fetch_from_api(date_from, date_to)
            if backup:
                self._report_error('страница итогов торгов пуста, данные взяты из запасного API '
                                   '(последние лоты, без участников и брокеров)')
                records = backup
        yield from assign_occurrences(records)

    def _report_error(self, message: str) -> None:
        logger.error('Каспий: %s', message)
        self.errors.append(message)

    # --- основной источник: страница итогов --------------------------------

    def _load_period(self, date_from: date, date_to: date) -> List[TradeRecord]:
        """Все сделки за период: страницы по PAGE_SIZE строк, пока не придёт неполная."""
        records: List[TradeRecord] = []
        first_rows_seen = set()
        page = 1
        while True:
            url = self._results_page_url(date_from, date_to, page)
            rows = self.parse_results_page(self.http.get_text(url), url)
            if not rows:
                break
            # за последней страницей сайт снова отдаёт последнюю — не зацикливаемся
            marker = (rows[0].trade_date, rows[0].product_name, rows[0].amount, rows[0].buyer_name)
            if marker in first_rows_seen:
                break
            first_rows_seen.add(marker)
            records.extend(r for r in rows if date_from <= r.trade_date <= date_to)
            if len(rows) < self.page_size:
                break
            page += 1
        return records

    def _results_page_url(self, date_from: date, date_to: date, page: int) -> str:
        params = {
            'lang': 'ru',
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'per_page': self.page_size,
            'page': page,
        }
        return f'{self.results_url}?{urlencode(params)}'

    def parse_results_page(self, html: str, source_url: str = '') -> List[TradeRecord]:
        """Строки таблицы итогов -> TradeRecord; строки «ИТОГО по дате» пропускаются.

        Нет таблицы (пустая выдача «Нет данных для отображения») -> пустой список.
        """
        soup = BeautifulSoup(html, 'lxml')
        table = soup.find('table')
        if table is None:
            return []
        headers = [clean_text(th.get_text(' ')).lower() for th in table.select('thead th')]
        columns = self._map_columns(headers)
        missing = REQUIRED - set(columns.values())
        if missing:
            raise ScraperError(f'в таблице нет колонок {sorted(missing)} — изменилась вёрстка?')

        records = []
        for tr in table.select('tbody tr'):
            cells = tr.find_all('td')
            if len(cells) < len(headers):
                continue
            values = {field: _cell_text(cells[index]) for index, field in columns.items()}
            if values.get('product_name', '').lower() == TOTAL_ROW:
                continue
            record = self._make_record(values, source_url)
            if record:
                records.append(record)
        return records

    @staticmethod
    def _map_columns(headers: List[str]) -> dict:
        columns = {}
        for index, header in enumerate(headers):
            for prefix, field in COLUMNS.items():
                if header.startswith(prefix) and field not in columns.values():
                    columns[index] = field
                    break
        return columns

    def _make_record(self, values: dict, source_url: str) -> Optional[TradeRecord]:
        trade_date = parse_date(values.get('trade_date'))
        product = clean_text(values.get('product_name'))
        if not trade_date or not product:
            self._report_error(f'пропущена строка без даты или товара: {values}')
            return None
        buyer_name, buyer_bin = split_name_and_bin(values.get('buyer'))
        seller_name, seller_bin = split_name_and_bin(values.get('seller'))
        deals = parse_decimal(values.get('deals_count'))
        return TradeRecord(
            exchange_code=self.exchange_code,
            trade_date=trade_date,
            product_name=product,
            tnved=_dash_to_empty(values.get('tnved')),
            buyer_name=buyer_name,
            buyer_bin=buyer_bin,
            seller_name=seller_name,
            seller_bin=seller_bin,
            buyer_broker=normalize_company_name(_dash_to_empty(values.get('buyer_broker'))),
            seller_broker=normalize_company_name(_dash_to_empty(values.get('seller_broker'))),
            deals_count=int(deals) if deals is not None else None,
            price_open=parse_decimal(values.get('price_open')),
            price_close=parse_decimal(values.get('price_close')),
            price_min=parse_decimal(values.get('price_min')),
            price_max=parse_decimal(values.get('price_max')),
            price_avg=parse_decimal(values.get('price_avg')),
            amount=parse_decimal(values.get('amount')),
            delivery_basis=_dash_to_empty(values.get('delivery_basis')),
            source_url=source_url,
        )

    # --- запасной источник: внутренний JSON --------------------------------

    def _fetch_from_api(self, date_from: date, date_to: date) -> List[TradeRecord]:
        records: List[TradeRecord] = []
        for section in self.sections:
            for regulated in (True, False):
                try:
                    items = self._load_api_section(section, regulated)
                except ScraperError as error:
                    logger.warning('Каспий, запасной API %s: %s', section, error)
                    continue
                for item in items:
                    record = self.parse_api_item(item)
                    if record and date_from <= record.trade_date <= date_to:
                        records.append(record)
        return records

    def _load_api_section(self, section: str, regulated: bool) -> list:
        params = {
            'section_code': section,
            'instrument_regulated': 'true' if regulated else 'false',
            'max_results': API_MAX_RESULTS,
        }
        url = f'{self.api_url}?{urlencode(params)}'
        try:
            payload = json.loads(self.http.get_text(url))
        except ValueError as error:
            raise ScraperError(f'ответ не похож на JSON: {error}')
        if not payload.get('success'):
            raise ScraperError(f'сайт ответил ошибкой: {payload.get("message", "без пояснения")}')
        return payload.get('data') or []

    def parse_api_item(self, item: dict) -> Optional[TradeRecord]:
        """Одна запись JSON -> TradeRecord (итог по лоту за день, без участников)."""
        trade_date = parse_date(item.get('day_of_trade'))
        name = clean_text(item.get('lot_name'))
        if not trade_date or not name:
            return None
        quantity, unit = split_volume(item.get('volume_deals'))
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


# --- вспомогательные функции ---------------------------------------------

def month_windows(date_from: date, date_to: date):
    """Разбить период на отрезки по календарным месяцам."""
    start = date_from
    while start <= date_to:
        month_end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        end = min(month_end, date_to)
        yield start, end
        start = end + timedelta(days=1)


def split_name_and_bin(value) -> tuple:
    """«ТОО Бастау 990740000613» -> ('ТОО Бастау', '990740000613')."""
    text = _dash_to_empty(value)
    match = _NAME_WITH_BIN.match(text)
    if match:
        return normalize_company_name(match.group('name')), normalize_bin(match.group('bin'))
    return normalize_company_name(text), ''


def split_volume(value) -> tuple:
    """«6 300 тонна» -> (6300, 'т')."""
    text = clean_text(value)
    if not text:
        return None, ''
    number, _, unit = text.rpartition(' ')
    quantity = parse_decimal(number)
    if quantity is None:
        return parse_decimal(text), ''
    return quantity, normalize_unit(unit)


def _cell_text(cell) -> str:
    """Полный текст ячейки: длинные названия на сайте обрезаны «…», полный — в title/data-full-text."""
    full = cell.find(attrs={'data-full-text': True})
    if full:
        return clean_text(full['data-full-text'])
    titled = cell.find(attrs={'title': True})
    if titled:
        return clean_text(titled['title'])
    return clean_text(cell.get_text(' '))


def _dash_to_empty(value) -> str:
    text = clean_text(value)
    return '' if text in ('-', '—') else text
