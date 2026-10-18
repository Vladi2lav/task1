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

from .queries import ParamError, TradeFilters, apply_filters, parse_filters

# Размеры списков в ответе
TOP_PRODUCTS_LIMIT = 10
TOP_PARTICIPANTS_LIMIT = 10
PRODUCT_LIST_LIMIT = 30


def _amount_or_zero(value) -> float:
    """Сумма из агрегата; NULL (нет ни одной суммы) считаем нулём."""
    return float(value) if value is not None else 0.0


def analytics_queryset(params) -> tuple:
    """Разбирает параметры -> (queryset сделок с фильтрами, фильтры).

    Используются только биржа, период и ключевое слово ``q``; остальные
    фильтры /api/trades/ тоже работают, если их передать.
    """
    filters = parse_filters(params)
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise ParamError('Дата начала позже даты окончания')
    return apply_filters(Trade.objects.all(), filters), filters


def _parse_product(params) -> tuple:
    """product_id / product_code -> (id или None, код или '')."""
    raw_id = (params.get('product_id') or '').strip()
    code = (params.get('product_code') or '').strip()
    if not raw_id:
        return None, code
    try:
        return int(raw_id), code
    except ValueError:
        raise ParamError('Параметр product_id: ожидается целое число')


# --------------------------------------------------------------- секции ответа

def summary(qs: QuerySet) -> dict:
    """Число записей, торговых дней, общая сумма и доля записей без суммы."""
    data = qs.aggregate(
        records=Count('id'),
        days=Count('trade_date', distinct=True),
        total=Sum('amount'),
        without_amount=Count('id', filter=Q(amount__isnull=True)),
        deals=Sum('deals_count'),
    )
    records = data['records']
    without_amount = data['without_amount']
    return {
        'records': records,
        'trading_days': data['days'],
        'total_amount': _amount_or_zero(data['total']),
        'deals_total': int(data['deals'] or 0),
        'records_without_amount': without_amount,
        'share_without_amount': round(without_amount / records, 4) if records else 0.0,
    }


def product_list(qs: QuerySet, limit: int = PRODUCT_LIST_LIMIT) -> list:
    """Товары для выбора графика цены: топ по числу записей."""
    rows = (
        qs.values('product_id', 'product__name', 'product__code')
        .annotate(n=Count('id'))
        .order_by('-n', 'product__name', 'product_id')[:limit]
    )
    return [
        {'id': r['product_id'], 'code': r['product__code'], 'name': r['product__name'], 'records': r['n']}
        for r in rows
    ]


def _pick_product(qs: QuerySet, products: list, product_id: Optional[int], product_code: str):
    """Товар для графика цены: заданный параметром, иначе самый частый в выборке."""
    if product_id is not None:
        return Product.objects.filter(pk=product_id).first()
    if product_code:
        # Код не обязан быть уникальным: берём самый частый товар с этим кодом в выборке
        found = (
            qs.filter(product__code=product_code)
            .values('product_id')
            .annotate(n=Count('id'))
            .order_by('-n', 'product_id')
            .first()
        )
        if found:
            return Product.objects.filter(pk=found['product_id']).first()
        return Product.objects.filter(code=product_code).order_by('id').first()
    if products:
        return Product.objects.filter(pk=products[0]['id']).first()
    return None


def price_dynamics(qs: QuerySet, product: Optional[Product]) -> dict:
    """Средневзвешенная цена товара по дням.

    Вес записи — её объём. Если объёма нет (ETS его не публикует), объём выводится
    как сумма / цена; если нет и суммы, запись весит как единица (для такого дня
    берётся обычное среднее). Записи без цены пропускаются.
    """
    if product is None:
        return {'product': None, 'points': [], 'records_without_price': 0}

    records = qs.filter(product_id=product.id)
    without_price = records.filter(Q(price_avg__isnull=True) | Q(price_avg__lte=0)).count()
    rows = records.filter(price_avg__gt=0).values_list('trade_date', 'price_avg', 'quantity', 'amount')

    # день -> [сумма цена*вес, сумма весов, сумма цен, число записей]
    days = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0), 0])
    for day, price, quantity, amount in rows:
        if quantity is not None and quantity > 0:
            weight = quantity
        elif amount is not None and amount > 0:
            weight = amount / price
        else:
            weight = Decimal(0)
        bucket = days[day]
        bucket[0] += price * weight
        bucket[1] += weight
        bucket[2] += price
        bucket[3] += 1

    points = []
    for day in sorted(days):
        weighted_sum, weight_total, price_sum, count = days[day]
        price = weighted_sum / weight_total if weight_total > 0 else price_sum / count
        points.append({'date': day.isoformat(), 'price': round(float(price), 2), 'records': count})
    return {
        'product': {'id': product.id, 'code': product.code, 'name': product.name},
        'points': points,
        'records_without_price': without_price,
    }


def top_products(qs: QuerySet, limit: int = TOP_PRODUCTS_LIMIT) -> list:
    """Топ товаров по сумме сделок (записи без суммы в топ не попадают)."""
    rows = (
        qs.filter(amount__isnull=False)
        .values('product_id', 'product__name', 'product__code')
        .annotate(total=Sum('amount'), n=Count('id'))
        .order_by('-total', 'product__name', 'product_id')[:limit]
    )
    return [
        {
            'id': r['product_id'],
            'code': r['product__code'],
            'name': r['product__name'],
            'amount': _amount_or_zero(r['total']),
            'records': r['n'],
        }
        for r in rows
    ]


def top_participants(qs: QuerySet, role: str, limit: int = TOP_PARTICIPANTS_LIMIT) -> list:
    """Топ покупателей (роль 'buyer') или продавцов ('seller') по сумме сделок."""
    if role not in ('buyer', 'seller'):
        raise ValueError('роль: buyer или seller')
    rows = (
        qs.filter(amount__isnull=False, **{f'{role}__isnull': False})
        .values(f'{role}_id', f'{role}__name', f'{role}__bin')
        .annotate(total=Sum('amount'), n=Count('id'))
        .order_by('-total', f'{role}__name', f'{role}_id')[:limit]
    )
    return [
        {
            'id': r[f'{role}_id'],
            'name': r[f'{role}__name'],
            'bin': r[f'{role}__bin'],
            'amount': _amount_or_zero(r['total']),
            'records': r['n'],
        }
        for r in rows
    ]


def turnover_by_day(qs: QuerySet) -> list:
    """Оборот по дням: сумма, число сделок, число записей, записей без суммы."""
    rows = (
        qs.values('trade_date')
        .annotate(
            total=Sum('amount'),
            deals=Sum('deals_count'),
            n=Count('id'),
            without_amount=Count('id', filter=Q(amount__isnull=True)),
        )
        .order_by('trade_date')
    )
    return [
        {
            'date': r['trade_date'].isoformat(),
            'amount': _amount_or_zero(r['total']),
            'deals': int(r['deals'] or 0),
            'records': r['n'],
            'records_without_amount': r['without_amount'],
        }
        for r in rows
    ]


def build_analytics(params) -> dict:
    """Полный ответ /api/analytics/; при некорректных параметрах бросает ParamError."""
    qs, filters = analytics_queryset(params)
    product_id, product_code = _parse_product(params)

    products = product_list(qs)
    product = _pick_product(qs, products, product_id, product_code)
    return {
        'filters': _filters_to_dict(filters),
        'summary': summary(qs),
        'products': products,
        'price_dynamics': price_dynamics(qs, product),
        'top_products': top_products(qs),
        'top_buyers': top_participants(qs, 'buyer'),
        'top_sellers': top_participants(qs, 'seller'),
        'turnover_by_day': turnover_by_day(qs),
    }


def _filters_to_dict(f: TradeFilters) -> dict:
    """Применённые фильтры (для отображения на странице)."""
    return {
        'exchange': f.exchange,
        'date_from': f.date_from.isoformat() if f.date_from else None,
        'date_to': f.date_to.isoformat() if f.date_to else None,
        'q': f.q,
    }
