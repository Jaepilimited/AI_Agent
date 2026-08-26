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
