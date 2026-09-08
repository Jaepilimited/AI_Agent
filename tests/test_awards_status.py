# -*- coding: utf-8 -*-
"""수상/랭킹 System Status 카드 — OP 재고 카드와 같은 규칙을 따른다.

`app/core/safety.py::get_safety_status()` 가 `services["수상"]` 을 낸다.
OP(`app/core/inventory.py`)와 같은 자리에 있고, 판정 방식도 같다:
  · 정상 — 적재 건수 + 구분별 분포 + 마지막 적재 시각 + 원본 시트 URL
  · 0행/테이블 없음 — `status: "updating"` + `"적재 대기"` + URL
  · 예외 — `status: "error"` + 잘라낸 사유 (URL 없음, OP 와 동일)

⚠️ `app/core/awards.py` 는 다른 세션이 작업 중이라 여기서는 **읽기만** 한다
   (CLAUDE.md 공유 작업트리 규칙). 실제 DB 를 타지 않도록 `awards.status` 와
   `app.db.mariadb.fetch_all` 을 monkeypatch 한다.
"""
from datetime import datetime
from unittest.mock import patch

import app.db.mariadb as mariadb
from app.core import awards, safety


def test_awards_card_carries_the_source_sheet_url():
    """OP 처럼 '시트 연결' — 카드가 원본 시트 URL 을 실어야 한다."""
    with patch.object(awards, "status",
                       return_value={"count": 206, "synced_at": datetime(2026, 9, 8, 12, 36)}), \
         patch.object(mariadb, "fetch_all",
                      side_effect=lambda sql, *a, **k:
                          [{"category": "랭킹", "c": 206}] if "awards_rankings" in sql else []):
        status = safety.get_safety_status()

    card = status["services"]["수상"]
    assert card["url"] == awards.SHEET_URL
    assert card["status"] == "ok"


def test_awards_card_breaks_down_by_category_highest_first():
    """구분별 건수가 detail 에 들어가고, 많은 순으로 정렬돼야 한다."""
    def _fetch_all(sql, *a, **k):
        if "awards_rankings" in sql:
            return [{"category": "랭킹", "c": 94}, {"category": "설문", "c": 60},
                     {"category": "수상", "c": 52}]
        return []

    with patch.object(awards, "status",
                       return_value={"count": 206, "synced_at": datetime(2026, 9, 8, 12, 36)}), \
         patch.object(mariadb, "fetch_all", side_effect=_fetch_all):
        status = safety.get_safety_status()

    detail = status["services"]["수상"]["detail"]
    assert "랭킹" in detail and "94" in detail
    assert "설문" in detail and "60" in detail
    assert "수상" in detail and "52" in detail
    # 많은 순 정렬 — 랭킹이 설문보다 먼저 나와야 한다
    assert detail.index("랭킹") < detail.index("설문") < detail.index("수상")
    # 총 건수와 적재 시각도 함께 실린다
    assert "206" in detail
    assert "09-08" in detail and "12:36" in detail


def test_awards_card_shows_updating_when_table_is_empty():
    """테이블이 없거나 0행이면 죽지 않고 OP 처럼 '적재 대기' 로 나와야 한다."""
    with patch.object(awards, "status", return_value={"count": 0, "synced_at": None}):
        status = safety.get_safety_status()

    card = status["services"]["수상"]
    assert card["status"] == "updating"
    assert card["detail"] == "적재 대기"
    assert card["url"] == awards.SHEET_URL


def test_awards_card_reports_error_without_url_when_query_fails():
    """조회가 죽어도 카드 전체가 죽지 않는다 — OP 의 예외 처리와 같은 모양이다.

    ⚠️ OP 는 예외 시 `url` 을 싣지 않는다 (`services["OP"] = {"status": "error",
    "detail": str(_e)[:30]}`) — 수상 카드도 그 모양을 그대로 따른다.
    """
    with patch.object(awards, "status", side_effect=RuntimeError("db down")):
        status = safety.get_safety_status()

    card = status["services"]["수상"]
    assert card["status"] == "error"
    assert "url" not in card
