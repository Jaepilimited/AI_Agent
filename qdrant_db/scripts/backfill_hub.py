"""
Hub 전체 backfill 스크립트

실행:
  python scripts/backfill_hub.py                  # 증분 처리 (변경된 페이지만)
  python scripts/backfill_hub.py --team 마케팅    # 특정 팀만 증분 처리
  python scripts/backfill_hub.py --force          # 전체 강제 재적재
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.backfill_hub import backfill_hub
from app.core.logging import logger

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Notion Hub 전체 backfill")
    parser.add_argument("--team", type=str, default=None, help="특정 팀만 처리")
    parser.add_argument("--force", action="store_true", help="변경 없어도 전체 강제 재적재")
    args = parser.parse_args()

    results = backfill_hub(team_filter=args.team, force=args.force)

    print("\n========== backfill 결과 ==========")
    print(f"전체: {results['total']}  성공: {results['ok']}  스킵: {results['skip']}  실패: {results['error']}")

    if results["error"] > 0:
        print("\n실패 목록:")
        for d in results["details"]:
            if d["status"] == "error":
                print(f"  - {d['page_id']}: {d.get('reason', '')}")
