"""골든셋 회귀 러너 — 답변 품질을 '사고 후'가 아니라 '배포 전/매일 아침'에 잡는다.

배경 (2026-08-06):
    이번 주 품질 사고(라우팅 오분류, 후속 질문 맥락 유실, 브랜드/대륙 오답 등)는
    전부 사용자가 겪은 뒤에야 발견됐다. 카나리아 2문항은 구조적 건강(잘림/오류)만
    보고 내용 회귀는 못 잡는다. 골든셋은 실사용 질문 + 사고 회귀 문항을 매일 돌려
    "언제부터, 어떤 문항이, 어떻게 깨졌나"를 런 단위로 비교 가능하게 기록한다.

구성:
    - 문항: data/golden_set.json (freq: daily=매일 / weekly=일요일 전체 런)
    - 실행: 매일 05:30 스케줄(golden_daily) 또는 Admin 수동 실행
    - 판정: 답변 본문 포함/제외 + 생성 SQL 규칙(sql_contains_any) + 길이/시간
    - 저장: golden_runs(런 요약) / golden_results(문항별) — 런 간 diff 비교용
    - 골든 호출은 user_email='golden-bot@system' 으로 기록돼 사용 통계와 분리된다

원칙:
    - 시점에 따라 변하는 숫자(이번 달 매출 등)를 기대값으로 고정하지 않는다
    - 문항 판정은 LLM 표현 변동에 강해야 한다 — 문구가 아니라 사실(날짜·제품명·
      SQL 규칙)을 검사한다
    - 실패 시 알림은 자가 점검(golden_regression 검사)이 담당 — 상태 변화만 알린다
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

from app.core.answer_check import has_number_near
from app.db.mariadb import execute, execute_lastid, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

GOLDEN_EMAIL = "golden-bot@system"
_GOLDEN_SET_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "golden_set.json"
_TOTAL_BUDGET_S = 40 * 60  # 런 전체 시간 예산 — 초과 시 남은 문항은 다음 런으로

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS golden_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME NULL,
    trigger_type VARCHAR(20) NOT NULL DEFAULT 'scheduled',
    scope VARCHAR(10) NOT NULL DEFAULT 'daily',
    total INT NOT NULL DEFAULT 0,
    passed INT NOT NULL DEFAULT 0,
    avg_ms INT NOT NULL DEFAULT 0,
    note VARCHAR(200) NOT NULL DEFAULT '',
    INDEX idx_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_DDL_RESULTS = """
CREATE TABLE IF NOT EXISTS golden_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id INT NOT NULL,
    item_id VARCHAR(64) NOT NULL,
    category VARCHAR(32) NOT NULL DEFAULT '',
    ok TINYINT NOT NULL DEFAULT 0,
    fail_reasons TEXT,
    elapsed_ms INT NOT NULL DEFAULT 0,
    answer_len INT NOT NULL DEFAULT 0,
    route VARCHAR(48) NOT NULL DEFAULT '',
    answer_head TEXT,
    INDEX idx_run (run_id),
    INDEX idx_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_golden_tables() -> None:
    try:
        execute(_DDL_RUNS)
        execute(_DDL_RESULTS)
    except Exception as e:
        logger.debug("golden_ddl_skip", error=str(e)[:120])


def load_golden_set() -> list[dict]:
    with open(_GOLDEN_SET_PATH, encoding="utf-8") as f:
        return json.load(f)["items"]


# ── 실행 ─────────────────────────────────────────────────────────────────────


def _ensure_golden_user() -> int:
    """골든 전용 비관리자 users 행을 보장하고 id 를 돌려준다.

    ⚠️ 권한 판정(FI 등)은 JWT 가 아니라 **users.id 기반 DB 조회**다 (routes.py).
    첫 런에서 admin 의 user_id 를 빌려 썼더니 role='admin' 우회가 발동해
    perm_fi_denied 가 오탐으로 실패했다 (2026-08-06) — 방어선은 정상이었고
    하네스가 틀렸던 것. 전용 계정은 role='user' + ad_user_id NULL 이라
    FI 미승인 상태가 정확히 재현된다.
    """
    row = fetch_one("SELECT id FROM users WHERE email = %s", (GOLDEN_EMAIL,))
    if row:
        return row["id"]
    return execute_lastid(
        "INSERT INTO users (email, password_hash, display_name, role, is_active) "
        "VALUES (%s, %s, %s, 'user', 1)",
        (GOLDEN_EMAIL, "!golden-no-login", "골든셋 러너"),
    )


def _make_token(auth: str) -> str:
    """골든 전용 토큰. email 이 실사용자와 달라 사용 통계·audit 에서 분리된다."""
    from app.core.session_auth import create_service_token

    if auth == "user":
        uid, role = _ensure_golden_user(), "user"
    else:
        adm = fetch_one("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        uid, role = (adm or {}).get("id", 1), "admin"
    return create_service_token(
        uid, GOLDEN_EMAIL, role=role,
        service="golden_runner", lifetime_seconds=7200,
    )


def _extract_sql_blocks(answer: str) -> str:
    return "\n".join(re.findall(r"```sql\s*\n(.*?)```", answer, re.DOTALL | re.IGNORECASE))


_RE_DETAILS = re.compile(r"<details>.*?</details>", re.DOTALL | re.IGNORECASE)
_RE_FOLLOWUP_HEAD = re.compile(r"💡")
_RE_FOLLOWUP_ITEM = re.compile(r"^>?\s*[-*]\s*.+")


def _strip_followup_block(answer: str) -> str:
    """후속 질문 제안 **블록만** 걷는다 — 그 뒤에 오는 본문은 남긴다.

    ⛔ **끝까지 지우지 마라.** 예전에는 `💡 이런 것도 물어보세요` 부터 문자열
       끝까지를 통째로 버렸다(`DOTALL`). 그런데 이 파이프라인은 제안 **뒤에도**
       본문을 덧붙인다 — PR 출처 링크·대조용 원본 표·합계 블록·물류 공시가 전부
       그 자리다. 그래서 판정이 그 구간을 **구조적으로 못 봤다**:
       `pr_issue_reaches_pr` 은 답변에 원본 시트 링크가 **있는데도** "필수 누락"
       으로 9런 내리 실패했다 (2026-09-15 실측 — 문항이 태어난 뒤 통과 이력 0).
    ⛔ 더 나쁜 쪽은 **음성 단언**이다. `not_contains` 가 그 구간을 못 보면
       금지 문구가 거기 있어도 조용히 통과한다 — 실패는 눈에 띄지만 무력해진
       단언은 아무 소리도 내지 않는다.
    ⚠️ 규칙은 화면(`chat.js` 의 `stripFollowupBlock`)과 **같아야 한다.** 사용자가
       보는 본문과 판정이 보는 본문이 갈리면 어느 쪽도 못 믿는다 — 제안 항목 줄
       (`- …`)·인용부호만 있는 줄·빈 줄까지만 건너뛰고, 그 밖의 줄을 만나면 블록이
       끝난 것으로 본다.
    """
    if not answer or "💡" not in answer:
        return answer or ""
    kept: list[str] = []
    in_followup = False
    for line in answer.split("\n"):
        stripped = line.strip()
        if _RE_FOLLOWUP_HEAD.search(stripped) and (
                "물어보세요" in stripped or "질문" in stripped):
            in_followup = True
            continue
        if in_followup:
            if _RE_FOLLOWUP_ITEM.match(stripped) or stripped in (">", ""):
                continue
            in_followup = False
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)


def _body_only(answer: str) -> str:
    """판정에 쓸 **본문**만 남긴다.

    후속 질문 안내와 `<details>`(실행 쿼리) 블록을 뺀다. 둘 다 LLM 이 관련 용어를
    잔뜩 흩뿌리는 구간이라, 포함시키면 본문이 틀려도 기대 키워드가 걸려 통과한다.

    실제로 겪은 것 (2026-08-12 run#23):
        "B2B 할인 금액" 문항이 기대어 'B2C' 로 통과했는데, 그 'B2C' 는 본문이 아니라
        후속 질문 제안("B2C 채널의 할인 금액은?")에 있었다. 본문은 오히려
        "B2B 프로모션 검토 필요" 라고 오도하고 있었다.
    sql_contains_any 는 원문에서 따로 뽑으므로 영향받지 않는다.
    """
    return _RE_DETAILS.sub("", _strip_followup_block(answer))


def _evaluate(item: dict, answer: str, elapsed_s: float) -> list[str]:
    """기대 조건 평가 — 실패 사유 목록 반환 (빈 목록 = 통과)."""
    exp = item.get("expect", {})
    reasons = []
    a = _body_only(answer)

    for kw in exp.get("contains_all", []):
        if kw not in a:
            reasons.append(f"필수 누락: {kw!r}")
    any_kws = exp.get("contains_any", [])
    if any_kws and not any(kw in a for kw in any_kws):
        reasons.append(f"다음 중 하나 필요: {any_kws}")
    for kw in exp.get("not_contains", []):
        if kw in a:
            reasons.append(f"금지 문구 등장: {kw!r}")

    # ── 수치는 문자열이 아니라 **허용오차**로 본다 ────────────────────────────
    # ⛔ 살아 있는 집계값을 문자열로 얼리지 마라. 2026-09-07 실측: 문항 4개가 매일
    #    실패하고 있었는데 앱은 정상이었고 얼려 둔 숫자만 낡았다 (789→790, 930→932,
    #    5,577→5,576.9). 매일 실패하는 문항은 곧 아무도 안 읽고, 그러면 진짜
    #    회귀가 났을 때 그 문항도 함께 무시당한다.
    # ⛔ 더 나쁜 쪽은 **음성 단언**이다 — `not_contains: "5,577"` 은 전사값이
    #    5,576.9 로 밀리는 순간 아무 흔적 없이 죽는다. 실패는 눈에 띄지만 무력해진
    #    단언은 조용하다. 그래서 양·음 두 방향 모두 허용오차로 쓴다.
    # ⚠️ 값이 여럿인 문항도 있다 (분기 비교처럼) — dict 하나든 목록이든 받는다.
    #    하나만 받게 두면 나머지를 `contains_all` 문자열로 적게 되고, 그 순간 표기가
    #    흔들릴 때마다 깜빡인다 (2026-09-15 `inc_megawari_quarter_amounts`: 값은
    #    똑같은데 요약이 "73.4억" 대신 원 단위로 적혀 실패했다).
    def _specs(v):
        return [v] if isinstance(v, dict) else list(v or [])

    for near in _specs(exp.get("number_near")):
        if not has_number_near(a, near["value"], near.get("pct", 2) / 100.0):
            reasons.append(f"수치 불일치 — {near['value']:,} ±{near.get('pct', 2)}% 가 본문에 없음")
    for far in _specs(exp.get("number_not_near")):
        if has_number_near(a, far["value"], far.get("pct", 2) / 100.0):
            reasons.append(f"금지 수치 등장 — {far['value']:,} ±{far.get('pct', 2)}%")

    # ⚠️ SQL 은 원문에서 뽑는다 — `<details>` 안에 있고 본문에서는 그 블록을 걷어냈다
    sql_any = exp.get("sql_contains_any", [])
    sql_all = exp.get("sql_contains_all", [])
    sql_none = exp.get("sql_not_contains", [])
    if sql_any or sql_all or sql_none:
        sql = (_extract_sql_blocks(answer or "") or (answer or "")).lower()
        if sql_any and not any(kw.lower() in sql for kw in sql_any):
            reasons.append(f"SQL 규칙 위반 — 다음 중 하나 필요: {sql_any}")
        # ⛔ "붙이면 안 되는 절"은 본문으로는 볼 수 없다 — 필터는 표에 안 나온다.
        #    스킨천사의 `Line != 'ZB'` 가 그 예다: 붙어도 값이 0.1% 만 달라져
        #    표를 아무리 봐도 틀린 줄 모른다 (2026-09-15).
        for kw in sql_none:
            if kw.lower() in sql:
                reasons.append(f"SQL 금지 절 등장: {kw!r}")
        # ⛔ 필터가 제대로 걸렸는지는 **SQL 에서** 본다. 본문 기대어로 걸면 그 값이
        #    표에 나올 때만(= GROUP BY 축일 때만) 맞고, 필터로만 쓰였을 때는 LLM 이
        #    문장에 우연히 적어 줘야 통과한다 — 확률적으로 깜빡이는 문항이 된다
        #    (2026-09-15 `inc_date_cap_gate_never_kills_request` 20런 중 9회 실패).
        for kw in sql_all:
            if kw.lower() not in sql:
                reasons.append(f"SQL 필수 누락: {kw!r}")

    # 길이는 "답변이 오긴 왔는가" 검사라 원문 기준으로 둔다 (본문 기준으로 바꾸면
    # 기존 문항들의 임계값이 한꺼번에 어긋난다)
    min_len = exp.get("min_len", 1)
    if len((answer or "").strip()) < min_len:
        reasons.append(f"답변이 짧음 ({len((answer or '').strip())}자 < {min_len})")
    max_s = exp.get("max_seconds")
    if max_s and elapsed_s > max_s:
        reasons.append(f"응답 {elapsed_s:.0f}s (허용 {max_s:.0f}s 초과)")
    return reasons


def _fetch_route(question: str) -> str:
    row = fetch_one(
        "SELECT route FROM audit_logs WHERE user_email = %s AND query = %s "
        "ORDER BY id DESC LIMIT 1",
        (GOLDEN_EMAIL, question),
    )
    return (row or {}).get("route", "") or ""


def run_golden(trigger_type: str = "scheduled", scope: Optional[str] = None) -> dict:
    """골든셋 실행. scope: 'daily'|'full'|None(자동 — 일요일이면 full)."""
    import httpx

    from app.config import get_settings

    ensure_golden_tables()

    if scope not in ("daily", "full"):
        scope = "full" if datetime.now().weekday() == 6 else "daily"

    # 중복 실행 가드 — 진행 중(1시간 내 시작, 미종료) 런이 있으면 거부
    running = fetch_one(
        "SELECT id FROM golden_runs WHERE finished_at IS NULL "
        "AND started_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR) LIMIT 1")
    if running:
        return {"error": f"런 {running['id']} 가 이미 진행 중입니다"}

    items = load_golden_set()
    if scope == "daily":
        items = [it for it in items if it.get("freq", "daily") == "daily"]

    # ⚠️ LAST_INSERT_ID() 를 별도 쿼리로 읽으면 풀의 다른 커넥션이 걸려 0이 나온다
    # (2026-08-06 첫 런에서 실제 발생 — 런 마감 UPDATE가 빗나가 가드에 걸렸다)
    run_id = execute_lastid(
        "INSERT INTO golden_runs (trigger_type, scope) VALUES (%s, %s)",
        (trigger_type, scope))
    logger.info("golden_run_start", run_id=run_id, scope=scope, items=len(items))

    s = get_settings()
    tokens = {"admin": _make_token("admin"), "user": _make_token("user")}
    t_run0 = time.time()
    executed = passed = 0
    total_ms = 0
    note = ""

    with httpx.Client(base_url=f"http://127.0.0.1:{s.port}") as client:
        for item in items:
            if time.time() - t_run0 > _TOTAL_BUDGET_S:
                note = f"시간 예산 {_TOTAL_BUDGET_S//60}분 초과 — {len(items)-executed}문항 미실행"
                logger.warning("golden_run_budget_exceeded", run_id=run_id)
                break
            messages = list(item.get("history", [])) + [
                {"role": "user", "content": item["question"]}]
            limit = item.get("expect", {}).get("max_seconds", 120)
            t0 = time.time()
            answer = ""
            route = ""
            reasons: list[str] = []
            try:
                r = client.post(
                    "/v1/chat/completions",
                    json={"model": "claude", "stream": False, "messages": messages},
                    cookies={"token": tokens[item.get("auth", "admin")]},
                    timeout=limit + 60,
                )
                elapsed = time.time() - t0
                if r.status_code != 200:
                    reasons = [f"HTTP {r.status_code}"]
                else:
                    body = r.json()
                    route = body.get("route") or ""
                    answer = ((body.get("choices") or [{}])[0]
                              .get("message") or {}).get("content", "") or ""
                    reasons = _evaluate(item, answer, elapsed)
            except Exception as e:
                elapsed = time.time() - t0
                reasons = [f"{type(e).__name__}: {str(e)[:100]}"]

            ok = not reasons
            executed += 1
            passed += ok
            total_ms += int(elapsed * 1000)
            execute(
                "INSERT INTO golden_results (run_id, item_id, category, ok, fail_reasons, "
                "elapsed_ms, answer_len, route, answer_head) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, item["id"], item.get("category", ""), int(ok),
                 " / ".join(reasons)[:2000] if reasons else None,
                 int(elapsed * 1000), len(answer), route or _fetch_route(item["question"]),
                 answer[:400]),
            )
            logger.info("golden_item", run_id=run_id, item=item["id"], ok=ok,
                        elapsed_ms=int(elapsed * 1000))

    execute(
        "UPDATE golden_runs SET finished_at=NOW(), total=%s, passed=%s, avg_ms=%s, note=%s "
        "WHERE id=%s",
        (executed, passed, total_ms // max(executed, 1), note, run_id),
    )
    logger.info("golden_run_done", run_id=run_id, passed=passed, total=executed)
    return {"run_id": run_id, "scope": scope, "total": executed, "passed": passed,
            "pass_rate": round(passed / executed * 100, 1) if executed else 0.0,
            "note": note}


# ── 조회·비교 ─────────────────────────────────────────────────────────────────


def get_runs(limit: int = 30) -> list[dict]:
    rows = fetch_all(
        "SELECT id, started_at, finished_at, trigger_type, scope, total, passed, avg_ms, note "
        "FROM golden_runs WHERE finished_at IS NOT NULL "
        "ORDER BY id DESC LIMIT %s", (limit,))
    for r in rows:
        r["pass_rate"] = round(r["passed"] / r["total"] * 100, 1) if r["total"] else 0.0
        r["started_at"] = str(r["started_at"])
        r["finished_at"] = str(r["finished_at"])
    return rows


def get_run_detail(run_id: int) -> dict:
    run = fetch_one("SELECT * FROM golden_runs WHERE id=%s", (run_id,))
    if not run:
        return {"error": "런이 없습니다"}
    run["started_at"] = str(run["started_at"])
    run["finished_at"] = str(run["finished_at"])
    results = fetch_all(
        "SELECT item_id, category, ok, fail_reasons, elapsed_ms, answer_len, route, answer_head "
        "FROM golden_results WHERE run_id=%s ORDER BY category, item_id", (run_id,))
    run["pass_rate"] = round(run["passed"] / run["total"] * 100, 1) if run["total"] else 0.0
    return {"run": run, "results": results}


def compare_runs(run_a: int, run_b: int) -> dict:
    """두 런 비교 — a(과거) 대비 b(최신)에서 무엇이 바뀌었나.

    같은 문항이 양쪽 다 실행된 경우만 비교한다 (daily vs full 런 조합 대비).
    """
    def _results(rid):
        return {r["item_id"]: r for r in fetch_all(
            "SELECT item_id, category, ok, fail_reasons, elapsed_ms, route "
            "FROM golden_results WHERE run_id=%s", (rid,))}

    ra, rb = _results(run_a), _results(run_b)
    if not ra or not rb:
        return {"error": "비교할 결과가 없습니다"}
    common = sorted(set(ra) & set(rb))

    newly_failed, newly_passed, still_failing, route_changed = [], [], [], []
    lat_deltas = []
    for iid in common:
        a, b = ra[iid], rb[iid]
        if a["ok"] and not b["ok"]:
            newly_failed.append({"item_id": iid, "category": b["category"],
                                 "fail_reasons": b["fail_reasons"]})
        elif not a["ok"] and b["ok"]:
            newly_passed.append({"item_id": iid, "category": b["category"]})
        elif not a["ok"] and not b["ok"]:
            still_failing.append({"item_id": iid, "category": b["category"],
                                  "fail_reasons": b["fail_reasons"]})
        if a["route"] and b["route"] and a["route"] != b["route"]:
            route_changed.append({"item_id": iid, "from": a["route"], "to": b["route"]})
        lat_deltas.append({"item_id": iid, "from_ms": a["elapsed_ms"],
                           "to_ms": b["elapsed_ms"],
                           "delta_ms": b["elapsed_ms"] - a["elapsed_ms"]})

    lat_deltas.sort(key=lambda x: abs(x["delta_ms"]), reverse=True)
    pass_a = sum(1 for i in common if ra[i]["ok"])
    pass_b = sum(1 for i in common if rb[i]["ok"])
    return {
        "run_a": run_a, "run_b": run_b, "common_items": len(common),
        "pass_rate_a": round(pass_a / len(common) * 100, 1) if common else 0.0,
        "pass_rate_b": round(pass_b / len(common) * 100, 1) if common else 0.0,
        "newly_failed": newly_failed,
        "newly_passed": newly_passed,
        "still_failing": still_failing,
        "route_changed": route_changed,
        "latency_top_changes": lat_deltas[:10],
    }


def latest_regression() -> dict:
    """최근 두 런의 회귀 요약 — 자가 점검(golden_regression)이 사용."""
    runs = fetch_all(
        "SELECT id FROM golden_runs WHERE finished_at IS NOT NULL AND total > 0 "
        "ORDER BY id DESC LIMIT 2")
    if len(runs) < 2:
        return {"comparable": False}
    cmp = compare_runs(runs[1]["id"], runs[0]["id"])
    return {"comparable": True, "latest_run": runs[0]["id"], "prev_run": runs[1]["id"],
            "newly_failed": cmp.get("newly_failed", []),
            "pass_rate": cmp.get("pass_rate_b")}
