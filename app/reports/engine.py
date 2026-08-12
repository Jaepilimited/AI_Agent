# -*- coding: utf-8 -*-
"""스펙 → payload. 조회·품질 게이트·파생 지표를 순서대로 돌린다.

payload 구조:
    meta      : 스펙 id·제목·파라미터·생성 시각
    facts     : {fact_id: rows}
    gates     : [{id, label, passed, detail, impact}]
    derived   : 파생 지표 (spec.derive 가 만든다)
    expects   : {fact_id: 기대값 서술}  — 재실행 대조용

조회는 기존 `security.validate_sql()` 을 통과시킨다. 보고서라고 방어선을 우회하지 않는다.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

import structlog

from app.reports.spec import ReportSpec, Rows

logger = structlog.get_logger(__name__)


class GateBlocked(RuntimeError):
    """blocking 게이트가 실패해 보고서를 낼 수 없는 상태."""


def _run_sql(sql: str, timeout: float) -> Rows:
    from app.core.bigquery import get_bigquery_client
    from app.core.security import validate_sql

    ok, reason = validate_sql(sql)
    if not ok:
        raise ValueError(f"SQL 검증 실패: {reason}")
    return get_bigquery_client().execute_query(sql, timeout=timeout)


def build_payload(
    spec: ReportSpec,
    *,
    timeout: float = 300.0,
    only: List[str] | None = None,
) -> Dict[str, Any]:
    """스펙을 실행해 payload 를 만든다.

    only: 특정 fact 만 돌리고 싶을 때 (개발 중 반복 실행용)
    """
    t0 = time.time()
    facts: Dict[str, Rows] = {}
    expects: Dict[str, str] = {}
    timings: Dict[str, float] = {}

    for f in spec.facts:
        if only and f.id not in only:
            continue
        sql = f.sql.format(**spec.params)
        t = time.time()
        rows = _run_sql(sql, timeout)
        timings[f.id] = round(time.time() - t, 2)
        facts[f.id] = rows
        expects[f.id] = f.expect
        logger.info("report_fact_done", fact=f.id, rows=len(rows), sec=timings[f.id])

    gates: List[Dict[str, Any]] = []
    for g in spec.gates:
        if g.fact not in facts:
            continue
        passed, detail = g.verdict(facts[g.fact])
        gates.append({
            "id": g.id,
            "label": g.label,
            "passed": passed,
            "detail": detail,
            "impact": "" if passed else g.impact,
        })
        if not passed:
            logger.warning("report_gate_failed", gate=g.id, detail=detail)
            if g.blocking:
                raise GateBlocked(f"{g.label}: {detail}")

    derived: Dict[str, Any] = {}
    if spec.derive and not only:
        derived = spec.derive(facts, spec.params)

    payload = {
        "meta": {
            "spec": spec.id,
            "title": spec.title,
            "params": spec.params,
            "elapsed_sec": round(time.time() - t0, 1),
            "fact_seconds": timings,
        },
        "facts": facts,
        "gates": gates,
        "derived": derived,
        "expects": expects,
    }
    logger.info("report_payload_built", spec=spec.id, facts=len(facts),
                gates_failed=sum(1 for g in gates if not g["passed"]),
                sec=payload["meta"]["elapsed_sec"])
    return payload


def write_verification_sql(spec: ReportSpec, path: str) -> None:
    """fact 들을 기대값 주석과 함께 .sql 로 떨군다.

    보고서 수치를 사람이 손으로 검산할 수 있어야 한다. 골든셋이 답변에 하는 일과 같다.
    """
    parts = [
        f"-- {spec.title} — 수치 검증용 쿼리",
        f"-- 스펙: {spec.id} / 파라미터: {spec.params}",
        "-- 각 쿼리 상단의 '기대'와 실제 결과가 어긋나면 보고서를 다시 만들어야 한다.",
        "",
    ]
    for i, f in enumerate(spec.facts):
        parts.append(f"-- ── Q{i}. {f.id} " + "─" * 40)
        if f.note:
            parts.append(f"-- 목적: {f.note}")
        if f.expect:
            parts.append(f"-- 기대: {f.expect}")
        parts.append(f.sql.format(**spec.params).strip())
        parts.append(";")
        parts.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(parts))
