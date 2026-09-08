# -*- coding: utf-8 -*-
"""저장된 `@@` 소스 이름은 **개명을 넘어서도** 살아야 한다 — 2026-09-04.

⛔ **실사용 장애**: `@@` 키를 `LOG` → `물류` 로 바꾼 뒤, 브라우저(localStorage)에
   `LOG` 를 저장해 둔 사용자가 물류 질문마다 *"허용되지 않은 테이블입니다"* 를 받았다.
   프로덕션 로그 실측: 16:56 · 16:58 · 16:59 세 요청이 그렇게 죽었다.

⚠️ **에러 화면이 아니라 조회 실패로 보인다** — 사용자는 데이터가 없는 줄 안다.
⚠️ 프론트에 저장된 값은 서버가 고칠 수 없다. **보정은 서버에서** 해야 한다.
"""
import pytest

TABLE = "skin1004-319714.Export_control.export_logistics"


def _allowed(srcs):
    from app.agents.sql_agent import _allowed_tables_from_sources

    return _allowed_tables_from_sources(srcs) or set()


@pytest.mark.parametrize("saved", ["LOG", "log", "물류", "선적", "출고", "logistics"])
def test_old_and_alias_names_still_scope_correctly(saved):
    """옛 키·별칭 어느 것으로 저장돼 있어도 그 테이블이 열려야 한다."""
    assert TABLE in _allowed([saved]), saved


def test_unrelated_source_still_blocks():
    """⚠️ 넓히려다 범위 선택이 무의미해지면 안 된다 — 무관한 소스는 계속 막힌다."""
    assert TABLE not in _allowed(["매출"])


def test_mixed_old_and_new_names():
    assert TABLE in _allowed(["LOG", "매출"])
    assert "skin1004-319714.Sales_Integration.SALES_ALL_Backup" in _allowed(["LOG", "매출"])


def test_canonicalization_covers_every_registry_key_and_alias():
    """⛔ 한 소스만 고치면 다음 개명에서 같은 일이 난다 — 전 소스가 흡수돼야 한다."""
    from app.agents.orchestrator import OrchestratorAgent
    from app.agents.sql_agent import _canonical_source

    for entry in OrchestratorAgent.get_db_registry():
        key = entry["key"]
        assert _canonical_source(key) == key, key
        for alias in entry.get("aliases", []):
            assert _canonical_source(alias) == key, (alias, key)


def test_unknown_name_is_passed_through_not_crashed():
    """⚠️ 모르는 이름은 그대로 흘려보낸다 (예전 동작 유지 — 조용히 전체를 열지 않는다)."""
    from app.agents.sql_agent import _canonical_source

    assert _canonical_source("존재하지않는소스") == "존재하지않는소스"
    assert TABLE not in _allowed(["존재하지않는소스"])
