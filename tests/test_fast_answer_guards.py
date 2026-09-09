# -*- coding: utf-8 -*-
"""빠른 경로도 **같은 수치 방어선**을 탄다 — 2026-09-09.

⛔ **왜 생겼나.** `BQ_FAST_ANSWER=1` 을 프로덕션에 켰더니(측정 −38%/−22%) 빠른
   경로 `_fast_answer_stream` 이 방어선을 거의 안 타고 있었다. 실측 대조:

       방어선                  빠른경로   정상경로
       _amount_note              ❌       ✅
       _number_check_notice      ❌       ✅
       logistics_quality         ❌       ✅
       krw_notice                ❌       ✅
       _mask_stream              ✅       ✅

⛔ **표가 아니라 문장이 위험하다.** 빠른 경로도 인사이트는 LLM 이 쓴다. 그리고
   CLAUDE.md 가 기록한 실제 사고가 정확히 그 자리다 — 2026-08-31, 표는
   8,403만원으로 맞게 나왔는데 요약 문장만 *"약 828.7억원"*(1,000배)이었고
   사용자가 *"표와 시각화는 값이 맞는데 이부분이 안맞음"* 이라고 제보했다.
   빠진 넷이 그때 붙인 것들이다.

⚠️ 실제로 켠 12분 동안 뽑은 답변이 그 모양이었다 — 표에 비중 컬럼이 없으니
   LLM 이 문장에서 `약 47.3%` 를 **직접 계산**했다. 이번엔 맞았지만, 맞았다는
   것을 사람이 계산기로 확인해야 알 수 있었다. 그게 방어선이 하던 일이다.

⚠️ 목록을 손으로 적되 **낡지 않게 막는다**: 아래 이름이 정상 경로에도 없으면
   그것부터 실패한다 (이름이 바뀌었거나 방어선이 사라졌다는 뜻이므로).
"""
import re
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parent.parent / "app" / "agents"
       / "sql_agent.py").read_text(encoding="utf-8")
LINES = SRC.splitlines()

#: 두 경로가 **함께** 타야 하는 것. 값은 왜 필요한지다 — 실패 메시지에 그대로 나간다.
REQUIRED = {
    "_amount_note":
        "억/만 환산을 코드가 미리 준다. 없으면 LLM 이 나눗셈을 직접 한다 "
        "(2026-08-31 1,000배 사고)",
    "_number_check_notice":
        "문장 속 수치를 조회 결과와 대조하고 안 맞으면 원본 표를 붙인다 "
        "(그 사고 때 미검증률 91.7%)",
    "logistics_quality":
        "물류 수량 이상치(3,492배) 공시 — 표보다 먼저 말해야 한다",
    "krw_notice":
        "한화 환산 공시 — 물류비를 매출로 답한 붐따 #162",
    "_mask_stream":
        "내부 테이블 경로 마스킹",
}


def _span(name: str) -> tuple[int, int]:
    start = next(i for i, l in enumerate(LINES)
                 if re.match(rf"^(async )?def {re.escape(name)}\(", l))
    end = len(LINES)
    for i in range(start + 1, len(LINES)):
        if re.match(r"^(async )?def ", LINES[i]):
            end = i
            break
    return start, end


def _calls(name: str) -> set:
    """그 함수 본문에서 실제로 불리는 이름들. 주석은 세지 않는다."""
    start, end = _span(name)
    body = [l for l in LINES[start:end] if not l.strip().startswith("#")]
    return {g for g in REQUIRED if any(g in l for l in body)}


@pytest.mark.parametrize("guard", sorted(REQUIRED))
def test_the_normal_path_still_has_it(guard):
    """⚠️ 목록이 낡지 않게 막는다 — 정상 경로에도 없으면 이름이 바뀐 것이다.

    이게 없으면 방어선이 통째로 사라진 뒤에도 아래 검사가 **양쪽 다 없음**으로
    조용히 통과할 수 있다.
    """
    assert guard in _calls("run_sql_agent_stream"), (
        f"정상 경로에서 {guard} 가 사라졌다 — 이름이 바뀌었거나 방어선이 없어졌다. "
        f"({REQUIRED[guard]})")


@pytest.mark.parametrize("guard", sorted(REQUIRED))
def test_the_fast_path_has_it_too(guard):
    """⛔ 여기가 이 파일의 전부다. 빠른 경로가 같은 방어선을 타야 한다."""
    assert guard in _calls("_fast_answer_stream"), (
        f"빠른 경로(_fast_answer_stream)가 {guard} 를 타지 않는다. "
        f"{REQUIRED[guard]}")


def test_both_paths_are_compared_not_assumed():
    """⚠️ 두 함수가 실제로 갈라져 있는지 확인한다 — 한쪽이 다른 쪽을 통째로
    감싸고 있으면 위 비교가 무의미하다."""
    fs, fe = _span("_fast_answer_stream")
    ss, se = _span("run_sql_agent_stream")
    assert fe <= ss or se <= fs, "두 함수 구간이 겹친다 — 비교가 성립하지 않는다"


def test_the_flag_still_gates_the_fast_path():
    """⚠️ 분기가 사라지면 이 파일이 지키는 것이 없어진다."""
    assert 'os.getenv("BQ_FAST_ANSWER")' in SRC, "빠른 경로 게이트가 사라졌다"
    ss, se = _span("run_sql_agent_stream")
    assert any("BQ_FAST_ANSWER" in l for l in LINES[ss:se]), \
        "게이트가 스트리밍 경로 밖으로 옮겨졌다 — 사용자가 지나는 길이 아니다"
