# -*- coding: utf-8 -*-
"""보고서 저장·열람 권한.

**본인이 만든 보고서만 열람한다** (admin 제외 — 2026-08-12 사용자 결정).
보고서에는 원가·마진·거래처별 FOC율이 들어가므로 매출 질문과 같은 수준으로 열지 않는다.

권한 판정 원칙은 FI 와 같다:
    - **반드시 서버에서 DB 조회로** 판정한다. JWT·프론트 값은 stale 위험이 있어 믿지 않는다
    - 소유자 비교는 `reports.user_id` 하나로 끝난다 — 조건을 늘리면 우회 경로가 늘어난다

HTML 본문은 파일로 두고 DB 에는 경로만 둔다 (수십 KB 를 DB 에 넣지 않기 위해).
파일이 사라져도 payload 로 다시 렌더할 수 있게 payload 는 DB 에 넣는다.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog

from app.db.mariadb import execute, execute_lastid, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

REPORT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "reports",
)

_DDL = """
CREATE TABLE IF NOT EXISTS reports (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT NOT NULL,
    spec          VARCHAR(64) NOT NULL,
    title         VARCHAR(255) NOT NULL,
    params_hash   CHAR(32) NOT NULL,
    params_json   TEXT NOT NULL,
    payload_json  MEDIUMTEXT NULL,
    html_path     VARCHAR(512) NULL,
    question      VARCHAR(1000) NULL,
    gates_failed  INT NOT NULL DEFAULT 0,
    elapsed_sec   FLOAT NULL,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_created (user_id, created_at),
    INDEX idx_spec_hash (spec, params_hash, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_report_tables() -> None:
    try:
        execute(_DDL)
        os.makedirs(REPORT_DIR, exist_ok=True)
    except Exception as e:  # 기동을 막지 않는다
        logger.debug("report_ddl_skip", error=str(e)[:120])


def params_hash(spec_id: str, params: Dict[str, Any]) -> str:
    """캐시 키. 값이 같으면 같은 보고서다."""
    blob = json.dumps({"spec": spec_id, "params": params}, sort_keys=True, default=str)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def find_fresh(spec_id: str, phash: str, max_age_hours: int = 12) -> Optional[Dict[str, Any]]:
    """같은 스펙·파라미터의 최근 보고서. 열 명이 같은 걸 물어도 BigQuery 는 한 번만 훑는다.

    ⚠️ **소유자를 보지 않는다** — 여기서 나온 payload 로 요청자 본인의 새 행을 만든다.
    남의 보고서 행을 그대로 돌려주면 열람 권한이 무너진다.
    """
    return fetch_one(
        "SELECT id, payload_json, title FROM reports "
        "WHERE spec = %s AND params_hash = %s AND payload_json IS NOT NULL "
        "AND created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR) "
        "ORDER BY created_at DESC LIMIT 1",
        (spec_id, phash, max_age_hours),
    )


def save(*, user_id: int, spec_id: str, title: str, params: Dict[str, Any],
         payload: Dict[str, Any], html: str, question: str = "",
         cache_key: Optional[str] = None) -> int:
    """cache_key: 캐시 조회에 쓸 해시.

    ⚠️ 품질 게이트가 채널을 제외하면 2회차 파라미터가 달라진다. 최종 파라미터로 해시를 만들면
       다음 요청(1회차 파라미터)과 영영 어긋나 **캐시가 절대 히트하지 않는다** (2026-08-12 실측).
       그래서 **요청 시점의 파라미터**로 만든 해시를 받아서 저장한다.
    """
    ensure_report_tables()
    phash = cache_key or params_hash(spec_id, params)
    gates_failed = sum(1 for g in payload.get("gates", []) if not g.get("passed"))

    rid = execute_lastid(
        "INSERT INTO reports (user_id, spec, title, params_hash, params_json, "
        "payload_json, question, gates_failed, elapsed_sec) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (user_id, spec_id, title, phash,
         json.dumps(params, ensure_ascii=False, default=str),
         json.dumps(payload, ensure_ascii=False, default=str),
         question[:1000], gates_failed,
         (payload.get("meta") or {}).get("elapsed_sec")),
    )

    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"{rid}.html")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    execute("UPDATE reports SET html_path = %s WHERE id = %s", (path, rid))

    logger.info("report_saved", report_id=rid, spec=spec_id, user_id=user_id,
                gates_failed=gates_failed)
    return rid


def get_for_user(report_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """소유자 본인에게만 돌려준다. 없거나 남의 것이면 None — 둘을 구분해 알려주지 않는다."""
    return fetch_one(
        "SELECT id, user_id, spec, title, params_json, payload_json, html_path, "
        "question, gates_failed, created_at "
        "FROM reports WHERE id = %s AND user_id = %s",
        (report_id, user_id),
    )


def list_for_user(user_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id, spec, title, question, gates_failed, elapsed_sec, created_at "
        "FROM reports WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, limit),
    )


def read_html(row: Dict[str, Any]) -> Optional[str]:
    """저장된 HTML. 파일이 없으면 payload 로 다시 렌더한다."""
    path = row.get("html_path")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    if not row.get("payload_json"):
        return None
    try:
        from app.reports import render
        from app.reports.registry import get_spec
        payload = json.loads(row["payload_json"])
        spec = get_spec(row["spec"])
        return render.render(payload, spec.template, allow_literals=spec.allow_literals)
    except Exception as e:
        logger.warning("report_rerender_failed", report_id=row.get("id"), error=str(e)[:200])
        return None


def purge_older_than(days: int = 90) -> int:
    """오래된 보고서 정리. 원가·마진이 든 파일을 무기한 쌓아두지 않는다."""
    rows = fetch_all(
        "SELECT id, html_path FROM reports WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
        (days,),
    )
    for r in rows:
        p = r.get("html_path")
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    if rows:
        execute("DELETE FROM reports WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)", (days,))
    return len(rows)
