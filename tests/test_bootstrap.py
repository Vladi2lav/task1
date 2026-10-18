"""Команда первичной инициализации manage.py bootstrap и проверка /healthz."""
import json
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from exchanges.models import Exchange, News, Trade


def run_bootstrap(*args):
    out = StringIO()
    call_command('bootstrap', '--skip-static', *args, stdout=out)
    return out.getvalue()


def write_fixture(path, objects):
    path.write_text(json.dumps(objects, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def fixture_dir(tmp_path):
    """Маленький дамп: биржа, одна новость (итоги торгов в тесте не нужны — достаточно проверить загрузку)."""
    write_fixture(tmp_path / 'trades_fixture.json', [
        {'model': 'exchanges.exchange', 'pk': 1, 'fields': {'code': 'ets', 'name': 'ETS', 'website': 'https://ets.kz'}},
    ])
    write_fixture(tmp_path / 'news_fixture.json', [
        {'model': 'exchanges.news', 'pk': 1, 'fields': {
            'exchange': 1, 'title': 'Новость', 'published_at': '2026-10-01',
            'url': 'https://ets.kz/press_centre/news/1/', 'summary': '', 'image_url': '',
            'created_at': '2026-10-01T00:00:00Z'}},
    ])
    return tmp_path


@pytest.mark.django_db
def test_bootstrap_loads_real_dump_into_empty_db():
    assert not Trade.objects.exists()
    output = run_bootstrap()
    assert Trade.objects.count() > 0
    assert Exchange.objects.filter(code='ets').exists()
    assert 'Готово' in output


@pytest.mark.django_db
def test_bootstrap_is_idempotent(fixture_dir):
    run_bootstrap('--fixture-dir', str(fixture_dir))
    assert News.objects.count() == 1
    output = run_bootstrap('--fixture-dir', str(fixture_dir))  # повторно: ничего не дублируется и не падает
    assert News.objects.count() == 1
    assert Exchange.objects.count() == 1
    assert 'Не удалось' not in output


@pytest.mark.django_db
def test_bootstrap_without_dump_files(tmp_path):
    output = run_bootstrap('--fixture-dir', str(tmp_path))  # в папке нет файлов
    assert 'не найден' in output
    assert not Trade.objects.exists()


@pytest.mark.django_db
def test_bootstrap_no_fixture_flag():
    run_bootstrap('--no-fixture')
    assert not Trade.objects.exists()


@pytest.mark.django_db
def test_bootstrap_creates_superuser_once(monkeypatch, tmp_path):
    monkeypatch.setenv('DJANGO_SUPERUSER_USERNAME', 'admin')
    monkeypatch.setenv('DJANGO_SUPERUSER_PASSWORD', 'S3cure-pass-for-test')
    monkeypatch.setenv('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')
    run_bootstrap('--no-fixture')
    output = run_bootstrap('--no-fixture')
    user = get_user_model().objects.get(username='admin')
    assert user.is_superuser and user.check_password('S3cure-pass-for-test')
    assert 'уже существует' in output


@pytest.mark.django_db
def test_bootstrap_without_password_does_not_create_user(monkeypatch):
    monkeypatch.setenv('DJANGO_SUPERUSER_USERNAME', 'admin')
    monkeypatch.delenv('DJANGO_SUPERUSER_PASSWORD', raising=False)
    run_bootstrap('--no-fixture')
    assert not get_user_model().objects.filter(username='admin').exists()


@pytest.mark.django_db
def test_healthz_ok(client):
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'database': 'ok'}
