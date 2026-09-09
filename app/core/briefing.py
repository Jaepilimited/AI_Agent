# -*- coding: utf-8 -*-
"""개인화 데일리 브리핑 — 사용자가 묻지 않아도 먼저 찾아간다.

왜 만드는가 (2026-08-20 실측):
    AD 500명 중 가입 61명, 30일 활성 29명, **30일 중 하루만 쓴 사람이 11명(38%)**.
    지금 Cella 는 "궁금할 때 찾아가는 도구" 라서 궁금하지 않은 날은 아무도 오지 않는다.
    기능을 더 붙여도 이 구조는 바뀌지 않는다 — **열 이유를 매일 만드는 것**이 유일한 길이다.

설계 원칙:
    1. ⛔ **변화가 없으면 보내지 않는다.** 매일 같은 알림은 곧 무시당한다 (이 저장소의 원칙).
    2. ⛔ **틀린 숫자를 보내면 첫날로 끝난다.** 사용자가 묻지도 않았는데 먼저 간 숫자가
       틀리면 신뢰 회복이 안 된다. 그래서 기준일 판정(`stable_date`)이 이 모듈의 핵심이다.
    3. 숫자는 코드가 만든다. LLM 은 여기서 한 글자도 쓰지 않는다.

⛔ 매출 테이블의 함정 둘 (실측):
    - **미래 날짜가 8,741건** 있다 (2026-08-24 ~ 10-31). 안 막으면 합계가 조용히 부푼다.
    - **적재가 1~2일 늦다.** 어제 358행 / 그저께 21,653행 / 3일 전 34,138행.
      "어제 매출" 로 브리핑하면 정상일의 1~2% 만 보고 "매출 급감" 이라고 알린다.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import structlog

from app.db.mariadb import execute, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

# 브리핑을 보낼 최소 변화폭 — 이보다 작으면 "평소와 같다" 로 보고 보내지 않는다
# ⚠️ 임계가 낮으면 매일 뜬다 — 매일 뜨는 알림은 곧 무시당한다. 실측(2026-08-20)에서
#    주간 변동은 대부분 ±20% 안팎이라 15% 를 넘으면 "말할 만한 변화" 로 본다
_MIN_DELTA_PCT = 15.0
# 축·변화 항목의 최소 규모. ⛔ 0.3억은 너무 낮았다 (사용자 지적 2026-08-21) —
#    실측에서 국가 66개 중 32개가 통과해 중앙값(0.25억) 언저리를 다 끌어들였다.
_MIN_SCALE_KRW = 100_000_000    # 1억 (국가 66개 중 22개가 통과)

# "눈에 띄는 변화" 는 **금액**으로 고른다. ⛔ 변화율로 고르면 작은 나라의 배수가 이긴다 —
#    실측: 규칙이 영국 +733%(+10.8억)를 골랐지만, 정작 가장 크게 움직인 것은
#    아랍에미리트 **−12.9억**(16.8억→3.9억)이었다. 그게 눈에 띄는 변화다.
# 그리고 축 합계 대비 비중도 본다 — 전사 143억에서 1억 움직임은 눈에 띄지 않는다.
_CHANGE_MIN_SHARE = 0.05        # 축 합계의 5% 이상 움직였을 때만
_CHANGE_MIN_PCT = 10.0          # 그 나라 기준으로도 10% 이상 움직였을 때만
_REPEAT_COOLDOWN_DAYS = 7       # 같은 축·나라·방향은 한 비교 주기 동안 다시 알리지 않는다
_MARKETING_TABLE = "skin1004-319714.marketing_analysis.integrated_ad"


_DDL = """
CREATE TABLE IF NOT EXISTS daily_briefings (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    for_date   DATE NOT NULL,
    scope      VARCHAR(40) NOT NULL DEFAULT '',
    title      VARCHAR(300) NOT NULL,
    body       TEXT NULL,
    follow_up  VARCHAR(300) NOT NULL DEFAULT '',
    notified   TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    seen_at    DATETIME NULL,
    UNIQUE KEY uq_user_date (user_id, for_date),
    INDEX idx_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_SALES_SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS briefing_sales_snapshots (
    for_date   DATE PRIMARY KEY,
    payload    LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_MARKETING_SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS briefing_marketing_snapshots (
    for_date   DATE PRIMARY KEY,
    payload    LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


#: 이미 만들어진 테이블에는 CREATE 문이 닿지 않는다. 뒤에 생긴 컬럼은 따로 붙인다.
_MIGRATIONS = (
    "ALTER TABLE daily_briefings ADD COLUMN notified TINYINT NOT NULL DEFAULT 1",
)


def ensure_tables() -> None:
    for ddl in (_DDL, _MARKETING_SNAPSHOT_DDL, _SALES_SNAPSHOT_DDL):
        try:
            execute(ddl)
        except Exception as e:
            logger.debug("briefing_ddl_skip", error=str(e)[:120])
    for statement in _MIGRATIONS:
        try:
            execute(statement)
        except Exception:
            # 이미 있는 컬럼이다. 없을 때만 의미가 있고, 실패해도 기존 기능은 산다.
            pass


# ── 기준일 ───────────────────────────────────────────────────────────────────

def stable_date(bq, table: str, lookback: int = 14) -> Optional[date]:
    """데이터가 **안정적으로 들어온** 마지막 날.

    일별 행수를 보고 중앙값의 50% 이상인 가장 최근 날짜를 고른다. 미래 날짜는 애초에
    제외한다. 이 판정이 없으면 적재 지연 중인 날을 "매출 급감" 으로 알리게 된다.
    """
    rows = bq.execute_query(f"""
        SELECT Date AS d, COUNT(*) AS n
        FROM `{table}`
        WHERE Date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(lookback)} DAY)
          AND Date <= CURRENT_DATE()
        GROUP BY d ORDER BY d DESC
    """)
    if not rows:
        return None
    counts = sorted(int(r["n"]) for r in rows)
    median = counts[len(counts) // 2]
    floor = median * 0.5
    for r in rows:                      # 최신순 — 처음 통과하는 날이 기준일
        if int(r["n"]) >= floor:
            d = r["d"]
            return d.date() if isinstance(d, datetime) else d
    return None


# ── 관심 축 ──────────────────────────────────────────────────────────────────

def resolve_scope(department: str, team_map: Dict[str, str]) -> Dict[str, str]:
    """이 사람에게 보여줄 축을 정한다.

    사용자 부서 문자열에 공식 팀명이 들어 있으면 그 팀. ⚠️ 표기 공백이 다르다
    ("서구권 마케팅팀" vs `서구권마케팅팀`) — 공백을 지우고 비교한다.
    못 찾으면 전사로 두되, **왜 이 숫자를 보는지 문구에 밝힌다**.
    """
    dep = (department or "").replace(" ", "")
    for code, kr in team_map.items():
        if kr.replace(" ", "") in dep:
            return {"kind": "team", "code": code, "label": kr}
    return {"kind": "all", "code": "", "label": "전사"}



def infer_country(user_email: str, known: List[str], days: int = 30,
                  min_hits: int = 3) -> Optional[str]:
    """최근 질문에서 이 사람이 **반복해서 묻는 나라**를 찾는다.

    소속 팀이 매출 축에 없는 사람(상품·운영·CS 등)이 실측 61명 중 37명이다. 그들에게
    전사 숫자만 보내면 "내 얘기" 가 아니어서 곧 안 본다. 대신 **실제로 묻던 것**을 준다.
    ⚠️ 국가 목록은 손으로 적지 않는다 — 값 목록 캐시(실측)를 받아 쓴다.
    ⚠️ 세 번 이상 물어야 인정한다. 한두 번은 지나가는 질문이다.
    """
    if not user_email or not known:
        return None
    rows = fetch_all(
        "SELECT `query` q FROM audit_logs WHERE user_email = %s "
        "AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) LIMIT 300",
        (user_email, int(days))) or []
    if not rows:
        return None
    hits: Dict[str, int] = {}
    for r in rows:
        q = r.get("q") or ""
        for name in known:
            if len(name) >= 2 and name in q:
                hits[name] = hits.get(name, 0) + 1
    if not hits:
        return None
    top, n = max(hits.items(), key=lambda kv: kv[1])
    return top if n >= min_hits else None


# ── 집계 (쿼리는 두 번뿐 — 사용자 수와 무관하다) ────────────────────────────

def _fmt_eok(v: float) -> str:
    return f"{v / 100_000_000:,.1f}억"


def collect(bq, table: str, base: date) -> Dict[str, Any]:
    """최근 7일 vs 직전 7일 — 팀별 합계와 국가별 변화.

    ⛔ **하루 대 하루로 비교하면 안 된다** (2026-08-20 실측). B2B·유통은 주문 단위라
       하루 매출 0이 정상이고, 그대로 알리면 "영업2팀 −100%"·"미국 −100%"·
       "아랍에미리트 +991%" 같은 문구가 나간다. 사용자가 묻지도 않았는데 먼저 간 숫자가
       이러면 첫날로 끝난다. 7일 합계는 요일 편차와 주문 단위 튐을 함께 흡수한다.
    ⚠️ 모든 구간이 기준일 이하다 — 미래 날짜(8,741건)가 섞이면 조용히 부푼다.
    """
    cur_from = base - timedelta(days=6)          # 기준일 포함 7일
    prev_to = cur_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=6)

    teams = bq.execute_query(f"""
        SELECT IFNULL(Team_NEW, '(없음)') AS team,
               SUM(IF(Date BETWEEN DATE '{cur_from}' AND DATE '{base}', Sales1_R, 0)) AS now_amt,
               SUM(IF(Date BETWEEN DATE '{prev_from}' AND DATE '{prev_to}', Sales1_R, 0)) AS prev_amt
        FROM `{table}`
        WHERE Date BETWEEN DATE '{prev_from}' AND DATE '{base}'
        GROUP BY team
    """)
    by_team = {r["team"]: {"now": float(r["now_amt"] or 0), "prev": float(r["prev_amt"] or 0)}
               for r in teams}

    countries = bq.execute_query(f"""
        SELECT IFNULL(Team_NEW, '(없음)') AS team, Country AS country,
               SUM(IF(Date BETWEEN DATE '{cur_from}' AND DATE '{base}', Sales1_R, 0)) AS now_amt,
               SUM(IF(Date BETWEEN DATE '{prev_from}' AND DATE '{prev_to}', Sales1_R, 0)) AS prev_amt
        FROM `{table}`
        WHERE Date BETWEEN DATE '{prev_from}' AND DATE '{base}' AND Country IS NOT NULL
        GROUP BY team, country
    """)
    by_country: Dict[str, Dict[str, float]] = {}
    for r in countries:
        slot = by_country.setdefault(r["country"], {"now": 0.0, "prev": 0.0})
        slot["now"] += float(r["now_amt"] or 0)
        slot["prev"] += float(r["prev_amt"] or 0)

    return {"base": base, "cur_from": cur_from, "prev_from": prev_from, "prev_to": prev_to,
            "by_team": by_team, "countries": countries, "by_country": by_country}


def collect_marketing(bq, table: str, base: date) -> Dict[str, Any]:
    """Collect ad spend and clicks on the scopes shared with sales."""
    cur_from = base - timedelta(days=6)
    prev_to = cur_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=6)
    rows = bq.execute_query(f"""
        SELECT IFNULL(team, '(없음)') AS team,
               IFNULL(country, '(없음)') AS country,
               SUM(IF(Date BETWEEN DATE '{cur_from}' AND DATE '{base}', cost_krw, 0)) AS now_cost,
               SUM(IF(Date BETWEEN DATE '{prev_from}' AND DATE '{prev_to}', cost_krw, 0)) AS prev_cost,
               SUM(IF(Date BETWEEN DATE '{cur_from}' AND DATE '{base}', clicks, 0)) AS now_clicks,
               SUM(IF(Date BETWEEN DATE '{prev_from}' AND DATE '{prev_to}', clicks, 0)) AS prev_clicks,
               SUM(IF(Date BETWEEN DATE '{cur_from}' AND DATE '{base}',
                      conversion_value_krw, 0)) AS now_conv,
               SUM(IF(Date BETWEEN DATE '{prev_from}' AND DATE '{prev_to}',
                      conversion_value_krw, 0)) AS prev_conv
        FROM `{table}`
        WHERE Date BETWEEN DATE '{prev_from}' AND DATE '{base}'
        GROUP BY team, country
    """)

    metric_names = ("now_cost", "prev_cost", "now_clicks", "prev_clicks",
                    "now_conv", "prev_conv")

    def add(target: Dict[str, float], row: Dict[str, Any]) -> None:
        for name in metric_names:
            target[name] = target.get(name, 0.0) + float(row.get(name) or 0)

    by_team: Dict[str, Dict[str, float]] = {}
    by_country: Dict[str, Dict[str, float]] = {}
    total: Dict[str, float] = {}
    for row in rows:
        add(by_team.setdefault(row["team"], {}), row)
        add(by_country.setdefault(row["country"], {}), row)
        add(total, row)

    return {
        "base": base, "cur_from": cur_from, "prev_from": prev_from, "prev_to": prev_to,
        "by_team": by_team, "by_country": by_country, "all": total,
        # ⛔ team×country 원본을 버리지 마라 — `by_country` 는 전사 집계라
        #    팀 화면에 남의 팀 국가가 뜬다 (2026-08-26 실측).
        "pairs": [{
            "team": r["team"], "country": r["country"],
            **{k: float(r.get(k) or 0) for k in metric_names},
        } for r in rows],
    }


def save_marketing_snapshot(data: Dict[str, Any]) -> bool:
    """Persist one grounded marketing snapshot for login cards.

    The snapshot is deliberately separate from ``daily_briefings``: those rows are
    notable-change alerts and may be suppressed for a week, while the login card
    still needs the latest available marketing metrics.
    """
    if not data or not data.get("base"):
        return False
    serializable = dict(data)
    for key in ("base", "cur_from", "prev_from", "prev_to"):
        value = serializable.get(key)
        if isinstance(value, (date, datetime)):
            serializable[key] = value.isoformat()
    payload = json.dumps(serializable, ensure_ascii=False, separators=(",", ":"))
    n = execute(
        "INSERT INTO briefing_marketing_snapshots (for_date, payload) VALUES (%s,%s) "
        "ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=NOW()",
        (data["base"], payload),
    )
    return bool(n)


def _serialize_snapshot(data: Dict[str, Any]) -> str:
    payload = dict(data)
    for key in ("base", "cur_from", "prev_from", "prev_to"):
        value = payload.get(key)
        if isinstance(value, (date, datetime)):
            payload[key] = value.isoformat()
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _read_snapshot(table: str) -> Optional[Dict[str, Any]]:
    try:
        row = fetch_one(f"SELECT payload FROM {table} ORDER BY for_date DESC LIMIT 1")
        if not row or not row.get("payload"):
            return None
        payload = row["payload"]
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        for key in ("base", "cur_from", "prev_from", "prev_to"):
            value = data.get(key)
            if isinstance(value, str):
                data[key] = date.fromisoformat(value)
        return data
    except Exception as e:
        logger.warning("briefing_snapshot_read_failed", table=table, error=type(e).__name__)
        return None


def save_sales_snapshot(data: Dict[str, Any]) -> bool:
    """매출 집계를 그대로 남긴다.

    ⛔ `daily_briefings` 로 대신할 수 없다 — 그쪽은 **알릴 만한 변화**만 담는 자리라
       조용한 날엔 행이 없고, 그러면 화면 지표에 며칠 전 수치가 붙는다.
       마케팅 스냅샷이 이미 같은 이유로 따로 있다.
    """
    if not data or not data.get("base"):
        return False
    ensure_tables()
    return bool(execute(
        "INSERT INTO briefing_sales_snapshots (for_date, payload) VALUES (%s,%s) "
        "ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=NOW()",
        (data["base"], _serialize_snapshot(data)),
    ))


def latest_sales_snapshot() -> Optional[Dict[str, Any]]:
    return _read_snapshot("briefing_sales_snapshots")


def latest_marketing_snapshot() -> Optional[Dict[str, Any]]:
    """Return the most recent saved marketing snapshot; fail open for the card."""
    try:
        row = fetch_one(
            "SELECT payload FROM briefing_marketing_snapshots "
            "ORDER BY for_date DESC LIMIT 1"
        )
        if not row or not row.get("payload"):
            return None
        payload = row["payload"]
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        for key in ("base", "cur_from", "prev_from", "prev_to"):
            value = data.get(key)
            if isinstance(value, str):
                data[key] = date.fromisoformat(value)
        return data
    except Exception as e:
        logger.warning("briefing_marketing_snapshot_read_failed", error=type(e).__name__)
        return None


def refresh_marketing_snapshot(bq=None) -> Dict[str, Any]:
    """Collect and persist marketing only, without creating alerts or sending mail."""
    if bq is None:
        from app.core.bigquery import BigQueryClient

        bq = BigQueryClient()
    ensure_tables()
    base = stable_date(bq, _MARKETING_TABLE)
    if not base:
        return {}
    data = collect_marketing(bq, _MARKETING_TABLE, base)
    save_marketing_snapshot(data)
    return data


def _pct(now: float, prev: float) -> Optional[float]:
    if not prev:
        return None
    return (now - prev) / prev * 100.0


def _qualified_changes(rows: List[Dict[str, Any]], team: Optional[str],
                       team_now: float = 0.0) -> List[Dict[str, Any]]:
    """규모·비중·변화율을 모두 넘은 국가 변화 후보."""
    candidates = []
    for r in rows:
        if team and r["team"] != team:
            continue
        now, prev = float(r["now_amt"] or 0), float(r["prev_amt"] or 0)
        if now < _MIN_SCALE_KRW or prev < _MIN_SCALE_KRW:
            continue
        p = _pct(now, prev)
        if p is None or abs(p) < _CHANGE_MIN_PCT:
            continue
        if team_now and now >= team_now * 0.85:
            continue          # 팀 합계와 같은 말 — 새 정보가 없다
        delta = now - prev
        if team_now and abs(delta) < team_now * _CHANGE_MIN_SHARE:
            continue          # 축 규모에 비해 미미하다
        candidates.append({"country": r["country"], "now": now, "prev": prev,
                           "pct": p, "delta": delta})
    return candidates


def _top_change(rows: List[Dict[str, Any]], team: Optional[str],
                team_now: float = 0.0) -> Optional[Dict[str, Any]]:
    """**가장 크게 움직인** 나라 하나 — 변화율이 아니라 **금액**으로 고른다.

    ⛔ 변화율로 고르면 작은 나라의 배수가 이긴다. 실측(2026-08-21): 영국 +733%(+10.8억)가
       뽑혔지만 실제로 가장 크게 움직인 것은 아랍에미리트 −12.9억(16.8억→3.9억)이었다.
       "가장 눈에 띄는 변화" 라고 적어 놓고 가장 눈에 띄지 않는 것을 고르고 있었다.

    후보 자격 세 가지를 모두 넘어야 한다:
      ① 이번·직전 **양쪽 다** 최소 규모(1억) 이상 — 한쪽만 보면 "0원 → −100%" 가 이긴다
      ② 증감액이 **축 합계의 5% 이상** — 전사 143억에서 1억 움직임은 눈에 띄지 않는다
      ③ 그 나라 기준으로도 10% 이상 — 큰 나라가 미미하게 흔들린 것을 제외한다
    """
    candidates = _qualified_changes(rows, team, team_now)
    return max(candidates, key=lambda x: abs(x["delta"])) if candidates else None


def _watch_items(rows: List[Dict[str, Any]], team: Optional[str],
                 scope_now: float, limit: int = 2) -> List[Dict[str, Any]]:
    """지금 주시할 변화. 하락 위험을 먼저, 큰 반등 기회를 그다음에 둔다."""
    candidates = _qualified_changes(rows, team, scope_now)
    risks = sorted((x for x in candidates if x["delta"] < 0),
                   key=lambda x: abs(x["delta"]), reverse=True)
    opportunities = sorted((x for x in candidates if x["delta"] > 0),
                           key=lambda x: abs(x["delta"]), reverse=True)
    chosen: List[Dict[str, Any]] = []
    if risks:
        chosen.append(risks.pop(0))
    if opportunities and len(chosen) < limit:
        chosen.append(opportunities.pop(0))
    remaining = sorted(risks + opportunities,
                       key=lambda x: abs(x["delta"]), reverse=True)
    return (chosen + remaining)[:limit]


def _marketing_slot(scope: Dict[str, str], data: Optional[Dict[str, Any]]):
    """(슬롯, 기간표기, 기준일). **수치를 고르는 자리는 여기 하나뿐이다** —
    문장용(`_marketing_line`)과 구조용(`marketing_item`)이 같은 값을 보게 한다."""
    if not data:
        return None, "", None
    base = data["base"]
    cur_from = data["cur_from"]
    span = f"{cur_from.month}/{cur_from.day}~{base.month}/{base.day}"
    if scope["kind"] == "team":
        slot = data.get("by_team", {}).get(scope["code"])
    elif scope["kind"] == "country":
        slot = data.get("by_country", {}).get(scope["code"])
    else:
        slot = data.get("all")
    return slot, span, base


def _marketing_line(scope: Dict[str, str], data: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return a grounded ad-spend/click snapshot for the user's existing scope."""
    slot, span, base = _marketing_slot(scope, data)
    if base is None:
        return None
    if not slot:
        return f"· 마케팅 · {span} 광고 집행 기록 없음"

    now_cost = float(slot.get("now_cost") or 0)
    prev_cost = float(slot.get("prev_cost") or 0)
    pct = _pct(now_cost, prev_cost)
    move = "직전 7일 대비 —" if pct is None else f"직전 7일 대비 {pct:+.0f}%"
    clicks = int(round(float(slot.get("now_clicks") or 0)))
    return (f"· 마케팅 · {span} 광고비 {_fmt_eok(now_cost)} "
            f"({move}, 직전 7일 {_fmt_eok(prev_cost)}) · 클릭 {clicks:,}회")


#: 광고비 규모 하한. 매출(1억)보다 작다 — 전사 광고비가 7일에 4억 규모다.
_AD_MIN_SCALE_KRW = 10_000_000


def _contribution(delta: float, total_delta: float) -> str:
    """이 변화가 축 전체 변화에서 차지하는 몫.

    ⛔ 방향이 반대면 몫을 적지 마라 — 전사가 늘었는데 이 나라만 줄었을 때
       "감소분의 32%" 는 거짓이다. 그럴 땐 반대로 움직였다는 사실을 말한다.
    """
    if not total_delta:
        return ""
    if (delta >= 0) != (total_delta >= 0):
        return "전체와 반대 방향"
    share = abs(delta) / abs(total_delta)
    if share > 1.2:
        # 이 축 하나가 전체 증감분보다 크다 = 다른 곳이 반대로 움직였다는 뜻이다.
        # 그냥 "155%" 라고 적으면 읽는 사람이 그 뜻을 다시 풀어야 한다.
        return "증감분보다 큼 · 다른 곳은 반대로"
    word = "증가분" if total_delta >= 0 else "감소분"
    return f"{word}의 {share * 100:.0f}%"


def _ad_by_country(data: Optional[Dict[str, Any]], scope: Dict[str, str]) -> dict:
    """그 축 **안에서만** 국가별로 합친 광고 지표.

    ⛔ `by_country` 를 그냥 쓰면 전사 집계라 팀 화면에 남의 팀 국가가 뜬다.
    ⚠️ 국가 축은 쪼갤 것이 없다 — 빈 dict 를 돌려준다.
    """
    if not data or scope["kind"] == "country":
        return {}
    pairs = data.get("pairs")
    if pairs is None:
        # 옛 스냅샷에는 원본이 없다. 전사일 때만 by_country 로 대신한다 —
        # 팀 축에서 쓰면 틀린 답을 자신 있게 낸다.
        return dict(data.get("by_country") or {}) if scope["kind"] == "all" else {}
    team = scope["code"] if scope["kind"] == "team" else None
    out: dict = {}
    for row in pairs:
        if team and row.get("team") != team:
            continue
        slot = out.setdefault(row["country"], {})
        for key in ("now_cost", "prev_cost", "now_conv", "prev_conv"):
            slot[key] = slot.get(key, 0.0) + float(row.get(key) or 0)
    return out


def _movers(by_axis: dict, now_key: str, prev_key: str, floor: float) -> list:
    """규모 하한을 넘고 10% 이상 움직인 축을 **증감액** 큰 순으로.

    ⛔ 변화율로 정렬하지 마라 — 작은 축의 배수가 이긴다 (`_top_change` 주석의 실제 사고).
    """
    rows = []
    for name, slot in (by_axis or {}).items():
        now = float(slot.get(now_key) or 0)
        prev = float(slot.get(prev_key) or 0)
        if now < floor or prev < floor:
            continue
        pct = _pct(now, prev)
        if pct is None or abs(pct) < _CHANGE_MIN_PCT:
            continue
        rows.append({"name": name, "now": now, "prev": prev,
                     "pct": pct, "delta": now - prev})
    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    return rows


def sales_item(scope_label: str,
               data: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """**가장 크게 움직인 국가** 하나 + 그것이 축 전체 증감에서 차지하는 몫.

    ⛔ 합계만 적으면 "무슨 일이 있었나" 를 말하지 못한다 (2026-08-26 사용자 요청).
    ⛔ 저장된 알림이 아니라 **최신 집계**에서 만든다 — 조용한 날엔 알림 행이 없다.
    """
    if not data or not data.get("base"):
        return None
    scope = _scope_from_saved_label(scope_label)
    base, cur_from = data["base"], data["cur_from"]
    span = f"{cur_from.month}/{cur_from.day}~{base.month}/{base.day}"
    if scope["kind"] == "country":
        slot = data.get("by_country", {}).get(scope["code"]) or {}
    elif scope["kind"] == "team":
        slot = data.get("by_team", {}).get(scope["code"]) or {}
    else:
        teams = (data.get("by_team") or {}).values()
        slot = {"now": sum(float(v.get("now") or 0) for v in teams),
                "prev": sum(float(v.get("prev") or 0) for v in teams)}
    now, prev = float(slot.get("now") or 0), float(slot.get("prev") or 0)
    pct = _pct(now, prev)
    move = "—" if pct is None else f"{pct:+.0f}%"
    summary = f"{scope['label']} 전체 {_fmt_eok(now)} ({move}, 직전 {_fmt_eok(prev)})"

    # ⛔ 국가 축이면 그 나라 자체가 주제다 — 다른 나라 변화를 뽑으면 축을 벗어난다
    #    (`compose()` 가 이미 지키는 규칙이다). 실측: `말레이시아` 축에 호주가 떴다.
    team = scope["code"] if scope["kind"] == "team" else None
    top = (None if scope["kind"] == "country"
           else _top_change(data.get("countries") or [], team, now))
    if not top:
        return {
            "for_date": str(base),
            "title": f"{scope['label']} 매출 {_fmt_eok(now)} · 직전 7일 대비 {move}",
            # 없는 것을 지어내지 않는다 — 기준을 함께 밝혀 "왜 없나" 에 답한다.
            "body": f"{span} · 눈에 띄게 움직인 국가 없음 (1억·10%·축의 5% 기준)",
            "follow_up": f"{scope['label']} 최근 매출 추이를 보여줘",
        }
    share = _contribution(top["delta"], now - prev)
    return {
        "for_date": str(base),
        "title": (f"{top['country']} {_fmt_eok(top['prev'])} → {_fmt_eok(top['now'])} "
                  f"({top['delta'] / 100_000_000:+,.1f}억, {top['pct']:+.0f}%)"),
        "body": f"{span} · {summary}" + (f" · {share}" if share else ""),
        "follow_up": f"{top['country']} 매출이 왜 이렇게 움직였는지 분석해줘",
    }


def marketing_item(scope_label: str,
                   data: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """광고비가 **가장 크게 움직인 국가** + 그것이 축 전체 증감에서 차지하는 몫.

    ⚠️ 기준일이 매출과 다를 수 있다 (광고 적재가 하루 빠르다) — 자기 기준일을 함께 준다.
    """
    scope = _scope_from_saved_label(scope_label)
    slot, span, base = _marketing_slot(scope, data)
    if base is None:
        return None
    if not slot:
        return {"for_date": str(base), "title": f"광고 집행 기록 없음 ({span})",
                "body": "", "follow_up": ""}

    now_cost = float(slot.get("now_cost") or 0)
    prev_cost = float(slot.get("prev_cost") or 0)
    pct = _pct(now_cost, prev_cost)
    move = "—" if pct is None else f"{pct:+.0f}%"
    clicks = int(round(float(slot.get("now_clicks") or 0)))
    summary = (f"{scope['label']} 광고비 {_fmt_eok(now_cost)} ({move}) "
               f"· 클릭 {clicks:,}회")

    # 축 안에서 가장 크게 움직인 국가. 전사 축일 때만 국가로 쪼갠다.
    movers = _movers(_ad_by_country(data, scope), "now_cost", "prev_cost",
                     _AD_MIN_SCALE_KRW)
    if not movers:
        return {
            "for_date": str(base),
            "title": f"광고비 {_fmt_eok(now_cost)} · 직전 7일 대비 {move}",
            "body": f"{span} · 클릭 {clicks:,}회 · 직전 7일 {_fmt_eok(prev_cost)}",
            "follow_up": f"{scope['label']} 광고비 추이를 국가별로 보여줘",
        }
    top = movers[0]
    share = _contribution(top["delta"], now_cost - prev_cost)
    return {
        "for_date": str(base),
        "title": (f"{top['name']} 광고비 {_fmt_eok(top['prev'])} → {_fmt_eok(top['now'])} "
                  f"({top['pct']:+.0f}%)"),
        "body": f"{span} · {summary}" + (f" · {share}" if share else ""),
        "follow_up": f"{top['name']} 광고비와 매출을 함께 보여줘",
    }


def roas_item(scope_label: str,
              data: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """광고 효율이 가장 높은 곳과 낮은 곳 — 합계 하나로는 어디를 손댈지 알 수 없다.

    ⛔ **실매출이 아니다.** `conversion_value_krw` 는 광고 플랫폼이 스스로 집계한
       전환매출이고 `Sales1_R` 과 다르다 (저장소 데이터 규칙).
    ⛔ **전환매출 0 을 '최악' 으로 줄 세우지 마라** — Meta·Tiktok 은 전환 추적이 없으면
       0 으로 들어온다. 매출이 없다는 뜻이 아니다. 순위에서 빼고 **뺀 사실을 적는다.**
    """
    scope = _scope_from_saved_label(scope_label)
    slot, span, base = _marketing_slot(scope, data)
    if base is None or not slot:
        return None
    cost = float(slot.get("now_cost") or 0)
    if cost <= 0:
        return None
    conv = float(slot.get("now_conv") or 0)
    follow_up = f"{scope['label']} 광고비 대비 전환매출을 국가별로 비교해줘"
    if conv <= 0:
        return {
            "for_date": str(base),
            "title": "광고 전환 ROAS 산출 불가 · 전환 추적 없음",
            "body": f"{span} · 광고비 {_fmt_eok(cost)} · 플랫폼 전환매출 0원 "
                    f"(Meta·Tiktok 은 추적이 없으면 0 으로 들어온다)",
            "follow_up": follow_up,
        }
    overall = conv / cost

    ranked, skipped = [], 0
    for name, row in _ad_by_country(data, scope).items():
        row_cost = float(row.get("now_cost") or 0)
        row_conv = float(row.get("now_conv") or 0)
        if row_cost < _AD_MIN_SCALE_KRW:
            continue
        if row_conv <= 0:
            skipped += 1        # 추적 없음 — 0 으로 줄 세우면 '최악' 으로 읽힌다
            continue
        ranked.append({"name": name, "roas": row_conv / row_cost, "cost": row_cost})
    ranked.sort(key=lambda r: r["roas"], reverse=True)

    note = f"{span} · {scope['label']} 전체 {overall:.2f} · 플랫폼 자체 집계값"
    if skipped:
        note += f" · 전환 추적 없는 {skipped}곳 제외"
    if len(ranked) < 2:
        return {
            "for_date": str(base),
            "title": f"광고 전환 ROAS {overall:.2f}",
            "body": note,
            "follow_up": follow_up,
        }
    best, worst = ranked[0], ranked[-1]
    return {
        "for_date": str(base),
        "title": (f"ROAS 최고 {best['name']} {best['roas']:.1f} "
                  f"· 최저 {worst['name']} {worst['roas']:.1f}"),
        "body": note,
        "follow_up": follow_up,
    }


def sales_body(body: str) -> str:
    """저장된 본문에서 **마케팅 줄을 뺀 매출 부분**만. 둘을 따로 보여주기 위한 것이다."""

    return "\n".join(
        part for part in (body or "").splitlines() if not part.startswith("· 마케팅 ·")
    ).strip()


def _scope_from_saved_label(label: str) -> Dict[str, str]:
    """Recreate the scope stored with an existing business-alert row."""
    clean = (label or "").replace(" ", "")
    if not clean or clean == "전사":
        return {"kind": "all", "code": "", "label": "전사"}
    from app.agents.sql_agent import TEAM_CODE2KR

    for code, korean in TEAM_CODE2KR.items():
        if korean.replace(" ", "") == clean:
            return {"kind": "team", "code": code, "label": korean}
    return {"kind": "country", "code": label, "label": label}


def enrich_body_with_marketing(body: str, scope_label: str,
                               data: Optional[Dict[str, Any]]) -> str:
    """Add or replace the marketing line in a saved business-alert body."""
    line = _marketing_line(_scope_from_saved_label(scope_label), data)
    if not line:
        return body or ""
    lines = [part for part in (body or "").splitlines()
             if not part.startswith("· 마케팅 ·")]
    insert_at = next(
        (index for index, part in enumerate(lines) if part.startswith("· 기준일은")),
        len(lines),
    )
    lines.insert(insert_at, line)
    return "\n".join(lines)


def compose(scope: Dict[str, str], data: Dict[str, Any],
            marketing: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
    """브리핑 한 건. **알릴 만한 변화가 없으면 None** — 안 보내는 것이 기본이다."""
    team = scope["code"] if scope["kind"] == "team" else None
    if scope["kind"] == "country":
        slot = data["by_country"].get(scope["code"]) or {"now": 0.0, "prev": 0.0}
    elif team:
        slot = data["by_team"].get(team) or {"now": 0.0, "prev": 0.0}
    else:
        slot = {"now": sum(v["now"] for v in data["by_team"].values()),
                "prev": sum(v["prev"] for v in data["by_team"].values())}
    now, prev = slot["now"], slot["prev"]
    # ⛔ **이번 기간에 규모가 없으면 어떤 축이든 보내지 않는다.**
    #    2026-08-21 실제 사고: 인도가 관심 축으로 뽑혔는데 최근 7일 매출이 0원이라
    #    "인도 최근 7일 매출 0.0억 · 직전 7일 대비 -100%" 가 사용자에게 나갔다.
    #    팀 축에만 하한을 걸고 국가 축에는 안 건 것이 원인이다 — 여기서 **한 번에** 막는다.
    #    직전이 컸어도(1.5억 → 0원) 마찬가지다. 0원짜리는 브리핑이 아니라 소음이다.
    if now < _MIN_SCALE_KRW:
        return None

    pct = _pct(now, prev)
    # 국가 축이면 그 나라 자체가 주제다 — 변화 항목을 또 뽑으면 같은 말이 된다.
    # 팀·전사는 국가 변화를 주시 항목으로 만든다. 합계가 평평해도 국가별 급락과
    # 반등이 상쇄된 날은 조용한 날이 아니다.
    if scope["kind"] == "country":
        watch = []
    elif team:
        watch = _watch_items(data["countries"], team, now)
    else:
        # ⛔ 전사는 국가 **합계**로 본다. 팀×국가 셀 단위로 고르면 "전사 브리핑인데
        #    한 팀의 호주" 가 뽑힌다 — 같은 나라가 팀별로 쪼개져 진짜 큰 이동을 놓친다
        #    (2026-08-21 실측: 아랍에미리트 −12.9억을 놓치고 호주 +12.7억을 골랐다).
        watch = _watch_items(
            [{"team": None, "country": c, "now_amt": v["now"], "prev_amt": v["prev"]}
             for c, v in data["by_country"].items()], None, now)
    notable = (pct is not None and abs(pct) >= _MIN_DELTA_PCT) or bool(watch)
    if not notable:
        return None                     # 평소와 같은 날은 보내지 않는다

    base, label = data["base"], scope["label"]
    span = f"{data['cur_from'].month}/{data['cur_from'].day}~{base.month}/{base.day}"
    move = "직전 7일 대비 —" if pct is None else f"직전 7일 대비 {pct:+.0f}%"
    if watch:
        primary = watch[0]
        direction = "하락" if primary["delta"] < 0 else "반등"
        title = (f"{label} 주시 · {primary['country']} {direction} "
                 f"{_fmt_eok(primary['prev'])} → {_fmt_eok(primary['now'])} "
                 f"({primary['pct']:+.0f}%)")
    else:
        direction = "하락" if (pct or 0) < 0 else "반등"
        title = f"{label} 주시 · 최근 7일 매출 {direction} {_fmt_eok(prev)} → {_fmt_eok(now)} ({pct:+.0f}%)"

    lines = []
    for item in watch:
        tag = "주의" if item["delta"] < 0 else "기회"
        signed_delta = (_fmt_eok(item["delta"]) if item["delta"] < 0
                        else "+" + _fmt_eok(item["delta"]))
        lines.append(f"· {tag}: {item['country']} {_fmt_eok(item['prev'])} → "
                     f"{_fmt_eok(item['now'])} ({signed_delta}, {item['pct']:+.0f}%)")
    if not watch:
        tag = "주의" if (pct or 0) < 0 else "기회"
        lines.append(f"· {tag}: {label} 최근 7일 매출 {_fmt_eok(prev)} → "
                     f"{_fmt_eok(now)} ({pct:+.0f}%)")
    lines.append(f"· {label} 전체 · {span} 합계 {_fmt_eok(now)} ({move}, 직전 7일 {_fmt_eok(prev)})")
    marketing_line = _marketing_line(scope, marketing)
    if marketing_line:
        lines.append(marketing_line)
    lines.append("· 기준일은 데이터가 안정적으로 들어온 마지막 날입니다 "
                 f"({base} — 적재 지연으로 최근 1~2일은 제외).")

    follow = (f"{watch[0]['country']} 최근 2주 매출 추이 보여줘" if watch
              else f"{label} 최근 7일 채널별 매출 알려줘" if scope["kind"] == "country"
              else f"{label} 최근 7일 국가별 매출 알려줘")
    return {"title": title[:300], "body": "\n".join(lines), "follow_up": follow[:300]}


# ── 저장·조회 ────────────────────────────────────────────────────────────────

_STORY_NUMBER = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?(?:억|%|원)?")


def _story_signature(title: str) -> str:
    """금액·비율만 바뀐 제목을 같은 이야기로 묶는다."""
    without_numbers = _STORY_NUMBER.sub("#", title or "")
    return " ".join(without_numbers.split())


def is_repeat(user_id: int, title: str, for_date: Optional[date] = None) -> bool:
    """최근 비교 주기 안에 보낸 것과 **같은 축·나라·방향의 이야기**인가.

    ⛔ 제목 완전 일치만 보면 금액이 조금씩 달라지는 같은 추세가 매일 새 알림으로 간다.
       숫자를 제외한 의미가 같으면 7일 동안 쉬고, 하락→반등처럼 방향이 바뀌면 알린다.
    """
    if for_date:
        rows = fetch_all(
            "SELECT title FROM daily_briefings WHERE user_id = %s "
            f"AND for_date >= DATE_SUB(%s, INTERVAL {_REPEAT_COOLDOWN_DAYS - 1} DAY) "
            "ORDER BY for_date DESC",
            (int(user_id), for_date)) or []
    else:
        rows = fetch_all(
            "SELECT title FROM daily_briefings WHERE user_id = %s "
            "ORDER BY for_date DESC LIMIT 7", (int(user_id),)) or []
    signature = _story_signature(title)
    return any(_story_signature(r.get("title") or "") == signature for r in rows)


def save(user_id: int, for_date: date, scope: str, b: Dict[str, str],
         notified: bool = True) -> bool:
    """하루 한 건. 같은 날 두 번 돌아도 덮어쓰기만 한다 (중복 알림 방지).

    ``notified=False`` 는 **알림은 보내지 않되 화면 지표로는 쓴다**는 뜻이다 —
    같은 이야기가 이어져도 수치는 매일 새로워야 한다.
    """
    ensure_tables()
    n = execute(
        "INSERT INTO daily_briefings "
        "(user_id, for_date, scope, title, body, follow_up, notified) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE title=VALUES(title), body=VALUES(body), "
        "follow_up=VALUES(follow_up), scope=VALUES(scope), "
        # ⚠️ 알림 플래그는 **올리기만** 한다. 같은 날 두 번 돌 때 이미 알린 것을
        #    조용한 갱신이 0 으로 내리면 알림함에서 사라진다.
        "notified=GREATEST(notified, VALUES(notified))",
        (int(user_id), for_date, scope, b["title"], b["body"], b["follow_up"],
         1 if notified else 0))
    return bool(n)


def latest_for_user(user_id: int) -> Optional[Dict[str, Any]]:
    """화면 지표용 — **알림 여부와 무관하게** 가장 최근 것 하나.

    ⛔ `for_user()` 를 쓰지 마라. 그쪽은 알림함용이라 조용한 갱신분이 빠진다.
    """
    ensure_tables()
    return fetch_one(
        "SELECT id, for_date, scope, title, body, follow_up, created_at, seen_at, notified "
        "FROM daily_briefings WHERE user_id = %s ORDER BY for_date DESC LIMIT 1",
        (int(user_id),))


def for_user(user_id: int, limit: int = 7) -> List[Dict[str, Any]]:
    return fetch_all(
        # ⛔ 조용한 갱신분(notified=0)은 알림함에 올리지 않는다 — 매일 같은 알림은
        #    곧 무시당하고, 정작 답을 기다리던 공유·붐따 회신까지 함께 묻힌다.
        "SELECT id, for_date, scope, title, body, follow_up, created_at, seen_at "
        "FROM daily_briefings WHERE user_id = %s AND notified = 1 "
        "ORDER BY for_date DESC LIMIT %s",
        (int(user_id), int(limit))) or []


def mark_seen(user_id: int) -> int:
    return int(execute("UPDATE daily_briefings SET seen_at = NOW() "
                       "WHERE user_id = %s AND seen_at IS NULL", (int(user_id),)) or 0)


# ── 매일 실행 ────────────────────────────────────────────────────────────────

def run_daily() -> Dict[str, Any]:
    """전 사용자 브리핑 생성. 집계 쿼리 수는 사용자 수와 무관하게 고정된다."""
    from app.agents.sql_agent import TEAM_CODE2KR
    from app.config import get_settings
    from app.core.bigquery import BigQueryClient

    ensure_tables()
    ensure_optout_column()
    s = get_settings()
    bq = BigQueryClient()
    table = s.sales_table_full_path

    base = stable_date(bq, table)
    if not base:
        logger.warning("briefing_no_stable_date", table=table)
        return {"error": "기준일을 정할 수 없음", "made": 0}

    data = collect(bq, table, base)
    try:
        save_sales_snapshot(data)
    except Exception as e:
        # 스냅샷 실패가 알림 생성을 막지 않는다.
        logger.warning("briefing_sales_snapshot_failed", error=type(e).__name__)
    marketing: Dict[str, Any] = {}
    try:
        marketing = refresh_marketing_snapshot(bq)
    except Exception as e:
        # 광고 집계 장애가 검증된 매출 브리핑까지 숨기면 안 된다.
        logger.warning("briefing_marketing_failed", error=type(e).__name__)
    # ⚠️ 퇴사자에게 매일 매출 브리핑을 보내지 않는다 (사용자 부서로 걸러진다)
    users = fetch_all(
        "SELECT u.id, u.display_name, COALESCE(a.email, u.email) AS email, "
        "       COALESCE(a.department, '') AS department "
        "FROM users u LEFT JOIN directory_users a ON a.id = u.ad_user_id "
        "WHERE u.is_active = 1 AND COALESCE(u.briefing_opt_out, 0) = 0 "
        "  AND COALESCE(a.department,'') NOT LIKE %s", ("%퇴사%",)) or []

    try:
        from app.core.value_lists import _cached
        known_countries = [c for c in (_cached("Country") or []) if len(c) >= 2]
    except Exception:
        known_countries = []

    made = skipped = mailed = 0
    for u in users:
        scope = resolve_scope(u["department"], TEAM_CODE2KR)
        if scope["kind"] == "all":
            # 팀이 매출 축에 없으면 **실제로 묻던 나라**로 좁힌다.
            # ⚠️ 단 그 나라에 **최근 매출이 실제로 있을 때만** 채택한다. 자주 물었다고
            #    매출이 있는 것은 아니다 — 인도가 그래서 0원 브리핑이 됐다 (2026-08-21).
            c = infer_country(u.get("email") or "", known_countries)
            if c and (data["by_country"].get(c, {}).get("now", 0) >= _MIN_SCALE_KRW):
                scope = {"kind": "country", "code": c, "label": c}
        b = compose(scope, data, marketing)
        if not b:
            skipped += 1
            continue
        # 같은 이야기가 이어지면 **알리지는 않되 저장은 한다** — 첫 화면 지표는
        # 알림이 아니라 대시보드라 수치가 매일 새로워야 한다 (2026-08-26).
        quiet = is_repeat(u["id"], b["title"], base)
        save(u["id"], base, scope["label"], b, notified=not quiet)
        if quiet:
            skipped += 1
            continue
        made += 1
        try:
            from app.core import mailer
            if mailer.is_enabled() and u.get("email"):
                if mailer.send(u["email"], f"[Cella] {b['title']}",
                               b["body"] + f"\n\n이어서 물어보기: {b['follow_up']}\n"
                                           f"{s.public_base_url}\n— Cella (회신하지 마세요)"):
                    mailed += 1
        except Exception as e:
            logger.warning("briefing_mail_failed", user_id=u["id"],
                           error=f"{type(e).__name__}: {str(e)[:150]}")

    logger.info("briefing_daily_done", base=str(base), made=made,
                skipped=skipped, mailed=mailed, users=len(users))
    return {"base": str(base), "made": made, "skipped": skipped,
            "mailed": mailed, "users": len(users)}


# ── 수신 거부 ────────────────────────────────────────────────────────────────
# ⛔ 끌 수 없는 알림은 결국 **전체 알림을 무시하게** 만든다. 브리핑이 매일 배지를
#    띄우는 동안 공유·붐따 회신까지 함께 묻히면, 정작 답을 기다리던 사람이 못 본다.

def ensure_optout_column() -> None:
    try:
        if not fetch_one(
            "SELECT 1 AS ok FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
            "AND COLUMN_NAME = 'briefing_opt_out'"):
            execute("ALTER TABLE users ADD COLUMN briefing_opt_out TINYINT NOT NULL DEFAULT 0")
            logger.info("users_briefing_opt_out_added")
    except Exception as e:
        logger.debug("optout_column_skip", error=str(e)[:120])


def set_opt_out(user_id: int, off: bool) -> None:
    ensure_optout_column()
    execute("UPDATE users SET briefing_opt_out = %s WHERE id = %s",
            (1 if off else 0, int(user_id)))
    logger.info("briefing_opt_out_changed", user_id=user_id, off=off)


def is_opted_out(user_id: int) -> bool:
    ensure_optout_column()
    r = fetch_one("SELECT briefing_opt_out o FROM users WHERE id = %s", (int(user_id),))
    return bool(r and r.get("o"))


# ── 효과 측정 ────────────────────────────────────────────────────────────────
# ⛔ "자주 쓰게 만든다" 가 목적인데 측정이 없으면 성공·실패를 알 수 없다.
#    감으로 "쓰는 것 같다" 고 말하게 된다 — 이 저장소가 피해 온 방식이다.

def effect_stats(days: int = 7) -> Dict[str, Any]:
    """보낸 것 대비 **열어봤는가**, 그리고 **질문으로 이어졌는가**."""
    sent = fetch_one(
        "SELECT COUNT(*) c, SUM(seen_at IS NOT NULL) seen FROM daily_briefings "
        "WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)", (int(days),)) or {}
    total = int(sent.get("c") or 0)
    seen = int(sent.get("seen") or 0)
    # 브리핑을 받은 날, 그 사람이 질문도 했는가 (전환)
    conv = fetch_one(
        "SELECT COUNT(DISTINCT b.user_id) c FROM daily_briefings b "
        "JOIN users u ON u.id = b.user_id "
        "JOIN audit_logs a ON a.user_email = u.email "
        "  AND DATE(a.created_at) = DATE(b.created_at) "
        "WHERE b.created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)", (int(days),)) or {}
    people = fetch_one(
        "SELECT COUNT(DISTINCT user_id) c FROM daily_briefings "
        "WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)", (int(days),)) or {}
    n_people = int(people.get("c") or 0)
    return {
        "days": days, "sent": total, "seen": seen,
        "seen_pct": round(seen / total * 100, 1) if total else 0.0,
        "people": n_people, "asked_same_day": int(conv.get("c") or 0),
        "conversion_pct": round(int(conv.get("c") or 0) / n_people * 100, 1) if n_people else 0.0,
    }
