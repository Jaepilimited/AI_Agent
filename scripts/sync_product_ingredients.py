"""제품 전성분 스프레드시트 → MariaDB 적재.

사용:
    python scripts/sync_product_ingredients.py --dry-run   # 매칭 현황만 확인
    python scripts/sync_product_ingredients.py             # 실제 적재

WAS 스케줄러가 매일 04:00 에 자동 실행한다 (id=ingredient_sync_daily).
외부 호출(Google Sheets + BigQuery)이 있으므로 프록시 경로가 필요하다.
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BASE_DIR, ".env"))

from app.core.ingredients import sync_ingredients  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="제품 전성분 적재")
    ap.add_argument("--dry-run", action="store_true", help="DB 변경 없이 매칭 현황만")
    args = ap.parse_args()

    stats = sync_ingredients(dry_run=args.dry_run)
    print("=" * 56)
    print("제품 전성분 적재" + (" (DRY RUN)" if args.dry_run else ""))
    print("=" * 56)
    print(f"  시트 제품          : {stats['sheet_products']}개")
    print(f"  그중 전성분 보유    : {stats['with_ingredients']}개")
    print(f"  BigQuery 제품      : {stats['bq_products']}종")
    print(f"  매칭됨             : {stats['matched']}종")
    print(f"  미매칭             : {stats['unmatched']}종 "
          f"(Sachet·기획세트 등 — 성분 '미상'이지 '미포함'이 아님)")
    rate = stats["matched"] / max(stats["bq_products"], 1) * 100
    print(f"  종수 기준 커버리지  : {rate:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
