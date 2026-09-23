"""매출 이월 확인 — 두 스냅샷의 거래처별 대조 (2026-09-23).

판정은 순수 함수라 BigQuery 없이 검사한다. 조회는 `runner` 로 갈아 끼운다.
사고 모양을 그대로 재현한다: 실측(8/31↔9/3) 에서 **건수는 같은데 금액만 −4% 로 일제히**
움직인 재환산과, **건수가 줄고 다음 달이 늘어난** 진짜 이월이 한 표에 섞여 있었다.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core import sales_carryover as sc

SNAPS = ["20260831", "20260901", "20260903", "20260912", "20260930"]


def _raw(company, ym, s_a, s_b, n_a, n_b):
    return {"company": company, "ym": ym, "s_a": s_a, "s_b": s_b, "n_a": n_a, "n_b": n_b}


# ── 입력 검증 ─────────────────────────────────────────────────────────────

def test_validate_orders_dates_and_defaults_month_to_first_date():
    a, b, m = sc.validate("20260930", "20260912", None, SNAPS)
    assert (a, b, m) == ("20260912", "20260930", "2026-09")


@pytest.mark.parametrize("a, b, msg", [
    ("2026091", "20260930", "여덟 자리"),
    ("20260931", "20260930", "달력에 없는"),
    ("20260913", "20260930", "백업 테이블이 없습니다"),   # 실제로 빠진 날
    ("20260912", "20260912", "같은 날짜"),
])
def test_validate_rejects_bad_dates_with_a_reason(a, b, msg):
    with pytest.raises(sc.CarryoverError) as e:
        sc.validate(a, b, None, SNAPS)
    assert msg in str(e.value)


def test_validate_rejects_bad_month():
    with pytest.raises(sc.CarryoverError):
        sc.validate("20260912", "20260930", "2026-13", SNAPS)


def test_sql_only_uses_validated_snapshot_names_and_fixed_filter():
    sql = sc.build_sql("20260912", "20260930", "2026-09")
    assert "`skin1004-319714.Sales_Integration.SALES_ALL_Backup_20260912`" in sql
    assert "`skin1004-319714.Sales_Integration.SALES_ALL_Backup_20260930`" in sql
    assert "Brand IN ('SK', 'CBT')" in sql and "Sales_Type = 'B2B'" in sql
    assert "SUM(Sales1_R)" in sql and "Company_Name" in sql
    # 대상월 + 다음 달까지 읽는다 (이월 신호). 다다음달은 읽지 않는다
    assert "DATETIME '2026-09-01'" in sql and "DATETIME '2026-11-01'" in sql
    assert "FULL OUTER JOIN" in sql


def test_month_bounds_roll_over_the_year():
    assert sc._month_bounds("2026-12") == ("2026-12-01", "2027-01-01", "2027-02-01")
    assert sc._month_bounds("2026-11") == ("2026-11-01", "2026-12-01", "2027-01-01")


# ── 판정 ──────────────────────────────────────────────────────────────────

def test_assemble_classifies_each_company_and_orders_missing_first():
    raw = [
        _raw("Gone Co", "2026-08", 1000, None, 3, None),
        _raw("Target", "2026-08", 250.0, 46.0, 29, 20),
        _raw("Target", "2026-09", 207.0, 390.0, 10, 18),
        _raw("Same Co", "2026-08", 500, 500, 5, 5),
        _raw("Up Co", "2026-08", 100, 150, 2, 3),
        _raw("New Co", "2026-08", None, 80, None, 1),
        _raw("Only Next", "2026-09", 10, 10, 1, 1),   # 대상월에 없으면 표에 안 오른다
    ]
    cmp = sc.assemble("20260831", "20260903", "2026-08", raw)
    by = {r.company: r for r in cmp.rows}
    assert [r.company for r in cmp.rows] == ["Gone Co", "Target", "Same Co", "Up Co", "New Co"]
    assert by["Gone Co"].status == "사라짐" and by["Gone Co"].diff == -1000
    assert by["Target"].status == "감소" and by["Target"].next_diff == 183.0
    assert by["Same Co"].status == "동일"
    assert by["Up Co"].status == "증가" and by["Up Co"].pct == 50.0
    assert by["New Co"].status == "신규" and by["New Co"].pct is None
    d = cmp.as_dict()
    assert d["summary"] == {"companies": 5, "total_a": 1850, "total_b": 776, "diff": -1074,
                            "gone": 1, "decreased": 1, "increased": 1, "new": 1, "truncated": 0}
    assert d["next_month"] == "2026-09"
    assert d["filter"] == {"brand": ["SK", "CBT"], "sales_type": "B2B"}


def test_uniform_shift_with_same_row_counts_is_called_out_before_the_table():
    # 실측 재현: 건수는 그대로인데 금액만 −4% — 환율 재환산이지 이월이 아니다
    raw = [_raw(f"C{i}", "2026-08", 1000.0, 960.0, 10, 10) for i in range(6)]
    raw.append(_raw("Target", "2026-08", 250.0, 46.0, 29, 20))
    cmp = sc.assemble("20260831", "20260903", "2026-08", raw)
    assert cmp.notices and "환율" in cmp.notices[0] and "-4.0%" in cmp.notices[0]
    assert sc.common_shift_pct(cmp.rows) == pytest.approx(-4.0)


def test_no_shift_notice_when_amounts_are_stable_or_too_few():
    raw = [_raw(f"C{i}", "2026-08", 1000.0, 1000.0, 10, 10) for i in range(6)]
    cmp = sc.assemble("20260831", "20260903", "2026-08", raw)
    assert not any("환율" in n for n in cmp.notices)
    # 두 곳뿐이면 판단하지 않는다 — 우연이 중앙값이 된다
    few = sc.assemble("20260831", "20260903", "2026-08",
                      [_raw("A", "2026-08", 100, 90, 1, 1), _raw("B", "2026-08", 100, 90, 1, 1)])
    assert sc.common_shift_pct(few.rows) is None


def test_row_count_change_excludes_a_company_from_the_shift_baseline():
    # 건수가 바뀐 행(이월)은 재환산 기준선에 안 들어간다 — 들어가면 이월이 환율로 위장한다
    raw = [_raw(f"C{i}", "2026-08", 1000.0, 1000.0, 10, 10) for i in range(4)]
    raw += [_raw(f"M{i}", "2026-08", 1000.0, 200.0, 10, 3) for i in range(6)]
    cmp = sc.assemble("20260831", "20260903", "2026-08", raw)
    assert sc.common_shift_pct(cmp.rows) == pytest.approx(0.0)


def test_empty_result_says_so_instead_of_a_blank_table():
    cmp = sc.assemble("20260831", "20260903", "2026-08", [])
    assert cmp.rows == [] and any("어디에도 없습니다" in n for n in cmp.notices)


def test_null_company_name_is_kept_under_a_visible_label():
    cmp = sc.assemble("20260831", "20260903", "2026-08", [_raw(None, "2026-08", 10, 5, 1, 1)])
    assert cmp.rows[0].company == "(거래처 없음)"


# ── 조회 배선 ────────────────────────────────────────────────────────────

def test_compare_validates_then_runs_one_query_with_injected_runner():
    calls = []

    def runner(sql):
        calls.append(sql)
        return [_raw("X", "2026-09", 10, 5, 1, 1)]

    cmp = sc.compare("20260930", "20260912", None, runner=runner, snapshots=SNAPS)
    assert cmp.date_a == "20260912" and cmp.month == "2026-09"
    assert len(calls) == 1 and "SALES_ALL_Backup_20260912" in calls[0]


def test_list_snapshots_parses_table_ids_and_caches(monkeypatch):
    monkeypatch.setattr(sc, "_snapshot_cache", ([], 0.0))
    calls = []

    def runner(sql):
        calls.append(sql)
        return [{"table_id": "SALES_ALL_Backup"}, {"table_id": "SALES_ALL_Backup_20260912"},
                {"table_id": "SALES_ALL_Backup_20260901"}, {"table_id": "other"}]

    assert sc.list_snapshots(runner=runner) == ["20260901", "20260912"]
    assert sc.list_snapshots(runner=runner) == ["20260901", "20260912"]
    assert len(calls) == 1, "30분 안에는 다시 읽지 않는다"
    monkeypatch.setattr(sc, "_snapshot_cache", ([], 0.0))


# ── 배선: 라우터·사이드바·화면 ───────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]


def test_page_and_button_are_wired():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "sales_carryover_router" in main and "include_router(sales_carryover_router)" in main
    html = (ROOT / "app/frontend/chat.html").read_text(encoding="utf-8")
    js = (ROOT / "app/frontend/chat.js").read_text(encoding="utf-8")
    assert 'id="btn-sales-carryover"' in html
    assert re.search(r'getElementById\("btn-sales-carryover"\)[\s\S]{0,200}"/sales-carryover"', js)
    page = (ROOT / "app/static/sales_carryover.html").read_text(encoding="utf-8")
    assert 'id="sc-notice"' in page and page.index('id="sc-notice"') < page.index('id="sc-table"'), \
        "공시는 표보다 먼저 온다"


# ── API ─────────────────────────────────────────────────────────────────

_SECRET = "test-only-carryover-session-secret-" + "x" * 40
_TENANT = "11111111-1111-4111-8111-111111111111"


class _User:
    id = 7
    email = "tester@skin1004korea.com"
    role = "user"


@pytest.fixture
def client(monkeypatch):
    from app.api import auth_middleware
    from app.api.auth_middleware import get_current_user
    from app.config import get_settings
    monkeypatch.setenv("MIGRATED_REDIRECT_URL", "")
    monkeypatch.setenv("JWT_SECRET_KEY", _SECRET)
    monkeypatch.setenv("ENTRA_TENANT_ID", _TENANT)
    monkeypatch.setenv("PASSWORD_LOGIN_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(auth_middleware, "_user_cache", {})
    # 공통 세션 미들웨어가 `get_current_user` 를 직접 부른다 — 의존성 오버라이드가 안 닿는다
    monkeypatch.setattr(auth_middleware, "fetch_one", lambda *args, **kwargs: {
        "id": 7, "email": _User.email, "role": _User.role, "display_name": "Tester",
        "account_active": 1, "directory_active": 1, "must_change_password": 0,
        "requires_group_assignment": 0,
    })
    from app.main import create_app
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _User()
    session = TestClient(app)
    session.cookies.set("token", jwt.encode({
        "user_id": 7, "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "purpose": "session", "auth_provider": "entra", "entra_tid": _TENANT,
        "entra_oid": "22222222-2222-4222-8222-222222222222",
    }, _SECRET, algorithm="HS256"))
    yield session
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _grant(monkeypatch, brand_filter, flag=1):
    from app.api import sales_carryover_api as api
    monkeypatch.setattr(api, "fetch_one",
                        lambda *a, **k: {"requires_group_assignment": flag, "brand_filter": brand_filter})


def test_snapshots_endpoint_returns_dates_for_sk_group(client, monkeypatch):
    _grant(monkeypatch, "SK,CL,CBT")
    monkeypatch.setattr(sc, "list_snapshots", lambda: ["20260912", "20260930"])
    r = client.get("/api/sales-carryover/snapshots")
    assert r.status_code == 200 and r.json()["dates"] == ["20260912", "20260930"]


def test_dd_only_group_is_refused(client, monkeypatch):
    _grant(monkeypatch, "UM")
    r = client.get("/api/sales-carryover/snapshots")
    assert r.status_code == 403 and "SK" in r.json()["detail"]


def test_unassigned_new_account_is_refused_but_legacy_account_passes(client, monkeypatch):
    monkeypatch.setattr(sc, "list_snapshots", lambda: ["20260912"])
    _grant(monkeypatch, "", flag=1)
    assert client.get("/api/sales-carryover/snapshots").status_code == 403
    _grant(monkeypatch, "", flag=0)
    assert client.get("/api/sales-carryover/snapshots").status_code == 200


def test_compare_endpoint_maps_input_errors_to_400(client, monkeypatch):
    _grant(monkeypatch, "SK")
    monkeypatch.setattr(sc, "list_snapshots", lambda runner=None, force=False: ["20260912", "20260930"])
    r = client.get("/api/sales-carryover/compare", params={"a": "20260913", "b": "20260930"})
    assert r.status_code == 400 and "백업 테이블이 없습니다" in r.json()["detail"]


def test_compare_endpoint_returns_assembled_rows(client, monkeypatch):
    _grant(monkeypatch, "SK")
    monkeypatch.setattr(sc, "compare", lambda a, b, m: sc.assemble(
        "20260912", "20260930", "2026-09", [_raw("Gone", "2026-09", 100, None, 2, None)]))
    r = client.get("/api/sales-carryover/compare", params={"a": "20260912", "b": "20260930"})
    body = r.json()
    assert r.status_code == 200 and body["rows"][0]["status"] == "사라짐"
    assert body["summary"]["gone"] == 1


def test_page_redirects_to_login_without_session(client):
    client.cookies.clear()
    r = client.get("/sales-carryover", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"
