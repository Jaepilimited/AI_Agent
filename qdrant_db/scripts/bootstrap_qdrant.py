"""
Qdrant 컬렉션 초기화 스크립트

실행:
  python scripts/bootstrap_qdrant.py             # 없으면 생성, 있으면 유지
  python scripts/bootstrap_qdrant.py --recreate  # 기존 컬렉션 삭제 후 재생성
"""

import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.qdrant.store import QdrantStore
from app.core.logging import logger

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true", help="기존 컬렉션 삭제 후 재생성")
    args = parser.parse_args()

    store = QdrantStore()

    if args.recreate:
        logger.info("--recreate 옵션: 컬렉션 삭제 후 재생성")
        store.recreate_collection()
    else:
        store.ensure_collection()

    info = store.get_collection_info()
    logger.info(f"컬렉션 상태: {info}")
