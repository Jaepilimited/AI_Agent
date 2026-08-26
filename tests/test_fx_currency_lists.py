# -*- coding: utf-8 -*-
"""환율 통화 목록이 두 곳에서 갈리지 않는가.

⛔ 목록이 **두 벌**이다: `app/core/fx_rates.py`(화면·잔디가 읽는다)와
   `scripts/fx_relay.py`(DB_PC 가 값을 가져온다). 사본은 서버가 환율 API 에 붙지
   못해서 생겼다 — 릴레이는 DB_PC 에서 단독 실행돼야 하므로 앱 모듈을 import 하지
   않는다. 없앨 수 없는 사본이면 **대조라도 해야 한다.**

한쪽만 늘리면 에러가 나지 않는다:
  · 릴레이만 늘리면 → 값은 쌓이는데 화면에 안 뜬다
  · 코어만 늘리면 → 화면 코드가 그 통화를 찾다 없어서 **조용히 건너뛴다**
둘 다 "고쳤는데 아무 일도 안 일어난다" 로 나타난다.
"""
import importlib.util
from pathlib import Path

import pytest

fx_rates = pytest.importorskip("app.core.fx_rates")

ROOT = Path(__file__).resolve().parent.parent


def _relay():
    """릴레이는 스크립트라 패키지 경로가 없다 — 파일에서 직접 읽는다."""
    path = ROOT / "scripts" / "fx_relay.py"
    if not path.exists():
        pytest.skip("fx_relay.py 가 없다")
    spec = importlib.util.spec_from_file_location("_fx_relay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_currency_lists_match():
    relay = _relay()
    assert tuple(relay.CURRENCIES) == tuple(fx_rates.CURRENCIES), (
        "가져오는 쪽과 보여주는 쪽의 통화가 다르다 — 한쪽만 고쳤다")


def test_units_match():
    relay = _relay()
    assert dict(relay.UNITS) == dict(fx_rates.UNITS), "고시 단위가 다르다"


def test_small_currencies_are_quoted_per_hundred():
    """⚠️ 1단위가 1원도 안 되는 통화는 100단위로 적는다 — 그대로 적으면 0 처럼 보인다.
       실측(2026-08-26): JPY 8.69원 · IDR **0.078원**. 한국 은행 고시도 100단위다."""
    for code in ("JPY", "IDR"):
        assert fx_rates.UNITS.get(code) == 100, code


def test_requested_markets_are_included():
    """사용자가 지정한 시장 (2026-08-26): 동남아 4개국 + 오세아니아."""
    for code in ("SGD", "PHP", "MYR", "IDR", "AUD"):
        assert code in fx_rates.CURRENCIES, code


# ── 전월대비 (2026-08-26 사용자 지정) ────────────────────────────────────────

def test_month_before_clamps_to_month_end():
    """⚠️ `timedelta(days=30)` 으로 때우면 달 길이가 달라 **전월대비가 달마다 다른
       기간**을 재게 된다. 없는 날짜(3/31 → 2월)는 말일로 당긴다."""
    from datetime import date

    assert fx_rates.month_before(date(2026, 8, 26)) == date(2026, 7, 26)
    assert fx_rates.month_before(date(2026, 3, 31)) == date(2026, 2, 28)
    assert fx_rates.month_before(date(2026, 1, 15)) == date(2025, 12, 15)


def test_basis_is_a_month_back_not_the_previous_day():
    """⛔ 전일대비는 하루 0.1% 수준이라 화면에서 거의 늘 "보합" 이었다.
       비교 기준을 한 달 전으로 옮겼다 — `latest()` 가 그 날짜를 함께 돌려줘야
       화면이 "무엇과 견줬는지" 밝힐 수 있다."""
    import inspect

    src = inspect.getsource(fx_rates.latest)
    assert "_basis_date" in src, "전일 비교가 남아 있다"
    assert "for_date < %s" not in src, "직전 보유일과 비교하고 있다"
    assert "basis_date" in src, "기준일을 안 돌려주면 화면이 밝힐 수 없다"


def test_basis_lookup_tolerates_holidays():
    """⛔ 한 달 전 날짜를 콕 집어 찾으면 주말·공휴일에 **조용히 0건**이 된다."""
    import inspect

    src = inspect.getsource(fx_rates._basis_date)
    assert "<=" in src and "MAX(for_date)" in src


def test_relay_can_backfill_history():
    """전월대비는 한 달 전 값이 있어야 성립한다. open.er-api 무료 요금제에는 과거
    조회가 없어 ECB(frankfurter)로만 채운다 — 그래서 백필 경로가 따로 있다."""
    relay = _relay()
    assert hasattr(relay, "fetch_history")
    import inspect
    src = inspect.getsource(relay.fetch_history)
    # ⚠️ 요청한 날짜가 아니라 **응답이 준 날짜**를 써야 한다 (휴일이면 직전 영업일이다)
    assert 'data.get("date")' in src
