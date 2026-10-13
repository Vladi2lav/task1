"""HTTP-клиент для парсеров: вежливые паузы, таймаут, повторы с растущей задержкой."""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from django.conf import settings

from .base import ScraperError

logger = logging.getLogger(__name__)

# коды, при которых имеет смысл повторить запрос
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpClient:
    """Обёртка над requests.Session. Параметры берутся из [http] в scraper.toml."""

    def __init__(self, config: Optional[dict] = None):
        config = config if config is not None else settings.SCRAPER_CONFIG['http']
        self.delay = float(config.get('delay_seconds', 1.5))
        self.timeout = float(config.get('timeout_seconds', 30))
        self.retries = int(config.get('retries', 3))
        self.session = requests.Session()
        self.session.headers['User-Agent'] = config.get('user_agent', 'TradeResultsBot/1.0')
        self._last_request_at = 0.0

    def get_bytes(self, url: str) -> bytes:
        """Скачать содержимое по URL (для XLSX)."""
        return self._get(url).content

    def get_text(self, url: str) -> str:
        """Скачать страницу как текст. Кодировка принудительно UTF-8."""
        return self._get(url).content.decode('utf-8', errors='replace')

    def _pause(self) -> None:
        """Выдержать паузу с момента прошлого запроса."""
        wait = self._last_request_at + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _get(self, url: str) -> requests.Response:
        last_error = ''
        for attempt in range(1, self.retries + 1):
            self._pause()
            self._last_request_at = time.monotonic()
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f'{type(exc).__name__}: {exc}'
            else:
                if response.ok:
                    return response
                last_error = f'HTTP {response.status_code}'
                if response.status_code not in _RETRY_STATUSES:
                    break  # 404 и подобные повторять бессмысленно

            if attempt < self.retries:
                backoff = self.delay * 2 ** attempt  # растущая задержка: 2x, 4x, ...
                logger.warning('Запрос %s не удался (%s), попытка %d из %d, повтор через %.1f с',
                               url, last_error, attempt, self.retries, backoff)
                time.sleep(backoff)

        raise ScraperError(f'Не удалось загрузить {url}: {last_error}')
