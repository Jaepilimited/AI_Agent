# -*- coding: utf-8 -*-
"""배포 직전 점검 — `/health` 로는 못 잡는 종류만 본다.

⛔ **왜 만들었나** (2026-09-03 실측 사고):

    같은 작업트리를 쓰는 다른 세션이 `orchestrator.py` 를 편집하는 중간에
    배포가 나갔다. 모듈 레벨 `def` 하나가 클래스 **한가운데**에 들어가면서
    뒤따르던 메서드 5개가 그 함수 안으로 빨려 들어갔고, `OrchestratorAgent`
    에서 `_handle_direct` 가 사라졌다.

    · 파일은 **문법적으로 멀쩡했다** (`ast.parse` 통과, import 도 된다)
    · 서비스는 `active`, `/health` 는 **200**
    · 배포 스크립트는 "기동 에러 0건" 이라고 찍었다
    · 그런데 direct 라우트는 전부 `AttributeError` 였다 — 13분간

    아무 신호도 없었다. 사람이 우연히 테스트를 돌려서 알았다.

⛔ **손으로 적은 필수 메서드 목록을 두지 않는다.** 그런 목록은 반드시 낡고,
   낡으면 아무것도 안 지킨다 (이 저장소가 여러 번 겪은 것). 대신 **코드가
   스스로 말하는 것**을 본다: `self._foo(...)` 라고 부르는데 그 클래스에
   `_foo` 가 없으면, 그건 곧 터질 `AttributeError` 다.

⚠️ **정상 작업을 막으면 그날로 꺼지고, 꺼진 게이트는 아무것도 안 지킨다.**
   그래서 확실할 때만 실패시킨다:
     · 바깥(다른 파일)에서 상속받은 클래스는 통째로 건너뛴다 — 부모의 메서드를
       알 수 없어 전부 오탐이 된다
     · `self.foo = ...` 로 붙이는 것, `setattr`, 클래스 변수도 정의로 친다
     · 호출(`self.foo(...)`)만 본다 — 속성 참조는 보지 않는다
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Set, Tuple

# 검사 대상 — 앱이 실제로 import 하는 것만.
# ⛔ `scripts/` 는 넣지 않는다. 일회용 조사 스크립트가 문법이 깨진 채 남아 있고
#    (앱은 import 하지 않는다), 그것 때문에 게이트가 매번 실패하면 곧 꺼진다.
SOURCE_ROOTS = ("app",)


def _iter_python(root: Path) -> List[Path]:
    out = []
    for base in SOURCE_ROOTS:
        for p in (root / base).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            out.append(p)
    return sorted(out)


def syntax_errors(root: Path) -> List[str]:
    """문법이 깨진 파일. 이건 배포하면 **기동 자체가 안 된다**."""
    bad = []
    for p in _iter_python(root):
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            bad.append(f"{p.relative_to(root).as_posix()}:{e.lineno} — {e.msg}")
        except Exception as e:                # noqa: BLE001
            bad.append(f"{p.relative_to(root).as_posix()} — 읽지 못함: {str(e)[:80]}")
    return bad


def _defined_names(cls: ast.ClassDef) -> Set[str]:
    """이 클래스가 가진 이름 — 메서드·클래스변수·`self.x = ...` 전부."""
    names: Set[str] = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    # 메서드 안에서 붙이는 것도 정의다 (`self.foo = ...`)
    for node in ast.walk(cls):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "self"):
                    names.add(t.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "setattr":
            # 무엇을 붙이는지 알 수 없다 — 이 클래스는 판단하지 않는다
            names.add("*")
    return names


def _called_on_self(cls: ast.ClassDef) -> Dict[str, int]:
    """`self.foo(...)` 로 **부른** 이름과 첫 줄번호."""
    calls: Dict[str, int] = {}
    for node in ast.walk(cls):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"):
            calls.setdefault(node.func.attr, node.lineno)
    return calls


def _has_external_base(cls: ast.ClassDef, local_classes: Set[str]) -> bool:
    """이 파일 밖에서 상속받았는가. 그러면 부모의 메서드를 알 수 없다."""
    for base in cls.bases:
        if isinstance(base, ast.Name):
            if base.id not in local_classes and base.id != "object":
                return True
        else:                                  # `module.Base` 같은 형태
            return True
    return False


def missing_self_methods(root: Path) -> List[str]:
    """`self._foo(...)` 를 부르는데 클래스에 `_foo` 가 없는 자리.

    ⛔ 오늘 사고가 정확히 이 모양이었다 — 문법은 멀쩡하고 `/health` 는 200 인데
       그 메서드를 부르는 순간 `AttributeError` 가 난다.
    """
    problems = []
    for p in _iter_python(root):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:                      # noqa: BLE001
            continue                           # 문법은 `syntax_errors` 가 본다
        local = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if _has_external_base(cls, local):
                continue
            defined = _defined_names(cls)
            if "*" in defined:                 # setattr 로 붙이는 클래스
                continue
            for name, line in _called_on_self(cls).items():
                if name not in defined:
                    problems.append(
                        f"{p.relative_to(root).as_posix()}:{line} — "
                        f"{cls.name}.{name}() 를 부르는데 정의가 없다")
    return problems


def run(root: Path = None) -> Tuple[bool, List[str]]:
    """(통과 여부, 문제 목록)."""
    root = Path(root or Path(__file__).resolve().parents[2])
    problems = syntax_errors(root) + missing_self_methods(root)
    return (not problems), problems


# ---------------------------------------------------------------------------
# 배포에 실리는데 git 이 모르는 소스
# ---------------------------------------------------------------------------
#
# ⛔ **왜 있나** (2026-09-08 실측): 이 저장소의 배포는 git 이 아니라 작업트리를
#    통째로 SFTP 전송한다. 그래서 **git 이 배포 관문이 아니다** — 커밋하지 않은
#    코드가 프로덕션에 있는 것이 사고가 아니라 구조적으로 정상 경로다.
#
#    그날 프로덕션은 HEAD 가 아니라 작업트리를 돌고 있었고, 그중 넷은
#    `git log --all` 이 한 번도 본 적 없는 파일이었다:
#
#        app/core/user_directory.py            330줄  로그인 경로
#        app/core/user_directory_migration.py  393줄  로그인 경로
#        app/core/calendar_stats.py            350줄
#        scripts/sync_entra_users.py            30줄  APP 크론 22:00 진입점
#
#    작업트리가 사라지면 돌아갈 길이 프로덕션 서버뿐이었다. 에러도 경고도
#    없었고, 사람이 해시를 대조해 보고서야 드러났다.
#
# ⛔ **막지 않는다. 적기만 한다.** 더티 트리를 막는 관문을 만들면 그날로 꺼진다 —
#    이 트리는 세션 서넛이 상시 공유해서 깨끗한 순간이 사실상 없고, 매번 걸리는
#    관문은 곧 우회된다 (CLAUDE.md 가 훅에 대해 적어 둔 그대로). 드물게 떠야
#    읽힌다.
#
# ⛔ **전송 목록에서 판정한다.** 경로 규칙을 여기 따로 적으면 `EXCLUDE_PATHS` 가
#    바뀔 때 경고만 조용히 낡는다 — `knowledge_map` 이 이름으로 걸려
#    `app/knowledge_map/` 이 통째로 빠졌던 그 자리다. 그래서 호출부가 `collect()`
#    결과를 그대로 넘기고, 여기서는 그 안에서만 고른다.
#
# ⚠️ `scripts/_*` 는 뺀다 — 밑줄이 일회성 관례이고 CLAUDE.md 도 "scripts/ 일회성
#    파일" 로 부른다. 46개가 상시 떠 있으면 그 경고는 태어나자마자 죽는다.
#    ⚠️ 이건 의도한 맞바꿈이다: 그것들도 전송되므로 사라지면 함께 사라진다.
#    잃어도 되는 것이라는 판단이 전제다.
#
# ⚠️ `.gitignore` 로 가려진 것은 보지 않는다 — 무시는 **결정**이지 실수가 아니다.
#    (`analyze_warns.py` 가 `.gitignore:139` 에 이름째 적혀 있는 것이 그 예다.)
#    추적 안 됨은 "아직 아무도 판단하지 않았다" 이고, 그것만이 알릴 값이 있다.
#
# ⚠️ `tests/` 는 볼 필요가 없다 — `EXCLUDE_DIRS` 에 있어 애초에 전송되지 않는다.
#    전송 목록에서 고르므로 규칙을 따로 적지 않아도 자연히 빠진다.

# ⛔ **자가 점검(매일)으로 만들지 마라 — 서버에서는 돌 수 없다.** 신규 서버는
#    git 저장소가 아니라 SFTP 전송본이라 `git status` 가 성립하지 않는다
#    (CLAUDE.md: "git pull 로 갱신되지 않는다"). 이 경고는 **git 이 있는 쪽**,
#    즉 배포하는 사람의 화면에서만 뜻이 있다. 그래서 배포 시점에만 뜬다.

WATCHED_ROOTS = ("app", "scripts")
_SCRATCH_PREFIX = "_"


def _rel(root: Path, path) -> str:
    """저장소 상대경로를 슬래시 표기로."""
    p = Path(path)
    try:
        p = p.resolve().relative_to(Path(root).resolve())
    except ValueError:
        p = p if not p.is_absolute() else Path(p.name)
    return str(p).replace("\\", "/")


def _load_bearing(rel: str) -> bool:
    """잃으면 아픈 소스인가 — 감시 대상 판정 한 곳."""
    if not rel.endswith(".py"):
        return False
    parts = rel.split("/")
    if len(parts) < 2 or parts[0] not in WATCHED_ROOTS:
        return False
    if parts[0] == "scripts" and parts[-1].startswith(_SCRATCH_PREFIX):
        return False
    return True


def untracked_paths(root: Path) -> Set[str]:
    """git 이 모르는 파일의 상대경로.

    ⚠️ `-z` 로 받는다 — 기본 출력은 비ASCII 경로를 따옴표로 감싸고 이스케이프해서
       한글 파일명이 그대로 안 온다.
    """
    import subprocess

    out = subprocess.run(
        ["git", "status", "--porcelain", "-uall", "-z"],
        cwd=str(root), capture_output=True, timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError((out.stderr or b"").decode("utf-8", "replace")[:200] or "git status 실패")
    found: Set[str] = set()
    for chunk in out.stdout.decode("utf-8", "replace").split("\0"):
        if chunk.startswith("?? "):
            found.add(chunk[3:].strip().rstrip("/"))
    return found


def untracked_in_payload(root: Path, payload) -> List[Tuple[str, int]]:
    """전송 목록 중 git 이 모르는 소스. `(상대경로, 줄수)` 를 큰 것부터.

    `payload` 는 배포 스크립트의 `collect()` 결과를 그대로 받는다 —
    경고와 실제 전송이 같은 것을 보게 하려는 것이 이 인자의 전부다.
    """
    root = Path(root)
    unknown = untracked_paths(root)
    rows: List[Tuple[str, int]] = []
    for path in payload:
        rel = _rel(root, path)
        if rel not in unknown or not _load_bearing(rel):
            continue
        try:
            with open(root / rel, "rb") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            lines = 0
        rows.append((rel, lines))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


def format_untracked_notice(rows: List[Tuple[str, int]]) -> List[str]:
    """사람이 읽을 줄들. 없으면 빈 목록 — 조용할 때는 아무 말도 하지 않는다."""
    if not rows:
        return []
    total = sum(n for _, n in rows)
    out = [f"  [보존] !! git 이 모르는 소스 {len(rows)}개({total}줄)가 이번 전송에 포함됩니다"]
    for rel, n in rows[:10]:
        out.append(f"           {rel}  {n}줄")
    if len(rows) > 10:
        out.append(f"           ... 외 {len(rows) - 10}개")
    out.append("         작업트리가 사라지면 이 코드는 프로덕션에만 남습니다. 커밋을 권합니다")
    out.append("         (막지 않습니다 - 전송은 그대로 진행됩니다)")
    return out
