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
    """Задача планировщика: собрать по всем биржам из конфига.

    Период — последние lookback_days дней, а если с прошлого успешного сбора прошло больше
    (компьютер был выключен), то начиная с даты, по которую тот сбор шёл: пропущенные дни не теряются.
    """
    config = settings.SCRAPER_CONFIG['schedule']
    date_to = datetime.now(ZoneInfo(TIMEZONE)).date()
    for code in config['exchanges']:
        date_from = collect_period_start(code, date_to)
        logger.info('Плановый сбор %s за %s — %s', code, date_from, date_to)
        try:
            collect([code], date_from, date_to, trigger='schedule')
        except Exception:  # ошибка одной биржи не мешает остальным
            logger.exception('Плановый сбор по бирже %s не выполнен', code)
    collect_news_scheduled(date_to)


def collect_period_start(code: str, today: date) -> date:
    """С какой даты собирать: lookback_days назад или раньше — от последнего успешного сбора биржи."""
    from exchanges.models import ParseRun

    lookback = today - timedelta(days=int(settings.SCRAPER_CONFIG['schedule'].get('lookback_days', 3)))
    history_limit = today - timedelta(days=int(settings.SCRAPER_CONFIG['history'].get('days', 92)))
    last = (ParseRun.objects
            .filter(exchange__code=code, status__in=[ParseRun.Status.SUCCESS, ParseRun.Status.PARTIAL],
                    date_to__isnull=False)
            .order_by('-date_to').values_list('date_to', flat=True).first())
    if last is None:
        return lookback
    return max(min(lookback, last), history_limit)


def previous_run_time(now: datetime | None = None) -> datetime:
    """Когда по расписанию должен был пройти последний сбор (сегодня или вчера в [schedule].hour:minute)."""
    config = settings.SCRAPER_CONFIG['schedule']
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    planned = now.replace(hour=int(config['hour']), minute=int(config['minute']), second=0, microsecond=0)
    return planned if planned <= now else planned - timedelta(days=1)


def missed_run(now: datetime | None = None) -> bool:
    """Пропущен ли последний плановый сбор: после его времени не было ни одного планового запуска.

    Так бывает, если в момент расписания планировщик не работал (компьютер выключен, процесс не запущен).
    """
    from exchanges.models import ParseRun

    due = previous_run_time(now)
    return not ParseRun.objects.filter(trigger='schedule', started_at__gte=due).exists()


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


def build_scheduler(catch_up: bool = True) -> BlockingScheduler:
    """Планировщик с ежедневной задачей (час и минута из [schedule]).

    catch_up: если последний плановый сбор пропущен (планировщик тогда не работал), он выполняется
    сразу после старта, с опозданием, а дальше — снова по расписанию.
    """
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    scheduler.add_job(collect_scheduled, _cron_trigger(), id='collect_trades',
                      # проснулся после сна позже расписания — всё равно выполнить (один раз)
                      misfire_grace_time=None, coalesce=True)
    if catch_up and missed_run():
        logger.warning('Плановый сбор %s пропущен — запускаю сейчас',
                       previous_run_time().strftime('%d.%m.%Y %H:%M'))
        scheduler.add_job(collect_scheduled, id='catch_up', next_run_time=datetime.now(ZoneInfo(TIMEZONE)))
    return scheduler
