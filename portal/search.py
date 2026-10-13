"""Регистронезависимый поиск подстроки, корректно работающий с кириллицей.

Проблема: встроенный ``icontains`` на SQLite превращается в ``LIKE``, а SQLite
считает нечувствительными к регистру только ASCII-символы, поэтому
«пшеница» не найдёт «Пшеница». Функция ``LOWER`` в SQLite тоже работает только
с ASCII.

Решение без изменения моделей: в соединении SQLite регистрируется Python-функция
``ru_lower`` (``str.lower`` понимает Unicode), а для полей ``CharField``/``TextField``
добавляется lookup ``rucontains``:

* SQLite:     ``ru_lower(поле) LIKE '%значение%' ESCAPE '\\'``
* PostgreSQL: ``LOWER(поле) LIKE '%значение%' ESCAPE '\\'`` (LOWER там уже знает Unicode)

Использование: ``Trade.objects.filter(product__name__rucontains='пшеница')``.
"""
from django.db import connections
from django.db.backends.signals import connection_created
from django.db.models import CharField, Lookup, TextField

_registered = False

# Символ экранирования в шаблоне LIKE — одна обратная косая черта
ЭКРАН = chr(92)


def ru_lower(value):
    """Нижний регистр для любых символов Unicode (не-строки остаются как есть)."""
    return value.lower() if isinstance(value, str) else value


def _добавить_функцию_sqlite(sender, connection, **kwargs):
    """Регистрирует ``ru_lower`` в каждом новом соединении SQLite."""
    if connection.vendor == 'sqlite':
        connection.connection.create_function('ru_lower', 1, ru_lower, deterministic=True)


def экранировать_like(text):
    """Экранирует служебные символы шаблона LIKE: обратную косую, % и _."""
    for символ in (ЭКРАН, '%', '_'):
        text = text.replace(символ, ЭКРАН + символ)
    return text


class RuContains(Lookup):
    """Подстрока без учёта регистра (в том числе для кириллицы)."""

    lookup_name = 'rucontains'

    def process_rhs(self, compiler, connection):
        # Значение приводим к нижнему регистру и оборачиваем в %...%
        значение = ru_lower(str(self.rhs))
        return '%s', ['%' + экранировать_like(значение) + '%']

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        функция = 'ru_lower' if connection.vendor == 'sqlite' else 'LOWER'
        return f"{функция}({lhs}) LIKE {rhs} ESCAPE '{ЭКРАН}'", [*lhs_params, *rhs_params]


def register():
    """Идемпотентно подключает функцию SQLite и lookup ``rucontains``."""
    global _registered
    if _registered:
        return
    _registered = True
    connection_created.connect(_добавить_функцию_sqlite, dispatch_uid='portal_ru_lower')
    CharField.register_lookup(RuContains)
    TextField.register_lookup(RuContains)
    # Соединения, открытые до ready() (например, в тестах), дополняем вручную
    for alias in connections:
        соединение = connections[alias]
        if соединение.vendor == 'sqlite' and соединение.connection is not None:
            _добавить_функцию_sqlite(None, соединение)
