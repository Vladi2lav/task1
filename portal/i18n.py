"""Язык интерфейса: поддерживаемые языки, подписи по кодам и словарь переводов для app.js.

Языки — ru (по умолчанию), kk, en (settings.LANGUAGES). Язык выбирается переключателем
в шапке (стандартный set_language, cookie django_language), при первом визите — по
Accept-Language (LocaleMiddleware).

Данные из источников (названия товаров, компаний, новости) не переводятся. Переводятся
только подписи, которые портал выводит сам: названия бирж по коду, статусы запусков
парсеров (модели не меняются — подписи choices берутся отсюда), способ запуска.
"""
from django.conf import settings
from django.utils.translation import get_language, gettext, gettext_lazy as _

from .js_messages import JS_MESSAGES

DEFAULT_LANGUAGE = 'ru'

# Локали для Intl в браузере (числа и даты). kk-KZ поддерживают Chrome, Edge, Firefox;
# если браузер её не знает, app.js берёт запасную ru-RU.
INTL_LOCALES = {'ru': 'ru-RU', 'kk': 'kk-KZ', 'en': 'en-GB'}

# Короткие подписи в переключателе языка (язык казахский — kk, подпись — KZ)
LANGUAGE_SHORT = {'ru': 'RU', 'kk': 'KZ', 'en': 'EN'}

EXCHANGE_LABELS = {'ets': 'ETS', 'caspy': _('Каспий')}

# Подписи статусов ParseRun.Status на языке интерфейса (в модели — русские)
RUN_STATUS_LABELS = {
    'running': _('Выполняется'),
    'success': _('Успешно'),
    'partial': _('С ошибками'),
    'failed': _('Ошибка'),
}

TRIGGER_LABELS = {'manual': _('Вручную'), 'schedule': _('По расписанию')}


def language_codes() -> list:
    return [code for code, _name in settings.LANGUAGES]


def current_language() -> str:
    """Код активного языка из settings.LANGUAGES (ru, kk, en)."""
    code = (get_language() or settings.LANGUAGE_CODE or DEFAULT_LANGUAGE).lower()
    if code in language_codes():
        return code
    code = code.split('-')[0]
    return code if code in language_codes() else DEFAULT_LANGUAGE


def languages() -> list:
    """[(код, короткая подпись, название на самом языке)] для переключателя."""
    return [(code, LANGUAGE_SHORT.get(code, code.upper()), name) for code, name in settings.LANGUAGES]


def exchange_label(code: str, default: str = '') -> str:
    """Название биржи на языке интерфейса по коду; неизвестный код — название из БД."""
    label = EXCHANGE_LABELS.get(code)
    return str(label) if label is not None else (default or code)


def run_status_label(status: str, default: str = '') -> str:
    label = RUN_STATUS_LABELS.get(status)
    return str(label) if label is not None else (default or status)


def trigger_label(trigger: str) -> str:
    label = TRIGGER_LABELS.get(trigger)
    return str(label) if label is not None else trigger


def js_catalog() -> dict:
    """Словарь для app.js: язык, локаль Intl и переводы всех строк скрипта.

    Ключи одинаковы для всех языков (русский текст строки); для русского значения
    совпадают с ключами.
    """
    lang = current_language()
    return {
        'lang': lang,
        'locale': INTL_LOCALES.get(lang, INTL_LOCALES[DEFAULT_LANGUAGE]),
        'messages': {message: gettext(message) for message in JS_MESSAGES},
    }
