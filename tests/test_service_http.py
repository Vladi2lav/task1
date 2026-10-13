from datetime import date

import pytest
import requests

from exchanges.models import ParseRun
from scrapers import http as http_module
from scrapers.base import ScraperError
from scrapers.http import HttpClient
from scrapers.scheduler import next_run_time
from scrapers.service import collect

CONFIG = {'delay_seconds': 0, 'timeout_seconds': 5, 'retries': 3, 'user_agent': 'TestBot/1.0'}


class FakeResponse:
    def __init__(self, status_code=200, content='привет'.encode('utf-8')):
        self.status_code, self.content = status_code, content

    @property
    def ok(self):
        return self.status_code < 400


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(http_module.time, 'sleep', lambda seconds: None)


def client_with(monkeypatch, responses):
    """HttpClient, у которого session.get по очереди отдаёт ответы (или бросает исключения)."""
    client = HttpClient(CONFIG)
    queue = list(responses)
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(client.session, 'get', fake_get)
    client.calls = calls
    return client


def test_http_sends_user_agent_and_decodes_utf8():
    client = HttpClient(CONFIG)
    assert client.session.headers['User-Agent'] == 'TestBot/1.0'


def test_http_get_text_is_utf8(monkeypatch, no_sleep):
    client = client_with(monkeypatch, [FakeResponse()])
    assert client.get_text('http://x') == 'привет'


def test_http_retries_then_succeeds(monkeypatch, no_sleep):
    client = client_with(monkeypatch, [requests.ConnectionError('нет связи'), FakeResponse(503), FakeResponse()])
    assert client.get_text('http://x') == 'привет'
    assert len(client.calls) == 3


def test_http_gives_up_with_scraper_error(monkeypatch, no_sleep):
    client = client_with(monkeypatch, [FakeResponse(500)] * 3)
    with pytest.raises(ScraperError):
        client.get_text('http://x')
    assert len(client.calls) == 3


def test_http_404_is_not_retried(monkeypatch, no_sleep):
    client = client_with(monkeypatch, [FakeResponse(404)])
    with pytest.raises(ScraperError, match='404'):
        client.get_text('http://x')
    assert len(client.calls) == 1


@pytest.mark.django_db
def test_collect_runs_every_exchange_even_if_one_fails():
    runs = collect(['caspy', 'caspy'], date(2026, 10, 1), date(2026, 10, 14), trigger='schedule')
    assert [run.status for run in runs] == [ParseRun.Status.FAILED] * 2
    assert {run.trigger for run in runs} == {'schedule'}


def test_collect_rejects_unknown_exchange():
    with pytest.raises(ValueError, match='nope'):
        collect(['nope'], date(2026, 10, 1), date(2026, 10, 14))


def test_next_run_time_follows_config():
    run_time = next_run_time()
    assert (run_time.hour, run_time.minute) == (18, 30)
    assert str(run_time.tzinfo) == 'Asia/Almaty'
