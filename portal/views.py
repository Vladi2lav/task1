"""Страницы и JSON API портала. Портал читает только БД."""
import json
import logging
import math
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.management import get_commands
from django.core.paginator import Paginator
from django.db.models import Count, Max, Min, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from exchanges.models import Broker, Exchange, News, ParseRun, Participant, Product, Trade

from . import analytics, collect_runner, i18n, legal
from .excel_export import build_xlsx
from .queries import (
    EXCHANGE_CODES,
    NEWS_PAGE_SIZE_DEFAULT,
    NEWS_PAGE_SIZE_MAX,
    NEWS_PAGE_SIZE_MIN,
    ParamError,
    parse_page,
    select_news,
    select_trades,
)
from .serializers import news_to_dict, run_to_dict, trade_to_dict

logger = logging.getLogger(__name__)

# Биржи по умолчанию (пока в БД нет записей Exchange); названия выводятся на языке интерфейса
DEFAULT_EXCHANGE_NAMES = {'ets': 'ETS', 'caspy': 'Каспий'}

# Глубина ручного запуска, дней
COLLECT_DAYS_MIN, COLLECT_DAYS_MAX = 1, 366

SUGGEST_FIELDS = ('product', 'party', 'broker', 'delivery')

# Сколько новостей показывать в блоке «Последние новости»
LATEST_NEWS_COUNT = 5

READ_ONLY_MESSAGE = gettext_lazy(
    'Демо-версия: сбор данных отключён. Запустите проект локально, '
    'чтобы собирать данные с сайтов бирж.'
)


def is_read_only() -> bool:
    """Режим «только чтение» (демо-развёртывание): ручной сбор недоступен."""
    return bool(getattr(settings, 'PORTAL_READ_ONLY', False))


# Значок сайта (тот же, что inline в base.html) — для браузеров, запрашивающих /favicon.ico
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="8" fill="#1d5fd0"/>'
    '<path d="M9 8h14M9 12.5h14M16 12.5V25" stroke="white" stroke-width="3" '
    'stroke-linecap="round" fill="none"/></svg>'
)


@require_GET
def favicon(request):
    response = HttpResponse(FAVICON_SVG, content_type='image/svg+xml')
    response['Cache-Control'] = 'public, max-age=604800'
    return response


def _json(data, status=200):
    """JsonResponse с текстом на любом языке без \\uXXXX."""
    return JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})


def _error(message, status=400):
    """Ответ с ошибкой; текст — на языке интерфейса (cookie django_language / Accept-Language)."""
    return _json({'error': str(message)}, status=status)


def _exchange_names() -> dict:
    """Код биржи -> название на языке интерфейса: биржи из БД, недостающие — по умолчанию."""
    names = {e.code: i18n.exchange_label(e.code, e.name) for e in Exchange.objects.order_by('id')}
    for code, name in DEFAULT_EXCHANGE_NAMES.items():
        names.setdefault(code, i18n.exchange_label(code, name))
    return names


def _latest_news(limit=LATEST_NEWS_COUNT) -> list:
    return list(News.objects.select_related('exchange').order_by('-published_at', '-id')[:limit])


def _news_commands() -> list:
    """Management-команды сбора новостей, если они есть в проекте."""
    return sorted(name for name in get_commands() if 'news' in name)


# ----------------------------------------------------------------- страницы

@ensure_csrf_cookie
def trades_page(request):
    """Главная: таблица итогов торгов (данные подгружает JS через /api/trades/)."""
    return render(request, 'portal/trades.html', {
        'page': 'trades',
        'latest_news': _latest_news(),
    })


@ensure_csrf_cookie
def analytics_page(request):
    """Аналитика: фильтры, карточки сводки и графики (данные подгружает JS через /api/analytics/)."""
    return render(request, 'portal/analytics.html', {'page': 'analytics'})


@ensure_csrf_cookie
def status_page(request):
    """Состояние данных и ручной запуск сбора."""
    config = getattr(settings, 'SCRAPER_CONFIG', {})
    return render(
        request,
        'portal/status.html',
        {
            'page': 'status',
            'read_only': is_read_only(),
            'default_days': config.get('schedule', {}).get('lookback_days', 3),
            'history_days': config.get('history', {}).get('days', 92),
            'days_min': COLLECT_DAYS_MIN,
            'days_max': COLLECT_DAYS_MAX,
            'news_commands': _news_commands(),
        },
    )


def _page_numbers(current: int, total: int) -> list:
    """Номера страниц для пагинации: первая, последняя и ±2 от текущей; None — разрыв «…»."""
    visible = {1, total} | {p for p in range(current - 2, current + 3) if 1 <= p <= total}
    numbers, previous = [], 0
    for number in sorted(visible):
        if number - previous > 1:
            numbers.append(None)
        numbers.append(number)
        previous = number
    return numbers


def news_page(request):
    """Лента новостей бирж: фильтр по бирже, поиск по заголовку, пагинация (без JS)."""
    try:
        qs, exchange, q = select_news(request.GET)
    except ParamError:
        # неизвестная биржа в адресе — показываем все новости
        qs, exchange, q = select_news({'q': request.GET.get('q', '')})
    paginator = Paginator(qs, NEWS_PAGE_SIZE_DEFAULT)
    page_obj = paginator.get_page(request.GET.get('page'))
    filters_query = urlencode([(key, value) for key, value in (('exchange', exchange), ('q', q)) if value])
    return render(request, 'portal/news.html', {
        'page': 'news',
        'news_list': page_obj.object_list,
        'page_obj': page_obj,
        'page_numbers': _page_numbers(page_obj.number, paginator.num_pages) if paginator.num_pages > 1 else [],
        'filters_query': filters_query,
        'exchange': exchange,
        'q': q,
        'exchanges': list(_exchange_names().items()),
        'has_any_news': News.objects.exists(),
        'news_commands': _news_commands(),
    })


def _legal_templates(name: str) -> list:
    """Шаблоны юридической страницы: перевод на язык интерфейса, затем русская редакция.

    Русская редакция (portal/legal/<name>.html) — юридически значимая; переводы лежат
    в portal/legal/<язык>/<name>.html. Нет перевода — показывается русская редакция.
    """
    lang = i18n.current_language()
    candidates = [f'portal/legal/{name}.html']
    if lang != i18n.DEFAULT_LANGUAGE:
        candidates.insert(0, f'portal/legal/{lang}/{name}.html')
    return candidates


def _legal_page(name, page):
    def view(request):
        return render(request, _legal_templates(name), {'page': page, **legal.legal_context()})
    view.__name__ = page.replace('-', '_') + '_page'
    view.__doc__ = f'Юридическая страница (portal/legal/{name}.html и переводы).'
    return view


legal_terms_page = _legal_page('terms', 'legal-terms')
legal_privacy_page = _legal_page('privacy', 'legal-privacy')


def legal_cookies_page(request):
    """Политика использования cookie: сроки берутся из фактических настроек Django."""
    language_age = getattr(settings, 'LANGUAGE_COOKIE_AGE', None)
    return render(request, _legal_templates('cookies'), {
        'page': 'legal-cookies',
        'csrf_cookie_name': settings.CSRF_COOKIE_NAME,
        'csrf_cookie_days': round(settings.CSRF_COOKIE_AGE / 86400),
        'session_cookie_name': settings.SESSION_COOKIE_NAME,
        'session_cookie_days': round(settings.SESSION_COOKIE_AGE / 86400),
        'language_cookie_name': settings.LANGUAGE_COOKIE_NAME,
        # None — cookie живёт до закрытия браузера
        'language_cookie_days': round(language_age / 86400) if language_age else None,
        **legal.legal_context(),
    })


def legal_sources_page(request):
    """Источники данных и лицензии используемых библиотек."""
    config = getattr(settings, 'SCRAPER_CONFIG', {})
    return render(request, _legal_templates('sources'), {
        'page': 'legal-sources',
        'libraries': legal.used_libraries(),
        'http_config': config.get('http', {}),
        'schedule_config': config.get('schedule', {}),
        **legal.legal_context(),
    })


# ---------------------------------------------------------------------- API

@require_GET
def api_trades(request):
    """Список итогов торгов: фильтры, сортировка, пагинация."""
    try:
        qs, _, ordering = select_trades(request.GET)
        page, size = parse_page(request.GET)
    except ParamError as exc:
        return _error(str(exc))

    total = qs.count()
    pages = max(1, math.ceil(total / size))
    page = min(page, pages)
    start = (page - 1) * size
    rows = [trade_to_dict(t) for t in qs[start:start + size]]
    amount_sum = qs.aggregate(s=Sum('amount'))['s'] if total else None
    return _json({
        'count': total,
        'page': page,
        'pages': pages,
        'page_size': size,
        'ordering': ordering,
        'amount_sum': float(amount_sum) if amount_sum is not None else None,
        'results': rows,
    })


@require_GET
def api_analytics(request):
    """Сводная аналитика: динамика цены, топы, оборот по дням и по месяцам, сводка.

    Параметры: exchange, date_from, date_to, q (как в /api/trades/);
    product=ID (повторяемый) и/или products=1,2,3 — выбранные товары (до 10):
    все секции считаются только по ним, график и сравнение — по каждому отдельно;
    product_id или product_code — старый способ выбрать товар для price_dynamics.
    """
    try:
        return _json(analytics.build_analytics(request.GET))
    except ParamError as exc:
        return _error(str(exc))


@require_GET
def api_analytics_products(request):
    """Товары для выбора на странице аналитики: все, у которых есть сделки в выборке.

    Параметры: exchange, date_from, date_to; q — поиск по названию, коду и ТН ВЭД.
    """
    try:
        results = analytics.products_for_picker(request.GET)
    except ParamError as exc:
        return _error(str(exc))
    return _json({'count': len(results), 'results': results})


@require_GET
def api_trades_export(request):
    """Экспорт текущей выборки (те же фильтры и сортировка) в Excel."""
    try:
        qs, filters, ordering = select_trades(request.GET)
    except ParamError as exc:
        return _error(str(exc))
    data, rows, truncated = build_xlsx(qs, filters, ordering)
    response = HttpResponse(
        data,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    filename = f'itogi_torgov_{timezone.localdate():%Y%m%d}.xlsx'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['X-Export-Rows'] = str(rows)
    response['X-Export-Truncated'] = '1' if truncated else '0'
    return response


def _sort_key(text: str):
    return text.lower(), text


def _suggestions(field: str, text: str, limit: int = 10) -> list:
    """Подсказки для текстовых фильтров по введённым буквам (без учёта регистра)."""
    if field == 'delivery':
        # Место поставки и базис поставки — одним списком
        values = set()
        for column in ('delivery_place', 'delivery_basis'):
            qs = Trade.objects.exclude(**{column: ''}).filter(**{f'{column}__rucontains': text})
            values.update(qs.values_list(column, flat=True).distinct().order_by(column)[:limit])
        return sorted(values, key=_sort_key)[:limit]
    if field == 'product':
        qs = Product.objects.filter(name__rucontains=text)
    elif field == 'party':
        qs = Participant.objects.filter(name__rucontains=text)
    else:  # broker
        qs = Broker.objects.filter(name__rucontains=text)
    return list(qs.values_list('name', flat=True).distinct().order_by('name')[:limit])


def _top_delivery_places(limit: int = 30) -> list:
    """Самые частые места и базисы поставки: [(название, число записей)]."""
    counts = Counter()
    for column in ('delivery_place', 'delivery_basis'):
        rows = Trade.objects.exclude(**{column: ''}).values(column).annotate(n=Count('id'))
        for row in rows:
            counts[row[column]] += row['n']
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]


@require_GET
def api_filters(request):
    """Данные для выпадающих списков.

    Без параметров: биржи, топ мест (и базисов) поставки, границы дат.
    ``?field=product|party|broker|delivery&term=...`` — подсказки по введённому тексту.
    """
    field = request.GET.get('field', '').strip()
    if field:
        if field not in SUGGEST_FIELDS:
            return _error(_('Параметр field: допустимы %(values)s') % {'values': ', '.join(SUGGEST_FIELDS)})
        text = request.GET.get('term', '').strip()
        if len(text) < 2:
            return _json({'field': field, 'suggestions': []})
        return _json({'field': field, 'suggestions': _suggestions(field, text)})

    bounds = Trade.objects.aggregate(first=Min('trade_date'), last=Max('trade_date'))
    return _json({
        'exchanges': [{'code': code, 'name': name} for code, name in _exchange_names().items()],
        'delivery_places': [{'name': name, 'count': n} for name, n in _top_delivery_places()],
        'date_min': bounds['first'].isoformat() if bounds['first'] else None,
        'date_max': bounds['last'].isoformat() if bounds['last'] else None,
    })


@require_GET
def api_status(request):
    """Состояние данных: по каждой бирже и журнал запусков."""
    totals = {
        r['exchange__code']: r
        for r in Trade.objects.values('exchange__code').annotate(n=Count('id'), last=Max('trade_date'))
    }
    exchanges = list(Exchange.objects.order_by('id'))
    known = {e.code for e in exchanges}
    items = [(e.code, i18n.exchange_label(e.code, e.name), e) for e in exchanges] + [
        (code, i18n.exchange_label(code, name), None)
        for code, name in DEFAULT_EXCHANGE_NAMES.items() if code not in known
    ]
    exchanges_data = []
    for code, name, exchange in items:
        stats = totals.get(code, {})
        last_run = last_success = None
        if exchange is not None:
            runs = ParseRun.objects.filter(exchange=exchange).select_related('exchange').order_by('-started_at', '-id')
            last_run = runs.first()
            last_success = runs.filter(status__in=[ParseRun.Status.SUCCESS, ParseRun.Status.PARTIAL]).first()
        exchanges_data.append({
            'code': code,
            'name': name,
            'trades_count': stats.get('n', 0),
            'last_trade_date': stats['last'].isoformat() if stats.get('last') else None,
            'last_run': run_to_dict(last_run) if last_run else None,
            'last_success_at': (
                timezone.localtime(last_success.finished_at or last_success.started_at).isoformat()
                if last_success else None
            ),
        })
    recent_runs = ParseRun.objects.select_related('exchange').order_by('-started_at', '-id')[:10]
    return _json({
        'exchanges': exchanges_data,
        'total_trades': sum(e['trades_count'] for e in exchanges_data),
        'runs': [run_to_dict(r) for r in recent_runs],
        'collect': {
            **collect_runner.get_state(),
            # Идёт запуск из другого процесса (планировщик, командная строка)
            'running_elsewhere': (
                not collect_runner.is_running() and collect_runner.has_active_run_in_log()
            ),
        },
        'read_only': is_read_only(),
        'server_time': timezone.localtime().isoformat(),
    })


@require_GET
def api_news(request):
    """Лента новостей: exchange, q (поиск по заголовку), page, page_size (1–100, по умолчанию 20)."""
    try:
        qs, _, _ = select_news(request.GET)
        page, size = parse_page(request.GET, NEWS_PAGE_SIZE_DEFAULT, NEWS_PAGE_SIZE_MIN, NEWS_PAGE_SIZE_MAX)
    except ParamError as exc:
        return _error(str(exc))
    total = qs.count()
    pages = max(1, math.ceil(total / size))
    page = min(page, pages)
    start = (page - 1) * size
    return _json({
        'count': total,
        'page': page,
        'pages': pages,
        'page_size': size,
        'results': [news_to_dict(item) for item in qs[start:start + size]],
    })


# ----------------------------------------------------------- ручной запуск

def _parse_body(request) -> dict:
    """Параметры запуска из JSON-тела или из формы."""
    if 'json' in (request.content_type or ''):
        try:
            data = json.loads(request.body or b'{}')
        except ValueError:
            raise ParamError(_('Тело запроса: некорректный JSON'))
        if not isinstance(data, dict):
            raise ParamError(_('Тело запроса: ожидается JSON-объект'))
        return data
    data = {key: request.POST.get(key) for key in request.POST}
    if 'exchanges' in request.POST:
        data['exchanges'] = request.POST.getlist('exchanges')
    return data


def _exchange_codes(data: dict) -> list:
    """Коды бирж для запуска: exchanges (список) или exchange (ets|caspy|all)."""
    raw = data.get('exchanges')
    if raw in (None, '', []):
        raw = data.get('exchange')
    if raw in (None, '', []):
        return list(EXCHANGE_CODES)
    if isinstance(raw, str):
        raw = raw.split(',')
    if not isinstance(raw, list):
        raise ParamError(_('Биржа: допустимы ets, caspy или all'))
    codes = []
    for code in raw:
        code = str(code).strip().lower()
        if code == 'all':
            return list(EXCHANGE_CODES)
        if code not in EXCHANGE_CODES:
            raise ParamError(_('Биржа: допустимы ets, caspy или all'))
        if code not in codes:
            codes.append(code)
    return codes


def _collect_period(data: dict) -> tuple:
    """Период сбора: либо date_from/date_to, либо глубина в днях (days)."""
    try:
        if data.get('date_from') or data.get('date_to'):
            end = date.fromisoformat(str(data.get('date_to') or timezone.localdate().isoformat()))
            begin = date.fromisoformat(str(data.get('date_from') or end.isoformat()))
        else:
            raw_days = data.get('days')
            days = 3 if raw_days in (None, '') else int(raw_days)
            if not COLLECT_DAYS_MIN <= days <= COLLECT_DAYS_MAX:
                raise ParamError(_('Глубина: от %(min)s до %(max)s дней') % {
                    'min': COLLECT_DAYS_MIN, 'max': COLLECT_DAYS_MAX})
            end = timezone.localdate()
            begin = end - timedelta(days=days - 1)
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ParamError):
            raise
        raise ParamError(_('Период: даты в формате ГГГГ-ММ-ДД, глубина — целое число дней'))
    if begin > end:
        raise ParamError(_('Дата начала позже даты окончания'))
    if (end - begin).days + 1 > COLLECT_DAYS_MAX:
        raise ParamError(_('Период не должен превышать %(max)s дней') % {'max': COLLECT_DAYS_MAX})
    return begin, end


@require_POST
def api_collect(request):
    """Ручной запуск сбора в фоновом потоке (нужен CSRF-токен)."""
    if is_read_only():
        return _error(READ_ONLY_MESSAGE, status=403)
    try:
        data = _parse_body(request)
        codes = _exchange_codes(data)
        begin, end = _collect_period(data)
    except ParamError as exc:
        return _error(str(exc))

    # Ленивый импорт: функцию сбора пишет отдельный модуль, он может быть недоступен
    try:
        from scrapers.service import collect
    except ImportError as exc:
        logger.warning('Сервис сбора недоступен: %s', exc)
        return _error(_('Сервис сбора данных пока не подключён (scrapers.service.collect)'), status=503)

    if collect_runner.is_running() or collect_runner.has_active_run_in_log():
        return _error(_('Сбор уже выполняется — дождитесь завершения'), status=409)
    if not collect_runner.start(collect, codes, begin, end):
        return _error(_('Сбор уже выполняется — дождитесь завершения'), status=409)
    return _json({
        'started': True,
        'exchanges': codes,
        'date_from': begin.isoformat(),
        'date_to': end.isoformat(),
    }, status=202)
