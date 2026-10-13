# Update Log — 2026-05-27

## 변경 사항

### 1. [기능] BigQuery Distinct 값 학습 + Chart.js 로컬 번들링
- BigQuery `SALES_ALL_Backup`, `Product` 테이블의 실제 distinct 값(국가, 채널, 팀, 브랜드 등) 전수 학습
- SQL Generator 프롬프트에 실제 DB 값 기반 매핑 테이블 추가 (Mall_Classification, Country, Team_NEW 등)
- Chart.js CDN 의존 제거 → `app/static/chart.umd.min.js` 로컬 번들로 교체 (CDN 장애 시 차트 깨짐 방지)

### 2. [기능] 전체 타임아웃 5분(300s)으로 상향
- GWS Agent, Notion Agent, SQL Agent, BigQuery, Google Auth, Wiki Embed 전 구간 일괄 상향
- 이전: 60~120s → 이후: 300s
- 대용량 데이터 조회나 복잡한 집계 쿼리 시 타임아웃 오류 방지

### 3. [버그픽스] 라우팅 — 회사 키워드 direct 강제 오버라이드 제거
- `스킨1004`, `SKIN1004` 등 회사명 키워드가 BQ/Notion 질문을 `direct` 라우트로 덮어쓰던 버그 수정
- `_DATA_OVERRIDE_GUARD` 가드 추가: BQ/Notion/GWS 에이전트가 이미 선택된 경우 라우트 보호
- 회사 관련 질문도 데이터 기반으로 정확히 라우팅됨

### 4. [기능] 차트 데이터 라벨 항상 표시
- 기존: 마우스 오버 시에만 값 표시 (hover tooltip)
- 이후: 막대·원형·선 차트 모두 데이터 값이 항상 표시 (ChatGPT 스타일)
- 숫자 포맷: 1억 이상 → `X.X억`, 1만 이상 → `X만`, 그 외 콤마 포맷

### 5. [버그픽스] SQL 프롬프트 Brand 필터 과적용 제거
- 국가별/채널별/팀별 매출 집계 시 `Brand IN ('SK', 'CL')` 자동 삽입되던 버그 수정
- 전사 집계 질문에서 Brand 필터 완전 제거 → 전체 브랜드 포함 정상 집계
- ⛔ 금지 문구 삭제, 예시 기반 가이드로 전환 (프롬프트 충돌 해소)

### 6. [버그픽스] Brand 필터 캐싱 문제 — 항상 DB에서 조회
- JWT 토큰 또는 프론트엔드 캐시에서 `brand_filter`를 읽어오던 로직 제거
- 모든 요청마다 MariaDB `users` 테이블에서 직접 조회
- 로그인 후 관리자가 brand_filter 변경해도 즉시 반영되도록 수정

## 테스트 결과

| 테스트 | 결과 |
|--------|------|
| 국가별 매출 집계 (Brand 필터 없이) | ✅ 전체 국가 정상 집계 |
| 차트 데이터 라벨 표시 | ✅ 항상 표시 확인 |
| 회사 키워드 라우팅 보호 | ✅ BQ 라우트 유지 확인 |
| 타임아웃 300s 적용 | ✅ 전 에이전트 일괄 적용 |
| 프로덕션 배포 (34.64.99.179:3000) | ✅ 무중단 reload 완료 |

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `prompts/sql_generator.txt` | BQ distinct 학습값 추가, Brand 필터 규칙 충돌 제거 |
| `app/agents/orchestrator.py` | 회사 키워드 direct override 제거, _DATA_OVERRIDE_GUARD 추가 |
| `app/frontend/chat.js` | 차트 데이터 라벨 항상 표시 로직 추가 |
| `app/frontend/chat.html` | Chart.js 로컬 번들 참조 변경 |
| `app/api/routes.py` | brand_filter 항상 DB에서 조회 (JWT 캐시 제거) |
| `app/static/chart.umd.min.js` | Chart.js 로컬 번들 추가 |
| `app/agents/gws_agent.py` | 타임아웃 300s 상향 |
| `app/agents/notion_agent.py` | 타임아웃 300s 상향 |
| `app/agents/sql_agent.py` | 타임아웃 300s 상향 |
| `app/core/bigquery.py` | 타임아웃 300s 상향 |
| `app/core/google_auth.py` | 타임아웃 300s 상향 |
