"""Отпечаток строки итогов торгов: по нему отсекаются дубли при повторных запусках."""
from __future__ import annotations

import hashlib
from collections import Counter
from decimal import Decimal
from typing import Iterable, List

from .base import TradeRecord


def _text(value) -> str:
    if value is None:
        return ''
    if isinstance(value, Decimal):
        return format(value.normalize(), 'f')  # 88000 и 88000.00 дают одно и то же
    return str(value)


def record_identity(record: TradeRecord) -> tuple:
    """Значения, по которым строки считаются одной и той же (без номера повтора)."""
    return (
        record.exchange_code,
        record.trade_date.isoformat(),
        record.buyer_bin or record.buyer_name,
        record.seller_bin or record.seller_name,
        record.product_code,
        record.product_name,
        record.delivery_basis,
        record.price_open,
        record.price_close,
        record.price_min,
        record.price_max,
        record.price_avg,
        record.amount,
        record.deals_count,
    )


def make_source_key(record: TradeRecord) -> str:
    """sha1 от значений строки и номера повтора."""
    parts = [_text(v) for v in record_identity(record)] + [str(record.occurrence)]
    return hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()


def assign_occurrences(records: Iterable[TradeRecord]) -> List[TradeRecord]:
    """Пронумеровать одинаковые строки внутри одного источника: 0, 1, 2, ...

    Иначе две совпавшие по значениям строки получили бы один отпечаток и слиплись бы.
    """
    seen: Counter = Counter()
    result = []
    for record in records:
        identity = record_identity(record)
        record.occurrence = seen[identity]
        seen[identity] += 1
        result.append(record)
    return result
