"""Ручной сбор итогов торгов: python manage.py collect_trades --exchange ets --days 30"""
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from scrapers.registry import SCRAPERS
from scrapers.service import collect, setup_console_logging


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CommandError(f'Дата «{value}» должна быть в формате ГГГГ-ММ-ДД')


class Command(BaseCommand):
    help = 'Собрать итоги торгов с сайтов бирж и сохранить в БД'

    def add_arguments(self, parser):
        parser.add_argument('--exchange', default='all',
                            help=f'Биржа: {" | ".join(SCRAPERS)} | all (по умолчанию all)')
        parser.add_argument('--since', help='Начало периода, ГГГГ-ММ-ДД')
        parser.add_argument('--until', help='Конец периода, ГГГГ-ММ-ДД (по умолчанию сегодня)')
        parser.add_argument('--days', type=int,
                            help='Сколько последних дней собрать, если нет --since (по умолчанию [history].days)')

    def handle(self, *args, **options):
        setup_console_logging()
        date_to = _parse_day(options['until']) if options['until'] else date.today()
        if options['since']:
            date_from = _parse_day(options['since'])
        else:
            days = options['days'] or int(settings.SCRAPER_CONFIG['history']['days'])
            date_from = date_to - timedelta(days=days)
        if date_from > date_to:
            raise CommandError('Начало периода позже его конца')

        codes = list(SCRAPERS) if options['exchange'] == 'all' else [options['exchange']]
        self.stdout.write(f'Сбор за {date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}: {", ".join(codes)}')
        try:
            runs = collect(codes, date_from, date_to, trigger='manual')
        except ValueError as exc:
            raise CommandError(str(exc))

        self.stdout.write('\nИтоги:')
        for run in runs:
            self.stdout.write(f'  {run.exchange.name}: {run.get_status_display()}, '
                              f'добавлено {run.rows_added}, пропущено дублей {run.rows_skipped}')
            if run.error:
                self.stdout.write(self.style.WARNING('    Ошибки: ' + run.error.replace('\n', '\n    ')))
