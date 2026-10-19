"""Преобразование записей БД в плоские словари для JSON и Excel."""
from django.utils import timezone

from exchanges.models import News, ParseRun, Trade

from . import i18n


def to_float(value):
    """Decimal -> float (для JSON); None остаётся None."""
    return None if value is None else float(value)


def name_or_empty(obj):
    """Наименование связанного объекта или пустая строка."""
    return obj.name if obj is not None else ''


def trade_to_dict(t: Trade) -> dict:
    """Плоское представление одной записи итогов торгов (нужны select_related)."""
    return {
        'id': t.id,
        'exchange': t.exchange.code,
        'exchange_name': i18n.exchange_label(t.exchange.code, t.exchange.name),
        'trade_date': t.trade_date.isoformat(),
        'trade_time': t.trade_time.strftime('%H:%M:%S') if t.trade_time else None,
        'product': t.product.name,
        'product_code': t.product.code,
        'tnved': t.product.tnved,
        'buyer': name_or_empty(t.buyer),
        'buyer_bin': t.buyer.bin if t.buyer else '',
        'seller': name_or_empty(t.seller),
        'seller_bin': t.seller.bin if t.seller else '',
        'buyer_broker': name_or_empty(t.buyer_broker),
        'seller_broker': name_or_empty(t.seller_broker),
        'deals_count': t.deals_count,
        'quantity': to_float(t.quantity),
        'unit': t.unit,
        'price_open': to_float(t.price_open),
        'price_close': to_float(t.price_close),
        'price_min': to_float(t.price_min),
        'price_max': to_float(t.price_max),
        'price_avg': to_float(t.price_avg),
        'price_with_vat': to_float(t.price_with_vat),
        'price_without_vat': to_float(t.price_without_vat),
        'amount': to_float(t.amount),
        'delivery_basis': t.delivery_basis,
        'delivery_place': t.delivery_place,
        'delivery_term': t.delivery_term,
        'source_url': t.source_url,
    }


def format_datetime(value):
    """datetime -> 'дд.мм.гггг чч:мм' в местном часовом поясе."""
    if value is None:
        return ''
    return timezone.localtime(value).strftime('%d.%m.%Y %H:%M')


def run_to_dict(run: ParseRun) -> dict:
    """Представление записи журнала запусков парсера."""
    duration = None
    if run.finished_at and run.started_at:
        duration = int((run.finished_at - run.started_at).total_seconds())
    return {
        'id': run.id,
        'exchange': run.exchange.code,
        'exchange_name': i18n.exchange_label(run.exchange.code, run.exchange.name),
        'status': run.status,
        # подписи — на языке интерфейса (в модели choices русские)
        'status_display': i18n.run_status_label(run.status, run.get_status_display()),
        'trigger': run.trigger,
        'trigger_display': i18n.trigger_label(run.trigger),
        'started_at': timezone.localtime(run.started_at).isoformat() if run.started_at else None,
        'started_at_display': format_datetime(run.started_at),
        'finished_at': timezone.localtime(run.finished_at).isoformat() if run.finished_at else None,
        'finished_at_display': format_datetime(run.finished_at),
        'duration_seconds': duration,
        'date_from': run.date_from.isoformat() if run.date_from else None,
        'date_to': run.date_to.isoformat() if run.date_to else None,
        'rows_added': run.rows_added,
        'rows_skipped': run.rows_skipped,
        'error': run.error,
    }


def news_to_dict(item: News) -> dict:
    """Представление новости биржи (нужен select_related('exchange'))."""
    return {
        'id': item.id,
        'exchange': i18n.exchange_label(item.exchange.code, item.exchange.name),
        'exchange_code': item.exchange.code,
        'title': item.title,
        'published_at': item.published_at.isoformat() if item.published_at else None,
        'url': item.url,
        'summary': item.summary,
        'image_url': item.image_url,
    }
