# -*- coding: utf-8 -*-
"""풀리지 않는 import 는 배포를 **막는다**. 2026-09-09.

⛔ **왜 있나** (프로덕션 80초 정지):

        09:03:36  한 세션이 sql_agent.py 에 import 를 넣었다
                    from app.core.sales_outlook import (...)
        09:04:0x  다른 세션의 배포 전송  ← 이 순간 sales_outlook.py 는 없었다
        09:04~    ModuleNotFoundError 크래시 루프 · /health 000 · 재시작 11회
        09:06:29  그 세션이 sales_outlook.py 를 만듦

   import 를 먼저 넣고 3분 뒤 모듈을 만든 것이고, 전송이 그 사이에 떨어졌다.
   배포가 트리 통째 전송이라 **편집 중간 상태가 그대로 프로덕션에 간다.**

⛔ **기존 두 검사는 이걸 못 잡는다.** 문법도 클래스 구조도 멀쩡했다.
   그런데 배포에서 죽는 방식 중 가장 흔한 것이 이것이고, 서버는
   `/health 000` 으로만 말한다.

⛔ **이건 경고가 아니라 관문이다.** 미추적 소스 경고는 "잃을 수도 있다" 는
   가능성이라 막지 않지만, 풀리지 않는 import 는 **확실히 죽는 조건**이다.

⚠️ 이 파일의 절반은 **오탐이 안 나는지**를 본다. 매번 걸리는 관문은 그날로
   꺼지고, 꺼진 관문은 아무것도 안 지킨다. 실제로 처음 구현했을 때 실제
   트리에서 **32건이 오탐**으로 걸렸다 (`from app.core import product_lines`
   같은 하위 모듈 가져오기). 그 32건을 못 봤으면 이 관문은 태어나자마자
   죽었을 것이다.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.deploy_preflight import run, unresolved_imports  # noqa: E402


@pytest.fixture()
def tree(tmp_path):
    """`app/` 뼈대만 있는 빈 트리."""
    for d in ("app/core", "app/agents", "app/api"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path


def w(tree, rel, src):
    p = tree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 잡아야 하는 것
# ---------------------------------------------------------------------------

def test_it_catches_the_module_that_does_not_exist_yet(tree):
    """⛔ 2026-09-09 09:04 에 실제로 프로덕션을 죽인 그 모양이다."""
    w(tree, "app/agents/sql_agent.py", "from app.core.sales_outlook import build_outlook\n")
    problems = unresolved_imports(tree)
    assert len(problems) == 1
    assert "sales_outlook" in problems[0]
    assert "app/agents/sql_agent.py:1" in problems[0]


def test_it_catches_the_name_that_does_not_exist_yet(tree):
    """⚠️ 파일은 있는데 함수가 아직 없는 중간 상태 — 이쪽이 더 흔하다.

    실측: 그날 `sales_outlook.py` 는 09:06 에 생긴 뒤 09:09 에 또 바뀌었고
    **크기가 줄었다**(18,703 → 18,675). 그 사이에 전송됐으면 오늘과 같은 일이
    `ImportError: cannot import name` 으로 났을 것이다.
    """
    w(tree, "app/core/sales_outlook.py", "def something_else():\n    pass\n")
    w(tree, "app/agents/sql_agent.py", "from app.core.sales_outlook import build_outlook\n")
    problems = unresolved_imports(tree)
    assert len(problems) == 1
    assert "build_outlook" in problems[0]


def test_plain_import_of_a_missing_module_is_caught(tree):
    w(tree, "app/agents/x.py", "import app.core.gone\n")
    assert unresolved_imports(tree)


def test_a_package_without_init_is_caught(tree):
    """디렉토리만 있고 `__init__.py` 가 없으면 import 되지 않는다."""
    (tree / "app" / "core" / "halfmade").mkdir()
    w(tree, "app/agents/x.py", "from app.core.halfmade import thing\n")
    assert unresolved_imports(tree)


# ---------------------------------------------------------------------------
# 잡으면 안 되는 것 — 이쪽이 관문의 수명을 정한다
# ---------------------------------------------------------------------------

def test_a_complete_import_is_silent(tree):
    w(tree, "app/core/sales_outlook.py", "def build_outlook():\n    pass\n")
    w(tree, "app/agents/sql_agent.py", "from app.core.sales_outlook import build_outlook\n")
    assert unresolved_imports(tree) == []


def test_importing_a_submodule_from_a_package_is_silent(tree):
    """⛔ 이걸 놓쳐서 실제 트리에서 32건이 오탐으로 걸렸다.

    `from app.core import product_lines` 는 `__init__.py` 의 이름이 아니라
    **하위 모듈**을 가져오는 것이다. 이 저장소가 도처에서 쓰는 형태라
    여기서 걸리면 관문이 매번 실패한다.
    """
    w(tree, "app/core/product_lines.py", "KNOWN = 1\n")
    w(tree, "app/agents/sql_agent.py", "from app.core import product_lines\n")
    assert unresolved_imports(tree) == []


def test_a_lazy_import_inside_a_function_is_not_touched(tree):
    """⚠️ 함수 안 지연 import 는 **의도된 것**이다 — 순환 참조와 기동 속도 때문에
    이 저장소가 실제로 많이 쓴다. 여기서 걸면 관문이 죽는다."""
    w(tree, "app/agents/x.py", "def f():\n    from app.core.not_yet import thing\n    return thing\n")
    assert unresolved_imports(tree) == []


def test_a_method_level_import_is_not_touched(tree):
    w(tree, "app/agents/x.py",
      "class A:\n    def f(self):\n        from app.core.not_yet import thing\n        return thing\n")
    assert unresolved_imports(tree) == []


@pytest.mark.parametrize("handler", ["ImportError", "ModuleNotFoundError", "Exception"])
def test_an_import_guarded_by_try_except_is_not_touched(tree, handler):
    """없을 수 있음을 이미 다루고 있는 코드다."""
    w(tree, "app/agents/x.py",
      "try:\n    from app.core.optional import thing\nexcept %s:\n    thing = None\n" % handler)
    assert unresolved_imports(tree) == []


def test_third_party_imports_are_not_judged(tree):
    """`app.` 이 아닌 것은 여기서 판단하지 않는다 — 설치 여부는 다른 문제다."""
    w(tree, "app/agents/x.py", "import numpy\nfrom fastapi import FastAPI\n")
    assert unresolved_imports(tree) == []


def test_relative_imports_are_not_judged(tree):
    w(tree, "app/core/x.py", "from .y import thing\n")
    assert unresolved_imports(tree) == []


def test_a_module_with_star_import_is_treated_as_unknown(tree):
    """⚠️ 무엇이 들어오는지 정적으로 알 수 없다 — 모르면 통과시킨다."""
    w(tree, "app/core/reexport.py", "from app.core.other import *\n")
    w(tree, "app/core/other.py", "def thing():\n    pass\n")
    w(tree, "app/agents/x.py", "from app.core.reexport import anything_at_all\n")
    assert unresolved_imports(tree) == []


def test_a_module_with_getattr_is_treated_as_unknown(tree):
    w(tree, "app/core/dyn.py", "def __getattr__(name):\n    return 1\n")
    w(tree, "app/agents/x.py", "from app.core.dyn import whatever\n")
    assert unresolved_imports(tree) == []


def test_a_conditionally_defined_name_is_treated_as_unknown(tree):
    """조건부 정의도 모르는 쪽이다 — 안전하게 통과시킨다."""
    w(tree, "app/core/cond.py", "import os\nif os.name == 'nt':\n    def thing():\n        pass\n")
    w(tree, "app/agents/x.py", "from app.core.cond import thing\n")
    assert unresolved_imports(tree) == []


def test_a_syntax_error_is_left_to_the_other_check(tree):
    """⚠️ 여기서 또 보고하면 같은 파일이 두 번 찍힌다."""
    w(tree, "app/agents/broken.py", "def f(:\n")
    assert unresolved_imports(tree) == []


def test_names_defined_as_assignments_and_classes_count(tree):
    w(tree, "app/core/m.py", "CONST = 1\n\n\nclass Thing:\n    pass\n\n\nasync def go():\n    pass\n")
    w(tree, "app/agents/x.py", "from app.core.m import CONST, Thing, go\n")
    assert unresolved_imports(tree) == []


def test_a_name_re_exported_by_import_counts(tree):
    """`__init__.py` 가 끌어올린 이름도 밖에서는 쓸 수 있다."""
    w(tree, "app/core/inner.py", "def thing():\n    pass\n")
    w(tree, "app/core/__init__.py", "from app.core.inner import thing\n")
    w(tree, "app/agents/x.py", "from app.core import thing\n")
    assert unresolved_imports(tree) == []


# ---------------------------------------------------------------------------
# 실제 트리 · 배선
# ---------------------------------------------------------------------------

def test_the_real_tree_has_no_unresolved_imports():
    """⚠️ 여기가 빨개지면 **지금 배포하면 프로덕션이 죽는다**는 뜻이다."""
    assert unresolved_imports(ROOT) == []


def test_run_includes_the_import_gate():
    """⛔ 만들어 놓고 배선하지 않으면 아무것도 안 지킨다 (이 저장소의 전례)."""
    import inspect

    from app.core import deploy_preflight

    src = inspect.getsource(deploy_preflight.run)
    assert "unresolved_imports" in src


def test_the_gate_blocks_rather_than_warns():
    """⛔ 미추적 경고와 다르다 — 이건 확실히 죽는 조건이라 막는다."""
    ok, problems = run(ROOT)
    assert ok is True and problems == []


def test_the_escape_hatch_survives():
    """⚠️ 롤백을 막는 관문이 되면 안 된다 — `--skip-preflight` 는 남아야 한다."""
    src = (ROOT / "scripts" / "deploy_new_server.py").read_text(encoding="utf-8")
    assert "--skip-preflight" in src


def test_the_gate_is_separate_from_the_untracked_notice():
    """⛔ 막는 것과 적는 것을 섞지 마라 — 섞이면 둘 중 하나가 성격을 잃는다."""
    import inspect

    from app.core import deploy_preflight

    run_src = inspect.getsource(deploy_preflight.run)
    assert "untracked" not in run_src, "경고가 관문 안으로 들어왔다"
