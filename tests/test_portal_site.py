"""Тесты сайта в целом: юридические страницы, подвал, баннер cookie, режим «только чтение»,
фильтр места/базиса поставки, страницы ошибок, заголовки и меню.

Данные создаются прямо в тестах; сеть не используется.
"""
import json
import re
import sys
import types
from datetime import date

import pytest
from django.template.loader import get_template, render_to_string
from django.test import Client

from exchanges.models import Exchange, Product, Trade
from portal import collect_runner, legal
from portal.templatetags.portal_extras import http_url

pytestmark = pytest.mark.django_db

LEGAL_PAGES = {
    '/legal/terms/': 'Пользовательское соглашение',
    '/legal/privacy/': 'Политика конфиденциальности',
    '/legal/cookies/': 'Политика использования cookie',
    '/legal/sources/': 'Источники данных и лицензии',
}

SITE_PAGES = {
    '/': 'Итоги торгов',
    '/analytics/': 'Аналитика',
    '/news/': 'Новости бирж',
    '/status/': 'Состояние данных',
    **LEGAL_PAGES,
}


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def clean_collect_state():
    collect_runner.wait_finished(2)
    collect_runner.reset_state()
    yield
    collect_runner.wait_finished(2)
    collect_runner.reset_state()


def title_of(html):
    match = re.search(r'<title>(.*?)</title>', html, re.S)
    return match.group(1).strip() if match else ''


# ---------------------------------------------------- заголовки, меню, подвал

@pytest.mark.parametrize('address, title', SITE_PAGES.items())
def test_every_page_has_clear_title_and_footer(client, address, title):
    response = client.get(address)
    assert response.status_code == 200
    html = response.content.decode('utf-8')
    assert title_of(html) == f'{title} — ETS и Каспий'
    # подвал: ссылки на правовые страницы, источники данных и год
    for legal_address in LEGAL_PAGES:
        assert f'href="{legal_address}"' in html
    assert 'Данные: <a href="https://ets.kz/"' in html and '>Каспий</a>' in html
    footer = html[html.index('<footer class="site-footer">'):]
    assert re.search(r'20\d\d', footer)
    # favicon (inline SVG) и баннер cookie на каждой странице
    assert 'rel="icon" type="image/svg+xml" href="data:image/svg+xml,' in html
    assert 'id="cookie-banner"' in html


@pytest.mark.parametrize('address, label', [
    ('/', 'Итоги торгов'), ('/analytics/', 'Аналитика'), ('/news/', 'Новости'), ('/status/', 'Состояние данных'),
])
def test_active_menu_item(client, address, label):
    html = client.get(address).content.decode('utf-8')
    nav = html[html.index('class="main-nav"'):html.index('</nav>')]
    assert f'aria-current="page" class="active">{label}</a>' in nav
    assert nav.count('class="active"') == 1


@pytest.mark.parametrize('address', LEGAL_PAGES)
def test_legal_page_marks_current_link(client, address):
    html = client.get(address).content.decode('utf-8')
    assert f'href="{address}" aria-current="page"' in html
    assert 'class="active"' not in html[html.index('class="main-nav"'):html.index('</nav>')]


# ------------------------------------------------------- юридические страницы

def test_terms_content(client):
    html = client.get('/legal/terms/').content.decode('utf-8')
    for text in (
        'https://ets.kz/documents/trading-results/', 'https://ccx.kz/trading-and-analytics/trading-results',
        'не являются публичной офертой', 'инвестиционной рекомендацией', 'законодательство Республики Казахстан',
        'Интеллектуальная собственность', 'Изменение Соглашения', 'Дата редакции:',
    ):
        assert text in html, text


def test_privacy_content(client):
    html = client.get('/legal/privacy/').content.decode('utf-8')
    for text in ('№ 94-V', 'О персональных данных и их защите', 'IP-адрес', 'БИН', 'хостинг-провайдер',
                 'Права субъекта персональных данных', 'требование об удалении'):
        assert text.lower() in html.lower(), text


def test_cookies_content_lists_real_cookies(client, settings):
    html = client.get('/legal/cookies/').content.decode('utf-8')
    assert '<code>csrftoken</code>' in html
    assert '<code>sessionid</code>' in html
    assert '<code>cookie_consent</code>' in html
    assert 'аналитических и статистических cookie' in html
    # срок берётся из настроек Django
    assert f'{round(settings.CSRF_COOKIE_AGE / 86400)} дн.' in html


def test_sources_page_lists_libraries_and_licenses(client):
    html = client.get('/legal/sources/').content.decode('utf-8')
    for name, license_name in (('Django', 'BSD-3-Clause'), ('requests', 'Apache-2.0'), ('Beautiful Soup 4', 'MIT'),
                               ('lxml', 'BSD-3-Clause'), ('openpyxl', 'MIT'), ('APScheduler', 'MIT'),
                               ('Chart.js', 'MIT')):
        assert f'<td>{name}</td>' in html
        assert license_name in html
    assert 'THIRD_PARTY_LICENSES.md' in html
    assert 'https://ets.kz/' in html and 'https://ccx.kz/' in html


def test_optional_libraries_from_requirements(tmp_path):
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text('Django>=5.2\ngunicorn==23.0  # сервер\nwhitenoise\npsycopg[binary]>=3.2\n',
                            encoding='utf-8')
    names = [lib['name'] for lib in legal.used_libraries(requirements)]
    assert 'Gunicorn' in names and 'WhiteNoise' in names and 'psycopg 3' in names
    assert 'Django' in names
    assert legal.used_libraries(tmp_path / 'missing.txt') == legal.CORE_LIBRARIES


def test_owner_placeholders_and_settings(client, settings):
    for name in ('SITE_OWNER', 'SITE_CONTACT_EMAIL'):
        if hasattr(settings, name):
            delattr(settings, name)
    html = client.get('/legal/terms/').content.decode('utf-8')
    assert 'Владелец сайта: ИМЯ ФАМИЛИЯ' in html
    assert 'ПОЧТА' in html
    settings.SITE_OWNER = 'Иванов Иван Иванович'
    settings.SITE_CONTACT_EMAIL = 'owner@example.kz'
    html = client.get('/legal/privacy/').content.decode('utf-8')
    assert 'Иванов Иван Иванович' in html
    assert 'mailto:owner@example.kz' in html


# ------------------------------------------------------------------ баннер

def test_cookie_banner_markup(client):
    html = client.get('/').content.decode('utf-8')
    banner = html[html.index('id="cookie-banner"'):]
    banner = banner[:banner.index('</section>')]
    assert 'role="region"' in html[html.index('<section class="cookie-banner"'):html.index('id="cookie-banner"') + 200]
    assert 'hidden' in html[html.index('<section class="cookie-banner"'):html.index('id="cookie-banner"') + 200]
    assert 'только необходимые cookie' in banner
    assert 'href="/legal/cookies/"' in banner and 'href="/legal/privacy/"' in banner
    assert 'id="cookie-accept"' in banner and 'Принять' in banner and 'Подробнее' in banner


def test_cookie_banner_script_uses_local_storage():
    from pathlib import Path
    script = (Path(__file__).resolve().parent.parent / 'portal' / 'static' / 'portal' / 'app.js').read_text('utf-8')
    assert "'cookie_consent'" in script
    assert 'localStorage' in script and 'try {' in script


# ------------------------------------------------------- режим только чтения

@pytest.fixture
def fake_service(monkeypatch):
    calls = []
    module = types.ModuleType('scrapers.service')
    module.collect = lambda *args, **kwargs: calls.append(args)
    monkeypatch.setitem(sys.modules, 'scrapers.service', module)
    return calls


def test_status_page_normal_mode_has_collect_button(client, settings):
    settings.PORTAL_READ_ONLY = False
    html = client.get('/status/').content.decode('utf-8')
    assert 'id="btn-collect"' in html
    assert 'Демо-версия' not in html
    # подсказка по запуску из консоли
    assert 'python manage.py collect_trades' in html and 'python manage.py run_scheduler' in html


def test_read_only_hides_button_and_explains(client, settings):
    settings.PORTAL_READ_ONLY = True
    html = client.get('/status/').content.decode('utf-8')
    assert 'Демо-версия: сбор данных отключён' in html
    assert 'id="btn-collect"' not in html and 'id="collect-form"' not in html
    assert 'python manage.py collect_trades' in html
    assert client.get('/api/status/').json()['read_only'] is True


def test_read_only_rejects_collect_with_403(client, settings, fake_service):
    settings.PORTAL_READ_ONLY = True
    response = client.post('/api/collect/', data=json.dumps({'days': 1}), content_type='application/json')
    assert response.status_code == 403
    assert 'Демо-версия: сбор данных отключён' in response.json()['error']
    assert fake_service == [] and collect_runner.is_running() is False


def test_collect_allowed_when_not_read_only(client, settings, fake_service):
    settings.PORTAL_READ_ONLY = False
    response = client.post('/api/collect/', data=json.dumps({'days': 1}), content_type='application/json')
    assert response.status_code == 202
    assert client.get('/api/status/').json()['read_only'] is False


# ------------------------------------------------- место / базис поставки

@pytest.fixture
def delivery_data():
    ets = Exchange.objects.create(code='ets', name='ETS')
    caspy = Exchange.objects.create(code='caspy', name='Каспий')
    ets_coal = Product.objects.create(name='Уголь марки Д, FCA ст. Шубарколь', code='C1')
    caspy_coal = Product.objects.create(name='Уголь каменный Шубарколь', code='C2')
    caspy_fuel = Product.objects.create(name='Дизельное топливо', code='F1')
    trades = {
        'ets': Trade.objects.create(exchange=ets, product=ets_coal, trade_date=date(2026, 10, 1),
                                    delivery_basis='FCA', delivery_place='', source_key='d1'),
        'caspy_coal': Trade.objects.create(exchange=caspy, product=caspy_coal, trade_date=date(2026, 10, 2),
                                           delivery_basis='DAP', delivery_place='Станция Екибастуз',
                                           source_key='d2'),
        'caspy_fuel': Trade.objects.create(exchange=caspy, product=caspy_fuel, trade_date=date(2026, 10, 3),
                                           delivery_basis='EXW', delivery_place='Нефтебаза Павлодар',
                                           source_key='d3'),
    }
    return trades


def found(client, **params):
    return {r['id'] for r in client.get('/api/trades/', params).json()['results']}


@pytest.mark.parametrize('query, expected', [
    ('екибастуз', {'caspy_coal'}),
    ('ЕКИБАСТУЗ', {'caspy_coal'}),
    ('fca', {'ets'}),            # базис поставки ETS
    ('dap', {'caspy_coal'}),     # базис поставки Каспия
    ('шубарколь', {'ets'}),      # у ETS места нет — ищем в названии товара
    ('станция', {'caspy_coal'}),
    ('мурманск', set()),
])
def test_delivery_filter_place_or_basis(client, delivery_data, query, expected):
    assert found(client, delivery=query) == {delivery_data[k].id for k in expected}


def test_delivery_filter_in_export(client, delivery_data):
    response = client.get('/api/trades/export.xlsx', {'delivery': 'fca'})
    assert response.status_code == 200 and response['X-Export-Rows'] == '1'


def test_delivery_suggestions_include_basis(client, delivery_data):
    body = client.get('/api/filters/', {'field': 'delivery', 'term': 'fc'}).json()
    assert body['suggestions'] == ['FCA']
    body = client.get('/api/filters/', {'field': 'delivery', 'term': 'ЕКИБ'}).json()
    assert body['suggestions'] == ['Станция Екибастуз']
    body = client.get('/api/filters/', {'field': 'delivery', 'term': 'ст'}).json()
    assert body['suggestions'] == ['Станция Екибастуз']


def test_top_delivery_places_include_basis(client, delivery_data):
    places = {p['name']: p['count'] for p in client.get('/api/filters/').json()['delivery_places']}
    assert places == {'FCA': 1, 'DAP': 1, 'EXW': 1, 'Станция Екибастуз': 1, 'Нефтебаза Павлодар': 1}


# ---------------------------------------------------------- ошибки 404/500

def test_404_page_in_site_style(client):
    response = client.get('/no-such-page/')
    assert response.status_code == 404
    html = response.content.decode('utf-8')
    assert 'Страница не найдена' in html
    assert title_of(html) == 'Страница не найдена — ETS и Каспий'
    assert 'href="/legal/terms/"' in html and 'class="main-nav"' in html


def test_error_templates_are_found_in_portal():
    for name in ('404.html', '500.html'):
        origin = get_template(name).origin.name.replace('\\', '/')
        assert origin.endswith(f'portal/templates/{name}')
    html = render_to_string('500.html')
    assert 'Внутренняя ошибка сервера' in html and 'portal/style' in html


def test_favicon_ico_served(client):
    response = client.get('/favicon.ico')
    assert response.status_code == 200
    assert response['Content-Type'] == 'image/svg+xml'
    assert b'<svg' in response.content


# ------------------------------------------------------------- фильтр ссылок

@pytest.mark.parametrize('value, expected', [
    ('https://ets.kz/news/1', 'https://ets.kz/news/1'),
    ('HTTP://ccx.kz/a', 'HTTP://ccx.kz/a'),
    ('javascript:alert(1)', ''),
    ('  data:text/html,x', ''),
    ('', ''),
    (None, ''),
])
def test_http_url_filter(value, expected):
    assert http_url(value) == expected
