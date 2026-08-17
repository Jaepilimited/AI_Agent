# -*- coding: utf-8 -*-
"""답변 수치 검증기의 오탐을 줄이되 **검출력은 죽이지 않는다**.

⛔ 이 검사는 양방향으로 실패할 수 있고, 두 방향 모두 조용하다:
   - 오탐이 많으면 경보가 소음이 되어 **아무도 안 본다**
   - 너무 넓히면 조회에 없는 값이 우연히 설명되어 **오답이 통과한다**
   그래서 모든 완화에는 반대 방향 검사를 같이 둔다.

2026-08-18 프로덕션 로그(7일 56건) 분석에서 나온 오탐 두 종을 고정한다.
"""
import pytest

from app.core.answer_check import _numbers_in, verify

_ROWS = [{"country": "인도네시아", "sales": 8_720_000_000}]


class TestNamesAreNotNumbers:
    """⚠️ 낱말에 붙은 숫자는 값이 아니라 이름이다 (실측: SKIN1004 의 1004)."""

    @pytest.mark.parametrize("text,expected", [
        ("SKIN1004 브랜드 매출은 87.2억입니다", [87.2]),
        ("Q10 채널 100ml 제품 41.1억", [41.1]),
        ("B2B 매출 12.5억", [12.5]),
        ("SPF50 제품 3.4억", [3.4]),
    ])
    def test_alnum_token_excluded(self, text, expected):
        assert _numbers_in(text) == expected

    def test_korean_unit_is_not_a_letter(self):
        """⛔ 파이썬에서 한글도 isalpha() 다. ASCII 로 한정하지 않으면
           `87.2억`·`59건` 의 단위에 걸려 **검출력이 통째로 죽는다.**"""
        assert _numbers_in("총 59건") == [59.0]
        assert _numbers_in("매출 87.2억원") == [87.2]
        assert _numbers_in("증가율 12.3%") == [12.3]


class TestRowCountIsExplained:
    """행 수는 조회 결과가 설명하는 값이다 — 실측 발생률 1위였다."""

    def test_total_count_accepted(self):
        rows = [{"m": "2026-01", "n": 5}] * 59
        assert verify("총 59건의 프로모션이 있습니다.", rows, "프로모션 일정")["unverified"] == []

    def test_zero_rows(self):
        assert verify("0건입니다.", [], "질문")["unverified"] == []


class TestDetectionStillWorks:
    """⛔ 완화가 진짜 오답까지 통과시키면 이 모듈은 의미가 없다."""

    def test_fabricated_number_caught(self):
        r = verify("베트남 매출은 88.3억입니다.", [{"c": "일본", "s": 100}], "매출")
        assert "88.3" in r["unverified"]

    def test_real_row_value_passes(self):
        r = verify("인도네시아 매출은 87.2억입니다.", _ROWS, "인도네시아 매출")
        assert r["unverified"] == []

    def test_name_digits_do_not_launder_a_wrong_number(self):
        """이름 제외가 **인접한 진짜 오답**을 가려주면 안 된다.

        ⚠️ 값은 반올림 허용치(2%) 밖으로 고른다 — 87.2 옆의 88.3 은 1.3% 차이라
           정상적으로 통과한다(표기 반올림). 그걸로 검출력을 재면 테스트가 거짓말한다.
        """
        r = verify("SKIN1004 의 베트남 매출은 152.6억입니다.", _ROWS, "매출")
        assert "152.6" in r["unverified"]
