"""Страницы и JSON API портала. Портал читает только БД."""
import json
import logging
import math
from datetime import date, timedelta

from django.conf import settings
from django.db.models import Count, Max, Min, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from exchanges.models import Broker, Exchange, ParseRun, Participant, Product, Trade

from . import collect_runner
from .excel_export import собрать_xlsx
from .queries import (
    КОДЫ_БИРЖ,
    ОшибкаПараметров,
    выборка_итогов,
    разобрать_страницу,
)
from .serializers import запуск_в_словарь, итог_в_словарь

logger = logging.getLogger(__name__)

# Названия бирж по умолчанию (пока в БД нет записей Exchange)
НАЗВАНИЯ_БИРЖ = {'ets': 'ETS', 'caspy': 'Каспий'}

# Глубина ручного запуска, дней
ГЛУБИНА_МИН, ГЛУБИНА_МАКС = 1, 366

ПОЛЯ_ПОДСКАЗОК = ('product', 'party', 'broker', 'delivery')


def _json(данные, status=200):
    """JsonResponse с русским текстом без \\uXXXX."""
    return JsonResponse(данные, status=status, json_dumps_params={'ensure_ascii': False})


def _ошибка(сообщение, status=400):
    return _json({'error': сообщение}, status=status)


# ----------------------------------------------------------------- страницы

@ensure_csrf_cookie
def страница_торгов(request):
    """Главная: таблица итогов торгов (данные подгружает JS через /api/trades/)."""
    return render(request, 'portal/trades.html', {'page': 'trades'})


@ensure_csrf_cookie
def страница_состояния(request):
    """Состояние данных и ручной запуск сбора."""
    конфиг = getattr(settings, 'SCRAPER_CONFIG', {})
    return render(
        request,
        'portal/status.html',
        {
            'page': 'status',
            'глубина_по_умолчанию': конфиг.get('schedule', {}).get('lookback_days', 3),
            'глубина_истории': конфиг.get('history', {}).get('days', 92),
            'глубина_мин': ГЛУБИНА_МИН,
            'глубина_макс': ГЛУБИНА_МАКС,
        },
    )


# ---------------------------------------------------------------------- API

@require_GET
def api_итоги(request):
    """Список итогов торгов: фильтры, сортировка, пагинация."""
    try:
        qs, _, ordering = выборка_итогов(request.GET)
        страница, размер = разобрать_страницу(request.GET)
    except ОшибкаПараметров as exc:
        return _ошибка(str(exc))

    всего = qs.count()
    страниц = max(1, math.ceil(всего / размер))
    страница = min(страница, страниц)
    начало = (страница - 1) * размер
    строки = [итог_в_словарь(t) for t in qs[начало:начало + размер]]
    сумма = qs.aggregate(s=Sum('amount'))['s'] if всего else None
    return _json({
        'count': всего,
        'page': страница,
        'pages': страниц,
        'page_size': размер,
        'ordering': ordering,
        'amount_sum': float(сумма) if сумма is not None else None,
        'results': строки,
    })


@require_GET
def api_экспорт_xlsx(request):
    """Экспорт текущей выборки (те же фильтры и сортировка) в Excel."""
    try:
        qs, фильтры, ordering = выборка_итогов(request.GET)
    except ОшибкаПараметров as exc:
        return _ошибка(str(exc))
    данные, строк, усечено = собрать_xlsx(qs, фильтры, ordering)
    ответ = HttpResponse(
        данные,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    имя = f'itogi_torgov_{timezone.localdate():%Y%m%d}.xlsx'
    ответ['Content-Disposition'] = f'attachment; filename="{имя}"'
    ответ['X-Export-Rows'] = str(строк)
    ответ['X-Export-Truncated'] = '1' if усечено else '0'
    return ответ


def _подсказки(поле: str, текст: str, лимит: int = 10) -> list:
    """Подсказки для текстовых фильтров по введённым буквам (без учёта регистра)."""
    if поле == 'product':
        qs, имя_поля = Product.objects.filter(name__rucontains=текст), 'name'
    elif поле == 'party':
        qs, имя_поля = Participant.objects.filter(name__rucontains=текст), 'name'
    elif поле == 'broker':
        qs, имя_поля = Broker.objects.filter(name__rucontains=текст), 'name'
    else:  # delivery
        qs = Trade.objects.exclude(delivery_place='').filter(delivery_place__rucontains=текст)
        имя_поля = 'delivery_place'
    return list(qs.values_list(имя_поля, flat=True).distinct().order_by(имя_поля)[:лимит])


@require_GET
def api_фильтры(request):
    """Данные для выпадающих списков.

    Без параметров: биржи, топ мест поставки, границы дат.
    ``?field=product|party|broker|delivery&term=...`` — подсказки по введённому тексту.
    """
    поле = request.GET.get('field', '').strip()
    if поле:
        if поле not in ПОЛЯ_ПОДСКАЗОК:
            return _ошибка('Параметр field: допустимы ' + ', '.join(ПОЛЯ_ПОДСКАЗОК))
        текст = request.GET.get('term', '').strip()
        if len(текст) < 2:
            return _json({'field': поле, 'suggestions': []})
        return _json({'field': поле, 'suggestions': _подсказки(поле, текст)})

    биржи = {e.code: e.name for e in Exchange.objects.order_by('id')}
    for код, название in НАЗВАНИЯ_БИРЖ.items():
        биржи.setdefault(код, название)
    места = (
        Trade.objects.exclude(delivery_place='')
        .values('delivery_place')
        .annotate(n=Count('id'))
        .order_by('-n', 'delivery_place')[:30]
    )
    границы = Trade.objects.aggregate(first=Min('trade_date'), last=Max('trade_date'))
    return _json({
        'exchanges': [{'code': код, 'name': название} for код, название in биржи.items()],
        'delivery_places': [{'name': м['delivery_place'], 'count': м['n']} for м in места],
        'date_min': границы['first'].isoformat() if границы['first'] else None,
        'date_max': границы['last'].isoformat() if границы['last'] else None,
    })


@require_GET
def api_состояние(request):
    """Состояние данных: по каждой бирже и журнал запусков."""
    итоги = {
        r['exchange__code']: r
        for r in Trade.objects.values('exchange__code').annotate(n=Count('id'), last=Max('trade_date'))
    }
    биржи = list(Exchange.objects.order_by('id'))
    известные = {e.code for e in биржи}
    элементы = [(e.code, e.name, e) for e in биржи] + [
        (код, название, None) for код, название in НАЗВАНИЯ_БИРЖ.items() if код not in известные
    ]
    данные_бирж = []
    for код, название, биржа in элементы:
        сводка = итоги.get(код, {})
        последний = успешный = None
        if биржа is not None:
            запуски = ParseRun.objects.filter(exchange=биржа).select_related('exchange').order_by('-started_at', '-id')
            последний = запуски.first()
            успешный = запуски.filter(status__in=[ParseRun.Status.SUCCESS, ParseRun.Status.PARTIAL]).first()
        данные_бирж.append({
            'code': код,
            'name': название,
            'trades_count': сводка.get('n', 0),
            'last_trade_date': сводка['last'].isoformat() if сводка.get('last') else None,
            'last_run': запуск_в_словарь(последний) if последний else None,
            'last_success_at': (
                timezone.localtime(успешный.finished_at or успешный.started_at).isoformat() if успешный else None
            ),
        })
    последние = ParseRun.objects.select_related('exchange').order_by('-started_at', '-id')[:10]
    return _json({
        'exchanges': данные_бирж,
        'total_trades': sum(e['trades_count'] for e in данные_бирж),
        'runs': [запуск_в_словарь(r) for r in последние],
        'collect': {
            **collect_runner.получить_состояние(),
            # Идёт запуск из другого процесса (планировщик, командная строка)
            'running_elsewhere': (
                not collect_runner.запущен() and collect_runner.есть_активный_запуск_в_журнале()
            ),
        },
        'server_time': timezone.localtime().isoformat(),
    })


# ----------------------------------------------------------- ручной запуск

def _разобрать_тело(request) -> dict:
    """Параметры запуска из JSON-тела или из формы."""
    if 'json' in (request.content_type or ''):
        try:
            данные = json.loads(request.body or b'{}')
        except ValueError:
            raise ОшибкаПараметров('Тело запроса: некорректный JSON')
        if not isinstance(данные, dict):
            raise ОшибкаПараметров('Тело запроса: ожидается JSON-объект')
        return данные
    данные = {ключ: request.POST.get(ключ) for ключ in request.POST}
    if 'exchanges' in request.POST:
        данные['exchanges'] = request.POST.getlist('exchanges')
    return данные


def _коды_бирж(данные: dict) -> list:
    """Коды бирж для запуска: exchanges (список) или exchange (ets|caspy|all)."""
    сырые = данные.get('exchanges')
    if сырые in (None, '', []):
        сырые = данные.get('exchange')
    if сырые in (None, '', []):
        return list(КОДЫ_БИРЖ)
    if isinstance(сырые, str):
        сырые = сырые.split(',')
    if not isinstance(сырые, list):
        raise ОшибкаПараметров('Биржа: допустимы ets, caspy или all')
    коды = []
    for код in сырые:
        код = str(код).strip().lower()
        if код == 'all':
            return list(КОДЫ_БИРЖ)
        if код not in КОДЫ_БИРЖ:
            raise ОшибкаПараметров('Биржа: допустимы ets, caspy или all')
        if код not in коды:
            коды.append(код)
    return коды


def _период(данные: dict) -> tuple:
    """Период сбора: либо date_from/date_to, либо глубина в днях (days)."""
    try:
        if данные.get('date_from') or данные.get('date_to'):
            конец = date.fromisoformat(str(данные.get('date_to') or timezone.localdate().isoformat()))
            начало = date.fromisoformat(str(данные.get('date_from') or конец.isoformat()))
        else:
            сырые_дни = данные.get('days')
            дни = 3 if сырые_дни in (None, '') else int(сырые_дни)
            if not ГЛУБИНА_МИН <= дни <= ГЛУБИНА_МАКС:
                raise ОшибкаПараметров(f'Глубина: от {ГЛУБИНА_МИН} до {ГЛУБИНА_МАКС} дней')
            конец = timezone.localdate()
            начало = конец - timedelta(days=дни - 1)
    except (ValueError, TypeError):
        raise ОшибкаПараметров('Период: даты в формате ГГГГ-ММ-ДД, глубина — целое число дней')
    if начало > конец:
        raise ОшибкаПараметров('Дата начала позже даты окончания')
    if (конец - начало).days + 1 > ГЛУБИНА_МАКС:
        raise ОшибкаПараметров(f'Период не должен превышать {ГЛУБИНА_МАКС} дней')
    return начало, конец


@require_POST
def api_запуск_сбора(request):
    """Ручной запуск сбора в фоновом потоке (нужен CSRF-токен)."""
    try:
        данные = _разобрать_тело(request)
        коды = _коды_бирж(данные)
        начало, конец = _период(данные)
    except ОшибкаПараметров as exc:
        return _ошибка(str(exc))

    # Ленивый импорт: функцию сбора пишет отдельный модуль, он может быть недоступен
    try:
        from scrapers.service import collect
    except ImportError as exc:
        logger.warning('Сервис сбора недоступен: %s', exc)
        return _ошибка('Сервис сбора данных пока не подключён (scrapers.service.collect)', status=503)

    if collect_runner.запущен() or collect_runner.есть_активный_запуск_в_журнале():
        return _ошибка('Сбор уже выполняется — дождитесь завершения', status=409)
    if not collect_runner.запустить(collect, коды, начало, конец):
        return _ошибка('Сбор уже выполняется — дождитесь завершения', status=409)
    return _json({
        'started': True,
        'exchanges': коды,
        'date_from': начало.isoformat(),
        'date_to': конец.isoformat(),
    }, status=202)
