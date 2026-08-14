# -*- coding: utf-8 -*-
"""조용한 실패 탐지 — **에러가 나지 않는 고장**을 찾는다.

이 시스템에서 발견이 늦는 결함은 예외를 던지지 않는다. 없는 CSS 변수는 폴백으로
넘어가고, 템플릿 스크립트가 깨지면 서버는 200 을 주며, 프론트와 서버의 `@@` 목록이
어긋나도 질문만 조용히 오염된다. **사람이 눈으로 볼 때까지 아무도 모른다.**

⛔ **판정 로직은 여기 없다** — `app/core/static_checks.py` 한 곳에 있고, 서버의
   자가 점검(매일 07:30)도 **같은 함수**를 부른다. 서버에는 pytest 도 tests/ 도 없어서
   테스트만으로는 매일 돌 수 없다. 같은 규칙을 두 번 구현하면 한쪽만 고쳐진다.
"""
import glob
import io
import os

import pytest

from app.core import static_checks as SC

ROOT = SC.ROOT


@pytest.mark.parametrize("check_id,fn,label",
                         SC.ALL, ids=[c[0] for c in SC.ALL])
def test_static_check(check_id, fn, label):
    ok, detail = fn()
    assert ok, f"{label}: {detail}"


def test_report_templates_have_valid_script():
    """⛔ 문법이 깨지면 **에러 없이 백지**가 나간다 (서버 200, HTML 정상 저장).

    node 가 필요해 자가 점검에는 넣지 못했다 — 서버에 node 가 없다. 개발 중에만 돈다.
    """
    import shutil

    from app.reports import render

    if not shutil.which("node"):
        pytest.skip("node 없음 — 문법 검사 건너뜀")
    for path in glob.glob(os.path.join(ROOT, "app/reports/templates/*.html")):
        with io.open(path, encoding="utf-8") as fh:
            err = render.lint_script(fh.read())
        assert not err, f"{os.path.basename(path)} 스크립트 문법 오류: {err}"


@pytest.mark.parametrize("key", sorted(SC.front_source_keys()))
def test_every_front_source_parses_cleanly(key):
    """고른 소스가 질문에서 **완전히** 걷혀야 한다 — 부스러기가 남으면 질문이 바뀐다."""
    from app.agents.orchestrator import OrchestratorAgent

    entry, clean = OrchestratorAgent.parse_db_prefix(f"@@{key} 매출 알려줘")
    assert entry, f"@@{key} 를 서버가 인식하지 못한다"
    assert clean.strip() == "매출 알려줘", f"@@{key} 파싱 후 질문이 오염됐다: {clean!r}"


# ── 채팅 답변 수치 검증 (2026-08-13) ────────────────────────────────────────
# 보고서는 이 방어선을 갖고 있었지만 채팅에는 없었다. 지금은 계측만 한다.

_ROWS = [{"country": "미국", "rev": 115360000000.0},
         {"country": "일본", "rev": 25650000000.0},
         {"country": "인도네시아", "rev": 51830000000.0}]


@pytest.mark.parametrize("answer", [
    "미국 1,153.6억, 일본 256.5억입니다.",          # 행 값 (억 표기)
    "미국 115,360,000,000원입니다.",                # 행 값 (원 표기)
    "전체 합계는 1,928.4억입니다.",                  # 열 합계
    "미국이 전체의 59.8%를 차지합니다.",              # 비중
    "일본은 미국 대비 -77.8% 입니다.",               # 증감률
    "2026년 상위 3개 국가입니다.",                   # 연도·작은 정수
])
def test_answer_check_accepts_derivable_numbers(answer):
    """⚠️ 정상 답변을 미검증으로 잡으면 경보가 무의미해진다 — 단위 변환이 특히 중요하다."""
    from app.core.answer_check import verify
    res = verify(answer, _ROWS, "2026 상반기 국가별 매출")
    assert not res["unverified"], f"정상 수치를 미검증으로 잡았다: {res['unverified']}"


@pytest.mark.parametrize("answer,bad", [
    ("미국 1,153.6억이며 광고비는 412.7억입니다.", "412.7"),   # 조회에 없는 지표
    ("미국이 전체의 88.8%를 차지합니다.", "88.8"),             # 틀린 비중
    ("베트남은 88.3억입니다.", "88.3"),                       # 조회에 없는 행
])
def test_answer_check_flags_unexplainable_numbers(answer, bad):
    from app.core.answer_check import verify
    res = verify(answer, _ROWS, "2026 상반기 국가별 매출")
    assert bad in res["unverified"], f"{bad} 를 못 잡았다: {res}"


def test_llm_clients_share_generate_json_signature():
    """⛔ 클라이언트를 바꿔 끼우려면 **서명이 같아야** 한다.

    ClaudeClient.generate_json 에 `max_output_tokens` 가 없어서, 그걸 넘기는 호출부에서
    TypeError 가 나고 호출부의 except 가 삼켰다 — 보고서 해석 절이 Claude 에서만
    조용히 사라졌다 (2026-08-14 모델 비교에서 발견).
    """
    import inspect

    from app.core.llm import ClaudeClient, GeminiClient

    g = set(inspect.signature(GeminiClient.generate_json).parameters)
    c = set(inspect.signature(ClaudeClient.generate_json).parameters)
    assert g <= c, f"Claude 에 없는 인자: {sorted(g - c)} — 호출부가 조용히 실패한다"


# ── 질문 → 검색 키워드 (2026-08-14) ────────────────────────────────────────
# ⛔ 한국어는 교착어라 **조사를 떼고 나서** 불용어를 봐야 한다. 안 그러면 검색이
#    조용히 빗나간다 — 드라이브는 항상 0건이었고("구글드라이브에서 …" 통째로 검색),
#    위키는 `매출이` 로 LIKE 를 걸어 "매출" 문서를 못 찾았다.

@pytest.mark.parametrize("question,expected", [
    ("구글드라이브에서 내가 작성한 신규 입사자 교안 자료 찾아줘", ["신규", "입사자", "교안"]),
    ("일본에서 매출이 왜 늘었는지 알려줘", ["일본", "매출", "늘었는지"]),
    ("보고서 파이프라인의 판정 계층은 뭐야", ["보고서", "파이프라인", "판정", "계층"]),
    # ⚠️ 원문 표기를 지켜야 한다 — 대문자가 뜻인 경우가 있다
    ("B2B 거래처의 첫 거래일", ["B2B", "거래처", "거래일"]),
    # 유형어만 남으면 키워드는 비어야 한다 (mimeType 필터가 대신한다)
    ("내 드라이브에 있는 사진 보여줘", []),
])
def test_query_keywords(question, expected):
    from app.core.query_keywords import extract
    drive_stop = {"드라이브", "구글드라이브", "구글", "사진", "작성한", "시트"}
    assert extract(question, extra_stop=drive_stop) == expected


def test_search_paths_share_one_extractor():
    """검색 경로마다 불용어를 다시 만들면 한 곳만 고쳐진다 — 실제로 그래서 두 곳이 깨졌다."""
    import inspect

    from app.agents import gws_agent
    from app.knowledge import wiki_search

    for mod in (gws_agent, wiki_search):
        src = inspect.getsource(mod)
        assert "query_keywords" in src, f"{mod.__name__} 이 공용 추출기를 쓰지 않는다"


def test_strip_particle_keeps_short_nouns():
    """⚠️ 조사를 떼다 낱말을 깎으면 안 된다 — '교안'의 '안', '자료'의 '료'."""
    from app.core.textmatch import strip_particle
    assert strip_particle("구글드라이브에서") == "구글드라이브"
    assert strip_particle("매출이") == "매출"
    assert strip_particle("교안") == "교안"
    assert strip_particle("자료") == "자료"
