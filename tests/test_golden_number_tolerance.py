"""골든 문항이 **살아 있는 데이터의 값을 문자열로 얼리는 것**을 막는다.

2026-09-07 실측으로 드러난 것: 문항 4개가 매일 실패하고 있었는데 앱은 정상이었다.
얼려 둔 숫자만 낡은 것이다.

    log_team_spelling_scatter          789 → 실제 790   (+1건)
    log_routing_not_missing_data       930 → 실제 932   (+2건)
    inc_brand_named_not_company_wide   4,322 → 4,321.4억
    inc_company_wide_stays_company_wide 5,577 → 5,576.9억

⛔ 더 나쁜 것은 **음성 단언이 조용히 죽은 것**이다. `inc_brand_named_...` 의
   `not_contains: "5,577"` 은 전사값이 5,576.9 로 밀리는 순간 아무것도 못 잡는다 —
   붐따 #100(전사값을 브랜드 매출로 답한 사고)이 오늘 되살아나도 그 단언은 통과한다.
   실패하는 문항은 눈에 띄지만, **무력해진 음성 단언은 아무 흔적도 남기지 않는다.**

그래서 허용오차 단언을 둔다. 배율은 무시한다 — 답변은 `432,140,854,164` 라고도
`4,321.4억` 이라고도 쓴다.
"""

import json

import pytest

from app.core.answer_check import has_number_near
from app.core.golden_runner import _evaluate, load_golden_set

# 2026-09-07 실측 (BigQuery 직접 조회)
BRAND_H1 = 432_140_854_164      # 스킨천사(SK+CBT, ZB 제외) 상반기
COMPANY_H1 = 557_691_820_060    # 전사 상반기


# ── has_number_near ──────────────────────────────────────────────────────────

def test_matches_the_raw_number_written_out_in_full() -> None:
    assert has_number_near("총매출액은 432,140,854,164원입니다", BRAND_H1)


def test_matches_the_same_value_written_in_eok_units() -> None:
    """답변은 배율을 바꿔 쓴다 — `4,321.4억` 과 원 단위는 같은 값이다."""
    assert has_number_near("총매출액은 약 4,321.4억원입니다", BRAND_H1)


def test_tolerance_absorbs_data_drift_but_not_a_different_aggregate() -> None:
    # 어제 값이어도 통과해야 한다 (드리프트)
    assert has_number_near("약 4,321.9억원", BRAND_H1, tol=0.05)
    # 전사값(29% 차이)은 통과하면 안 된다 — 이 문항이 잡으려는 바로 그 오답이다
    assert not has_number_near("약 5,576.9억원", BRAND_H1, tol=0.05)


def test_ignores_digits_that_are_part_of_a_name() -> None:
    """⚠️ `SKIN1004` 의 1004 는 값이 아니라 이름이다 (answer_check 가 이미 아는 함정)."""
    assert not has_number_near("SKIN1004 브랜드입니다", 1004)


def test_a_wrong_scale_is_not_near() -> None:
    assert not has_number_near("약 828.7억원", 82_871_719, tol=0.02)


# ── _evaluate 배선 ────────────────────────────────────────────────────────────

def _item(**expect):
    return {"id": "t", "question": "q", "expect": {"min_len": 1, **expect}}


def test_number_near_passes_when_the_answer_carries_the_value() -> None:
    reasons = _evaluate(_item(number_near={"value": 790, "pct": 10}),
                        "영업1팀 발주 건수는 총 790건입니다", 1.0)
    assert reasons == []


def test_number_near_fails_when_the_answer_is_off_by_more_than_tolerance() -> None:
    """표기 흩어짐을 놓치면 789 가 아니라 2 가 나온다 — 그것을 잡아야 한다."""
    reasons = _evaluate(_item(number_near={"value": 790, "pct": 10}),
                        "영업1팀 발주 건수는 총 2건입니다", 1.0)
    assert reasons and "790" in reasons[0]


def test_number_not_near_fails_when_the_forbidden_value_appears() -> None:
    reasons = _evaluate(_item(number_not_near={"value": COMPANY_H1, "pct": 5}),
                        "SKIN1004 상반기 매출은 약 5,576.9억원입니다", 1.0)
    assert reasons and "5" in reasons[0]


def test_number_not_near_passes_on_the_correct_answer() -> None:
    reasons = _evaluate(_item(number_not_near={"value": COMPANY_H1, "pct": 5}),
                        "SKIN1004 상반기 매출은 약 4,321.4억원입니다", 1.0)
    assert reasons == []


# ── 문항 자체가 값을 얼리지 않는지 ──────────────────────────────────────────────

DRIFT_PRONE = {
    "inc_brand_named_not_company_wide",
    "inc_company_wide_stays_company_wide",
    "log_team_spelling_scatter",
    "log_routing_not_missing_data",
}


@pytest.mark.parametrize("item_id", sorted(DRIFT_PRONE))
def test_drift_prone_items_do_not_freeze_a_live_total_as_a_string(item_id: str) -> None:
    """⛔ 살아 있는 집계값을 문자열로 못 박지 마라 — 반드시 낡고, 낡으면 매일 실패한다.

    연도(2026)나 작은 정수는 괜찮다. 자릿수가 큰 값만 막는다.
    """
    item = next(i for i in load_golden_set() if i["id"] == item_id)
    exp = item["expect"]
    frozen = []
    for key in ("contains_any", "contains_all", "not_contains"):
        for kw in exp.get(key, []):
            digits = kw.replace(",", "").replace(".", "")
            if digits.isdigit() and len(digits) >= 3 and not (1900 <= int(digits) <= 2100):
                frozen.append(f"{key}:{kw}")
    assert not frozen, (
        f"{item_id} 가 아직 값을 문자열로 얼려 두었다: {frozen}. "
        "number_near / number_not_near 로 허용오차를 두어라."
    )


def test_every_drift_prone_item_still_asserts_something_about_its_numbers() -> None:
    """⚠️ 얼린 숫자를 걷어내면서 **아무것도 검사하지 않는 문항**을 만들면 안 된다.

    그건 매일 통과하는 빈 문항이고, 매일 실패하는 문항보다 나쁘다 — 조용하기 때문이다.
    """
    for item_id in sorted(DRIFT_PRONE):
        exp = next(i for i in load_golden_set() if i["id"] == item_id)["expect"]
        has_check = any(k in exp for k in
                        ("number_near", "number_not_near", "sql_contains_any",
                         "contains_all", "contains_any"))
        assert has_check, f"{item_id} 에 남은 단언이 없다"


def test_the_golden_set_file_stays_valid_json() -> None:
    with open("data/golden_set.json", encoding="utf-8") as fh:
        json.load(fh)
