# -*- coding: utf-8 -*-
"""보고서 HTML → PDF, 그리고 렌더 검증.

`data-report-ready="true"` 를 기다린다. JS 가 실패하면 플래그가 서지 않아 타임아웃으로
실패하고 **깨진 PDF 를 남기지 않는다** — 원본 파이프라인의 장치를 그대로 가져왔다.

    python scripts/render_report_pdf.py docs/reports/cost_efficiency.html
    python scripts/render_report_pdf.py docs/reports/cost_efficiency.html --check-only
"""
from __future__ import annotations

import argparse
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--out", default="")
    ap.add_argument("--check-only", action="store_true",
                    help="PDF 없이 렌더 성공 여부와 표 행 수만 확인")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    src = os.path.abspath(args.html)
    out = args.out or os.path.splitext(src)[0] + ".pdf"

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("file:///" + src.replace("\\", "/"))
        try:
            page.wait_for_selector("html[data-report-ready='true']", timeout=15000)
        except Exception:
            print("❌ 렌더 실패 — data-report-ready 가 서지 않았다 (JS 오류 가능)")
            for e in errors:
                print("   JS:", e)
            b.close()
            return 1

        stats = page.evaluate("""() => {
            const o = {};
            document.querySelectorAll('table[id]').forEach(t => {
                o[t.id] = t.querySelectorAll('tbody tr').length;
            });
            o._gates = document.querySelectorAll('#gates .note, #gates .ok').length;
            o._missing = document.querySelectorAll('.missing').length;
            return o;
        }""")
        print("렌더 OK — 표 행 수:")
        for k, v in stats.items():
            if not k.startswith("_"):
                print(f"   {k}: {v}행")
        print(f"   품질 게이트 표시: {stats['_gates']}건 / 미치환 슬롯: {stats['_missing']}개")
        if errors:
            print("   ⚠️ JS 오류:", errors)

        empty = [k for k, v in stats.items() if not k.startswith("_") and v == 0]
        if empty:
            print(f"❌ 비어 있는 표: {empty}")
            b.close()
            return 1

        if not args.check_only:
            page.pdf(path=out, format="A4", landscape=True, print_background=True,
                     margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"})
            print(f"PDF: {out}")
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
