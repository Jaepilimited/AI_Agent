# -*- coding: utf-8 -*-
"""같은 질문을 **여러 모델로** 보고서로 만들어 나란히 견준다.

이 파이프라인에서 LLM 이 관여하는 곳은 두 군데뿐이다:
    1. `planner`  — 어떤 절을 어떤 지표·축으로 (숫자·SQL 은 쓰지 않는다)
    2. `insight`  — 해석과 다음 할 일 (수치는 조회 결과와 대조해 검증된다)
따라서 **모델을 바꿔도 표의 숫자는 달라지지 않는다.** 달라지는 것은 *무엇을 볼지*와
*어떻게 읽을지*다 — 비교도 그 둘만 본다.

    python scripts/compare_report_models.py "2026 상반기 일본 매출 보고서 만들어줘"
    python scripts/compare_report_models.py "..." --models opus,flash --html

⚠️ 모델당 BigQuery 조회가 8~12회 돈다. 비교는 필요할 때만.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ⚠️ Windows 콘솔은 cp949 라 '—' 한 글자에 UnicodeEncodeError 로 죽는다.
#    실제로 두 모델을 다 돌리고 **출력 단계에서 결과를 통째로 잃었다** (2026-08-14).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _client(name: str):
    """이름 → LLM 클라이언트. 없는 이름이면 그대로 알려준다.

    보고서 기본값은 `get_llm_client()` — 인자를 안 주면 Gemini(Pro) 다.
    """
    from app.core.llm import MODEL_CLAUDE, MODEL_GEMINI, get_flash_client, get_llm_client
    table = {
        "gemini": lambda: get_llm_client(MODEL_GEMINI),   # 현재 보고서 기본값
        "claude": lambda: get_llm_client(MODEL_CLAUDE),   # Opus — 채팅 direct 경로와 같은 모델
        "flash": get_flash_client,                        # 빠르고 싸다
    }
    if name not in table:
        raise SystemExit(f"모르는 모델: {name} (쓸 수 있는 값: {', '.join(table)})")
    return table[name]()


def run_one(question: str, model: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    from app.reports import dynamic as D
    from app.reports import insight as INS
    from app.reports import planner as P

    llm = _client(model)
    t0 = time.time()
    plan = P.plan(question, ctx, llm=llm)          # ① 계획을 이 모델로
    t_plan = time.time() - t0

    _orig = INS.build

    def _patched(q, sections, c, llm_=None, already=None):   # ② 해석도 이 모델로
        return _orig(q, sections, c, llm=llm, already=already)

    INS.build = _patched
    try:
        payload = D.build(question, ctx, plan=plan)
    finally:
        INS.build = _orig

    lead = next((s for s in payload["sections"] if s["block"] == "lead"), {})
    return {
        "model": model,
        "plan_sec": round(t_plan, 1),
        "total_sec": payload["meta"]["elapsed_sec"],
        "queries": payload["meta"]["queries"],
        "intent": plan.get("intent", "-"),
        "title": payload["meta"]["title"],
        "sections": [s["block"] for s in payload["sections"]],
        "headlines": [s.get("headline", "") for s in payload["sections"] if s.get("headline")],
        "insights": lead.get("insights") or [],
        "actions": [a.get("title", "") for a in (lead.get("actions") or [])],
        "insight_dropped": lead.get("dropped", 0),
        "planner_dropped": len(plan.get("dropped") or []),
        "skipped": payload["meta"].get("skipped") or [],
        "payload": payload,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--models", default="gemini,claude",
                    help="쉼표로 구분 (gemini,claude,flash)")
    ap.add_argument("--html", action="store_true", help="모델별 HTML 도 저장")
    args = ap.parse_args()

    from app.reports import registry

    ctx = dict(registry.parse_period(args.question))
    ctx["base_filters"] = registry.extract_filters(args.question)

    results: List[Dict[str, Any]] = []
    for m in [x.strip() for x in args.models.split(",") if x.strip()]:
        print(f"\n▶ {m} 로 생성 중…", flush=True)
        try:
            results.append(run_one(args.question, m, dict(ctx)))
        except Exception as e:
            print(f"  실패: {str(e)[:200]}")

    if not results:
        raise SystemExit("생성된 결과가 없다")

    # 출력이 깨져도 결과는 남긴다 — 조회 8~12회를 모델 수만큼 태운 것을 잃지 않기 위해.
    # 실제로 두 모델을 다 돌리고 **출력 단계의 인코딩 오류로 통째로 잃었다** (2026-08-14)
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "docs", "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "compare_last.json"), "w", encoding="utf-8") as fh:
        json.dump([{k: v for k, v in r.items() if k != "payload"} for r in results],
                  fh, ensure_ascii=False, indent=1)

    print("\n" + "=" * 78)
    print(f"질문: {args.question}")
    print(f"필터: {ctx.get('base_filters') or '(없음)'} · 유형: {results[0]['intent']}")
    print("=" * 78)
    print(f"\n{'':<14}" + "".join(f"{r['model']:<22}" for r in results))
    def row(label, fn):
        print(f"{label:<14}" + "".join(f"{str(fn(r)):<22}" for r in results))
    row("총 시간", lambda r: f"{r['total_sec']}s")
    row("계획 시간", lambda r: f"{r['plan_sec']}s")
    row("조회 수", lambda r: r["queries"])
    row("절 수", lambda r: len(r["sections"]))
    row("해석 문장", lambda r: len(r["insights"]))
    row("해석 버림", lambda r: r["insight_dropped"])
    row("계획 버림", lambda r: r["planner_dropped"])
    row("빈 절", lambda r: len(r["skipped"]))

    for r in results:
        print(f"\n── {r['model']} · {r['title']}")
        print(f"   절: {' → '.join(r['sections'])}")
        for h in r["headlines"][:3]:
            print(f"   결론: {h[:96]}")
        for i in r["insights"][:3]:
            print(f"   해석: {i[:96]}")
        if r["actions"]:
            print(f"   할 일: {', '.join(r['actions'])}")

    # 어느 쪽이 나은지는 사람이 본다 — 자동 점수를 매기지 않는다.
    # (⛔ LLM 이 LLM 을 채점하게 하면 근거 없는 숫자가 하나 더 생길 뿐이다)
    print("\n" + "-" * 78)
    print("판단은 사람이 한다. 볼 것: 질문에 답했는가 · 절 순서가 논지를 만드는가 ·")
    print("해석이 표를 되풀이하지 않는가 · 버려진 문장이 많지 않은가")

    if args.html:
        from app.reports import render
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "docs", "reports")
        os.makedirs(out, exist_ok=True)
        for r in results:
            html = render.render(r["payload"], "dynamic.html", allow_literals=[])
            path = os.path.join(out, f"compare_{r['model']}.html")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(html)
            print(f"  저장: {path}")


if __name__ == "__main__":
    main()
