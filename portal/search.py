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
ESCAPE_CHAR = chr(92)


def ru_lower(value):
    """Нижний регистр для любых символов Unicode (не-строки остаются как есть)."""
    return value.lower() if isinstance(value, str) else value


def _add_sqlite_function(sender, connection, **kwargs):
    """Регистрирует ``ru_lower`` в каждом новом соединении SQLite."""
    if connection.vendor == 'sqlite':
        connection.connection.create_function('ru_lower', 1, ru_lower, deterministic=True)


def escape_like(text):
    """Экранирует служебные символы шаблона LIKE: обратную косую, % и _."""
    for char in (ESCAPE_CHAR, '%', '_'):
        text = text.replace(char, ESCAPE_CHAR + char)
    return text


class RuContains(Lookup):
    """Подстрока без учёта регистра (в том числе для кириллицы)."""

    lookup_name = 'rucontains'

    def process_rhs(self, compiler, connection):
        # Значение приводим к нижнему регистру и оборачиваем в %...%
        value = ru_lower(str(self.rhs))
        return '%s', ['%' + escape_like(value) + '%']

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        func = 'ru_lower' if connection.vendor == 'sqlite' else 'LOWER'
        return f"{func}({lhs}) LIKE {rhs} ESCAPE '{ESCAPE_CHAR}'", [*lhs_params, *rhs_params]


def register():
    """Идемпотентно подключает функцию SQLite и lookup ``rucontains``."""
    global _registered
    if _registered:
        return
    _registered = True
    connection_created.connect(_add_sqlite_function, dispatch_uid='portal_ru_lower')
    CharField.register_lookup(RuContains)
    TextField.register_lookup(RuContains)
    # Соединения, открытые до ready() (например, в тестах), дополняем вручную
    for alias in connections:
        conn = connections[alias]
        if conn.vendor == 'sqlite' and conn.connection is not None:
            _add_sqlite_function(None, conn)
