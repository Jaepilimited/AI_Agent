# -*- coding: utf-8 -*-
"""사용자가 무엇을 자주 묻는지 — 질문 이력에서 **결정적으로** 뽑는다.

⛔ **LLM 에 맡기지 않는다.** 여기서 뽑은 값은 화면의 제안이 되고, 제안은 사람이
   그대로 누른다. 확률로 뽑으면 "왜 이게 떴는지" 를 아무도 설명할 수 없다.
   축 추출은 보고서가 이미 쓰는 `reports.registry.extract_filters` 를 **그대로** 쓴다 —
   같은 규칙을 두 곳에서 따로 구현하면 언젠가 서로 다른 말을 한다.

⛔ **자동으로 필터를 끼우지 않는다.** 이 모듈은 "이 사람은 쇼피·인도네시아를 자주 본다"
   까지만 말한다. 묻지 않은 조건이 질문에 조용히 붙는 것은 이 시스템에서 가장 위험한
   실패다 (기간을 말없이 자르던 사고와 같은 부류). 붙이는 것은 **사람이 눌러서** 한다.

⚠️ 남의 이력은 보지 않는다. 프로필은 언제나 **자기 것만** 읽힌다 —
   질문에는 담당 거래처·미출시 제품처럼 남이 보면 안 되는 말이 섞인다.

근거 (2026-08-27 실측): 실사용 질문 3,459건 · 58명. 같은 질문을 여러 사람이 되풀이한다
(쇼피 인도네시아 매출 24회·6명). 그런데 `sql_cache` 는 문자열 완전 일치라 거의 안 걸리고,
첫 화면 칩은 **모두에게 같은 8개**가 고정으로 떠 있었다.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Set

import structlog

from app.db.mariadb import execute, fetch_all

logger = structlog.get_logger(__name__)

#: 프로필을 만들 때 볼 기간. 너무 길면 지난 분기 습관이 남고, 너무 짧으면 표본이 없다.
LOOKBACK_DAYS = 90
#: 화면에 올릴 최근 질문 수.
TOP_QUESTIONS = 6
#: 이만큼은 물어봐야 "자주" 라고 부른다 — 한 번 물어본 것을 습관이라 하면 안 된다.
MIN_AXIS_HITS = 2

#: 사람이 아닌 호출자. 프로필에 섞이면 남의 습관이 내 화면에 뜬다.
_BOT_EMAILS = ("golden-bot@system", "canary@system", "system@system")

#: 프로필에 담을 경로. ⛔ `direct` 를 넣지 마라 — 인사말·잡담·기능 질문이 그리로 간다.
#:    실측(2026-08-27): 반복이 없는 사람은 상위 제안이 "1+1+(1*5) 는 뭐임?" 이 됐다.
#:    첫 화면 칩은 **업무를 다시 하게 만드는 자리**지 지난 잡담을 보여주는 자리가 아니다.
_WORK_ROUTES = ("bigquery", "notion", "cs", "inventory", "report", "model_rights",
                "multi", "gws")

#: 칩 한 줄에 들어갈 길이. 넘치면 잘려 무슨 질문인지 알 수 없다.
#: ⚠️ 여러 줄짜리 보고서 요청(수백 자)이 그대로 칩이 되면 화면이 무너진다.
MAX_CHIP_LEN = 46

_DDL = """
CREATE TABLE IF NOT EXISTS user_query_profile (
    user_email  VARCHAR(190) NOT NULL,
    kind        VARCHAR(24)  NOT NULL,
    value       VARCHAR(255) NOT NULL,
    n           INT          NOT NULL DEFAULT 0,
    last_at     DATETIME     NULL,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_email, kind, value),
    INDEX idx_user_kind (user_email, kind, n)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_tables() -> None:
    try:
        execute(_DDL)
    except Exception as e:
        logger.warning("query_profile_ddl_failed", error=str(e)[:160])


_TEAM_SCOPE_SQL = """
WITH t AS (
  SELECT Team_NEW AS team, Country AS country, SUM(Sales1_R) AS amt
  FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
  WHERE Date >= DATE_SUB(CURRENT_DATE(), INTERVAL 12 MONTH)
    AND Team_NEW IS NOT NULL AND Team_NEW NOT IN ('기타','OP')
    AND Country IS NOT NULL AND Country != ''
  GROUP BY team, country
),
s AS (SELECT team, SUM(amt) AS team_total FROM t GROUP BY team)
SELECT t.team, t.country, t.amt / s.team_total AS share
FROM t JOIN s USING (team)
WHERE t.amt / s.team_total >= 0.01
"""

#: 광고 기준 범위. ⛔ **매출 범위를 그대로 쓰지 마라 — 팀 구성이 다르다.**
#: 실측(최근 12개월): 광고를 돌리는 팀은 7개뿐이고 영업1·2팀·유통1·2팀·BCM 은
#: 집행 자체가 없다. 매출 국가로 광고 질문을 거르면 안 파는 곳을 허용하고,
#: 정작 광고하는 곳을 막는다. 전사 광고 1% 이상은 10개국이다 (태국은 없다).
_AD_TEAM_SCOPE_SQL = """
WITH t AS (
  SELECT team, country, SUM(cost_krw) AS amt
  FROM `skin1004-319714.marketing_analysis.integrated_ad`
  WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 12 MONTH)
    AND date <= CURRENT_DATE()
    AND team IS NOT NULL AND team NOT IN ('기타','OP')
    AND country IS NOT NULL AND country != ''
  GROUP BY team, country
),
s AS (SELECT team, SUM(amt) AS team_total FROM t GROUP BY team)
SELECT t.team, t.country, t.amt / s.team_total AS share
FROM t JOIN s USING (team)
WHERE t.amt / s.team_total >= 0.01
"""

#: ⚠️ 기간 상한을 반드시 둘 것 — 이 표에는 미래 날짜 행이 있다 (CLAUDE.md 실측).
_AD_COMPANY_SCOPE_SQL = """
WITH c AS (
  SELECT country, SUM(cost_krw) AS amt
  FROM `skin1004-319714.marketing_analysis.integrated_ad`
  WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 12 MONTH)
    AND date <= CURRENT_DATE()
    AND country IS NOT NULL AND country != ''
  GROUP BY country
),
s AS (SELECT SUM(amt) AS company_total FROM c)
SELECT c.country, c.amt / s.company_total AS share
FROM c CROSS JOIN s
WHERE c.amt / s.company_total >= 0.01
"""

_COMPANY_SCOPE_SQL = """
WITH c AS (
  SELECT Country AS country, SUM(Sales1_R) AS amt
  FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
  WHERE Date >= DATE_SUB(CURRENT_DATE(), INTERVAL 12 MONTH)
    AND Country IS NOT NULL AND Country != ''
  GROUP BY country
),
s AS (SELECT SUM(amt) AS company_total FROM c)
SELECT c.country, c.amt / s.company_total AS share
FROM c CROSS JOIN s
WHERE c.amt / s.company_total >= 0.01
"""


def scope_country_map() -> Optional[Dict[str, Any]]:
    """최근 판매 실적으로 팀별·전사 주요 국가를 한 번에 만든다.

    ⛔ 사용자마다 BigQuery를 부르면 배치 비용과 시간이 사용자 수만큼 늘어난다.
       이 함수에서 전체 범위를 모아 `rebuild_all()`이 모든 사용자에게 나눠 준다.
    ⚠️ 제안 칩은 보조 기능이다. 범위 조회 실패로 칩까지 사라지지 않도록 실패 시
       `None`을 반환해 기존 무필터 동작으로 통과시킨다.
    """
    try:
        from app.core.bigquery import BigQueryClient

        bq = BigQueryClient()
        team_rows = bq.execute_query(_TEAM_SCOPE_SQL)
        company_rows = bq.execute_query(_COMPANY_SCOPE_SQL)
        ad_team_rows = bq.execute_query(_AD_TEAM_SCOPE_SQL)
        ad_company_rows = bq.execute_query(_AD_COMPANY_SCOPE_SQL)
    except Exception as e:
        logger.warning("query_profile_scope_country_failed", error=str(e)[:160])
        return None

    teams: Dict[str, Set[str]] = {}
    for row in team_rows or []:
        team = str(row.get("team") or "").strip()
        country = str(row.get("country") or "").strip()
        if team and country:
            teams.setdefault(team, set()).add(country)
    def _countries(rows):
        return {str(r.get("country") or "").strip()
                for r in (rows or []) if str(r.get("country") or "").strip()}

    def _by_team(rows):
        out: Dict[str, Set[str]] = {}
        for r in rows or []:
            team = str(r.get("team") or "").strip()
            country = str(r.get("country") or "").strip()
            if team and country:
                out.setdefault(team, set()).add(country)
        return out

    return {
        "teams": teams,
        "all": _countries(company_rows),
        # 광고는 별도 축이다 — 매출 범위와 섞지 않는다.
        "ad_teams": _by_team(ad_team_rows),
        "ad_all": _countries(ad_company_rows),
    }


# ── 질문 정규화 ──────────────────────────────────────────────────────────────
# ⚠️ "쇼피 인도네시아 이번 달 매출 알려줘" 와 "쇼피 인도네시아 이번달 매출" 은 같은
#    질문이다. 원문 그대로 세면 반복이 반복으로 보이지 않는다 — `sql_cache` 가
#    문자열 완전 일치라 거의 안 걸리는 것과 같은 함정이다.
_TAIL = re.compile(r"\s*(알려줘|보여줘|알려주세요|보여주세요|해줘|해주세요|줄래\??|줘)\s*$")


#: 광고 축으로 판정할 질문. ⚠️ 낱말을 늘리기 전에 실제 질문으로 확인할 것 —
#: `전환율` 안의 `환율`, `가이드라인` 안의 `라인` 처럼 짧은 말은 다른 낱말에 걸린다
#: (이 저장소가 라우팅에서 이미 겪은 사고다).
_AD_WORDS = ("roas", "광고", "마케팅", "캠페인", "노출", "클릭", "전환매출",
             "광고비", "cpc", "cpm", "ctr", "퍼포먼스")


def _is_ad_question(question: str) -> bool:
    """광고 실적을 묻는 질문인가 — 그렇다면 담당 범위도 광고 기준으로 본다."""
    low = (question or "").lower()
    return any(word in low for word in _AD_WORDS)


def signature(question: str) -> str:
    """같은 뜻의 질문을 한 덩이로 묶는 열쇠."""
    q = _TAIL.sub("", str(question or "").strip())
    return re.sub(r"\s+", "", q).lower()


def _is_person(email: str) -> bool:
    return bool(email) and email not in _BOT_EMAILS and not email.startswith("golden-bot")


# ── 프로필 만들기 ────────────────────────────────────────────────────────────

def rebuild(user_email: str,
            scope_map: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """한 사람의 질문 이력을 훑어 프로필을 다시 만든다.

    ⚠️ 원본(`audit_logs`)은 그대로 두고 **파생만** 다시 만든다. 규칙을 고쳤을 때
       과거까지 새 규칙으로 다시 계산할 수 있어야 한다.
    """
    ensure_tables()
    if not _is_person(user_email):
        return {"skipped": "not_a_person"}

    rows = fetch_all(
        "SELECT query, route, created_at, COALESCE(context_len, 0) ctx FROM audit_logs "
        "WHERE user_email = %s AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "ORDER BY id DESC",
        (user_email, LOOKBACK_DAYS),
    ) or []
    if not rows:
        return {"questions": 0, "dropped_out_of_scope": 0}

    from app.reports.registry import extract_filters

    allowed_countries: Optional[Set[str]] = None
    allowed_ad_countries: Optional[Set[str]] = None
    if scope_map is not None:
        try:
            from app.agents.sql_agent import TEAM_CODE2KR
            from app.core.briefing import resolve_scope

            department_rows = fetch_all(
                "SELECT COALESCE(a.department, '') AS department FROM users u "
                "LEFT JOIN directory_users a ON a.id = u.ad_user_id "
                "WHERE COALESCE(a.email, u.email) = %s LIMIT 1",
                (user_email,),
            ) or []
            department = (str(department_rows[0].get("department") or "")
                          if department_rows else "")
            scope = resolve_scope(department, TEAM_CODE2KR)

            def _pick(team_key: str, all_key: str) -> Set[str]:
                """내 팀 범위를 쓰되, **없으면 전사로 떨어진다.**

                사용자 지시(2026-08-27): "파트는 없으면 전사 기준으로 보고".
                ⚠️ 광고에서 특히 중요하다 — 영업1·2팀·유통1·2팀·BCM 은 집행이 아예
                   없어서(실측) 팀 범위가 비어 있다. 빈 범위를 그대로 쓰면 그분들의
                   마케팅 질문이 **통째로 사라진다**.
                """
                if scope["kind"] == "team":
                    mine = set((scope_map.get(team_key) or {}).get(scope["code"]) or set())
                    if mine:
                        return mine
                return set(scope_map.get(all_key) or set())

            allowed_countries = _pick("teams", "all")
            allowed_ad_countries = _pick("ad_teams", "ad_all")
        except Exception as e:
            # ⚠️ 소속 조회 실패가 기존 제안까지 지우면 안 된다. 이 사용자만 필터 없이 통과시킨다.
            logger.warning("query_profile_scope_resolve_failed", user=user_email,
                           error=str(e)[:160])

    axes: Counter = Counter()
    routes: Counter = Counter()
    asked: Dict[str, Dict[str, Any]] = {}
    dropped_out_of_scope = 0

    for row in rows:
        question = str(row.get("query") or "").strip()
        route = str(row.get("route") or "").split(":")[0]
        if route:
            routes[route] += 1
        # ⛔ 업무 질문만 제안 후보로 삼는다 (위 `_WORK_ROUTES` 주석 참조)
        if route not in _WORK_ROUTES:
            continue
        if len(question) < 6 or len(question) > MAX_CHIP_LEN:
            continue
        if chr(10) in question:
            continue                      # 여러 줄 요청은 칩으로 쓸 수 없다
        # ⛔ **혼자서도 말이 되는 질문만** 제안한다. 대화 도중의 후속 질문
        #    ("도넛 차트로 랜더링 해줘" · "TOP10이 아니라 전체 SKU로") 을 칩으로 만들면
        #    눌러도 앞 대화가 없어 엉뚱한 답이 나간다 — 제안이 함정이 된다.
        #    `context_len` 이 0 이면 그 질문이 대화의 **첫 마디**였다는 뜻이다
        #    (이미 기록하고 있던 값이다 — 새로 심을 필요가 없었다).
        if int(row.get("ctx") or 0) > 0:
            continue
        filters: Dict[str, Any] = {}
        try:
            filters = extract_filters(question)
        except Exception as e:            # 추출 실패여도 프로필은 만들어야 한다
            logger.warning("query_profile_extract_failed", error=str(e)[:120])

        # ⛔ 국가 문자열을 다시 파싱하지 않는다. 팀명 속 국가어 오인을 이미 막는
        #    `extract_filters` 결과만 믿어 보고서와 제안 칩의 범위 판정을 같게 유지한다.
        countries = filters.get("국가")
        # ⛔ 광고 질문을 **매출 범위로 거르지 마라** (2026-08-27 사용자 지시:
        #    "마케팅 ROAS도 마찬가지로"). 두 축은 팀 구성이 다르다 — 광고를 돌리는
        #    팀은 7개뿐이고, 전사 광고 1% 국가는 10개로 매출(22개)보다 훨씬 좁다.
        #    매출 범위로 판정하면 광고하지 않는 나라를 허용하게 된다.
        scope_for_question = (allowed_ad_countries if _is_ad_question(question)
                              else allowed_countries)
        if scope_for_question is not None and countries:
            named = countries if isinstance(countries, list) else [countries]
            if any(str(country) not in scope_for_question for country in named):
                dropped_out_of_scope += 1
                continue
        # ⚠️ 국가를 지목하지 않은 질문은 담당 국가 범위와 무관하므로 그대로 둔다.

        sig = signature(question)
        seen = asked.get(sig)
        if seen:
            seen["n"] += 1
        else:
            asked[sig] = {"text": question, "n": 1, "at": row.get("created_at")}

        # ⛔ 축은 보고서와 **같은 추출기**로 뽑는다 (사본을 만들지 않는다)
        try:
            for kind, value in filters.items():
                for one in (value if isinstance(value, list) else [value]):
                    if one:
                        axes[(kind, str(one))] += 1
        except Exception as e:            # 추출이 실패해도 프로필은 만들어야 한다
            logger.warning("query_profile_extract_failed", error=str(e)[:120])

    payload: List[tuple] = []
    # ⚠️ 동점이면 **최근순**이다. 가나다순으로 두면 반복이 없는 사람에게
    #    아무 질문이나 이름순으로 올라온다 (실측에서 그렇게 나왔다).
    ordered = sorted(asked.items(),
                     key=lambda kv: (-kv[1]["n"],
                                     -(kv[1]["at"].timestamp() if kv[1]["at"] else 0)))
    for sig, item in ordered[:TOP_QUESTIONS]:
        payload.append(("question", item["text"][:255], item["n"], item["at"]))
    for (kind, value), n in axes.items():
        if n >= MIN_AXIS_HITS:
            payload.append((f"axis:{kind}"[:24], value[:255], n, None))
    for route, n in routes.items():
        payload.append(("route", route[:255], n, None))

    execute("DELETE FROM user_query_profile WHERE user_email = %s", (user_email,))
    for kind, value, n, at in payload:
        execute(
            "INSERT INTO user_query_profile (user_email, kind, value, n, last_at) "
            "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE n = VALUES(n), "
            "last_at = VALUES(last_at), updated_at = NOW()",
            (user_email, kind, value, int(n), at),
        )
    stats = {"questions": len(rows), "distinct": len(asked), "rows": len(payload),
             "dropped_out_of_scope": dropped_out_of_scope}
    logger.info("query_profile_user_rebuilt", user=user_email,
                dropped_out_of_scope=dropped_out_of_scope,
                questions=len(rows), rows=len(payload))
    return stats


def rebuild_all() -> Dict[str, Any]:
    """최근에 쓴 사람만 다시 만든다 — 58명 전부를 매일 훑을 이유가 없다."""
    ensure_tables()
    rows = fetch_all(
        "SELECT DISTINCT user_email FROM audit_logs "
        "WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)", (LOOKBACK_DAYS,),
    ) or []
    # ⛔ 사용자 루프 안에서 범위를 조회하지 않는다. 전 사용자의 팀·전사 목록을 한 번만 만든다.
    scope_map = scope_country_map()
    done = 0
    for row in rows:
        email = str(row.get("user_email") or "")
        if not _is_person(email):
            continue
        try:
            rebuild(email, scope_map=scope_map)
            done += 1
        except Exception as e:
            logger.warning("query_profile_rebuild_failed", user=email, error=str(e)[:120])
    logger.info("query_profile_rebuilt", users=done)
    return {"users": done}


# ── 읽기 ─────────────────────────────────────────────────────────────────────

def for_user(user_email: str) -> Dict[str, Any]:
    """그 사람의 프로필. ⚠️ 남의 것은 절대 돌려주지 않는다 (호출부가 아니라 여기서 막는다)."""
    ensure_tables()
    if not _is_person(user_email):
        return {"questions": [], "axes": {}, "routes": []}

    rows = fetch_all(
        "SELECT kind, value, n, last_at FROM user_query_profile "
        "WHERE user_email = %s ORDER BY n DESC, value", (user_email,),
    ) or []

    questions = [
        {"text": r["value"], "n": int(r["n"] or 0), "at": r.get("last_at")}
        for r in rows if r["kind"] == "question"
    ]
    axes: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        kind = str(r["kind"])
        if kind.startswith("axis:"):
            axes.setdefault(kind[5:], []).append(
                {"value": r["value"], "n": int(r["n"] or 0)})
    routes = [{"route": r["value"], "n": int(r["n"] or 0)}
              for r in rows if r["kind"] == "route"]
    return {"questions": questions, "axes": axes, "routes": routes}
