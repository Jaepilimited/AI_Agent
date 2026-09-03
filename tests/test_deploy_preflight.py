# -*- coding: utf-8 -*-
"""배포 게이트 — 오늘 실제로 프로덕션을 13분 죽인 모양을 잡는가.

2026-09-03: 다른 세션이 편집 중이던 `orchestrator.py` 가 배포됐다. 모듈 레벨
`def` 하나가 클래스 한가운데에 들어가 뒤따르던 메서드 5개(`_handle_direct`
포함)가 그 함수 안으로 빨려 들어갔다. **문법은 멀쩡했고 `/health` 는 200 이었다.**
"""
from pathlib import Path

import pytest

from app.core import deploy_preflight as PF


def _write(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return tmp_path


# ── 오늘 그 사고 ────────────────────────────────────────────────────────

BROKEN = '''
class Orchestrator:
    def route(self):
        return self._handle_direct()

    def _handle_bigquery(self):
        return 1


def _module_level_helper():
    """클래스 한가운데 끼어든 모듈 레벨 함수."""

    async def _handle_direct(self):
        return 2
'''

HEALTHY = '''
class Orchestrator:
    def route(self):
        return self._handle_direct()

    def _handle_bigquery(self):
        return 1

    def _handle_direct(self):
        return 2


def _module_level_helper():
    return 3
'''


def test_it_catches_a_method_that_fell_out_of_its_class(tmp_path):
    root = _write(tmp_path, "app/agents/orchestrator.py", BROKEN)
    ok, problems = PF.run(root)
    assert not ok
    assert any("_handle_direct" in p for p in problems), problems


def test_the_broken_file_still_parses(tmp_path):
    """⛔ 이게 이 검사가 필요한 이유다 — 문법 검사로는 절대 안 잡힌다."""
    import ast
    ast.parse(BROKEN)                     # 터지지 않는다
    root = _write(tmp_path, "app/agents/orchestrator.py", BROKEN)
    assert PF.syntax_errors(root) == []   # 문법은 멀쩡하다
    assert PF.missing_self_methods(root)  # 그런데 부르면 터진다


def test_the_repaired_file_passes(tmp_path):
    """반대 방향 — 고치면 통과해야 한다."""
    root = _write(tmp_path, "app/agents/orchestrator.py", HEALTHY)
    ok, problems = PF.run(root)
    assert ok, problems


# ── 정상 작업을 막지 않는다 (막으면 그날로 꺼진다) ──────────────────────

def test_the_real_tree_passes():
    """⛔ 오탐이 하나라도 있으면 이 게이트는 첫날에 꺼진다."""
    ok, problems = PF.run()
    assert ok, problems[:10]


def test_attributes_assigned_in_init_are_not_flagged(tmp_path):
    root = _write(tmp_path, "app/x.py", '''
class A:
    def __init__(self):
        self.fn = lambda: 1

    def go(self):
        return self.fn()
''')
    assert PF.missing_self_methods(root) == []


def test_a_class_inheriting_from_elsewhere_is_skipped(tmp_path):
    """부모의 메서드를 알 수 없다 — 판단하면 전부 오탐이 된다."""
    root = _write(tmp_path, "app/x.py", '''
from somewhere import Base

class A(Base):
    def go(self):
        return self.inherited_thing()
''')
    assert PF.missing_self_methods(root) == []


def test_setattr_makes_the_class_unjudgeable(tmp_path):
    root = _write(tmp_path, "app/x.py", '''
class A:
    def build(self):
        setattr(self, "fn", lambda: 1)

    def go(self):
        return self.fn()
''')
    assert PF.missing_self_methods(root) == []


def test_a_plain_attribute_read_is_not_a_call(tmp_path):
    """호출만 본다 — 속성 참조까지 보면 오탐이 쏟아진다."""
    root = _write(tmp_path, "app/x.py", '''
class A:
    def go(self):
        return self.some_value
''')
    assert PF.missing_self_methods(root) == []


def test_class_level_constants_count_as_defined(tmp_path):
    root = _write(tmp_path, "app/x.py", '''
class A:
    HANDLERS = {}

    def go(self):
        return self.HANDLERS.get("x")
''')
    assert PF.missing_self_methods(root) == []


# ── 문법 오류 ───────────────────────────────────────────────────────────

def test_a_syntax_error_is_reported_with_a_line(tmp_path):
    root = _write(tmp_path, "app/broken.py", "def f(:\n    pass\n")
    ok, problems = PF.run(root)
    assert not ok
    assert "app/broken.py:1" in problems[0]


def test_scripts_are_not_checked(tmp_path):
    """⛔ 일회용 조사 스크립트가 깨진 채 남아 있다 (앱은 import 하지 않는다).
    그것 때문에 매번 실패하면 게이트는 곧 꺼진다."""
    root = _write(tmp_path, "scripts/_scratch.py", "def f(:\n")
    (root / "app").mkdir(exist_ok=True)
    ok, problems = PF.run(root)
    assert ok, problems


# ── 배포 스크립트에 배선돼 있는가 ───────────────────────────────────────

def test_the_deploy_script_runs_the_gate():
    """⛔ 만들어 놓고 배선하지 않으면 아무 일도 안 한다 — 이 저장소가 실제로
    겪은 실패다 (`answer_check` 가 실사용 경로에 없어 계측조차 안 됐다)."""
    with open("scripts/deploy_new_server.py", encoding="utf-8") as fh:
        src = fh.read()
    assert "deploy_preflight" in src
    assert "--skip-preflight" in src, "비상용 우회로가 있어야 롤백이 막히지 않는다"
