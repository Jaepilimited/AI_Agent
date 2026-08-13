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
