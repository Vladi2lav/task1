"""Новости бирж: разбор лент ETS и Каспий, сохранение без дублей, команда collect_news (без сети)."""
from datetime import date

import pytest
from django.core.management import CommandError, call_command

from exchanges.models import News
from scrapers import news as news_module
from scrapers import scheduler
from scrapers.news import CaspyNewsScraper, EtsNewsScraper, NewsItem, collect_news, make_summary
from scrapers.normalize import parse_ru_date
from tests.conftest import FakeHttp, fixture_text

ETS_SOURCE = {'base_url': 'https://ets.kz', 'news_path': '/press_centre/news/'}
ETS_NEWS = 'https://ets.kz/press_centre/news/'
ETS_PAGE2 = f'{ETS_NEWS}?PAGEN_1=2'
ETS_PAGE3 = f'{ETS_NEWS}?PAGEN_1=3'
ETS_FIRST = f'{ETS_NEWS}2026-zhyl-y-yrk-yekte-eszh-birzhasynda-m-nay-nimderimen-birzhaly-sauda-satty-ty-orytyndylary-itogi-b/'
ETS_CHANGES = f'{ETS_NEWS}izmeneniya-v-perechne-birzhevykh-tovarov-i-pravilakh-birzhevoy-torgovli/'

CASPY_SOURCE = {'base_url': 'https://ccx.kz', 'news_path': '/news/category/',
                'news_categories': ['announcement', 'corporate'], 'news_per_page': 50}
CASPY_ANN = 'https://ccx.kz/news/category/announcement?lang=ru&per_page=50&page=1'
CASPY_CORP = 'https://ccx.kz/news/category/corporate?lang=ru&per_page=50&page=1'

NO_DETAILS = {'max_pages': 10, 'details': False}
WITH_DETAILS = {'max_pages': 10, 'details': True}
# с этой даты: у ETS 2 новости (01.10, 30.09), у Каспия по 9 в каждой категории (до 14.09) — без перехода на страницу 2
SINCE = date(2026, 9, 12)


def ets_pages(**extra):
    pages = {ETS_NEWS: fixture_text('news_ets_page1.html'), ETS_PAGE2: fixture_text('news_ets_page2.html')}
    pages.update(extra)
    return pages


def ets(pages=None, config=NO_DETAILS):
    return EtsNewsScraper(http=FakeHttp(pages if pages is not None else ets_pages()), source=ETS_SOURCE,
                          news_config=config)


def caspy(pages=None, config=NO_DETAILS):
    if pages is None:
        pages = {CASPY_ANN: fixture_text('news_caspy_page1.html')}
    return CaspyNewsScraper(http=FakeHttp(pages), source=CASPY_SOURCE, news_config=config)


# --- даты и анонс --------------------------------------------------------

@pytest.mark.parametrize('text, expected', [
    ('14 октября 2026', date(2026, 10, 14)),
    ('3 марта 2026 г.', date(2026, 3, 3)),
    ('1 мая 2026', date(2026, 5, 1)),
    ('Астана, 12 июня 2026 года', date(2026, 6, 12)),
    ('5 сент. 2026', date(2026, 9, 5)),
    ('14.10.2026', date(2026, 10, 14)),
    ('  06.10.2026\n', date(2026, 10, 6)),
    ('Опубликовано 01.09.2026 в 10:00', date(2026, 9, 1)),
    ('31 февраля 2026', None),
    ('14 брюмера 2026', None),
    ('', None),
    (None, None),
])
def test_parse_ru_date(text, expected):
    assert parse_ru_date(text) == expected


def test_make_summary_strips_title_and_cuts_by_word():
    text = 'Заголовок новости. ' + 'слово ' * 200
    summary = make_summary(text, 'Заголовок новости')
    assert summary.startswith('слово') and summary.endswith('…')
    assert len(summary) <= 401 and not summary[:-1].endswith(' ')
    assert make_summary('  короткий\n текст ') == 'короткий текст'


# --- ETS -----------------------------------------------------------------

def test_ets_list_page_parsed():
    items, has_next = ets().parse_list(fixture_text('news_ets_page1.html'), 1)

    assert has_next is True
    assert len(items) == 12
    first = items[0]
    assert first == NewsItem('ets', 'ИТОГИ БИРЖЕВЫХ ТОРГОВ НЕФТЕПРОДУКТАМИ В СЕНТЯБРЕ 2026 ГОДА НА БИРЖЕ ЕТС',
                             date(2026, 10, 1), ETS_FIRST)
    assert items[1].title == 'Изменения в Перечне биржевых товаров и Правилах биржевой торговли'
    assert items[1].url == ETS_CHANGES
    assert all(item.url.startswith('https://ets.kz/press_centre/news/') for item in items)
    assert [item.published_at for item in items] == sorted((item.published_at for item in items), reverse=True)


def test_ets_walks_pages_until_since():
    scraper = ets()
    items = list(scraper.fetch(date(2026, 1, 20)))

    # страница 1 целиком (12) + на странице 2 новости с 02.02 по 21.01 (5), дальше старее
    assert len(items) == 17
    assert items[-1].published_at == date(2026, 1, 21)
    assert min(item.published_at for item in items) >= date(2026, 1, 20)
    assert scraper.http.requested == [ETS_NEWS, ETS_PAGE2]  # третья страница не нужна
    assert scraper.errors == []


def test_ets_stops_on_first_page_when_since_is_recent():
    scraper = ets()
    items = list(scraper.fetch(date(2026, 9, 15)))
    assert [item.published_at for item in items] == [date(2026, 10, 1), date(2026, 9, 30)]
    assert scraper.http.requested == [ETS_NEWS]


def test_ets_respects_max_pages():
    scraper = ets(config={'max_pages': 2, 'details': False})
    items = list(scraper.fetch(date(2000, 1, 1)))
    assert len(items) == 24
    assert ETS_PAGE3 not in scraper.http.requested


def test_ets_details_give_summary_and_missing_detail_is_not_fatal():
    scraper = ets(ets_pages(**{ETS_CHANGES: fixture_text('news_ets_detail.html')}), config=WITH_DETAILS)
    first, changes = list(scraper.fetch(date(2026, 9, 15)))

    assert changes.summary.startswith('АО «Товарная биржа «Евразийская Торговая Система» информирует')
    assert 'Изменения в Перечне' not in changes.summary[:40]  # повтор заголовка убран
    assert len(changes.summary) <= 401
    # страницы первой новости в заглушке нет: новость всё равно отдана, ошибка записана
    assert first.url == ETS_FIRST and first.summary == ''
    assert len(scraper.errors) == 1 and ETS_FIRST in scraper.errors[0]


def test_ets_changed_layout_is_reported():
    scraper = ets({ETS_NEWS: '<html><body><p>Сайт на обслуживании</p></body></html>'})
    assert list(scraper.fetch(date(2026, 1, 1))) == []
    assert len(scraper.errors) == 1 and 'изменилась вёрстка' in scraper.errors[0]


# --- Каспий --------------------------------------------------------------

def test_caspy_list_page_parsed():
    items, has_next = caspy().parse_list(fixture_text('news_caspy_page1.html'), 1)

    assert has_next is True  # в образце кнопки на страницы 2, 3 и последнюю (19: 189 записей по 10)
    assert len(items) == 10
    first = items[0]
    assert first.exchange_code == 'caspy'
    assert first.title == 'График проведения торговых сессий нефтепродуктами, по плану поставки на октябрь месяц 2026 года.'
    assert first.published_at == date(2026, 10, 6)
    assert first.url == 'https://ccx.kz/news/219'
    assert items[-1].url == 'https://ccx.kz/news/209' and items[-1].published_at == date(2026, 9, 11)
    _, has_next_last = caspy().parse_list(fixture_text('news_caspy_page1.html'), 19)
    assert has_next_last is False


def test_caspy_stops_by_since_and_survives_failed_category():
    scraper = caspy()  # страницы категории corporate в заглушке нет (404)
    items = list(scraper.fetch(date(2026, 9, 20)))

    assert [item.url.rsplit('/', 1)[1] for item in items] == ['219', '218', '217', '216', '215', '214', '213', '212']
    assert scraper.http.requested == [CASPY_ANN, CASPY_CORP]
    assert len(scraper.errors) == 1 and 'corporate' in scraper.errors[0]


def test_caspy_detail_page_parsed():
    scraper = caspy()
    item = NewsItem('caspy', '5 итогов участия ТБК в Общем годовом собрании Международной ассоциации бирж',
                    date(2026, 9, 25), 'https://ccx.kz/news/211')
    summary, image = scraper.parse_detail(fixture_text('news_caspy_detail.html'), item)

    assert summary.startswith('17–18 сентября 2026 года делегация АО «Товарная Биржа «Каспий»')
    assert '\n' not in summary and '  ' not in summary
    assert image.startswith('https://ccx.kz/storage/news/images/') and image.endswith('.jpg')
    assert scraper.detail_url(item) == 'https://ccx.kz/news/211?lang=ru'


# --- сохранение ----------------------------------------------------------

@pytest.fixture
def sites(monkeypatch):
    """Парсеры новостей в collect_news работают на заглушках; страницы можно менять по ходу теста."""
    state = {'ets': ets_pages(), 'caspy': {CASPY_ANN: fixture_text('news_caspy_page1.html'),
                                           CASPY_CORP: fixture_text('news_caspy_page1.html').replace('/news/2', '/news/9')},
             'config': NO_DETAILS, 'scrapers': []}

    def factory(make, code):
        def build():
            scraper = make(dict(state[code]), state['config'])
            state['scrapers'].append(scraper)
            return scraper
        return build

    monkeypatch.setitem(news_module.NEWS_SCRAPERS, 'ets', factory(ets, 'ets'))
    monkeypatch.setitem(news_module.NEWS_SCRAPERS, 'caspy', factory(caspy, 'caspy'))
    return state


@pytest.mark.django_db
def test_collect_news_saves_and_rerun_adds_no_duplicates(sites):
    first = collect_news(['ets', 'caspy'], SINCE)

    assert first['ets'] == {'exchange': 'ETS', 'found': 2, 'added': 2, 'updated': 0, 'unchanged': 0,
                            'errors': [], 'status': 'success'}
    assert (first['caspy']['added'], first['caspy']['status']) == (18, 'success')  # 9 + 9 из двух категорий
    assert News.objects.count() == 20
    news = News.objects.get(url=ETS_CHANGES)
    assert news.exchange.code == 'ets' and news.published_at == date(2026, 9, 30)

    second = collect_news(['ets', 'caspy'], SINCE)
    assert (second['ets']['added'], second['ets']['unchanged']) == (0, 2)
    assert (second['caspy']['added'], second['caspy']['unchanged']) == (0, 18)
    assert News.objects.count() == 20


@pytest.mark.django_db
def test_collect_news_updates_changed_title(sites):
    collect_news(['ets'], SINCE)
    old = 'Изменения в Перечне биржевых товаров и Правилах биржевой торговли'
    sites['ets'][ETS_NEWS] = sites['ets'][ETS_NEWS].replace(old, 'Изменения в Перечне биржевых товаров (уточнено)')

    result = collect_news(['ets'], SINCE)

    assert (result['ets']['added'], result['ets']['updated'], result['ets']['unchanged']) == (0, 1, 1)
    assert News.objects.get(url=ETS_CHANGES).title == 'Изменения в Перечне биржевых товаров (уточнено)'
    assert News.objects.count() == 2


@pytest.mark.django_db
def test_collect_news_details_fetched_once(sites):
    sites['config'] = WITH_DETAILS
    sites['ets'][ETS_CHANGES] = fixture_text('news_ets_detail.html')
    collect_news(['ets'], date(2026, 9, 15))
    assert News.objects.get(url=ETS_CHANGES).summary.startswith('АО «Товарная биржа')

    collect_news(['ets'], date(2026, 9, 15))
    second_requests = sites['scrapers'][-1].http.requested
    assert ETS_CHANGES not in second_requests  # анонс уже есть — страницу новости не открываем
    assert ETS_FIRST in second_requests        # у этой анонса нет — пробуем снова
    assert News.objects.get(url=ETS_CHANGES).summary  # пустой анонс не затёр сохранённый


@pytest.mark.django_db
def test_collect_news_one_exchange_failure_does_not_stop_other(sites):
    sites['ets'] = {}  # сайт ETS недоступен

    result = collect_news(['ets', 'caspy'], SINCE)

    assert result['ets']['status'] == 'failed' and result['ets']['errors']
    assert result['caspy']['status'] == 'success' and result['caspy']['added'] == 18
    assert News.objects.filter(exchange__code='caspy').count() == 18


@pytest.mark.django_db
def test_collect_news_unknown_exchange():
    with pytest.raises(ValueError):
        collect_news(['moex'], date(2026, 1, 1))


# --- команда и планировщик ----------------------------------------------

@pytest.mark.django_db
def test_collect_news_command(sites, capsys):
    call_command('collect_news', '--exchange', 'all', '--since', SINCE.isoformat())
    out = capsys.readouterr().out
    assert 'ETS: Успешно, найдено 2, добавлено 2' in out
    assert 'Каспий: Успешно, найдено 18, добавлено 18' in out

    call_command('collect_news', '--exchange', 'ets', '--since', SINCE.isoformat())
    assert 'добавлено 0, обновлено 0, без изменений 2; всего в БД 2' in capsys.readouterr().out
    assert News.objects.count() == 20

    with pytest.raises(CommandError):
        call_command('collect_news', '--exchange', 'moex', '--days', '5')


@pytest.mark.django_db
def test_scheduler_runs_news_after_trades_and_isolates_errors(monkeypatch, settings):
    calls = []

    def failing_collect(codes, *args, **kwargs):
        calls.append(('trades', tuple(codes)))
        raise RuntimeError('биржа недоступна')

    def failing_news(codes, since, trigger):
        calls.append(('news', tuple(codes), trigger))
        raise RuntimeError('новости недоступны')

    monkeypatch.setattr(scheduler, 'collect', failing_collect)
    monkeypatch.setattr(scheduler, 'collect_news', failing_news)
    settings.SCRAPER_CONFIG = {**settings.SCRAPER_CONFIG,
                               'schedule': {**settings.SCRAPER_CONFIG['schedule'], 'exchanges': ['ets', 'caspy']},
                               'news': {'enabled': True, 'days': 30}}

    scheduler.collect_scheduled()  # ни торги, ни новости не роняют задачу
    assert calls == [('trades', ('ets',)), ('trades', ('caspy',)), ('news', ('ets', 'caspy'), 'schedule')]

    calls.clear()
    settings.SCRAPER_CONFIG = {**settings.SCRAPER_CONFIG, 'news': {'enabled': False}}
    scheduler.collect_scheduled()
    assert [call[0] for call in calls] == ['trades', 'trades']
