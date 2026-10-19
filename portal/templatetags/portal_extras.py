"""Фильтры и теги шаблонов портала."""
from django import template
from django.utils.html import json_script

from .. import i18n

register = template.Library()


@register.filter
def http_url(value):
    """Ссылка только со схемой http/https, иначе пустая строка (защита от javascript: и т.п.)."""
    text = str(value or '').strip()
    return text if text.lower().startswith(('http://', 'https://')) else ''


@register.filter
def exchange_label(exchange):
    """Название биржи на языке интерфейса: {{ item.exchange|exchange_label }} (объект Exchange или код)."""
    if exchange is None:
        return ''
    code = getattr(exchange, 'code', exchange)
    return i18n.exchange_label(str(code), getattr(exchange, 'name', ''))


@register.simple_tag
def portal_i18n():
    """Словарь переводов для app.js: <script id="portal-i18n" type="application/json">.

    Работает и без request (страница 500): язык берётся из активного перевода.
    """
    return json_script(i18n.js_catalog(), 'portal-i18n')


@register.simple_tag
def portal_languages():
    """[(код, подпись, название)] для переключателя языка."""
    return i18n.languages()
