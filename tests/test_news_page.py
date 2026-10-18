"""Тесты ленты новостей: страница /news/, API /api/news/, блок «Последние новости» на главной.

Новости создаются прямо в тестах; сеть не используется.
"""
from datetime import date, timedelta

import pytest
from django.test import Client

from exchanges.models import Exchange, News

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def exchanges():
    ets = Exchange.objects.get_or_create(code='ets', defaults={'name': 'ETS'})[0]
    caspy = Exchange.objects.get_or_create(code='caspy', defaults={'name': 'Каспий'})[0]
    return ets, caspy


def make_news(exchange, title, published_at, n, **fields):
    fields.setdefault('url', f'https://example.kz/news/{n}')
    return News.objects.create(exchange=exchange, title=title, published_at=published_at, **fields)


@pytest.fixture
def news(exchanges):
    ets, caspy = exchanges
    d = date(2026, 10, 1)
    return {
        'ets_coal': make_news(ets, 'Итоги торгов углём за сентябрь', d, 1,
                              summary='Объём торгов углём вырос.', image_url='https://ets.kz/img/1.jpg'),
        'ets_grain': make_news(ets, 'Биржевые торги ПШЕНИЦЕЙ: новый рекорд', d + timedelta(days=2), 2),
        'caspy_fuel': make_news(caspy, 'Каспий запускает секцию ГСМ', d + timedelta(days=1), 3,
                                summary='Новая торговая секция.'),
        'caspy_cement': make_news(caspy, 'Торги цементом возобновлены', d + timedelta(days=3), 4),
    }


# ------------------------------------------------------------------- API

def test_api_news_format_and_order(client, news):
    response = client.get('/api/news/')
    assert response.status_code == 200
    assert response['Content-Type'].startswith('application/json')
    body = response.json()
    assert {'count', 'page', 'pages', 'results'} <= set(body)
    assert (body['count'], body['page'], body['pages']) == (4, 1, 1)
    # свежие сверху
    assert [r['id'] for r in body['results']] == [
        news['caspy_cement'].id, news['ets_grain'].id, news['caspy_fuel'].id, news['ets_coal'].id,
    ]
    item = next(r for r in body['results'] if r['id'] == news['ets_coal'].id)
    assert item == {
        'id': news['ets_coal'].id,
        'exchange': 'ETS',
        'exchange_code': 'ets',
        'title': 'Итоги торгов углём за сентябрь',
        'published_at': '2026-10-01',
        'url': 'https://example.kz/news/1',
        'summary': 'Объём торгов углём вырос.',
        'image_url': 'https://ets.kz/img/1.jpg',
    }


def test_api_news_russian_text_not_escaped(client, news):
    assert 'Каспий запускает секцию ГСМ' in client.get('/api/news/').content.decode('utf-8')


def test_api_news_filter_by_exchange(client, news):
    body = client.get('/api/news/', {'exchange': 'caspy'}).json()
    assert {r['id'] for r in body['results']} == {news['caspy_fuel'].id, news['caspy_cement'].id}
    assert all(r['exchange_code'] == 'caspy' for r in body['results'])
    assert client.get('/api/news/', {'exchange': 'ETS'}).json()['count'] == 2
    assert client.get('/api/news/', {'exchange': 'all'}).json()['count'] == 4


@pytest.mark.parametrize('query, expected', [
    ('пшеницей', {'ets_grain'}),
    ('ПШЕНИЦЕЙ', {'ets_grain'}),
    ('торги', {'ets_grain', 'caspy_cement'}),
    ('итоги углём', {'ets_coal'}),
    ('нет такого', set()),
])
def test_api_news_search_by_title_case_insensitive(client, news, query, expected):
    body = client.get('/api/news/', {'q': query}).json()
    assert {r['id'] for r in body['results']} == {news[k].id for k in expected}


def test_api_news_pagination(client, news):
    first = client.get('/api/news/', {'page_size': 3}).json()
    assert (first['count'], first['pages'], first['page'], len(first['results'])) == (4, 2, 1, 3)
    second = client.get('/api/news/', {'page_size': 3, 'page': 2}).json()
    assert second['page'] == 2 and len(second['results']) == 1
    # страница за пределом заменяется последней
    assert client.get('/api/news/', {'page_size': 3, 'page': 99}).json()['page'] == 2
    # размер страницы ограничен 1–100
    assert client.get('/api/news/', {'page_size': 0}).json()['page_size'] == 1
    assert client.get('/api/news/', {'page_size': 5000}).json()['page_size'] == 100


def test_api_news_empty(client):
    body = client.get('/api/news/').json()
    assert body['count'] == 0 and body['results'] == [] and body['pages'] == 1


@pytest.mark.parametrize('params', [{'exchange': 'nyse'}, {'page': 'abc'}, {'page_size': 'много'}])
def test_api_news_bad_params_400(client, news, params):
    response = client.get('/api/news/', params)
    assert response.status_code == 400
    assert response.json()['error']


def test_api_news_get_only(client):
    assert client.post('/api/news/').status_code == 405


# --------------------------------------------------------------- страница

def test_news_page_cards(client, news):
    response = client.get('/news/')
    assert response.status_code == 200
    html = response.content.decode('utf-8')
    assert '<title>Новости бирж — ETS и Каспий</title>' in html
    assert 'data-page="news"' in html
    for item in news.values():
        assert item.title in html
    # заголовок — ссылка на оригинал в новой вкладке
    assert 'href="https://example.kz/news/1" target="_blank" rel="noopener noreferrer"' in html
    assert '01.10.2026' in html and 'datetime="2026-10-01"' in html
    assert 'badge badge-caspy' in html and 'badge badge-ets' in html
    assert 'Объём торгов углём вырос.' in html
    # свежая новость выше старой
    assert html.index('Торги цементом возобновлены') < html.index('Итоги торгов углём за сентябрь')


def test_news_page_menu_item_active(client, news):
    html = client.get('/news/').content.decode('utf-8')
    assert 'class="active">Новости' in html
    for address in ('/', '/analytics/', '/status/'):
        page = client.get(address).content.decode('utf-8')
        assert 'href="/news/"' in page and '>Новости<' in page


def test_news_page_filter_and_search(client, news):
    html = client.get('/news/', {'exchange': 'caspy'}).content.decode('utf-8')
    assert 'Каспий запускает секцию ГСМ' in html
    assert 'Итоги торгов углём за сентябрь' not in html
    assert '<option value="caspy" selected>' in html

    html = client.get('/news/', {'q': 'ЦЕМЕНТОМ'}).content.decode('utf-8')
    assert 'Торги цементом возобновлены' in html
    assert 'Каспий запускает секцию ГСМ' not in html
    assert 'value="ЦЕМЕНТОМ"' in html


def test_news_page_unknown_exchange_shows_all(client, news):
    response = client.get('/news/', {'exchange': 'nyse'})
    assert response.status_code == 200
    assert 'Каспий запускает секцию ГСМ' in response.content.decode('utf-8')


def test_news_page_no_matches(client, news):
    html = client.get('/news/', {'q': 'несуществующее'}).content.decode('utf-8')
    assert 'Ничего не найдено' in html
    assert 'Новостей пока нет' not in html


def test_news_page_empty_state(client, exchanges):
    response = client.get('/news/')
    assert response.status_code == 200
    html = response.content.decode('utf-8')
    assert 'Новостей пока нет — запустите сбор' in html


def test_news_page_pagination_keeps_filters(client, exchanges):
    ets, _ = exchanges
    for n in range(45):
        make_news(ets, f'Новость ETS номер {n}', date(2026, 1, 1) + timedelta(days=n), 100 + n)
    html = client.get('/news/', {'exchange': 'ets'}).content.decode('utf-8')
    assert 'страница 1 из 3' in html
    assert 'href="?exchange=ets&amp;page=2"' in html
    assert 'Новость ETS номер 44' in html and 'Новость ETS номер 0<' not in html
    last = client.get('/news/', {'exchange': 'ets', 'page': 3}).content.decode('utf-8')
    assert 'Новость ETS номер 0<' in last
    assert 'aria-current="page">3<' in last
    assert '<title>Новости бирж, страница 3 — ETS и Каспий</title>' in last
    # некорректный номер страницы не ломает страницу
    assert client.get('/news/', {'page': 'abc'}).status_code == 200


def test_news_page_unsafe_url_is_not_a_link(client, exchanges):
    ets, _ = exchanges
    make_news(ets, 'Подозрительная новость', date(2026, 10, 5), 0, url='javascript:alert(1)')
    html = client.get('/news/').content.decode('utf-8')
    assert 'Подозрительная новость' in html
    assert 'javascript:alert' not in html


# ----------------------------------------------------- блок на главной

def test_latest_news_block_on_main_page(client, exchanges):
    ets, caspy = exchanges
    for n in range(7):
        make_news(ets if n % 2 else caspy, f'Последняя новость {n}', date(2026, 10, 1) + timedelta(days=n), 200 + n)
    html = client.get('/').content.decode('utf-8')
    assert 'Последние новости' in html
    assert 'href="/news/"' in html and 'Все новости' in html
    shown = [n for n in range(7) if f'Последняя новость {n}<' in html]
    assert shown == [2, 3, 4, 5, 6]  # пять самых свежих
    assert html.index('Последняя новость 6') < html.index('Последняя новость 2')
    assert 'target="_blank" rel="noopener noreferrer">Последняя новость 6' in html


def test_latest_news_block_empty(client):
    html = client.get('/').content.decode('utf-8')
    assert 'Последние новости' in html
    assert 'Новостей пока нет' in html
