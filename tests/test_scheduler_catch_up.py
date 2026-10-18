"""Досбор после пропущенного планового запуска (планировщик не работал в момент расписания)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from exchanges.models import Exchange, ParseRun
from scrapers import scheduler

TZ = ZoneInfo('Asia/Almaty')


def _run(code, *, trigger='schedule', status=ParseRun.Status.SUCCESS, started=None, date_to=None):
    exchange, _ = Exchange.objects.get_or_create(code=code, defaults={'name': code})
    run = ParseRun.objects.create(exchange=exchange, trigger=trigger, status=status, date_to=date_to)
    if started:
        ParseRun.objects.filter(pk=run.pk).update(started_at=started)
    return run


def test_previous_run_time_today_or_yesterday():
    evening = datetime(2026, 10, 18, 20, 0, tzinfo=TZ)
    morning = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    assert scheduler.previous_run_time(evening) == datetime(2026, 10, 18, 18, 30, tzinfo=TZ)
    assert scheduler.previous_run_time(morning) == datetime(2026, 10, 17, 18, 30, tzinfo=TZ)


@pytest.mark.django_db
def test_missed_when_no_scheduled_run_after_due_time():
    now = datetime(2026, 10, 18, 20, 0, tzinfo=TZ)
    _run('ets', started=datetime(2026, 10, 17, 18, 30, tzinfo=TZ))  # вчерашний — не считается
    _run('ets', trigger='manual', started=datetime(2026, 10, 18, 19, 0, tzinfo=TZ))  # ручной — тоже
    assert scheduler.missed_run(now) is True
    _run('ets', started=datetime(2026, 10, 18, 18, 31, tzinfo=TZ))
    assert scheduler.missed_run(now) is False


@pytest.mark.django_db
def test_build_scheduler_adds_immediate_catch_up_job(monkeypatch):
    monkeypatch.setattr(scheduler, 'missed_run', lambda now=None: True)
    jobs = {job.id for job in scheduler.build_scheduler().get_jobs()}
    assert jobs == {'collect_trades', 'catch_up'}
    monkeypatch.setattr(scheduler, 'missed_run', lambda now=None: False)
    assert {job.id for job in scheduler.build_scheduler().get_jobs()} == {'collect_trades'}


@pytest.mark.django_db
def test_period_starts_from_last_successful_run_after_downtime(settings):
    today = date(2026, 10, 18)
    # без истории — обычные lookback_days
    assert scheduler.collect_period_start('ets', today) == today - timedelta(days=3)
    # компьютер был выключен неделю: собираем с даты последнего успешного сбора минус lookback_days
    # (биржа могла выложить файлы за последние дни уже после того сбора)
    _run('ets', date_to=date(2026, 10, 10))
    _run('ets', status=ParseRun.Status.FAILED, date_to=date(2026, 10, 16))  # неудачный не считается
    assert scheduler.collect_period_start('ets', today) == date(2026, 10, 7)
    # недавний сбор: всё равно перепроверяем lookback_days до него
    _run('ets', date_to=date(2026, 10, 18))
    assert scheduler.collect_period_start('ets', today) == date(2026, 10, 15)
    # но не глубже [history].days
    ParseRun.objects.all().delete()
    _run('ets', date_to=date(2025, 1, 1))
    assert scheduler.collect_period_start('ets', today) == today - timedelta(days=settings.SCRAPER_CONFIG['history']['days'])


@pytest.mark.django_db
def test_collect_scheduled_uses_period_per_exchange(monkeypatch, settings):
    calls = []
    monkeypatch.setattr(scheduler, 'collect', lambda codes, df, dt, trigger: calls.append((codes[0], df, dt, trigger)))
    monkeypatch.setattr(scheduler, 'collect_news_scheduled', lambda today: None)
    settings.SCRAPER_CONFIG = {**settings.SCRAPER_CONFIG,
                               'schedule': {**settings.SCRAPER_CONFIG['schedule'], 'exchanges': ['ets', 'caspy']}}
    today = timezone.now().astimezone(TZ).date()
    _run('caspy', date_to=today - timedelta(days=10))
    scheduler.collect_scheduled()
    assert calls[0] == ('ets', today - timedelta(days=3), today, 'schedule')
    assert calls[1] == ('caspy', today - timedelta(days=13), today, 'schedule')
