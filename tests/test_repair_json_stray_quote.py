# -*- coding: utf-8 -*-
"""Gemini 가 낸 JSON 결함으로 차트가 조용히 사라지던 것을 지킨다.

⛔ 이 결함군의 특징은 **에러가 사용자에게 안 보인다**는 것이다. 차트만 없어지고
   답변은 정상으로 나가므로 아무도 신고하지 않는다. 실제로 하루 38건이 사라지고
   있었다(2026-08-11 발견).

원문은 전부 프로덕션 로그에서 그대로 가져왔다 — 손으로 지어낸 예시를 쓰면
실제 모델이 내는 형태를 놓친다.
"""
import json

import pytest

from app.core.llm import repair_json

# 2026-08-12·14 실측. 마지막 값 뒤에 **홀로 뜬 따옴표**가 한 줄 있다.
# 괄호는 멀쩡해서 기존 괄호 수선으로는 잡히지 않았고, 차트 4건이 유실됐다.
_STRAY_QUOTE = (
    '{\n'
    '  "needs_chart": true,\n'
    '  "chart_type": "horizontal_bar",\n'
    '  "x_column": "product_name",\n'
    '  "y_column": "total_revenue",\n'
    '  "group_column": null,\n'
    '  "title": "히알루시카 선세럼 제품별 올해 매출 현황",\n'
    '  "x_label": "매출액",\n'
    '  "y_label": "제품명"\n'
    '"\n'
    '}'
)


class TestStrayQuote:
    def test_production_payload_recovers(self):
        d = json.loads(repair_json(_STRAY_QUOTE))
        assert d["chart_type"] == "horizontal_bar"
        assert d["y_label"] == "제품명"
        assert d["needs_chart"] is True

    def test_second_production_payload(self):
        raw = _STRAY_QUOTE.replace("total_revenue", "total_quantity")
        assert json.loads(repair_json(raw))["y_column"] == "total_quantity"


class TestDoesNotBreakValidInput:
    """⚠️ 수선이 멀쩡한 응답을 망가뜨리면 더 나쁘다."""

    @pytest.mark.parametrize("raw", [
        '{"a": 1}',
        '{\n  "a": 1,\n  "b": "x"\n}',
        '[]',
        '{"t": "따옴표 \\" 포함"}',
        '{"multi": "줄바꿈\\n포함"}',
    ])
    def test_valid_json_untouched(self, raw):
        assert repair_json(raw) == raw
        json.loads(repair_json(raw))

    def test_quote_inside_string_survives(self):
        raw = '{"t": "a\\"b"}'
        assert json.loads(repair_json(raw))["t"] == 'a"b'


class TestExistingRepairsKept:
    """2026-08-11 에 넣은 괄호 수선이 계속 동작해야 한다."""

    def test_missing_close_brace(self):
        assert json.loads(repair_json('{"a": 1, "b": 2'))["b"] == 2

    def test_extra_close_brace(self):
        assert json.loads(repair_json('{"a": 1}}'))["a"] == 1

    def test_unrepairable_returned_as_is(self):
        """고칠 수 없으면 원문 그대로 — 호출부가 로그를 남긴다."""
        bad = "이건 JSON 이 아니다"
        assert repair_json(bad) == bad
