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

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import structlog

from app.db.mariadb import execute, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

# 브리핑을 보낼 최소 변화폭 — 이보다 작으면 "평소와 같다" 로 보고 보내지 않는다
# ⚠️ 임계가 낮으면 매일 뜬다 — 매일 뜨는 알림은 곧 무시당한다. 실측(2026-08-20)에서
#    주간 변동은 대부분 ±20% 안팎이라 15% 를 넘으면 "말할 만한 변화" 로 본다
_MIN_DELTA_PCT = 15.0
# 변화 항목(국가)으로 뽑을 최소 규모 — ⛔ 규모 하한이 없으면 러시아 +183% 같은
#    작은 숫자가 1위로 올라온다 (보고서 판정 계층에서 이미 겪은 실패)
_MIN_SCALE_KRW = 30_000_000     # 0.3억


_DDL = """
CREATE TABLE IF NOT EXISTS daily_briefings (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    for_date   DATE NOT NULL,
    scope      VARCHAR(40) NOT NULL DEFAULT '',
    title      VARCHAR(300) NOT NULL,
    body       TEXT NULL,
    follow_up  VARCHAR(300) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    seen_at    DATETIME NULL,
    UNIQUE KEY uq_user_date (user_id, for_date),
    INDEX idx_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_tables() -> None:
    try:
        execute(_DDL)
    except Exception as e:
        logger.debug("briefing_ddl_skip", error=str(e)[:120])


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

    AD 부서 문자열에 공식 팀명이 들어 있으면 그 팀. ⚠️ 표기 공백이 다르다
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


def _pct(now: float, prev: float) -> Optional[float]:
    if not prev:
        return None
    return (now - prev) / prev * 100.0


def _top_change(rows: List[Dict[str, Any]], team: Optional[str],
                team_now: float = 0.0) -> Optional[Dict[str, Any]]:
    """가장 눈에 띄는 국가 변화 하나. ⛔ 규모 하한을 반드시 건다.

    ⚠️ 팀이 사실상 한 나라만 담당하면(일본사업팀 → 일본) 그 나라는 팀 합계와 **같은 말**이다.
       "일본사업팀 +77% · 눈에 띄는 변화: 일본 +77%" 는 한 줄을 두 번 쓴 것이다.
    """
    best = None
    for r in rows:
        if team and r["team"] != team:
            continue
        now, prev = float(r["now_amt"] or 0), float(r["prev_amt"] or 0)
        # ⛔ **양쪽 다** 하한을 넘어야 한다. 한쪽만 보면 "이번 주 0원 → −100%" 가
        #    1위로 올라온다 — 규모가 없는 변화는 변화가 아니다
        if now < _MIN_SCALE_KRW or prev < _MIN_SCALE_KRW:
            continue
        p = _pct(now, prev)
        if p is None:
            continue
        if team_now and now >= team_now * 0.85:
            continue          # 팀 합계와 같은 말 — 새 정보가 없다
        if best is None or abs(p) > abs(best["pct"]):
            best = {"country": r["country"], "now": now, "prev": prev, "pct": p}
    return best


def compose(scope: Dict[str, str], data: Dict[str, Any]) -> Optional[Dict[str, str]]:
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
    # 기준 기간에 기록이 없으면 보내지 않는다 — "0원 · −100%" 는 알림이 아니라 소음이다
    if now < _MIN_SCALE_KRW and prev < _MIN_SCALE_KRW:
        return None

    pct = _pct(now, prev)
    # 국가 축이면 그 나라 자체가 주제다 — 변화 항목을 또 뽑으면 같은 말이 된다
    change = None if scope["kind"] == "country" else _top_change(data["countries"], team, now)
    notable = (pct is not None and abs(pct) >= _MIN_DELTA_PCT) or \
              (change is not None and abs(change["pct"]) >= _MIN_DELTA_PCT * 2)
    if not notable:
        return None                     # 평소와 같은 날은 보내지 않는다

    base, label = data["base"], scope["label"]
    span = f"{data['cur_from'].month}/{data['cur_from'].day}~{base.month}/{base.day}"
    move = "직전 7일 대비 —" if pct is None else f"직전 7일 대비 {pct:+.0f}%"
    title = f"{label} 최근 7일 매출 {_fmt_eok(now)} · {move}"

    lines = [f"· {label} 기준 · {span} 합계 {_fmt_eok(now)} ({move}, 직전 7일 {_fmt_eok(prev)})"]
    if change:
        lines.append(f"· 눈에 띄는 변화: {change['country']} {_fmt_eok(change['now'])} "
                     f"({change['pct']:+.0f}%, 직전 7일 {_fmt_eok(change['prev'])})")
    lines.append("· 기준일은 데이터가 안정적으로 들어온 마지막 날입니다 "
                 f"({base} — 적재 지연으로 최근 1~2일은 제외).")

    follow = (f"{label} 최근 7일 채널별 매출 알려줘" if scope["kind"] == "country"
              else f"{label} 최근 7일 국가별 매출 알려줘" if not change
              else f"{change['country']} 최근 2주 매출 추이 보여줘")
    return {"title": title[:300], "body": "\n".join(lines), "follow_up": follow[:300]}


# ── 저장·조회 ────────────────────────────────────────────────────────────────

def is_repeat(user_id: int, title: str) -> bool:
    """직전에 보낸 것과 **같은 이야기**인가.

    ⛔ 임계만으로는 부족하다. 같은 추세가 이어지면 같은 문장이 매일 간다 —
       그러면 사람은 제목만 보고 지나치게 되고, 정작 달라진 날에도 안 본다.
    """
    row = fetch_one(
        "SELECT title FROM daily_briefings WHERE user_id = %s ORDER BY for_date DESC LIMIT 1",
        (int(user_id),))
    return bool(row and (row.get("title") or "") == title)


def save(user_id: int, for_date: date, scope: str, b: Dict[str, str]) -> bool:
    """하루 한 건. 같은 날 두 번 돌아도 덮어쓰기만 한다 (중복 알림 방지)."""
    ensure_tables()
    n = execute(
        "INSERT INTO daily_briefings (user_id, for_date, scope, title, body, follow_up) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE title=VALUES(title), body=VALUES(body), "
        "follow_up=VALUES(follow_up), scope=VALUES(scope)",
        (int(user_id), for_date, scope, b["title"], b["body"], b["follow_up"]))
    return bool(n)


def for_user(user_id: int, limit: int = 7) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id, for_date, scope, title, body, follow_up, created_at, seen_at "
        "FROM daily_briefings WHERE user_id = %s ORDER BY for_date DESC LIMIT %s",
        (int(user_id), int(limit))) or []


def mark_seen(user_id: int) -> int:
    return int(execute("UPDATE daily_briefings SET seen_at = NOW() "
                       "WHERE user_id = %s AND seen_at IS NULL", (int(user_id),)) or 0)


# ── 매일 실행 ────────────────────────────────────────────────────────────────

def run_daily() -> Dict[str, Any]:
    """전 사용자 브리핑 생성. **쿼리는 두 번뿐** — 사람 수와 무관하다."""
    from app.agents.sql_agent import TEAM_CODE2KR
    from app.config import get_settings
    from app.core.bigquery import BigQueryClient

    ensure_tables()
    s = get_settings()
    bq = BigQueryClient()
    table = s.sales_table_full_path

    base = stable_date(bq, table)
    if not base:
        logger.warning("briefing_no_stable_date", table=table)
        return {"error": "기준일을 정할 수 없음", "made": 0}

    data = collect(bq, table, base)
    # ⚠️ 퇴사자에게 매일 매출 브리핑을 보내지 않는다 (AD 부서로 걸러진다)
    users = fetch_all(
        "SELECT u.id, u.display_name, COALESCE(a.email, u.email) AS email, "
        "       COALESCE(a.department, '') AS department "
        "FROM users u LEFT JOIN ad_users a ON a.id = u.ad_user_id "
        "WHERE u.is_active = 1 AND COALESCE(a.department,'') NOT LIKE %s", ("%퇴사%",)) or []

    try:
        from app.core.value_lists import _cached
        known_countries = [c for c in (_cached("Country") or []) if len(c) >= 2]
    except Exception:
        known_countries = []

    made = skipped = mailed = 0
    for u in users:
        scope = resolve_scope(u["department"], TEAM_CODE2KR)
        if scope["kind"] == "all":
            # 팀이 매출 축에 없으면 **실제로 묻던 나라**로 좁힌다
            c = infer_country(u.get("email") or "", known_countries)
            if c:
                scope = {"kind": "country", "code": c, "label": c}
        b = compose(scope, data)
        if not b or is_repeat(u["id"], b["title"]):
            skipped += 1
            continue
        save(u["id"], base, scope["label"], b)
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
