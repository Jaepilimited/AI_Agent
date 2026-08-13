# -*- coding: utf-8 -*-
"""보고서 규칙 회귀 — 골든셋이 볼 수 없는 안쪽을 본다.

골든셋은 **사용자가 보는 답변 문자열**만 판정한다. 보고서는 채팅에 요약 4줄만
돌려주므로, 판정 임계값·필터 추출·외부 절 방어 같은 것은 골든 문항으로 감시할 수 없다.
"기대 키워드가 실패 답변에도 들어가면 그 문항은 무용지물"([[CLAUDE.md]]) 과 같은 이유로,
**관측할 수 없는 것을 골든으로 감시하는 척하지 않는다.** 여기서 결정적으로 검사한다.

조회도 LLM 호출도 하지 않는다 — 전부 순수 함수다.
"""
import pytest

from app.reports import external, judge, registry
from app.reports.blocks import _josa


# ── 질문에서 필터 뽑기 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("question,expected", [
    # 2026-08-13 사고: '외부 요인도' 의 '인도' 를 국가로 잡아 일본 보고서가
    # '일본 · 인도' 보고서가 됐다. 앞 글자가 한글이면 더 긴 낱말의 일부로 본다
    ("2026 상반기 일본 매출 보고서 만들어줘 날씨나 외부 요인도 같이 봐줘", {"국가": ["일본"]}),
    ("매출 요인 분석 보고서", {}),
    # 반대 방향 — 진짜 인도는 계속 잡아야 한다
    ("인도 매출 보고서", {"국가": ["인도"]}),
    ("인도네시아 매출 보고서", {"국가": ["인도네시아"]}),
    # 조사가 붙는 것은 정상이므로 **뒤는 보지 않는다**
    ("베트남과 태국 매출 보고서", {"국가": ["베트남", "태국"]}),
    # 팀 이름 안의 국가어를 국가로 잡으면 그 팀의 다른 나라 실적이 통째로 빠진다
    ("중국사업팀 상반기 실적 보고서", {"팀": ["CBT"]}),
    ("미국 B2C 채널별 매출 보고서", {"국가": ["미국"], "영업유형": ["B2C"]}),
])
def test_extract_filters(question, expected):
    assert registry.extract_filters(question) == expected


# ── 보고서를 만들 질문인가 ────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "2026년 일본 매출 보고서 만들어줘",
    "우마 브랜드 매출 리포트 뽑아줘",
])
def test_wants_report_true(question):
    assert registry.wants_report(question)


@pytest.mark.parametrize("question", [
    # 신호어는 있지만 대상이 시스템 자신이다 — 분석할 데이터가 없는 질문
    "보고서 기능은 어떤 때 쓰면 좋아?",
    "이 시스템으로 뭘 할 수 있는지 짧게 정리해줘",
    "보고서 만드는 방법 알려줘",
    # 신호어가 아예 없다
    "2026년 상반기 일본 매출 정리해줘",
])
def test_wants_report_false(question):
    assert not registry.wants_report(question)


def test_report_route_needs_explicit_or_wording():
    """`@@보고서` 지정이면 문구를 보지 않는다 — explicit 이 관통하는지 본다."""
    assert registry.route("2026년 일본 매출", explicit=True) is not None
    assert registry.route("2026년 일본 매출", explicit=False) is None


# ── 판정 계층 ────────────────────────────────────────────────────────────────

def test_headline_skips_methodology():
    """⛔ 방법론 문장은 결론이 아니다 — 실제로 결론 자리에 올라와 있었다."""
    s = {"block": "movers", "rows": [], "columns": [], "findings": [
        "전체의 0.5% 미만인 항목은 뺐다 — 작은 수의 배율 변동을 막기 위해서다",
        "급증: 미국 +173%",
        "급감: 라오스 -100%",
    ]}
    judge.apply([s])
    assert s["headline"].startswith("급증")
    assert "뺐다" not in s["headline"]
    assert 0 not in s["headline_skip"]        # 방법론은 본문 글머리표에 남는다


def test_headline_absent_when_only_methodology():
    s = {"block": "x", "rows": [], "columns": [],
         "findings": ["0.5% 미만은 뺐다", "제외 기준은 이렇다"]}
    judge.apply([s])
    assert not s.get("headline")


def test_growth_verdicts():
    s = {"block": "compare", "dim": "국가", "findings": ["x"], "columns": [], "rows": [
        {"dim": "A", "growth": 66.0}, {"dim": "B", "growth": 7.0},
        {"dim": "C", "growth": 1.0}, {"dim": "D", "growth": -20.0},
        {"dim": "E", "growth": -70.0},
    ]}
    judge.apply([s])
    assert [r["verdict"] for r in s["rows"]] == ["급증", "증가", "보합", "감소", "급감"]


def test_ratio_baseline_excludes_impossible_and_zero():
    """⛔ 있을 수 없는 120% 하나가 평균을 끌어올려 멀쩡한 행이 죄다 '낮음'이 됐었다."""
    s = {"block": "ratio", "metric": "할인", "metric2": "매출", "dim": "채널",
         "findings": ["전체 8.0%"], "columns": [], "rows": [
             {"dim": "A", "ratio": 120.0},   # 할인이 매출보다 클 수 없다 → 데이터 문제
             {"dim": "B", "ratio": 10.0},
             {"dim": "C", "ratio": 8.0},
             {"dim": "D", "ratio": 0.0},
             {"dim": "E", "ratio": 14.0},
         ]}
    judge.apply([s])
    got = {r["dim"]: r["verdict"] for r in s["rows"]}
    assert got["A"] == "확인 필요"
    assert got["D"] == "미집계"
    assert got["E"] == "높음" and got["C"] == "낮음"
    # 이상값이 기준에 섞였다면 평균이 30을 넘고 B 까지 '낮음'이 된다
    assert got["B"] == "평균"
    assert "100%" in " ".join(s["findings"])


def test_focus_applies_size_floor_without_share_column():
    """⛔ `share` 가 없는 절에서 규모 필터가 통과해 잔챙이가 실행안 1위로 올라왔다."""
    s = {"block": "compare", "dim": "국가", "findings": ["x"], "columns": [], "rows": [
        {"dim": "미국", "value": 1153.6, "growth": 173.0},
        {"dim": "러시아", "value": 12.0, "growth": 183.6},     # 증가율은 1위지만 잔챙이
        {"dim": "불가리아", "value": 0.4, "growth": -100.0},
        {"dim": "중국", "value": 327.0, "growth": -30.0},
    ]}
    judge.apply([s])
    foc = judge.focus([s], {}, [], [])
    names = [r["dim"] for r in foc["rows"]]
    assert "미국" in names and "중국" in names
    assert "러시아" not in names and "불가리아" not in names


def test_focus_puts_data_gaps_before_row_level_items():
    """데이터 구멍을 뒤에 두면 버킷 상한에 잘려 사라진다."""
    ratio = {"block": "ratio", "metric": "전환매출", "metric2": "광고비", "dim": "국가",
             "findings": ["y"], "columns": [],
             "rows": [{"dim": f"N{i}", "ratio": 0.0} for i in range(5)]}
    judge.apply([ratio])
    foc = judge.focus([ratio], {}, [{"label": "수수료 미반영", "text": "Service_Fee 가 음수"}],
                      ["제품별 매출"])
    check = [r["dim"] for r in foc["rows"] if r["bucket"] == "확인할 곳"]
    assert check[0] == "수수료 미반영"


# ── 외부 맥락 절 — 코드가 버리는 것 ──────────────────────────────────────────

@pytest.mark.parametrize("text,reason", [
    ("기온이 35도까지 올라 무더웠다", "숫자"),
    ("연휴 때문에 이동 수요가 늘었다", "인과"),
    ("골든위크로 매출이 늘었다", "실적 언급"),
])
def test_external_drops_unverifiable(text, reason):
    dropped = []
    assert external._clean({"month": "2026-04", "text": text, "source": "언론"},
                           "2026-01", "2026-06", dropped) is None, reason
    assert dropped


def test_external_keeps_plain_event():
    dropped = []
    got = external._clean(
        {"month": "2026-04", "text": "일본의 장기 연휴인 골든위크가 시작되었다", "source": "관광청"},
        "2026-01", "2026-06", dropped)
    assert got and got["dim"] == "2026-04" and not dropped


def test_external_only_when_asked():
    assert external.wants_external("일본 매출 보고서, 날씨 같은 외부 요인도 봐줘")
    assert not external.wants_external("일본 매출 보고서 만들어줘")


# ── 한국어 조사 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("word,kind,expected", [
    ("일본", "은는", "일본은"),
    ("미국", "은는", "미국은"),
    ("인도네시아", "은는", "인도네시아는"),
    ("55.1억", "이가", "55.1억이"),
    ("2", "이가", "2가"),            # 이(二) — 받침 없음
    ("3", "이가", "3이"),            # 삼 — 받침 있음
])
def test_josa(word, kind, expected):
    assert _josa(word, kind) == expected
