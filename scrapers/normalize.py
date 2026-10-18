"""Приведение значений из источников к единому виду: числа, даты, текст, БИН, единицы."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# --- числа ---------------------------------------------------------------

_SPACES = re.compile(r'[\s   ]+')


def parse_decimal(value: Any) -> Optional[Decimal]:
    """Число из ячейки/строки: '1 234,56', '88000', 88000.0 -> Decimal.

    Пустое значение, формула ('=SUM(...)') и любой нечисловой текст дают None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if value == value else None  # NaN -> None

    text = _SPACES.sub('', str(value))
    if not text or text.startswith('='):
        return None
    text = text.replace(',', '.')
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


# --- даты ----------------------------------------------------------------

_DATE_DMY = re.compile(r'^(\d{1,2})[./](\d{1,2})[./](\d{4})$')
_DATE_ISO = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')


def parse_date(value: Any) -> Optional[date]:
    """Дата из 'дд.мм.гггг', ISO 'гггг-мм-дд' или datetime/date. Непонятное -> None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None

    text = str(value).strip()
    for pattern, order in ((_DATE_DMY, 'dmy'), (_DATE_ISO, 'ymd')):
        match = pattern.match(text)
        if not match:
            continue
        a, b, c = (int(g) for g in match.groups())
        day, month, year = (a, b, c) if order == 'dmy' else (c, b, a)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


# родительный падеж («14 октября») и именительный («октябрь 2026»), а также сокращения «окт.»
_RU_MONTHS = {
    'январ': 1, 'янв': 1, 'феврал': 2, 'фев': 2, 'март': 3, 'мар': 3, 'апрел': 4, 'апр': 4,
    'мая': 5, 'май': 5, 'июн': 6, 'июл': 7, 'август': 8, 'авг': 8, 'сентябр': 9, 'сен': 9, 'сент': 9,
    'октябр': 10, 'окт': 10, 'ноябр': 11, 'ноя': 11, 'декабр': 12, 'дек': 12,
}
_RU_DATE = re.compile(r'(\d{1,2})\s+([а-яё]+)\.?\s+(\d{4})', re.IGNORECASE)


def _ru_month(word: str) -> Optional[int]:
    word = word.lower()
    for stem in sorted(_RU_MONTHS, key=len, reverse=True):  # длинные основы раньше коротких
        if word.startswith(stem):
            return _RU_MONTHS[stem]
    return None


def parse_ru_date(value: Any) -> Optional[date]:
    """Дата с месяцем словом («14 октября 2026», «3 окт. 2026 г.») или в цифрах («14.10.2026»).

    Сначала пробуется parse_date (цифровые форматы), затем поиск «число месяц год» в тексте.
    """
    parsed = parse_date(value)
    if parsed or value is None or isinstance(value, date):
        return parsed
    text = clean_text(value)
    match = _RU_DATE.search(text)
    if not match:
        found = re.search(r'\b(\d{1,2}[./]\d{1,2}[./]\d{4})\b', text)  # дата внутри строки
        return parse_date(found.group(1)) if found else None
    month = _ru_month(match.group(2))
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


# --- текст ---------------------------------------------------------------

def clean_text(value: Any, max_length: Optional[int] = None) -> str:
    """Строка без лишних пробелов и переносов; None -> ''. При max_length обрезается."""
    if value is None:
        return ''
    text = _SPACES.sub(' ', str(value)).strip()
    if max_length and len(text) > max_length:
        text = text[:max_length].rstrip()
    return text


def normalize_bin(value: Any) -> str:
    """БИН/ИИН: только цифры, ровно 12 знаков, иначе пустая строка.

    Числа из Excel теряют ведущие нули (050140001234 -> 50140001234),
    поэтому для числовых значений недостающие нули возвращаются.
    """
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    digits = re.sub(r'\D', '', str(value))
    if isinstance(value, int) and 0 < len(digits) < 12:
        digits = digits.zfill(12)
    return digits if len(digits) == 12 else ''


_QUOTES = '«»“”„‟‹›"'
_QUOTE_RE = re.compile(f'[{_QUOTES}]')


def normalize_company_name(value: Any) -> str:
    """Название организации: пробелы схлопнуты, кавычки одного вида ("..."), без пробелов внутри кавычек.

    Смысл и регистр букв не меняются: ТОО  «Астык  Трейд» -> ТОО "Астык Трейд".
    """
    text = clean_text(value)
    if not text:
        return ''
    text = _QUOTE_RE.sub('"', text)
    parts = text.split('"')
    if len(parts) % 2 == 0:  # непарные кавычки — оставляем как есть
        return text
    result = parts[0]
    for number, part in enumerate(parts[1:], start=1):
        if number % 2:  # открывающая кавычка: пробел перед ней, без пробела после
            if result and result[-1] not in ' ([':
                result += ' '
            result += '"' + part.lstrip()
        else:  # закрывающая: без пробела перед ней
            result = result.rstrip() + '"' + part
    return clean_text(result)


# --- единицы измерения ---------------------------------------------------

_UNITS = {
    'т': 'т', 'тн': 'т', 'тонна': 'т', 'тонны': 'т', 'тонн': 'т', 'tn': 'т', 'ton': 'т', 'tons': 'т',
    'кг': 'кг', 'килограмм': 'кг', 'килограмма': 'кг', 'kg': 'кг',
    'л': 'л', 'литр': 'л', 'литра': 'л', 'литров': 'л', 'l': 'л',
    'шт': 'шт', 'штука': 'шт', 'штуки': 'шт', 'штук': 'шт', 'pcs': 'шт',
    'м3': 'м3', 'м³': 'м3', 'куб.м': 'м3', 'кубометр': 'м3', 'кубометров': 'м3', 'm3': 'м3',
    'м2': 'м2', 'м²': 'м2', 'кв.м': 'м2',
    'м': 'м', 'метр': 'м', 'метров': 'м',
    'тыс.м3': 'тыс. м3', 'тыс.м³': 'тыс. м3',
}


def normalize_unit(value: Any) -> str:
    """Единица измерения в короткий единый вид: тонна/тн/т -> 'т'. Неизвестная возвращается как есть."""
    text = clean_text(value)
    if not text:
        return ''
    key = text.lower().replace(' ', '').rstrip('.')
    return _UNITS.get(key, _UNITS.get(key.replace('.', ''), text))


# --- двуязычные названия -------------------------------------------------

_SLASH_BETWEEN_DIGITS = re.compile(r'\d/\d')
_CYRILLIC = re.compile('[а-яёА-ЯЁ]')
_KAZAKH_LETTERS = set('әғқңөұүһіӘҒҚҢӨҰҮҺІ')


def split_bilingual_product(name: Any) -> str:
    """Название вида «казахское/русское» -> русская часть. Без разделителя возвращается как есть.

    Слэши внутри частей ('ж/д', 'т/j', '5/10') разделителем не считаются. Разделитель — первый слэш,
    за которым начинается кириллица, а перед ним её нет (казахское название у ETS написано латиницей).
    Если казахская часть тоже кириллицей, ориентиром служат казахские буквы (ә, ғ, қ, ң, ө, ұ, ү, һ, і).
    """
    text = clean_text(name)
    slashes = [
        i for i, ch in enumerate(text)
        if ch == '/' and text[:i].strip() and text[i + 1:].strip()
        and not _SLASH_BETWEEN_DIGITS.fullmatch(text[i - 1:i + 2])
    ]
    for i in slashes:
        left, right = text[:i].rstrip(), text[i + 1:].lstrip()
        if not _CYRILLIC.match(left[-1]) and _CYRILLIC.match(right[0]):
            return right
    for i in slashes:
        if _KAZAKH_LETTERS.intersection(text[:i]):
            return text[i + 1:].strip()
    return text
