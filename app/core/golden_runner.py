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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import structlog

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
    import jwt as _pyjwt

    from app.config import get_settings

    s = get_settings()
    if auth == "user":
        uid, role = _ensure_golden_user(), "user"
    else:
        adm = fetch_one("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        uid, role = (adm or {}).get("id", 1), "admin"
    return _pyjwt.encode(
        {"user_id": uid, "email": GOLDEN_EMAIL, "role": role, "brand_filter": "",
         "exp": datetime.now(timezone.utc) + timedelta(hours=2)},
        s.jwt_secret_key, algorithm="HS256",
    )


def _extract_sql_blocks(answer: str) -> str:
    return "\n".join(re.findall(r"```sql\s*\n(.*?)```", answer, re.DOTALL | re.IGNORECASE))


_RE_FOLLOWUP = re.compile(r"\n>?\s*💡\s*\*{0,2}이런 것도 물어보세요.*", re.DOTALL)
_RE_DETAILS = re.compile(r"<details>.*?</details>", re.DOTALL | re.IGNORECASE)


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
    a = _RE_FOLLOWUP.sub("", answer or "")
    return _RE_DETAILS.sub("", a)


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

    sql_kws = exp.get("sql_contains_any", [])
    if sql_kws:
        # ⚠️ 원문에서 뽑는다 — SQL 은 <details> 안에 있고, 본문에서는 그 블록을 걷어냈다
        sql = _extract_sql_blocks(answer or "") or (answer or "")
        if not any(kw.lower() in sql.lower() for kw in sql_kws):
            reasons.append(f"SQL 규칙 위반 — 다음 중 하나 필요: {sql_kws}")

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
