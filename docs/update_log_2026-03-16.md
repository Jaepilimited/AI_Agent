# Update Log — 2026-03-16 (v7.5.1 QA 검증 + SQL 캐시 + 최신정보 검색 + 코드 품질)

## 변경 사항

### 1. QA 검증 시트 분석 및 반영

#### 1-A. 분석
- Google Sheet JP탭 QA 검증 데이터 76건 실패 건 분석
- 8개 카테고리로 분류하여 sql_generator.txt, orchestrator.py, sql_agent.py 수정

#### 1-B. 주요 수정

### 2. SQL Generator 프롬프트 개선 (`prompts/sql_generator.txt`)

#### 규칙 20: 카테고리 수준 질문 → Category 컬럼 사용
- "크림 매출", "앰플 수량", "토너 매출" 등 라인 지정 없이 카테고리만 언급 시 `Category = 'Cream'` 사용
- 기존 `SET LIKE '%Cream%'` / `Product LIKE '%Cream%'` 방식 폐기
- 특정 라인+제품(예: "센텔라 크림")은 기존 LIKE 유지

#### 규칙 21: Product 테이블 Sachet 제외
- `AND Product != 'Sachet'` 필수 (샘플/증정품이므로 일반 분석 제외)
- FOC 관련 질문만 Sachet 포함

#### 규칙 22: 동남아 → Continent2 사용
- "동남아", "동남아시아" → `Continent2 = '동남아시아'` (Continent1에 해당 값 없음!)
- `Continent1 = '동남아시아'` 사용 시 0행 반환 문제 해결

#### 규칙 23: 인기/베스트셀러 → 매출 기준
- "인기 제품", "베스트셀러" → `ORDER BY total_revenue DESC` (매출 기준 정렬)

#### 규칙 24: B2C Company_Name = Mall_Classification
- B2C 데이터에서는 Company_Name이 Mall_Classification 값

#### 규칙 25: 수량 질문 → Product 테이블 우선
- "판매 수량", "갯수", "몇 개 팔렸어" → Product 테이블 사용 강화

#### 기타 수정
- 신규 업체 매출: `New_Flag = '신규'` 사용 (First_Cohort 연도 비교 사용 금지)
- 라빈네이처 → 랩인네이쳐 한국어 매핑 추가
- 테이블 선택 규칙에 "갯수", "세일즈", "매상" 키워드 추가

### 3. 라우팅 키워드 보강 (`app/agents/orchestrator.py`)

#### _DATA_KEYWORDS 추가
- "세일즈", "매상", "비중", "비율", "갯수", "개수", "판매량", "전년 대비", "베스트셀러", "인기 제품"

#### _STRONG_DATA 추가 (CS 오분류 방지)
- "판매량", "세일즈", "매상", "갯수", "개수", "비교", "비중", "비율", "전년 대비", "베스트셀러", "인기 제품", "가장 많이 팔"
- 이전: "히알루시카 크림 미국 아마존 판매량" → CS 라우팅 (오류)
- 이후: → BigQuery 라우팅 (정상)

### 4. 답변 생성 안티 할루시네이션 (`app/agents/sql_agent.py`)

- format_answer 프롬프트에 최우선 규칙 추가:
  > "⚠️ 절대 규칙: SQL 실행 결과 데이터만 사용하여 답변! 일반 상식이나 외부 정보 절대 포함 금지"
- 이전: "싱가포르 베스트셀러" → 일반 싱가포르 식품/전자 정보 할루시네이션
- 이후: → SKIN1004 실제 매출 데이터 기반 답변

### 5. Brand Filter 서버사이드 강제 (`app/api/routes.py`)

- 비 admin 사용자가 brand_filter 미전송 시, 서버에서 자동으로 그룹 기반 필터 적용
- Frontend에서 brand_filter dropdown 제거, my_brand_filter 자동 전송

### 6. Admin Group Management UI 개선 (`app/frontend/chat.js`)

- 그룹 상세보기: alert → 모달 (부서 그룹핑, 검색, 체크박스 일괄 제거)
- 부서 배정: "교체 모드" 추가 (기존 멤버 전체 교체)

### 7. SQL 캐시 시스템 (`app/agents/sql_agent.py`)

- **동일 질문 반복 시 SQL 생성 LLM 호출 스킵** (5.4s → 0.01s)
- MariaDB `sql_cache` 테이블 + 인메모리 LRU (500건)
- 캐시 키: 질문 정규화 + brand_filter 해시
- 대화 맥락이 있는 후속 질문은 캐시 제외 (정확성 보장)
- **테스트 결과: 2회차부터 27~30% 속도 향상 (3~5초 절감)**

| 질문 | 1회차 (miss) | 2회차 (hit) | 절감 |
|------|-------------|------------|------|
| 2025년 미국 매출 | 12.0s | 8.8s | -3.2s (27%) |
| 2025년 일본 매출 | 11.7s | 8.2s | -3.5s (30%) |
| 국가별 크림 매출 | 19.3s | 14.5s | -4.8s (25%) |

### 8. 최신 정보 검색 수정 (`app/agents/orchestrator.py`)

- `_needs_web_search()` 수정: 검색 키워드 체크를 길이 체크보다 먼저 실행
- 이전: "현재 대통령이 누구야?" → 검색 없이 답변 시도 → 실패
- 이후: "대통령" 키워드 매칭 → Google Search 활성화 → **이재명 (21대) 정확 응답**
- `_SEARCH_KEYWORDS` 추가: "대통령", "총리", "올해", "최근", "지금", "선거", "국회", "정부"
- 검색 실패 시 빈 문자열 → 안내 메시지 반환

| 질문 | 수정 전 | 수정 후 | 시간 |
|------|---------|---------|------|
| 현재 대통령 | 답변 불가 | 이재명 (21대, 2025.6.4 취임) ✅ | 10.0s |
| 오늘 주요 뉴스 | 답변 불가 | 2026.3.16 실시간 뉴스 ✅ | 22.9s |
| 원달러 환율 | 답변 불가 | 약 1,499원 ✅ | 120s |

### 9. LLM 속도 최적화 (`app/core/llm.py`)

- retry delay: `[1, 2, 4]s` → `[0.3, 0.8, 2]s` (재시도 시 최대 3초 절감)
- JSON 생성 `max_output_tokens`: 4096 → 1024 (chart config 생성 속도 향상)

### 10. 코드 품질 개선

- `chart.py`: `COLORS_SOLID`/`BORDERS` 중복 제거 → alias
- `sql_agent.py`: `brand_filter` 타입 힌트 `str` → `Optional[str]`
- `routes.py`: brand_filter 조회 실패 시 `pass` → `logger.warning()`
- `chat.js`: 이벤트 리스너 누수 수정 (dropdown/load 반복 시 중복 등록)
- `admin_group_api.py`: 불필요한 빈 줄 정리
- `main.py`: 무의미한 버전 코멘트 제거
- `mariadb.py`: SQLite 스키마에 `brand_filter`, `sql_cache` 테이블 추가

### 11. 라우팅 정확도 개선 (`app/agents/orchestrator.py`)

#### 기능 질문 가드 (Capability Pattern Guard)
- "이미지 분석 가능해?", "차트 그릴 수 있어?" 등 시스템 기능 문의 → direct 라우팅
- `_CAPABILITY_PATTERNS`: "가능해", "수 있어", "뭐할 수" 등 감지
- "되나", "돼?" 등은 CS 질문과 충돌 방지를 위해 제외 (예: "임산부가 써도 되나요")

#### 복합 Notion 키워드 우선 처리
- "반품 정책 알려줘" → "반품" in `_DATA_KEYWORDS`가 notion 라우팅 차단 문제
- `_COMPOUND_NOTION`: "반품 정책", "반품정책" → _DATA_KEYWORDS 제외 체크보다 우선 처리
- "쇼피 반품 추이" 등 데이터 질문은 기존대로 bigquery 라우팅 유지

#### CS 키워드 "아이" 오분류 수정
- "아이폰 최신 가격" → "아이" 서브스트링 매칭으로 CS 오라우팅 발생
- "아이" 단독 제거 → 복합 키워드로 대체: "아이 피부", "아이에게", "아이가 써", "아이한테"

#### 웹 검색 가드 (SKIN1004 비즈니스 컨텍스트 체크)
- "올해 한국 GDP 성장률" → "성장률" in `_DATA_KEYWORDS` + "올해" in `_SEARCH_KEYWORDS` → bigquery 오라우팅
- SKIN1004 비즈니스 용어 없으면 → direct 라우팅 (일반 지식/시사 질문)

| 질문 | 수정 전 | 수정 후 |
|------|---------|---------|
| 반품 정책 알려줘 | bigquery (차단) | notion ✅ |
| 이미지 분석 가능해? | bigquery ("분석") | direct ✅ |
| 차트 그릴 수 있어? | bigquery ("차트") | direct ✅ |
| 아이폰 최신 가격 | cs ("아이") | direct ✅ |
| 올해 한국 GDP 성장률 | bigquery ("성장률") | direct ✅ |

### 12. 시스템 프롬프트 자기인식 보강

- Direct LLM 응답 시 시스템 기능 7가지를 정확히 안내하도록 프롬프트 추가
  - Google 실시간 웹검색, BigQuery SQL 실행, Notion 문서 검색
  - Google Workspace 연동, CS Q&A, 이미지 분석, 차트 생성
- `orchestrator.py` + `graph.py` 양쪽 시스템 프롬프트 동기화
- 이전: "구글 웹서치 api 맞아?" → "실시간 웹검색 없음" (오답)
- 이후: → "Google 실시간 웹검색 연동되어 있습니다" ✅

### 13. 코드 리뷰 & 리팩토링

- `sql_agent.py`: SQL 캐시 LRU 버그 수정 — `dict` → `OrderedDict` + `move_to_end()` (접근 순서 기반 정확한 LRU 구현)
- `sql_agent.py`: 캐시 히트 시 불필요한 DB UPDATE 제거 (MariaDB I/O 절감)
- `app/core/prompt_fragments.py`: 중복 언어 감지 프롬프트 → 공유 상수 추출 (orchestrator.py + sql_agent.py DRY)
- `admin_group_api.py`: N+1 쿼리 → 배치 SELECT로 최적화 (100명 배정 시 200쿼리 → 2쿼리)
- `auth_api.py`: `/me` 엔드포인트 — admin 사용자 불필요한 쿼리 제거 (10-15ms 절감)

## QA 테스트 결과 (수정 후)

| 질문 | 이전 | 이후 | 시간 |
|------|------|------|------|
| 히알루시카 크림 미국 아마존 판매량 | CS (오류) | BigQuery ✅ | 19.1s |
| 싱가포르 베스트셀러 제품 | Direct 할루시네이션 | BigQuery ✅ | 16.3s |
| 센텔라 앰플 전년 대비 갯수 비교 | CS (오류) | BigQuery ✅ | 16.0s |
| 국가별 크림 매출 | SET LIKE (부정확) | Category=Cream ✅ | 20.4s |
| 동남아 전체 매출 | 0행 (Continent1 오류) | 3,602.7억원 ✅ | 11.6s |
| 제품별 수량 순위 2025년 | Sachet 1위 | Sachet 제외 ✅ | 17.6s |
| 미국 토너 제품 매출 | SET LIKE (부정확) | Category=Toner ✅ | 20.0s |
| 국가별 토너 수량 2025년 | 0행 | 3,549,747개 ✅ | 18.5s |
| B2B 신규 업체 매출 | First_Cohort 오류 | New_Flag=신규 ✅ | 17.2s |
| 필리핀 인기 제품 TOP 5 | 수량 기준 | 매출 기준 ✅ | 14.7s |

**10/10 테스트 통과 (100%)**

## 파이프라인 성능 분석 (측정)

```
SQL Agent 파이프라인 단계별 소요 시간:
  1. SQL 생성 (LLM):    5.40s  (39%) ← 캐시로 스킵 가능
  2. SQL 검증:          0.00s  (0%)
  3. BQ 실행:           1.30s  (9%)
  4. 답변 생성 (LLM):   7.27s  (52%)
  ────────────────────────────────
  총:                  13.96s
  LLM 합계:           12.66s  (91%)
```

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `prompts/sql_generator.txt` | 규칙 20-25 추가, Sachet/Category/Continent/인기 기준 |
| `app/agents/orchestrator.py` | 라우팅 정확도 개선 (기능질문 가드, 복합 Notion, CS "아이" 수정, 웹검색 가드, 시스템 프롬프트) |
| `app/agents/graph.py` | direct_llm_answer 시스템 프롬프트에 기능 설명 추가 |
| `app/agents/sql_agent.py` | SQL 캐시 시스템 + 안티 할루시네이션 + LRU 버그 수정 + 타입 힌트 |
| `app/core/llm.py` | retry delay 최적화, JSON max_tokens 축소 |
| `app/core/chart.py` | COLORS_SOLID/BORDERS 중복 제거, 라인차트 개선 |
| `app/core/prompt_fragments.py` | 언어 감지 프롬프트 공유 상수 추출 (신규) |
| `app/api/routes.py` | 서버사이드 brand_filter 강제 + 에러 로깅 |
| `app/api/admin_group_api.py` | N+1 쿼리 → 배치 SELECT 최적화 |
| `app/api/auth_api.py` | /me admin 불필요 쿼리 제거 |
| `app/frontend/chat.js` | Brand filter dropdown 제거 + admin modal 개선 + 이벤트 리스너 누수 수정 |
| `app/frontend/chat.html` | brand-filter-select 요소 제거 |
| `app/static/style.css` | Brand filter 스타일 제거, admin modal 스타일 추가 |
| `app/db/mariadb.py` | SQLite 스키마에 brand_filter, sql_cache 추가 |
| `app/main.py` | 불필요 코멘트 제거 |
