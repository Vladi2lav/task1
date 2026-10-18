"""Сбор новостей с сайтов бирж: python manage.py collect_news --exchange all --days 92"""
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from exchanges.models import News
from scrapers.news import NEWS_SCRAPERS, collect_news
from scrapers.service import setup_console_logging

STATUS_NAMES = {'success': 'Успешно', 'partial': 'С ошибками', 'failed': 'Ошибка'}


class Command(BaseCommand):
    help = 'Собрать новости с сайтов бирж и сохранить в БД (повторный запуск дублей не создаёт)'

    def add_arguments(self, parser):
        parser.add_argument('--exchange', default='all',
                            help=f'Биржа: {" | ".join(NEWS_SCRAPERS)} | all (по умолчанию all)')
        parser.add_argument('--days', type=int,
                            help='За сколько последних дней собрать новости (по умолчанию [news].days из scraper.toml)')
        parser.add_argument('--since', help='С какой даты собрать новости, ГГГГ-ММ-ДД (вместо --days)')

    def handle(self, *args, **options):
        setup_console_logging()
        if options['since']:
            try:
                since = date.fromisoformat(options['since'])
            except ValueError:
                raise CommandError(f'Дата «{options["since"]}» должна быть в формате ГГГГ-ММ-ДД')
        else:
            days = options['days']
            if days is None:
                days = int(settings.SCRAPER_CONFIG.get('news', {}).get('days', 92))
            if days < 0:
                raise CommandError('--days не может быть отрицательным')
            since = date.today() - timedelta(days=days)

        codes = list(NEWS_SCRAPERS) if options['exchange'] == 'all' else [options['exchange']]
        self.stdout.write(f'Сбор новостей с {since:%d.%m.%Y}: {", ".join(codes)}')
        try:
            results = collect_news(codes, since, trigger='manual')
        except ValueError as exc:
            raise CommandError(str(exc))

        self.stdout.write('\nИтоги:')
        for code, result in results.items():
            total = News.objects.filter(exchange__code=code).count()
            self.stdout.write(
                f'  {result["exchange"]}: {STATUS_NAMES.get(result["status"], result["status"])}, '
                f'найдено {result["found"]}, добавлено {result["added"]}, обновлено {result["updated"]}, '
                f'без изменений {result["unchanged"]}; всего в БД {total}')
            if result['errors']:
                self.stdout.write(self.style.WARNING('    Ошибки: ' + '\n    '.join(result['errors'])))
