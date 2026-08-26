"""PreToolUse 훅 — CLAUDE.md 의 ⛔ 규칙 중 **되돌릴 수 없는 것**을 실행 전에 막는다.

왜 훅인가:
    이 프로젝트의 원칙은 *"프롬프트는 확률이고 보증은 코드다"* 다 (FI 방어선·팀 값 교정·
    경로 마스킹이 전부 그렇게 만들어졌다). 그런데 정작 **작업 규칙 자체는 CLAUDE.md 1,275줄
    안의 프롬프트**로만 있었다 — 읽히면 지켜지고 안 읽히면 안 지켜진다.
    여기 있는 넷은 어겼을 때 **되돌릴 수 없거나 남에게 피해가 가는 것**들이라 코드로 옮겼다.

무엇을 막나:
    1. `scripts/notion_user_guide.py` 실행     — 가이드 페이지를 통째로 지운다 (clear 후 재생성)
    2. `pm2 delete/stop/kill skin1004-prod`   — 172.16.1.250 은 롤백 대비. 절대 금지
    3. `pm2 reload`                           — Windows fork 모드에서 고아 프로세스 (2026-07-06 장애)
    4. `git commit -a`                        — 작업트리를 다른 세션과 공유한다. 남의 미완성이 섞인다
    5. `.env` 편집                             — 배포 제외 대상이라 서버와 조용히 어긋난다

⛔ 여기에 "위험해 보이는 것" 을 쌓지 마라. 막을 것의 기준은 위험도가 아니라
   **되돌릴 수 있는가** 다. 되돌릴 수 있는 실수는 사람이 고치면 되고, 목록이 길어지면
   훅이 방해물이 되어 결국 통째로 꺼진다 (알림을 늘리면 아무도 안 읽게 되는 것과 같다).

stdin:  {"tool_name": "Bash", "tool_input": {"command": "..."}, ...}
차단:   exit 2 + stderr (Claude 에게 이유가 전달된다)
통과:   exit 0
⚠️ 판단이 안 서면 **통과시킨다.** 훅이 오작동해서 정상 작업을 막으면 그 다음부터 꺼진다.
"""

from __future__ import annotations

import io
import json
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

#: (정규식, 사람이 읽을 이유). 명령 문자열 전체에 대해 검사한다.
_BASH_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        # ⚠️ 이름만 나온 것은 언급이다 — **인터프리터로 부를 때만** 실행으로 본다.
        re.compile(r"\b(python3?|py|uv|bash|sh)\b[^|;&\n]*\bnotion_user_guide\.py"),
        "scripts/notion_user_guide.py 는 노션 가이드 페이지를 통째로 지웁니다 "
        "(clear 후 재생성인데 실제 페이지는 손으로 많이 편집됐습니다). "
        "가이드는 바꿀 구간만 Notion MCP update-page 의 old_str/new_str 로 고치세요.",
    ),
    (
        # pm2 <동사> ... skin1004-prod  /  pm2 <동사> ... <id>  가 아니라 이름을 지목한 것만 본다
        re.compile(r"\bpm2\s+(delete|del|stop|kill)\b[^|;&]*\bskin1004-prod\b"),
        "172.16.1.250 의 skin1004-prod 는 롤백 대비로 유지 중입니다 — kill/stop/delete 금지 "
        "(CLAUDE.md 배포 규칙). 반영은 `pm2 restart skin1004-prod` 입니다.",
    ),
    (
        re.compile(r"\bpm2\s+reload\b"),
        "Windows fork 모드에서 `pm2 reload` 는 고아 프로세스를 만듭니다 "
        "(2026-07-06 실제 장애). 반드시 `pm2 restart` 를 쓰세요.",
    ),
    (
        # `git commit -a` / `--all`. `-m` 뒤 메시지 안의 문자열은 보지 않도록 옵션 자리만 본다.
        re.compile(r"\bgit\s+commit\b(?=[^|;&]*\s(-[a-zA-Z]*a[a-zA-Z]*|--all)\b)"),
        "이 작업트리는 다른 세션과 공유합니다 — `git commit -a` 는 남의 미완성 파일까지 "
        "쓸어 담습니다. 내 파일만 `git add <경로>` 로 명시해 스테이징하세요.",
    ),
]

#: 편집을 막을 경로. `.env.example` 류는 템플릿이라 통과시킨다.
_ENV_PATH = re.compile(r"(^|[\\/])\.env(\.[a-z]+)?$", re.IGNORECASE)
_ENV_ALLOWED = re.compile(r"\.env\.(example|sample|template)$", re.IGNORECASE)


_HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1.*?^\s*\2\s*$",
    re.DOTALL | re.MULTILINE,
)


def strip_noncode(command: str) -> str:
    """실행되지 않는 부분(heredoc 본문)을 지운다.

    ⛔ 명령 문자열 전체에 정규식을 걸면 **글이 명령으로 읽힌다.** 이 훅을 붙인 첫날
       바로 걸렸다 — 커밋 메시지 heredoc 안에 규칙을 설명하며 스크립트 이름을 적었더니
       "그 스크립트를 실행하려 한다" 로 보고 막았다. 정상 작업을 막는 훅은 그날로 꺼지고,
       꺼진 훅은 아무것도 지키지 않는다.
    """
    return _HEREDOC.sub("\n", command)


def _block(reason: str) -> int:
    print(f"[차단] {reason}", file=sys.stderr)
    return 2


def check(tool_name: str, tool_input: dict) -> str | None:
    """막아야 하면 이유를, 통과면 None 을 돌려준다."""

    if tool_name in ("Bash", "PowerShell"):
        command = strip_noncode(str(tool_input.get("command") or ""))
        for pattern, reason in _BASH_RULES:
            if pattern.search(command):
                return reason
        return None

    if tool_name in ("Edit", "Write", "NotebookEdit"):
        path = str(tool_input.get("file_path") or "")
        if _ENV_PATH.search(path) and not _ENV_ALLOWED.search(path):
            return (
                f"{path} 는 배포에서 제외되는 파일입니다 — 코드만 올리면 서버 값이 그대로라 "
                "조용히 어긋납니다 (모델명이 .env 때문에 안 바뀌던 사고). "
                "서버에서 직접 고치고 재기동하세요."
            )
        return None

    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # 입력을 못 읽으면 통과 — 훅이 작업을 막는 쪽으로 실패하면 안 된다

    try:
        reason = check(str(payload.get("tool_name") or ""),
                       payload.get("tool_input") or {})
    except Exception as exc:  # noqa: BLE001 - 훅은 절대 터져서 막으면 안 된다
        print(f"[guard_destructive] 검사 실패, 통과시킴: {type(exc).__name__}",
              file=sys.stderr)
        return 0

    return _block(reason) if reason else 0


if __name__ == "__main__":
    _force_utf8_stderr()
    sys.exit(main())
