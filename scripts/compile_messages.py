"""Компиляция переводов .po -> .mo без GNU gettext (замена ``manage.py compilemessages``).

    python scripts/compile_messages.py           # проверить и скомпилировать все locale/*/LC_MESSAGES/*.po
    python scripts/compile_messages.py --check   # только проверить (код выхода 1 при ошибках)

Проверки: нет пустых и неточных (fuzzy) переводов; подстановки совпадают с исходной
строкой (%(name)s и {name}); HTML-теги в переводе те же, что в исходной строке.
Готовые .mo хранятся в репозитории, поэтому запуску (start.bat, Docker, Vercel) gettext не нужен.
Нужен пакет polib (requirements-dev.txt).
"""
import argparse
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOCALE_DIR = BASE_DIR / 'locale'

try:
    import polib
except ImportError:  # pragma: no cover
    sys.exit('Нужен пакет polib: pip install -r requirements-dev.txt')

PERCENT = re.compile(r'%\((\w+)\)[sdif]|%[sdif]')
BRACES = re.compile(r'\{(\w+)\}')
TAG = re.compile(r'</?([a-zA-Z][a-zA-Z0-9]*)')


def _signature(pattern, text):
    return sorted(pattern.findall(text))


def check_entry(entry) -> list:
    """Ошибки одной строки перевода."""
    problems = []
    if entry.obsolete:
        return problems
    if 'fuzzy' in entry.flags:
        problems.append('неточный перевод (fuzzy)')
    if not entry.msgstr.strip():
        problems.append('нет перевода')
        return problems
    for name, pattern in (('подстановки %(...)s', PERCENT), ('подстановки {...}', BRACES), ('HTML-теги', TAG)):
        if _signature(pattern, entry.msgid) != _signature(pattern, entry.msgstr):
            problems.append(f'{name} не совпадают с исходной строкой')
    return problems


def check_po(path: Path) -> list:
    errors = []
    po = polib.pofile(str(path))
    for entry in po:
        for problem in check_entry(entry):
            errors.append(f'{path.relative_to(BASE_DIR)}: {problem}: {entry.msgid[:70]!r}')
    return errors


def po_files() -> list:
    return sorted(LOCALE_DIR.glob('*/LC_MESSAGES/*.po'))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='только проверить, без компиляции')
    args = parser.parse_args(argv)
    files = po_files()
    if not files:
        print('Нет файлов locale/*/LC_MESSAGES/*.po')
        return 1
    errors = [error for path in files for error in check_po(path)]
    for error in errors:
        print(error)
    if errors:
        print(f'Ошибок: {len(errors)} — .mo не обновлены')
        return 1
    if args.check:
        print(f'Проверено файлов: {len(files)}, ошибок нет')
        return 0
    for path in files:
        po = polib.pofile(str(path))
        mo_path = path.with_suffix('.mo')
        po.save_as_mofile(str(mo_path))
        print(f'{mo_path.relative_to(BASE_DIR)}: {len(po.translated_entries())} строк')
    return 0


if __name__ == '__main__':
    sys.exit(main())
