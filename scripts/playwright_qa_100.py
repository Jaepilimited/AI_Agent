#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Playwright E2E QA — 100 diverse questions via actual browser UI.

Logs into the AI Agent, types questions in the chat, waits for streaming
response to complete, and collects question/answer/SQL/time.

Usage:
  python -X utf8 scripts/playwright_qa_100.py
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# ── Config ──
BASE_URL = "http://127.0.0.1:3000"
LOGIN_DEPT = "Craver_Accounts > Users > Brand > DB > 데이터분석"
LOGIN_NAME = "임재필"
LOGIN_PW = "1234"
RESULTS_FILE = Path(__file__).parent / "playwright_qa100_results.json"
TIMEOUT_MS = 180_000  # 3 minutes per question
MAX_WAIT_STREAMING = 180  # seconds

# ── 100 diverse questions across all DB categories ──
QUESTIONS = [
    # === 통합매출 (sales_all) — 15개 ===
    "어제 전사 매출 실적 알려줘",
    "2026년 3월 누적 전사 실적",
    "사업부별 매출액과 주문수량 보여줘",
    "B2B 사업부 이번 달 매출 얼마야",
    "GM 사업부 국가별 매출 비교해줘",
    "2025년 연간 매출 TOP 5 팀",
    "전월 대비 이번 달 매출 성장률",
    "쇼피 인도네시아 2026년 1분기 매출",
    "미국 아마존 월별 매출 추이 차트로 보여줘",
    "B2C 채널 국가별 매출 순위",
    "2025년 하반기 대비 2026년 상반기 매출 비교",
    "라인별 매출 비중 파이차트로",
    "센텔라 라인 월별 매출 추이",
    "크레이버코퍼레이션 2025년 연간 매출",
    "DD 사업부 팀별 실적 알려줘",

    # === 제품 (product) — 10개 ===
    "센텔라 앰플 100ml 2025년 판매 수량",
    "카테고리별 판매 수량 TOP 3",
    "LIN 라인 SKU별 누적 판매량",
    "2026년 1분기 신규 SKU 판매 현황",
    "국가별 가장 많이 팔린 제품",
    "히알루시카 라인 vs 센텔라 라인 수량 비교",
    "일본 시장 제품별 판매 수량 순위",
    "2025년 대비 2026년 제품 판매 증감",
    "선크림 카테고리 월별 판매 추이",
    "Brand SK 제품 중 판매 수량 하위 5개",

    # === 광고비 (advertising) — 10개 ===
    "이번 달 총 광고비 얼마야",
    "플랫폼별 ROAS 비교",
    "구글 광고 캠페인별 CTR 순위",
    "국가별 광고 전환율 비교",
    "전월 대비 광고비 증감률",
    "CPC가 가장 낮은 캠페인 TOP 3",
    "2025년 분기별 광고 성과 추이",
    "광고비 대비 매출 효율이 가장 높은 국가",
    "인도네시아 광고 월별 노출수 추이",
    "전환수가 가장 높은 광고 그룹",

    # === 인플루언서 (influencer) — 10개 ===
    "이번 달 인플루언서 마케팅 총 비용",
    "티어별 평균 조회수 비교",
    "인스타그램 vs 틱톡 인플루언서 성과 비교",
    "CPV가 가장 낮은 인플루언서 TOP 5",
    "국가별 인플루언서 캠페인 수",
    "Macro 티어 인플루언서 평균 참여율",
    "2025년 인플루언서 비용 월별 추이",
    "팔로워 100만 이상 인플루언서 리스트",
    "인플루언서 플랫폼별 총 좋아요 수",
    "인플루언서 마케팅 ROI 가장 높은 국가",

    # === 마케팅비용 (marketing_cost) — 8개 ===
    "이번 달 총 마케팅 비용 얼마야",
    "광고비 vs 인플루언서 비용 비율",
    "플랫폼별 마케팅 비용 순위",
    "월별 마케팅 ROAS 추이 차트",
    "국가별 마케팅 비용 대비 매출",
    "2025년 연간 마케팅비 합계",
    "마케팅 비용 유형별 비중",
    "전분기 대비 마케팅비 증감",

    # === 메타 광고 (meta_ads) — 10개 ===
    "국가별 메타 광고 수",
    "활성 광고 vs 비활성 광고 비율",
    "SKIN1004 한국 메타 광고 보여줘",
    "페이지별 광고 수 순위",
    "최근 7일 신규 메타 광고",
    "페이스북 플랫폼 광고 수",
    "메타 광고 전체 현황 요약",
    "비디오 유형 광고 몇 개야",
    "공식 vs 파트너십 광고 비율",
    "브랜드별 활성 광고 건수",

    # === 아마존 검색 (amazon_search) — 8개 ===
    "아마존 검색 CTR 가장 높은 ASIN",
    "국가별 아마존 검색 노출수",
    "장바구니 추가율 TOP 5 제품",
    "월별 아마존 구매 전환율 추이",
    "미국 아마존 검색 쿼리 TOP 10",
    "아마존 검색에서 클릭수 대비 구매 비율",
    "ASIN별 평균 CTR 순위",
    "최근 한 달 아마존 검색 성과 요약",

    # === 플랫폼 (platform) — 8개 ===
    "쇼피 인도네시아 제품 순위",
    "아마존 US 제품 가격 비교",
    "플랫폼별 평균 평점 비교",
    "할인율이 가장 높은 제품 TOP 5",
    "라자다 말레이시아 제품 리뷰 수",
    "틱톡샵 필리핀 판매량 순위",
    "채널별 평균 가격 비교",
    "2025년 플랫폼별 랭킹 변동 추이",

    # === Shopify — 6개 ===
    "쇼피파이 이번 달 순매출",
    "쇼피파이 제품별 판매 수량 TOP 10",
    "쇼피파이 국가별 주문 건수",
    "쇼피파이 월별 환불 금액 추이",
    "쇼피파이 할인 적용 비율",
    "쇼피파이 SKU별 평균 단가",

    # === CS / Notion / Direct — 10개 ===
    "센텔라 앰플 사용법 알려줘",
    "틱톡샵 접속 방법",
    "우리 회사 뭐하는 회사야",
    "센텔라 vs 히알루시카 차이점",
    "반품 절차 알려줘",
    "SKIN1004 주요 성분 뭐야",
    "인도네시아 쇼피 운영 가이드",
    "오늘 날씨 어때",
    "대한민국 대통령 누구야",
    "환율 정보 알려줘",

    # === 복합 (multi) — 5개 ===
    "인도네시아 시장 매출 + 경쟁사 분석",
    "미국 아마존 매출과 광고 성과 종합 분석",
    "2025년 연간 실적 리포트 만들어줘",
    "센텔라 앰플 매출 데이터와 고객 리뷰 종합",
    "글로벌 시장별 매출 현황과 마케팅 전략 제안",
]


def extract_sql(answer: str) -> str:
    """Extract SQL from answer."""
    m = re.search(r'<details>.*?```sql\s*\n(.*?)```', answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r'```sql\s*\n(.*?)```', answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def detect_source(answer: str) -> str:
    """Detect source from badge or content."""
    if "BQ 매출" in answer or "SALES_ALL" in answer:
        return "bigquery"
    if "Notion" in answer:
        return "notion"
    if "CS" in answer or "🧴" in answer:
        return "cs"
    if "Google" in answer and "검색" in answer:
        return "multi"
    return "direct"


def run_test():
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.set_default_timeout(60000)
        page = context.new_page()

        # ── Login via API cookie (bypass UI) ──
        print("  Logging in via API...")
        import requests as req_lib
        login_resp = req_lib.post(f"{BASE_URL}/api/auth/signin", json={
            "department": LOGIN_DEPT,
            "name": LOGIN_NAME,
            "password": LOGIN_PW,
        })
        if login_resp.status_code != 200:
            print(f"  Login failed: {login_resp.text}")
            return

        token = login_resp.cookies.get("token")
        page.goto(BASE_URL)
        context.add_cookies([{
            "name": "token",
            "value": token,
            "domain": "127.0.0.1",
            "path": "/",
        }])
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        print("  Login successful!\n")

        # ── Run questions ──
        total = len(QUESTIONS)
        for idx, question in enumerate(QUESTIONS):
            qnum = idx + 1
            print(f"  [{qnum:3d}/{total}] {question[:50]}...", end="", flush=True)

            # New conversation every 5 questions to avoid context buildup
            if idx > 0 and idx % 5 == 0:
                new_btn = page.locator("button#btn-new-chat").first
                if new_btn.is_visible():
                    new_btn.click()
                    page.wait_for_timeout(1000)

            # Count existing assistant messages BEFORE sending
            pre_count = len(page.locator(".message.message-assistant").all())

            start_time = time.time()

            # Type and send via Enter key
            chat_input = page.locator("textarea#chat-input").first
            chat_input.click()
            chat_input.fill(question)
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")

            # Wait for a NEW assistant message (count > pre_count) then stabilize
            page.wait_for_timeout(3000)
            prev_text = ""
            stable_count = 0
            for _ in range(MAX_WAIT_STREAMING):
                try:
                    msgs = page.locator(".message.message-assistant").all()
                    if len(msgs) <= pre_count:
                        page.wait_for_timeout(1000)
                        continue
                    cur_text = msgs[-1].inner_text() if msgs else ""
                    if cur_text and "\n" in cur_text:
                        cur_text = cur_text.split("\n", 1)[1].strip()
                except Exception:
                    cur_text = ""
                if cur_text and len(cur_text) > 10 and cur_text == prev_text:
                    stable_count += 1
                    if stable_count >= 3:
                        break
                else:
                    stable_count = 0
                prev_text = cur_text
                page.wait_for_timeout(1000)

            elapsed = time.time() - start_time

            # Extract answer
            answer = prev_text or ""

            used_sql = extract_sql(answer)
            source = detect_source(answer)
            answer_len = len(answer)

            # Determine status
            if elapsed >= 90:
                status = "FAIL"
            elif answer_len < 20:
                status = "EMPTY"
            elif elapsed >= 60:
                status = "WARN"
            else:
                status = "OK"

            icon = {"OK": "+", "WARN": "!", "FAIL": "X", "EMPTY": "0"}.get(status, "?")
            print(f" [{icon}] {elapsed:.1f}s len={answer_len:4d} [{source}]")

            results.append({
                "id": f"PW-{qnum:03d}",
                "query": question,
                "answer_preview": answer[:500].replace("\n", " "),
                "used_sql": used_sql,
                "source": source,
                "status": status,
                "time": round(elapsed, 1),
                "answer_len": answer_len,
            })

            # Save after each question (resume support)
            RESULTS_FILE.write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            page.wait_for_timeout(500)

        browser.close()

    # ── Summary ──
    print("\n" + "=" * 70)
    print(f"  PLAYWRIGHT QA 100 — SUMMARY")
    print("=" * 70)

    ok = sum(1 for r in results if r["status"] == "OK")
    warn = sum(1 for r in results if r["status"] == "WARN")
    fail = sum(1 for r in results if r["status"] in ("FAIL", "EMPTY"))
    avg_t = sum(r["time"] for r in results) / len(results) if results else 0

    print(f"  OK={ok} WARN={warn} FAIL={fail}")
    print(f"  PASS: {(ok + warn) / len(results) * 100:.1f}%")
    print(f"  avg={avg_t:.1f}s")

    # Source distribution
    from collections import Counter
    src_counts = Counter(r["source"] for r in results)
    print(f"\n  Sources: {dict(src_counts)}")
    print(f"\n  Results saved to: {RESULTS_FILE}")
    print("=" * 70)

    return results


if __name__ == "__main__":
    print("=" * 70)
    print(f"  PLAYWRIGHT E2E QA — {len(QUESTIONS)} questions via browser")
    print(f"  Target: {BASE_URL}")
    print("=" * 70)
    run_test()
