"""Разбор параметров запроса, фильтрация и сортировка итогов торгов и новостей.

Один и тот же набор параметров используют JSON API, экспорт в Excel и страница
в браузере, поэтому логика собрана здесь и не дублируется во views.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.db.models import F, Q, QuerySet

from exchanges.models import News, Trade

# Допустимые коды бирж в фильтре
EXCHANGE_CODES = ('ets', 'caspy')

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MIN = 10
PAGE_SIZE_MAX = 200

# Пагинация ленты новостей
NEWS_PAGE_SIZE_DEFAULT = 20
NEWS_PAGE_SIZE_MIN = 1
NEWS_PAGE_SIZE_MAX = 100

# Сортировка по умолчанию: свежие торги сверху
DEFAULT_ORDERING = '-trade_date'

# Белый список полей сортировки: имя в API -> поле в БД
ORDERING_FIELDS = {
    'trade_date': 'trade_date',
    'exchange': 'exchange__name',
    'product': 'product__name',
    'buyer': 'buyer__name',
    'seller': 'seller__name',
    'buyer_broker': 'buyer_broker__name',
    'seller_broker': 'seller_broker__name',
    'deals_count': 'deals_count',
    'quantity': 'quantity',
    'price_open': 'price_open',
    'price_close': 'price_close',
    'price_min': 'price_min',
    'price_max': 'price_max',
    'price_avg': 'price_avg',
    'price_with_vat': 'price_with_vat',
    'price_without_vat': 'price_without_vat',
    'amount': 'amount',
    'delivery_basis': 'delivery_basis',
    'delivery_place': 'delivery_place',
    'delivery_term': 'delivery_term',
}


class ParamError(ValueError):
    """Некорректный параметр запроса (превращается в ответ 400)."""


@dataclass
class TradeFilters:
    """Разобранные и проверенные фильтры выборки."""

    exchange: str = ''
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    q: str = ''
    party: str = ''
    broker: str = ''
    amount_min: Optional[Decimal] = None
    amount_max: Optional[Decimal] = None
    delivery: str = ''


def _text_param(params, name):
    return (params.get(name) or '').strip()


def _parse_date(params, name):
    value = _text_param(params, name)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ParamError(f'Параметр {name}: ожидается дата в формате ГГГГ-ММ-ДД')


def _parse_amount(params, name):
    value = _text_param(params, name).replace(' ', '').replace(',', '.')
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ParamError(f'Параметр {name}: ожидается число')
    if not number.is_finite() or abs(number) >= Decimal('1e20'):
        raise ParamError(f'Параметр {name}: ожидается число')
    return number


def parse_exchange(params) -> str:
    """Код биржи из параметра exchange: ets, caspy или '' (обе биржи)."""
    exchange = _text_param(params, 'exchange').lower()
    if exchange in ('all', 'both'):
        exchange = ''
    if exchange and exchange not in EXCHANGE_CODES:
        raise ParamError('Параметр exchange: допустимы ets, caspy или пусто')
    return exchange


def parse_filters(params) -> TradeFilters:
    """Читает фильтры из QueryDict/словаря; при ошибке бросает ParamError."""
    return TradeFilters(
        exchange=parse_exchange(params),
        date_from=_parse_date(params, 'date_from'),
        date_to=_parse_date(params, 'date_to'),
        q=_text_param(params, 'q'),
        party=_text_param(params, 'party'),
        broker=_text_param(params, 'broker'),
        amount_min=_parse_amount(params, 'amount_min'),
        amount_max=_parse_amount(params, 'amount_max'),
        delivery=_text_param(params, 'delivery'),
    )


def parse_ordering(params) -> str:
    """Возвращает нормализованное значение ordering ('-amount', 'product' ...)."""
    raw = _text_param(params, 'ordering') or DEFAULT_ORDERING
    field = raw.lstrip('-')
    if field not in ORDERING_FIELDS:
        raise ParamError(
            'Параметр ordering: недопустимое поле. Допустимы: ' + ', '.join(ORDERING_FIELDS)
        )
    return ('-' if raw.startswith('-') else '') + field


def parse_page(params, default_size=PAGE_SIZE_DEFAULT, min_size=PAGE_SIZE_MIN, max_size=PAGE_SIZE_MAX) -> tuple:
    """Возвращает (page, page_size); размер страницы ограничивается min_size–max_size (для торгов 10–200)."""
    try:
        page = int(_text_param(params, 'page') or 1)
        size = int(_text_param(params, 'page_size') or default_size)
    except ValueError:
        raise ParamError('Параметры page и page_size должны быть целыми числами')
    size = max(min_size, min(max_size, size))
    return max(1, page), size


def _words(text):
    return [word for word in text.split() if word]


def trades_queryset() -> QuerySet:
    """Базовый запрос с подгрузкой связанных таблиц (без N+1)."""
    return Trade.objects.select_related(
        'exchange', 'product', 'buyer', 'seller', 'buyer_broker', 'seller_broker'
    )


def delivery_condition(text: str) -> Q:
    """Условие фильтра «место поставки».

    Ищем подстроку (без учёта регистра, в том числе кириллицы) в месте поставки
    или в базисе поставки. У ETS отдельного места нет — станция и базис записаны
    в названии товара, поэтому для записей без места смотрим и название товара.
    """
    return (
        Q(delivery_place__rucontains=text)
        | Q(delivery_basis__rucontains=text)
        | (Q(delivery_place='') & Q(product__name__rucontains=text))
    )


def apply_filters(qs: QuerySet, f: TradeFilters) -> QuerySet:
    """Накладывает фильтры; все условия комбинируются через И."""
    if f.exchange:
        qs = qs.filter(exchange__code=f.exchange)
    if f.date_from:
        qs = qs.filter(trade_date__gte=f.date_from)
    if f.date_to:
        qs = qs.filter(trade_date__lte=f.date_to)
    # Ключевое слово: каждое слово должно встретиться в названии товара
    for word in _words(f.q):
        qs = qs.filter(product__name__rucontains=word)
    if f.party:
        # Продавец или покупатель: по наименованию или по БИН
        qs = qs.filter(
            Q(buyer__name__rucontains=f.party)
            | Q(seller__name__rucontains=f.party)
            | Q(buyer__bin__contains=f.party)
            | Q(seller__bin__contains=f.party)
        )
    if f.broker:
        qs = qs.filter(
            Q(buyer_broker__name__rucontains=f.broker) | Q(seller_broker__name__rucontains=f.broker)
        )
    if f.amount_min is not None:
        qs = qs.filter(amount__gte=f.amount_min)
    if f.amount_max is not None:
        qs = qs.filter(amount__lte=f.amount_max)
    if f.delivery:
        qs = qs.filter(delivery_condition(f.delivery))
    return qs


def apply_ordering(qs: QuerySet, ordering: str) -> QuerySet:
    """Сортировка по полю из белого списка; пустые значения всегда в конце.

    В конец добавляются дата и id, чтобы порядок строк был стабильным
    (иначе страницы при пагинации могут «плавать»).
    """
    descending = ordering.startswith('-')
    field = ORDERING_FIELDS[ordering.lstrip('-')]
    expression = F(field).desc(nulls_last=True) if descending else F(field).asc(nulls_last=True)
    order = [expression]
    if field != 'trade_date':
        order.append('-trade_date')
    order.append('-id')
    return qs.order_by(*order)


def select_trades(params) -> tuple:
    """Полный разбор параметров -> (queryset с фильтрами и сортировкой, фильтры, ordering)."""
    filters = parse_filters(params)
    ordering = parse_ordering(params)
    qs = apply_ordering(apply_filters(trades_queryset(), filters), ordering)
    return qs, filters, ordering


# ------------------------------------------------------------------ новости

def select_news(params) -> tuple:
    """Лента новостей: фильтры exchange и q (слова заголовка) -> (queryset, exchange, q).

    Сортировка — свежие сверху (дата публикации, затем id).
    """
    exchange = parse_exchange(params)
    q = _text_param(params, 'q')
    qs = News.objects.select_related('exchange')
    if exchange:
        qs = qs.filter(exchange__code=exchange)
    for word in _words(q):
        qs = qs.filter(title__rucontains=word)
    return qs.order_by('-published_at', '-id'), exchange, q
