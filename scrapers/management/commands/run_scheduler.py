"""Планировщик: python manage.py run_scheduler — ежедневный сбор по расписанию из scraper.toml."""
import logging

from django.core.management.base import BaseCommand

from scrapers.scheduler import TIMEZONE, build_scheduler, next_run_time
from scrapers.service import setup_console_logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Запустить планировщик ежедневного сбора итогов торгов (остановка — Ctrl+C)'

    def handle(self, *args, **options):
        setup_console_logging()
        scheduler = build_scheduler()
        logger.info('Планировщик запущен (%s). Ближайший сбор: %s', TIMEZONE, next_run_time().strftime('%d.%m.%Y %H:%M'))
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info('Планировщик остановлен')
