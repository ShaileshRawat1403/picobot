"""Regression coverage for DAX run poll backoff.

`_poll_run` used to compute `poll_interval` from `POLL_INTERVALS` /
`_get_poll_interval` and then never use it: every active run was polled on
every 5-second loop tick regardless of age, so the "poll less often as a run
gets older" table had no effect. `_due_for_poll` is what makes it effective.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from picobot.bus.dax_service import ActiveRun, DaxPollingService


def test_due_for_poll_true_when_never_polled():
    run = ActiveRun(run_id="r1", chat_id="c1")
    assert DaxPollingService._due_for_poll(run, poll_interval=5, now=datetime.now()) is True


def test_due_for_poll_false_before_interval_elapses():
    now = datetime.now()
    run = ActiveRun(run_id="r1", chat_id="c1", last_polled_at=now - timedelta(seconds=2))
    assert DaxPollingService._due_for_poll(run, poll_interval=5, now=now) is False


def test_due_for_poll_true_once_interval_elapses():
    now = datetime.now()
    run = ActiveRun(run_id="r1", chat_id="c1", last_polled_at=now - timedelta(seconds=5))
    assert DaxPollingService._due_for_poll(run, poll_interval=5, now=now) is True


def test_due_for_poll_backs_off_for_older_runs():
    # An hour-old run should be on the 60s bucket per POLL_INTERVALS, so
    # 30 seconds since the last poll is not enough to be due again.
    from picobot.bus.dax_service import _get_poll_interval

    now = datetime.now()
    interval = _get_poll_interval(3600)
    assert interval == 60
    run = ActiveRun(run_id="r1", chat_id="c1", last_polled_at=now - timedelta(seconds=30))
    assert DaxPollingService._due_for_poll(run, poll_interval=interval, now=now) is False
