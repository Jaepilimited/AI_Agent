# -*- coding: utf-8 -*-
"""질문 → 보고서 한 건. 채팅에서 부르는 진입점.

흐름:
    match(질문) → 캐시 확인 → 조회(병렬) → 품질 게이트 → 파생 → 렌더 → 저장 → 요약

채팅에는 **요약 몇 줄과 링크만** 돌려준다. 보고서 본문을 대화창에 쏟으면 표가 깨지고,
사용자는 어차피 문서를 열어서 본다.

요약 문장도 payload 에서 만든다 — 여기서도 LLM 이 숫자를 쓰지 않는다.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import structlog

from app.reports import engine, registry, render, store

logger = structlog.get_logger(__name__)


def _fmt_eok(v) -> str:
    return f"{float(v or 0):,.1f}억"


def _summarize(spec_id: str, payload: Dict[str, Any]) -> list[str]:
    """채팅에 띄울 핵심 줄. 스펙마다 다르다."""
    lines: list[str] = []
    d = payload.get("derived") or {}

    if spec_id == "cost_efficiency":
        cur = (d.get("pnl") or {}).get("focus") or {}
        yoy = d.get("yoy") or {}
        conc = d.get("b2b_concentration") or {}
        if cur:
            lines.append(
                f"인센티브 지출 **{_fmt_eok(cur.get('incentive'))}** — "
                f"매출 대비 {cur.get('incentive_of_sales_pct')}%, "
                f"매출총이익 대비 **{cur.get('incentive_of_gross_pct')}%**"
            )
        if yoy:
            lines.append(
                f"늘어난 매출에 붙은 한계 인센티브율 {yoy.get('marginal_rate_pct')}% "
                f"(직전 평균 {yoy.get('avg_rate_prev_pct')}%) — "
                f"평균이 유지됐을 때와의 차이 {_fmt_eok(yoy.get('drift_cost'))}"
            )
        if conc:
            lines.append(
                f"FOC 거래처 {conc.get('n_accounts')}곳 중 상위 5곳이 "
                f"{conc.get('top5_pct')}% — 정책이 아니라 협상 대상"
            )
        sim = [s for s in (d.get("foc_cap_sim") or []) if s.get("cap_pct") == 8]
        if sim:
            lines.append(
                f"거래처 FOC 상한 8%를 걸면 {_fmt_eok(sim[0].get('save'))} 절감 "
                f"(대상 {sim[0].get('n_accounts_over')}곳, 탄력성 미반영)"
            )

    failed = [g for g in payload.get("gates", []) if not g.get("passed")]
    if failed:
        lines.append(
            "⚠️ 데이터 품질 문제 " + str(len(failed)) + "건을 제외하고 집계했습니다 — "
            + " / ".join(g["label"] for g in failed) + ". 근거는 보고서 마지막 절에 있습니다."
        )
    return lines


def run(question: str, user_id: int, *, spec_id: Optional[str] = None,
        use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """보고서를 만들고 저장한다. 해당 없으면 None.

    캐시가 있어도 **요청자 본인의 행을 새로 만든다** — 남의 행을 돌려주면 열람 권한이 무너진다.
    """
    if spec_id:
        params = registry.parse_params(question, spec_id)
    else:
        hit = registry.match(question)
        if not hit:
            return None
        spec_id, params = hit

    store.ensure_report_tables()
    spec = registry.get_spec(spec_id, **{k: v for k, v in params.items()
                                         if not k.startswith("_")})
    phash = store.params_hash(spec.id, spec.params)

    payload = None
    cached = store.find_fresh(spec.id, phash) if use_cache else None
    if cached:
        try:
            payload = json.loads(cached["payload_json"])
            logger.info("report_cache_hit", spec=spec.id, source_report=cached["id"])
        except Exception:
            payload = None

    if payload is None:
        payload = engine.build_payload(spec)
        # 게이트가 잡아낸 '할인 미적재 채널' 을 빼고 다시 집계한다.
        zero = [r["channel"] for r in payload["facts"].get("zero_discount_channels", [])]
        if zero:
            spec2 = registry.get_spec(
                spec_id,
                **{**{k: v for k, v in params.items() if not k.startswith("_")},
                   "excluded_channels": ", ".join(f"'{c}'" for c in zero)},
            )
            p2 = engine.build_payload(spec2)
            p2["gates"] = payload["gates"]          # 게이트 판정은 '제외 전' 사실이다
            p2["meta"]["excluded_channels"] = zero
            p2["meta"]["excluded_sales"] = round(
                sum(r["sales"] or 0 for r in payload["facts"]["zero_discount_channels"]), 1)
            payload, spec = p2, spec2

    html = render.render(payload, spec.template, allow_literals=spec.allow_literals)
    rid = store.save(user_id=user_id, spec_id=spec.id, title=spec.title,
                     params=spec.params, payload=payload, html=html, question=question,
                     cache_key=phash)

    notes = []
    if params.get("_brand_downgraded"):
        notes.append(
            f"{params['_brand_downgraded']} 브랜드는 제품원가가 사실상 적재돼 있지 않아 "
            "(행의 99%가 0원) 원가 분석이 불가능합니다. SK 기준으로 만들었습니다."
        )

    return {
        "report_id": rid,
        "spec": spec.id,
        "title": spec.title,
        "url": f"/api/reports/{rid}",
        "period": spec.params.get("focus_label"),
        "summary": _summarize(spec.id, payload),
        "notes": notes,
        "gates_failed": sum(1 for g in payload.get("gates", []) if not g.get("passed")),
        "elapsed_sec": (payload.get("meta") or {}).get("elapsed_sec"),
    }


def to_markdown(result: Dict[str, Any]) -> str:
    """채팅 답변 본문."""
    parts = [f"**{result['title']}** · {result['period']} 기준 보고서를 만들었습니다.", ""]
    parts += [f"- {s}" for s in result["summary"]]
    for n in result.get("notes", []):
        parts += ["", f"> {n}"]
    parts += [
        "",
        f"[보고서 열기]({result['url']})  ·  본인만 열람할 수 있습니다.",
        "",
        "수치는 전부 조회 결과에서 왔고, 검산용 쿼리가 함께 저장돼 있습니다.",
    ]
    return "\n".join(parts)
