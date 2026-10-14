"""Тесты парсера Каспия: сеть не используется, API подменён образцом из tests/fixtures."""
import json
from datetime import date
from decimal import Decimal

import pytest

from exchanges.models import ParseRun, Trade
from scrapers.caspy import CaspyScraper
from scrapers.runner import run_scraper

from .conftest import FakeHttp, fixture_text

API = 'https://ccx.kz/api/internal/trading-results'
CONFIG = {'base_url': 'https://ccx.kz', 'sections': ['CL']}
SAMPLE = json.loads(fixture_text('caspy_trading_results_CL.json'))


def _url(regulated: bool) -> str:
    return (f'{API}?section_code=CL&instrument_regulated={"true" if regulated else "false"}'
            '&max_results=20')


def _payload(regulated: bool) -> str:
    flag = 'true' if regulated else 'false'
    rows = [{k: v for k, v in row.items() if k != '_reg'} for row in SAMPLE if row['_reg'] == flag]
    return json.dumps({'success': True, 'message': 'ok', 'data': rows})


@pytest.fixture
def caspy_site():
    return FakeHttp({_url(True): _payload(True), _url(False): _payload(False)})


def test_parse_item_maps_fields(caspy_site):
    scraper = CaspyScraper(http=caspy_site, config=CONFIG)
    record = scraper.parse_item({k: v for k, v in SAMPLE[0].items() if k != '_reg'})
    assert record.exchange_code == 'caspy'
    assert record.trade_date == date(2026, 10, 9)
    assert record.product_code == 'CL17FCQMB'
    assert record.product_name == 'Уголь марки Б'
    assert record.delivery_basis == 'FCA'
    assert record.delivery_place.startswith('Павлодарская область')
    assert record.deals_count == 6
    assert record.quantity == Decimal('6300')
    assert record.unit == 'т'
    assert record.price_avg == Decimal('8689.55')
    assert record.price_with_vat == Decimal('8689.55') and record.price_without_vat is None
    assert record.amount == Decimal('54744144.00')
    assert record.buyer_name == '' and record.seller_name == ''


def test_fetch_filters_by_period(caspy_site):
    scraper = CaspyScraper(http=caspy_site, config=CONFIG)
    records = list(scraper.fetch(date(2026, 10, 1), date(2026, 10, 14)))
    assert records and all(date(2026, 10, 1) <= r.trade_date <= date(2026, 10, 14) for r in records)
    everything = list(scraper.fetch(date(2025, 1, 1), date(2026, 12, 31)))
    assert len(everything) == len(SAMPLE)


def test_without_vat_goes_to_separate_price(caspy_site):
    scraper = CaspyScraper(http=caspy_site, config=CONFIG)
    row = {k: v for k, v in SAMPLE[0].items() if k != '_reg'}
    row['vat'] = 'без НДС'
    record = scraper.parse_item(row)
    assert record.price_without_vat == Decimal('8689.55') and record.price_with_vat is None


def test_broken_section_is_logged_and_others_survive():
    http = FakeHttp({_url(True): _payload(True), _url(False): '<html>не JSON</html>'})
    scraper = CaspyScraper(http=http, config=CONFIG)
    records = list(scraper.fetch(date(2025, 1, 1), date(2026, 12, 31)))
    assert records
    assert len(scraper.errors) == 1


@pytest.mark.django_db
def test_rerun_does_not_create_duplicates(caspy_site):
    first = run_scraper(CaspyScraper(http=caspy_site, config=CONFIG), date(2025, 1, 1), date(2026, 12, 31))
    count = Trade.objects.count()
    assert first.status == ParseRun.Status.SUCCESS and count == len(SAMPLE)
    second = run_scraper(CaspyScraper(http=caspy_site, config=CONFIG), date(2025, 1, 1), date(2026, 12, 31))
    assert second.rows_added == 0 and second.rows_skipped == count
    assert Trade.objects.count() == count
