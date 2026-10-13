"""Сводная аналитика по итогам торгов: расчёты для страницы /analytics/ и API /api/analytics/.

Фильтры (биржа, период, ключевое слово по товару) разбираются теми же функциями,
что и для /api/trades/ (см. queries.py), поэтому выборки совпадают.

Суммы сделок (amount) у части записей отсутствуют (NULL) — например, у части
данных ETS. Такие записи:
  * не попадают в суммы и в топы «по сумме» (SQL SUM игнорирует NULL);
  * считаются отдельно и выводятся как доля записей без суммы.
"""
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from django.db.models import Count, Q, QuerySet, Sum

from exchanges.models import Product, Trade

from .queries import ОшибкаПараметров, Фильтры, применить_фильтры, разобрать_фильтры

# Размеры списков в ответе
ТОП_ТОВАРОВ = 10
ТОП_УЧАСТНИКОВ = 10
ТОВАРОВ_В_СПИСКЕ = 30


def _сумма(значение) -> float:
    """Сумма из агрегата; NULL (нет ни одной суммы) считаем нулём."""
    return float(значение) if значение is not None else 0.0


def выборка_для_аналитики(params) -> tuple:
    """Разбирает параметры -> (queryset сделок с фильтрами, фильтры).

    Используются только биржа, период и ключевое слово ``q``; остальные
    фильтры /api/trades/ тоже работают, если их передать.
    """
    фильтры = разобрать_фильтры(params)
    if фильтры.date_from and фильтры.date_to and фильтры.date_from > фильтры.date_to:
        raise ОшибкаПараметров('Дата начала позже даты окончания')
    return применить_фильтры(Trade.objects.all(), фильтры), фильтры


def _разобрать_товар(params) -> tuple:
    """product_id / product_code -> (id или None, код или '')."""
    сырой_id = (params.get('product_id') or '').strip()
    код = (params.get('product_code') or '').strip()
    if not сырой_id:
        return None, код
    try:
        return int(сырой_id), код
    except ValueError:
        raise ОшибкаПараметров('Параметр product_id: ожидается целое число')


# --------------------------------------------------------------- секции ответа

def сводка(qs: QuerySet) -> dict:
    """Число записей, торговых дней, общая сумма и доля записей без суммы."""
    данные = qs.aggregate(
        записей=Count('id'),
        дней=Count('trade_date', distinct=True),
        сумма=Sum('amount'),
        без_суммы=Count('id', filter=Q(amount__isnull=True)),
        сделок=Sum('deals_count'),
    )
    записей = данные['записей']
    без_суммы = данные['без_суммы']
    return {
        'records': записей,
        'trading_days': данные['дней'],
        'total_amount': _сумма(данные['сумма']),
        'deals_total': int(данные['сделок'] or 0),
        'records_without_amount': без_суммы,
        'share_without_amount': round(без_суммы / записей, 4) if записей else 0.0,
    }


def список_товаров(qs: QuerySet, лимит: int = ТОВАРОВ_В_СПИСКЕ) -> list:
    """Товары для выбора графика цены: топ по числу записей."""
    строки = (
        qs.values('product_id', 'product__name', 'product__code')
        .annotate(n=Count('id'))
        .order_by('-n', 'product__name', 'product_id')[:лимит]
    )
    return [
        {'id': r['product_id'], 'code': r['product__code'], 'name': r['product__name'], 'records': r['n']}
        for r in строки
    ]


def _выбрать_товар(qs: QuerySet, товары: list, product_id: Optional[int], product_code: str):
    """Товар для графика цены: заданный параметром, иначе самый частый в выборке."""
    if product_id is not None:
        return Product.objects.filter(pk=product_id).first()
    if product_code:
        # Код не обязан быть уникальным: берём самый частый товар с этим кодом в выборке
        найден = (
            qs.filter(product__code=product_code)
            .values('product_id')
            .annotate(n=Count('id'))
            .order_by('-n', 'product_id')
            .first()
        )
        if найден:
            return Product.objects.filter(pk=найден['product_id']).first()
        return Product.objects.filter(code=product_code).order_by('id').first()
    if товары:
        return Product.objects.filter(pk=товары[0]['id']).first()
    return None


def динамика_цены(qs: QuerySet, товар: Optional[Product]) -> dict:
    """Средневзвешенная цена товара по дням.

    Вес записи — её объём. Если объёма нет (ETS его не публикует), объём выводится
    как сумма / цена; если нет и суммы, запись весит как единица (для такого дня
    берётся обычное среднее). Записи без цены пропускаются.
    """
    if товар is None:
        return {'product': None, 'points': [], 'records_without_price': 0}

    записи = qs.filter(product_id=товар.id)
    без_цены = записи.filter(Q(price_avg__isnull=True) | Q(price_avg__lte=0)).count()
    строки = записи.filter(price_avg__gt=0).values_list('trade_date', 'price_avg', 'quantity', 'amount')

    # день -> [сумма цена*вес, сумма весов, сумма цен, число записей]
    дни = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0), 0])
    for день, цена, объём, сумма in строки:
        if объём is not None and объём > 0:
            вес = объём
        elif сумма is not None and сумма > 0:
            вес = сумма / цена
        else:
            вес = Decimal(0)
        ячейка = дни[день]
        ячейка[0] += цена * вес
        ячейка[1] += вес
        ячейка[2] += цена
        ячейка[3] += 1

    точки = []
    for день in sorted(дни):
        произведение, вес, сумма_цен, число = дни[день]
        цена = произведение / вес if вес > 0 else сумма_цен / число
        точки.append({'date': день.isoformat(), 'price': round(float(цена), 2), 'records': число})
    return {
        'product': {'id': товар.id, 'code': товар.code, 'name': товар.name},
        'points': точки,
        'records_without_price': без_цены,
    }


def топ_товаров(qs: QuerySet, лимит: int = ТОП_ТОВАРОВ) -> list:
    """Топ товаров по сумме сделок (записи без суммы в топ не попадают)."""
    строки = (
        qs.filter(amount__isnull=False)
        .values('product_id', 'product__name', 'product__code')
        .annotate(сумма=Sum('amount'), n=Count('id'))
        .order_by('-сумма', 'product__name', 'product_id')[:лимит]
    )
    return [
        {
            'id': r['product_id'],
            'code': r['product__code'],
            'name': r['product__name'],
            'amount': _сумма(r['сумма']),
            'records': r['n'],
        }
        for r in строки
    ]


def топ_участников(qs: QuerySet, роль: str, лимит: int = ТОП_УЧАСТНИКОВ) -> list:
    """Топ покупателей (роль 'buyer') или продавцов ('seller') по сумме сделок."""
    if роль not in ('buyer', 'seller'):
        raise ValueError('роль: buyer или seller')
    строки = (
        qs.filter(amount__isnull=False, **{f'{роль}__isnull': False})
        .values(f'{роль}_id', f'{роль}__name', f'{роль}__bin')
        .annotate(сумма=Sum('amount'), n=Count('id'))
        .order_by('-сумма', f'{роль}__name', f'{роль}_id')[:лимит]
    )
    return [
        {
            'id': r[f'{роль}_id'],
            'name': r[f'{роль}__name'],
            'bin': r[f'{роль}__bin'],
            'amount': _сумма(r['сумма']),
            'records': r['n'],
        }
        for r in строки
    ]


def оборот_по_дням(qs: QuerySet) -> list:
    """Оборот по дням: сумма, число сделок, число записей, записей без суммы."""
    строки = (
        qs.values('trade_date')
        .annotate(
            сумма=Sum('amount'),
            сделок=Sum('deals_count'),
            n=Count('id'),
            без_суммы=Count('id', filter=Q(amount__isnull=True)),
        )
        .order_by('trade_date')
    )
    return [
        {
            'date': r['trade_date'].isoformat(),
            'amount': _сумма(r['сумма']),
            'deals': int(r['сделок'] or 0),
            'records': r['n'],
            'records_without_amount': r['без_суммы'],
        }
        for r in строки
    ]


def аналитика(params) -> dict:
    """Полный ответ /api/analytics/; при некорректных параметрах бросает ОшибкаПараметров."""
    qs, фильтры = выборка_для_аналитики(params)
    product_id, product_code = _разобрать_товар(params)

    товары = список_товаров(qs)
    товар = _выбрать_товар(qs, товары, product_id, product_code)
    return {
        'filters': _фильтры_в_словарь(фильтры),
        'summary': сводка(qs),
        'products': товары,
        'price_dynamics': динамика_цены(qs, товар),
        'top_products': топ_товаров(qs),
        'top_buyers': топ_участников(qs, 'buyer'),
        'top_sellers': топ_участников(qs, 'seller'),
        'turnover_by_day': оборот_по_дням(qs),
    }


def _фильтры_в_словарь(ф: Фильтры) -> dict:
    """Применённые фильтры (для отображения на странице)."""
    return {
        'exchange': ф.exchange,
        'date_from': ф.date_from.isoformat() if ф.date_from else None,
        'date_to': ф.date_to.isoformat() if ф.date_to else None,
        'q': ф.q,
    }
