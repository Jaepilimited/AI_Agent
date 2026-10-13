# Update Log — 2026-05-21

## 변경 사항

### 1. [버그픽스] SQL Agent Team_NEW 팀명 현행화
- BigQuery `SALES_ALL_Backup` 테이블의 Team_NEW 컬럼 실제 값과 프롬프트 불일치 수정
- 구버전 팀명으로 조회 시 매출 0원 반환되던 문제 해결
- GM본부 4개 팀(EAST1/2, WEST_Ecomm, WEST_MKT)이 정상 집계됨

| 구버전 (수정 전) | 현재 DB 값 (수정 후) |
|---|---|
| GM_EAST1 | EAST1 |
| GM_EAST2 | EAST2 |
| GM_Ecomm | WEST_Ecomm |
| GM_MKT | WEST_MKT |
| DD_DT1 | DT1 |
| DD_DT2 | DT2 |

### 2. [정리] SQL 프롬프트 Brand 필터 규칙 정비
- `Brand != 'ETC'` 자동 삽입 규칙 삭제 (DB에 해당 데이터 없음)
- 전사 매출 질문 시 Brand 필터 없이 전체 포함하도록 변경
- 제품/라인/카테고리 질문은 기존대로 `Brand IN ('SK', 'CL')` 유지

### 3. [기능] Qdrant Google Search Fallback (2026-05-19 배포)
- Qdrant 벡터 검색 품질 미달 시 Google Search 기반 폴백 응답 추가
- QUALITY_GATE 0.50 → 0.57 상향 (무관 질문 차단 강화)
- 관련 없는 질문에 대한 오답 생성 방지

## 테스트 결과

| 테스트 | 결과 |
|--------|------|
| GM본부 팀별 매출 (7개 팀) | ✅ 전체 집계 정상 |
| 프로덕션 배포 (34.64.99.179:3000) | ✅ 무중단 reload 완료 |
| System Status | ✅ All OK |

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `prompts/sql_generator.txt` | Team_NEW 팀명 현행화, Brand ETC 규칙 삭제 |
| `app/agents/qdrant_agent.py` | Google Search fallback 추가, QUALITY_GATE 강화 |
