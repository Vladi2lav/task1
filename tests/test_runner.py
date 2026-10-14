from datetime import date
from decimal import Decimal

import pytest

from exchanges.models import Broker, Exchange, ParseRun, Participant, Product, Trade
from scrapers.base import BaseScraper, ScraperError, TradeRecord
from scrapers.ets import EtsScraper
from scrapers.runner import run_scraper

PERIOD = (date(2026, 10, 1), date(2026, 10, 14))


def make_record(**changes) -> TradeRecord:
    values = dict(exchange_code='test', trade_date=date(2026, 10, 7), product_name='пшеница',
                  buyer_name='ТОО А', buyer_bin='160740012545', seller_name='ТОО Б', seller_bin='950440000101',
                  buyer_broker='Брокер 1', amount=Decimal('1000'), price_avg=Decimal('100'))
    values.update(changes)
    return TradeRecord(**values)


class FakeScraper(BaseScraper):
    """Парсер-заглушка: отдаёт заданные записи, затем может упасть."""
    exchange_code = 'test'
    exchange_name = 'Тестовая'

    def __init__(self, records=(), errors=(), fail_with=None):
        self.records, self.errors, self.fail_with = list(records), list(errors), fail_with

    def fetch(self, date_from, date_to):
        yield from self.records
        if self.fail_with:
            raise self.fail_with


@pytest.mark.django_db
def test_run_saves_trades_and_dictionaries():
    run = run_scraper(FakeScraper([make_record(), make_record(occurrence=1)]), *PERIOD, trigger='schedule')

    assert run.status == ParseRun.Status.SUCCESS
    assert (run.rows_added, run.rows_skipped) == (2, 0)
    assert run.finished_at and run.trigger == 'schedule' and run.date_from == PERIOD[0]
    assert Exchange.objects.get(code='test').name == 'Тестовая'
    # справочники не раздуваются повторами
    assert (Product.objects.count(), Participant.objects.count(), Broker.objects.count()) == (1, 2, 1)
    trade = Trade.objects.first()
    assert trade.parse_run == run and trade.buyer.bin == '160740012545' and trade.seller_broker is None


@pytest.mark.django_db
def test_rerun_adds_nothing():
    records = [make_record(), make_record(occurrence=1), make_record(product_name='ячмень')]
    first = run_scraper(FakeScraper(records), *PERIOD)
    second = run_scraper(FakeScraper(records), *PERIOD)

    assert (first.rows_added, first.rows_skipped) == (3, 0)
    assert (second.rows_added, second.rows_skipped) == (0, 3)
    assert Trade.objects.count() == 3
    assert second.status == ParseRun.Status.SUCCESS


@pytest.mark.django_db
def test_identical_rows_without_occurrence_collapse():
    run = run_scraper(FakeScraper([make_record(), make_record()]), *PERIOD)
    assert (run.rows_added, run.rows_skipped) == (1, 1)


@pytest.mark.django_db
def test_participant_with_bin_is_not_duplicated_when_name_changes():
    run_scraper(FakeScraper([make_record()]), *PERIOD)
    run_scraper(FakeScraper([make_record(buyer_name='ТОО "А"', trade_date=date(2026, 10, 8))]), *PERIOD)
    assert Participant.objects.filter(bin='160740012545').count() == 1


@pytest.mark.django_db
def test_source_failure_is_failed_and_data_is_kept():
    run_scraper(FakeScraper([make_record()]), *PERIOD)

    run = run_scraper(FakeScraper(fail_with=RuntimeError('сайт недоступен')), *PERIOD)

    assert run.status == ParseRun.Status.FAILED
    assert 'сайт недоступен' in run.error
    assert run.finished_at is not None
    assert Trade.objects.count() == 1


@pytest.mark.django_db
def test_failure_after_some_records_keeps_saved_data():
    run = run_scraper(FakeScraper([make_record()], fail_with=RuntimeError('обрыв')), *PERIOD)
    assert run.status == ParseRun.Status.FAILED
    assert run.rows_added == 1 and Trade.objects.count() == 1  # собранное до сбоя сохранено


@pytest.mark.django_db
def test_unavailable_source_is_failed_with_message():
    run = run_scraper(FakeScraper(fail_with=ScraperError('Сайт Каспия недоступен')), *PERIOD)
    assert run.status == ParseRun.Status.FAILED
    assert 'Каспия' in run.error


@pytest.mark.django_db
def test_partial_when_some_files_failed():
    run = run_scraper(FakeScraper([make_record()], errors=['Файл x.xlsx: битый']), *PERIOD)
    assert run.status == ParseRun.Status.PARTIAL
    assert 'x.xlsx' in run.error and run.rows_added == 1


@pytest.mark.django_db
def test_failed_when_all_files_failed():
    run = run_scraper(FakeScraper(errors=['Файл x.xlsx: битый']), *PERIOD)
    assert run.status == ParseRun.Status.FAILED


@pytest.mark.django_db
def test_ets_end_to_end_with_rerun(ets_site):
    first = run_scraper(EtsScraper(http=ets_site), *PERIOD)
    second = run_scraper(EtsScraper(http=ets_site), *PERIOD)

    assert first.status == ParseRun.Status.SUCCESS
    assert (first.rows_added, second.rows_added, second.rows_skipped) == (5, 0, 5)
    assert Trade.objects.count() == 5
    assert Exchange.objects.get(code='ets').website == 'https://ets.kz'
    assert sum(t.amount for t in Trade.objects.all()) == Decimal('1320088000')
    assert Broker.objects.filter(name='ATC Brok ТОО').count() == 1
