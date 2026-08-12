# -*- coding: utf-8 -*-
"""보고서 생성 — 스펙을 실행해 payload·HTML·검증 SQL 을 만든다.

    python scripts/build_report.py cost_efficiency
    python scripts/build_report.py cost_efficiency --out docs/reports
    python scripts/build_report.py cost_efficiency --lint-only   # 조회 없이 템플릿만 점검

품질 게이트가 잡아낸 '할인 미적재 채널'은 2회차에 자동으로 제외 파라미터에 반영된다 —
사람이 채널 이름을 옮겨 적는 경로를 두지 않기 위해서다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

SPECS = {
    "cost_efficiency": "app.reports.specs.cost_efficiency",
}


def load_spec(name: str, **overrides):
    import importlib
    mod = importlib.import_module(SPECS[name])
    return mod.build_spec(**overrides)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", choices=sorted(SPECS))
    ap.add_argument("--out", default=os.path.join(BASE_DIR, "docs", "reports"))
    ap.add_argument("--lint-only", action="store_true", help="조회 없이 템플릿 린트만")
    ap.add_argument("--no-exclude", action="store_true",
                    help="게이트가 잡은 채널을 제외하지 않고 1회차만 돌린다")
    args = ap.parse_args()

    from app.reports import engine, render

    spec = load_spec(args.spec)

    if args.lint_only:
        path = os.path.join(render.TEMPLATE_DIR, spec.template)
        with open(path, encoding="utf-8") as fh:
            problems = render.lint_template(fh.read(), spec.allow_literals)
        if problems:
            print(f"❌ 서술에 하드코딩된 숫자 {len(problems)}곳:")
            for n, t in problems:
                print(f"   {n}행: {t}")
            return 1
        print("✅ 서술에 하드코딩된 숫자 없음 — 모든 수치가 payload 에서 온다")
        return 0

    os.makedirs(args.out, exist_ok=True)

    print(f"[1/3] 조회 — {len(spec.facts)}종")
    payload = engine.build_payload(spec)

    # 게이트가 잡은 채널을 제외하고 재집계 (2회차)
    zero = [r["channel"] for r in payload["facts"].get("zero_discount_channels", [])]
    if zero and not args.no_exclude:
        lost = round(sum(r["sales"] or 0 for r in payload["facts"]["zero_discount_channels"]), 1)
        print(f"[2/3] 재집계 — 할인 미적재 채널 {len(zero)}개 제외 (매출 {lost}억)")
        spec2 = load_spec(args.spec,
                          excluded_channels=", ".join(f"'{c}'" for c in zero))
        payload2 = engine.build_payload(spec2)
        # 게이트 결과는 '제외 전' 사실이므로 1회차 것을 남긴다
        payload2["gates"] = payload["gates"]
        payload2["meta"]["excluded_channels"] = zero
        payload2["meta"]["excluded_sales"] = lost
        payload, spec = payload2, spec2
    else:
        print("[2/3] 재집계 생략")

    stem = os.path.join(args.out, spec.id)
    with open(stem + ".payload.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    engine.write_verification_sql(spec, stem + ".verify.sql")
    render.write(payload, spec.template, stem + ".html",
                 allow_literals=spec.allow_literals)

    print(f"[3/3] 완료\n  {stem}.html\n  {stem}.payload.json\n  {stem}.verify.sql")
    failed = [g for g in payload["gates"] if not g["passed"]]
    if failed:
        print(f"\n⚠️ 품질 게이트 {len(failed)}건이 본문에 공시된다:")
        for g in failed:
            print(f"   · {g['label']} — {g['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
