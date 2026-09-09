# -*- coding: utf-8 -*-
"""답변을 사용자에게 흘리는 경로가 몇 개이고, 각각 무엇을 타야 하는가.

⛔ **왜 있나** (2026-09-09). 공시·검증 배선 회귀들이 전부 `src.count(...) == 2`
   로 **호출 횟수를 세고** 있었다. 그런데 답변 경로가 하나 늘자(`_fast_answer_stream`
   에 방어선을 붙였다) 규칙과 무관하게 **8건이 한꺼번에 깨졌다.**

   세는 단언의 문제는 깨지는 것이 아니라, 다음 사람이 **뜻을 안 보고 숫자만
   올린다**는 것이다. `== 2` 를 `== 3` 으로 고치면 통과하지만, 그때 새 경로가
   실제로 그 방어선을 타는지는 **아무도 확인하지 않는다.**

   규칙은 *"두 번 나온다"* 가 아니라 **"모든 답변 경로가 그것을 탄다"** 이다.

⚠️ 새 답변 경로를 만들면 `ANSWER_PATHS` 에 더해라. 그러면 이 파일을 쓰는 회귀가
   전부 그 경로까지 함께 검사한다 — 하나씩 고쳐 다닐 필요가 없다.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "app" / "agents" / "sql_agent.py"

#: 답변을 사용자에게 내보내는 함수들. 셋 다 같은 방어선을 타야 한다.
ANSWER_PATHS = (
    "format_answer",          # 비스트리밍
    "run_sql_agent_stream",   # 스트리밍 (프로덕션 기본)
    "_fast_answer_stream",    # 스트리밍 빠른 경로 (BQ_FAST_ANSWER=1)
)


def function_body(name: str, src: str | None = None) -> str:
    """`def name(` 부터 다음 최상위 `def` 직전까지."""
    src = src if src is not None else AGENT.read_text(encoding="utf-8")
    lines = src.splitlines()
    starts = [i for i, l in enumerate(lines)
              if l.startswith(("def " + name + "(", "async def " + name + "("))]
    if not starts:
        raise AssertionError("답변 경로 %r 를 찾지 못했다 — 이름이 바뀌었으면 "
                             "ANSWER_PATHS 도 함께 고쳐라" % name)
    s = starts[0]
    e = next((i for i in range(s + 1, len(lines))
              if lines[i].startswith(("def ", "async def "))), len(lines))
    return "\n".join(lines[s:e])


def assert_every_answer_path_has(needle: str, why: str = "") -> None:
    """모든 답변 경로가 `needle` 을 갖는지. 없으면 **어느 경로가** 빠졌는지 말한다.

    ⚠️ 횟수를 세지 않는다 — 경로가 늘어도 규칙은 그대로다.
    """
    src = AGENT.read_text(encoding="utf-8")
    missing = [p for p in ANSWER_PATHS if needle not in function_body(p, src)]
    assert not missing, (
        "%s 가 %s 경로에 없다%s — 경로마다 답이 갈린다"
        % (needle, ", ".join(missing), (" (" + why + ")") if why else ""))
