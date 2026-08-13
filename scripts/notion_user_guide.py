"""
SKIN1004 AI 처음 사용자 가이드 → Notion (v3 — 핵심만)

⛔⛔ **그냥 돌리지 마라. 페이지를 통째로 지운다.** ⛔⛔

이 스크립트는 `clear_page()` 로 대상 페이지의 모든 블록을 지우고 아래 build() 결과로
다시 채운다. 그런데 실제 페이지("AI Chat 가이드")는 2026-03 이후 **손으로 많이
편집됐다** — 스크린샷 여러 장, 대시보드 콘솔·Google Workspace·System Status 설명,
DB 정보, 추가 예정 DB, @@·피드백·사내문서 토글, ❓Tester 섹션. 여기 build() 에는
그중 어느 것도 없다. 돌리면 전부 사라진다 (2026-08-13 확인).

또한 `upload_to_notion` 이 블록 헬퍼(get_token·paragraph·table_block…)를 더 이상
내보내지 않아 **import 단계에서 이미 깨져 있다.** 즉 이 파일은 최소 두 가지 이유로
현재 실행 불가다.

가이드를 고칠 때는 **바꿀 구간만** 수정하라 (Notion MCP `update-page` 의
`update_content` 로 old_str/new_str 치환). 전체 재생성이 정말 필요하면 먼저
build() 를 현재 페이지와 맞춘 뒤, 페이지를 복제해 백업하고 돌려라.
"""
import os, sys, time
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

# 가드는 import 보다 **앞에** 둔다 — 아래 import 가 이미 깨져 있어서, 뒤에 두면
# 경고 대신 ImportError 만 보이고 왜 못 쓰는지 알 수 없다
if __name__ == "__main__" and "--i-know-this-wipes-the-page" not in sys.argv:
    try:  # 윈도우 콘솔(cp949)은 '—' 를 못 찍어 경고가 통째로 안 보인다
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(__doc__)
    print("중단했다. 실행하지 않았다.")
    raise SystemExit(0)

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
        # ⚠️ 예전 문구는 "…리포트 만들어줘" 였는데, 지금은 '리포트'라고 적으면
        #    복합 분석이 아니라 **보고서**가 만들어진다 (2026-08-13 규칙). 문구를 바꿨다
        [colored_callout(
            "🔀 복합 분석\n\n"
            "\"인도네시아 시장 상황\n종합해서 알려줘\"\n"
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
    # 3. 보고서로 받기
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    b.append(heading1("보고서로 받기"))
    b.append(paragraph(
        "여러 각도로 나눠 본 문서가 필요할 때 씁니다. 질문 하나로 총량·추세·구성·"
        "전년 대비·순위 같은 절을 조합해 만들어 주고, 채팅에는 요약 몇 줄과 "
        "[보고서 열기] 링크가 옵니다."))
    b.append(paragraph(""))

    b.append(colored_callout(
        "보고서는 \"보고서\"라고 적었을 때만 만들어집니다.\n"
        "조회를 여러 번 돌아 10~30초가 걸리는 기능이라, 원할 때만 나가도록 해 두었습니다.",
        "📄", "blue_background",
    ))
    b.append(paragraph(""))

    b.append(heading3("만드는 방법 두 가지"))
    b.append(numbered_item("질문에 \"보고서\"(또는 \"리포트\")라고 적습니다 — 예: \"2026년 일본 매출 보고서 만들어줘\""))
    b.append(numbered_item("입력창에 @@보고서 를 먼저 고릅니다 — 이때는 '보고서'라고 안 적어도 됩니다"))
    b.append(paragraph(""))

    b.append(heading3("이렇게 물어보세요"))
    b.append(paragraph("굵게 표시한 조건이 보고서의 모든 절에 그대로 적용됩니다."))
    b.append(table_block(
        ["질문 예시", "이렇게 좁혀집니다"],
        [
            ["2026년 일본 매출 보고서 만들어줘", "일본만"],
            ["일본 B2C 매출 보고서 — 달마다 오르내리는 원인 위주로", "일본 · B2C · 프로모션 일정 대조"],
            ["우마 브랜드 매출 보고서 만들어줘", "우마(UM)만"],
            ["스킨천사 상반기 매출 보고서", "스킨천사만 · 2026 상반기"],
            ["중국사업팀 상반기 실적 보고서 만들어줘", "중국사업팀 전체 (중국 국가로 좁히지 않음)"],
            ["동남아 채널별 매출 보고서 만들어줘", "동남아시아 권역"],
            ["미국 B2C 채널별 매출 보고서", "미국 · B2C"],
            ["SK FOC 바우처 비용 효율화 보고서 만들어줘", "무상출고·바우처 비용 전용 보고서"],
        ],
    ))
    b.append(paragraph(""))

    b.append(toggle("알아두면 좋은 것", [
        bulleted("기간을 안 적으면 직전에 끝난 반기로 만듭니다. \"2026년\"·\"상반기\"처럼 적으면 그대로 따릅니다."),
        bulleted("보고서는 만든 사람만 열 수 있습니다. 링크를 받아도 다른 사람은 열리지 않습니다."),
        bulleted("숫자는 전부 조회 결과에서 나옵니다. AI가 수치를 지어내지 않고, 검산용 쿼리가 함께 저장됩니다."),
        bulleted("데이터가 온전하지 않은 부분은 빼고, 뺐다는 사실과 이유를 본문에 적습니다."),
        bulleted("우마(UM)·CBT 는 제품명이 데이터에 없어 제품별 절이 나오지 않습니다. "
                 "안 팔린 것이 아니라 이름이 적재되지 않은 것이며, 보고서가 그 사실을 알려 줍니다."),
        paragraph(""),
        paragraph("❌ \"우마 매출 분석해줘\" → 보고서가 아니라 일반 답변이 옵니다."),
        paragraph("✅ \"우마 매출 보고서 만들어줘\""),
    ]))
    b.append(paragraph(""))
    b.append(divider())

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 4. ChatGPT와 다른 점
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
    # 실수로 실행해 페이지를 날리는 것을 막는다 (위 주석 참조).
    # 정말 전체 재생성이 필요하면 build() 를 현재 페이지와 맞춘 뒤 이 플래그로 돌린다.
    if "--i-know-this-wipes-the-page" not in sys.argv:
        print(__doc__)
        print("중단했다. 실행하지 않았다.")
        return

    token = get_token()
    print(f"Target: {PAGE_ID}")
    clear_page(token, PAGE_ID)
    blocks = build()
    print(f"  Uploading {len(blocks)} blocks...")
    ok = append_blocks(token, PAGE_ID, blocks)
    print("✅ Done!" if ok else "❌ Failed")


if __name__ == "__main__":
    main()
