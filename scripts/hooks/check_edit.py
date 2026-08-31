"""PostToolUse 훅 — 파일을 고친 **직후** 그 파일에 걸린 가드를 돌린다.

왜 여기인가:
    `app/core/static_checks.py` 는 이미 두 곳이 부른다 — `tests/test_no_silent_failures.py`
    (개발)와 자가 점검(서버, 매일 07:30). 빠져 있던 세 번째 호출자가 **편집 시점**이다.
    지금까지는 고치고 → 테스트를 돌려야 알았고, 안 돌리면 배포 후에 알았다.
    실제로 하루에 금지 낱말 가드에 세 번 걸렸다 (`localStorage`·`claude`·자기 assert).
    각각은 편집 직후에 알았으면 한 번에 끝날 일이었다.

⛔ **규칙을 여기에 새로 쓰지 마라.** 판정은 전부 기존 소스에서 가져온다:
     · `app/core/static_checks.py` 의 순수 함수들
     · 가드 테스트가 이미 정한 금지 낱말 (`tests/test_briefing.py::test_llm_is_not_used_anywhere`,
       `tests/frontend/test_personal_briefing_welcome.py`, `tests/test_inventory.py`)
   같은 규칙을 두 번 구현하면 한쪽만 고쳐지고, 그때 나는 것은 에러가 아니라 **불일치**다.

⚠️ 느리면 꺼진다. 파일 종류로 먼저 걸러 **관련 검사만** 돌린다 (전체 0.2초 미만).
   DB 를 보는 검사(`qdrant_*`·`notion_*`·`team_link_coverage`)는 절대 부르지 않는다.

stdin:  {"tool_name": "Edit", "tool_input": {"file_path": "..."}, ...}
경고:   exit 2 + stderr (편집은 이미 끝났으므로 되돌리는 게 아니라 Claude 에게 알린다)
통과:   exit 0
"""

from __future__ import annotations

import io
import json
import os
import re
import sys


def _force_utf8_stderr() -> None:
    """⚠️ 이 훅은 한국어를 stderr 로 낸다. 윈도우 콘솔 기본이 cp949 라 그대로 쓰면
    `UnicodeEncodeError` 로 **훅이 죽는다** — 그러면 exit 1 이라 차단이 조용히 풀려
    막아야 할 명령이 그냥 통과한다.

    ⛔ import 시점에 `sys.stderr` 를 바꾸지 마라 — 이 모듈을 import 하는 쪽
       (pytest 등)의 캡처를 빼앗아 'lost sys.stderr' 로 죽는다. 실행할 때만 바꾼다.
    """
    try:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                      errors="replace")
    except Exception:  # noqa: BLE001 - buffer 가 없는 환경이면 그대로 쓴다
        pass

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 파일별 금지 낱말. 출처는 각 가드 테스트이며 **여기가 원본이 아니다**.
#: (상대경로 접미사, 낱말들, 왜, 예외처리 함수)
_FORBIDDEN: list[tuple[str, tuple[str, ...], str]] = [
    (
        "app/core/briefing.py",
        ("generate", "llm", "gemini", "claude"),
        "브리핑은 숫자도 문장도 코드가 만든다는 보증이라 파일 전체를 훑는 가드가 있습니다 "
        "(tests/test_briefing.py::test_llm_is_not_used_anywhere). 주석에 써도 걸립니다 — "
        "규칙 문서를 인용할 땐 다른 이름으로 부르세요.",
    ),
    (
        "app/frontend/personal-briefing.js",
        ("localStorage", "sessionStorage"),
        "구글 데이터를 다루는 파일이라 브라우저 영속 저장 경로를 아예 두지 않는 것이 규칙입니다 "
        "(tests/frontend/test_personal_briefing_welcome.py). 상태는 메모리에만 두세요.",
    ),
]


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _forbidden_words(path: str, rel: str) -> list[str]:
    out = []
    for suffix, words, why in _FORBIDDEN:
        if not rel.endswith(suffix):
            continue
        source = _read(path)
        # test_briefing 이 쓰는 것과 같은 예외 (한국어 문장 속 'LLM 은/이' 는 허용)
        haystack = source.lower().replace("llm 은", "").replace("llm 이", "")
        hit = [w for w in words if w.lower() in haystack]
        if hit:
            out.append(f"{rel} 에 금지 낱말 {hit} 이(가) 있습니다. {why}")
    return out


#: 주석 줄과 백틱으로 감싼 인용. 이 저장소는 규칙을 백틱으로 인용하는 문체를 쓴다.
_COMMENT_LINE = re.compile(r"^\s*#.*$", re.MULTILINE)
_BACKTICKED = re.compile(r"`[^`\n]*`")


def code_only(source: str) -> str:
    """설명을 걷어내고 **돌아가는 코드만** 남긴다.

    ⛔ 이 훅의 오탐은 전부 뿌리가 하나다 — **규칙을 설명하는 글을 규칙 위반으로 읽는 것**.
       붙인 첫날 세 번 걸렸다: 커밋 메시지가 명령으로, 테스트 픽스처가 코드로, 그리고
       마지막엔 **이 검사기 자신의 설명문**이 위반으로 읽혔다.
       패턴 검사는 글자를 볼 뿐 코드를 보지 못한다는 것을 전제로 짜야 한다.
    """
    return _BACKTICKED.sub("", _COMMENT_LINE.sub("", source))


def _executor_trap(path: str, rel: str) -> list[str]:
    """`with ThreadPoolExecutor` + `result(timeout=)` — 타임아웃이 무력화된다."""

    # ⛔ 테스트는 **반례를 일부러 담는다** — 가드 테스트의 픽스처가 곧 나쁜 패턴이다.
    #    검사 대상은 돌아가는 코드다.
    if not rel.endswith(".py") or rel.startswith("tests/"):
        return []
    source = code_only(_read(path))
    if "ThreadPoolExecutor" not in source or "timeout=" not in source:
        return []
    # ⛔ **파일 전체에서 둘을 찾으면 안 된다** (2026-08-31 실측). `sql_agent.py` 는
    #    `with` 블록 하나(타임아웃 없음)와, **직접 만든 pool** 을 쓰는 올바른
    #    `.result(timeout=)` 다섯 곳을 함께 갖고 있다. 둘을 엮으면 관계없는 코드가
    #    위반이 되고, 그러면 이 프로젝트에서 가장 큰 파일을 **편집할 때마다 막힌다.**
    #    방해물이 된 훅은 그날로 꺼지고, 꺼진 훅은 아무것도 안 지킨다.
    #    → 같은 `with` 블록 **안**에 있을 때만 잡는다 (들여쓰기로 블록 끝을 판정).
    lines = source.splitlines()
    for i, line in enumerate(lines):
        opener = re.search(r"^(\s*)with\s+(concurrent\.futures\.)?ThreadPoolExecutor", line)
        if not opener:
            continue
        indent = len(opener.group(1))
        for body in lines[i + 1:]:
            if body.strip() and (len(body) - len(body.lstrip())) <= indent:
                break  # 들여쓰기가 돌아왔다 = 블록 끝
            if re.search(r"\.result\(\s*timeout\s*=", body):
                return [
                    f"{rel}: `with ThreadPoolExecutor` + `.result(timeout=)` 는 블록을 "
                    "빠져나갈 때 shutdown(wait=True) 가 걸려 **타임아웃이 무의미해집니다** "
                    "(CLAUDE.md 코드 규칙). pool 을 직접 만들고 "
                    "`finally: pool.shutdown(wait=False)` 를 쓰세요."
                ]
    return []


def _static(rel: str) -> list[str]:
    """기존 정적 검사 중 이 파일과 관련 있고 DB 를 안 보는 것만 돌린다."""

    try:
        sys.path.insert(0, REPO)
        from app.core import static_checks
    except Exception:
        return []  # 검사를 못 불러오는 것이 작업을 막을 이유는 되지 않는다

    wanted = []
    if rel.endswith((".css", ".js", ".html")):
        wanted += [static_checks.undefined_css_vars, static_checks.asset_stamping_wired]
    if rel.endswith(".py"):
        wanted.append(static_checks.stray_control_chars)
    if "prompts/" in rel:
        wanted += [static_checks.fi_prompt_masking,
                   static_checks.prompt_no_handwritten_value_lists]
    if rel.endswith(("chat.js", "orchestrator.py")):
        wanted.append(static_checks.at_source_parity)

    out = []
    for check in wanted:
        try:
            ok, message = check()
        except Exception:
            continue  # 검사 하나가 죽어도 나머지는 돈다
        if not ok:
            out.append(f"[{check.__name__}] {message}")
    return out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if str(payload.get("tool_name") or "") not in ("Edit", "Write", "NotebookEdit"):
        return 0

    path = str((payload.get("tool_input") or {}).get("file_path") or "")
    if not path or not os.path.exists(path):
        return 0

    try:
        rel = os.path.relpath(path, REPO).replace("\\", "/")
    except ValueError:
        return 0
    if rel.startswith(".."):
        return 0  # 저장소 밖 파일은 우리 규칙 대상이 아니다

    try:
        problems = _forbidden_words(path, rel) + _executor_trap(path, rel) + _static(rel)
    except Exception as exc:  # noqa: BLE001 - 훅이 터져서 작업을 막으면 안 된다
        print(f"[check_edit] 검사 실패, 통과시킴: {type(exc).__name__}", file=sys.stderr)
        return 0

    if not problems:
        return 0

    print("가드 검사에 걸렸습니다 — 배포 전에 고치세요:", file=sys.stderr)
    for line in problems:
        print(f"  · {line}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    _force_utf8_stderr()
    sys.exit(main())
