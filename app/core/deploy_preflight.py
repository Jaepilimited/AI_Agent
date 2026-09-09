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
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

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


# ---------------------------------------------------------------------------
# 풀리지 않는 import
# ---------------------------------------------------------------------------
#
# ⛔ **왜 있나** (2026-09-09 실측, 프로덕션 80초 정지):
#
#       09:03:36  한 세션이 sql_agent.py 에 import 를 넣었다
#                   from app.core.sales_outlook import (...)
#       09:04:0x  다른 세션의 배포 전송  ← 이 순간 sales_outlook.py 는 없었다
#       09:04~    ModuleNotFoundError 크래시 루프 · /health 000 · 재시작 11회
#       09:05:21  서버 파일 하나를 되돌려 복구
#       09:06:29  그 세션이 sales_outlook.py 를 만듦
#
#    import 를 먼저 넣고 3분 뒤 모듈을 만든 것이고, 전송이 그 사이에 떨어졌다.
#    이 저장소는 트리를 통째로 보내므로 **편집 중간 상태가 그대로 프로덕션에 간다.**
#
# ⛔ **`syntax_errors` 도 `missing_self_methods` 도 이걸 못 잡는다.** 문법은
#    멀쩡했고 클래스 구조도 온전했다. 그런데 배포에서 죽는 방식 중 가장 흔한
#    것이 이것이다 — 그리고 서버는 `/health 000` 으로만 말한다.
#
# ⛔ **이건 경고가 아니라 관문이다.** 미추적 소스 경고(아래)는 "잃을 수도 있다"
#    는 가능성이라 막지 않지만, 풀리지 않는 import 는 **확실히 죽는 조건**이다.
#    실행 없이 AST 로만 판정하므로 부작용도 비용도 없다.
#
# ⚠️ **좁게 본다 — 매번 걸리는 관문은 그날로 꺼진다.** 넷을 제외한다:
#      · `app.` 으로 시작하지 않는 import (외부 패키지는 여기서 판단하지 않는다)
#      · 모듈 최상위가 아닌 것 (함수·메서드 안의 지연 import 는 **의도된 것**이다.
#        이 저장소는 순환 참조와 기동 속도 때문에 실제로 많이 쓴다)
#      · `try/except ImportError` 로 감싼 것 (없을 수 있음을 이미 다루고 있다)
#      · `from X import *` 나 `__getattr__` 을 가진 모듈에서 가져오는 이름
#        (무엇이 나올지 정적으로 알 수 없다 — 모르면 통과시킨다)
#
# ⚠️ `app/**` 은 전부 전송된다(EXCLUDE_PATHS 의 `app/static/charts` 는 .py 가
#    아니다). 그래서 트리에서 판정해도 전송본과 같다 — 그 전제를 회귀가
#    `collect()` 에 직접 물어 지킨다.

_APP_PREFIX = "app."


def _module_file(root: Path, dotted: str) -> Path:
    """`app.core.x` → 실재하는 `app/core/x.py` 또는 `app/core/x/__init__.py`."""
    rel = dotted.replace(".", "/")
    for cand in (root / (rel + ".py"), root / rel / "__init__.py"):
        if cand.exists():
            return cand
    return None


def _toplevel_names(path: Path) -> Tuple[Set[str], bool]:
    """모듈이 밖으로 내놓는 최상위 이름들. 두 번째 값이 True 면 '알 수 없음'."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return set(), True                             # 못 읽으면 판단하지 않는다
    names: Set[str] = set()
    opaque = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    opaque = True                      # 무엇이 들어오는지 모른다
                else:
                    names.add(a.asname or a.name)
        elif isinstance(node, (ast.Try, ast.If)):
            opaque = True                              # 조건부 정의 — 모르면 통과
    if "__getattr__" in names:
        opaque = True
    return names, opaque


def _guarded_by_import_error(node: ast.AST, parents: dict) -> bool:
    """`try/except ImportError` 안에 있으면 없을 수 있음을 이미 다루는 코드다."""
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.Try):
            for h in cur.handlers:
                t = h.type
                cands = t.elts if isinstance(t, ast.Tuple) else ([t] if t else [])
                for c in cands:
                    nm = getattr(c, "id", None) or getattr(c, "attr", None)
                    if nm in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException"):
                        return True
        cur = parents.get(id(cur))
    return False


def unresolved_imports(root: Path) -> List[str]:
    """모듈 최상위에서 `app.*` 를 부르는데 그 모듈이나 이름이 없는 곳."""
    root = Path(root)
    problems: List[str] = []
    for path in _iter_python(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:                              # noqa: BLE001
            continue                                   # syntax_errors 가 이미 잡는다
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent
        rel = path.relative_to(root).as_posix()

        for node in tree.body + [n for t in tree.body if isinstance(t, (ast.Try, ast.If))
                                 for n in getattr(t, "body", [])]:
            mods = []
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.startswith(_APP_PREFIX) or node.module == "app":
                    mods.append((node.module, [a.name for a in node.names], node))
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith(_APP_PREFIX):
                        mods.append((a.name, [], node))
            for dotted, wanted, nd in mods:
                if _guarded_by_import_error(nd, parents):
                    continue
                target = _module_file(root, dotted)
                if target is None:
                    problems.append(
                        f"{rel}:{nd.lineno} — `{dotted}` 모듈이 없다 "
                        f"(import 만 있고 파일이 아직 없다)")
                    continue
                if not wanted:
                    continue
                have, opaque = _toplevel_names(target)
                if opaque:
                    continue                           # 모르면 통과시킨다
                # ⛔ `from app.core import product_lines` 는 패키지에서 **하위 모듈**을
                #    가져오는 것이다. `__init__.py` 의 이름만 보면 이게 전부 오탐이 된다
                #    — 실측으로 32건이 그렇게 걸렸다. 매번 걸리는 관문은 그날로 꺼진다.
                missing = [w for w in wanted
                           if w != "*" and w not in have
                           and _module_file(root, f"{dotted}.{w}") is None]
                if missing:
                    problems.append(
                        f"{rel}:{nd.lineno} — `{dotted}` 에 "
                        f"{', '.join('`%s`' % m for m in missing)} 가 없다")
    return problems


def run(root: Path = None) -> Tuple[bool, List[str]]:
    """(통과 여부, 문제 목록)."""
    root = Path(root or Path(__file__).resolve().parents[2])
    problems = syntax_errors(root) + unresolved_imports(root) + missing_self_methods(root)
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


def format_untracked_notice(rows: List[Tuple[str, int]], ages: dict = None) -> List[str]:
    """사람이 읽을 줄들. 없으면 빈 목록 — 조용할 때는 아무 말도 하지 않는다.

    ⛔ **`ages` 는 "방금 생겼다" 를 붙이려고 있다** (2026-09-09). 관문이 통과할
       때는 `[편집중]` 블록이 안 뜨는데, **가장 위험한 상태가 바로 "관문 통과 +
       편집 중"** 이다. 그날 09:15 이 그랬다 — 구문도 import 도 통과하고
       `/health` 도 200 인데 스위트가 76건 실패했고, 원인은 20초 전에 생긴
       `session_auth.py` 였다.
    ⚠️ **블록을 따로 만들지 않는다.** 이 목록이 이미 그 파일들을 보여주고 있고,
       빠진 것은 *언제* 뿐이다. 블록을 더하면 같은 파일을 두 번 적게 되고,
       두 번 적힌 것은 곧 한 번도 안 읽힌다.
    ⚠️ 여기에 `M` 은 넣지 않는다 — 오늘 상시 114~117개였고 사고를 낸 것은 없다.
       `??` 는 두 번 뜨고 두 번 다 사고였다. 신호 대 잡음이 다르다.
    """
    if not rows:
        return []
    ages = ages or {}
    total = sum(n for _, n in rows)
    out = [f"  [보존] !! git 이 모르는 소스 {len(rows)}개({total}줄)가 이번 전송에 포함됩니다"]
    fresh = False
    for rel, n in rows[:10]:
        age = ages.get(rel)
        if age is not None and age <= RECENT_EDIT_WINDOW_SECONDS:
            fresh = True
            out.append(f"           {rel}  {n}줄  <- {_ago(age)}에 바뀜")
        else:
            out.append(f"           {rel}  {n}줄")
    if len(rows) > 10:
        out.append(f"           ... 외 {len(rows) - 10}개")
    out.append("         작업트리가 사라지면 이 코드는 프로덕션에만 남습니다. 커밋을 권합니다")
    if fresh:
        out.append("         !! 방금 바뀐 것이 있습니다 - 다른 세션이 편집 중일 수 있습니다")
    out.append("         (막지 않습니다 - 전송은 그대로 진행됩니다)")
    return out


# ---------------------------------------------------------------------------
# 지금 누가 만지고 있는가 — 관문이 걸렸을 때만 보여준다
# ---------------------------------------------------------------------------
#
# ⛔ **왜 있나** (2026-09-09, 하루에 두 번):
#
#       09:04  import 만 있고 모듈이 없는 상태가 전송됐다 → 프로덕션 80초 정지
#       09:15  인증 미들웨어가 반쯤 배선된 상태 → 스위트 76건 실패
#
#    두 번 다 원인은 **다른 세션이 그 순간 편집 중**이었다는 것이다. 그런데
#    누르는 사람이 보는 것은 `76 failed` 뿐이라, **"내가 뭘 깼나" 를 한참
#    뒤지게 된다.** 실제로 그날 두 세션이 각자 그 판단에 시간을 썼다.
#    (같은 스위트를 4분 간격으로 두 번 쟀더니 76건 → 1건이었다. 둘 다
#    정확히 잰 것이고 **트리가 그 사이에 달라진 것**이다.)
#
#    ⟹ 실패 목록 옆에 **"최근에 바뀐 파일"** 을 함께 찍으면, *"내 잘못이
#       아니라 기다릴 일"* 이라는 판단을 사람이 즉시 할 수 있다.
#
# ⛔ **막지 않는다. 판단도 하지 않는다.** 이건 이미 걸린 관문 옆에 붙는
#    보조 설명이라, 이것만으로 배포를 세우지 않는다.
#
# ⛔ **"누가" 를 단정하지 마라.** mtime 은 *언제* 만 말한다. 오늘 우리는
#    파일 이름만 보고 세션을 잘못 짚었다 (`sales_outlook` → S&OP 세션인 줄
#    알았는데 아니었다). 그래서 문구는 "다른 세션이 편집 중일 수 있습니다" 다.
#
# ⚠️ **`??`(추적 안 됨)를 함께 표시한다.** 오늘 두 번 다 신호가 그것이었다 —
#    `sales_outlook.py` 도 `session_auth.py` 도 **방금 생긴 untracked 새
#    모듈**이었다. `M` 보다 훨씬 강한 신호다.
#
# ⚠️ 창은 10분이다. 실측(2026-09-09 09:30): 2분 0개 · 5분 2개 · 10분 2개 ·
#    20분 11개 · 60분 16개. 오늘 두 사고는 각각 30초·2분 간격이라 어느
#    창으로도 잡히지만, 넓히면 조용한 날에도 목록이 길어져 읽히지 않는다.

RECENT_EDIT_WINDOW_SECONDS = 600


def recently_touched(root: Path, within_seconds: int = RECENT_EDIT_WINDOW_SECONDS,
                     now: float = None) -> List[Tuple[str, int, str]]:
    """최근에 바뀐 감시 대상 소스. `(상대경로, 몇 초 전, 상태)` 를 최신순.

    상태는 `??`(git 이 모름) 또는 `M`(고쳐짐) 또는 빈 문자열(방금 커밋됨).
    """
    import time

    root = Path(root)
    now = time.time() if now is None else now
    unknown = set()
    try:
        unknown = untracked_paths(root)
    except Exception:                                  # noqa: BLE001
        pass                                           # git 이 없어도 mtime 은 말한다

    modified = set()
    try:
        import subprocess
        out = subprocess.run(["git", "status", "--porcelain", "-z"],
                             cwd=str(root), capture_output=True, timeout=120)
        for chunk in out.stdout.decode("utf-8", "replace").split("\0"):
            if chunk[:2] in (" M", "M ", "MM"):
                modified.add(chunk[3:].strip())
    except Exception:                                  # noqa: BLE001
        pass

    rows: List[Tuple[str, int, str]] = []
    for base in WATCHED_ROOTS:
        for p in (root / base).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(root).as_posix()
            if not _load_bearing(rel):
                continue
            try:
                age = int(now - p.stat().st_mtime)
            except OSError:
                continue
            if age < 0 or age > within_seconds:
                continue
            state = "??" if rel in unknown else ("M" if rel in modified else "")
            rows.append((rel, age, state))
    rows.sort(key=lambda r: r[1])
    return rows


def _ago(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds}초 전"
    return f"{seconds // 60}분 전"


def format_recent_edits_notice(rows: List[Tuple[str, int, str]]) -> List[str]:
    """관문이 걸렸을 때 옆에 붙는 설명. 없으면 아무 말도 하지 않는다."""
    if not rows:
        return []
    out = ["  [편집중] 최근 10분 안에 바뀐 소스가 있습니다 - 다른 세션이 편집 중일 수 있습니다"]
    for rel, age, state in rows[:8]:
        tag = "  <- git 에 없음(새 파일)" if state == "??" else ""
        out.append(f"           {rel}  {_ago(age)}{tag}")
    if len(rows) > 8:
        out.append(f"           ... 외 {len(rows) - 8}개")
    out.append("         그 작업이 끝난 뒤 다시 실행하면 통과할 수 있습니다")
    return out


# ---------------------------------------------------------------------------
# 배포 직전 스위트 — "뜨는가" 말고 "도는가"
# ---------------------------------------------------------------------------
#
# ⛔ **왜 있나** (2026-09-09, 같은 날 두 사고가 서로 다른 것을 보여줬다):
#
#       09:04  import 만 있고 모듈이 없었다  → 프로세스가 안 뜬다   (시끄럽다)
#       09:15  인증이 반쯤 배선돼 있었다      → 뜨는데 401 을 낸다   (조용하다)
#
#    위의 관문들(`syntax_errors`·`unresolved_imports`·`missing_self_methods`)은
#    **"프로세스가 뜨는가"** 를 본다. 09:15 상태는 그것을 전부 통과하고
#    `/health` 도 200 이었는데 **스위트가 76건 실패**했다. COA 찾기 41건이
#    죽어 있었고, 그건 사용자에게 조용히 도달한다.
#
#    ⟹ **"도는가" 는 스위트만 안다.** 실측 66초다.
#
# ⛔ **판정만 여기서 하고 실행은 호출부가 한다.** 서버(자가 점검)에는 pytest 도
#    `tests/` 도 없다 — 이 모듈이 서버에서도 import 되므로 여기서 pytest 를
#    부르면 안 된다.
#
# ⚠️ **`--skip-tests` 우회로를 반드시 둔다.** 롤백을 막는 관문이 되면 안 된다
#    (`--skip-preflight` 와 같은 규칙). 그리고 이 트리는 세션 서넛이 공유해서
#    **남의 편집 때문에 상시 막힐 수 있다** — 실측으로 같은 스위트가 4분 사이에
#    76건 → 1건이 됐다. 그래서 실패 화면에 `[편집중]` 을 함께 찍어
#    *"내 잘못이 아니라 기다릴 일"* 을 즉시 알 수 있게 한다.

TEST_TARGETS = ("tests/", "--ignore=tests/frontend")
TEST_TIMEOUT_SECONDS = 900
_SUMMARY = re.compile(
    r"^(?:=+\s*)?(?:(?P<failed>\d+) failed[,\s]+)?"
    r"(?:(?P<passed>\d+) passed)?"
    r"(?:[,\s]+(?P<skipped>\d+) skipped)?"
    r"(?:[,\s]+(?P<errors>\d+) errors?)?",
    re.M)
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(?P<file>[^\s:]+)", re.M)


def _can_run_suite(python: str) -> bool:
    """그 파이썬으로 스위트를 돌릴 수 있나 — pytest 와 프로젝트 의존성이 다 있나."""
    import subprocess

    try:
        done = subprocess.run([python, "-c", "import pytest, fastapi"],
                              capture_output=True, timeout=60)
        return done.returncode == 0
    except Exception:                                     # noqa: BLE001
        return False


def suite_python() -> Optional[str]:
    """스위트를 돌릴 파이썬.

    ⛔ **`sys.executable` 을 그대로 쓰면 안 된다.** CLAUDE.md 는 배포를
       `./sshenv/Scripts/python` 으로 돌리라고 정하고 있는데(paramiko 가 거기 있다),
       그 venv 에는 **프로젝트 의존성이 없다.** 실측(2026-09-09 첫 실전):
       pytest 는 있어서 명령은 뜨는데 **수집 단계에서 118건이 죽고 4.2초 만에**
       요약 줄 없이 끝난다 → 관문이 "결과를 읽지 못했습니다" 로 배포를 막았다.
       테스트 실패가 아니라 **관문이 자기를 못 돌린 것**이었다.

    ⚠️ 이름으로 고르지 않고 **물어본다** — 실제로 import 가 되는 파이썬만 쓴다.
       경로 규칙을 적어 두면 다른 기계에서 조용히 어긋난다.
    """
    import shutil
    import sys as _sys

    seen = set()
    for cand in (_sys.executable, shutil.which("python"), shutil.which("python3")):
        if not cand or cand in seen:
            continue
        seen.add(cand)
        if _can_run_suite(cand):
            return cand
    return None


def test_command(python: str = None) -> List[str]:
    """돌릴 명령. ⚠️ 목록을 호출부에 다시 적지 마라 — 갈리면 관문이 다른 것을 잰다.

    ⛔ 돌릴 파이썬이 없으면 **막지 않고 알린다.** 영원히 못 도는 관문은 곧
       `--skip-tests` 로 넘겨지고, 그러면 아무것도 안 지킨다.
    """
    chosen = python or suite_python()
    if not chosen:
        raise RuntimeError(
            "스위트를 돌릴 파이썬을 찾지 못했습니다 (pytest+프로젝트 의존성 필요)")
    return [chosen, "-m", "pytest", *TEST_TARGETS, "-q"]


def parse_pytest_output(text: str) -> dict:
    """pytest `-q` 출력에서 결과를 읽는다.

    ⚠️ **요약 줄을 못 읽으면 통과로 치지 않는다.** pytest 가 수집 단계에서
       죽으면 요약 줄이 아예 없는데, 그때 `failed=0` 으로 읽어 통과시키면
       **이 관문이 가장 필요한 순간에 침묵한다.**
    """
    text = text or ""
    failed = passed = skipped = errors = None
    for line in reversed(text.strip().splitlines()):
        m = _SUMMARY.match(line.strip())
        if m and (m.group("passed") or m.group("failed")):
            failed = int(m.group("failed") or 0)
            passed = int(m.group("passed") or 0)
            skipped = int(m.group("skipped") or 0)
            errors = int(m.group("errors") or 0)
            break
    files = {}
    for m in _FAILED_LINE.finditer(text):
        f = m.group("file")
        files[f] = files.get(f, 0) + 1
    return {
        "parsed": failed is not None,
        "failed": failed or 0,
        "passed": passed or 0,
        "skipped": skipped or 0,
        "errors": errors or 0,
        "files": sorted(files.items(), key=lambda kv: (-kv[1], kv[0])),
    }


def suite_ok(result: dict, returncode: int) -> bool:
    """통과 판정. ⛔ 요약을 못 읽었거나 종료코드가 0이 아니면 통과가 아니다."""
    if not result.get("parsed"):
        return False
    return returncode == 0 and result["failed"] == 0 and result["errors"] == 0


def format_suite_notice(result: dict, seconds: float = None) -> List[str]:
    """사람이 읽을 줄들. 통과했으면 한 줄, 실패했으면 어디가 깨졌는지."""
    took = f" ({seconds:.0f}초)" if seconds is not None else ""
    if not result.get("parsed"):
        # ⚠️ 여기는 **테스트가 깨진 것과 스위트를 못 돌린 것이 똑같이 생기는** 자리다.
        #    2026-09-09 실전에서 실제로 후자였고(격리 venv 에 의존성이 없었다),
        #    그 구분에 10분이 들었다. 직접 돌려 볼 명령을 함께 준다.
        out = [f"  [스위트] !! 결과를 읽지 못했습니다{took} - 수집 단계에서 죽었을 수 있습니다",
               "         ⚠️ 테스트가 깨진 것이 아니라 **스위트를 못 돌린 것**일 수 있습니다"]
        try:
            out.append("         직접 돌려 보세요: " + " ".join(test_command()))
        except Exception:                                 # noqa: BLE001
            out.append("         돌릴 파이썬을 찾지 못했습니다 (pytest+프로젝트 의존성 필요)")
        out.append("         보내지 않습니다. 정말 이대로 보내야 하면 --skip-tests")
        return out
    if result["failed"] == 0 and result["errors"] == 0:
        return [f"  [스위트] 통과 {result['passed']}건{took}"]
    out = [f"  [스위트] !! {result['failed']}건 실패 / {result['passed']}건 통과{took} - 보내지 않습니다"]
    for f, n in result["files"][:8]:
        out.append(f"           {f}  {n}건")
    if len(result["files"]) > 8:
        out.append(f"           ... 외 {len(result['files']) - 8}개 파일")
    return out
