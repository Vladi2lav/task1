"""Разбор настроек окружения: .env и DATABASE_URL (config/env.py)."""
import os
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.env import env_bool, env_list, load_dotenv, parse_database_url, parse_dotenv

BASE = Path('C:/project') if os.name == 'nt' else Path('/project')


def test_parse_dotenv_formats():
    values = parse_dotenv(
        '# комментарий\n'
        'A=1\n'
        'export B = two words  # пояснение\n'
        'C="quoted # not comment"\n'
        "D='single'\n"
        'EMPTY=\n'
        'broken line\n'
        'URL=postgres://u:p@h:5432/db#x\n'
    )
    assert values == {'A': '1', 'B': 'two words', 'C': 'quoted # not comment', 'D': 'single', 'EMPTY': '',
                      'URL': 'postgres://u:p@h:5432/db#x'}


def test_load_dotenv_does_not_override_environment(tmp_path, monkeypatch):
    env_file = tmp_path / '.env'
    env_file.write_text('X_TEST_ONE=from_file\nX_TEST_TWO=from_file\n', encoding='utf-8')
    monkeypatch.setenv('X_TEST_ONE', 'from_env')
    monkeypatch.delenv('X_TEST_TWO', raising=False)
    load_dotenv(env_file)
    assert os.environ['X_TEST_ONE'] == 'from_env'
    assert os.environ['X_TEST_TWO'] == 'from_file'
    monkeypatch.delenv('X_TEST_TWO')


def test_load_dotenv_missing_file(tmp_path):
    assert load_dotenv(tmp_path / 'nope.env') == {}


def test_env_helpers(monkeypatch):
    monkeypatch.setenv('X_BOOL', 'Yes')
    monkeypatch.setenv('X_LIST', ' a, b ,,c ')
    assert env_bool('X_BOOL') is True
    assert env_bool('X_MISSING_BOOL', True) is True
    assert env_list('X_LIST') == ['a', 'b', 'c']
    assert env_list('X_MISSING_LIST', ['d']) == ['d']
    monkeypatch.setenv('X_BOOL', 'maybe')
    with pytest.raises(ImproperlyConfigured):
        env_bool('X_BOOL')


def test_database_url_postgres():
    config = parse_database_url('postgres://user:p%40ss@db:5433/trades?sslmode=require', BASE)
    assert config == {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': 'trades', 'USER': 'user', 'PASSWORD': 'p@ss',
        'HOST': 'db', 'PORT': '5433', 'OPTIONS': {'sslmode': 'require'},
    }


def test_database_url_sqlite_paths():
    assert parse_database_url('sqlite:///db.sqlite3', BASE)['NAME'] == str(BASE / 'db.sqlite3')
    assert parse_database_url('sqlite:////data/db.sqlite3', BASE)['NAME'] == '/data/db.sqlite3'
    assert parse_database_url('sqlite:///C:/data/db.sqlite3', BASE)['NAME'] == 'C:/data/db.sqlite3'
    assert parse_database_url('sqlite://', BASE)['NAME'] == ':memory:'


def test_database_url_unknown_scheme():
    with pytest.raises(ImproperlyConfigured):
        parse_database_url('mysql://u@h/db', BASE)
