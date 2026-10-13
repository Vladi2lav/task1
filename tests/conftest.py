"""Общие заготовки тестов: заглушка HTTP-клиента, отдающая файлы из tests/fixtures (сеть не используется)."""
from pathlib import Path

import pytest

from scrapers.base import ScraperError

FIXTURES = Path(__file__).parent / 'fixtures'

ETS = 'https://ets.kz'
RESULTS = f'{ETS}/documents/trading-results/'
YEAR_2026 = f'{RESULTS}itogi-torgov-2026/'
MONTH_OCT_2026 = f'{YEAR_2026}itogi-torgov-2026-god-oktyabr/'
XLSX_07_10 = f'{ETS}/upload/iblock/f5b/6578nw2x6pqtu6gmpvabg8hpc6igqlbz.xlsx'

# главная страница итогов: два года
INDEX_HTML = f'''<html><body>
<a href="/documents/trading-results/2025-god/">Итоги торгов 2025 год</a>
<a href="/documents/trading-results/itogi-torgov-2026/">Итоги торгов 2026 год</a>
</body></html>'''


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_text(name: str) -> str:
    return fixture_bytes(name).decode('utf-8')


class FakeHttp:
    """Вместо сети отдаёт заранее заданные страницы и файлы; запоминает запрошенные адреса."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.requested = []

    def _get(self, url):
        self.requested.append(url)
        if url not in self.pages:
            raise ScraperError(f'Не удалось загрузить {url}: HTTP 404')
        return self.pages[url]

    def get_text(self, url):
        return self._get(url)

    def get_bytes(self, url):
        return self._get(url)


@pytest.fixture
def ets_site():
    """Сайт ETS в заглушке: октябрь 2026 с одним файлом за 07.10.2026."""
    return FakeHttp({
        RESULTS: INDEX_HTML,
        YEAR_2026: fixture_text('ets_year_2026.html'),
        MONTH_OCT_2026: fixture_text('ets_month_oktyabr_2026.html'),
        XLSX_07_10: fixture_bytes('ets_2026-10-07.xlsx'),
    })
