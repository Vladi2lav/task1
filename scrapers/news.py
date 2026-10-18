"""Сбор новостей с сайтов бирж (дополнительное задание).

Парсеры новостей обходят ленту новостей биржи постранично, пока даты новостей не станут
старше заданной (`since`), но не дальше `[news].max_pages` страниц. Для новых новостей
дополнительно открывается страница самой новости: оттуда берутся анонс (начало текста)
и картинка — в списках бирж их нет.

Сохранение (`collect_news`) — upsert по ссылке: новая новость добавляется, у известной
обновляются заголовок/анонс/картинка/дата, если они изменились. Журнал ParseRun не ведётся
(он про итоги торгов) — результат возвращается словарём и пишется в лог.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup
from django.conf import settings

from exchanges.models import Exchange, News

from .base import ScraperError
from .http import HttpClient
from .normalize import clean_text, parse_ru_date

logger = logging.getLogger(__name__)

SUMMARY_LENGTH = 400   # длина анонса, символов
MAX_FIELD = 500        # ограничение полей title/url/image_url в модели News


@dataclass
class NewsItem:
    """Одна новость в едином формате (на выходе любого парсера новостей)."""

    exchange_code: str
    title: str
    published_at: date
    url: str
    summary: str = ''
    image_url: str = ''


def make_summary(text: str, title: str = '', length: int = SUMMARY_LENGTH) -> str:
    """Анонс из текста новости: без повтора заголовка в начале, обрезан по границе слова."""
    text = clean_text(text)
    title = clean_text(title)
    if title and text.lower().startswith(title.lower()):
        text = text[len(title):].lstrip(' .:—-')
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(' ', 1)[0].rstrip(' ,.;:—-')
    return cut + '…'


class BaseNewsScraper:
    """Общий обход ленты: страницы списка -> NewsItem; детали — со страницы новости."""

    exchange_code: str = ''
    exchange_name: str = ''

    def __init__(self, http: Optional[HttpClient] = None, source: Optional[dict] = None,
                 news_config: Optional[dict] = None):
        scraper_config = getattr(settings, 'SCRAPER_CONFIG', {})
        self.source = source if source is not None else scraper_config['sources'][self.exchange_code]
        news_config = news_config if news_config is not None else scraper_config.get('news', {})
        self.base_url = self.source['base_url']
        self.website = self.base_url
        self.max_pages = max(1, int(news_config.get('max_pages', 10)))
        self.details = bool(news_config.get('details', True))
        self.http = http or HttpClient()
        self.errors: List[str] = []

    # --- что переопределяют наследники ------------------------------------

    def feeds(self) -> List[str]:
        """Ленты, которые нужно обойти (у Каспия — по категориям)."""
        return ['']

    def page_url(self, feed: str, page: int) -> str:
        raise NotImplementedError

    def parse_list(self, html: str, page: int) -> Tuple[List[NewsItem], bool]:
        """Новости со страницы списка и признак, что есть следующая страница."""
        raise NotImplementedError

    def detail_url(self, item: NewsItem) -> str:
        return item.url

    def parse_detail(self, html: str, item: NewsItem) -> Tuple[str, str]:
        """(анонс, картинка) со страницы новости."""
        raise NotImplementedError

    # --- общий обход --------------------------------------------------------

    def fetch(self, since: date, skip_details: Iterable[str] = ()) -> Iterator[NewsItem]:
        """Новости с датой >= since, свежие сначала.

        skip_details — ссылки, для которых страницу новости открывать не нужно
        (анонс уже сохранён в БД), чтобы не делать лишних запросов.
        """
        self.errors = []
        skip = set(skip_details)
        seen: Set[str] = set()
        for feed in self.feeds():
            yield from self._walk_feed(feed, since, seen, skip)

    def _walk_feed(self, feed: str, since: date, seen: Set[str], skip: Set[str]) -> Iterator[NewsItem]:
        for page in range(1, self.max_pages + 1):
            url = self.page_url(feed, page)
            try:
                items, has_next = self.parse_list(self.http.get_text(url), page)
            except ScraperError as error:
                self._report_error(f'{url}: {error}')
                return
            reached_old = False
            new_on_page = 0
            for item in items:
                if item.published_at < since:
                    reached_old = True  # лента идёт от свежих к старым: дальше только старее
                    continue
                if item.url in seen:
                    continue
                seen.add(item.url)
                new_on_page += 1
                if self.details and item.url not in skip:
                    self._add_details(item)
                yield item
            if reached_old or not has_next or not items:
                return
            if not new_on_page and page > 1:  # сайт отдал ту же страницу повторно — дальше идти незачем
                return
        logger.info('%s: достигнут предел max_pages=%d (%s)', self.exchange_name, self.max_pages, feed or 'лента')

    def _add_details(self, item: NewsItem) -> None:
        url = self.detail_url(item)
        try:
            summary, image_url = self.parse_detail(self.http.get_text(url), item)
        except ScraperError as error:
            self._report_error(f'{url}: {error}')  # новость сохраняется и без анонса
            return
        item.summary = item.summary or summary
        item.image_url = item.image_url or image_url

    def _report_error(self, message: str) -> None:
        logger.error('Новости %s: %s', self.exchange_name, message)
        self.errors.append(message)

    # --- помощники для наследников ------------------------------------------

    def absolute(self, href: str) -> str:
        return urldefrag(urljoin(self.base_url + '/', href.strip()))[0]

    def make_item(self, title_text: str, date_text: str, href: str, summary: str = '',
                  image: str = '') -> Optional[NewsItem]:
        """NewsItem из сырых строк; без даты/заголовка/ссылки — None (с записью в ошибки)."""
        title = clean_text(title_text, MAX_FIELD)
        published = parse_ru_date(date_text)
        url = self.absolute(href) if href else ''
        if not title or not url or published is None:
            self._report_error(f'пропущена новость без заголовка, ссылки или даты: '
                               f'«{title[:80]}», {clean_text(date_text)!r}, {href!r}')
            return None
        if len(url) > MAX_FIELD:
            self._report_error(f'слишком длинная ссылка, новость пропущена: {url[:120]}…')
            return None
        image_url = self.absolute(image) if image else ''
        return NewsItem(self.exchange_code, title, published, url, make_summary(summary, title),
                        image_url if len(image_url) <= MAX_FIELD else '')

    @staticmethod
    def first_image(*nodes) -> str:
        for node in nodes:
            if node is None:
                continue
            img = node.find('img', src=True)
            if img and img['src'].strip() and not img['src'].startswith('data:'):
                return img['src'].strip()
        return ''


class EtsNewsScraper(BaseNewsScraper):
    """ETS (ets.kz, Битрикс): /press_centre/news/, страницы ?PAGEN_1=N.

    В списке есть только дата и заголовок-ссылка; анонс и картинка — со страницы новости.
    """

    exchange_code = 'ets'
    exchange_name = 'ETS'

    def page_url(self, feed: str, page: int) -> str:
        url = urljoin(self.base_url, self.source.get('news_path', '/press_centre/news/'))
        return url if page == 1 else f'{url}?PAGEN_1={page}'

    def parse_list(self, html: str, page: int) -> Tuple[List[NewsItem], bool]:
        soup = BeautifulSoup(html, 'lxml')
        box = soup.select_one('div.i_press_centre_news_list')
        if box is None:
            raise ScraperError('на странице нет списка новостей (div.i_press_centre_news_list) — изменилась вёрстка?')
        items = []
        for node in box.select('div.i_press_centre_news_item'):
            link = node.find('a', href=True)
            date_node = node.select_one('.i_press_centre_news_item_date')
            item = self.make_item(link.get_text() if link else '', date_node.get_text() if date_node else '',
                                  link['href'] if link else '')
            if item:
                items.append(item)
        # Битрикс на номер страницы за пределами ленты отдаёт последнюю страницу,
        # поэтому конец ленты определяется по отсутствию ссылки «Вперёд»
        has_next = soup.select_one('li.i_pagination_next a[href]') is not None
        return items, has_next

    def parse_detail(self, html: str, item: NewsItem) -> Tuple[str, str]:
        soup = BeautifulSoup(html, 'lxml')
        detail = soup.select_one('div.i_press_centre_news_detail')
        text_node = soup.select_one('div.i_press_centre_news_detail_text')
        if detail is None or text_node is None:
            raise ScraperError('на странице новости нет блока текста (i_press_centre_news_detail_text)')
        og_image = soup.find('meta', property='og:image')
        image = self.first_image(detail) or (og_image.get('content', '') if og_image else '')
        return make_summary(text_node.get_text(' '), item.title), self.absolute(image) if image else ''


_PAGE_IN_ONCLICK = re.compile(r'[?&]page=(\d+)')


class CaspyNewsScraper(BaseNewsScraper):
    """Каспий (ccx.kz, Laravel): /news/category/<категория>?lang=ru&per_page=50&page=N.

    Внутренний JSON /api/internal/news/latest отдаёт только 7 последних новостей
    без пагинации, поэтому лента читается из HTML. Без lang=ru сайт отдаёт казахскую версию.
    """

    exchange_code = 'caspy'
    exchange_name = 'Каспий'

    def feeds(self) -> List[str]:
        return list(self.source.get('news_categories', ['announcement', 'corporate']))

    def page_url(self, feed: str, page: int) -> str:
        path = self.source.get('news_path', '/news/category/')
        per_page = int(self.source.get('news_per_page', 50))
        return f'{urljoin(self.base_url, path)}{feed}?lang=ru&per_page={per_page}&page={page}'

    def detail_url(self, item: NewsItem) -> str:
        return f'{item.url}?lang=ru'

    def parse_list(self, html: str, page: int) -> Tuple[List[NewsItem], bool]:
        soup = BeautifulSoup(html, 'lxml')
        prefix = 'stock-market-news-container-main--news-list-item'
        nodes = soup.select(f'a.{prefix}[href]')
        pagination = soup.select_one('.stock-market-news-container-main--news-list_pagination')
        if not nodes and pagination is None:
            raise ScraperError('на странице нет списка новостей — изменилась вёрстка?')
        items = []
        for node in nodes:
            title = node.select_one(f'.{prefix}--header-title')
            day = node.select_one(f'.{prefix}--footer-date')
            body = node.select_one(f'.{prefix}--body')
            item = self.make_item(title.get_text() if title else '', day.get_text() if day else '',
                                  node['href'], body.get_text(' ') if body else '',
                                  self.first_image(node))
            if item:
                items.append(item)
        pages = [int(m.group(1)) for el in (pagination.select('[onclick]') if pagination else [])
                 for m in _PAGE_IN_ONCLICK.finditer(el['onclick'])]
        return items, any(number > page for number in pages)

    def parse_detail(self, html: str, item: NewsItem) -> Tuple[str, str]:
        soup = BeautifulSoup(html, 'lxml')
        info = soup.select_one('div.single-news-container-info')
        content = soup.select_one('div.single-news-container-info--content')
        if info is None or content is None:
            raise ScraperError('на странице новости нет блока текста (single-news-container-info--content)')
        image = self.first_image(info.select_one('.news-images'), content)
        return make_summary(content.get_text(' '), item.title), self.absolute(image) if image else ''


# реестр: код биржи -> парсер новостей
NEWS_SCRAPERS = {
    'ets': EtsNewsScraper,
    'caspy': CaspyNewsScraper,
}


# --- сохранение ----------------------------------------------------------

def collect_news(exchange_codes: Iterable[str], since: date, trigger: str = 'manual') -> Dict[str, dict]:
    """Собрать новости бирж с даты since и сохранить (upsert по ссылке).

    Возвращает {код биржи: {'exchange', 'found', 'added', 'updated', 'unchanged', 'errors', 'status'}}.
    Сбой одной биржи не мешает остальным. Неизвестный код — ValueError до начала сбора.
    """
    codes = list(exchange_codes)
    unknown = [code for code in codes if code not in NEWS_SCRAPERS]
    if unknown:
        raise ValueError(f'Неизвестные биржи: {", ".join(unknown)}. Доступны: {", ".join(NEWS_SCRAPERS)}')

    results = {}
    for code in codes:
        logger.info('[%s] сбор новостей с %s (%s)', code, since, trigger)
        try:
            result = _collect_exchange(NEWS_SCRAPERS[code](), since)
        except Exception as exc:  # запасная защита: например, ошибка конфигурации
            logger.exception('[%s] сбор новостей завершился аварийно', code)
            result = _empty_result(code)
            result['errors'].append(f'{type(exc).__name__}: {exc}')
        result['status'] = _status(result)
        logger.info('[%s] новости: найдено %d, добавлено %d, обновлено %d, без изменений %d, ошибок %d',
                    code, result['found'], result['added'], result['updated'], result['unchanged'],
                    len(result['errors']))
        results[code] = result
    return results


def _empty_result(code: str) -> dict:
    return {'exchange': code, 'found': 0, 'added': 0, 'updated': 0, 'unchanged': 0, 'errors': []}


def _status(result: dict) -> str:
    if not result['errors']:
        return 'success'
    return 'partial' if result['found'] else 'failed'


def _collect_exchange(scraper: BaseNewsScraper, since: date) -> dict:
    exchange, _ = Exchange.objects.get_or_create(
        code=scraper.exchange_code,
        defaults={'name': scraper.exchange_name or scraper.exchange_code, 'website': scraper.website},
    )
    result = _empty_result(exchange.code)
    result['exchange'] = exchange.name
    # у уже сохранённых новостей с анонсом страницу новости повторно не открываем
    known = News.objects.filter(exchange=exchange, published_at__gte=since).exclude(summary='')
    skip_details = set(known.values_list('url', flat=True))

    try:
        for item in scraper.fetch(since, skip_details=skip_details):
            result['found'] += 1
            try:
                result[_save_item(exchange, item)] += 1
            except Exception as exc:
                logger.exception('[%s] не удалось сохранить новость %s', exchange.code, item.url)
                result['errors'].append(f'{item.url}: {type(exc).__name__}: {exc}')
    except Exception as exc:
        logger.exception('[%s] источник новостей упал', exchange.code)
        result['errors'].append(f'{type(exc).__name__}: {exc}')
    result['errors'] = list(getattr(scraper, 'errors', None) or []) + result['errors']
    return result


def _save_item(exchange: Exchange, item: NewsItem) -> str:
    """Добавить или обновить новость; возвращает 'added' | 'updated' | 'unchanged'."""
    news = News.objects.filter(url=item.url).first()
    if news is None:
        News.objects.create(exchange=exchange, title=item.title, published_at=item.published_at,
                            url=item.url, summary=item.summary, image_url=item.image_url)
        return 'added'

    changes = {}
    if news.title != item.title:
        changes['title'] = item.title
    if news.published_at != item.published_at:
        changes['published_at'] = item.published_at
    # пустой анонс/картинка (страница новости не открывалась) не затирают сохранённые
    if item.summary and news.summary != item.summary:
        changes['summary'] = item.summary
    if item.image_url and news.image_url != item.image_url:
        changes['image_url'] = item.image_url
    if not changes:
        return 'unchanged'
    News.objects.filter(pk=news.pk).update(**changes)
    logger.info('[%s] обновлена новость %s: %s', exchange.code, item.url, ', '.join(changes))
    return 'updated'
