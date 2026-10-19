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
        'filters', 'selected', 'unknown_ids', 'compare_mode', 'summary', 'products', 'price_dynamics',
        'series', 'comparison', 'top_products', 'top_buyers', 'top_sellers', 'turnover_by_day',
        'turnover_by_month',
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


# ------------------------------------------------------------ оборот по месяцам
# Календарь 2026: 01.10 — четверг, 03.10 — суббота, 01.11 — воскресенье, 27.11 — пятница,
# 30.11 — понедельник, 14.09 — понедельник.

def месяц(тело, ключ):
    return next(m for m in тело['turnover_by_month'] if m['month'] == ключ)


def test_оборот_по_месяцам(клиент, данные):
    assert получить(клиент)['turnover_by_month'] == [{
        'month': '2026-10', 'amount': 16000.0, 'deals': 7, 'records': 8, 'records_without_amount': 3,
        'period_from': '2026-10-01', 'period_to': '2026-10-03',
        # данные есть только по 03.10, дальше в октябре есть будни — месяц неполный в конце
        'partial_start': False, 'partial_end': True,
    }]


def test_оборот_по_месяцам_несколько_месяцев_и_края_периода(клиент, данные):
    сделка(данные.ets, данные.пшеница, date(2026, 9, 14), amount=D(500), deals_count=2)
    сделка(данные.ets, данные.пшеница, date(2026, 9, 30), amount=D(250))
    сделка(данные.каспий, данные.уголь, date(2026, 11, 27), amount=D(100), deals_count=1)
    тело = получить(клиент)
    месяцы = тело['turnover_by_month']
    assert [(m['month'], m['amount'], m['deals'], m['records']) for m in месяцы] == [
        ('2026-09', 750.0, 2, 2), ('2026-10', 16000.0, 7, 8), ('2026-11', 100.0, 1, 1),
    ]
    # сентябрь: данные с понедельника 14.09, а 1–11 сентября — будни -> неполный в начале
    assert (месяцы[0]['partial_start'], месяцы[0]['partial_end']) == (True, False)
    assert месяцы[0]['period_from'] == '2026-09-14' and месяцы[0]['period_to'] == '2026-09-30'
    # октябрь внутри периода — полный, хотя сделки были только 1–3 числа
    assert (месяцы[1]['partial_start'], месяцы[1]['partial_end']) == (False, False)
    assert (месяцы[1]['period_from'], месяцы[1]['period_to']) == ('2026-10-01', '2026-10-31')
    # ноябрь: данные по пятницу 27.11, а 30.11 — понедельник -> неполный в конце
    assert (месяцы[2]['partial_start'], месяцы[2]['partial_end']) == (False, True)
    assert месяцы[2]['period_to'] == '2026-11-27'
    # суммы по месяцам сходятся со сводкой и с оборотом по дням
    assert sum(m['amount'] for m in месяцы) == тело['summary']['total_amount']
    assert sum(m['records'] for m in месяцы) == sum(d['records'] for d in тело['turnover_by_day'])


def test_оборот_по_месяцам_выходные_на_краю_не_делают_месяц_неполным(клиент, данные):
    # 01.11 — воскресенье: данные с понедельника 02.11 — начало месяца полное
    сделка(данные.ets, данные.пшеница, date(2026, 11, 2), amount=D(10))
    сделка(данные.ets, данные.пшеница, date(2026, 11, 30), amount=D(20))
    ноябрь = месяц(получить(клиент, date_from='2026-11-01'), '2026-11')
    assert (ноябрь['partial_start'], ноябрь['partial_end']) == (False, False)
    # период обрезан пользователем посреди месяца -> неполный с обеих сторон
    ноябрь = месяц(получить(клиент, date_from='2026-11-02', date_to='2026-11-20'), '2026-11')
    assert ноябрь['amount'] == 10.0
    assert (ноябрь['partial_start'], ноябрь['partial_end']) == (False, True)
    assert ноябрь['period_to'] == '2026-11-02'  # последний день данных в периоде


def test_оборот_по_месяцам_учитывает_биржу_и_период(клиент, данные):
    assert получить(клиент, exchange='caspy')['turnover_by_month'] == [{
        'month': '2026-10', 'amount': 5000.0, 'deals': 4, 'records': 1, 'records_without_amount': 0,
        'period_from': '2026-10-03', 'period_to': '2026-10-03', 'partial_start': True, 'partial_end': True,
    }]
    октябрь = получить(клиент, date_from='2026-10-02', date_to='2026-10-02')['turnover_by_month']
    assert [(m['amount'], m['records'], m['records_without_amount']) for m in октябрь] == [(4000.0, 3, 1)]
    assert октябрь[0]['period_from'] == октябрь[0]['period_to'] == '2026-10-02'
    assert получить(клиент, date_from='2027-01-01')['turnover_by_month'] == []


def test_оборот_по_месяцам_по_выбранным_товарам(клиент, данные):
    тело = получить(клиент, product=данные.уголь.id)
    assert [(m['month'], m['amount'], m['deals'], m['records'], m['records_without_amount'])
            for m in тело['turnover_by_month']] == [('2026-10', 5000.0, 4, 2, 1)]
    # уголь торговался только 03.10, но данные выборки (все товары) начинаются 01.10:
    # начало месяца не считается неполным из-за того, что товар не торговался
    assert тело['turnover_by_month'][0]['period_from'] == '2026-10-01'
    assert тело['turnover_by_month'][0]['partial_start'] is False
    тело = получить(клиент, product=[данные.уголь.id, данные.пшеница.id], exchange='ets')
    assert тело['turnover_by_month'][0]['amount'] == 11000.0
    assert получить(клиент, product=данные.сахар.id)['turnover_by_month'] == []


def test_оборот_по_месяцам_один_запрос(данные, django_assert_num_queries):
    from portal import analytics
    for номер in range(1, 13):
        сделка(данные.ets, данные.пшеница, date(2025, номер, 15), amount=D(номер), deals_count=1)
    qs = Trade.objects.all()
    with django_assert_num_queries(1):
        месяцы = analytics.turnover_by_month(qs)
    assert len(месяцы) == 13
    assert [m['month'] for m in месяцы][:2] == ['2025-01', '2025-02']
    assert месяцы[-1]['month'] == '2026-10'


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
        'selected': [], 'unknown_ids': [], 'compare_mode': 'top',
        'products': [],
        'price_dynamics': {'product': None, 'points': [], 'records_without_price': 0},
        'series': [], 'comparison': [],
        'top_products': [], 'top_buyers': [], 'top_sellers': [], 'turnover_by_day': [],
        'turnover_by_month': [],
    }


def test_пустая_выборка_после_фильтра(клиент, данные):
    тело = получить(клиент, q='несуществующий товар')
    assert тело['summary']['records'] == 0
    assert тело['price_dynamics']['product'] is None
    assert тело['turnover_by_day'] == []
    assert тело['turnover_by_month'] == []


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


# ---------------------------------------------- множественный выбор товаров

def строки(тело):
    return {r['code']: r for r in тело['comparison']}


def test_ничего_не_выбрано_значит_все_товары(клиент, данные):
    тело = получить(клиент)
    assert тело['selected'] == [] and тело['unknown_ids'] == []
    assert тело['summary']['records'] == 8
    assert тело['summary']['total_amount'] == 16000.0
    assert [t['code'] for t in тело['top_products']] == ['WH3', 'CL1']
    # для сравнения берутся самые торгуемые товары (по числу сделок): пшеница 7, уголь 5
    assert тело['compare_mode'] == 'top'
    assert [s['code'] for s in тело['series']] == ['WH3', 'CL1']
    assert [r['code'] for r in тело['comparison']] == ['WH3', 'CL1']


def test_по_умолчанию_сравниваются_пять_самых_торгуемых(клиент, данные):
    for номер in range(6):
        товар = Product.objects.create(name=f'Товар {номер}', code=f'T{номер}')
        сделка(данные.ets, товар, Д1, price_avg=D(10), deals_count=номер + 10)
    тело = получить(клиент)
    assert [s['code'] for s in тело['series']] == ['T5', 'T4', 'T3', 'T2', 'T1']


def test_выбран_один_товар_все_секции_только_по_нему(клиент, данные):
    тело = получить(клиент, product=данные.уголь.id)
    assert тело['compare_mode'] == 'selected'
    assert тело['selected'] == [{'id': данные.уголь.id, 'name': 'Уголь каменный марки Д', 'code': 'CL1', 'tnved': '2701'}]
    assert тело['summary']['records'] == 2
    assert тело['summary']['total_amount'] == 5000.0
    assert тело['summary']['records_without_amount'] == 1
    assert [t['code'] for t in тело['top_products']] == ['CL1']
    assert [(t['name'], t['amount']) for t in тело['top_buyers']] == [('ТОО Гамма', 5000.0)]
    assert [(t['name'], t['amount']) for t in тело['top_sellers']] == [('ТОО Альфа', 5000.0)]
    assert [d['date'] for d in тело['turnover_by_day']] == ['2026-10-03']
    assert [s['code'] for s in тело['series']] == ['CL1']
    # старый блок графика цены тоже следует выбору
    assert тело['price_dynamics']['product']['code'] == 'CL1'


@pytest.mark.parametrize('способ', ['повтор', 'список', 'смешанный'])
def test_несколько_товаров_разными_способами(клиент, данные, способ):
    у, п = данные.уголь.id, данные.пшеница.id
    if способ == 'повтор':
        ответ = клиент.get('/api/analytics/', {'product': [у, п]})
    elif способ == 'список':
        ответ = клиент.get('/api/analytics/', {'products': f'{у}, {п}'})
    else:
        ответ = клиент.get(f'/api/analytics/?product={у}&products={п},{у}')
    тело = ответ.json()
    assert ответ.status_code == 200
    # порядок — как в запросе, повторы убраны
    assert [p['id'] for p in тело['selected']] == [у, п]
    assert [s['code'] for s in тело['series']] == ['CL1', 'WH3']
    assert тело['summary']['records'] == 8


def test_ряды_по_дням_для_каждого_товара(клиент, данные):
    тело = получить(клиент, product=[данные.пшеница.id, данные.уголь.id])
    пшеница, уголь = тело['series']
    assert пшеница['name'] == 'Пшеница мягкая 3 класс'
    assert пшеница['exchange_codes'] == ['ets']
    assert [(p['date'], p['avg_price'], p['amount'], p['volume']) for p in пшеница['points']] == [
        ('2026-10-01', 175.0, 7000.0, 40.0),
        ('2026-10-02', 200.0, 4000.0, None),
        ('2026-10-03', 100.0, None, None),
    ]
    assert уголь['exchange_codes'] == ['caspy', 'ets']
    # у2 без цены и суммы: день есть, цена — по у1
    assert уголь['points'] == [
        {'date': '2026-10-03', 'avg_price': 50.0, 'amount': 5000.0, 'volume': 100.0, 'records': 1},
    ]


def test_сравнение_товаров(клиент, данные):
    тело = получить(клиент, product=[данные.пшеница.id, данные.уголь.id])
    п = строки(тело)['WH3']
    assert п['records'] == 6
    assert п['deals'] == 7  # 2 + 1 и по одной сделке у записей без deals_count
    assert п['volume'] == 40.0
    assert п['amount'] == 11000.0
    assert п['avg_price'] == 183.33  # (1000 + 6000 + 1000 + 3000) / (10 + 30 + 10 + 10)
    assert (п['min_price'], п['max_price']) == (100.0, 500.0)
    assert (п['first_price'], п['first_date']) == (175.0, '2026-10-01')
    assert (п['last_price'], п['last_price_date']) == (100.0, '2026-10-03')
    assert п['change_pct'] == -42.86  # (100 - 175) / 175
    assert п['last_date'] == '2026-10-03'
    assert п['exchange_codes'] == ['ets']

    у = строки(тело)['CL1']
    assert (у['records'], у['deals'], у['amount'], у['volume']) == (2, 5, 5000.0, 100.0)
    assert (у['min_price'], у['max_price'], у['avg_price']) == (50.0, 50.0, 50.0)
    assert у['first_price'] == у['last_price'] == 50.0
    assert у['change_pct'] is None  # один день с ценой — изменения нет
    assert у['records_without_price'] == 1


def test_изменение_цены_положительное(клиент, данные):
    тело = получить(клиент, product=данные.пшеница.id, date_from='2026-10-01', date_to='2026-10-02')
    строка = тело['comparison'][0]
    assert (строка['first_price'], строка['last_price']) == (175.0, 200.0)
    assert строка['change_pct'] == 14.29
    assert строка['last_date'] == '2026-10-02'


def test_мин_и_макс_по_полям_мин_и_макс_цены(клиент, данные):
    Trade.objects.filter(pk=данные.записи['п1'].pk).update(price_min=D(90))
    Trade.objects.filter(pk=данные.записи['п5'].pk).update(price_max=D(650))
    строка = получить(клиент, product=данные.пшеница.id)['comparison'][0]
    assert (строка['min_price'], строка['max_price']) == (90.0, 650.0)


def test_выбранный_товар_без_сделок_в_выборке(клиент, данные):
    тело = получить(клиент, product=[данные.пшеница.id, данные.сахар.id], exchange='caspy')
    assert [p['code'] for p in тело['selected']] == ['WH3', 'SG1']
    assert тело['summary']['records'] == 0
    assert [s['points'] for s in тело['series']] == [[], []]
    for строка in тело['comparison']:
        assert строка['deals'] == 0 and строка['records'] == 0
        assert строка['amount'] is None and строка['avg_price'] is None
        assert строка['change_pct'] is None and строка['last_date'] is None


def test_неизвестные_id_игнорируются(клиент, данные):
    тело = получить(клиент, product=[999999, данные.уголь.id])
    assert тело['unknown_ids'] == [999999]
    assert [p['code'] for p in тело['selected']] == ['CL1']
    assert тело['summary']['records'] == 2
    # если не нашлось ни одного — как будто ничего не выбрано
    тело = получить(клиент, product=999999)
    assert тело['selected'] == [] and тело['compare_mode'] == 'top'
    assert тело['summary']['records'] == 8


def test_не_больше_десяти_товаров(клиент, данные):
    десять = [данные.пшеница.id, данные.уголь.id] + list(range(900001, 900009))
    assert клиент.get('/api/analytics/', {'product': десять}).status_code == 200
    ответ = клиент.get('/api/analytics/', {'product': десять + [900100]})
    assert ответ.status_code == 400
    assert 'не больше 10' in ответ.json()['error']
    ответ = клиент.get('/api/analytics/', {'products': ','.join(str(i) for i in range(1, 12))})
    assert ответ.status_code == 400
    # повторы не считаются
    assert клиент.get('/api/analytics/', {'product': [данные.уголь.id] * 15}).status_code == 200


@pytest.mark.parametrize('параметры', [{'product': 'abc'}, {'products': '1,x'}, {'product': '-3'}])
def test_некорректный_выбор_даёт_400(клиент, данные, параметры):
    ответ = клиент.get('/api/analytics/', параметры)
    assert ответ.status_code == 400
    assert 'product' in ответ.json()['error']


def test_совместимость_product_id_с_выбором(клиент, данные):
    # product_id без выбора: только товар графика, выборка не сужается
    тело = получить(клиент, product_id=данные.уголь.id)
    assert тело['summary']['records'] == 8
    assert тело['selected'] == [] and тело['compare_mode'] == 'top'
    # вместе с выбором: секции — по выбору, price_dynamics — по product_id
    тело = получить(клиент, product=данные.пшеница.id, product_id=данные.уголь.id)
    assert тело['summary']['records'] == 6
    assert тело['price_dynamics']['product']['code'] == 'CL1'
    assert тело['price_dynamics']['points'] == []  # угля нет в выбранной выборке


def test_совместимость_q_с_выбором(клиент, данные):
    тело = получить(клиент, q='уголь')
    assert тело['summary']['records'] == 2 and [s['code'] for s in тело['series']] == ['CL1']
    # q и выбор комбинируются через И
    тело = получить(клиент, q='уголь', product=[данные.пшеница.id, данные.уголь.id])
    assert тело['summary']['records'] == 2
    assert строки(тело)['WH3']['deals'] == 0
    assert тело['filters']['q'] == 'уголь'


def test_выбор_не_добавляет_запросов_на_товар(клиент, данные, django_assert_max_num_queries):
    товары = [Product.objects.create(name=f'Товар {n}', code=f'T{n}') for n in range(8)]
    for товар in товары:
        сделка(данные.ets, товар, Д1, price_avg=D(10), amount=D(100))
    with django_assert_max_num_queries(12):
        тело = получить(клиент, product=[данные.пшеница.id, данные.уголь.id] + [t.id for t in товары])
    assert len(тело['comparison']) == 10


# ------------------------------------------------- список товаров для выбора

def список(клиент, **параметры):
    ответ = клиент.get('/api/analytics/products/', параметры)
    assert ответ.status_code == 200, ответ.content
    тело = ответ.json()
    assert тело['count'] == len(тело['results'])
    return тело['results']


def test_список_товаров_для_выбора(клиент, данные):
    товары = список(клиент)
    assert товары == [
        {'id': данные.пшеница.id, 'name': 'Пшеница мягкая 3 класс', 'code': 'WH3', 'tnved': '1001',
         'exchange_codes': ['ets'], 'deals': 7, 'records': 6},
        {'id': данные.уголь.id, 'name': 'Уголь каменный марки Д', 'code': 'CL1', 'tnved': '2701',
         'exchange_codes': ['caspy', 'ets'], 'deals': 5, 'records': 2},
    ]  # сахар без сделок в список не попадает


def test_список_товаров_фильтр_по_бирже_и_периоду(клиент, данные):
    assert [(t['code'], t['deals'], t['exchange_codes']) for t in список(клиент, exchange='caspy')] == [
        ('CL1', 4, ['caspy']),
    ]
    assert [(t['code'], t['deals']) for t in список(клиент, date_from='2026-10-03')] == [('CL1', 5), ('WH3', 1)]
    assert [(t['code'], t['deals']) for t in список(клиент, date_to='2026-10-01')] == [('WH3', 3)]
    assert список(клиент, date_from='2027-01-01') == []


def test_список_товаров_поиск_на_сервере(клиент, данные):
    assert [t['code'] for t in список(клиент, q='ПШЕН')] == ['WH3']
    assert [t['code'] for t in список(клиент, q='wh3')] == ['WH3']  # по коду товара
    assert [t['code'] for t in список(клиент, q='2701')] == ['CL1']  # по коду ТН ВЭД
    assert [t['code'] for t in список(клиент, q='уголь мар')] == ['CL1']  # части слов
    assert список(клиент, q='нет такого') == []


def test_список_товаров_без_n_плюс_1(клиент, данные, django_assert_max_num_queries):
    for номер in range(30):
        сделка(данные.ets, Product.objects.create(name=f'Товар {номер}', code=f'T{номер}'), Д1)
    with django_assert_max_num_queries(2):
        assert len(список(клиент)) == 32


@pytest.mark.parametrize('параметры', [
    {'exchange': 'nasdaq'}, {'date_from': 'вчера'}, {'date_from': '2026-10-05', 'date_to': '2026-10-01'},
])
def test_список_товаров_ошибки(клиент, данные, параметры):
    ответ = клиент.get('/api/analytics/products/', параметры)
    assert ответ.status_code == 400 and ответ.json()['error']
    assert клиент.post('/api/analytics/products/').status_code == 405


# ---------------------------------------------------------------- страница

def test_страница_аналитики(клиент, данные):
    ответ = клиент.get('/analytics/')
    assert ответ.status_code == 200
    html = ответ.content.decode('utf-8')
    for элемент in ('chart-price', 'chart-turnover', 'chart-months', 'months-hint', 'chart-products', 'chart-parties',
                    'a-products', 'a-products-input', 'a-products-list', 'compare-table'):
        assert f'id="{элемент}"' in html
    assert 'role="combobox"' in html and 'role="listbox"' in html
    assert 'placeholder="Все товары"' in html
    # старые поля заменены одним выбором товаров
    assert 'id="a-q"' not in html and 'id="a-product_id"' not in html
    assert '/static/portal/vendor/chart.umd.min.js' in html
    assert 'cdn.' not in html and 'jsdelivr' not in html  # без CDN
    assert 'data-page="analytics"' in html
    assert 'Оборот по месяцам' in html


def test_скрипт_графика_по_месяцам_и_цвета_из_темы():
    from pathlib import Path
    скрипт = (Path(__file__).resolve().parent.parent / 'portal' / 'static' / 'portal' / 'app.js').read_text('utf-8')
    assert 'turnover_by_month' in скрипт and "ensureChart('months'" in скрипт
    # названия месяцев — из словаря переводов (12 названий через «|»), русские — исходные строки
    assert "_('янв|фев|мар|апр|май|июн|июл|авг|сен|окт|ноя|дек')" in скрипт
    assert 'месяц неполный: данные ' in скрипт
    # цвета графиков — из CSS-переменных, перерисовка при смене темы
    assert "'--chart-grid'" in скрипт and "'--series-'" in скрипт
    assert "addEventListener('portal:themechange'" in скрипт


def test_пункт_меню_аналитика_на_всех_страницах(клиент):
    for адрес in ('/', '/status/', '/analytics/'):
        html = клиент.get(адрес).content.decode('utf-8')
        assert 'href="/analytics/"' in html and 'Аналитика' in html
    assert 'class="active">Аналитика' in клиент.get('/analytics/').content.decode('utf-8')


def test_локальная_копия_chartjs_на_месте():
    from pathlib import Path
    файл = Path(__file__).resolve().parent.parent / 'portal' / 'static' / 'portal' / 'vendor' / 'chart.umd.min.js'
    assert файл.is_file() and файл.stat().st_size > 100_000
