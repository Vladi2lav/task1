from datetime import date
from decimal import Decimal

import pytest

from scrapers.normalize import (
    clean_text, normalize_bin, normalize_company_name, normalize_unit, parse_date, parse_decimal,
    split_bilingual_product,
)


@pytest.mark.parametrize('value, expected', [
    ('1 234,56', Decimal('1234.56')),
    ('1 234 567', Decimal('1234567')),
    ('88000', Decimal('88000')),
    (88000, Decimal('88000')),
    (88000.5, Decimal('88000.5')),
    (' 12,5 ', Decimal('12.5')),
    ('=SUM(Q5:Q9)', None),
    ('', None),
    (None, None),
    ('нет данных', None),
    (True, None),
])
def test_parse_decimal(value, expected):
    assert parse_decimal(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('07.10.2026', date(2026, 10, 7)),
    ('7.1.2026', date(2026, 1, 7)),
    ('2026-10-07', date(2026, 10, 7)),
    ('2026-10-07T12:00:00', date(2026, 10, 7)),
    ('31.02.2026', None),
    ('вчера', None),
    (None, None),
])
def test_parse_date(value, expected):
    assert parse_date(value) == expected


def test_clean_text():
    assert clean_text('  пшеница \n мягкая\t4 класса  ') == 'пшеница мягкая 4 класса'
    assert clean_text(None) == ''
    assert clean_text('абвгде', max_length=3) == 'абв'


@pytest.mark.parametrize('value, expected', [
    ('160740012545', '160740012545'),
    ('1607 4001 2545', '160740012545'),
    (160740012545, '160740012545'),
    (160740012545.0, '160740012545'),
    (50140001234, '050140001234'),  # Excel потерял ведущий ноль
    ('12345', ''),
    ('', ''),
    (None, ''),
    ('не указан', ''),
])
def test_normalize_bin(value, expected):
    assert normalize_bin(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('ТОО  "Астык Трейд KZ"', 'ТОО "Астык Трейд KZ"'),
    ('ТОО «Астык  Трейд»', 'ТОО "Астык Трейд"'),
    ('ТОО"Астык Трейд"', 'ТОО "Астык Трейд"'),
    ('ТОО " Астык Трейд "', 'ТОО "Астык Трейд"'),
    ('Продовольственная контрактная корпорация АО НК', 'Продовольственная контрактная корпорация АО НК'),
    ('ТОО "Астык', 'ТОО "Астык'),
    (None, ''),
])
def test_normalize_company_name(value, expected):
    assert normalize_company_name(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('тонна', 'т'), ('Тн', 'т'), ('т.', 'т'), ('кг', 'кг'), ('Литр', 'л'),
    ('шт.', 'шт'), ('м³', 'м3'), ('куб.м', 'м3'), ('баррель', 'баррель'), ('', ''), (None, ''),
])
def test_normalize_unit(value, expected):
    assert normalize_unit(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('4 klasty jumsaq bidai, EXW/пшеница мягкая 4 класса, EXW', 'пшеница мягкая 4 класса, EXW'),
    ('Қант/ сахар', 'сахар'),  # казахская часть кириллицей
    ('пшеница мягкая', 'пшеница мягкая'),
    ('мука 1/2 сорта', 'мука 1/2 сорта'),
    ('Aq qant 5/10/сахар 5/10', 'сахар 5/10'),
    # слэши внутри частей («ж/д») не разделители
    ('tek t/j/ koligimen jetkizy/Бензин АИ-92, поставка только ж/д транспортом',
     'Бензин АИ-92, поставка только ж/д транспортом'),
    ('D Transport/Топливо д/ реакт двиг', 'Топливо д/ реакт двиг'),
    ('', ''),
])
def test_split_bilingual_product(value, expected):
    assert split_bilingual_product(value) == expected
