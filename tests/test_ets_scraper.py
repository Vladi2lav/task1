import io
from datetime import date
from decimal import Decimal

import openpyxl
import pytest

from scrapers.base import ScraperError
from scrapers.ets import EtsScraper, parse_period_label

from .conftest import (
    FakeHttp, INDEX_HTML, MONTH_OCT_2026, RESULTS, XLSX_07_10, YEAR_2026, fixture_bytes, fixture_text,
)


def make_xlsx(rows, directory_rows=None) -> bytes:
    """XLSX в памяти из списка строк; directory_rows — строки скрытого листа-справочника товаров."""
    workbook = openpyxl.Workbook()
    for row in rows:
        workbook.active.append(row)
    if directory_rows:
        sheet = workbook.create_sheet('Лист1')
        sheet.sheet_state = 'hidden'
        for row in directory_rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize('text, expected', [
    ('Итоги торгов 2026 год Октябрь', (2026, 10)),
    ('Итоги торгов 2026 год январь', (2026, 1)),
    ('Итоги торгов 2026 год май', (2026, 5)),
    ('Итоги торгов 2025 год', (2025, None)),
    ('Итоги торгов', (None, None)),
])
def test_parse_period_label(text, expected):
    assert parse_period_label(text) == expected


def test_year_page_lists_all_months_by_link_text():
    months = EtsScraper(http=FakeHttp({})).parse_month_links(fixture_text('ets_year_2026.html'))
    assert sorted(month for _, month in months) == list(range(1, 11))
    # слаги непоследовательны, месяц определён именно по тексту ссылки
    assert months[(2026, 2)].endswith('/itogi-torgov-fevral-2026/')
    assert months[(2026, 10)] == MONTH_OCT_2026


def test_index_page_lists_years():
    years = EtsScraper(http=FakeHttp({})).parse_year_links(INDEX_HTML)
    assert years == {2025: f'{RESULTS}2025-god/', 2026: YEAR_2026}


def test_month_page_gives_xlsx_link_with_date():
    links = EtsScraper(http=FakeHttp({})).parse_xlsx_links(fixture_text('ets_month_oktyabr_2026.html'))
    assert [(link.url, link.trade_date) for link in links] == [(XLSX_07_10, date(2026, 10, 7))]


def test_only_current_edition_is_taken():
    html = '''<div class="i_documents_section"><span class="i_documents_section_name">08.10.2026 ИТОГИ ТОРГОВ</span>
      <div class="i_documents_section_items"><div class="i_documents_section_list_title"><strong>Действующая редакция</strong></div>
        <div class="i_documents_item"><a href="/upload/new.xlsx" download="08.10.2026 ИТОГИ.xlsx" class="i_documents_icon">XLSX</a></div></div>
      <div class="i_documents_section_items"><div class="i_documents_section_list_title"><strong>Архив</strong></div>
        <div class="i_documents_item"><a href="/upload/old.xlsx" download="08.10.2026 ИТОГИ.xlsx" class="i_documents_icon">XLSX</a></div></div>
    </div>'''
    links = EtsScraper(http=FakeHttp({})).parse_xlsx_links(html)
    assert [link.url for link in links] == ['https://ets.kz/upload/new.xlsx']


def test_xlsx_rows():
    records = EtsScraper(http=FakeHttp({})).parse_xlsx(fixture_bytes('ets_2026-10-07.xlsx'), XLSX_07_10)

    assert len(records) == 5  # строка «Итого» не попала
    assert all(r.exchange_code == 'ets' and r.trade_date == date(2026, 10, 7) for r in records)
    assert all(r.source_url == XLSX_07_10 for r in records)
    assert all('итого' not in r.product_name.lower() for r in records)
    assert sum(r.amount for r in records) == Decimal('1320088000')

    first = records[0]
    assert first.buyer_name == 'ТОО "Астык Трейд KZ"'
    assert first.buyer_bin == '160740012545'
    assert first.seller_name == 'Продовольственная контрактная корпорация АО НК'
    assert first.seller_bin == '950440000101'
    assert first.buyer_broker == 'ATC Brok ТОО'
    assert first.seller_broker == 'Продовольственная контрактная корпорация АО НК'
    assert first.product_name == 'пшеница мягкая 4 класса, клейковина 18%, EXW'
    assert first.delivery_basis == 'EXW'
    assert first.tnved == '1001 99 000 0'
    assert first.product_code == 'W4DE700'
    assert first.deals_count == 1
    assert (first.price_open, first.price_close, first.price_min, first.price_max, first.price_avg) == (Decimal(88000),) * 5
    assert first.amount == Decimal(213048000)
    assert first.quantity is None  # объём ETS не публикует
    assert {r.buyer_bin for r in records} == {'160740012545', '170540016097'}
    assert {r.buyer_broker for r in records} == {'ATC Brok ТОО'}


def test_identical_rows_get_different_occurrence():
    header = [None, 'Наименование покупателя', 'БИН покупателя', 'Наименование продавца', 'БИН продавца',
              'Наименование товара', 'Код товара', 'Общая сумма сделки']
    row = [None, 'ТОО А', '160740012545', 'ТОО Б', '950440000101', 'уголь', 'C1', 1000]
    content = make_xlsx([[None, 'ИТОГИ ТОРГОВ 01.07.2026'], header, row, row])

    records = EtsScraper(http=FakeHttp({})).parse_xlsx(content, 'u')
    assert [r.occurrence for r in records] == [0, 1]
    assert records[0].trade_date == date(2026, 7, 1)  # дата из заголовка листа


def test_shifted_columns_are_found_by_header_text():
    content = make_xlsx([
        ['ИТОГИ ТОРГОВ 01.07.2026'],
        [],
        ['№', 'Код товара', 'Наименование товара', 'Общая сумма сделки', 'Наименование продавца',
         'Наименование покупателя', 'БИН покупателя', 'БИН продавца'],
        [1, 'C1', 'уголь', '1 500,50', 'ТОО Б', 'ТОО А', '160740012545', '950440000101'],
        [None, None, 'Итого', '=SUM(D4:D4)'],
    ])
    (record,) = EtsScraper(http=FakeHttp({})).parse_xlsx(content, 'u')
    assert (record.product_name, record.product_code, record.amount) == ('уголь', 'C1', Decimal('1500.50'))
    assert (record.buyer_name, record.seller_name) == ('ТОО А', 'ТОО Б')


def test_short_header_variant():
    """В части файлов шапка сокращена: «Кол-во сделок», «Макс. цена», «Мин. цена», «Средневзв. цена»."""
    content = make_xlsx([
        [None, 'ИТОГИ ТОРГОВ  ·  22.07.2026'],
        [None, 'Наименование покупателя', 'БИН\nпокупателя', 'Брокер покупателя', 'Наименование продавца',
         'БИН\nпродавца', 'Брокер продавца', 'Наименование товара', 'Код ТН ВЭД\nЕАЭС', 'Код\nтовара',
         'Кол-во\nсделок', 'Цена\nоткрытия', 'Цена\nзакрытия', 'Макс.\nцена', 'Мин.\nцена',
         'Средневзв.\nцена', 'Общая сумма\nсделки'],
        [None, 'ТОО А', 160740012545, 'Брокер 1', 'ТОО Б', 950440000101, 'Брокер 2', 'сахар', '1701', 'S1',
         2, 300, 310, 320, 290, 305, 1000],
    ])
    (record,) = EtsScraper(http=FakeHttp({})).parse_xlsx(content, 'u')
    assert record.deals_count == 2
    assert (record.price_open, record.price_close, record.price_max, record.price_min, record.price_avg) == (
        Decimal(300), Decimal(310), Decimal(320), Decimal(290), Decimal(305))
    assert (record.buyer_bin, record.seller_bin) == ('160740012545', '950440000101')
    assert (record.buyer_broker, record.seller_broker) == ('Брокер 1', 'Брокер 2')


def test_old_format_without_bin_brokers_and_amount():
    """Файлы за 2025 год: только названия, код, число сделок и цены; БИН, брокеров и суммы нет."""
    content = make_xlsx([
        [None, 'ИТОГИ ТОРГОВ  01.08.2025'],
        [None, 'Наименование покупателя', 'Наименование продавца', 'Наименование товара', 'Код товара',
         'Количество заключенных сделок', 'Цена открытия торгов', 'Цена закрытия торгов',
         'Максимальная цена сделки', 'Минимальная цена сделки', 'Средневзвешанная цена сделки'],
        [None, 'ТОО А', 'ТОО Б', 'Bidai/Пшеница, EXW', 'W4DEXW', 1, 87000, 87000, 87000, 87000, 87000],
    ])
    (record,) = EtsScraper(http=FakeHttp({})).parse_xlsx(content, 'u')
    assert record.trade_date == date(2025, 8, 1)
    assert (record.buyer_bin, record.buyer_broker, record.tnved) == ('', '', '')
    assert record.amount is None
    assert record.price_avg == Decimal(87000)


def test_product_from_vlookup_formula_is_taken_from_directory_sheet():
    # так выглядят файлы, где название и ТН ВЭД товара — формулы по скрытому листу; сохранённых значений нет
    content = make_xlsx(
        [
            [None, 'ИТОГИ ТОРГОВ 14.07.2026'],
            [None, 'Наименование покупателя', 'БИН покупателя', 'Наименование продавца', 'БИН продавца',
             'Наименование товара', 'Код ТН ВЭД ЕАЭС', 'Код товара', 'Общая сумма сделки'],
            [None, 'ТОО А', '160740012545', 'ТОО Б', '950440000101',
             '=VLOOKUP(H3,Товар,2,FALSE)', '=VLOOKUP(H3,Товар,3,FALSE)', 'UWDEXWA', 89760000],
            [None, None, None, None, None, 'Итого', None, None, '=SUM(I3:I3)'],
        ],
        directory_rows=[
            [None, 'Код Товара', 'Наименование Товара', 'Код ТН ВЭД ЕАЭС'],
            [None, 'UWDEXWA', 'Qant/Сахар белый, EXW', '1701 99 100 0'],
        ],
    )
    (record,) = EtsScraper(http=FakeHttp({})).parse_xlsx(content, 'u')
    assert record.product_name == 'Сахар белый, EXW'
    assert record.tnved == '1701 99 100 0'
    assert record.delivery_basis == 'EXW'
    assert record.amount == Decimal(89760000)


def test_file_without_known_header_is_error():
    with pytest.raises(ScraperError):
        EtsScraper(http=FakeHttp({})).parse_xlsx(make_xlsx([['что-то', 'совсем', 'другое']]), 'u', date(2026, 7, 1))


def test_fetch_goes_through_pages_and_downloads_file(ets_site):
    scraper = EtsScraper(http=ets_site)
    records = list(scraper.fetch(date(2026, 10, 1), date(2026, 10, 14)))

    assert len(records) == 5
    assert scraper.errors == []
    # лишние месяцы и годы не открывались
    assert ets_site.requested == [RESULTS, YEAR_2026, MONTH_OCT_2026, XLSX_07_10]


def test_fetch_skips_file_outside_period(ets_site):
    scraper = EtsScraper(http=ets_site)
    assert list(scraper.fetch(date(2026, 10, 8), date(2026, 10, 14))) == []
    assert XLSX_07_10 not in ets_site.requested


def test_fetch_other_year_does_not_open_2026(ets_site):
    ets_site.pages[f'{RESULTS}2025-god/'] = '<html></html>'
    list(EtsScraper(http=ets_site).fetch(date(2025, 12, 1), date(2025, 12, 31)))
    assert YEAR_2026 not in ets_site.requested


def test_broken_file_is_reported_and_fetch_continues(ets_site):
    ets_site.pages[XLSX_07_10] = b'not an xlsx'
    scraper = EtsScraper(http=ets_site)
    assert list(scraper.fetch(date(2026, 10, 1), date(2026, 10, 14))) == []
    assert len(scraper.errors) == 1 and XLSX_07_10 in scraper.errors[0]


def test_missing_index_page_is_fatal():
    with pytest.raises(ScraperError):
        list(EtsScraper(http=FakeHttp({})).fetch(date(2026, 10, 1), date(2026, 10, 14)))
