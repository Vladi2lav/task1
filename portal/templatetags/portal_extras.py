"""Фильтры шаблонов портала."""
from django import template

register = template.Library()


@register.filter
def http_url(value):
    """Ссылка только со схемой http/https, иначе пустая строка (защита от javascript: и т.п.)."""
    text = str(value or '').strip()
    return text if text.lower().startswith(('http://', 'https://')) else ''
