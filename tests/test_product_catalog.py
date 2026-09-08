# -*- coding: utf-8 -*-
"""대표 제품 목록은 **실측 주입**이다 — 2026-09-03.

⛔ **사용자 제보**: *"왜 최신데이터가 반영이 안되어있지? 센텔라 테카같은거 말이야"*

    질문: "센텔라 테카 앰플이 뭐야?"
    답변: "'센텔라 테카 앰플'이라는 정확한 제품명은 **공식 제품 목록에서
           확인되지 않습니다.**"

`SK_Centella_Teca_Ampoule_50ml` 은 **2026-01-16 출시 · 361,315개 판매**된 주력이다.
BigQuery(9종)·전성분(8건)·매핑(15건)·CS 자료(39건)에 **전부 있었다.**
없는 것은 direct 프롬프트에 손으로 적어 둔 `## 대표 제품` 목록뿐이었고,
거기 붙은 *"제품명 창작 금지"* 가 **없는 제품이라고 더 강하게 단정하게** 만들었다.

⚠️ 데이터가 낡은 게 아니라 **프롬프트가 낡았다.** 사용자에게는 구분이 안 된다.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_direct_prompt_no_longer_hardcodes_products():
    """⛔ 손으로 적은 목록은 반드시 낡는다 — 자리표시자여야 한다."""
    src = _read("app/agents/orchestrator.py")
    assert "{_product_catalog_section()}" in src
    # 옛 하드코딩 흔적이 남아 있으면 안 된다
    assert "마다가스카르 센텔라 토닝 토너 (210ml)" not in src
    assert "기타 라인: 프로바이오시카, 티트리카, 히알루테카, 센텔라테카" not in src


def test_section_never_denies_a_product_it_does_not_list(monkeypatch):
    """⛔ 이것이 사고의 핵심이다 — 목록에 없다고 **없는 제품이라 단정하면 안 된다.**"""
    from app.core import product_catalog as PC

    monkeypatch.setattr(PC, "_cached", lambda: [
        {"line": "Centella", "items": [
            {"product": "SK_Centella_Ampoule_100ml", "qty": 1, "first_sold": "2020-01-01"}]}])
    text = PC.section()
    assert "없는 제품이 아닙니다" in text
    assert "단정하지 말고" in text


def test_missing_cache_does_not_turn_into_a_blanket_denial(monkeypatch):
    """⛔ 캐시가 비었을 때 '이 목록에 없으면 없는 제품' 규칙이 남으면
    **모든 제품을 부정한다** — 원래 사고보다 나쁘다."""
    from app.core import product_catalog as PC

    monkeypatch.setattr(PC, "_cached", lambda: None)
    text = PC.section()
    assert "단정하지 마세요" in text
    assert "지어내지" in text


def test_sets_and_supplies_are_not_representative_products():
    """⚠️ 실측 상위에 세트·샤쉐·파우치가 섞여 온다 — 대표 제품이 아니다."""
    from app.core.product_catalog import _looks_like_a_set

    for junk in ("SK_Centella_Travel_Kit", "SK_Centella_Double_Cleansing_Duo",
                 "SK_Centella_Teca_Ampoule_Sachet",
                 "SK_Hyalucica_Waterfit_Sun_Serum_50ml_twin_pack",
                 "SK_Signature_Pouch", "SK_Pure_Cotton_Pads_60ea", "Others"):
        assert _looks_like_a_set(junk), junk
    for real in ("SK_Centella_Teca_Ampoule_50ml", "SK_Centella_Ampoule_100ml",
                 "SK_Hyalu_Teca_Plumping_Ampoule_50ml"):
        assert not _looks_like_a_set(real), real


def test_residual_line_buckets_are_dropped():
    """⚠️ `Others`·`SET` 은 라인이 아니라 잔여 버킷이다."""
    from app.core.product_catalog import _EXCLUDE_LINES

    for bucket in ("others", "set", "기타"):
        assert bucket in _EXCLUDE_LINES


def test_refresh_refuses_to_overwrite_with_an_empty_list(monkeypatch):
    """⚠️ 0건은 성공이 아니다 — 빈 목록으로 덮으면 제품이 통째로 사라진다."""
    from app.core import product_catalog as PC

    monkeypatch.setattr(PC, "_fetch_live", lambda: [])
    assert PC.refresh() == 0


def test_catalog_refresh_is_scheduled():
    main = _read("app/main.py")
    assert "product_catalog" in main and "_catalog_refresh()" in main


def test_ingredient_message_no_longer_claims_we_have_no_data():
    """⛔ 2026-08-06 에 전성분을 적재했는데 안내문은 '보유하고 있지 않습니다' 였다."""
    from app.agents.orchestrator import INGREDIENT_EXCLUSION_MESSAGE as MSG

    assert "전성분 데이터를 시스템이 보유하고 있지 않습니다" not in MSG
    assert "모르는" in MSG and "안 들어간" in MSG


def test_help_line_names_come_from_the_single_source():
    """⛔ 라인 목록을 손으로 적어 둔 탓에 센텔라테카·히알루테카가 빠져 있었다."""
    from app.agents.orchestrator import _line_names_for_help

    names = _line_names_for_help()
    assert "센텔라테카" in names and "히알루테카" in names


def test_orchestrator_class_body_is_not_truncated():
    """⛔ 클래스 안에 들여쓰기 없는 `def` 를 넣으면 **거기서 클래스가 끝난다.**

    2026-09-03 실제 사고: 대표 제품 헬퍼를 `_build_direct_system_prompt` 앞에
    모듈 레벨로 끼워 넣었더니 뒤의 `_BIZ_CONTEXT`·`_SEARCH_KEYWORDS` 가 클래스
    밖으로 나갔다. **import 는 멀쩡히 됐고**, 라우팅이 그 속성을 만질 때서야
    `AttributeError` 가 났다 — 테스트 68건이 한 번에 깨졌고, 그 상태가
    프로덕션에 한 번 배포됐다.
    """
    from app.core.static_checks import orchestrator_class_intact

    ok, msg = orchestrator_class_intact()
    assert ok, msg


def test_catalog_helper_lives_outside_the_class():
    """⚠️ 헬퍼는 클래스 **앞**에 둔다 — 안에 두면 위 사고가 재현된다."""
    src = _read("app/agents/orchestrator.py")
    assert src.index("def _product_catalog_section") < src.index("class OrchestratorAgent")
