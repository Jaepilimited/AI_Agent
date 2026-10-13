# 국내·해외 CS 조회 계약

정의 확인일: 2026-09-16.

## 출처와 연결 방식

사용자가 공유한 [DB 정보 — 국내 CS](https://docs.google.com/spreadsheets/d/1iwFzA_ksXwC2W_AkrW-aVBjcBFkHt_96usQHoMy6wh0/edit?gid=0#gid=0)와 [해외 CS](https://docs.google.com/spreadsheets/d/1iwFzA_ksXwC2W_AkrW-aVBjcBFkHt_96usQHoMy6wh0/edit?gid=606652395#gid=606652395)는 **컬럼 정의서**다. 고객 상담 원문을 다시 수집하는 시트가 아니다. 두 탭의 정의를 기존 BigQuery 스키마와 대조하여 셀라의 SQL 조회에 연결한다.

| 소스 키 | BigQuery 테이블 | 한 행의 뜻 | 기본 기간 |
|---|---|---|---|
| `국내CS` / `@@국내CS` | `skin1004-319714.cs_dashboard.domestic_cs_records` | 원본에서 묶은 처리 기록 | `received_at` DATE |
| `해외CS` / `@@해외CS` | `skin1004-319714.cs_dashboard.overseas_cs_refunds` | Shopify 환불 | `DATE(refund_created_at, 'Asia/Seoul')` |

일반 질문과 소스 선택 모두 `bigquery` 경로를 사용하되, **CS 대시보드가 제공하는 질문은 해당 화면의 조회 API를 우선 사용한다**. 지원하지 않는 차원·지표는 위 BigQuery 테이블로 조회하고 기준을 구분한다. 기존 CS 문서는 BP에 통합했으며 `@@CS`는 `@@BP`의 호환 별칭이다. BP는 제품 Q&A 시트와 CS 문서를 함께 검색하고 실제 원본 링크를 표시한다.

## 대시보드 우선 답변

원본 화면은 [CS 대시보드](http://34.64.99.254:8061/)다. `app/core/cs_dashboard.py`는 국내 `GET /api/dashboard/summary`, 해외 `GET /api/overseas/dashboard/summary`를 읽기 전용으로 호출한다. 화면의 접수·처리·보상 요약, 일별 추이, 사유 분포 및 국내 판매처·상품별 현황을 표와 차트로 답하고, 적용한 기간·필터를 원본 화면 링크에도 전달한다. 상세 고객·이메일·주문 원문은 답변에 포함하지 않는다.

**해외 대시보드와 Shopify 환불 테이블은 별도 자료다.** 대시보드의 `source`는 `google_sheets_overseas`이고, 위 테이블은 Shopify 환불 원장이다. 2026-09-16 대조에서 2026년 8월 대시보드는 68건·USD 5,828.60, Shopify 테이블은 94건·USD 7,015.31이었다. 검증 시점의 수치이며 답변에 하드코딩하지 않는다. 국가별·고유 주문별·원화 환산 등 상세 조회를 대시보드 수치로 표현하지 않는다.

국내 링크는 `/dashboard`, `/reports/channel`, `/reports/product`, `/reports/reason`, 해외 링크는 `/global/dashboard`를 사용한다. 데이터소스 선택과 상태 화면의 국내·해외 CS 링크는 사용자가 지정한 공통 루트 URL로 연결한다. 대시보드의 보상 기록도 실제 현금 지급 확인액으로 해석하지 않는다.

## 국내 처리 기록

- `COUNT(*)`는 처리 기록 수다. `record_id`는 스냅샷 키이며 평생 유일한 CS 접수 ID가 아니다. 주문 수는 `COUNT(DISTINCT order_key)`로 세고, 키가 없는 기록 수를 함께 표시한다.
- 접수 기간은 `received_at`, 완료 기간 질문은 `completed_at`을 쓴다. 둘 다 DATE다. `ingested_at`은 적재 시각이며 접수일이 아니다. `source_fetched_at`도 원본 시트 최종 수정시각이 아니다.
- 채널은 `channel`, 유형은 `claim_type`, 상태는 `process_status`, 사유별 기록 수는 단일값 `reason_normalized`로 집계한다. `source_tab`은 수집 출처다. 비어 있는 분류도 미분류로 포함한다.
- `items`는 접수 상품, `inspections`는 검품 상품으로 서로 다를 수 있다. 두 배열을 함께 펼치면 곱집합이 된다. 상품수량은 해당 배열의 `quantity`를 사용하며 판매수량과 구분한다.
- 배열을 펼친 결과에서 부모 금액을 다시 합산하지 않는다. 상품별 기록 수는 `COUNT(DISTINCT record_id)`를 쓰고, 상품 조건에 맞는 기록의 금액은 `EXISTS`로 부모 행을 거른 뒤 합산한다. `SUM(DISTINCT 금액)`은 중복 방지책이 아니다.
- 수량 NULL은 수량 미확인이다. 상품 미상 재배송의 원본 0이 NULL로 변환된 경우가 있어 0개라고 해석하지 않는다. `CS_RP_` 참조값은 바코드·송장번호와 구분한다.
- 기본 보상 지표는 `eligible_recorded_compensation`이다. 완료·비철회·검토 대상 제외 조건의 **기록액**이며 실제 지급 확인액이 아니다. 제외·미기재 건수도 표시한다. `compensation_amount`는 조건 적용 전 원본 기록액이다.
- `source_order_amount`는 `source_order_amount_basis`별로 나눈다. 네이버 구매금액과 다른 채널의 실결제금액을 통합 순결제액으로 합산하지 않는다. 동일 주문의 여러 처리 기록에 붙은 금액도 고유 주문의 결제액으로 오인하지 않는다.

## 해외 환불

- 환불 수는 환불 행 수 또는 `COUNT(DISTINCT shopify_refund_id)`, 주문 수는 `COUNT(DISTINCT shopify_order_id)`다. 복수 환불이 있는 주문이 존재한다.
- 기본 기간은 환불 생성시각 `refund_created_at`이다. 해외 TIMESTAMP를 날짜·월로 바꿀 때 한국 시간대를 명시한다. 완료 기간 질문만 `completed_at`을 쓰고 그 기준을 표시한다.
- `received_at`은 연결된 Return의 생성시각이다. 반품 연결이 없는 환불은 NULL이므로 기본 기간 필터에 쓰지 않는다.
- `refund_amount_usd`는 성공한 환불 거래액이다. 환불 전체가 완료되지 않아도 일부 성공 거래액이 존재할 수 있다. 완료분만 요청한 경우에만 완료 조건을 건다.
- `refund_amount_krw`의 NULL을 0으로 바꾸지 않는다. 원화 합계와 누락 환불 수를 함께 보여 준다. 미완료·환율 미확보 등 누락 사유는 실제 필드를 확인하여 설명한다.
- `payment_amount_usd`와 `payment_amount_krw`는 주문 전체 수취액이 환불 행마다 반복된 값이다. USD 주문금액은 주문별 중복 제거 후 집계한다. KRW는 같은 주문도 환불 완료일별 환율로 달라질 수 있으므로 임의의 행을 골라 합산하지 않는다. `SUM(DISTINCT 금액)`으로 처리하면 서로 다른 주문의 같은 금액이 사라진다.
- `country_name`은 영문 배송국가명이다. 매출의 한글 `Country`와 혼동하지 않는다. `country_code`도 사용할 수 있다. 국가 미상도 집계에 남긴다.
- `return_type_normalized`는 표준 **환불 사유**이며 반품·교환 접수 유형이 아니다. `intake_type`이나 표준 사유가 비어 있으면 데이터가 없음을 밝히고 미분류 건수를 보여 준다. 비정형 메모에서 정식 분류를 임의로 만들지 않는다.
- 전체 해외 CS 접수 수·모든 반품 수는 이 표로 알 수 없다. 국내 처리 기록 수와 해외 환불 수는 기준을 나눠 비교하고 하나의 전체 CS 합계로 더하지 않는다.

집계 질문에는 `customer_email`, 고객 ID, `raw_payload`, 국내 `raw_rows` 등 원본 개인정보를 노출하지 않는다.

## 예시 질문

- `@@국내CS 2026년 8월 채널별 처리 기록 수와 주문 수 알려줘`
- `@@국내CS 2026년 8월 반품 사유별 처리 기록 수 알려줘`
- `@@국내CS 2026년 8월 보상 기록액과 집계 제외 건수 알려줘`
- `@@국내CS 2026년 8월 접수 상품별 수량과 수량 미확인 건수 알려줘`
- `@@해외CS 2026년 8월 국가별 환불 건수와 달러 환불액 알려줘`
- `@@해외CS 2026년 8월 완료된 환불액을 원화로 보여주고 원화 금액 누락 건수도 알려줘`
- `@@해외CS 2026년 8월 표준 환불 사유의 분류 상태를 알려줘`

## 등록과 갱신

기존 데이터 파이프라인이 갱신하는 BigQuery 두 테이블을 조회한다. 셀라에 신규 고객 데이터 적재 작업이나 Sheets 원본 쓰기 작업을 만들지 않는다. 정의서의 컬럼·의미가 바뀌면 실제 테이블 스키마와 이 계약을 함께 검토한다.

연결 지점은 `config.allowed_tables`, SQL 에이전트의 소스별 테이블 매핑·스키마 로딩, `_DB_REGISTRY`의 소스·별칭·경로, 프론트의 소스 선택, 테이블 상태 감시 및 `schema_watch.WATCHED_DATASETS`다. 상세 SQL 규칙은 `prompts/sql_generator.txt`의 테이블 17·18에 있고, FI 권한 마스킹과 무관하게 유지되도록 FI 섹션보다 앞에 둔다.

채널·유형·상태·사유·국가 값은 `app/core/value_lists.py`의 실제 값 캐시를 사용한다. 프롬프트에 값 목록을 손으로 복사하지 않는다.

| 캐시 이름 | 테이블 컬럼 |
|---|---|
| `DomesticCSChannel` | 국내 `channel` |
| `DomesticCSClaimType` | 국내 `claim_type` |
| `DomesticCSStatus` | 국내 `process_status` |
| `DomesticCSReason` | 국내 `reason_normalized` |
| `DomesticCSCompensation` | 국내 `compensation_eligibility` |
| `OverseasCSStatus` | 해외 `process_status` |
| `OverseasCSCountry` | 해외 `country_name` |
| `OverseasCSReason` | 해외 `return_type_normalized` |
| `OverseasCSNormalization` | 해외 `normalization_status` |

최초 연결 시 새 캐시를 갱신하고 이후 기존 정기 갱신을 따른다. 값이 전부 NULL인 컬럼의 빈 목록을 가상의 분류로 채우지 않는다. 스키마 변경 감시는 `cs_dashboard`를 포함하며, 최신 적재 여부는 실제 적재 시각·테이블 상태를 확인한다. 오래된 접수일이나 환불일만으로 지금 적재 중이라고 추정하지 않는다.

2026-09-16 연결 전 점검에서는 국내 551개 처리 기록·497개 주문, 해외 1,252개 환불·1,242개 주문이 확인됐다. 해외 `received_at`은 1,191건이 NULL, `intake_type`과 `return_type_normalized`는 모두 NULL이었다. 이는 해당 시점의 검증 기록이며 이후 답변의 숫자나 분류 목록에 하드코딩하지 않는다.
