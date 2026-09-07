# -*- coding: utf-8 -*-
"""노션 쓰기 관통 확인 — 수동 실행.

이 프로젝트는 `2025-09-03` 을 써 본 적이 없다(전부 `2022-06-28` 읽기 전용).

⛔ **실제 사용 경로(`resolve_target`·`save`)를 그대로 태운다.** 예전 버전은 DB 생성만
   직접 호출해 "관통 성공"을 찍었는데, 그러면 스키마를 데이터 소스가 아니라 DB 객체에서
   읽던 결함이 있어도 통과한다 — **기존 DB 에 대한 두 번째 저장부터 전부 실패하는** 결함이
   실제로 있었다(`_load_database`). 이 스크립트는 그 결함이 있으면 두 번째 저장에서 죽는다.

    python scripts/notion_write_probe.py <노션_페이지_URL>

⚠️ 콘솔이 cp949 다 — 아래 래핑이 없으면 한글 출력에서 죽는다.
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ⚠️ `python scripts/notion_write_probe.py` 로 바로 돌릴 수 있어야 한다 —
#    저장소 루트가 sys.path 에 없으면 `No module named 'app'` 로 죽는다 (2026-09-08 실측).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import notion_export as nx  # noqa: E402

_BODY = (
    "관통 확인 본문입니다.\n\n"
    "| 국가 | 매출 |\n"
    "|---|---|\n"
    "| 일본 | 55.1억 |\n"
)


def main() -> int:
    try:
        return _run()
    except nx.NotionError as exc:
        # ⚠️ 트레이스백 대신 읽을 수 있는 한 줄로 끝낸다 — 이 스크립트는 사람이
        #    직접 돌려 보는 진단 도구다.
        print(f"⛔ {exc.kind}: {exc}")
        return 1


def _run() -> int:
    if len(sys.argv) < 2:
        print("사용법: python scripts/notion_write_probe.py <노션_페이지_URL>")
        return 2
    if not nx.is_enabled():
        print("NOTION_WRITE_TOKEN 이 비어 있다. .env 에 넣고 다시 실행할 것.")
        return 2

    url = sys.argv[1]
    page_id = nx.parse_page_url(url)
    print(f"page_id = {page_id}")
    if not page_id:
        return 2

    # 1) DB 를 만든다 (또는 이미 있으면 그대로 쓴다) — 실제 사용 경로와 같다.
    target = nx.resolve_target(0, url)
    print(f"database_id      = {target.database_id}")
    print(f"data_source_id   = {target.data_source_id}")
    print(f"created          = {target.created}")

    # 2) 그 DB 의 URL 로 **다시** resolve — 여기서 스키마가 비면 결함이다.
    db_url = f"https://www.notion.so/{target.database_id.replace('-', '')}"
    reread = nx.resolve_target(0, db_url)
    print(f"properties (재조회) = {reread.properties}")
    if not reread.properties:
        print("⛔ 스키마가 비었다 — _load_database 가 데이터 소스가 아니라 "
              "데이터베이스에서 스키마를 읽고 있을 가능성이 크다")
        return 1

    # 3) 첫 저장.
    first = nx.save(reread, "관통 확인 1회차", _BODY)
    print(f"row url (1회차)   = {first.url}")

    # 4) 두 번째 저장 — 이 결함의 증상이 여기서 나타난다("제목 속성이 없는 DB").
    second = nx.save(reread, "관통 확인 2회차", _BODY)
    print(f"row url (2회차)   = {second.url}")

    print("✅ 관통 성공 — 위 두 URL 을 열어 표가 표로 보이는지 눈으로 확인할 것. "
          "만들어진 `셀라` DB 는 노션에서 지워도 된다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
