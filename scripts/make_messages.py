"""Обновление файлов переводов без GNU gettext (замена ``manage.py makemessages`` для Windows).

Собирает строки для перевода так же, как makemessages:
  * шаблоны (*.html) — через Django ``templatize`` ({% translate %}, {% blocktranslate %});
  * Python — вызовы ``_()``, ``gettext``, ``gettext_lazy``, ``gettext_noop``, ``pgettext*``
    с литералом-строкой (в том числе список строк app.js в portal/js_messages.py);
и объединяет их с locale/<язык>/LC_MESSAGES/django.po: готовые переводы сохраняются,
новые строки добавляются с пустым переводом, исчезнувшие помечаются устаревшими (#~).

Запуск из корня проекта:
    python scripts/make_messages.py            # kk и en
    python scripts/make_messages.py -l kk      # один язык
Затем — перевести новые строки в .po и скомпилировать: python scripts/compile_messages.py

Если GNU gettext установлен (Linux, Docker), можно пользоваться и стандартной командой:
    python manage.py makemessages -l kk -l en --ignore=tests --ignore=staticfiles --ignore=.venv
Нужен пакет polib (requirements-dev.txt).
"""
import argparse
import ast
import io
import os
import sys
import tokenize
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

try:
    import polib
except ImportError:  # pragma: no cover
    sys.exit('Нужен пакет polib: pip install -r requirements-dev.txt')

# Откуда собираются строки (относительно корня проекта)
SOURCE_DIRS = ['config', 'portal', 'setup_tools']
SKIP_PARTS = {'__pycache__', 'migrations', 'static', 'staticfiles'}

# Функции, чей первый аргумент — строка для перевода (pgettext*: контекст, строка)
SIMPLE_FUNCS = {'_', 'gettext', 'gettext_lazy', 'gettext_noop', 'ugettext', 'ugettext_lazy'}
CONTEXT_FUNCS = {'pgettext', 'pgettext_lazy'}

LANGUAGE_META = {
    'kk': {'Language': 'kk', 'Plural-Forms': 'nplurals=2; plural=(n!=1);'},
    'en': {'Language': 'en', 'Plural-Forms': 'nplurals=2; plural=(n != 1);'},
}


class Catalog:
    """Строки в порядке первого появления: (контекст, строка) -> [места]."""

    def __init__(self):
        self.items = {}

    def add(self, msgid, path, line, context=None):
        if not msgid:
            return
        key = (context, msgid)
        self.items.setdefault(key, [])
        place = (path, str(line))
        if place not in self.items[key]:
            self.items[key].append(place)


def _func_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ''


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def extract_python(path: Path, rel: str, catalog: Catalog):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in sorted(ast.walk(tree), key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0))):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = _func_name(node.func)
        if name in SIMPLE_FUNCS:
            text = _const(node.args[0])
            if text is not None:
                catalog.add(text, rel, node.lineno)
        elif name in CONTEXT_FUNCS and len(node.args) >= 2:
            context, text = _const(node.args[0]), _const(node.args[1])
            if context is not None and text is not None:
                catalog.add(text, rel, node.lineno, context)


def extract_template(path: Path, rel: str, catalog: Catalog):
    from django.utils.translation.template import templatize

    code = templatize(path.read_text(encoding='utf-8'), origin=str(path))
    tokens = [t for t in tokenize.generate_tokens(io.StringIO(code).readline)
              if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT)]
    for i, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string not in ('gettext', 'pgettext', '_'):
            continue
        if i + 2 >= len(tokens) or tokens[i + 1].string != '(' or tokens[i + 2].type != tokenize.STRING:
            continue
        first = ast.literal_eval(tokens[i + 2].string)
        if token.string == 'pgettext':
            if i + 4 < len(tokens) and tokens[i + 4].type == tokenize.STRING:
                catalog.add(ast.literal_eval(tokens[i + 4].string), rel, token.start[0], first)
        else:
            catalog.add(first, rel, token.start[0])


def collect() -> Catalog:
    import django

    django.setup()
    catalog = Catalog()
    for folder in SOURCE_DIRS:
        root = BASE_DIR / folder
        for path in sorted(root.rglob('*')):
            if not path.is_file() or SKIP_PARTS & set(path.relative_to(BASE_DIR).parts):
                continue
            rel = path.relative_to(BASE_DIR).as_posix()
            if path.suffix == '.py':
                extract_python(path, rel, catalog)
            elif path.suffix in ('.html', '.txt'):
                extract_template(path, rel, catalog)
    return catalog


def _flags(msgid: str) -> list:
    flags = []
    if '%(' in msgid or '%s' in msgid:
        flags.append('python-format')
    elif '{' in msgid and '}' in msgid:
        flags.append('python-brace-format')
    return flags


def update_po(lang: str, catalog: Catalog) -> tuple:
    po_path = BASE_DIR / 'locale' / lang / 'LC_MESSAGES' / 'django.po'
    po_path.parent.mkdir(parents=True, exist_ok=True)
    old = polib.pofile(str(po_path), wrapwidth=0) if po_path.exists() else None
    existing = {}
    if old is not None:
        for entry in old:
            existing[(entry.msgctxt, entry.msgid)] = entry
        for entry in old.obsolete_entries():
            existing.setdefault((entry.msgctxt, entry.msgid), entry)

    po = polib.POFile(wrapwidth=0)
    po.header = 'Переводы портала «Итоги торгов бирж ETS и Каспий».\nОбновляется: python scripts/make_messages.py'
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M+0000')
    po.metadata = {
        'Project-Id-Version': 'portal',
        'Report-Msgid-Bugs-To': '',
        'POT-Creation-Date': now,
        'PO-Revision-Date': (old.metadata.get('PO-Revision-Date') if old is not None else now) or now,
        'Last-Translator': '',
        'Language-Team': '',
        'MIME-Version': '1.0',
        'Content-Type': 'text/plain; charset=UTF-8',
        'Content-Transfer-Encoding': '8bit',
        **LANGUAGE_META.get(lang, {'Language': lang}),
    }
    added = 0
    for (context, msgid), places in catalog.items.items():
        before = existing.pop((context, msgid), None)
        entry = polib.POEntry(
            msgid=msgid,
            msgctxt=context,
            msgstr=before.msgstr if before is not None else '',
            occurrences=places,
            flags=[f for f in (before.flags if before is not None else []) if f == 'fuzzy'] + _flags(msgid),
            tcomment=before.tcomment if before is not None else '',
        )
        if before is None:
            added += 1
        po.append(entry)
    obsolete = 0
    for entry in existing.values():
        if entry.msgstr:
            entry.obsolete = True
            entry.occurrences = []
            po.append(entry)
            obsolete += 1
    po.save(str(po_path))
    return po_path, len(catalog.items), added, obsolete


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-l', '--locale', action='append', help='язык (kk, en); по умолчанию все, кроме ru')
    args = parser.parse_args(argv)
    catalog = collect()
    languages = args.locale or ['kk', 'en']
    for lang in languages:
        path, total, added, obsolete = update_po(lang, catalog)
        print(f'{path.relative_to(BASE_DIR)}: строк {total}, новых {added}, устаревших {obsolete}')


if __name__ == '__main__':
    main()
