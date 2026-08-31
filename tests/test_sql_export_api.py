"""CSV 다운로드 엔드포인트 — 소유자만 열 수 있어야 한다.

매출 데이터라 원가·거래처별 수치가 들어간다. 보고서 열람(`app/reports/store.py`)과
같은 원칙: 판정은 단 한 곳, 남의 토큰과 없는 토큰은 같은 404를 준다.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import sql_export_api
from app.core import sql_result_store as store
from app.db.models import User


def _client_as(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(sql_export_api.router)
    app.dependency_overrides[sql_export_api.get_current_user] = lambda: User(id=user_id)
    return TestClient(app)


def test_owner_can_download_the_full_csv():
    token = store.save(
        11,
        ["country", "revenue"],
        [{"country": f"국가{i}", "revenue": i} for i in range(200)],
        labels={"country": "국가", "revenue": "매출"},
    )

    resp = _client_as(11).get(f"/api/sql-results/{token}/csv")

    assert resp.status_code == 200
    text = resp.content.decode("utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln]
    assert len(lines) == 201  # 헤더 + 200행 전부, 표처럼 잘리지 않는다
    assert "attachment" in resp.headers.get("content-disposition", "")


def test_another_user_gets_404_not_the_data():
    token = store.save(11, ["country"], [{"country": "베트남"}])

    resp = _client_as(99).get(f"/api/sql-results/{token}/csv")

    assert resp.status_code == 404
    assert "베트남" not in resp.text


def test_unknown_token_is_also_404():
    resp = _client_as(11).get("/api/sql-results/does-not-exist/csv")

    assert resp.status_code == 404


def test_hangul_survives_the_actual_http_round_trip():
    token = store.save(
        11, ["country"], [{"country": "베트남"}], labels={"country": "국가"}
    )

    resp = _client_as(11).get(f"/api/sql-results/{token}/csv")

    text = resp.content.decode("utf-8-sig")
    assert "베트남" in text
    assert "국가" in text
