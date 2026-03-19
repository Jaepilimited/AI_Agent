#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""QA 100 per table — 9 tables (no review, no notion) = 900 tests.

Phase 1: Generate 100 DIVERSE questions per table via LLM (no category overlap)
Phase 2: Run all tests
Phase 3: Export to Google Sheet tab '260319'

Usage:
  python -X utf8 scripts/qa_marketing/run_qa_100.py
  python -X utf8 scripts/qa_marketing/run_qa_100.py generate   # Generate only
  python -X utf8 scripts/qa_marketing/run_qa_100.py run         # Run + export only
"""

import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock, Semaphore

import requests

# ── Paths ──
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent.parent
RESULTS_DIR = BASE_DIR / "results_qa100"
QUESTIONS_DIR = BASE_DIR / "questions_qa100"

# ── API ──
API_URL = "http://localhost:3001/v1/chat/completions"
HEALTH_URL = "http://localhost:3001/health"
MODEL = "gemini"

# ── Threading ──
NUM_TABLE_THREADS = 3
MAX_CONCURRENT_API = 2
CALL_DELAY = 1.0
TIMEOUT = 180
MAX_RETRIES = 3

# ── Thresholds ──
FAIL_THRESHOLD = 90
WARN_THRESHOLD = 60
MIN_ANSWER_LEN = 20

QUESTIONS_PER_TABLE = 100

# ── Tables config ──
TABLE_SCHEMAS = {
    "sales_all": {
        "prefix": "SA",
        "label": "통합매출",
        "schema": (
            "테이블: SALES_ALL_Backup\n"
            "컬럼: Date(DATETIME), Brand(SK/CL/CBT/DD/UM/ETC), Country, Mall_Classification, "
            "Team_NEW, Sales_Type(B2B/B2C), Line(Centella/Hyalucica/LabinNature/...), "
            "Category(Ampoule/Cream/Toner/Sun/...), SET(제품명), SKU, "
            "Sales1_R(매출액), Total_Qty(수량), Unit_Price\n"
            "특징: 글로벌 전 플랫폼 매출 통합 데이터"
        ),
    },
    "advertising": {
        "prefix": "AD",
        "label": "광고비",
        "schema": (
            "테이블: integrated_advertising_data\n"
            "컬럼: Date, Platform, Country, Campaign, Ad_Group, "
            "Cost(광고비), Impressions(노출), Clicks(클릭), Conversions(전환), "
            "ROAS, CTR, CPC, CVR, Revenue\n"
            "특징: 통합 광고 성과 데이터"
        ),
    },
    "amazon_search": {
        "prefix": "AZ",
        "label": "아마존검색",
        "schema": (
            "테이블: amazon_search_analytics\n"
            "컬럼: Date, Country, ASIN, Search_Query, "
            "Impressions, Clicks, CTR, Cart_Adds, Purchases, "
            "Purchases_Conversion_Rate\n"
            "특징: 아마존 검색 분석 데이터"
        ),
    },
    "influencer": {
        "prefix": "IF",
        "label": "인플루언서",
        "schema": (
            "테이블: influencer_input_ALL_TEAMS\n"
            "컬럼: Date, Team, Country, Platform(Instagram/YouTube/TikTok), "
            "Tier(Mega/Macro/Micro/Nano), Influencer_Name, "
            "Followers, Views, Likes, Comments, Cost, "
            "Engagement_Rate, CPV, CPE\n"
            "특징: 인플루언서 마케팅 성과"
        ),
    },
    "marketing_cost": {
        "prefix": "MC",
        "label": "마케팅비용",
        "schema": (
            "테이블: Integrated_marketing_cost\n"
            "컬럼: Month, Platform, Country, Campaign, "
            "Cost, Cost_Type(광고/인플루언서), Revenue, ROAS\n"
            "특징: 광고+인플루언서 비용 통합"
        ),
    },
    "meta_ads": {
        "prefix": "MT",
        "label": "메타광고",
        "schema": (
            "테이블: meta data_test\n"
            "컬럼: collect_date, country_name, country_code, brand(소문자!), "
            "page_name, ad_type(official/partnership), ad_archive_id, "
            "snapshot(JSON-display_format), is_active, publisher_platform(리스트), "
            "start_date_formatted(TIMESTAMP), end_date_formatted, url\n"
            "특징: 메타 광고 라이브러리 크롤링 데이터"
        ),
    },
    "platform": {
        "prefix": "PL",
        "label": "플랫폼매출",
        "schema": (
            "테이블: Platform_Data.raw_data\n"
            "컬럼: Date, Channel(Shopee/Lazada/Amazon/TikTok/...), Country, "
            "Product_Name, Rank, Price, Discount_Price, Rating, Review_Count, "
            "Sales_Volume\n"
            "특징: 경쟁사 포함 플랫폼별 제품 순위/가격"
        ),
    },
    "product": {
        "prefix": "PR",
        "label": "제품",
        "schema": (
            "테이블: Product\n"
            "컬럼: Date, Brand(SK/CL), Country, SKU, SET(제품명), "
            "Line(Centella/LabinNature/Hyalucica/...), "
            "Category(Ampoule/Cream/Toner/Sun/SET/Pack/Cleanser/Oil/Others), "
            "Total_Qty(수량)\n"
            "특징: 제품별 판매 수량 집계"
        ),
    },
    "shopify": {
        "prefix": "SH",
        "label": "쇼피파이",
        "schema": (
            "테이블: shopify_analysis_sales\n"
            "컬럼: Date, Product_Title, SKU, Variant_Title, "
            "Net_Sales, Quantity, Order_Count, Country, "
            "Discount_Amount, Refund_Amount\n"
            "특징: Shopify 자사몰 판매 데이터"
        ),
    },
}

# ── Thread-safe ──
print_lock = Lock()
results_lock = Lock()
api_semaphore = Semaphore(MAX_CONCURRENT_API)
all_results: dict[str, list] = {}
completed_ids: set[str] = set()


# ═══════════════════════════════════════════════════════════
#  Phase 1: GENERATE diverse questions via LLM
# ═══════════════════════════════════════════════════════════

def generate_diverse_questions(table_name: str, cfg: dict) -> list[dict]:
    """Generate 100 diverse questions — each must cover a UNIQUE analytical pattern."""
    sys.path.insert(0, str(PROJECT_DIR))
    from app.core.llm import get_flash_client

    prompt = f"""당신은 SKIN1004 AI 시스템의 QA 테스터입니다.
아래 테이블에 대해 100개의 질문을 생성하세요.

## 핵심 규칙: 모든 질문이 서로 다른 분석 카테고리여야 합니다!
- ❌ "1월 매출", "2월 매출", "3월 매출" — 이건 같은 카테고리(월별 매출)
- ✅ "1월 매출", "국가별 수량 비교", "TOP 5 제품 매출", "전월 대비 성장률" — 전부 다른 카테고리

## 테이블 정보
{cfg['schema']}

## 100개 질문 카테고리 가이드 (각각 1개씩, 겹치지 않게!):
1-5: 단일 집계 (합계, 평균, 최대, 최소, 건수)
6-10: 기간별 추이 (월별, 분기별, 연도별, 주별, 일별)
11-15: 순위/TOP N (TOP 3, TOP 5, TOP 10, 최하위, 중간값)
16-20: 비교 분석 (전월 대비, 전년 대비, 국가간, 채널간, 브랜드간)
21-25: 비율/점유율 (비중, 비율, 점유율, 구성비, 기여도)
26-30: 필터 조합 (국가+기간, 브랜드+채널, 제품+기간, 복합 조건)
31-35: 차트/시각화 요청 ("그래프로", "차트로", "추이 보여줘", "파이차트", "바차트")
36-40: 부정/제외 조건 ("~제외", "~아닌", "~외의", "~빼고")
41-45: 존재 확인 ("~있어?", "데이터 있나", "조회 가능?")
46-50: 경계값/특수 (0인 데이터, 최근 1건, 가장 오래된, NULL)
51-55: 그룹별 집계 (팀별, 카테고리별, 라인별, 채널별, 유형별)
56-60: 기간 범위 (최근 3개월, 하반기, Q1~Q3, 특정 주)
61-65: 성장/감소 (성장률, 감소율, 증감, 변동폭, 변화)
66-70: 평균 관련 (평균 단가, 평균 수량, 이동평균, 가중평균)
71-75: 누적/합산 (누적 매출, YTD, MTD, 연간 합산)
76-80: 조건부 집계 (특정 조건 만족하는 것만, ~이상, ~이하)
81-85: 상세 조회 (특정 SKU, 특정 제품, 특정 캠페인 상세)
86-90: 교차 분석 (국가x카테고리, 채널x기간, 브랜드x라인)
91-95: 자연어/구어체 ("요즘 어때?", "잘 팔리는 거 뭐야?", "좀 알려줘")
96-100: 복합/고급 (서브쿼리 필요, HAVING, WINDOW 함수, PIVOT)

## 응답 형식
JSON 배열로만 응답. 각 항목은 질문 문자열.
```json
["질문1", "질문2", ...]
```
"""

    client = get_flash_client()

    for attempt in range(3):
        try:
            # Use generate() with high max_output_tokens (not generate_json which caps at 1024)
            text = client.generate(prompt, max_output_tokens=16384, temperature=0.7)
            text = text.strip()

            # Strip markdown fences
            if text.startswith("```"):
                text = re.sub(r'^```\w*\n?', '', text)
                text = re.sub(r'\n?```$', '', text)

            # Try JSON parse
            try:
                response = json.loads(text)
            except json.JSONDecodeError:
                # Salvage: extract all quoted strings between [ ]
                m = re.search(r'\[.*', text, re.DOTALL)
                if m:
                    items = re.findall(r'"([^"]{5,})"', m.group(0))
                    response = items
                else:
                    response = []

            if isinstance(response, list) and len(response) >= 50:
                questions = []
                for i, q in enumerate(response[:QUESTIONS_PER_TABLE]):
                    questions.append({
                        "id": f"{cfg['prefix']}-{i + 1:03d}",
                        "query": str(q),
                        "table": table_name,
                    })
                return questions

            print(f"    Attempt {attempt + 1}: got {len(response) if isinstance(response, list) else 0} questions, retrying...")
        except Exception as e:
            print(f"    Attempt {attempt + 1} error: {e}")
        time.sleep(3)

    return []


def phase_generate():
    """Generate diverse questions for all tables."""
    print("=" * 70)
    print("  PHASE 1: GENERATE — 100 diverse questions per table")
    print("=" * 70)

    QUESTIONS_DIR.mkdir(exist_ok=True)
    total = 0

    for table_name, cfg in sorted(TABLE_SCHEMAS.items()):
        out_file = QUESTIONS_DIR / f"questions_{table_name}.json"
        if out_file.exists():
            existing = json.loads(out_file.read_text(encoding="utf-8"))
            print(f"  [{table_name}] Already exists: {len(existing)} questions (skip)")
            total += len(existing)
            continue

        print(f"  [{table_name}] Generating 100 diverse questions via LLM...")
        questions = generate_diverse_questions(table_name, cfg)
        if questions:
            out_file.write_text(
                json.dumps(questions, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  [{table_name}] Generated {len(questions)} questions")
            total += len(questions)
        else:
            print(f"  [{table_name}] ERROR: No questions generated!")

    print(f"\n  GENERATE COMPLETE: {total} questions across {len(TABLE_SCHEMAS)} tables\n")


# ═══════════════════════════════════════════════════════════
#  Phase 2: RUN tests
# ═══════════════════════════════════════════════════════════

def _extract_sql(answer: str) -> str:
    """Extract SQL query from answer."""
    m = re.search(r'<details>.*?```sql\s*\n(.*?)```', answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r'```sql\s*\n(.*?)```', answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def _test_single(question: dict, table_name: str) -> dict:
    """Test a single question."""
    q = question["query"]
    qid = question["id"]

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": q}],
        "stream": False,
    }

    api_semaphore.acquire()
    start = time.time()
    try:
        resp = requests.post(API_URL, json=payload, timeout=TIMEOUT)
        elapsed = time.time() - start
        data = resp.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        alen = len(answer)
        used_sql = _extract_sql(answer)

        if elapsed >= FAIL_THRESHOLD:
            status = "FAIL"
        elif alen < MIN_ANSWER_LEN:
            status = "EMPTY"
        elif elapsed >= WARN_THRESHOLD:
            status = "WARN"
        else:
            status = "OK"

        return {
            "id": qid,
            "query": q,
            "table": table_name,
            "status": status,
            "time": round(elapsed, 1),
            "answer_len": alen,
            "answer_preview": answer[:500].replace("\n", " "),
            "used_sql": used_sql,
        }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "id": qid, "query": q, "table": table_name,
            "status": "ERROR", "time": round(elapsed, 1),
            "answer_len": 0, "answer_preview": str(e)[:200], "used_sql": "",
        }
    finally:
        api_semaphore.release()


def _run_table(table_name: str, questions: list):
    """Run questions for a single table."""
    remaining = [q for q in questions if q["id"] not in completed_ids]
    total = len(questions)

    with print_lock:
        print(f"\n  [{table_name}] {len(remaining)} remaining / {total} total", flush=True)

    if not remaining:
        return

    for i, q in enumerate(remaining):
        r = None
        for attempt in range(MAX_RETRIES):
            r = _test_single(q, table_name)
            if r and r["status"] != "ERROR":
                break
            wait = [5, 15, 30][min(attempt, 2)]
            time.sleep(wait)

        if r is None:
            continue

        with results_lock:
            if table_name not in all_results:
                all_results[table_name] = []
            all_results[table_name].append(r)
            completed_ids.add(r["id"])
            done = len(all_results[table_name])

        icon = {"OK": "+", "WARN": "!", "FAIL": "X", "ERROR": "E", "EMPTY": "0"}.get(r["status"], "?")
        with print_lock:
            print(
                f"  [{icon}] {r['id']:8s} {r['time']:5.1f}s len={r['answer_len']:4d} "
                f"({done}/{total}) [{table_name}] {q['query'][:40]}",
                flush=True,
            )

        # Save after each question (resume support)
        _save_table_result(table_name)
        time.sleep(CALL_DELAY)


def _save_table_result(table_name: str):
    RESULTS_DIR.mkdir(exist_ok=True)
    results = all_results.get(table_name, [])
    out = RESULTS_DIR / f"results_{table_name}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_existing():
    """Load existing results for resume."""
    global all_results, completed_ids
    if not RESULTS_DIR.exists():
        return
    for f in RESULTS_DIR.glob("results_*.json"):
        table_name = f.stem.replace("results_", "")
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            all_results[table_name] = data
            for r in data:
                completed_ids.add(r["id"])
        except Exception:
            pass


def phase_run():
    """Run all tests."""
    print("=" * 70)
    print("  PHASE 2: RUN — Execute tests")
    print("=" * 70)

    # Health check
    try:
        r = requests.get(HEALTH_URL, timeout=5)
        if r.status_code != 200:
            raise Exception("not healthy")
    except Exception:
        print("  ERROR: Server not responding on port 3001!")
        return False
    print("  Server healthy.")

    # Load questions
    QUESTIONS_DIR.mkdir(exist_ok=True)
    table_questions = {}
    for table_name in TABLE_SCHEMAS:
        f = QUESTIONS_DIR / f"questions_{table_name}.json"
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            table_questions[table_name] = data

    if not table_questions:
        print("  No questions found! Run 'generate' first.")
        return False

    total = sum(len(qs) for qs in table_questions.values())
    print(f"  {len(table_questions)} tables, {total} questions")

    # Resume
    _load_existing()
    remaining = total - len(completed_ids)
    print(f"  Completed: {len(completed_ids)}, Remaining: {remaining}")

    if remaining == 0:
        print("  All done!")
        return True

    wall_start = time.time()

    with ThreadPoolExecutor(max_workers=NUM_TABLE_THREADS) as executor:
        futures = {}
        for table_name, qs in sorted(table_questions.items()):
            future = executor.submit(_run_table, table_name, qs)
            futures[future] = table_name

        for future in as_completed(futures):
            table_name = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"  [ERROR] {table_name}: {e}")

    wall_time = time.time() - wall_start
    print(f"\n  Wall time: {wall_time:.0f}s ({wall_time / 60:.1f}min)")
    return True


def print_summary():
    """Print summary."""
    W = 80
    print("\n" + "=" * W)
    print(f"{'QA 100 — SUMMARY':^{W}}")
    print("=" * W)

    grand_total = grand_ok = grand_warn = grand_fail = 0
    grand_times = []

    for table_name in sorted(all_results.keys()):
        results = all_results[table_name]
        if not results:
            continue
        ok = sum(1 for r in results if r["status"] == "OK")
        warn = sum(1 for r in results if r["status"] == "WARN")
        fail = sum(1 for r in results if r["status"] in ("FAIL", "ERROR", "EMPTY"))
        total = len(results)
        avg_t = sum(r["time"] for r in results) / total

        grand_total += total
        grand_ok += ok
        grand_warn += warn
        grand_fail += fail
        grand_times.extend(r["time"] for r in results)

        bar_w = 20
        ok_bar = round(ok / total * bar_w)
        warn_bar = round(warn / total * bar_w)
        bar = "#" * ok_bar + "!" * warn_bar + "x" * (bar_w - ok_bar - warn_bar)

        print(f"  {table_name:20s} [{bar:20s}] OK={ok:3d} W={warn:3d} F={fail:3d} avg={avg_t:5.1f}s")

    if grand_total > 0:
        rate = (grand_ok + grand_warn) / grand_total * 100
        avg = sum(grand_times) / grand_total
        print(f"\n  OVERALL: {rate:.1f}% PASS ({grand_ok + grand_warn}/{grand_total})")
        print(f"  OK={grand_ok} WARN={grand_warn} FAIL={grand_fail} avg={avg:.1f}s")
    print("=" * W)


# ═══════════════════════════════════════════════════════════
#  Phase 3: EXPORT to Google Sheet
# ═══════════════════════════════════════════════════════════

def phase_export():
    """Export results to Google Sheet tab '260319'."""
    print("\n  PHASE 3: Export to Google Sheet '260319'...")

    sys.path.insert(0, str(PROJECT_DIR))
    from app.config import get_settings
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    settings = get_settings()
    creds = Credentials.from_service_account_file(
        settings.google_application_credentials,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    service = build("sheets", "v4", credentials=creds)

    SPREADSHEET_ID = "14alsi_x_P7psBNjm81EMQaoPxbTi-yCoII9RYKjv3WA"
    TAB_NAME = "260319"

    # Ensure tab — get actual sheetId
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheet_map = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
    if TAB_NAME not in sheet_map:
        resp = service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": TAB_NAME}}}]},
        ).execute()
        SHEET_ID = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        print(f"  Created tab: {TAB_NAME} (sheetId={SHEET_ID})")
    else:
        SHEET_ID = sheet_map[TAB_NAME]
        print(f"  Using existing tab: {TAB_NAME} (sheetId={SHEET_ID})")

    # Build rows
    header = ["#", "ID", "테이블", "질문", "답변", "사용 쿼리", "상태", "응답시간(s)"]
    rows = []
    idx = 0
    for table_name in sorted(all_results.keys()):
        label = TABLE_SCHEMAS.get(table_name, {}).get("label", table_name)
        for r in all_results[table_name]:
            idx += 1
            rows.append([
                str(idx),
                r.get("id", ""),
                label,
                r.get("query", ""),
                r.get("answer_preview", ""),
                r.get("used_sql", ""),
                r.get("status", ""),
                str(r.get("time", "")),
            ])

    all_rows = [header] + rows
    needed = len(all_rows) + 10

    # Expand grid
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": SHEET_ID, "gridProperties": {"rowCount": needed, "columnCount": 10}},
                "fields": "gridProperties(rowCount,columnCount)",
            }
        }]},
    ).execute()

    # Clear & write
    service.spreadsheets().values().clear(spreadsheetId=SPREADSHEET_ID, range=TAB_NAME).execute()

    BATCH = 500
    for start in range(0, len(all_rows), BATCH):
        batch = all_rows[start:start + BATCH]
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{TAB_NAME}'!A{start + 1}",
            valueInputOption="RAW",
            body={"values": batch},
        ).execute()

    # Bold header
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": [{
            "repeatCell": {
                "range": {"sheetId": SHEET_ID, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                }},
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        }]},
    ).execute()

    print(f"  Exported {len(rows)} rows to '{TAB_NAME}' tab")


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"

    if stage in ("generate", "all"):
        phase_generate()

    if stage in ("run", "all"):
        if not phase_run():
            return
        print_summary()

    if stage in ("export", "run", "all"):
        # Load results if not in memory
        if not all_results:
            _load_existing()
        phase_export()

    print("\n  ALL DONE!")


if __name__ == "__main__":
    main()
