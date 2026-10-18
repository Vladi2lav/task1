"""Точка входа для Vercel (Python Serverless Function): WSGI-приложение `app`.

Vercel выставляет VERCEL=1 — в этом режиме настройки (config/settings.py) включают демо только для чтения:
DEBUG выключен, PORTAL_READ_ONLY=True, база — копия снимка deploy/vercel_demo.sqlite3 в /tmp.
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from django.core.wsgi import get_wsgi_application  # noqa: E402

app = application = get_wsgi_application()
