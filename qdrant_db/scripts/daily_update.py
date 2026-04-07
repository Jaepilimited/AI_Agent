"""
일일 업데이트 스크립트

- API 페이지: 증분 처리 (last_edited_time 기준, 변경된 것만 재적재)
- 공개 페이지 (People 팀 등): 매일 Playwright 재스크래핑

실행: python scripts/daily_update.py
"""

import sys
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.backfill_hub import backfill_hub
from app.core.logging import logger

LOG_PATH = Path(__file__).parent.parent / "logs" / "daily_update.log"


def _write_update_log(started_at: datetime, results: dict) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)

    ok_details = [d for d in results["details"] if d.get("status") == "ok"]
    error_details = [d for d in results["details"] if d.get("status") == "error"]

    lines = []
    lines.append("=" * 72)
    lines.append(f"실행 시간  : {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"완료 시간  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"실행 결과  : {'성공' if results['error'] == 0 else '일부 실패'}")
    lines.append(
        f"요약       : 전체 {results['total']}  "
        f"업데이트 {results['ok']}  "
        f"스킵 {results['skip']}  "
        f"실패 {results['error']}"
    )
    lines.append("")

    # 업데이트된 페이지
    lines.append("[업데이트된 페이지]")
    if ok_details:
        for d in ok_details:
            team = d.get("team", "-")
            title = d.get("page_title") or d.get("page_id", "-")
            chunks = d.get("chunks", 0)
            lines.append(f"  [{team}] {title}  ({chunks}개 chunk)")
    else:
        lines.append("  변경사항 없음")

    # 실패한 페이지
    if error_details:
        lines.append("")
        lines.append("[실패한 페이지]")
        for d in error_details:
            team = d.get("team", "-")
            page_id = d.get("page_id", "-")
            reason = d.get("reason", "알 수 없음")
            lines.append(f"  [{team}] {page_id}  ({reason})")

    lines.append("=" * 72)
    lines.append("")

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _write_error_log(started_at: datetime, error: Exception) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    lines = [
        "=" * 72,
        f"실행 시간  : {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"완료 시간  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "실행 결과  : 실패 (스크립트 오류)",
        f"오류       : {error}",
        "=" * 72,
        "",
    ]
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    started_at = datetime.now()
    logger.info(f"일일 업데이트 시작: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        # force_public=True: 공개 페이지(People 팀)는 항상 재스크래핑
        results = backfill_hub(force_public=True)

        _write_update_log(started_at, results)

        # 콘솔 출력
        print("\n========== 일일 업데이트 결과 ==========")
        print(
            f"전체: {results['total']}  "
            f"업데이트: {results['ok']}  "
            f"스킵: {results['skip']}  "
            f"실패: {results['error']}"
        )

        ok_details = [d for d in results["details"] if d.get("status") == "ok"]
        if ok_details:
            print("\n[업데이트된 페이지]")
            for d in ok_details:
                team = d.get("team", "-")
                title = d.get("page_title") or d.get("page_id", "-")
                print(f"  [{team}] {title}")
        else:
            print("\n변경사항 없음")

        print(f"\n업데이트 로그: {LOG_PATH}")

        if results["error"] > 0:
            sys.exit(1)

    except Exception as e:
        logger.error(f"일일 업데이트 실패: {e}")
        _write_error_log(started_at, e)
        sys.exit(1)
