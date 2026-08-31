"""PreToolUse 훅 — CLAUDE.md 의 ⛔ 규칙 중 **되돌릴 수 없는 것**을 실행 전에 막는다.

왜 훅인가:
    이 프로젝트의 원칙은 *"프롬프트는 확률이고 보증은 코드다"* 다 (FI 방어선·팀 값 교정·
    경로 마스킹이 전부 그렇게 만들어졌다). 그런데 정작 **작업 규칙 자체는 CLAUDE.md 1,275줄
    안의 프롬프트**로만 있었다 — 읽히면 지켜지고 안 읽히면 안 지켜진다.
    여기 있는 것들은 어겼을 때 **되돌릴 수 없거나 남에게 피해가 가는 것**들이라 코드로 옮겼다.

무엇을 막나:
    1. `scripts/notion_user_guide.py` 실행     — 가이드 페이지를 통째로 지운다 (clear 후 재생성)
    2. `pm2 delete/stop/kill skin1004-prod`   — 172.16.1.250 은 롤백 대비. 절대 금지
    3. `pm2 reload`                           — Windows fork 모드에서 고아 프로세스 (2026-07-06 장애)
    4. 작업트리 **전체**를 담거나 되돌리는 git — 남의 미커밋 변경을 쓸어가거나 지운다
    5. `.env` 편집                             — 배포 제외 대상이라 서버와 조용히 어긋난다
    6. 캐시 번호를 안 올린 프론트 자산 커밋    — 브라우저가 옛 파일을 계속 쓴다

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
import os
import re
import subprocess
import sys
from pathlib import Path


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


# ── 작업트리 전체를 건드리는 git ────────────────────────────────────────────────
#
# ⛔ 이건 `git commit -a` 와 **같은 사고를 다르게 적은 것**이다. 그쪽만 막아 두고
#    이쪽을 열어 두면 규칙이 아니라 철자 검사가 된다 (2026-08-31 실측: `commit -a` 는
#    막혔지만 `add -A`·`add .`·`add -u`·`reset --hard`·`checkout -- .`·`restore .`·
#    `clean -fd` 가 전부 통과했다).
#
# ⚠️ 위쪽 넷은 남의 변경을 **내 커밋에 섞고**, 아래쪽 넷은 아예 **지운다.**
#    지운 쪽이 더 나쁘다 — 다른 세션은 자기 파일이 사라진 줄도 모르고 계속 편집한다.

#: 경로를 지정하지 않은 것과 같은 뜻인 pathspec.
_WHOLE_TREE = {".", "./", ":/", "*", ":/*", "-A", "--all"}


def _tokens(segment: str) -> list[str]:
    """명령 조각을 토큰으로. ⚠️ 따옴표 안의 공백까지 정확히 다루지는 않는다 —
    그때는 경로가 있는 것처럼 보여 **통과**한다(안전한 쪽)."""
    return segment.strip().split()


def _git_calls(command: str) -> list[tuple[str, list[str], list[str]]]:
    """명령에서 git 호출을 뽑아 (하위명령, 플래그, 경로) 로 돌려준다."""
    calls: list[tuple[str, list[str], list[str]]] = []
    for segment in re.split(r"&&|\|\||[;|\n]", command):
        toks = _tokens(segment)
        # `VAR=x git ...` 같은 환경변수 접두를 걷어낸다
        while toks and "=" in toks[0] and not toks[0].startswith("-"):
            toks.pop(0)
        if not toks or os.path.basename(toks[0]).lower() not in ("git", "git.exe"):
            continue
        rest = toks[1:]
        # `git -C <경로> ...` 같은 전역 옵션 건너뛰기
        while rest and rest[0].startswith("-"):
            if rest[0] in ("-C", "-c", "--git-dir", "--work-tree"):
                rest = rest[2:]
            else:
                rest = rest[1:]
        if not rest:
            continue
        sub, args = rest[0], rest[1:]
        flags = [a for a in args if a.startswith("-") and a != "--"]
        paths = [a for a in args if not a.startswith("-")]
        if "--" in args:
            paths = args[args.index("--") + 1:]
        calls.append((sub, flags, paths))
    return calls


def _has_flag(flags: list[str], short: str, *longs: str) -> bool:
    for f in flags:
        if f in longs:
            return True
        if f.startswith("-") and not f.startswith("--") and short in f[1:]:
            return True
    return False


def _scoped(paths: list[str]) -> bool:
    """`.` 같은 전체 지정이 아닌 **실제 경로**가 하나라도 있는가."""
    return any(p not in _WHOLE_TREE for p in paths)


_SWEEP_TAIL = (
    "이 작업트리는 다른 세션과 함께 씁니다. 내 것만 경로로 지목하세요 — "
    "`git add <경로>` · `git restore <경로>` · `git stash push -- <경로>`."
)


def check_worktree_wide_git(command: str) -> str | None:
    """작업트리 전체를 담거나 되돌리는 git 명령이면 이유를, 아니면 None."""

    for sub, flags, paths in _git_calls(command):
        if sub == "add":
            wide = _has_flag(flags, "A", "--all") or _has_flag(flags, "u", "--update")
            if (wide or any(p in _WHOLE_TREE for p in paths)) and not _scoped(paths):
                return ("`git add` 로 작업트리 전체를 담으려 합니다 — 남의 미완성 파일이 "
                        "내 커밋에 섞입니다 (`git commit -a` 와 같은 사고입니다). " + _SWEEP_TAIL)

        elif sub == "reset" and _has_flag(flags, "", "--hard"):
            return ("`git reset --hard` 는 **다른 세션이 편집 중인 파일까지** 되돌립니다 — "
                    "그쪽은 사라진 줄도 모르고 계속 씁니다. 내 파일만 되돌리려면 "
                    "`git checkout -- <경로>` 를 쓰세요.")

        elif sub in ("checkout", "restore") and paths and not _scoped(paths):
            if sub == "restore" and _has_flag(flags, "S", "--staged"):
                continue  # 스테이징 해제는 파일을 지우지 않는다
            return (f"`git {sub}` 로 작업트리 전체를 되돌리려 합니다 — 다른 세션의 "
                    "미커밋 변경이 함께 지워집니다. 경로를 지목하세요.")

        elif sub == "clean" and _has_flag(flags, "f", "--force") and not _scoped(paths):
            if _has_flag(flags, "n", "--dry-run"):
                continue
            return ("`git clean -f` 는 추적되지 않는 파일을 **지웁니다** — 다른 세션이 "
                    "아직 add 하지 않은 새 파일이 사라집니다. `-n` 으로 먼저 확인하고 "
                    "지울 경로를 지목하세요.")

        elif sub == "stash":
            # ⚠️ `push`/`save` 는 **하위명령이지 경로가 아니다.** 경로로 세면
            #    `git stash push` 가 "경로를 지목했다" 로 읽혀 그냥 통과한다.
            rest = list(paths)
            head = rest[0] if rest else ""
            if head == "save":
                # ⚠️ `stash save` 뒤는 **메시지**다 — 경로로 세면 "지목했다" 가 된다.
                rest = []
            elif head == "push":
                rest = rest[1:]
            elif head:
                continue  # list/show/pop/apply/drop 등은 담는 동작이 아니다
            if _scoped(rest):
                continue
            return ("`git stash` 는 작업트리 전체를 치웁니다 — 다른 세션의 편집 중인 "
                    "파일이 눈앞에서 사라집니다. `git stash push -- <경로>` 로 "
                    "내 것만 치우세요.")

    return None


# ── 캐시 번호를 안 올린 프론트 자산 커밋 ────────────────────────────────────────
#
# ⛔ 2026-08-31 실측: 다른 세션이 09:24 에 `chat.js?v=270 → 271` 을 올렸고, 몇 시간 뒤
#    내 차트 수정도 **같은 271** 로 커밋됐다. 이미 271 을 받아 둔 브라우저는 뒤 수정을
#    **영영 안 받는다** — 서버는 새 파일을 주고 배포도 성공이라 아무도 모른다.
#    최근 chat.js 커밋 18건 중 5건이 이렇게 번호를 나눠 쓰고 있었다.
#
# ⚠️ 감시는 `chat.html` 하나만 본다 (CLAUDE.md 가 세는 그 번호다). `login.html`·
#    `eval_review.html`·`coa_finder.html` 은 각자 번호를 따로 쓰므로 여기서 강제하면
#    안 건드린 화면까지 올리게 돼 오탐이 된다 — 대신 메시지로 알린다.

_CACHE_HTML = "app/frontend/chat.html"
_ASSET_VER = re.compile(r"([A-Za-z0-9_.-]+\.(?:js|css))\?v=(\d+)")
_GIT_TIMEOUT = 4.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(*args: str) -> str | None:
    """git 출력을 돌려준다. 실패하면 None — **훅은 모르면 통과시킨다.**"""
    try:
        proc = subprocess.run(["git", *args], cwd=str(_repo_root()),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=_GIT_TIMEOUT)
    except Exception:  # noqa: BLE001 - git 이 없거나 느리면 통과
        return None
    return proc.stdout if proc.returncode == 0 else None


def stale_cache_versions(staged: list[str], head_html: str,
                         index_html: str) -> list[str]:
    """커밋될 자산 중 **번호가 그대로인** 것들. 순수 함수라 테스트가 쉽다."""
    head = {m.group(1): m.group(2) for m in _ASSET_VER.finditer(head_html)}
    index = {m.group(1): m.group(2) for m in _ASSET_VER.finditer(index_html)}
    stale = []
    for path in staged:
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name in index and name in head and index[name] == head[name]:
            stale.append(f"{name}?v={index[name]}")
    return stale


def check_cache_version(command: str) -> str | None:
    if not re.search(r"\bgit\s+commit\b", command):
        return None
    staged_out = _git("diff", "--cached", "--name-only")
    if not staged_out:
        return None  # 스테이징이 비었으면(메시지만 고치는 --amend 등) 볼 것이 없다
    staged = [line.strip() for line in staged_out.splitlines() if line.strip()]
    head_html = _git("show", f"HEAD:{_CACHE_HTML}")
    index_html = _git("show", f":{_CACHE_HTML}")
    if head_html is None or index_html is None:
        return None
    stale = stale_cache_versions(staged, head_html, index_html)
    if not stale:
        return None
    return (
        f"캐시 번호를 안 올리고 프론트 자산을 커밋하려 합니다: {', '.join(stale)} — "
        f"직전 커밋과 **같은 번호**입니다. 그 번호를 이미 받아 둔 브라우저는 이 수정을 "
        f"영영 안 받습니다 (배포는 성공하고 서버는 새 파일을 주므로 아무도 모릅니다). "
        f"{_CACHE_HTML} 의 `?v=` 를 올리고 CLAUDE.md '캐시 버전' 줄도 함께 고치세요. "
        f"login.html·eval_review.html·coa_finder.html 은 번호가 따로이니 "
        f"그 화면을 건드렸으면 그쪽도 올리세요."
    )


def check(tool_name: str, tool_input: dict) -> str | None:
    """막아야 하면 이유를, 통과면 None 을 돌려준다."""

    if tool_name in ("Bash", "PowerShell"):
        command = strip_noncode(str(tool_input.get("command") or ""))
        for pattern, reason in _BASH_RULES:
            if pattern.search(command):
                return reason
        wide = check_worktree_wide_git(command)
        if wide:
            return wide
        return check_cache_version(command)

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


def _block(reason: str) -> int:
    print(f"[차단] {reason}", file=sys.stderr)
    return 2


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
