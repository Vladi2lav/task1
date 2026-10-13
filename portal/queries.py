"""Разбор параметров запроса, фильтрация и сортировка итогов торгов.

Один и тот же набор параметров используют JSON API, экспорт в Excel и страница
в браузере, поэтому логика собрана здесь и не дублируется во views.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.db.models import F, Q, QuerySet

from exchanges.models import Trade

# Допустимые коды бирж в фильтре
КОДЫ_БИРЖ = ('ets', 'caspy')

РАЗМЕР_СТРАНИЦЫ_ПО_УМОЛЧАНИЮ = 50
РАЗМЕР_СТРАНИЦЫ_МИН = 10
РАЗМЕР_СТРАНИЦЫ_МАКС = 200

# Сортировка по умолчанию: свежие торги сверху
СОРТИРОВКА_ПО_УМОЛЧАНИЮ = '-trade_date'

# Белый список полей сортировки: имя в API -> поле в БД
ПОЛЯ_СОРТИРОВКИ = {
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


class ОшибкаПараметров(ValueError):
    """Некорректный параметр запроса (превращается в ответ 400)."""


@dataclass
class Фильтры:
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


def _текст(params, имя):
    return (params.get(имя) or '').strip()


def _разобрать_дату(params, имя):
    значение = _текст(params, имя)
    if not значение:
        return None
    try:
        return date.fromisoformat(значение)
    except ValueError:
        raise ОшибкаПараметров(f'Параметр {имя}: ожидается дата в формате ГГГГ-ММ-ДД')


def _разобрать_сумму(params, имя):
    значение = _текст(params, имя).replace(' ', '').replace(',', '.')
    if not значение:
        return None
    try:
        число = Decimal(значение)
    except InvalidOperation:
        raise ОшибкаПараметров(f'Параметр {имя}: ожидается число')
    if not число.is_finite() or abs(число) >= Decimal('1e20'):
        raise ОшибкаПараметров(f'Параметр {имя}: ожидается число')
    return число


def разобрать_фильтры(params) -> Фильтры:
    """Читает фильтры из QueryDict/словаря; при ошибке бросает ОшибкаПараметров."""
    биржа = _текст(params, 'exchange').lower()
    if биржа in ('all', 'both'):
        биржа = ''
    if биржа and биржа not in КОДЫ_БИРЖ:
        raise ОшибкаПараметров('Параметр exchange: допустимы ets, caspy или пусто')
    фильтры = Фильтры(
        exchange=биржа,
        date_from=_разобрать_дату(params, 'date_from'),
        date_to=_разобрать_дату(params, 'date_to'),
        q=_текст(params, 'q'),
        party=_текст(params, 'party'),
        broker=_текст(params, 'broker'),
        amount_min=_разобрать_сумму(params, 'amount_min'),
        amount_max=_разобрать_сумму(params, 'amount_max'),
        delivery=_текст(params, 'delivery'),
    )
    return фильтры


def разобрать_сортировку(params) -> str:
    """Возвращает нормализованное значение ordering ('-amount', 'product' ...)."""
    сырое = _текст(params, 'ordering') or СОРТИРОВКА_ПО_УМОЛЧАНИЮ
    поле = сырое.lstrip('-')
    if поле not in ПОЛЯ_СОРТИРОВКИ:
        raise ОшибкаПараметров(
            'Параметр ordering: недопустимое поле. Допустимы: ' + ', '.join(ПОЛЯ_СОРТИРОВКИ)
        )
    return ('-' if сырое.startswith('-') else '') + поле


def разобрать_страницу(params) -> tuple:
    """Возвращает (page, page_size); размер страницы ограничивается 10–200."""
    try:
        страница = int(_текст(params, 'page') or 1)
        размер = int(_текст(params, 'page_size') or РАЗМЕР_СТРАНИЦЫ_ПО_УМОЛЧАНИЮ)
    except ValueError:
        raise ОшибкаПараметров('Параметры page и page_size должны быть целыми числами')
    размер = max(РАЗМЕР_СТРАНИЦЫ_МИН, min(РАЗМЕР_СТРАНИЦЫ_МАКС, размер))
    return max(1, страница), размер


def _слова(текст):
    return [слово for слово in текст.split() if слово]


def запрос_итогов() -> QuerySet:
    """Базовый запрос с подгрузкой связанных таблиц (без N+1)."""
    return Trade.objects.select_related(
        'exchange', 'product', 'buyer', 'seller', 'buyer_broker', 'seller_broker'
    )


def применить_фильтры(qs: QuerySet, ф: Фильтры) -> QuerySet:
    """Накладывает фильтры; все условия комбинируются через И."""
    if ф.exchange:
        qs = qs.filter(exchange__code=ф.exchange)
    if ф.date_from:
        qs = qs.filter(trade_date__gte=ф.date_from)
    if ф.date_to:
        qs = qs.filter(trade_date__lte=ф.date_to)
    # Ключевое слово: каждое слово должно встретиться в названии товара
    for слово in _слова(ф.q):
        qs = qs.filter(product__name__rucontains=слово)
    if ф.party:
        # Продавец или покупатель: по наименованию или по БИН
        qs = qs.filter(
            Q(buyer__name__rucontains=ф.party)
            | Q(seller__name__rucontains=ф.party)
            | Q(buyer__bin__contains=ф.party)
            | Q(seller__bin__contains=ф.party)
        )
    if ф.broker:
        qs = qs.filter(
            Q(buyer_broker__name__rucontains=ф.broker) | Q(seller_broker__name__rucontains=ф.broker)
        )
    if ф.amount_min is not None:
        qs = qs.filter(amount__gte=ф.amount_min)
    if ф.amount_max is not None:
        qs = qs.filter(amount__lte=ф.amount_max)
    if ф.delivery:
        qs = qs.filter(delivery_place__rucontains=ф.delivery)
    return qs


def применить_сортировку(qs: QuerySet, ordering: str) -> QuerySet:
    """Сортировка по полю из белого списка; пустые значения всегда в конце.

    В конец добавляются дата и id, чтобы порядок строк был стабильным
    (иначе страницы при пагинации могут «плавать»).
    """
    убывание = ordering.startswith('-')
    поле = ПОЛЯ_СОРТИРОВКИ[ordering.lstrip('-')]
    выражение = F(поле).desc(nulls_last=True) if убывание else F(поле).asc(nulls_last=True)
    порядок = [выражение]
    if поле != 'trade_date':
        порядок.append('-trade_date')
    порядок.append('-id')
    return qs.order_by(*порядок)


def выборка_итогов(params) -> tuple:
    """Полный разбор параметров -> (queryset с фильтрами и сортировкой, фильтры, ordering)."""
    фильтры = разобрать_фильтры(params)
    ordering = разобрать_сортировку(params)
    qs = применить_сортировку(применить_фильтры(запрос_итогов(), фильтры), ordering)
    return qs, фильтры, ordering
