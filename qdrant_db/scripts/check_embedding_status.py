"""
Hub 기준 임베딩 현황을 터미널에서 점검하는 스크립트.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.embedding_diagnostics import generate_embedding_diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description="Notion hub 임베딩 현황 진단")
    parser.add_argument("--team", type=str, default=None, help="특정 팀만 진단")
    parser.add_argument(
        "--show",
        choices=["action", "missing", "outdated", "extra", "embedded", "all"],
        default="action",
        help="터미널에 표시할 상세 목록 종류",
    )
    parser.add_argument("--limit", type=int, default=15, help="상세 목록 최대 출력 개수")
    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="전체 진단 결과를 JSON 파일로 저장할 경로",
    )
    parser.add_argument(
        "--skip-meta",
        action="store_true",
        help="Notion page 메타데이터 조회를 생략해 더 빠르게 실행",
    )
    args = parser.parse_args()

    report = generate_embedding_diagnostics(
        team_filter=args.team,
        resolve_metadata=not args.skip_meta,
    )

    _print_summary(report)
    _print_team_summary(report["team_summary"])
    _print_details(report, show=args.show, limit=args.limit)

    if args.save:
        _save_report(report, args.save)

    return 0


def _print_summary(report: dict) -> None:
    summary = report["summary"]
    print("\n=== 임베딩 현황 진단 ===")
    print(f"hub_id: {report['hub_id']}")
    if report.get("team_filter"):
        print(f"team_filter: {report['team_filter']}")
    print(f"Qdrant collection exists: {'yes' if report['collection_exists'] else 'no'}")
    print(
        "expected={expected_pages} embedded={embedded_pages} missing={missing_pages} "
        "outdated={outdated_pages} extra={extra_pages} metadata_error={metadata_error_pages}".format(
            **summary
        )
    )


def _print_team_summary(team_summary: list[dict]) -> None:
    if not team_summary:
        print("\n[팀별 요약] 없음")
        return

    print("\n[팀별 요약]")
    headers = ["TEAM", "EXPECTED", "EMBEDDED", "MISSING", "OUTDATED", "EXTRA"]
    rows = [
        [
            row["team"],
            str(row["expected_pages"]),
            str(row["embedded_pages"]),
            str(row["missing_pages"]),
            str(row["outdated_pages"]),
            str(row["extra_pages"]),
        ]
        for row in team_summary
    ]
    _print_table(headers, rows)


def _print_details(report: dict, show: str, limit: int) -> None:
    sections = _select_sections(show)

    for title, key in sections:
        rows = report.get(key, [])
        if not rows:
            continue

        print(f"\n[{title}] total={len(rows)}")
        headers = ["STATUS", "TEAM", "TITLE", "CHUNKS", "PAGE_ID", "NOTE"]
        table_rows = [
            [
                row.get("status", key.replace("_pages", "")),
                row.get("team", ""),
                _truncate(row.get("title", ""), 36),
                str(row.get("chunk_count", 0)),
                _truncate(row.get("page_id", ""), 36),
                _truncate(_build_note(row), 48),
            ]
            for row in rows[:limit]
        ]
        _print_table(headers, table_rows)

        if len(rows) > limit:
            print(f"... {len(rows) - limit}개 추가 항목 생략")

    if show != "all":
        print("\n전체 목록이 필요하면 --show all 또는 --save <path>를 사용하세요.")


def _select_sections(show: str) -> list[tuple[str, str]]:
    mapping = {
        "missing": [("미임베딩", "missing_pages")],
        "outdated": [("오래된 인덱스", "outdated_pages")],
        "extra": [("Hub 밖 잔존 데이터", "extra_pages")],
        "embedded": [("정상 임베딩", "embedded_pages")],
        "action": [
            ("미임베딩", "missing_pages"),
            ("오래된 인덱스", "outdated_pages"),
            ("Hub 밖 잔존 데이터", "extra_pages"),
        ],
        "all": [
            ("미임베딩", "missing_pages"),
            ("오래된 인덱스", "outdated_pages"),
            ("Hub 밖 잔존 데이터", "extra_pages"),
            ("정상 임베딩", "embedded_pages"),
        ],
    }
    return mapping[show]


def _build_note(row: dict) -> str:
    if row.get("reason"):
        return row["reason"]
    if row.get("page_url"):
        return row["page_url"]
    if row.get("metadata_error"):
        return f"metadata_error={row['metadata_error']}"
    return ""


def _save_report(report: dict, save_path: str) -> None:
    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n진단 결과 저장: {path}")


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(header) for header in headers]

    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = min(max(widths[index], len(cell)), 48)

    print(" | ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))

    for row in rows:
        print(" | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row)))


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
