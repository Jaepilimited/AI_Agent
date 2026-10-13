"""오늘의 환율 — DB_PC 가 가져와 넣고, 브리핑이 읽는다.

**왜 릴레이인가** (2026-08-26 실측). 서버는 환율 API 에 붙지 못한다:

    WAS → open.er-api.com / frankfurter / koreaexim / naver   전부 000 (2ms 즉시 실패)
    WAS → generativelanguage.googleapis.com                   404  ← 프록시는 살아 있다
    DB_PC → open.er-api.com / frankfurter / finance.naver.com  전부 OK

즉 프록시 화이트리스트에 없어서 막힌 것이지 네트워크가 죽은 게 아니다. 잔디와 **같은
구조**다 — 바깥과 이야기하는 쪽(DB_PC)과 값을 쓰는 쪽(WAS)이 다르다.

⛔ **LLM 에게 환율을 물어보지 마라.** 검색 그라운딩으로 가져오면 숫자를 지어낼 수 있고,
   환율은 그럴듯하게 틀리면 알아채기 어렵다 (`insight.py` 가 숫자를 못 쓰게 한 것과 같은
   이유). 값은 API 가 주고, 코드가 그대로 싣는다.
"""

from __future__ import annotations

from calendar import monthrange as _monthrange
from datetime import date, datetime
from typing import Any

from app.db.mariadb import execute, fetch_all, fetch_one

#: 변동률은 **전월대비**다 (2026-08-26 사용자 지정). 전일대비는 하루 0.1% 수준이라
#: 화면에서 거의 늘 "보합" 으로 보였다.
#: ⚠️ 기준값이 다른 출처면 출처 차이가 변동률에 섞인다. 실측(2026-08-26)으로
#:    open.er-api 와 ECB 는 대부분 0.08% 이내이나 **CNY 0.25% · IDR 0.17%** 벌어진다 —
#:    이보다 작은 변동률은 진짜 움직임이 아닐 수 있다. 한 달치가 쌓이면 같은 출처끼리
#:    비교하게 되어 저절로 사라진다.
#: 브리핑에 싣는 통화. 회사 주력 시장 기준 (일본·미국·중국·서구권 + 동남아·오세아니아).
#: ⚠️ 늘리면 한 줄이 길어져 읽히지 않는다. 늘릴 땐 화면부터 확인할 것.
#: ⛔ **`scripts/fx_relay.py` 에 같은 목록이 있다.** 릴레이는 DB_PC 에서 단독 실행돼야
#:    해서(서버는 환율 API 에 못 붙는다) 앱 모듈을 import 하지 않는다 — 사본이 불가피하다.
#:    한쪽만 늘리면 **에러 없이** 새 통화가 화면에 안 뜬다. 테스트가 두 목록을 대조한다.
CURRENCIES = ("USD", "JPY", "CNY", "EUR", "SGD", "PHP", "MYR", "IDR", "AUD")

#: JPY 는 100엔 단위로 고시하는 것이 한국 관행이다. 1엔으로 적으면 자릿수가 낯설다.
#: ⚠️ IDR 도 마찬가지다 — 1루피아는 **0.078원**이라 그대로 적으면 0 처럼 보인다
#:    (2026-08-26 실측). 한국 은행 고시도 100루피아 단위다.
UNITS = {"JPY": 100, "IDR": 100}

_DDL = """
CREATE TABLE IF NOT EXISTS fx_rates (
    for_date   DATE NOT NULL,
    currency   VARCHAR(8) NOT NULL,
    krw        DECIMAL(16,4) NOT NULL,
    unit       INT NOT NULL DEFAULT 1,
    source     VARCHAR(40) NOT NULL DEFAULT '',
    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (for_date, currency)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_tables() -> None:
    execute(_DDL)


def put(for_date: date, rows: list[dict[str, Any]], source: str) -> int:
    """하루치를 통째로 upsert. 같은 날 두 번 넣어도 마지막 값만 남는다."""

    ensure_tables()
    saved = 0
    for row in rows:
        currency = str(row.get("currency", "")).upper()[:8]
        try:
            krw = float(row.get("krw"))
        except (TypeError, ValueError):
            continue
        if not currency or krw <= 0:
            continue
        execute(
            "INSERT INTO fx_rates (for_date, currency, krw, unit, source) "
            "VALUES (%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE krw=VALUES(krw), unit=VALUES(unit), "
            "source=VALUES(source), fetched_at=NOW()",
            (for_date, currency, krw, int(row.get("unit") or 1), source[:40]),
        )
        saved += 1
    return saved


def _rows_for(for_date: date) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT currency, krw, unit, source, fetched_at FROM fx_rates "
        "WHERE for_date = %s", (for_date,),
    ) or []


def month_before(day: date) -> date:
    """한 달 전 같은 날. 없는 날짜는 그 달 말일로 당긴다 (3/31 → 2/28).

    ⚠️ `timedelta(days=30)` 으로 때우지 마라 — 달마다 길이가 달라 "전월대비" 가
       달에 따라 다른 기간을 재게 된다.
    """
    year, month = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    last = _monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


#: 비교 기준일이 '한 달 전' 에서 이만큼까지 떨어지는 것은 정상이다 (주말·공휴일).
#: ⚠️ 이보다 멀면 전월대비가 아니다 — 이름을 바꾼다.
BASIS_TOLERANCE_DAYS = 7


def basis_note(basis, target) -> str:
    """무엇과 견줬는지 한 마디. **화면과 잔디가 같이 쓴다** — 사본을 두지 않는다."""

    if not basis:
        return ""
    gap = (target - basis).days
    if 0 <= gap <= BASIS_TOLERANCE_DAYS:
        return f"전월대비 {basis}"
    # 보유일이 드물어 한 달 전과 견주지 못했다. 숫자는 진짜지만 이름은 아니다.
    return f"{basis} 대비"


def _basis_date(day: date):
    """전월대비의 기준일 — 한 달 전 **이전**에서 가장 가까운 보유일.

    ⛔ 한 달 전 날짜를 콕 집어 찾으면 주말·공휴일에 **조용히 0건**이 된다.
       그 날짜 이하에서 가장 가까운 값을 쓴다.
    ⚠️ 없으면 None 이다 — 그때는 변동률을 만들지 않는다. 없는 것을 0% 로 적으면
       "안 움직였다" 로 읽힌다 (이 파일이 이미 지키는 규칙이다).
    """
    row = fetch_one(
        "SELECT MAX(for_date) d FROM fx_rates WHERE for_date <= %s",
        (month_before(day),),
    ) or {}
    basis = row.get("d")
    if isinstance(basis, datetime):
        basis = basis.date()
    return basis

def latest(before_or_on: date) -> dict[str, Any]:
    """그 날짜 기준 가장 최근 환율 한 벌.

    ⚠️ 주말·공휴일에는 새 값이 안 들어온다. 그때 **빈 칸을 보여주지 않고** 마지막 값을
       쓰되, 며칠 것인지 함께 돌려준다 — 화면이 기준일을 밝힌다.
    """

    ensure_tables()
    row = fetch_one(
        "SELECT MAX(for_date) d FROM fx_rates WHERE for_date <= %s", (before_or_on,),
    ) or {}
    day = row.get("d")
    if not day:
        return {"status": "empty", "for_date": "", "stale_days": 0, "items": []}
    if isinstance(day, datetime):
        day = day.date()

    rows = {str(r["currency"]): r for r in _rows_for(day)}
    prior_day = _basis_date(day)
    prior = {str(r["currency"]): r for r in (_rows_for(prior_day) if prior_day else [])}

    items = []
    for currency in CURRENCIES:
        row = rows.get(currency)
        if not row:
            continue
        krw = float(row["krw"])
        was = prior.get(currency)
        change = None
        was_krw = None
        if was:
            try:
                basis = float(was["krw"])
                if basis > 0:
                    # ⚠️ 비교값을 계산에만 쓰고 버리지 않는다 — 변동률만 남으면
                    #    '얼마에서 얼마로' 가 안 보인다 (2026-08-26 사용자 요청).
                    was_krw = basis
                    change = round((krw - basis) / basis * 100, 2)
            except (TypeError, ValueError):
                change = None
        items.append({
            "currency": currency,
            "unit": int(row.get("unit") or 1),
            "krw": krw,
            "was_krw": was_krw,
            "change_pct": change,
        })

    return {
        "status": "ready" if items else "empty",
        "for_date": str(day),
        # ⚠️ 무엇과 견준 값인지 화면이 밝혀야 한다. 비어 있으면 변동률도 없다
        "basis_date": str(prior_day) if prior_day else "",
        "basis_note": basis_note(prior_day, month_before(day)),
        "stale_days": max(0, (before_or_on - day).days),
        "source": str((rows.get(CURRENCIES[0]) or {}).get("source", "")),
        "items": items,
    }


def amount(krw: float) -> str:
    """금액 서식. ⚠️ 작은 통화(PHP·IDR)는 소수 둘째 자리까지 — 반올림하면 0 이 된다."""

    value = float(krw)
    return f"{value:,.0f}" if value >= 100 else f"{value:,.2f}"


def label(item: dict[str, Any]) -> str:
    """`USD 1,283 → 1,383원` / 비교값이 없으면 `USD 1,383원`.

    화면과 잔디 본문이 같이 쓴다.
    ⚠️ 반올림 후 두 값이 같으면 화살표를 쓰지 않는다 — `1,383 → 1,383원` 은 소음이다.
    """

    unit = int(item.get("unit") or 1)
    name = f"{item['currency']}({unit})" if unit != 1 else str(item["currency"])
    now = amount(item["krw"])
    was = item.get("was_krw")
    if was is not None and amount(was) != now:
        return f"{name} {amount(was)} → {now}원"
    return f"{name} {now}원"


def change_label(item: dict[str, Any]) -> str:
    """비교 기준 대비 변동률 (기준일은 `latest()` 의 `basis_date` 가 밝힌다).

    ⚠️ 값이 없으면 0% 라고 쓰지 마라 — 안 움직인 것과 모르는 것은 다르다.
    """

    change = item.get("change_pct")
    if change is None:
        return ""
    if change > 0:
        return f"▲ {change:.2f}%"
    if change < 0:
        return f"▼ {abs(change):.2f}%"
    return "보합"


def cleanup(before: date) -> int:
    return int(execute("DELETE FROM fx_rates WHERE for_date < %s", (before,)) or 0)
