"""Преобразование записей БД в плоские словари для JSON и Excel."""
from django.utils import timezone

from exchanges.models import ParseRun, Trade


def число(значение):
    """Decimal -> float (для JSON); None остаётся None."""
    return None if значение is None else float(значение)


def название(объект):
    """Наименование связанного объекта или пустая строка."""
    return объект.name if объект is not None else ''


def итог_в_словарь(t: Trade) -> dict:
    """Плоское представление одной записи итогов торгов (нужны select_related)."""
    return {
        'id': t.id,
        'exchange': t.exchange.code,
        'exchange_name': t.exchange.name,
        'trade_date': t.trade_date.isoformat(),
        'trade_time': t.trade_time.strftime('%H:%M:%S') if t.trade_time else None,
        'product': t.product.name,
        'product_code': t.product.code,
        'tnved': t.product.tnved,
        'buyer': название(t.buyer),
        'buyer_bin': t.buyer.bin if t.buyer else '',
        'seller': название(t.seller),
        'seller_bin': t.seller.bin if t.seller else '',
        'buyer_broker': название(t.buyer_broker),
        'seller_broker': название(t.seller_broker),
        'deals_count': t.deals_count,
        'quantity': число(t.quantity),
        'unit': t.unit,
        'price_open': число(t.price_open),
        'price_close': число(t.price_close),
        'price_min': число(t.price_min),
        'price_max': число(t.price_max),
        'price_avg': число(t.price_avg),
        'price_with_vat': число(t.price_with_vat),
        'price_without_vat': число(t.price_without_vat),
        'amount': число(t.amount),
        'delivery_basis': t.delivery_basis,
        'delivery_place': t.delivery_place,
        'delivery_term': t.delivery_term,
        'source_url': t.source_url,
    }


def дата_время_для_показа(значение):
    """datetime -> 'дд.мм.гггг чч:мм' в местном часовом поясе."""
    if значение is None:
        return ''
    return timezone.localtime(значение).strftime('%d.%m.%Y %H:%M')


def запуск_в_словарь(запуск: ParseRun) -> dict:
    """Представление записи журнала запусков парсера."""
    длительность = None
    if запуск.finished_at and запуск.started_at:
        длительность = int((запуск.finished_at - запуск.started_at).total_seconds())
    return {
        'id': запуск.id,
        'exchange': запуск.exchange.code,
        'exchange_name': запуск.exchange.name,
        'status': запуск.status,
        'status_display': запуск.get_status_display(),
        'trigger': запуск.trigger,
        'trigger_display': {'manual': 'Вручную', 'schedule': 'По расписанию'}.get(
            запуск.trigger, запуск.trigger
        ),
        'started_at': timezone.localtime(запуск.started_at).isoformat() if запуск.started_at else None,
        'started_at_display': дата_время_для_показа(запуск.started_at),
        'finished_at': timezone.localtime(запуск.finished_at).isoformat() if запуск.finished_at else None,
        'finished_at_display': дата_время_для_показа(запуск.finished_at),
        'duration_seconds': длительность,
        'date_from': запуск.date_from.isoformat() if запуск.date_from else None,
        'date_to': запуск.date_to.isoformat() if запуск.date_to else None,
        'rows_added': запуск.rows_added,
        'rows_skipped': запуск.rows_skipped,
        'error': запуск.error,
    }
