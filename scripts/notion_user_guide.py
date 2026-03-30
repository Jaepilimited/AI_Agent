"""
SKIN1004 AI 처음 사용자 가이드 → Notion (v3 — 핵심만)
"""
import os, sys, time
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts.upload_to_notion import (
    get_token, headers, rich_text, paragraph, heading1, heading2, heading3,
    toggle, bulleted, divider, callout, table_block,
    append_blocks, get_children, delete_block,
)

PAGE_ID = "3262b4283b0080a3a12bfa86d0df705d"


def colored_callout(text, emoji, color):
    return {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": rich_text(text),
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color,
        },
    }


def numbered_item(text):
    return {
        "object": "block", "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": rich_text(text)},
    }


def quote_block(text):
    return {
        "object": "block", "type": "quote",
        "quote": {"rich_text": rich_text(text)},
    }


def column_list(columns):
    return {
        "object": "block", "type": "column_list",
        "column_list": {"children": [
            {"object": "block", "type": "column", "column": {"children": blks}}
            for blks in columns
        ]},
    }


def clear_page(token, page_id):
    children = get_children(token, page_id)
    print(f"  Clearing {len(children)} blocks...")
    for c in children:
        delete_block(token, c["id"])
        time.sleep(0.2)


def build():
    b = []

    # ── 헤더 ──
    b.append(colored_callout(
        "SKIN1004 AI 사용 가이드",
        "✨", "blue_background",
    ))
    b.append(paragraph(""))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 1. 가입 & 로그인
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    b.append(heading1("가입 & 로그인"))
    b.append(paragraph(""))
    b.append(colored_callout(
        "사내 AD(Active Directory)에 등록된 사원만 가입 가능합니다.",
        "🔒", "red_background",
    ))
    b.append(paragraph(""))

    b.append(column_list([
        [colored_callout(
            "회원가입\n\n"
            "이름 입력 → 소속 팀 자동 표시\n"
            "→ 비밀번호 설정 → 완료",
            "📝", "yellow_background",
        )],
        [colored_callout(
            "로그인\n\n"
            "이름 입력 → 팀 확인\n"
            "→ 비밀번호 입력 → 완료",
            "🔑", "blue_background",
        )],
    ]))
    b.append(paragraph(""))
    b.append(quote_block("비밀번호 분실 시 DB팀(jeffrey@skin1004korea.com)에 초기화 요청"))
    b.append(paragraph(""))
    b.append(divider())

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 2. 이런 걸 물어볼 수 있어요
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    b.append(heading1("이런 걸 물어볼 수 있어요"))
    b.append(paragraph("질문하면 AI가 자동으로 최적 경로를 선택합니다. 경로를 직접 고를 필요 없어요."))
    b.append(paragraph(""))

    # Row 1
    b.append(column_list([
        [colored_callout(
            "📊 매출 데이터\n\n"
            "\"쇼피 인도네시아 이번 달 매출\"\n"
            "\"아마존 미국 Top 10 제품\"\n"
            "\"국가별 매출 순위\"",
            "📊", "orange_background",
        )],
        [colored_callout(
            "📋 사내 문서 (Notion)\n\n"
            "\"틱톡샵 접속 방법\"\n"
            "\"해외 출장 가이드\"\n"
            "\"스마트스토어 운영 방법\"",
            "📋", "yellow_background",
        )],
        [colored_callout(
            "🧴 CS / 제품 정보\n\n"
            "\"센텔라 앰플 성분 알려줘\"\n"
            "\"클렌징 오일 사용법\"\n"
            "\"반품 정책 알려줘\"",
            "🧴", "green_background",
        )],
    ]))
    # Row 2
    b.append(column_list([
        [colored_callout(
            "📧 Google 서비스\n\n"
            "\"오늘 일정 알려줘\"\n"
            "\"안 읽은 메일 확인\"\n"
            "(Google 연결 필요)",
            "📧", "blue_background",
        )],
        [colored_callout(
            "🔀 복합 분석\n\n"
            "\"인도네시아 시장 분석\n리포트 만들어줘\"\n"
            "(데이터+문서+웹 종합)",
            "🔀", "purple_background",
        )],
        [colored_callout(
            "💬 일반 질문\n\n"
            "\"ROAS가 뭐야?\"\n"
            "\"CAC 계산법 알려줘\"\n"
            "(일반 상식/용어)",
            "💬", "gray_background",
        )],
    ]))
    b.append(paragraph(""))
    b.append(divider())

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 3. ChatGPT와 다른 점
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    b.append(heading1("ChatGPT / Gemini와 다른 점"))
    b.append(paragraph(""))
    b.append(table_block([
        ["", "ChatGPT / Gemini", "SKIN1004 AI"],
        ["매출 데이터", "모름 (외부 서비스)", "BigQuery 실시간 조회"],
        ["사내 문서", "접근 불가", "Notion 문서 자동 검색"],
        ["CS/제품 정보", "일반 정보만", "SKIN1004 제품 DB 직접 참조"],
        ["Google 연동", "별도 앱 필요", "Gmail·캘린더·드라이브 통합"],
        ["데이터 출처", "출처 불명", "답변마다 출처·기준일 표시"],
    ]))
    b.append(paragraph(""))
    b.append(divider())

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 4. 알아두면 좋은 것
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    b.append(heading1("알아두면 좋은 것"))
    b.append(paragraph(""))

    b.append(toggle("📎 이미지 첨부 분석", [
        paragraph("입력창 왼쪽 📎 버튼으로 이미지를 첨부하면 AI가 분석합니다."),
        bulleted("PNG, JPG, GIF, WebP 지원 · 여러 장 동시 가능"),
        bulleted("예: 매출 스크린샷 + \"이 데이터 분석해줘\""),
    ]))

    b.append(toggle("🔗 Google 계정 연결", [
        paragraph("상단 「Google 연결」 버튼 → 사내 Google 계정 로그인 → 완료"),
        paragraph("연결하면 Gmail·캘린더·드라이브 관련 질문이 가능합니다."),
        callout("선택 사항입니다. 매출·노션·CS 기능은 연결 없이 사용 가능!", "💡"),
    ]))

    b.append(toggle("💡 후속 질문 칩", [
        paragraph("AI 답변 아래에 후속 질문 칩이 자동 생성됩니다."),
        paragraph("클릭하면 관련 질문을 바로 이어갈 수 있어요."),
    ]))

    b.append(toggle("📊 차트 자동 생성", [
        paragraph("매출 데이터 질문 시 답변에 차트가 자동으로 포함됩니다."),
        paragraph("더 구체적으로 질문할수록 정확한 시각화가 나옵니다."),
    ]))

    b.append(toggle("🔍 질문 팁", [
        paragraph("구체적일수록 좋습니다:"),
        bulleted("❌ \"매출 알려줘\""),
        bulleted("✅ \"2026년 1월 쇼피 인도네시아 SKU별 매출 Top 5\""),
        paragraph(""),
        paragraph("기간·국가·플랫폼을 명시하면 정확도가 올라갑니다."),
    ]))
    b.append(paragraph(""))
    b.append(divider())

    # ── Footer ──
    b.append(colored_callout(
        "문의: DB팀 · jeffrey@skin1004korea.com\n"
        "마지막 업데이트: 2026-03-17",
        "📮", "gray_background",
    ))

    return b


def main():
    token = get_token()
    print(f"Target: {PAGE_ID}")
    clear_page(token, PAGE_ID)
    blocks = build()
    print(f"  Uploading {len(blocks)} blocks...")
    ok = append_blocks(token, PAGE_ID, blocks)
    print("✅ Done!" if ok else "❌ Failed")


if __name__ == "__main__":
    main()
