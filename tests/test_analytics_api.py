"""Тесты аналитики: JSON API /api/analytics/ и страница /analytics/.

Данные создаются в самих тестах; сеть не используется. Набор данных подобран так,
чтобы каждая секция ответа проверялась точными числами (см. фикстуру ``данные``).
"""
import types
from datetime import date
from decimal import Decimal

import pytest
from django.test import Client

from exchanges.models import Exchange, Participant, Product, Trade

pytestmark = pytest.mark.django_db

Д1, Д2, Д3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
_счётчик = {'n': 0}


def D(значение):
    return Decimal(str(значение))


def сделка(биржа, товар, дата, **поля):
    """Запись Trade с уникальным source_key."""
    _счётчик['n'] += 1
    return Trade.objects.create(
        exchange=биржа, product=товар, trade_date=дата, source_key=f'an-{_счётчик["n"]}', **поля
    )


@pytest.fixture
def клиент():
    return Client()


@pytest.fixture
def данные():
    """Две биржи, 3 товара, 3 участника, 8 записей за 3 дня.

    День 1: пшеница 10 т по 100 (1000) и 30 т по 200 (6000)  -> взвешенная цена 175.
    День 2: пшеница без объёма: цена 100 при сумме 1000 и цена 300 при сумме 3000
            (объём выводится как сумма / цена: 10 и 10 -> цена 200);
            ещё одна запись без суммы и объёма (цена 500, вес 0 — на цену не влияет).
    День 3: уголь (Каспий) 100 т по 50 (5000); уголь без цены и суммы;
            пшеница без суммы и объёма по цене 100 (день из одной «безвесовой» записи -> среднее 100).
    Сумма с NULL — 3 записи из 8.
    """
    ets = Exchange.objects.create(code='ets', name='ETS')
    каспий = Exchange.objects.create(code='caspy', name='Каспий')
    пшеница = Product.objects.create(name='Пшеница мягкая 3 класс', code='WH3', tnved='1001')
    уголь = Product.objects.create(name='Уголь каменный марки Д', code='CL1', tnved='2701')
    сахар = Product.objects.create(name='Сахар-песок', code='SG1', tnved='1701')  # сделок нет
    a = Participant.objects.create(name='ТОО Альфа', bin='111111111111')
    b = Participant.objects.create(name='ТОО Бета', bin='222222222222')
    c = Participant.objects.create(name='ТОО Гамма', bin='')

    записи = {
        'п1': сделка(ets, пшеница, Д1, buyer=a, seller=b, quantity=D(10), price_avg=D(100), amount=D(1000), deals_count=2),
        'п2': сделка(ets, пшеница, Д1, buyer=a, seller=c, quantity=D(30), price_avg=D(200), amount=D(6000), deals_count=1),
        'п3': сделка(ets, пшеница, Д2, buyer=b, seller=a, price_avg=D(100), amount=D(1000)),
        'п4': сделка(ets, пшеница, Д2, buyer=b, seller=a, price_avg=D(300), amount=D(3000)),
        'п5': сделка(ets, пшеница, Д2, buyer=c, seller=b, price_avg=D(500)),
        'у1': сделка(каспий, уголь, Д3, buyer=c, seller=a, quantity=D(100), price_avg=D(50), amount=D(5000), deals_count=4),
        'у2': сделка(ets, уголь, Д3, buyer=c, seller=b),
        'п6': сделка(ets, пшеница, Д3, buyer=a, seller=b, price_avg=D(100)),
    }
    return types.SimpleNamespace(
        ets=ets, каспий=каспий, пшеница=пшеница, уголь=уголь, сахар=сахар, a=a, b=b, c=c, записи=записи
    )


def получить(клиент, **параметры):
    ответ = клиент.get('/api/analytics/', параметры)
    assert ответ.status_code == 200, ответ.content
    return ответ.json()


def точки(тело):
    return {p['date']: p for p in тело['price_dynamics']['points']}


# ------------------------------------------------------------ формат ответа

def test_формат_ответа(клиент, данные):
    ответ = клиент.get('/api/analytics/')
    assert ответ.status_code == 200
    assert ответ['Content-Type'].startswith('application/json')
    тело = ответ.json()
    assert set(тело) == {
        'filters', 'summary', 'products', 'price_dynamics',
        'top_products', 'top_buyers', 'top_sellers', 'turnover_by_day',
    }
    assert тело['filters'] == {'exchange': '', 'date_from': None, 'date_to': None, 'q': ''}


def test_только_get(клиент, данные):
    assert клиент.post('/api/analytics/').status_code == 405


def test_русский_текст_без_экранирования(клиент, данные):
    assert 'Пшеница мягкая 3 класс' in клиент.get('/api/analytics/').content.decode('utf-8')


# --------------------------------------------------------------------- сводка

def test_сводка(клиент, данные):
    сводка = получить(клиент)['summary']
    assert сводка['records'] == 8
    assert сводка['trading_days'] == 3
    assert сводка['total_amount'] == 16000.0
    assert сводка['deals_total'] == 7
    assert сводка['records_without_amount'] == 3
    assert сводка['share_without_amount'] == 0.375


# ------------------------------------------------------------ динамика цены

def test_список_товаров_по_числу_записей(клиент, данные):
    товары = получить(клиент)['products']
    assert [(t['code'], t['records']) for t in товары] == [('WH3', 6), ('CL1', 2)]
    assert товары[0]['id'] == данные.пшеница.id
    assert товары[0]['name'] == 'Пшеница мягкая 3 класс'


def test_цена_по_умолчанию_для_самого_частого_товара(клиент, данные):
    блок = получить(клиент)['price_dynamics']
    assert блок['product'] == {'id': данные.пшеница.id, 'code': 'WH3', 'name': 'Пшеница мягкая 3 класс'}
    assert [p['date'] for p in блок['points']] == ['2026-10-01', '2026-10-02', '2026-10-03']


def test_средневзвешенная_цена_по_объёму(клиент, данные):
    # (10*100 + 30*200) / 40 = 175
    день = точки(получить(клиент))['2026-10-01']
    assert день['price'] == 175.0
    assert день['records'] == 2


def test_объём_выводится_из_суммы_и_цены_когда_его_нет(клиент, данные):
    # веса 1000/100 = 10 и 3000/300 = 10 -> (100*10 + 300*10) / 20 = 200; запись без суммы вес 0
    день = точки(получить(клиент))['2026-10-02']
    assert день['price'] == 200.0
    assert день['records'] == 3


def test_день_только_из_записей_без_веса_даёт_простое_среднее(клиент, данные):
    assert точки(получить(клиент))['2026-10-03']['price'] == 100.0


def test_товар_по_product_id(клиент, данные):
    блок = получить(клиент, product_id=данные.уголь.id)['price_dynamics']
    assert блок['product']['code'] == 'CL1'
    assert блок['points'] == [{'date': '2026-10-03', 'price': 50.0, 'records': 1}]
    assert блок['records_without_price'] == 1  # у2 без цены


def test_товар_по_product_code(клиент, данные):
    блок = получить(клиент, product_code='CL1')['price_dynamics']
    assert блок['product']['id'] == данные.уголь.id
    assert len(блок['points']) == 1


def test_товар_без_сделок_и_неизвестный_товар(клиент, данные):
    блок = получить(клиент, product_id=данные.сахар.id)['price_dynamics']
    assert блок['product']['code'] == 'SG1'
    assert блок['points'] == []
    assert получить(клиент, product_id=999999)['price_dynamics'] == {
        'product': None, 'points': [], 'records_without_price': 0,
    }
    assert получить(клиент, product_code='НЕТ-ТАКОГО')['price_dynamics']['product'] is None


def test_цена_учитывает_фильтры(клиент, данные):
    # только Каспий: у пшеницы там нет записей, график строится по самому частому товару выборки
    блок = получить(клиент, exchange='caspy')['price_dynamics']
    assert блок['product']['code'] == 'CL1'
    assert блок['points'] == [{'date': '2026-10-03', 'price': 50.0, 'records': 1}]
    # явный товар вне выборки -> пустой график
    assert получить(клиент, exchange='caspy', product_id=данные.пшеница.id)['price_dynamics']['points'] == []


# ------------------------------------------------------------------- топы

def test_топ_товаров_по_сумме(клиент, данные):
    топ = получить(клиент)['top_products']
    assert [(t['code'], t['amount'], t['records']) for t in топ] == [('WH3', 11000.0, 4), ('CL1', 5000.0, 1)]


def test_топ_покупателей(клиент, данные):
    топ = получить(клиент)['top_buyers']
    assert [(t['name'], t['amount'], t['records']) for t in топ] == [
        ('ТОО Альфа', 7000.0, 2), ('ТОО Гамма', 5000.0, 1), ('ТОО Бета', 4000.0, 2),
    ]
    assert топ[0]['bin'] == '111111111111'
    assert топ[1]['bin'] == ''


def test_топ_продавцов(клиент, данные):
    топ = получить(клиент)['top_sellers']
    assert [(t['name'], t['amount'], t['records']) for t in топ] == [
        ('ТОО Альфа', 9000.0, 3), ('ТОО Гамма', 6000.0, 1), ('ТОО Бета', 1000.0, 1),
    ]


def test_топ_ограничен_десятью(клиент, данные):
    for номер in range(12):
        товар = Product.objects.create(name=f'Товар {номер}', code=f'T{номер}')
        участник = Participant.objects.create(name=f'Участник {номер}', bin=f'{номер:012d}')
        сделка(данные.ets, товар, Д1, buyer=участник, seller=участник, amount=D(10 + номер))
    тело = получить(клиент)
    assert len(тело['top_products']) == 10
    assert len(тело['top_buyers']) == 10
    assert len(тело['top_sellers']) == 10
    assert len(тело['products']) == 14  # все товары с записями (меньше предела списка)
    суммы = [t['amount'] for t in тело['top_products']]
    assert суммы == sorted(суммы, reverse=True)
    assert суммы[0] == 11000.0  # пшеница всё ещё первая


def test_записи_без_покупателя_не_ломают_топ_участников(клиент, данные):
    сделка(данные.ets, данные.уголь, Д1, buyer=None, seller=None, amount=D(777))
    тело = получить(клиент)
    assert sum(t['amount'] for t in тело['top_buyers']) == 16000.0  # 777 безымянных не попали
    assert тело['summary']['total_amount'] == 16777.0


# --------------------------------------------------------------- оборот по дням

def test_оборот_по_дням(клиент, данные):
    дни = получить(клиент)['turnover_by_day']
    assert дни == [
        {'date': '2026-10-01', 'amount': 7000.0, 'deals': 3, 'records': 2, 'records_without_amount': 0},
        {'date': '2026-10-02', 'amount': 4000.0, 'deals': 0, 'records': 3, 'records_without_amount': 1},
        {'date': '2026-10-03', 'amount': 5000.0, 'deals': 4, 'records': 3, 'records_without_amount': 2},
    ]


# ---------------------------------------------------------------------- фильтры

def test_фильтр_по_бирже(клиент, данные):
    тело = получить(клиент, exchange='caspy')
    assert тело['summary']['records'] == 1
    assert тело['summary']['total_amount'] == 5000.0
    assert тело['summary']['trading_days'] == 1
    assert [t['code'] for t in тело['top_products']] == ['CL1']
    assert [d['date'] for d in тело['turnover_by_day']] == ['2026-10-03']
    assert получить(клиент, exchange='ets')['summary']['records'] == 7
    assert получить(клиент, exchange='all')['summary']['records'] == 8


def test_фильтр_по_периоду(клиент, данные):
    тело = получить(клиент, date_from='2026-10-02', date_to='2026-10-02')
    assert тело['summary']['records'] == 3
    assert тело['summary']['trading_days'] == 1
    assert тело['summary']['total_amount'] == 4000.0
    assert [d['date'] for d in тело['turnover_by_day']] == ['2026-10-02']
    assert [p['date'] for p in тело['price_dynamics']['points']] == ['2026-10-02']
    assert получить(клиент, date_from='2026-10-03')['summary']['records'] == 3
    assert получить(клиент, date_to='2026-10-01')['summary']['records'] == 2
    assert получить(клиент, date_from='2027-01-01')['summary']['records'] == 0


def test_фильтр_по_ключевому_слову_без_учёта_регистра(клиент, данные):
    тело = получить(клиент, q='УГОЛЬ')
    assert тело['summary']['records'] == 2
    assert [t['code'] for t in тело['products']] == ['CL1']
    assert [t['code'] for t in тело['top_products']] == ['CL1']
    assert тело['price_dynamics']['product']['code'] == 'CL1'
    assert получить(клиент, q='пшеница класс')['summary']['records'] == 6  # все слова в названии
    assert получить(клиент, q='нет такого')['summary']['records'] == 0


def test_фильтры_комбинируются(клиент, данные):
    тело = получить(клиент, exchange='ets', q='уголь')
    assert тело['summary']['records'] == 1
    assert тело['summary']['records_without_amount'] == 1
    assert тело['top_products'] == []


# ------------------------------------------------------------------ NULL-суммы

def test_все_суммы_пустые(клиент, данные):
    Trade.objects.update(amount=None)
    тело = получить(клиент)
    assert тело['summary']['records'] == 8
    assert тело['summary']['total_amount'] == 0.0
    assert тело['summary']['records_without_amount'] == 8
    assert тело['summary']['share_without_amount'] == 1.0
    assert тело['top_products'] == []
    assert тело['top_buyers'] == [] and тело['top_sellers'] == []
    assert [d['amount'] for d in тело['turnover_by_day']] == [0.0, 0.0, 0.0]
    # без сумм и объёмов цена считается простым средним
    assert точки(тело)['2026-10-02']['price'] == 300.0  # (100 + 300 + 500) / 3
    assert получить(клиент, exchange='caspy')['summary']['share_without_amount'] == 1.0


def test_цена_нулевая_или_пустая_пропускается(клиент, данные):
    Trade.objects.filter(pk=данные.записи['п4'].pk).update(price_avg=D(0))
    Trade.objects.filter(pk=данные.записи['п5'].pk).update(price_avg=None)
    блок = получить(клиент)['price_dynamics']
    assert точки({'price_dynamics': блок})['2026-10-02']['price'] == 100.0
    assert блок['records_without_price'] == 2


# ------------------------------------------------------------------ пустая БД

def test_пустая_база(клиент):
    ответ = клиент.get('/api/analytics/')
    assert ответ.status_code == 200
    assert ответ.json() == {
        'filters': {'exchange': '', 'date_from': None, 'date_to': None, 'q': ''},
        'summary': {
            'records': 0, 'trading_days': 0, 'total_amount': 0.0, 'deals_total': 0,
            'records_without_amount': 0, 'share_without_amount': 0.0,
        },
        'products': [],
        'price_dynamics': {'product': None, 'points': [], 'records_without_price': 0},
        'top_products': [], 'top_buyers': [], 'top_sellers': [], 'turnover_by_day': [],
    }


def test_пустая_выборка_после_фильтра(клиент, данные):
    тело = получить(клиент, q='несуществующий товар')
    assert тело['summary']['records'] == 0
    assert тело['price_dynamics']['product'] is None
    assert тело['turnover_by_day'] == []


# ------------------------------------------------------------------- ошибки

@pytest.mark.parametrize('параметры', [
    {'exchange': 'nasdaq'},
    {'date_from': '01.10.2026'},
    {'date_to': 'вчера'},
    {'date_from': '2026-10-05', 'date_to': '2026-10-01'},
    {'product_id': 'abc'},
])
def test_некорректные_параметры_дают_400(клиент, данные, параметры):
    ответ = клиент.get('/api/analytics/', параметры)
    assert ответ.status_code == 400
    assert ответ.json()['error']


# ---------------------------------------------------------------- страница

def test_страница_аналитики(клиент, данные):
    ответ = клиент.get('/analytics/')
    assert ответ.status_code == 200
    html = ответ.content.decode('utf-8')
    for элемент in ('chart-price', 'chart-turnover', 'chart-products', 'chart-parties', 'a-product_id'):
        assert f'id="{элемент}"' in html
    assert '/static/portal/vendor/chart.umd.min.js' in html
    assert 'cdn.' not in html and 'jsdelivr' not in html  # без CDN
    assert 'data-page="analytics"' in html


def test_пункт_меню_аналитика_на_всех_страницах(клиент):
    for адрес in ('/', '/status/', '/analytics/'):
        html = клиент.get(адрес).content.decode('utf-8')
        assert 'href="/analytics/"' in html and 'Аналитика' in html
    assert 'class="active">Аналитика' in клиент.get('/analytics/').content.decode('utf-8')


def test_локальная_копия_chartjs_на_месте():
    from pathlib import Path
    файл = Path(__file__).resolve().parent.parent / 'portal' / 'static' / 'portal' / 'vendor' / 'chart.umd.min.js'
    assert файл.is_file() and файл.stat().st_size > 100_000
