"""Сводная аналитика по итогам торгов: расчёты для страницы /analytics/ и API /api/analytics/.

Фильтры (биржа, период, ключевое слово по товару) разбираются теми же функциями,
что и для /api/trades/ (см. queries.py), поэтому выборки совпадают.

Выбор товаров (множественный, как в 1С):
  * ``product=ID`` (повторяемый) и/или ``products=1,2,3`` — выбранные товары;
  * ничего не выбрано — все товары выборки;
  * выбрано — все секции (сводка, оборот, топы) считаются только по выбранным,
    а график цены и таблица сравнения строятся по каждому выбранному товару
    отдельно (цены разных товаров в разных единицах, в одну линию не сводятся);
  * не больше ``MAX_SELECTED`` товаров (иначе ответ 400).
Старые параметры ``product_id`` / ``product_code`` по-прежнему выбирают товар
для блока ``price_dynamics`` и выборку не сужают; ``q`` — ключевое слово по названию.

Оборот считается по дням (``turnover_by_day``) и по месяцам (``turnover_by_month``,
группировка в SQL); месяцы на краях периода, покрытые не целиком, помечаются.

Суммы сделок (amount) у части записей отсутствуют (NULL) — например, у части
данных ETS. Такие записи:
  * не попадают в суммы и в топы «по сумме» (SQL SUM игнорирует NULL);
  * считаются отдельно и выводятся как доля записей без суммы.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from django.db.models import Count, F, IntegerField, Max, Min, Q, QuerySet, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils.translation import gettext as _, gettext_lazy

from exchanges.models import Product, Trade

from .queries import ParamError, TradeFilters, apply_filters, parse_filters

# Размеры списков в ответе
TOP_PRODUCTS_LIMIT = 10
TOP_PARTICIPANTS_LIMIT = 10
PRODUCT_LIST_LIMIT = 30
# Сколько товаров можно выбрать для сравнения
MAX_SELECTED = 10
# Сколько самых торгуемых товаров показывать, если ничего не выбрано
DEFAULT_COMPARE_COUNT = 5

MAX_SELECTED_MESSAGE = gettext_lazy(
    'Можно выбрать не больше %(max)s товаров для сравнения — снимите отметку с лишних'
)


def _deals():
    """Число сделок: поле deals_count, а где его нет — одна запись = одна сделка."""
    return Sum(Coalesce('deals_count', 1, output_field=IntegerField()))


def _amount_or_zero(value) -> float:
    """Сумма из агрегата; NULL (нет ни одной суммы) считаем нулём."""
    return float(value) if value is not None else 0.0


def _float_or_none(value, digits: int = 2):
    return round(float(value), digits) if value is not None else None


def _check_period(filters: TradeFilters):
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise ParamError(_('Дата начала позже даты окончания'))


def analytics_queryset(params) -> tuple:
    """Разбирает параметры -> (queryset сделок с фильтрами, фильтры).

    Используются биржа, период и ключевое слово ``q``; остальные фильтры
    /api/trades/ тоже работают, если их передать. Выбор товаров сюда не входит.
    """
    filters = parse_filters(params)
    _check_period(filters)
    return apply_filters(Trade.objects.all(), filters), filters


def _getlist(params, name) -> list:
    if hasattr(params, 'getlist'):
        return params.getlist(name)
    value = params.get(name)
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def parse_selected(params) -> list:
    """``product`` (повторяемый) и ``products`` (через запятую) -> список id без повторов.

    Порядок — как в запросе (от него зависит порядок линий и строк сравнения).
    """
    raw = list(_getlist(params, 'product'))
    for chunk in _getlist(params, 'products'):
        raw.extend(str(chunk).split(','))
    ids = []
    for value in raw:
        value = str(value).strip()
        if not value:
            continue
        try:
            number = int(value)
        except ValueError:
            raise ParamError(_('Параметры product / products: ожидаются целые числа (id товаров)'))
        if number <= 0:
            raise ParamError(_('Параметры product / products: ожидаются положительные id товаров'))
        if number not in ids:
            ids.append(number)
    if len(ids) > MAX_SELECTED:
        raise ParamError(str(MAX_SELECTED_MESSAGE) % {'max': MAX_SELECTED})
    return ids


def _parse_product(params) -> tuple:
    """Старые product_id / product_code -> (id или None, код или '')."""
    raw_id = (params.get('product_id') or '').strip()
    code = (params.get('product_code') or '').strip()
    if not raw_id:
        return None, code
    try:
        return int(raw_id), code
    except ValueError:
        raise ParamError(_('Параметр product_id: ожидается целое число'))


def _product_dict(product: Product) -> dict:
    return {'id': product.id, 'name': product.name, 'code': product.code, 'tnved': product.tnved}


# ------------------------------------------------------- список товаров (комбобокс)

def _search_condition(text: str) -> Q:
    """Каждое слово — в названии, коде товара или коде ТН ВЭД (без учёта регистра)."""
    condition = Q()
    for word in text.split():
        condition &= (
            Q(product__name__rucontains=word)
            | Q(product__code__rucontains=word)
            | Q(product__tnved__contains=word)
        )
    return condition


def products_for_picker(params) -> list:
    """Все товары, у которых есть записи в выборке (биржа и период), по убыванию числа сделок.

    ``q`` здесь — поиск по названию, коду товара и коду ТН ВЭД.
    Два запроса к БД независимо от числа товаров.
    """
    filters = parse_filters(params)
    _check_period(filters)
    search = filters.q
    filters.q = ''
    qs = apply_filters(Trade.objects.all(), filters)
    if search:
        qs = qs.filter(_search_condition(search))

    totals = {}
    rows = qs.values('product_id', 'exchange__code').annotate(n=Count('id'), deals=_deals())
    for row in rows:
        item = totals.setdefault(row['product_id'], {'records': 0, 'deals': 0, 'exchanges': set()})
        item['records'] += row['n']
        item['deals'] += int(row['deals'] or 0)
        item['exchanges'].add(row['exchange__code'])

    products = Product.objects.filter(pk__in=totals).only('id', 'name', 'code', 'tnved')
    result = []
    for product in products:
        item = totals[product.id]
        result.append({
            **_product_dict(product),
            'exchange_codes': sorted(item['exchanges']),
            'deals': item['deals'],
            'records': item['records'],
        })
    result.sort(key=lambda p: (-p['deals'], -p['records'], p['name'].lower(), p['id']))
    return result


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
    """Самые частые товары выборки (по числу записей); оставлено для совместимости."""
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
    """Товар для блока price_dynamics: заданный параметром, иначе самый частый в выборке."""
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


def _weight(price, quantity, amount):
    """Вес записи для средневзвешенной цены.

    Объём; если его нет (ETS его не публикует) — сумма / цена; если нет и суммы — 0
    (такие записи участвуют только в простом среднем дня без весов).
    """
    if quantity is not None and quantity > 0:
        return quantity
    if amount is not None and amount > 0:
        return amount / price
    return Decimal(0)


class _PriceBucket:
    """Накопитель для средневзвешенной цены (за день или за период)."""

    __slots__ = ('weighted', 'weights', 'prices', 'count')

    def __init__(self):
        self.weighted = Decimal(0)
        self.weights = Decimal(0)
        self.prices = Decimal(0)
        self.count = 0

    def add(self, price, quantity, amount):
        weight = _weight(price, quantity, amount)
        self.weighted += price * weight
        self.weights += weight
        self.prices += price
        self.count += 1

    def value(self):
        if not self.count:
            return None
        return self.weighted / self.weights if self.weights > 0 else self.prices / self.count


def price_dynamics(qs: QuerySet, product: Optional[Product]) -> dict:
    """Средневзвешенная цена одного товара по дням (старый формат ответа).

    Вес записи — её объём. Если объёма нет (ETS его не публикует), объём выводится
    как сумма / цена; если нет и суммы, запись весит как единица (для такого дня
    берётся обычное среднее). Записи без цены пропускаются.
    """
    if product is None:
        return {'product': None, 'points': [], 'records_without_price': 0}

    records = qs.filter(product_id=product.id)
    without_price = records.filter(Q(price_avg__isnull=True) | Q(price_avg__lte=0)).count()
    rows = records.filter(price_avg__gt=0).values_list('trade_date', 'price_avg', 'quantity', 'amount')

    days = defaultdict(_PriceBucket)
    for day, price, quantity, amount in rows:
        days[day].add(price, quantity, amount)

    points = [
        {'date': day.isoformat(), 'price': round(float(days[day].value()), 2), 'records': days[day].count}
        for day in sorted(days)
    ]
    return {
        'product': {'id': product.id, 'code': product.code, 'name': product.name},
        'points': points,
        'records_without_price': without_price,
    }


def top_traded_ids(qs: QuerySet, limit: int = DEFAULT_COMPARE_COUNT) -> list:
    """Самые торгуемые товары выборки: по числу сделок, затем по сумме."""
    rows = (
        qs.values('product_id')
        .annotate(deals=_deals(), total=Sum('amount'), n=Count('id'))
        .order_by('-deals', '-n', F('total').desc(nulls_last=True), 'product_id')[:limit]
    )
    return [r['product_id'] for r in rows]


def compare_products(qs: QuerySet, products: list) -> tuple:
    """Ряды по дням и строки таблицы сравнения для каждого товара отдельно.

    ``products`` — объекты Product в нужном порядке. Возвращает (series, comparison).
    Один запрос к БД на все товары.
    """
    ids = [p.id for p in products]
    stats = {
        pid: {
            'days': defaultdict(lambda: {'price': _PriceBucket(), 'amount': None, 'volume': None}),
            'period': _PriceBucket(),
            'records': 0, 'deals': 0, 'amount': None, 'volume': None,
            'min': None, 'max': None, 'last_date': None, 'exchanges': set(),
            'without_price': 0,
        }
        for pid in ids
    }
    rows = qs.filter(product_id__in=ids).values_list(
        'product_id', 'trade_date', 'price_avg', 'price_min', 'price_max',
        'quantity', 'amount', 'deals_count', 'exchange__code',
    )
    for pid, day, price, p_min, p_max, quantity, amount, deals, exchange in rows:
        item = stats[pid]
        bucket = item['days'][day]
        item['records'] += 1
        item['deals'] += deals if deals is not None else 1
        item['exchanges'].add(exchange)
        if item['last_date'] is None or day > item['last_date']:
            item['last_date'] = day
        if amount is not None:
            item['amount'] = (item['amount'] or Decimal(0)) + amount
            bucket['amount'] = (bucket['amount'] or Decimal(0)) + amount
        if quantity is not None:
            item['volume'] = (item['volume'] or Decimal(0)) + quantity
            bucket['volume'] = (bucket['volume'] or Decimal(0)) + quantity
        if price is None or price <= 0:
            item['without_price'] += 1
            continue
        bucket['price'].add(price, quantity, amount)
        item['period'].add(price, quantity, amount)
        low = p_min if p_min is not None and p_min > 0 else price
        high = p_max if p_max is not None and p_max > 0 else price
        item['min'] = low if item['min'] is None else min(item['min'], low)
        item['max'] = high if item['max'] is None else max(item['max'], high)

    series, comparison = [], []
    for product in products:
        item = stats[product.id]
        exchanges = sorted(item['exchanges'])
        points, priced_days = [], []
        for day in sorted(item['days']):
            bucket = item['days'][day]
            price = bucket['price'].value()
            points.append({
                'date': day.isoformat(),
                'avg_price': _float_or_none(price),
                'amount': _float_or_none(bucket['amount']),
                'volume': _float_or_none(bucket['volume'], 3),
                'records': bucket['price'].count,
            })
            if price is not None:
                priced_days.append((day, price))
        series.append({
            **_product_dict(product),
            'exchange_codes': exchanges,
            'points': points,
        })

        first = priced_days[0] if priced_days else None
        last = priced_days[-1] if priced_days else None
        change = None
        if len(priced_days) >= 2 and first[1] > 0:
            change = round(float((last[1] - first[1]) / first[1] * 100), 2)
        comparison.append({
            **_product_dict(product),
            'exchange_codes': exchanges,
            'records': item['records'],
            'deals': item['deals'],
            'volume': _float_or_none(item['volume'], 3),
            'amount': _float_or_none(item['amount']),
            'avg_price': _float_or_none(item['period'].value()),
            'min_price': _float_or_none(item['min']),
            'max_price': _float_or_none(item['max']),
            'first_price': _float_or_none(first[1]) if first else None,
            'first_date': first[0].isoformat() if first else None,
            'last_price': _float_or_none(last[1]) if last else None,
            'last_price_date': last[0].isoformat() if last else None,
            'change_pct': change,
            'last_date': item['last_date'].isoformat() if item['last_date'] else None,
            'records_without_price': item['without_price'],
        })
    return series, comparison


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


def _month_end(month_start: date) -> date:
    return date(month_start.year, month_start.month, monthrange(month_start.year, month_start.month)[1])


def _has_weekdays(first: date, last: date) -> bool:
    """Есть ли будни (пн–пт) среди дней first..last включительно; выходные торгов не дают."""
    day = first
    while day <= last:
        if day.weekday() < 5:
            return True
        day += timedelta(days=1)
    return False


def turnover_by_month(qs: QuerySet, coverage: Optional[tuple] = None) -> list:
    """Оборот по месяцам: сумма, число сделок, число записей, записей без суммы.

    Группировка — в SQL (TruncMonth), один запрос. ``coverage`` — (первый, последний)
    день данных выборки без учёта выбора товаров; если не передан, берётся из самих
    месяцев. Месяц на краю периода, покрытый не целиком, помечается
    ``partial_start`` / ``partial_end`` (выходные на краю месяца пробелом не считаются),
    а ``period_from`` / ``period_to`` — какие дни месяца попали в период.
    """
    rows = list(
        qs.annotate(month=TruncMonth('trade_date'))
        .values('month')
        .annotate(
            total=Sum('amount'),
            deals=Sum('deals_count'),
            n=Count('id'),
            without_amount=Count('id', filter=Q(amount__isnull=True)),
            first=Min('trade_date'),
            last=Max('trade_date'),
        )
        .order_by('month')
    )
    if not rows:
        return []
    if coverage is None or coverage[0] is None:
        coverage = (min(r['first'] for r in rows), max(r['last'] for r in rows))
    begin, end = coverage

    result = []
    for r in rows:
        month = r['month']
        if isinstance(month, datetime):  # на некоторых СУБД TruncMonth даёт datetime
            month = month.date()
        month_start = month.replace(day=1)
        month_end = _month_end(month_start)
        period_from = max(month_start, begin)
        period_to = min(month_end, end)
        partial_start = period_from > month_start and _has_weekdays(month_start, period_from - timedelta(days=1))
        partial_end = period_to < month_end and _has_weekdays(period_to + timedelta(days=1), month_end)
        result.append({
            'month': month_start.strftime('%Y-%m'),
            'amount': _amount_or_zero(r['total']),
            'deals': int(r['deals'] or 0),
            'records': r['n'],
            'records_without_amount': r['without_amount'],
            'period_from': period_from.isoformat(),
            'period_to': period_to.isoformat(),
            'partial_start': partial_start,
            'partial_end': partial_end,
        })
    return result


def build_analytics(params) -> dict:
    """Полный ответ /api/analytics/; при некорректных параметрах бросает ParamError."""
    base_qs, filters = analytics_queryset(params)
    selected_ids = parse_selected(params)
    product_id, product_code = _parse_product(params)

    found = Product.objects.in_bulk(selected_ids) if selected_ids else {}
    selected = [found[pid] for pid in selected_ids if pid in found]
    unknown_ids = [pid for pid in selected_ids if pid not in found]

    qs = base_qs.filter(product_id__in=[p.id for p in selected]) if selected else base_qs

    if selected:
        compared, mode = selected, 'selected'
    else:
        top_ids = top_traded_ids(qs)
        by_id = Product.objects.in_bulk(top_ids)
        compared, mode = [by_id[pid] for pid in top_ids if pid in by_id], 'top'
    series, comparison = compare_products(qs, compared)

    # Границы данных для пометки неполных месяцев — без учёта выбора товаров:
    # товар мог просто не торговаться в начале месяца, месяц от этого не неполный.
    coverage = None
    if selected:
        bounds = base_qs.aggregate(first=Min('trade_date'), last=Max('trade_date'))
        coverage = (bounds['first'], bounds['last'])

    products = product_list(qs)
    if product_id is None and not product_code and selected:
        product = selected[0]
    else:
        product = _pick_product(qs, products, product_id, product_code)
    return {
        'filters': _filters_to_dict(filters),
        'selected': [_product_dict(p) for p in selected],
        'unknown_ids': unknown_ids,
        'compare_mode': mode,
        'summary': summary(qs),
        'products': products,
        'price_dynamics': price_dynamics(qs, product),
        'series': series,
        'comparison': comparison,
        'top_products': top_products(qs),
        'top_buyers': top_participants(qs, 'buyer'),
        'top_sellers': top_participants(qs, 'seller'),
        'turnover_by_day': turnover_by_day(qs),
        'turnover_by_month': turnover_by_month(qs, coverage),
    }


def _filters_to_dict(f: TradeFilters) -> dict:
    """Применённые фильтры (для отображения на странице)."""
    return {
        'exchange': f.exchange,
        'date_from': f.date_from.isoformat() if f.date_from else None,
        'date_to': f.date_to.isoformat() if f.date_to else None,
        'q': f.q,
    }
