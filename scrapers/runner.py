"""Запуск парсера: сохранение записей в БД без дублей и журнал запусков (ParseRun)."""
from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from exchanges.models import Broker, Exchange, ParseRun, Participant, Product, Trade

from .base import BaseScraper, TradeRecord
from .fingerprint import make_source_key

logger = logging.getLogger(__name__)

BATCH_SIZE = 500  # записей в одной транзакции


def run_scraper(scraper: BaseScraper, date_from: date, date_to: date, trigger: str = 'manual') -> ParseRun:
    """Собрать данные биржи за период и сохранить. Возвращает запись журнала ParseRun.

    Ошибки источника не пробрасываются: они попадают в ParseRun.error, а уже
    сохранённые данные остаются нетронутыми (запись идёт пачками в транзакциях).
    """
    exchange, _ = Exchange.objects.get_or_create(
        code=scraper.exchange_code,
        defaults={'name': scraper.exchange_name or scraper.exchange_code, 'website': scraper.website},
    )
    run = ParseRun.objects.create(exchange=exchange, trigger=trigger, date_from=date_from, date_to=date_to)
    logger.info('[%s] запуск #%d за %s — %s (%s)', exchange.code, run.pk, date_from, date_to, trigger)

    saver = _TradeSaver(exchange, run)
    buffer: List[TradeRecord] = []
    fatal_error = ''
    try:
        for record in scraper.fetch(date_from, date_to):
            buffer.append(record)
            if len(buffer) >= BATCH_SIZE:
                saver.save(buffer)
                buffer = []
    except Exception as exc:
        logger.exception('[%s] источник упал', exchange.code)
        fatal_error = f'{type(exc).__name__}: {exc}'
    try:
        if buffer:
            saver.save(buffer)  # то, что успели собрать до сбоя, сохраняем
    except Exception as exc:
        logger.exception('[%s] не удалось сохранить записи', exchange.code)
        fatal_error = fatal_error or f'{type(exc).__name__}: {exc}'

    _finish_run(run, saver, list(getattr(scraper, 'errors', None) or []), fatal_error)
    logger.info('[%s] запуск #%d завершён: %s, добавлено %d, пропущено %d',
                exchange.code, run.pk, run.get_status_display(), run.rows_added, run.rows_skipped)
    return run


def _finish_run(run: ParseRun, saver: '_TradeSaver', errors: List[str], fatal_error: str) -> None:
    """Выставить итоговый статус и счётчики."""
    if fatal_error:
        status = ParseRun.Status.FAILED
        errors = errors + [fatal_error]
    elif errors:
        loaded_anything = saver.added + saver.skipped > 0
        status = ParseRun.Status.PARTIAL if loaded_anything else ParseRun.Status.FAILED
    else:
        status = ParseRun.Status.SUCCESS
        if saver.added + saver.skipped == 0:
            logger.warning('[%s] за период нет ни одной записи', run.exchange.code)

    run.status = status
    run.rows_added = saver.added
    run.rows_skipped = saver.skipped
    run.error = '\n'.join(errors)
    run.finished_at = timezone.now()
    run.save()


class _TradeSaver:
    """Сохраняет записи пачками, создавая справочники (товары, участники, брокеры) по мере надобности."""

    def __init__(self, exchange: Exchange, run: ParseRun):
        self.exchange = exchange
        self.run = run
        self.added = 0
        self.skipped = 0
        self._products: dict = {}
        self._participants: dict = {}
        self._brokers: dict = {}

    def save(self, records: List[TradeRecord]) -> None:
        keyed = {}
        for record in records:
            keyed.setdefault(make_source_key(record), record)  # одинаковые отпечатки в пачке — один раз
        existing = set(Trade.objects.filter(source_key__in=keyed).values_list('source_key', flat=True))

        with transaction.atomic():
            new_trades = [self._build_trade(key, record) for key, record in keyed.items() if key not in existing]
            Trade.objects.bulk_create(new_trades, ignore_conflicts=True)

        self.added += len(new_trades)
        self.skipped += len(records) - len(new_trades)

    def _build_trade(self, source_key: str, r: TradeRecord) -> Trade:
        return Trade(
            exchange=self.exchange,
            trade_date=r.trade_date,
            trade_time=r.trade_time,
            product=self._product(r),
            buyer=self._participant(r.buyer_name, r.buyer_bin),
            seller=self._participant(r.seller_name, r.seller_bin),
            buyer_broker=self._broker(r.buyer_broker),
            seller_broker=self._broker(r.seller_broker),
            deals_count=r.deals_count,
            quantity=r.quantity,
            unit=r.unit,
            price_open=r.price_open,
            price_close=r.price_close,
            price_min=r.price_min,
            price_max=r.price_max,
            price_avg=r.price_avg,
            price_with_vat=r.price_with_vat,
            price_without_vat=r.price_without_vat,
            amount=r.amount,
            delivery_basis=r.delivery_basis,
            delivery_place=r.delivery_place,
            delivery_term=r.delivery_term,
            source_url=r.source_url,
            source_key=source_key,
            parse_run=self.run,
        )

    def _product(self, r: TradeRecord) -> Product:
        key = (r.product_name, r.product_code, r.tnved)
        if key not in self._products:
            self._products[key], _ = Product.objects.get_or_create(
                name=r.product_name, code=r.product_code, tnved=r.tnved)
        return self._products[key]

    def _participant(self, name: str, bin_: str) -> Optional[Participant]:
        """Участник с БИН ищется по БИН (название могло написаться иначе), без БИН — по названию."""
        if not name and not bin_:
            return None
        key = bin_ or name
        if key not in self._participants:
            participant = Participant.objects.filter(bin=bin_).first() if bin_ else None
            if participant is None:
                participant, _ = Participant.objects.get_or_create(name=name or bin_, bin=bin_)
            self._participants[key] = participant
        return self._participants[key]

    def _broker(self, name: str) -> Optional[Broker]:
        if not name:
            return None
        if name not in self._brokers:
            self._brokers[name], _ = Broker.objects.get_or_create(name=name)
        return self._brokers[name]
