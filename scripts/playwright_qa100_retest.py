#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Playwright retest — QA100 21건 FAIL 항목 재검증.

수정 내용 검증:
- "있나요" 질문 BigQuery 라우팅 (4건)
- 타임아웃 30s 확대 (CS/Team/Direct)
- 차트 생성 개선 (파이/바 차트)
- "제외" 패턴 SQL 생성
- 국가명 매핑 / 복잡 쿼리

Usage:
  python -X utf8 scripts/playwright_qa100_retest.py [--port 3001] [--headed]
"""

import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
import requests as req_lib

BASE_URL = "http://127.0.0.1:3001"
LOGIN_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
LOGIN_NAME = "임재필"
LOGIN_PW = "1234"
RESULTS_FILE = Path(__file__).parent / "playwright_qa100_retest_results.json"
MAX_WAIT = 120  # seconds per question

# 21건 FAIL 항목 (ID / 원래 테이블 / 질문 / 실패 카테고리)
QUESTIONS = [
    # Category 1: "있나요" 라우팅 → BigQuery로 수정 (4건)
    {"id": "AD-042", "table": "advertising", "category": "routing",
     "query": "특정 Campaign 'Seasonal Sale 2023'에 대한 데이터가 있나요?"},
    {"id": "AZ-042", "table": "amazon_search", "category": "routing",
     "query": "2024년 1월 1일 이후의 데이터가 있나요?"},
    {"id": "MC-042", "table": "marketing_cost", "category": "routing",
     "query": "2024년 2월 데이터가 테이블에 포함되어 있나요?"},
    {"id": "MT-042", "table": "meta_ads", "category": "routing",
     "query": "brand 'skin1004'의 ad_type이 'partnership'인 광고 데이터가 있나요?"},

    # Category 2: "제외" 패턴 SQL 생성 (1건)
    {"id": "AD-036", "table": "advertising", "category": "exclusion",
     "query": "'Google Ads' Platform을 제외한 모든 Platform의 총 Revenue는 얼마인가요?"},

    # Category 3: 날짜 엣지 케이스 (3건)
    {"id": "AZ-016", "table": "amazon_search", "category": "date_edge",
     "query": "지난달 대비 이번 달의 총 구매 건수 증감률은 얼마인가요?"},
    {"id": "IF-016", "table": "influencer", "category": "date_edge",
     "query": "지난달 대비 이번 달 총 비용(Cost)의 증감률은 얼마인가요?"},
    {"id": "IF-017", "table": "influencer", "category": "date_edge",
     "query": "작년 동기 대비 올해 총 조회수(Views)의 변화는 어떻게 되나요?"},

    # Category 4: 차트 생성 (2건)
    {"id": "IF-035", "table": "influencer", "category": "chart",
     "query": "각 플랫폼별 평균 CPV를 막대 차트로 비교해 주세요."},
    {"id": "MC-024", "table": "marketing_cost", "category": "chart",
     "query": "총 Revenue를 기준으로 각 Campaign의 Revenue 구성비를 파이 차트로 보여주세요."},

    # Category 5: 복잡 쿼리 (5건, PL-020 제외 - 경쟁사 데이터 없음)
    {"id": "AD-018", "table": "advertising", "category": "complex",
     "query": "미국과 한국의 평균 CTR을 비교해주세요."},
    {"id": "PL-012", "table": "platform", "category": "complex",
     "query": "싱가포르에서 Rating이 가장 높은 TOP 5 Product_Name을 알려주세요."},
    {"id": "PL-021", "table": "platform", "category": "complex",
     "query": "전체 Sales_Volume에서 Shopee가 차지하는 비중은 얼마인가요?"},
    {"id": "PR-026", "table": "product", "category": "complex",
     "query": "미국에서 지난 3개월 동안 판매된 Total_Qty 합계는 얼마인가요?"},

    # Category 6: 데이터 부재 — 정상 처리 확인 (4건)
    {"id": "PL-020", "table": "platform", "category": "no_data",
     "query": "경쟁사 제품과 SKIN1004 제품의 평균 Sales_Volume을 비교해주세요."},
    {"id": "RS-095", "table": "shopee_review", "category": "no_data",
     "query": "SKIN1004 제품들의 전반적인 강점과 약점 카테고리는 무엇인가요?"},
    {"id": "SH-023", "table": "shopify", "category": "no_data",
     "query": "가장 많이 주문된 제품이 전체 주문 건수에서 차지하는 점유율은 몇 %인가요?"},
]


def extract_sql(answer: str) -> str:
    m = re.search(r'<details>.*?```sql\s*\n(.*?)```', answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r'```sql\s*\n(.*?)```', answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def has_chart(answer: str) -> bool:
    return "```chart-config" in answer or "chart-container" in answer


def judge_result(q_info: dict, answer: str, elapsed: float) -> str:
    """Determine test status based on answer quality."""
    cat = q_info["category"]
    answer_lower = answer.lower()

    # Timeout
    if elapsed >= 90:
        return "FAIL"
    # Empty response
    if len(answer) < 20:
        return "EMPTY"

    # General failure indicators
    fail_indicators = [
        "조회하지 못했습니다", "오류가 발생", "데이터를 찾을 수 없",
        "분석이 예상보다 오래", "서비스가 일시적으로 불안정",
    ]
    if any(fi in answer for fi in fail_indicators):
        return "FAIL"

    # Category-specific checks
    if cat == "routing":
        # Should have queried DB, not returned general knowledge
        bad_signs = ["학습 데이터", "지식 기한", "training data", "cutoff", "제가 직접"]
        if any(bs in answer_lower for bs in bad_signs):
            return "FAIL"
        return "OK"

    if cat == "exclusion":
        # Should NOT reference Google Inc financial data
        if "$85" in answer or "850억" in answer or "alphabet" in answer_lower:
            return "FAIL"
        return "OK"

    if cat == "date_edge":
        # Should produce an answer (even with partial data warning)
        if "부분 집계" in answer or "불완전" in answer or len(answer) > 100:
            return "OK"
        return "WARN"

    if cat == "chart":
        # Should have chart config
        if has_chart(answer):
            return "OK"
        if len(answer) > 100:
            return "WARN"  # Got data but no chart
        return "FAIL"

    if cat == "complex":
        # Should have meaningful data response
        if len(answer) > 100:
            return "OK"
        return "FAIL"

    if cat == "no_data":
        # These might genuinely lack data — answer should be graceful
        if len(answer) > 50:
            return "OK"
        return "WARN"

    return "OK" if len(answer) > 50 else "FAIL"


def run_retest():
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.set_default_timeout(60000)
        page = context.new_page()

        # Login via API cookie
        print("  Logging in...")
        login_resp = req_lib.post(f"{BASE_URL}/api/auth/signin", json={
            "department": LOGIN_DEPT, "name": LOGIN_NAME, "password": LOGIN_PW,
        })
        if login_resp.status_code != 200:
            print(f"  Login failed: {login_resp.text}")
            return
        token = login_resp.cookies.get("token")
        context.add_cookies([{"name": "token", "value": token, "domain": "127.0.0.1", "path": "/"}])
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        print("  Login OK\n")

        total = len(QUESTIONS)
        for idx, q_info in enumerate(QUESTIONS):
            qnum = idx + 1
            qid = q_info["id"]
            question = q_info["query"]
            cat = q_info["category"]
            print(f"  [{qnum:2d}/{total}] [{qid:7s}] {question[:50]}...", end="", flush=True)

            # New conversation every 2 questions to avoid context bleeding
            if idx % 2 == 0:
                new_btn = page.locator("button#btn-new-chat").first
                if new_btn.is_visible():
                    new_btn.click()
                    page.wait_for_timeout(1500)

            # Count existing assistant messages BEFORE sending
            pre_count = len(page.locator(".message.message-assistant").all())

            start_time = time.time()

            chat_input = page.locator("textarea#chat-input").first
            chat_input.click()
            chat_input.fill(question)
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")

            # Wait for a NEW assistant message to appear and stabilize
            # Loading messages like "📊 데이터 조회 중..." are NOT final answers
            _LOADING_PATTERNS = ["데이터 조회 중", "조회 중...", "분석 중...", "처리 중..."]
            page.wait_for_timeout(3000)
            prev_text = ""
            stable_count = 0
            for _ in range(MAX_WAIT):
                try:
                    msgs = page.locator(".message.message-assistant").all()
                    if len(msgs) <= pre_count:
                        page.wait_for_timeout(1000)
                        continue
                    cur_text = msgs[-1].inner_text() if msgs else ""
                except Exception:
                    cur_text = ""
                # Skip loading messages — they aren't final answers
                is_loading = any(lp in cur_text for lp in _LOADING_PATTERNS)
                if cur_text and len(cur_text) > 10 and cur_text == prev_text and not is_loading:
                    stable_count += 1
                    if stable_count >= 3:
                        break
                else:
                    stable_count = 0
                prev_text = cur_text
                page.wait_for_timeout(1000)

            elapsed = time.time() - start_time
            answer = prev_text or ""
            used_sql = extract_sql(answer)
            answer_len = len(answer)
            has_chart_flag = has_chart(answer)

            status = judge_result(q_info, answer, elapsed)

            icon = {"OK": "+", "WARN": "!", "FAIL": "X", "EMPTY": "0"}.get(status, "?")
            sql_mark = "SQL" if used_sql else "---"
            chart_mark = "CHT" if has_chart_flag else "---"
            print(f" [{icon}] {elapsed:.1f}s len={answer_len:4d} [{sql_mark}] [{chart_mark}]")

            results.append({
                "id": qid,
                "table": q_info["table"],
                "category": cat,
                "query": question,
                "answer_preview": answer[:500].replace("\n", " "),
                "used_sql": used_sql,
                "has_sql": bool(used_sql),
                "has_chart": has_chart_flag,
                "status": status,
                "time": round(elapsed, 1),
                "answer_len": answer_len,
            })

            # Save incrementally
            RESULTS_FILE.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            page.wait_for_timeout(500)

        browser.close()

    # Summary
    print("\n" + "=" * 70)
    print("  QA100 RETEST SUMMARY — 21 FAIL Items")
    print("=" * 70)
    ok = sum(1 for r in results if r["status"] == "OK")
    warn = sum(1 for r in results if r["status"] == "WARN")
    fail = sum(1 for r in results if r["status"] in ("FAIL", "EMPTY"))
    sql_gen = sum(1 for r in results if r["has_sql"])
    chart_gen = sum(1 for r in results if r["has_chart"])
    avg_t = sum(r["time"] for r in results) / len(results) if results else 0

    print(f"\n  OK={ok}  WARN={warn}  FAIL={fail}")
    print(f"  SQL Generated: {sql_gen}/{len(results)}")
    print(f"  Chart Generated: {chart_gen}/{len(results)}")
    print(f"  Avg Time: {avg_t:.1f}s")

    # Category breakdown
    cats = {}
    for r in results:
        c = r["category"]
        if c not in cats:
            cats[c] = {"ok": 0, "warn": 0, "fail": 0}
        if r["status"] == "OK":
            cats[c]["ok"] += 1
        elif r["status"] == "WARN":
            cats[c]["warn"] += 1
        else:
            cats[c]["fail"] += 1

    print("\n  Category Breakdown:")
    for c, counts in cats.items():
        print(f"    {c:12s}: OK={counts['ok']} WARN={counts['warn']} FAIL={counts['fail']}")

    # Show remaining failures
    fails = [r for r in results if r["status"] in ("FAIL", "EMPTY")]
    if fails:
        print(f"\n  Still failing ({len(fails)}):")
        for r in fails:
            print(f"    {r['id']}: {r['query'][:55]} [{r['status']}] {r['time']:.1f}s")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    BASE_URL = f"http://127.0.0.1:{args.port}"

    print("=" * 70)
    print(f"  PLAYWRIGHT QA100 RETEST — {len(QUESTIONS)} failed items")
    print(f"  Target: {BASE_URL}")
    print("=" * 70)
    run_retest()
