# -*- coding: utf-8 -*-
"""스키마 변화 감지 — 앱이 데이터 변화를 모르던 구멍을 막는다.

⛔ 배경 (2026-08-18): 데이터분석파트가 리뷰 테이블을 국내/해외/매장으로 통합했는데
   **앱은 한 달 넘게 몰랐다.** "국내몰 리뷰 2026년" 이 42,427건 중 4,140건만 셌고
   "플래그십 리뷰" 는 조회조차 못 했다. 에러는 하나도 안 났다 — 숫자가 그럴듯하게
   작게 나왔을 뿐이라 제보가 없었으면 계속 틀린 채로 답했다.
"""
import pytest

from app.core import schema_watch as sw


def _snap(**tables):
    return {k: v for k, v in tables.items()}


class TestDiff:
    def test_added_table(self):
        d = sw.diff({}, _snap(**{"Review_Data.Store_Review": {"a": "STRING"}}))
        assert any("새 테이블" in x for x in d["added_tables"])

    def test_removed_table(self):
        d = sw.diff(_snap(**{"Review_Data.Old": {"a": "STRING"}}), {})
        assert any("사라진 테이블" in x for x in d["removed_tables"])

    def test_added_and_removed_columns(self):
        prev = _snap(**{"Review_Data.T": {"a": "STRING"}})
        cur = _snap(**{"Review_Data.T": {"b": "INT64"}})
        d = sw.diff(prev, cur)
        assert any("+ Review_Data.T.b" in x for x in d["added_columns"])
        assert any("- Review_Data.T.a" in x for x in d["removed_columns"])

    def test_type_change(self):
        prev = _snap(**{"Review_Data.T": {"review_date": "STRING"}})
        cur = _snap(**{"Review_Data.T": {"review_date": "DATE"}})
        d = sw.diff(prev, cur)
        assert any("STRING → DATE" in x for x in d["changed_types"])

    def test_no_change(self):
        same = _snap(**{"Review_Data.T": {"a": "STRING"}})
        d = sw.diff(same, same)
        assert all(not v for v in d.values())


class TestWatchedBucket:
    """⚠️ 화이트리스트 테이블의 변화만 실패로 올린다 — 나머지는 매일 뜨면 소음이다."""

    def test_whitelisted_change_is_watched(self):
        """앱이 실제로 쓰는 테이블(리뷰 통합본)의 변화는 반드시 잡혀야 한다."""
        prev = _snap(**{"Review_Data.Store_Review": {"shopname": "STRING"}})
        cur = _snap(**{"Review_Data.Store_Review": {"shopname": "STRING",
                                                    "new_col": "INT64"}})
        assert sw.diff(prev, cur)["watched"], "화이트리스트 변화가 watched 에 없다"

    def test_unwatched_change_is_not_escalated(self):
        prev = _snap(**{"Review_Data.Review_Data_wordcloud_raw": {"a": "STRING"}})
        cur = _snap(**{"Review_Data.Review_Data_wordcloud_raw": {"a": "STRING",
                                                                 "b": "INT64"}})
        d = sw.diff(prev, cur)
        assert d["added_columns"], "변화 자체는 기록되어야 한다"
        assert not d["watched"], "화이트리스트 밖인데 실패로 올라갔다"

    def test_review_consolidation_would_have_been_caught(self):
        """이 사고를 이 검사가 잡았을지 — 통합본이 새로 생기는 상황."""
        prev = _snap(**{"Review_Data.New_Smartstore_Review": {"a": "STRING"}})
        cur = dict(prev, **{"Review_Data.Korea_mall_Review": {"a": "STRING"}})
        assert sw.diff(prev, cur)["watched"]


class TestScope:
    def test_watched_datasets_are_the_app_ones(self):
        for ds in ("Sales_Integration", "Review_Data", "marketing_analysis"):
            assert ds in sw.WATCHED_DATASETS

    def test_project_wide_not_watched(self):
        """⚠️ 프로젝트 전체는 3,500개가 넘어 매일 알림이 소음이 된다."""
        assert len(sw.WATCHED_DATASETS) <= 8


class TestRegistered:
    def test_self_check_registered(self):
        from app.core.self_check import CHECKS
        assert any(c.id == "schema_changes" for c in CHECKS)
