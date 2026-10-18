"""Ежедневный запуск сбора по расписанию из scraper.toml (APScheduler)."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings

from .news import NEWS_SCRAPERS, collect_news
from .service import collect

logger = logging.getLogger(__name__)

TIMEZONE = 'Asia/Almaty'


def collect_scheduled() -> None:
    """Задача планировщика: перезагрузить последние lookback_days дней по всем биржам из конфига."""
    config = settings.SCRAPER_CONFIG['schedule']
    date_to = datetime.now(ZoneInfo(TIMEZONE)).date()
    date_from = date_to - timedelta(days=int(config.get('lookback_days', 3)))
    logger.info('Плановый сбор за %s — %s: %s', date_from, date_to, ', '.join(config['exchanges']))
    for code in config['exchanges']:
        try:
            collect([code], date_from, date_to, trigger='schedule')
        except Exception:  # ошибка одной биржи не мешает остальным
            logger.exception('Плановый сбор по бирже %s не выполнен', code)
    collect_news_scheduled(date_to)


def collect_news_scheduled(today: date) -> None:
    """Новости — после итогов торгов, если [news].enabled. Ошибки новостей на торги не влияют."""
    news_config = settings.SCRAPER_CONFIG.get('news', {})
    if not news_config.get('enabled', False):
        return
    codes = [code for code in settings.SCRAPER_CONFIG['schedule']['exchanges'] if code in NEWS_SCRAPERS]
    since = today - timedelta(days=int(news_config.get('days', 92)))
    try:
        collect_news(codes, since, trigger='schedule')
    except Exception:
        logger.exception('Плановый сбор новостей не выполнен')


def _cron_trigger() -> CronTrigger:
    config = settings.SCRAPER_CONFIG['schedule']
    return CronTrigger(hour=config['hour'], minute=config['minute'], timezone=TIMEZONE)


def next_run_time() -> datetime:
    """Когда планировщик запустится в следующий раз."""
    return _cron_trigger().get_next_fire_time(None, datetime.now(ZoneInfo(TIMEZONE)))


def build_scheduler() -> BlockingScheduler:
    """Планировщик с одной ежедневной задачей (час и минута из [schedule])."""
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    scheduler.add_job(collect_scheduled, _cron_trigger(), id='collect_trades',
                      misfire_grace_time=3600, coalesce=True)
    return scheduler
