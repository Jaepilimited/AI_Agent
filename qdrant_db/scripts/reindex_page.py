"""
특정 페이지 재색인 스크립트

실행:
  python scripts/reindex_page.py --page-id <PAGE_ID> --team <TEAM>
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ingest_page import ingest_page
from app.core.logging import logger

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="특정 페이지 재색인")
    parser.add_argument("--page-id", required=True, help="Notion page ID")
    parser.add_argument("--team", required=True, help="팀 이름")
    args = parser.parse_args()

    result = ingest_page(page_id=args.page_id, team=args.team, hub_id="hub_main")
    print(f"\n재색인 결과: {result}")
