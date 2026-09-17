"""PR registration must keep its data and scheduled refresh observable."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
import sys

import pytest


def test_pr_refresh_and_data_checks_are_registered():
    from app.core.self_check import CHECKS, EXPECTED_JOBS
    from app.core.data_freshness import SOURCES

    assert EXPECTED_JOBS['pr_issues_sync_daily'][0] <= 30
    assert {'pr_vectors_present', 'pr_sheet_freshness', 'pr_unknown_team',
            'pr_month_unresolved'} <= {check.id for check in CHECKS}
    assert any('PR' in source.keys for source in SOURCES)


@pytest.fixture
def pr_status(monkeypatch):
    from app.core import pr_issues

    state = {'count': 12, 'synced_at': datetime.now(timezone.utc).isoformat(),
             'unknown_teams': [], 'month_unknown': 2, 'month_inferred': 3,
             'baseline_month_unknown': 2, 'baseline_month_inferred': 3}
    monkeypatch.setattr(pr_issues, 'status', lambda: dict(state))
    return state


def test_zero_rows_fail_even_with_a_recent_success_stamp(pr_status, monkeypatch):
    from app.core import data_freshness as fresh

    pr_status['count'] = 0
    monkeypatch.setattr(fresh, '_drive_modified', lambda _: datetime.now())
    result = fresh._probe_pr_issues()
    assert not result.ok
    assert '0' in result.detail


def test_old_pr_copy_is_current_if_original_has_not_changed(pr_status, monkeypatch):
    from app.core import data_freshness as fresh

    old = datetime.now() - timedelta(days=10)
    pr_status['synced_at'] = old.isoformat()
    monkeypatch.setattr(fresh, '_drive_modified', lambda _: old - timedelta(hours=1))
    assert fresh._probe_pr_issues().ok


@pytest.mark.parametrize('age,expected', [(1, True), (40, False)])
def test_missing_drive_metadata_falls_back_to_age(pr_status, monkeypatch, age, expected):
    from app.core import data_freshness as fresh

    pr_status['synced_at'] = (datetime.now(timezone.utc) - timedelta(hours=age)).isoformat()
    monkeypatch.setattr(fresh, '_drive_modified', lambda _: None)
    result = fresh._probe_pr_issues()
    assert result.ok is expected
    assert '나이로만' in result.detail


@pytest.fixture
def cloud_models(monkeypatch):
    """The external SDK boundary is exercised against the real SDK in release verification."""
    module = ModuleType('qdrant_client.models')
    for name in ('FieldCondition', 'Filter', 'MatchValue'):
        setattr(module, name, SimpleNamespace)
    monkeypatch.setitem(sys.modules, 'qdrant_client.models', module)


def test_pr_cloud_missing_is_detected_even_when_local_manifest_exists(pr_status, monkeypatch, cloud_models):
    from app.agents import qdrant_agent
    from app.core import self_check

    calls = []

    def count(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(count=0)

    monkeypatch.setattr(qdrant_agent, '_get_client', lambda: SimpleNamespace(count=count))
    result = self_check._check_pr_vectors_present()
    assert not result.ok
    assert calls[0]['exact'] is True
    conditions = calls[0]['count_filter'].must
    assert any(c.key == 'team' and c.match.value == 'PR' for c in conditions)


def test_matching_pr_cloud_count_passes(pr_status, monkeypatch, cloud_models):
    from app.agents import qdrant_agent
    from app.core import self_check

    monkeypatch.setattr(qdrant_agent, '_get_client', lambda: SimpleNamespace(
        count=lambda **kw: SimpleNamespace(count=12)))
    assert self_check._check_pr_vectors_present().ok


def test_pending_recovery_does_not_look_healthy(pr_status, monkeypatch, cloud_models):
    from app.core import data_freshness, self_check

    pr_status['pending_recovery'] = True
    monkeypatch.setattr(data_freshness, '_drive_modified', lambda _: datetime.now())
    assert not data_freshness._probe_pr_issues().ok
    assert not self_check._check_pr_vectors_present().ok


def test_new_unresolved_months_are_flagged_but_existing_baseline_is_not(pr_status):
    from app.core import self_check

    assert self_check._check_pr_month_unresolved().ok
    pr_status['month_unknown'] += 1
    assert not self_check._check_pr_month_unresolved().ok


def test_unknown_author_teams_are_reported_without_relabeling(pr_status):
    from app.core import self_check

    assert self_check._check_pr_unknown_team().ok
    pr_status['unknown_teams'] = ['BOP']
    result = self_check._check_pr_unknown_team()
    assert not result.ok and 'BOP' in result.detail


@pytest.mark.parametrize('stats,success', [
    ({'rows': 12, 'written': 12, 'empty': False}, True),
    ({'rows': 0, 'written': 0, 'empty': True}, False),
    ({'rows': 12, 'written': 12, 'empty': False, 'cleanup_refused': 40}, False),
])
def test_pr_job_records_empty_or_refused_refresh_as_failure(monkeypatch, stats, success):
    from app import main
    from app.core import pr_issues, self_check

    events = []

    @contextmanager
    def tracking(job_id):
        events.append(job_id)
        try:
            yield SimpleNamespace(set_note=lambda note: events.append(note))
        except Exception:
            events.append('failed')
            raise
        else:
            events.append('succeeded')

    monkeypatch.setattr(pr_issues, 'sync_pr_issues', lambda: dict(stats))
    monkeypatch.setattr(self_check, 'track_job', tracking)
    asyncio.run(main._pr_issues_sync_job())
    assert events[0] == 'pr_issues_sync_daily'
    assert events[-1] == ('succeeded' if success else 'failed')
