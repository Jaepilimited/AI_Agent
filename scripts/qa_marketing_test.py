"""Marketing Tables QA Test — 500 questions across 11 BigQuery tables.

Tables: integrated_advertising_data, Integrated_marketing_cost, shopify_analysis_sales,
Platform_Data.raw_data, influencer_input_ALL_TEAMS, amazon_search_analytics,
Amazon_Review, Qoo10_Review, Shopee_Review, Smartstore_Review, meta data_test
"""

import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

API_URL = "http://localhost:3001/v1/chat/completions"
RESULT_FILE = Path(__file__).resolve().parent.parent / "test_results_marketing.json"
MODEL = "skin1004-Analysis"
NUM_GROUPS = 2

# ---------------------------------------------------------------------------
# ALL 500 QUESTIONS
# ---------------------------------------------------------------------------

TESTS = [
    # ===== Table 1: integrated_advertising_data (광고데이터) — AD-001 ~ AD-060 =====
    {"id": "AD-001", "q": "2024년 전체 광고비 합계 알려줘", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-002", "q": "국가별 틱톡 광고비 비교해줘", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-003", "q": "페이스북 광고 월별 클릭수 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-004", "q": "쇼피 광고 GMV 국가별 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-005", "q": "구글 광고 전환율 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-006", "q": "네이버 검색광고 비용 대비 전환금액", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-007", "q": "카카오모먼츠 광고 성과 분석", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-008", "q": "아마존 광고 ROAS 계산해줘", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-009", "q": "2024년 틱톡 vs 페이스북 광고비 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-010", "q": "2025년 1분기 전체 광고 플랫폼별 노출수", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-011", "q": "틱톡 결제전환 금액 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-012", "q": "페이스북 웹사이트 구매수 국가별 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-013", "q": "쇼피 광고 클릭수 대비 전환수 비율", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-014", "q": "구글 광고 가장 많이 쓴 국가 top5", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-015", "q": "네이버 GFA 구매전환 매출 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-016", "q": "SingleONE 광고비 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-017", "q": "카카오모먼츠 구매수 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-018", "q": "아마존 광고비 가장 많은 달", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-019", "q": "2024년 하반기 전체 광고비 플랫폼별 합계", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-020", "q": "틱톡 노출 대비 클릭률 국가별", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-021", "q": "페이스북 광고비 월별 증감", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-022", "q": "쇼피 GMV 가장 높은 국가", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-023", "q": "구글 전환값 가장 높은 달", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-024", "q": "네이버 검색광고 클릭수 월별", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-025", "q": "2025년 1월 전체 광고 노출수 합계", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-026", "q": "광고 플랫폼별 비용 효율 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-027", "q": "틱톡 광고주별 비용 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-028", "q": "페이스북 링크클릭수 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-029", "q": "쇼피 광고 노출수 국가별 합계", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-030", "q": "구글 광고 클릭수 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-031", "q": "2024년 분기별 전체 광고비", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-032", "q": "틱톡 vs 구글 광고 전환수 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-033", "q": "카카오모먼츠 vs 네이버 광고비 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-034", "q": "아마존 광고 노출수 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-035", "q": "2024년 광고비 가장 많이 쓴 플랫폼", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-036", "q": "페이스북 구매전환값 국가별", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-037", "q": "쇼피 광고비 월별 국가별", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-038", "q": "구글 광고 계정별 비용 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-039", "q": "2025년 2월 틱톡 광고 성과", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-040", "q": "전체 플랫폼 광고 클릭수 합계", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-041", "q": "네이버 GFA vs 네이버 검색광고 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-042", "q": "SingleONE 광고 전환수 월별", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-043", "q": "카카오모먼츠 노출수 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-044", "q": "2024년 광고 ROI 플랫폼별 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-045", "q": "틱톡 결제완료 이벤트 수 월별", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-046", "q": "페이스북 노출수 국가별 top5", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-047", "q": "쇼피 전환수 가장 높은 달", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-048", "q": "구글 광고비 분기별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-049", "q": "전체 광고비 연도별 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-050", "q": "2024년 하반기 틱톡 광고 성과 요약", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-051", "q": "광고 플랫폼별 노출수 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-052", "q": "페이스북 구매수 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-053", "q": "쇼피 GMV 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-054", "q": "네이버 광고 전체 비용 합계", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-055", "q": "SingleONE 매출 월별 추이", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-056", "q": "카카오모먼츠 구매금액 월별", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-057", "q": "아마존 광고 구매수 월별", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-058", "q": "2024년 광고 전환수 합계", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-059", "q": "틱톡 광고 국가별 성과 비교", "route": "bigquery", "category": "광고데이터"},
    {"id": "AD-060", "q": "전체 광고 비용 월별 추이", "route": "bigquery", "category": "광고데이터"},

    # ===== Table 2: Integrated_marketing_cost (마케팅비용) — MK-001 ~ MK-045 =====
    {"id": "MK-001", "q": "2024년 전체 마케팅 비용 합계", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-002", "q": "팀별 마케팅 비용 비교", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-003", "q": "마케팅 매체별 비용 비교", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-004", "q": "국가별 마케팅 비용 합계", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-005", "q": "마케팅 비용 대비 매출 비율", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-006", "q": "월별 마케팅 비용 추이", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-007", "q": "마케팅 분야별 비용 분석", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-008", "q": "가장 많은 마케팅비 쓴 팀", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-009", "q": "2025년 1분기 마케팅 비용", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-010", "q": "마케팅 매체별 노출수 비교", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-011", "q": "마케팅 클릭수 월별 추이", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-012", "q": "마케팅 구매수 매체별 비교", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-013", "q": "마케팅 비용 분기별 추이", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-014", "q": "팀별 매체별 마케팅 비용", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-015", "q": "국가별 매체별 마케팅 비용", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-016", "q": "마케팅 비용 연도별 비교", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-017", "q": "마케팅 매출 가장 높은 매체", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-018", "q": "마케팅 노출수 국가별 합계", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-019", "q": "2024년 하반기 마케팅 비용", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-020", "q": "마케팅 비용 TOP5 팀", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-021", "q": "마케팅 클릭수 TOP5 매체", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-022", "q": "마케팅 구매수 월별 추이", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-023", "q": "마케팅 비용 국가별 월별", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-024", "q": "2025년 마케팅 매출 합계", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-025", "q": "마케팅 비용 효율 팀별 비교", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-026", "q": "마케팅 노출수 매체별 월별", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-027", "q": "마케팅 분야별 매출 비교", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-028", "q": "2024년 분기별 팀별 마케팅비", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-029", "q": "마케팅 클릭수 국가별 비교", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-030", "q": "마케팅 구매전환 매체별 비교", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-031", "q": "월별 마케팅 매출 추이", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-032", "q": "팀별 마케팅 ROI 분석", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-033", "q": "마케팅 비용 가장 많은 국가", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-034", "q": "마케팅 노출수 가장 높은 달", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-035", "q": "2024년 전체 마케팅 매출", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-036", "q": "마케팅 매체별 구매수 합계", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-037", "q": "마케팅 비용 팀별 월별", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-038", "q": "2025년 2월 마케팅 비용", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-039", "q": "마케팅 비용 대비 클릭수 효율", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-040", "q": "국가별 마케팅 구매수 합계", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-041", "q": "마케팅 비용 전년 대비", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-042", "q": "마케팅 매체별 국가별 비용", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-043", "q": "팀별 국가별 마케팅 비용", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-044", "q": "2024년 마케팅 비용 TOP10 분야", "route": "bigquery", "category": "마케팅비용"},
    {"id": "MK-045", "q": "마케팅 전체 노출수 합계", "route": "bigquery", "category": "마케팅비용"},

    # ===== Table 3: shopify_analysis_sales (Shopify) — SF-001 ~ SF-045 =====
    {"id": "SF-001", "q": "Shopify 전체 매출 합계", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-002", "q": "Shopify 국가별 매출 비교", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-003", "q": "Shopify 제품별 매출 TOP10", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-004", "q": "Shopify 월별 매출 추이", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-005", "q": "Shopify 반품 금액 월별 추이", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-006", "q": "Shopify 주문수량 국가별", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-007", "q": "Shopify 제품 유형별 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-008", "q": "Shopify 가장 많이 팔린 제품", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-009", "q": "2024년 Shopify 분기별 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-010", "q": "Shopify 국가별 주문수량 TOP5", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-011", "q": "Shopify 반품률 국가별", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-012", "q": "Shopify 제품별 주문수량 비교", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-013", "q": "Shopify 월별 주문수량 추이", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-014", "q": "Shopify 매출 가장 높은 국가", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-015", "q": "2025년 1월 Shopify 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-016", "q": "Shopify 제품 유형별 수량", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-017", "q": "Shopify 국가별 월별 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-018", "q": "Shopify 반품 가장 많은 제품", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-019", "q": "Shopify 매출 연도별 비교", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-020", "q": "Shopify 2024년 하반기 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-021", "q": "Shopify 제품별 국가별 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-022", "q": "Shopify 주문수량 월별 국가별", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-023", "q": "Shopify 매출 분기별 추이", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-024", "q": "Shopify 반품 국가별 합계", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-025", "q": "Shopify 가장 많이 팔린 유형", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-026", "q": "Shopify 국가 코드별 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-027", "q": "Shopify 제품별 반품 비율", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-028", "q": "Shopify 월별 반품 추이", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-029", "q": "2024년 Shopify 연간 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-030", "q": "Shopify TOP5 제품 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-031", "q": "Shopify 국가별 분기별 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-032", "q": "Shopify 제품 유형별 국가별", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-033", "q": "Shopify 주문수량 분기별", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-034", "q": "Shopify 반품 분기별 추이", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-035", "q": "Shopify 매출 성장률 분석", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-036", "q": "2025년 Shopify 국가별 매출", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-037", "q": "Shopify 제품별 수량 TOP10", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-038", "q": "Shopify 매출 TOP3 국가", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-039", "q": "Shopify 제품 유형 몇개 있어", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-040", "q": "Shopify 어떤 국가에서 팔아", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-041", "q": "Shopify 제품 몇개 있어", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-042", "q": "Shopify 2024년 월별 성과", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-043", "q": "Shopify 국가별 평균 주문금액", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-044", "q": "Shopify 반품 가장 많은 국가", "route": "bigquery", "category": "Shopify"},
    {"id": "SF-045", "q": "Shopify 최근 3개월 매출", "route": "bigquery", "category": "Shopify"},

    # ===== Table 4: Platform_Data.raw_data (플랫폼) — PL-001 ~ PL-035 =====
    {"id": "PL-001", "q": "플랫폼별 제품 순위 알려줘", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-002", "q": "채널별 브랜드 순위 비교", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-003", "q": "제품 가격 채널별 비교", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-004", "q": "할인가 적용된 제품 목록", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-005", "q": "플랫폼별 SKIN1004 제품 순위", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-006", "q": "가격 가장 높은 제품 TOP10", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-007", "q": "채널별 제품 수 비교", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-008", "q": "할인율 가장 높은 제품", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-009", "q": "브랜드별 평균 가격 비교", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-010", "q": "플랫폼별 제품 순위 TOP5", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-011", "q": "통화별 평균 가격", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-012", "q": "할인가 있는 제품 비율", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-013", "q": "채널별 가격 범위", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-014", "q": "브랜드별 제품 수", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-015", "q": "가장 많은 제품 있는 채널", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-016", "q": "제품 가격 분포 분석", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-017", "q": "할인 폭 가장 큰 제품", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-018", "q": "채널별 평균 할인율", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-019", "q": "브랜드별 채널 분포", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-020", "q": "제품 순위 1위 목록", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-021", "q": "가격대별 제품 분포", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-022", "q": "채널별 최고가 제품", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-023", "q": "채널별 최저가 제품", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-024", "q": "브랜드별 평균 순위", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-025", "q": "플랫폼 메트릭스 전체 제품 수", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-026", "q": "채널별 브랜드 수", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-027", "q": "할인가 없는 제품 수", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-028", "q": "제품 가격 통화별 분포", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-029", "q": "SKIN1004 제품 가격 채널별", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-030", "q": "채널별 순위 분포", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-031", "q": "가격 대비 할인가 비율", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-032", "q": "채널별 제품 가격 평균", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-033", "q": "브랜드별 순위 분포", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-034", "q": "플랫폼별 할인 제품 비율", "route": "bigquery", "category": "플랫폼"},
    {"id": "PL-035", "q": "전체 채널 목록", "route": "bigquery", "category": "플랫폼"},

    # ===== Table 5: influencer_input_ALL_TEAMS (인플루언서) — IF-001 ~ IF-060 =====
    {"id": "IF-001", "q": "인플루언서 마케팅 전체 비용 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-002", "q": "팀별 인플루언서 비용 비교", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-003", "q": "인플루언서 캠페인별 성과", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-004", "q": "인플루언서 티어별 비용 비교", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-005", "q": "미디어 플랫폼별 인플루언서 수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-006", "q": "인플루언서 팔로워수 TOP10", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-007", "q": "인플루언서 좋아요수 TOP10", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-008", "q": "인플루언서 조회수 TOP10", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-009", "q": "인플루언서 콘텐츠 유형별 성과", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-010", "q": "인플루언서 비용 월별 추이", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-011", "q": "인플루언서 국가별 비용 비교", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-012", "q": "인플루언서 에이전시별 비용", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-013", "q": "인플루언서 브랜드별 캠페인 수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-014", "q": "인플루언서 언어별 분포", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-015", "q": "인플루언서 댓글수 TOP10", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-016", "q": "인플루언서 저장수 TOP10", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-017", "q": "인플루언서 공유수 TOP10", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-018", "q": "인플루언서 비용(원화) 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-019", "q": "인플루언서 비용(달러) 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-020", "q": "인플루언서 컨택 유형별 분포", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-021", "q": "인플루언서 캠페인 수 월별", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-022", "q": "인플루언서 팀별 캠페인 수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-023", "q": "인플루언서 미디어별 좋아요 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-024", "q": "인플루언서 미디어별 조회수 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-025", "q": "인플루언서 티어별 평균 팔로워", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-026", "q": "인플루언서 가장 비싼 캠페인", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-027", "q": "2024년 인플루언서 비용 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-028", "q": "인플루언서 지역별 분포", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-029", "q": "인플루언서 타겟국가별 비용", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-030", "q": "인플루언서 콘텐츠 업로드 월별", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-031", "q": "인플루언서 미디어별 댓글 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-032", "q": "인플루언서 팀별 좋아요 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-033", "q": "인플루언서 티어별 조회수 평균", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-034", "q": "인플루언서 브랜드별 비용", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-035", "q": "인플루언서 에이전시별 캠페인 수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-036", "q": "인플루언서 캠페인 가장 많은 팀", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-037", "q": "인플루언서 평균 팔로워수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-038", "q": "인플루언서 비용 분기별", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-039", "q": "인플루언서 국가별 캠페인 수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-040", "q": "인플루언서 미디어별 저장수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-041", "q": "인플루언서 비용 TOP5 팀", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-042", "q": "인플루언서 조회수 가장 높은 콘텐츠", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-043", "q": "인플루언서 좋아요 가장 많은 미디어", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-044", "q": "2024년 하반기 인플루언서 비용", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-045", "q": "인플루언서 팀별 국가별 비용", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-046", "q": "인플루언서 콘텐츠 유형별 조회수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-047", "q": "인플루언서 미디어별 공유수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-048", "q": "인플루언서 비용 연도별 비교", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-049", "q": "인플루언서 팔로워수 티어별 분포", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-050", "q": "인플루언서 캠페인별 좋아요 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-051", "q": "인플루언서 비용 월별 국가별", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-052", "q": "인플루언서 미디어별 비용 비교", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-053", "q": "인플루언서 팀별 콘텐츠 수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-054", "q": "인플루언서 타겟국가별 조회수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-055", "q": "인플루언서 에이전시별 성과", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-056", "q": "인플루언서 전체 콘텐츠 수", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-057", "q": "인플루언서 전체 좋아요수 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-058", "q": "인플루언서 전체 조회수 합계", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-059", "q": "인플루언서 댓글 가장 많은 콘텐츠", "route": "bigquery", "category": "인플루언서"},
    {"id": "IF-060", "q": "인플루언서 비용 가장 높은 국가", "route": "bigquery", "category": "인플루언서"},

    # ===== Table 6: amazon_search_analytics (아마존검색) — AZ-001 ~ AZ-035 =====
    {"id": "AZ-001", "q": "아마존 검색 노출수 국가별", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-002", "q": "아마존 검색 클릭수 국가별", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-003", "q": "아마존 검색 CTR 국가별", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-004", "q": "아마존 장바구니 추가수 국가별", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-005", "q": "아마존 구매수 국가별", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-006", "q": "아마존 전환율 국가별", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-007", "q": "아마존 검색 성과 전체 요약", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-008", "q": "아마존 CTR 가장 높은 국가", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-009", "q": "아마존 전환율 가장 높은 국가", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-010", "q": "아마존 노출수 가장 많은 국가", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-011", "q": "아마존 클릭수 가장 많은 국가", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-012", "q": "아마존 장바구니 추가수 TOP5", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-013", "q": "아마존 구매수 가장 많은 국가", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-014", "q": "아마존 검색 성과 국가별 비교", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-015", "q": "아마존 검색 퍼널 분석", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-016", "q": "아마존 노출 대비 클릭률", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-017", "q": "아마존 클릭 대비 구매율", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-018", "q": "아마존 장바구니 대비 구매율", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-019", "q": "아마존 전체 노출수 합계", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-020", "q": "아마존 전체 클릭수 합계", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-021", "q": "아마존 전체 구매수 합계", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-022", "q": "아마존 전체 장바구니수 합계", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-023", "q": "아마존 평균 CTR", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-024", "q": "아마존 평균 전환율", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-025", "q": "아마존 국가별 장바구니 전환율", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-026", "q": "아마존 검색 노출 TOP3 국가", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-027", "q": "아마존 구매 전환율 TOP3", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-028", "q": "아마존 CTR 가장 낮은 국가", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-029", "q": "아마존 전환율 가장 낮은 국가", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-030", "q": "아마존 노출수 국가별 합계", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-031", "q": "아마존 클릭수 국가별 비교", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-032", "q": "아마존 검색 분석 전체 데이터", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-033", "q": "아마존 장바구니율 국가별", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-034", "q": "아마존 구매수 국가별 비교", "route": "bigquery", "category": "아마존검색"},
    {"id": "AZ-035", "q": "아마존 검색 성과 요약 보고서", "route": "bigquery", "category": "아마존검색"},

    # ===== Table 7: Amazon_Review (아마존리뷰) — RV-AM-001 ~ RV-AM-045 =====
    {"id": "RV-AM-001", "q": "아마존 리뷰 전체 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-002", "q": "아마존 리뷰 제품별 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-003", "q": "아마존 리뷰 브랜드별 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-004", "q": "아마존 리뷰 채널별 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-005", "q": "아마존 리뷰 월별 추이", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-006", "q": "아마존 리뷰 가장 많은 제품", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-007", "q": "아마존 리뷰 최근 수집 날짜", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-008", "q": "아마존 리뷰 제품별 최신 리뷰", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-009", "q": "아마존 리뷰 전체 제품 목록", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-010", "q": "아마존 리뷰 브랜드별 제품 수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-011", "q": "아마존 리뷰 수집 건수 월별", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-012", "q": "아마존 리뷰 채널별 제품 수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-013", "q": "아마존 리뷰 최근 3개월 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-014", "q": "아마존 리뷰 제품별 리뷰 수 TOP10", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-015", "q": "아마존 리뷰 가장 최근 리뷰 날짜", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-016", "q": "아마존 리뷰 브랜드별 월별 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-017", "q": "아마존 리뷰 제품별 채널별 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-018", "q": "아마존 리뷰 수집날짜별 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-019", "q": "아마존 리뷰 2024년 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-020", "q": "아마존 리뷰 제품명 목록", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-021", "q": "아마존 리뷰 채널 목록", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-022", "q": "아마존 리뷰 브랜드 목록", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-023", "q": "아마존 리뷰 리뷰날짜별 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-024", "q": "아마존 리뷰 제품 몇개 있어", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-025", "q": "아마존 리뷰 총 몇건이야", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-026", "q": "아마존 리뷰 가장 오래된 리뷰", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-027", "q": "아마존 리뷰 최근 1개월 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-028", "q": "아마존 리뷰 제품별 분포", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-029", "q": "아마존 리뷰 월별 수집 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-030", "q": "아마존 리뷰 Centella 관련 리뷰", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-031", "q": "아마존 리뷰 Ampoule 제품 리뷰 수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-032", "q": "아마존 리뷰 Sun 제품 리뷰 수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-033", "q": "아마존 리뷰 Cream 제품 리뷰 수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-034", "q": "아마존 리뷰 Toner 제품 리뷰 수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-035", "q": "아마존 리뷰 전체 브랜드 수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-036", "q": "아마존 리뷰 전체 채널 수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-037", "q": "아마존 리뷰 날짜 범위", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-038", "q": "아마존 리뷰 제품 카테고리별", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-039", "q": "아마존 리뷰 수집일 기준 최근", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-040", "q": "아마존 리뷰 가장 많은 브랜드", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-041", "q": "아마존 리뷰 가장 많은 채널", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-042", "q": "아마존 리뷰 2025년 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-043", "q": "아마존 리뷰 분기별 건수", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-044", "q": "아마존 리뷰 Hyalucica 제품 리뷰", "route": "bigquery", "category": "아마존리뷰"},
    {"id": "RV-AM-045", "q": "아마존 리뷰 브랜드별 리뷰 수", "route": "bigquery", "category": "아마존리뷰"},

    # ===== Table 8: Qoo10_Review (큐텐리뷰) — RV-QO-001 ~ RV-QO-045 =====
    {"id": "RV-QO-001", "q": "큐텐 리뷰 전체 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-002", "q": "큐텐 리뷰 제품별 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-003", "q": "큐텐 리뷰 브랜드별 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-004", "q": "큐텐 리뷰 채널별 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-005", "q": "큐텐 리뷰 월별 추이", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-006", "q": "큐텐 리뷰 가장 많은 제품", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-007", "q": "큐텐 리뷰 최근 수집 날짜", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-008", "q": "큐텐 리뷰 제품별 최신 리뷰", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-009", "q": "큐텐 리뷰 전체 제품 목록", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-010", "q": "큐텐 리뷰 브랜드별 제품 수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-011", "q": "큐텐 리뷰 수집 건수 월별", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-012", "q": "큐텐 리뷰 채널별 제품 수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-013", "q": "큐텐 리뷰 최근 3개월 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-014", "q": "큐텐 리뷰 제품별 리뷰 수 TOP10", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-015", "q": "큐텐 리뷰 가장 최근 리뷰 날짜", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-016", "q": "큐텐 리뷰 브랜드별 월별 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-017", "q": "큐텐 리뷰 제품별 채널별 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-018", "q": "큐텐 리뷰 수집날짜별 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-019", "q": "큐텐 리뷰 2024년 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-020", "q": "큐텐 리뷰 제품명 목록", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-021", "q": "큐텐 리뷰 채널 목록", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-022", "q": "큐텐 리뷰 브랜드 목록", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-023", "q": "큐텐 리뷰 리뷰날짜별 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-024", "q": "큐텐 리뷰 제품 몇개 있어", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-025", "q": "큐텐 리뷰 총 몇건이야", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-026", "q": "큐텐 리뷰 가장 오래된 리뷰", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-027", "q": "큐텐 리뷰 최근 1개월 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-028", "q": "큐텐 리뷰 제품별 분포", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-029", "q": "큐텐 리뷰 월별 수집 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-030", "q": "큐텐 리뷰 Centella 관련 리뷰", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-031", "q": "큐텐 리뷰 Ampoule 제품 리뷰 수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-032", "q": "큐텐 리뷰 Sun 제품 리뷰 수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-033", "q": "큐텐 리뷰 Cream 제품 리뷰 수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-034", "q": "큐텐 리뷰 Toner 제품 리뷰 수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-035", "q": "큐텐 리뷰 전체 브랜드 수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-036", "q": "큐텐 리뷰 전체 채널 수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-037", "q": "큐텐 리뷰 날짜 범위", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-038", "q": "큐텐 리뷰 제품 카테고리별", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-039", "q": "큐텐 리뷰 수집일 기준 최근", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-040", "q": "큐텐 리뷰 가장 많은 브랜드", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-041", "q": "큐텐 리뷰 가장 많은 채널", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-042", "q": "큐텐 리뷰 2025년 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-043", "q": "큐텐 리뷰 분기별 건수", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-044", "q": "큐텐 리뷰 Hyalucica 제품 리뷰", "route": "bigquery", "category": "큐텐리뷰"},
    {"id": "RV-QO-045", "q": "큐텐 리뷰 브랜드별 리뷰 수", "route": "bigquery", "category": "큐텐리뷰"},

    # ===== Table 9: Shopee_Review (쇼피리뷰) — RV-SH-001 ~ RV-SH-045 =====
    {"id": "RV-SH-001", "q": "쇼피 리뷰 전체 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-002", "q": "쇼피 리뷰 제품별 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-003", "q": "쇼피 리뷰 브랜드별 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-004", "q": "쇼피 리뷰 채널별 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-005", "q": "쇼피 리뷰 월별 추이", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-006", "q": "쇼피 리뷰 가장 많은 제품", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-007", "q": "쇼피 리뷰 최근 수집 날짜", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-008", "q": "쇼피 리뷰 제품별 최신 리뷰", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-009", "q": "쇼피 리뷰 전체 제품 목록", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-010", "q": "쇼피 리뷰 브랜드별 제품 수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-011", "q": "쇼피 리뷰 수집 건수 월별", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-012", "q": "쇼피 리뷰 채널별 제품 수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-013", "q": "쇼피 리뷰 최근 3개월 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-014", "q": "쇼피 리뷰 제품별 리뷰 수 TOP10", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-015", "q": "쇼피 리뷰 가장 최근 리뷰 날짜", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-016", "q": "쇼피 리뷰 브랜드별 월별 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-017", "q": "쇼피 리뷰 제품별 채널별 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-018", "q": "쇼피 리뷰 수집날짜별 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-019", "q": "쇼피 리뷰 2024년 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-020", "q": "쇼피 리뷰 제품명 목록", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-021", "q": "쇼피 리뷰 채널 목록", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-022", "q": "쇼피 리뷰 브랜드 목록", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-023", "q": "쇼피 리뷰 리뷰날짜별 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-024", "q": "쇼피 리뷰 제품 몇개 있어", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-025", "q": "쇼피 리뷰 총 몇건이야", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-026", "q": "쇼피 리뷰 가장 오래된 리뷰", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-027", "q": "쇼피 리뷰 최근 1개월 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-028", "q": "쇼피 리뷰 제품별 분포", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-029", "q": "쇼피 리뷰 월별 수집 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-030", "q": "쇼피 리뷰 Centella 관련 리뷰", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-031", "q": "쇼피 리뷰 Ampoule 제품 리뷰 수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-032", "q": "쇼피 리뷰 Sun 제품 리뷰 수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-033", "q": "쇼피 리뷰 Cream 제품 리뷰 수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-034", "q": "쇼피 리뷰 Toner 제품 리뷰 수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-035", "q": "쇼피 리뷰 전체 브랜드 수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-036", "q": "쇼피 리뷰 전체 채널 수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-037", "q": "쇼피 리뷰 날짜 범위", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-038", "q": "쇼피 리뷰 제품 카테고리별", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-039", "q": "쇼피 리뷰 수집일 기준 최근", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-040", "q": "쇼피 리뷰 가장 많은 브랜드", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-041", "q": "쇼피 리뷰 가장 많은 채널", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-042", "q": "쇼피 리뷰 2025년 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-043", "q": "쇼피 리뷰 분기별 건수", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-044", "q": "쇼피 리뷰 Hyalucica 제품 리뷰", "route": "bigquery", "category": "쇼피리뷰"},
    {"id": "RV-SH-045", "q": "쇼피 리뷰 브랜드별 리뷰 수", "route": "bigquery", "category": "쇼피리뷰"},

    # ===== Table 10: Smartstore_Review (스마트스토어리뷰) — RV-SM-001 ~ RV-SM-045 =====
    {"id": "RV-SM-001", "q": "스마트스토어 리뷰 전체 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-002", "q": "스마트스토어 리뷰 제품별 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-003", "q": "스마트스토어 리뷰 브랜드별 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-004", "q": "스마트스토어 리뷰 채널별 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-005", "q": "스마트스토어 리뷰 월별 추이", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-006", "q": "스마트스토어 리뷰 가장 많은 제품", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-007", "q": "스마트스토어 리뷰 피부고민별 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-008", "q": "스마트스토어 리뷰 피부고민별 제품 분포", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-009", "q": "스마트스토어 리뷰 전체 제품 목록", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-010", "q": "스마트스토어 리뷰 브랜드별 제품 수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-011", "q": "스마트스토어 리뷰 수집 건수 월별", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-012", "q": "스마트스토어 리뷰 채널별 제품 수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-013", "q": "스마트스토어 리뷰 최근 3개월 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-014", "q": "스마트스토어 리뷰 제품별 리뷰 수 TOP10", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-015", "q": "스마트스토어 리뷰 가장 최근 리뷰 날짜", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-016", "q": "스마트스토어 리뷰 브랜드별 월별 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-017", "q": "스마트스토어 리뷰 제품별 채널별 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-018", "q": "스마트스토어 리뷰 수집날짜별 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-019", "q": "스마트스토어 리뷰 2024년 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-020", "q": "스마트스토어 리뷰 제품명 목록", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-021", "q": "스마트스토어 리뷰 채널 목록", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-022", "q": "스마트스토어 리뷰 브랜드 목록", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-023", "q": "스마트스토어 리뷰 리뷰날짜별 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-024", "q": "스마트스토어 리뷰 제품 몇개 있어", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-025", "q": "스마트스토어 리뷰 총 몇건이야", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-026", "q": "스마트스토어 리뷰 가장 오래된 리뷰", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-027", "q": "스마트스토어 리뷰 최근 1개월 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-028", "q": "스마트스토어 리뷰 제품별 분포", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-029", "q": "스마트스토어 리뷰 월별 수집 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-030", "q": "스마트스토어 리뷰 Centella 관련 리뷰", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-031", "q": "스마트스토어 리뷰 Ampoule 제품 리뷰 수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-032", "q": "스마트스토어 리뷰 Sun 제품 리뷰 수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-033", "q": "스마트스토어 리뷰 Cream 제품 리뷰 수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-034", "q": "스마트스토어 리뷰 Toner 제품 리뷰 수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-035", "q": "스마트스토어 리뷰 전체 브랜드 수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-036", "q": "스마트스토어 리뷰 전체 채널 수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-037", "q": "스마트스토어 리뷰 날짜 범위", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-038", "q": "스마트스토어 리뷰 피부고민 유형 목록", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-039", "q": "스마트스토어 리뷰 수집일 기준 최근", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-040", "q": "스마트스토어 리뷰 가장 많은 브랜드", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-041", "q": "스마트스토어 리뷰 가장 많은 채널", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-042", "q": "스마트스토어 리뷰 2025년 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-043", "q": "스마트스토어 리뷰 분기별 건수", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-044", "q": "스마트스토어 리뷰 Hyalucica 제품 리뷰", "route": "bigquery", "category": "스마트스토어리뷰"},
    {"id": "RV-SM-045", "q": "스마트스토어 리뷰 피부고민별 월별", "route": "bigquery", "category": "스마트스토어리뷰"},

    # ===== Table 11: meta data_test (메타광고) — MT-001 ~ MT-040 =====
    {"id": "MT-001", "q": "메타 광고 전체 건수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-002", "q": "메타 광고 국가별 건수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-003", "q": "메타 광고 브랜드별 건수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-004", "q": "메타 광고 활성 광고 수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-005", "q": "메타 광고 페이지별 건수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-006", "q": "메타 광고 유형별 건수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-007", "q": "메타 광고 플랫폼별 건수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-008", "q": "메타 광고 국가별 활성 광고 비율", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-009", "q": "메타 광고 수집날짜별 건수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-010", "q": "메타 광고 가장 많은 국가", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-011", "q": "메타 광고 가장 많은 브랜드", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-012", "q": "메타 광고 가장 많은 페이지", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-013", "q": "메타 광고 활성 vs 비활성 비율", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-014", "q": "메타 광고 국가별 브랜드 분포", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-015", "q": "메타 광고 유형별 국가별", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-016", "q": "메타 광고 플랫폼별 국가별", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-017", "q": "메타 광고 전체 페이지 목록", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-018", "q": "메타 광고 전체 브랜드 목록", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-019", "q": "메타 광고 전체 국가 목록", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-020", "q": "메타 광고 전체 유형 목록", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-021", "q": "메타 광고 플랫폼 목록", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-022", "q": "메타 광고 국가코드별 건수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-023", "q": "메타 광고 수집일 기준 최근", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-024", "q": "메타 광고 최근 수집 데이터", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-025", "q": "메타 광고 월별 수집 건수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-026", "q": "메타 광고 국가별 페이지 수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-027", "q": "메타 광고 브랜드별 활성 광고", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-028", "q": "메타 광고 페이지별 활성 비율", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-029", "q": "메타 광고 유형별 활성 비율", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-030", "q": "메타 광고 국가별 광고 유형", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-031", "q": "메타 광고 브랜드별 페이지 수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-032", "q": "메타 광고 활성 광고 국가별", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-033", "q": "메타 광고 가장 오래된 광고", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-034", "q": "메타 광고 최근 시작된 광고", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-035", "q": "메타 광고 국가별 플랫폼 분포", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-036", "q": "메타 광고 브랜드별 국가 분포", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-037", "q": "메타 광고 페이지별 국가별", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-038", "q": "메타 광고 전체 광고 수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-039", "q": "메타 광고 2024년 수집 건수", "route": "bigquery", "category": "메타광고"},
    {"id": "MT-040", "q": "메타 광고 데이터 요약", "route": "bigquery", "category": "메타광고"},
]

# ---------------------------------------------------------------------------
# Execution Engine
# ---------------------------------------------------------------------------

print_lock = Lock()
results_lock = Lock()
all_results: list[dict] = []
completed = 0
completed_lock = Lock()


def run_one(t: dict, existing: dict) -> dict:
    """Run a single test question against the API."""
    # Skip if already OK/WARN (resumable)
    prev = existing.get(t["id"])
    if prev and prev.get("status") in ("OK", "WARN"):
        return prev

    start = time.time()
    try:
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": t["q"]}],
            "stream": False,
        }
        resp = requests.post(API_URL, json=payload, timeout=120)
        elapsed = time.time() - start
        answer = resp.json()["choices"][0]["message"]["content"]
        answer_len = len(answer)

        if answer_len < 20:
            status = "EMPTY"
        elif elapsed >= 90:
            status = "FAIL"
        elif elapsed >= 60:
            status = "WARN"
        else:
            status = "OK"

        return {
            "id": t["id"],
            "query": t["q"],
            "route": t["route"],
            "category": t["category"],
            "status": status,
            "time": round(elapsed, 1),
            "answer_len": answer_len,
            "answer_preview": answer[:200],
        }
    except Exception as e:
        return {
            "id": t["id"],
            "query": t["q"],
            "route": t["route"],
            "category": t["category"],
            "status": "ERROR",
            "time": round(time.time() - start, 1),
            "answer_len": 0,
            "answer_preview": str(e)[:200],
        }


def _save(results: list[dict]) -> None:
    sorted_results = sorted(results, key=lambda r: r["id"])
    RESULT_FILE.write_text(
        json.dumps(sorted_results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _print_summary(results: list[dict]) -> None:
    cats = defaultdict(
        lambda: {"OK": 0, "WARN": 0, "FAIL": 0, "ERROR": 0, "EMPTY": 0, "times": []}
    )
    for r in results:
        cats[r["category"]][r["status"]] += 1
        cats[r["category"]]["times"].append(r["time"])

    total = len(results)
    ok = sum(1 for r in results if r["status"] == "OK")
    warn = sum(1 for r in results if r["status"] == "WARN")
    fail = sum(1 for r in results if r["status"] in ("FAIL", "ERROR", "EMPTY"))

    print(f"\n{'=' * 70}")
    print(f"  MARKETING TABLES QA RESULTS  ({total} questions)")
    print(f"{'=' * 70}")
    print(f"  OK: {ok} ({ok / total * 100:.1f}%) | WARN: {warn} | FAIL+ERR+EMPTY: {fail}")

    times = [r["time"] for r in results if r["status"] not in ("ERROR",)]
    if times:
        times.sort()
        avg = sum(times) / len(times)
        p50 = times[len(times) // 2]
        p95 = times[int(len(times) * 0.95)]
        print(f"  Avg: {avg:.1f}s | p50: {p50:.1f}s | p95: {p95:.1f}s")

    print(
        f"\n  {'Category':<20} {'OK':>4} {'WARN':>5} {'FAIL':>5} {'ERR':>4} {'EMPTY':>5} "
        f"{'Total':>6} {'Avg':>6} {'Pass%':>6}"
    )
    print(
        f"  {'-' * 20} {'----':>4} {'-----':>5} {'-----':>5} {'----':>4} {'-----':>5} "
        f"{'------':>6} {'------':>6} {'------':>6}"
    )
    for cat in sorted(cats.keys()):
        c = cats[cat]
        cat_total = sum(c[s] for s in ("OK", "WARN", "FAIL", "ERROR", "EMPTY"))
        cat_ok = c["OK"] + c["WARN"]
        avg_t = sum(c["times"]) / len(c["times"]) if c["times"] else 0
        print(
            f"  {cat:<20} {c['OK']:>4} {c['WARN']:>5} {c['FAIL']:>5} {c['ERROR']:>4} "
            f"{c['EMPTY']:>5} {cat_total:>6} {avg_t:>5.1f}s {cat_ok / cat_total * 100:>5.1f}%"
        )

    # Top 20 slowest
    slow = sorted(results, key=lambda r: r["time"], reverse=True)[:20]
    print(f"\n  TOP 20 Slowest:")
    for r in slow:
        print(
            f"    [{r['id']}] {r['time']}s {r['status']} -- {r['query'][:50]}"
        )

    # Failures / Errors
    failures = [r for r in results if r["status"] in ("FAIL", "ERROR", "EMPTY")]
    if failures:
        print(f"\n  FAILURES / ERRORS ({len(failures)}):")
        for r in failures:
            print(
                f"    [{r['id']}] {r['status']} ({r['time']}s) -- {r['query'][:50]}"
            )
            if r["answer_preview"]:
                print(f"      Preview: {r['answer_preview'][:100]}")


def main() -> None:
    global completed

    # Load existing results for resumability
    existing: dict[str, dict] = {}
    if RESULT_FILE.exists():
        try:
            prev = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
            existing = {r["id"]: r for r in prev}
        except Exception:
            pass

    total = len(TESTS)
    skipped = sum(
        1 for t in TESTS if existing.get(t["id"], {}).get("status") in ("OK", "WARN")
    )
    print(f"Marketing QA Test: {total} questions, {skipped} already passed (resumable)")
    print(f"API: {API_URL} | Model: {MODEL} | Workers: {NUM_GROUPS}")
    print(f"Results: {RESULT_FILE}")
    print(f"{'-' * 70}")

    # Split into groups for parallel execution
    groups: list[list[dict]] = [[] for _ in range(NUM_GROUPS)]
    for i, t in enumerate(TESTS):
        groups[i % NUM_GROUPS].append(t)

    completed = 0

    def run_group(group: list[dict]) -> None:
        global completed
        for t in group:
            r = run_one(t, existing)
            with results_lock:
                all_results.append(r)
            with completed_lock:
                completed += 1
                current = completed
            status_icon = {
                "OK": "+",
                "WARN": "!",
                "FAIL": "X",
                "ERROR": "E",
                "EMPTY": "0",
            }.get(r["status"], "?")
            with print_lock:
                print(
                    f"  [{current}/{total}] [{r['id']}] {status_icon} {r['status']} "
                    f"({r['time']}s) {r['query'][:50]}"
                )

            # Save every 50
            if current % 50 == 0:
                with results_lock:
                    _save(list(all_results))

    with ThreadPoolExecutor(max_workers=NUM_GROUPS) as pool:
        futures = [pool.submit(run_group, g) for g in groups]
        for f in futures:
            f.result()

    # Final save
    with results_lock:
        _save(list(all_results))

    _print_summary(all_results)


if __name__ == "__main__":
    main()
