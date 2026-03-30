"""
Upload 2026-03-18 update log to Notion as a date toggle (prepend at top).
Uses the same pattern as upload_to_notion.py incremental mode.
"""
import sys
import os
import time
import httpx

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

# Import helpers from upload_to_notion
from scripts.upload_to_notion import (
    get_token, headers, rich_text, paragraph, heading2, heading3,
    bulleted, divider, callout, toggle, table_block,
    append_blocks, append_blocks_get_ids, get_children,
    PAGE_ID, MAX_BLOCKS_PER_CALL,
)

DATE = "2026-03-18"
VERSION = "v8.1"
TITLE = f"{DATE} | {VERSION} (컨텍스트 무제한 + SQL 프롬프트 대규모 수정)"


def build_blocks() -> list:
    """Build the blocks for today's update log toggle."""
    blocks = []

    # Summary callout (visible when toggle is collapsed)
    blocks.append(callout(
        "채팅 마키 제거 / 컨텍스트 무제한 확장 / Stop Hook / "
        "MariaDB PATH+비밀번호 / 메타 광고 SQL 규칙 16개 + 예시 14개 / Notion 사용자 가이드",
        "📋"
    ))

    # 1. 채팅 화면 마키 제거
    blocks.append(heading3("1. 채팅 화면 마키 제거"))
    blocks.append(bulleted("app/frontend/chat.html에서 craver-bg 마키 배경(텍스트+이미지) 전체 삭제"))
    blocks.append(bulleted("채팅 화면 배경 정리 — 깔끔한 인터페이스로 전환"))

    # 2. 컨텍스트 길이 무제한 확장
    blocks.append(heading3("2. 컨텍스트 길이 무제한 확장"))
    blocks.append(bulleted("routes.py: MAX_CONTEXT_MESSAGES 제한 완전 제거 (기존 50개 → 무제한)"))
    blocks.append(bulleted("orchestrator.py: _build_conversation_context() 턴 제한 제거 + 잘림(truncation) 제거"))
    blocks.append(bulleted("Gemini 1M 토큰 컨텍스트 윈도우 전체 활용 가능"))

    # 3. Stop Hook 추가
    blocks.append(heading3("3. Stop Hook 추가"))
    blocks.append(bulleted(".claude/settings.json에 Stop hook 설정"))
    blocks.append(bulleted("Claude 응답 종료 시 port 3000 health check 자동 실행"))
    blocks.append(bulleted("서버 다운 감지 시 재시작 강제"))

    # 4. MariaDB PATH 등록
    blocks.append(heading3("4. MariaDB PATH 등록"))
    blocks.append(bulleted('시스템 PATH에 "C:\\Program Files\\MariaDB 11.7\\bin" 추가'))
    blocks.append(bulleted("mysql 명령어 터미널에서 직접 사용 가능"))

    # 5. MariaDB 비밀번호 변경
    blocks.append(heading3("5. MariaDB 비밀번호 변경"))
    blocks.append(bulleted(".env MARIADB_PASSWORD 업데이트 → skin1004!"))

    # 6. 메타 광고 SQL 프롬프트 대규모 수정
    blocks.append(heading3("6. 메타 광고 SQL 프롬프트 대규모 수정"))
    blocks.append(bulleted("QA 시트7 검증 결과 82건 실패 분석 → 16가지 반복 패턴 추출"))
    blocks.append(bulleted("필수 규칙 16개 추가: brand 소문자, publisher_platform LIKE, 불필요 WHERE 금지 등"))
    blocks.append(bulleted("UNNEST / ROW_NUMBER / snapshot JSON 처리 규칙 추가"))
    blocks.append(bulleted("예시 쿼리 8개 → 14개로 확장"))
    blocks.append(bulleted("메타 광고 테이블 전용 규칙 체계화 (prompts/sql_generator.txt)"))

    # 7. Notion 사용자 가이드 작성
    blocks.append(heading3("7. Notion 사용자 가이드 작성"))
    blocks.append(bulleted("처음 사용자용 AI Agent 가이드 페이지 Notion 업로드"))
    blocks.append(bulleted("가이드 내용: 가입/로그인, 질문 유형별 예시, ChatGPT와 다른 점, 고급 기능 활용법"))

    # 수정 파일 테이블
    blocks.append(heading3("수정 파일"))
    blocks.append(table_block([
        ["파일", "변경 내용"],
        ["app/frontend/chat.html", "craver-bg 마키 배경 전체 삭제"],
        ["app/api/routes.py", "MAX_CONTEXT_MESSAGES 제한 제거 (무제한)"],
        ["app/agents/orchestrator.py", "_build_conversation_context() 턴/잘림 제한 제거"],
        [".claude/settings.json", "Stop hook 추가 (health check + 재시작)"],
        ["prompts/sql_generator.txt", "메타 광고 필수 규칙 16개 + 예시 14개 확장"],
    ]))

    return blocks


def main():
    token = get_token()
    print(f"Target page: {PAGE_ID}")
    print(f"Uploading: {TITLE}")

    blocks = build_blocks()

    # Separate callout (first block) as toggle summary, rest as details
    summary_block = blocks[0]  # callout
    detail_blocks = blocks[1:]

    # Create the date toggle with summary inside
    date_toggle = toggle(TITLE, [summary_block])

    # Find existing page blocks
    hdrs = headers(token)
    r = httpx.get(
        f"https://api.notion.com/v1/blocks/{PAGE_ID}/children?page_size=100",
        headers=hdrs, timeout=30,
    )
    page_blocks = r.json().get("results", [])

    # Find the insert point: paragraph with "위에 추가됩니다"
    insert_after_id = None
    existing_toggle_ids = []
    date_prefix = DATE  # "2026-03-18"

    for b in page_blocks:
        btype = b["type"]
        plain = "".join(
            seg.get("plain_text", "")
            for seg in b.get(btype, {}).get("rich_text", [])
        ) if btype not in ("divider", "table") else ""

        # Find insert point (paragraph after Update Log heading)
        if btype == "paragraph" and "위에 추가됩니다" in plain:
            insert_after_id = b["id"]

        # Collect ALL toggles with same date prefix (for replacement)
        if btype == "toggle" and date_prefix in plain[:15]:
            existing_toggle_ids.append(b["id"])

    # Delete existing toggles for the same date (replacement)
    for old_id in existing_toggle_ids:
        httpx.delete(
            f"https://api.notion.com/v1/blocks/{old_id}",
            headers=hdrs, timeout=30,
        )
        time.sleep(0.3)
    if existing_toggle_ids:
        print(f"  Deleted {len(existing_toggle_ids)} existing toggle(s) for {date_prefix}")

    # Insert the toggle
    if insert_after_id:
        # Insert after the description paragraph (prepend position)
        r = httpx.patch(
            f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
            headers=hdrs,
            json={"children": [date_toggle], "after": insert_after_id},
            timeout=60,
        )
        if r.status_code == 200:
            toggle_id = r.json().get("results", [{}])[0].get("id")
            print(f"  Inserted toggle: {TITLE}")
        else:
            print(f"  ERROR inserting: {r.status_code} {r.text[:300]}")
            return
    else:
        # Fallback: append at end
        ids = append_blocks_get_ids(token, PAGE_ID, [date_toggle])
        if ids:
            toggle_id = ids[0]
            print(f"  Appended toggle (fallback): {TITLE}")
        else:
            print("  ERROR creating toggle")
            return

    # Append detail blocks inside the toggle
    if toggle_id and detail_blocks:
        time.sleep(0.3)
        append_blocks(token, toggle_id, detail_blocks)
        print(f"  Added {len(detail_blocks)} detail blocks inside toggle")

    print(f"\nDone! Total blocks: {len(blocks)}")
    print(f"Check: https://notion.so/{PAGE_ID.replace('-', '')}")


if __name__ == "__main__":
    main()
