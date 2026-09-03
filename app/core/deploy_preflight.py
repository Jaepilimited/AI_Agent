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
