"""Первичная инициализация проекта одной командой: python manage.py bootstrap

1. migrate — создать/обновить таблицы;
2. collectstatic — собрать статику для WhiteNoise (кроме Vercel; --skip-static чтобы пропустить);
3. если таблица торгов пуста — загрузить дамп docs/db/trades_fixture.json
   (и docs/db/news_fixture.json, если есть и новостей ещё нет);
4. --collect (или BOOTSTRAP_COLLECT=1) — свежий сбор: collect_trades за [history].days и collect_news;
5. суперпользователь из DJANGO_SUPERUSER_USERNAME / DJANGO_SUPERUSER_PASSWORD / DJANGO_SUPERUSER_EMAIL.

Команду можно запускать сколько угодно раз: уже сделанные шаги пропускаются.
"""
import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command, get_commands
from django.core.management.base import BaseCommand
from django.db import transaction

from exchanges.models import News, Trade

DEFAULT_FIXTURE_DIR = Path(settings.BASE_DIR) / 'docs' / 'db'


def _env_flag(name: str) -> bool:
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


class Command(BaseCommand):
    help = 'Подготовить проект к работе: миграции, статика, начальные данные из дампа, суперпользователь'

    def add_arguments(self, parser):
        parser.add_argument('--skip-static', action='store_true', help='не запускать collectstatic')
        parser.add_argument('--no-fixture', action='store_true', help='не загружать данные из дампа')
        parser.add_argument('--collect', action='store_true',
                            help='после загрузки дампа собрать свежие данные с сайтов бирж (нужен интернет)')
        parser.add_argument('--days', type=int,
                            help='за сколько дней собирать при --collect (по умолчанию [history].days)')
        parser.add_argument('--fixture-dir', default=str(DEFAULT_FIXTURE_DIR),
                            help='папка с trades_fixture.json и news_fixture.json')

    def handle(self, *args, **options):
        verbosity = options['verbosity']

        self._step('Миграции базы данных')
        call_command('migrate', interactive=False, verbosity=max(verbosity - 1, 0))

        if options['skip_static'] or getattr(settings, 'IS_VERCEL', False):
            self._info('Статика: пропущено')
        else:
            self._step('Сбор статики (collectstatic)')
            call_command('collectstatic', interactive=False, verbosity=max(verbosity - 1, 0))

        if options['no_fixture']:
            self._info('Начальные данные: пропущено (--no-fixture)')
        else:
            self._load_fixtures(Path(options['fixture_dir']))

        if options['collect'] or _env_flag('BOOTSTRAP_COLLECT'):
            self._collect(options['days'])

        self._ensure_superuser()
        self.stdout.write(self.style.SUCCESS(
            f'Готово. Итогов торгов в базе: {Trade.objects.count()}, новостей: {News.objects.count()}.'))

    # --- шаги ---

    def _load_fixtures(self, fixture_dir: Path):
        trades_fixture = fixture_dir / 'trades_fixture.json'
        news_fixture = fixture_dir / 'news_fixture.json'

        if Trade.objects.exists():
            self._info(f'Начальные данные: в базе уже есть итоги торгов ({Trade.objects.count()}), дамп не нужен')
        elif not trades_fixture.exists():
            self._warn(f'Начальные данные: файл {trades_fixture} не найден, база останется пустой')
        else:
            self._step(f'Загрузка дампа {trades_fixture.name}')
            self._loaddata(trades_fixture)

        if news_fixture.exists() and not News.objects.exists():
            self._step(f'Загрузка новостей {news_fixture.name}')
            self._loaddata(news_fixture)

    def _loaddata(self, fixture: Path):
        try:
            with transaction.atomic():
                call_command('loaddata', str(fixture), verbosity=0)
        except Exception as exc:  # дамп не подходит к текущей базе — работаем дальше без него
            self._warn(f'Не удалось загрузить {fixture.name}: {exc}')
        else:
            self._info(f'Загружено: итогов торгов {Trade.objects.count()}, новостей {News.objects.count()}')

    def _collect(self, days):
        days = days or int(settings.SCRAPER_CONFIG['history']['days'])
        self._step(f'Сбор свежих итогов торгов за {days} дн. (может занять несколько минут)')
        try:
            call_command('collect_trades', days=days)
        except Exception as exc:  # сеть или сайт биржи недоступны — не мешаем запуску
            self._warn(f'Сбор итогов торгов не выполнен: {exc}')
        if 'collect_news' in get_commands():
            self._step('Сбор новостей бирж')
            try:
                call_command('collect_news')
            except Exception as exc:  # noqa: BLE001
                self._warn(f'Сбор новостей не выполнен: {exc}')

    def _ensure_superuser(self):
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', '').strip()
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '')
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip()
        if not username:
            return
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self._info(f'Администратор «{username}» уже существует')
            return
        if not password:
            self._warn('DJANGO_SUPERUSER_USERNAME задан, а DJANGO_SUPERUSER_PASSWORD — нет: администратор не создан')
            return
        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f'Создан администратор «{username}» (вход: /admin/)'))

    # --- вывод ---

    def _step(self, text):
        self.stdout.write(self.style.MIGRATE_HEADING(f'> {text}'))

    def _info(self, text):
        self.stdout.write(f'  {text}')

    def _warn(self, text):
        self.stdout.write(self.style.WARNING(f'  ! {text}'))
