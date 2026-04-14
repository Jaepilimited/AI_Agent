# Update Log — 2026-04-08

## 변경 사항

### 1. [기능] 메가와리 기간 매핑 시스템
- 큐텐(Qoo10) 메가와리 이벤트 기간 2023~2026년 전체 SQL 프롬프트에 등록
- "메가와리" 질문 시 자동으로 해당 기간 + `Mall_Classification LIKE '%Q10%'` 필터 적용
- CASE WHEN 패턴으로 분기별 조회 (UNION ALL 금지 — SQL 잘림 방지)
- 매출+마케팅비용 cross-table JOIN CTE 지원 (매출/광고비/비중 동시 조회)
- PDF 수치 검증: DB 59.4억원 vs PDF 56억원 (환율 차 ±5% 이내 일치)

### 2. [기능] Integrated_marketing_cost 스키마 업데이트
- **Field 컬럼 분류**: '광고' = 퍼포먼스 마케팅, '시딩' = 인플루언서 마케팅
- **Media 설명 강화**: 매체/미디어별 질문 → 퍼포먼스 마케팅
- 퍼포먼스 vs 인플루언서 비교 예시 쿼리 추가

### 3. [기능] SALES_ALL 스키마 업데이트 (데이터학습.xlsb 반영)
- Brand: `ETC` = 아예 무시 규칙 추가
- Sales1_R/Sales2_R 우선순위 명확화 (매출 = Sales1_R, "매출2" 명시 시만 Sales2_R)
- Mall_Classification 확장: CBT_B2B_DutyFreeShop, ETC_B2C_Silii_Amazon(무시) 등
- ID 컬럼: "아이디" → "고객" 변경

### 4. [기능] System Status 팀별 Notion 분리
- System Status에서 팀별 Notion 페이지 개별 관리
- @@ 다중선택 지원 (여러 데이터소스 동시 선택)
- 월 인식 수정 — "이번 달", "지난달" 정확한 해석

### 5. [성능] BQ 타임아웃 확대
- 오케스트레이터 BQ 타임아웃: 30초 → 5분 (300초)
- 복잡한 cross-table 쿼리도 타임아웃 없이 처리

### 6. [버그] 분기 포맷 오류 수정
- `FORMAT_DATE('%Y-Q', Date)` 사용 금지 규칙 최우선 배치
- 올바른 패턴: `CONCAT(EXTRACT(YEAR), '-Q', EXTRACT(QUARTER))`
- "2025-Q" → "2025-Q1, 2025-Q2..." 개별 분기 정상 출력

### 7. [버그] 라우팅 누락 수정
- "마케팅 비용", "퍼포먼스", "시딩", "총액", "얼마", "메가와리" → BQ 강제 라우팅 추가
- _BIZ_CONTEXT, _STRONG_DATA 키워드 보강
- "JBT 퍼포먼스 마케팅 비용 총액이 얼마야?" → direct LLM 답변에서 BQ 정상 라우팅으로 수정

### 8. [버그] SQL 잘림(truncation) 자동 복구
- sanitize_sql: 괄호 불일치 감지 → 빈 SQL 반환 (깨진 SQL 실행 방지)
- 잘린 CTE SQL 자동 `FROM`/`ORDER BY` 추가 복구
- BQ 구문 오류 시 단순화된 SQL로 자동 재시도

### 9. [버그] 차트 생성 실패 수정
- Gemini Flash `generate_json` max_tokens: 1024 → 4096 (thinking 모드 토큰 소비 대응)
- chart config 프롬프트에 SQL 300자 + result_preview 800자로 축약 전달
- stacked_bar grouped 데이터 행 수 제한 완화 (46행 정상 처리)
- "분기별", "월별", "추이", "비중" 키워드 시 차트 자동 생성

### 10. [UI] 출력 품질 개선
- 답변 800자 제한 규칙 추가 (PDF 1페이지 분량 목표)
- max_tokens: 4096 → 3072 (간결한 출력)
- 노션 데이터 규칙: 명시적 언급 없으면 노션 데이터 미포함

## 테스트 결과

| 테스트 | 결과 | 비고 |
|--------|------|------|
| 메가와리 매출 (26-Q1) | OK 23.7s | 59.4억원 정확 |
| 퍼포먼스/인플루언서 비용 분기별 | OK+CHART 22.7s | 5개 분기 정상 |
| 카테고리별 분기 매출 | OK+CHART 29.8s | SET/Ampoule 주력 확인 |
| 퍼포먼스 비용 총액 (BQ 라우팅) | OK 14.6s | 11.1억원, 라우팅 정상 |
| 복합 질문 (매출+광고비 비중) | OK 28.9s | cross-table JOIN 성공 |

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| CLAUDE.md | 노션 데이터 규칙, 메가와리 기간표 추가 |
| prompts/sql_generator.txt | 메가와리 기간, 분기 포맷 규칙, Field 분류, CASE WHEN, cross-table JOIN |
| app/agents/orchestrator.py | BQ 타임아웃 5분, 라우팅 키워드 추가 (마케팅비용/퍼포먼스/메가와리) |
| app/agents/sql_agent.py | 출력 간결화, 차트 SQL 축약, 구문 오류 재시도, 차트 자동생성 키워드 |
| app/core/chart.py | stacked_bar grouped 제한 완화 |
| app/core/llm.py | generate_json max_tokens 1024→4096 |
| app/core/security.py | 잘린 CTE 자동 복구, 괄호 불일치 감지 |
