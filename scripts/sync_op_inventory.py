# -*- coding: utf-8 -*-
"""OP 재고 시트 → MariaDB 적재 (수동 실행용).

자동 실행은 매일 04:10 `op_inventory_sync_daily` 다 (`app/main.py`).
이 스크립트는 시트를 고친 직후 바로 반영하고 싶을 때 쓴다.

사용:
    python scripts/sync_op_inventory.py            # 실제 적재
    python scripts/sync_op_inventory.py --dry-run  # 읽기만 (행 수·창고 목록 확인)
    python scripts/sync_op_inventory.py --check    # 현재 적재 상태만 본다
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(PROJ / ".env")
    from app.core.inventory import SHEET_TAB, SHEET_URL, status, sync_inventory

    if "--check" in sys.argv:
        st = status()
        print("적재 행 {:,} · SKU {:,} · 창고 {}곳".format(
            st["rows"], st["skus"], st["locations"]))
        print("마지막 적재:", st["last_sync"])
        print("시트 기준  :", st["sheet_updated_at"] or "(미상)")
        print("원본       :", SHEET_URL)
        return 0 if st["rows"] else 1

    dry = "--dry-run" in sys.argv
    print("===== OP 재고 적재 ({} · 탭 '{}') =====".format(
        "미리보기" if dry else "적용", SHEET_TAB))
    st = sync_inventory(dry_run=dry)
    print("  행 {:,} · SKU {:,}".format(st["rows"], st["skus"]))
    print("  창고:", ", ".join(st["locations"]) or "(없음)")
    print("  시트 기준:", st["sheet_updated_at"] or "(미상)")
    if not dry:
        print("  적재:", "{:,}".format(st["written"]))
        if st.get("removed"):
            print("  정리:", st["removed"])
        # ⛔ 정리가 거부됐다는 것은 적재가 반쯤 실패했다는 뜻이다 — 조용히 넘기지 않는다
        if st.get("cleanup_refused"):
            print("  ⚠️ 정리 거부: 오래된 행 {}개가 이번 적재분 이상이다. "
                  "시트 읽기가 부분 실패했을 수 있으니 다시 돌릴 것".format(
                      st["cleanup_refused"]))
            return 2
        after = status()
        print("  적재 후 상태: 행 {:,} · SKU {:,}".format(after["rows"], after["skus"]))
        if not after["rows"]:
            print("  ⛔ 적재 후 테이블이 비었다 — 실패다")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
