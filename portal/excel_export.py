"""Выгрузка текущей выборки итогов торгов в Excel (xlsx)."""
from copy import copy
from io import BytesIO

from django.conf import settings
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Максимум строк в файле по умолчанию (защита от выгрузки всей базы).
# Можно переопределить в settings.py: PORTAL_EXPORT_MAX_ROWS = 20000.
# Ориентир по времени: openpyxl записывает около 1000 строк в секунду.
МАКС_СТРОК_ЭКСПОРТА = 50_000


def макс_строк_экспорта() -> int:
    return int(getattr(settings, 'PORTAL_EXPORT_MAX_ROWS', МАКС_СТРОК_ЭКСПОРТА))

ФОРМАТ_ДАТЫ = 'DD.MM.YYYY'
ФОРМАТ_ВРЕМЕНИ = 'HH:MM:SS'
ФОРМАТ_ЦЕНЫ = '#,##0.00'
ФОРМАТ_ОБЪЁМА = '#,##0.000'
ФОРМАТ_ЦЕЛОГО = '#,##0'

# (заголовок, ширина, функция получения значения из записи Trade, формат ячейки)
КОЛОНКИ = [
    ('Биржа', 10, lambda t: t.exchange.name, None),
    ('Дата торгов', 13, lambda t: t.trade_date, ФОРМАТ_ДАТЫ),
    ('Время', 10, lambda t: t.trade_time, ФОРМАТ_ВРЕМЕНИ),
    ('Товар', 50, lambda t: t.product.name, None),
    ('Код товара', 14, lambda t: t.product.code, None),
    ('Код ТН ВЭД', 14, lambda t: t.product.tnved, None),
    ('Продавец', 40, lambda t: t.seller.name if t.seller else '', None),
    ('БИН продавца', 15, lambda t: t.seller.bin if t.seller else '', None),
    ('Покупатель', 40, lambda t: t.buyer.name if t.buyer else '', None),
    ('БИН покупателя', 15, lambda t: t.buyer.bin if t.buyer else '', None),
    ('Брокер продавца', 32, lambda t: t.seller_broker.name if t.seller_broker else '', None),
    ('Брокер покупателя', 32, lambda t: t.buyer_broker.name if t.buyer_broker else '', None),
    ('Число сделок', 10, lambda t: t.deals_count, ФОРМАТ_ЦЕЛОГО),
    ('Объём', 14, lambda t: t.quantity, ФОРМАТ_ОБЪЁМА),
    ('Ед. изм.', 10, lambda t: t.unit, None),
    ('Цена открытия, тг', 16, lambda t: t.price_open, ФОРМАТ_ЦЕНЫ),
    ('Цена закрытия, тг', 16, lambda t: t.price_close, ФОРМАТ_ЦЕНЫ),
    ('Мин. цена, тг', 16, lambda t: t.price_min, ФОРМАТ_ЦЕНЫ),
    ('Макс. цена, тг', 16, lambda t: t.price_max, ФОРМАТ_ЦЕНЫ),
    ('Средневзв. цена, тг', 18, lambda t: t.price_avg, ФОРМАТ_ЦЕНЫ),
    ('Цена с НДС, тг', 16, lambda t: t.price_with_vat, ФОРМАТ_ЦЕНЫ),
    ('Цена без НДС, тг', 16, lambda t: t.price_without_vat, ФОРМАТ_ЦЕНЫ),
    ('Сумма сделки, тг', 20, lambda t: t.amount, ФОРМАТ_ЦЕНЫ),
    ('Базис поставки', 22, lambda t: t.delivery_basis, None),
    ('Место поставки', 30, lambda t: t.delivery_place, None),
    ('Срок поставки', 22, lambda t: t.delivery_term, None),
    ('Источник', 40, lambda t: t.source_url, None),
]


_СТИЛИ_ФОРМАТОВ = {}


def _стиль_формата(лист, формат):
    """Готовый «стиль ячейки» для числового формата (создаётся один раз на формат).

    Присвоение готового стиля намного быстрее, чем выставление number_format
    в каждой из миллиона ячеек.
    """
    ключ = (id(лист.parent), формат)
    if ключ not in _СТИЛИ_ФОРМАТОВ:
        образец = WriteOnlyCell(лист)
        образец.number_format = формат
        _СТИЛИ_ФОРМАТОВ[ключ] = образец._style
    return _СТИЛИ_ФОРМАТОВ[ключ]


def _ячейка(лист, значение, формат=None):
    """Значение для записи в строку: простые значения — как есть, с форматом — ячейка со стилем.

    Decimal переводится в float, пустое — в None. Строки, начинающиеся с «=»,
    явно помечаются как текст, чтобы Excel не воспринял данные биржи как формулу.
    """
    if значение == '' or значение is None:
        return None
    if hasattr(значение, 'as_tuple'):  # Decimal
        значение = float(значение)
    if isinstance(значение, str):
        if значение[:1] == '=':
            ячейка = WriteOnlyCell(лист, value=значение)
            ячейка.data_type = 's'
            return ячейка
        return значение
    if формат:
        ячейка = WriteOnlyCell(лист, value=значение)
        ячейка._style = copy(_стиль_формата(лист, формат))
        return ячейка
    return значение


def описание_фильтров(фильтры, ordering, выгружено, усечено, предел):
    """Строки для листа «Параметры»: какие фильтры были применены."""
    названия_бирж = {'ets': 'ETS', 'caspy': 'Каспий', '': 'Обе биржи'}
    строки = [
        ('Дата выгрузки', timezone.localtime().strftime('%d.%m.%Y %H:%M')),
        ('Биржа', названия_бирж.get(фильтры.exchange, фильтры.exchange)),
        ('Период с', фильтры.date_from.strftime('%d.%m.%Y') if фильтры.date_from else ''),
        ('Период по', фильтры.date_to.strftime('%d.%m.%Y') if фильтры.date_to else ''),
        ('Товар (ключевое слово)', фильтры.q),
        ('Продавец / покупатель', фильтры.party),
        ('Брокер', фильтры.broker),
        ('Сумма от, тг', float(фильтры.amount_min) if фильтры.amount_min is not None else ''),
        ('Сумма до, тг', float(фильтры.amount_max) if фильтры.amount_max is not None else ''),
        ('Место поставки', фильтры.delivery),
        ('Сортировка', ordering),
        ('Строк в файле', выгружено),
    ]
    if усечено:
        строки.append(
            ('Внимание', f'Выгрузка ограничена {предел:,} строками — уточните фильтры'.replace(',', ' '))
        )
    return строки


def собрать_xlsx(qs, фильтры, ordering) -> tuple:
    """Строит xlsx по queryset (с select_related). Возвращает (байты, число_строк, усечено)."""
    книга = Workbook(write_only=True)
    лист = книга.create_sheet('Итоги торгов')
    for номер, (_, ширина, _, _) in enumerate(КОЛОНКИ, start=1):
        лист.column_dimensions[get_column_letter(номер)].width = ширина
    лист.freeze_panes = 'A2'

    шрифт = Font(bold=True, color='FFFFFF')
    заливка = PatternFill('solid', fgColor='1F4E79')
    выравнивание = Alignment(horizontal='center', vertical='center', wrap_text=True)
    заголовок = []
    for название, *_ in КОЛОНКИ:
        ячейка = WriteOnlyCell(лист, value=название)
        ячейка.font = шрифт
        ячейка.fill = заливка
        ячейка.alignment = выравнивание
        заголовок.append(ячейка)
    лист.append(заголовок)

    выгружено = 0
    усечено = False
    # Берём на одну строку больше лимита, чтобы понять, что выборка обрезана
    предел = макс_строк_экспорта()
    for t in qs[: предел + 1].iterator(chunk_size=2000):
        if выгружено >= предел:
            усечено = True
            break
        лист.append([_ячейка(лист, получить(t), формат) for _, _, получить, формат in КОЛОНКИ])
        выгружено += 1
    лист.auto_filter.ref = f'A1:{get_column_letter(len(КОЛОНКИ))}{max(выгружено, 1) + 1}'

    параметры = книга.create_sheet('Параметры')
    параметры.column_dimensions['A'].width = 28
    параметры.column_dimensions['B'].width = 60
    for строка in описание_фильтров(фильтры, ordering, выгружено, усечено, предел):
        параметры.append([_ячейка(параметры, значение) for значение in строка])

    буфер = BytesIO()
    книга.save(буфер)
    return буфер.getvalue(), выгружено, усечено
