"""Тесты парсера Каспия: сеть не используется, страницы подменяются образцами из tests/fixtures."""
import json
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

import pytest

from exchanges.models import ParseRun, Trade
from scrapers.caspy import CaspyScraper, month_windows, split_name_and_bin
from scrapers.runner import run_scraper

from .conftest import FakeHttp, fixture_text

RESULTS = 'https://ccx.kz/trading-and-analytics/trading-results'
API = 'https://ccx.kz/api/internal/trading-results'
CONFIG = {'base_url': 'https://ccx.kz', 'sections': ['CL'], 'page_size': 1000}
DAY = date(2026, 10, 16)
PAGE_HTML = fixture_text('caspy_results_2026-10-16.html')
EMPTY_HTML = '<html><body><div class="last_trades_tabs--sectional-list"><div class="empty-state">Нет данных</div></div></body></html>'
API_SAMPLE = json.loads(fixture_text('caspy_trading_results_CL.json'))


def results_url(date_from, date_to, page=1, per_page=1000):
    params = {'lang': 'ru', 'date_from': date_from.isoformat(), 'date_to': date_to.isoformat(),
              'per_page': per_page, 'page': page}
    return f'{RESULTS}?{urlencode(params)}'


def api_url(regulated: bool) -> str:
    return f'{API}?section_code=CL&instrument_regulated={"true" if regulated else "false"}&max_results=100'


def api_payload(regulated: bool) -> str:
    flag = 'true' if regulated else 'false'
    rows = [{k: v for k, v in row.items() if k != '_reg'} for row in API_SAMPLE if row['_reg'] == flag]
    return json.dumps({'success': True, 'message': 'ok', 'data': rows})


@pytest.fixture
def caspy_site():
    return FakeHttp({results_url(DAY, DAY): PAGE_HTML})


# --- страница итогов -----------------------------------------------------

def test_page_rows_are_parsed_and_total_row_skipped():
    records = CaspyScraper(http=FakeHttp({}), config=CONFIG).parse_results_page(PAGE_HTML, 'src')
    assert len(records) == 4  # 5 строк таблицы минус «ИТОГО по дате»
    assert all(r.product_name.lower() != 'итого по дате' for r in records)
    first = records[0]
    assert first.exchange_code == 'caspy'
    assert first.trade_date == DAY
    assert first.product_name == 'Цемент марки ЦЕМ I'
    assert first.tnved == '2523'
    assert first.buyer_bin == '990740000613' and 'Бастау' in first.buyer_name
    assert first.buyer_broker and first.seller_broker
    assert first.price_avg == Decimal('51000.00')
    assert first.amount == Decimal('10557000.00')
    assert first.deals_count == 1
    assert first.delivery_basis == 'CPT'
    assert first.source_url == 'src'


def test_full_name_is_taken_from_title_not_truncated_text():
    records = CaspyScraper(http=FakeHttp({}), config=CONFIG).parse_results_page(PAGE_HTML)
    seller = records[0]
    assert '…' not in seller.seller_name
    assert seller.seller_bin == '021240000022'
    assert seller.seller_name.endswith('Семей')


def test_empty_page_gives_no_rows():
    assert CaspyScraper(http=FakeHttp({}), config=CONFIG).parse_results_page(EMPTY_HTML) == []


def test_changed_layout_is_reported():
    html = '<table><thead><tr><th>Что-то</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>'
    from scrapers.base import ScraperError
    with pytest.raises(ScraperError, match='вёрстка'):
        CaspyScraper(http=FakeHttp({}), config=CONFIG).parse_results_page(html)


def test_fetch_uses_results_page(caspy_site):
    scraper = CaspyScraper(http=caspy_site, config=CONFIG)
    records = list(scraper.fetch(DAY, DAY))
    assert len(records) == 4 and not scraper.errors
    assert caspy_site.requested == [results_url(DAY, DAY)]


def test_pages_are_followed_until_short_page():
    config = {**CONFIG, 'page_size': 2}
    http = FakeHttp({
        results_url(DAY, DAY, 1, 2): PAGE_HTML,          # 4 сделки >= 2 — есть следующая
        results_url(DAY, DAY, 2, 2): EMPTY_HTML,
    })
    records = list(CaspyScraper(http=http, config=config).fetch(DAY, DAY))
    assert len(records) == 4
    assert len(http.requested) == 2


def test_repeated_last_page_does_not_loop():
    config = {**CONFIG, 'page_size': 2}
    http = FakeHttp({results_url(DAY, DAY, page, 2): PAGE_HTML for page in (1, 2, 3)})
    records = list(CaspyScraper(http=http, config=config).fetch(DAY, DAY))
    assert len(records) == 4  # вторая страница совпала с первой — остановились
    assert len(http.requested) == 2


def test_month_windows():
    assert list(month_windows(date(2026, 1, 20), date(2026, 3, 5))) == [
        (date(2026, 1, 20), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 5)),
    ]


def test_split_name_and_bin():
    assert split_name_and_bin('ТОО Бастау 990740000613') == ('ТОО Бастау', '990740000613')
    assert split_name_and_bin('ТОО Без БИН') == ('ТОО Без БИН', '')
    assert split_name_and_bin('-') == ('', '')


# --- запасной API --------------------------------------------------------

def test_api_fallback_when_results_page_is_empty():
    period = (date(2025, 1, 1), date(2025, 1, 31))
    http = FakeHttp({
        results_url(*period): EMPTY_HTML,
        api_url(True): api_payload(True),
        api_url(False): api_payload(False),
    })
    scraper = CaspyScraper(http=http, config=CONFIG)
    assert list(scraper.fetch(*period)) == []  # в образце API нет января 2025

    period = (date(2025, 12, 1), date(2026, 10, 31))
    pages = {results_url(start, end): EMPTY_HTML for start, end in month_windows(*period)}
    http = FakeHttp({**pages, api_url(True): api_payload(True), api_url(False): api_payload(False)})
    scraper = CaspyScraper(http=http, config=CONFIG)
    records = list(scraper.fetch(*period))
    assert len(records) == len(API_SAMPLE)
    assert any('запасного API' in e for e in scraper.errors)


def test_api_item_maps_fields():
    scraper = CaspyScraper(http=FakeHttp({}), config=CONFIG)
    record = scraper.parse_api_item({k: v for k, v in API_SAMPLE[0].items() if k != '_reg'})
    assert record.product_code == 'CL17FCQMB'
    assert record.quantity == Decimal('6300') and record.unit == 'т'
    assert record.price_with_vat == Decimal('8689.55')
    assert record.buyer_name == ''


# --- сохранение ----------------------------------------------------------

@pytest.mark.django_db
def test_rerun_does_not_create_duplicates(caspy_site):
    first = run_scraper(CaspyScraper(http=caspy_site, config=CONFIG), DAY, DAY)
    assert first.status == ParseRun.Status.SUCCESS and Trade.objects.count() == 4
    trade = Trade.objects.select_related('buyer', 'seller', 'buyer_broker').first()
    assert trade.buyer and trade.buyer.bin and trade.buyer_broker
    second = run_scraper(CaspyScraper(http=caspy_site, config=CONFIG), DAY, DAY)
    assert second.rows_added == 0 and second.rows_skipped == 4
    assert Trade.objects.count() == 4
