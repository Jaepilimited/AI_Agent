# Update Log — 2026-06-24

## 변경 사항

### 1. [기능] 자발적 성장 프로세스 — CS 지식 갭 감지 + 주간 리포트 + Admin 대시보드
- CS Agent가 매칭 실패한 질문을 `knowledge_gaps` 테이블에 자동 기록 (CS 공백 자동 탐지)
- 매주 월요일 00:10 APScheduler로 주간 성장 지표 자동 집계 (`growth_snapshots` 테이블)
  - 측정 항목: SQL 캐시 적중률, CS 갭 건수, 사용자 피드백(👍/👎), 노드 수 추이
- Admin 드로어에 "성장 지표" 탭 추가 — 주간 리포트 카드 + CS 갭 목록 + 검토 완료 처리
- API 4개 추가: GET/PATCH knowledge-gaps, GET/POST growth-report

### 2. [버그픽스] Maintenance 페이지 완전 차단 제거
- 기존: `manual` 점검 모드 시 `/login`, `/` 모두 maintenance.html 서빙 → 사용자가 서비스 종료로 오해
- 이후: 로그인 페이지는 항상 접근 가능, BQ 답변에만 업데이트 경고 표시
  - `manual` 점검 → BQ 응답에 "데이터 점검 중" 즉시 안내
  - `auto` 점검 → BQ 답변 끝에 "데이터 테이블이 업데이트 중일 수 있습니다" soft warning 추가
- 스트리밍 BQ 경로 2곳(DB prefix early-return + 메인 스트리밍) 모두 처리

### 3. [보안] BQ 데이터 출처 비공개
- 기존: 답변 footer에 테이블명(`SALES_ALL_Backup`), 컬럼명(`Sales1_R`), 프로젝트ID 노출
- 이후: "내부 데이터베이스"로만 표현, 내부 스키마 정보 완전 비공개
- SQL Generator 시스템 프롬프트에 출처 보안 규칙 명시

### 4. [버그픽스] SQL GROUP BY 오류 방지 — CTE + CROSS JOIN 비율 계산
- CTE 집계 컬럼을 SELECT에 쓸 때 GROUP BY 누락으로 BigQuery 오류 발생하던 패턴 수정
- `prompts/sql_generator.txt`에 규칙 추가: GROUP BY 포함 또는 MAX() 래핑 또는 스칼라 서브쿼리 3가지 방법 명시
- ❌ `WITH ts AS (...총합...) GROUP BY country` → ✅ `GROUP BY country, ts.total` 예시 포함

### 5. [버그픽스] 라우팅 — 제품명 + 지역 키워드 → CS 대신 BigQuery
- 기존: "포어마이징 벨벳 선크림 남미 반응" → CS 키워드("포어마이징", "선크림") 우선 → CS 라우팅
- 원인: "남미", "동남아" 등 지역 키워드가 CS 우선 체크를 넘기는 `_STRONG_DATA`에 없었음
- 수정: `_STRONG_DATA`에 지역 키워드 추가 — 남미, 북미, 동남아, 유럽, 중동, 아시아, 주요 국가명
- LLM 분류기 프롬프트에 명시적 예시 추가: "제품명 + 국가/지역 + 반응/매출" → bigquery

### 6. [기능] 전체 제품 SKU 271개 — 제품명 인식 전면 개선
- 기존: LLM이 "포어마이징 벨벳 피니쉬 선스크린" → `%Velvet_Finish%` 패턴 알 수 없어 CS로 잘못 처리
- BigQuery에서 실제 SKU 271개 전수 추출 → `sql_generator.txt`에 라인별 그룹화 등재
- SK(센텔라/히알루시카/히알루테카/포어마이징/티트리카/톤브라이트닝/프로바이오시카/랩인네이처),
  ZB(좀비뷰티), CL(커먼랩스) 전 제품 한국어 별칭 + LIKE 패턴 매핑
- 이제 어떤 제품 이름으로 물어봐도 정확한 SQL LIKE 패턴 생성

### 7. [버그픽스] line 차트 데이터 라벨 + 숫자 단위 포맷 (2026-06-11)
- 기존: bar/doughnut 차트만 라벨 표시, line 차트는 hover tooltip만
- 이후: line 차트도 항상 데이터 라벨 표시 (inlineLineLabels 플러그인 추가)
- 숫자 포맷 공통화: 1억 이상 → X.X억, 1만 이상 → X.X만, 라벨 경계 잘림 clamp 보정

### 8. [버그픽스] 피드백 기반 개선 — 재고 질문 + 제품명 환각 (2026-06-11)
- 재고/입출고 데이터 미보유 명시: SQL로 몰래 판매량으로 대체하지 않고 정직하게 안내
- Direct 라우트 시스템 프롬프트에 공식 제품 목록 추가 → 존재하지 않는 제품명 창작 방지

### 9. [버그픽스] multi 라우트 타임아웃 30s → 300s + 대화 컨텍스트 주입 (2026-06-11)
- multi 라우트 타임아웃이 30s로 짧아 긴 분석 질문이 중간에 잘리던 문제 수정
- `_handle_multi`: 이전 대화 맥락을 데이터 질문 재작성·최종 합성 프롬프트에 주입
  → "방금 그", "위 MD처럼" 참조형 복합 질문 해석 가능

### 10. [인프라] 로컬 서버 메인 전환 (2026-06-12)
- GCP(34.64.99.179) AI Agent 서비스 중지 → 로컬 172.16.1.250:3000이 프로덕션 메인
- GCP는 CRM(skin1004-crm-*) 서비스만 운영 유지
- 대화 이력 GCP→로컬 병합 복구 완료: 713 convos / 5,040 msgs / 피드백 36건

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/agents/orchestrator.py` | maintenance 스트리밍 처리, 지역 키워드 STRONG_DATA 추가, LLM 분류기 예시, multi 타임아웃·컨텍스트 |
| `app/agents/sql_agent.py` | 데이터 출처 보안 규칙, format_answer footer |
| `app/agents/cs_agent.py` | knowledge_gaps 자동 기록 |
| `app/core/growth_report.py` | 주간 성장 리포트 (신규) |
| `app/db/mariadb.py` | knowledge_gaps, growth_snapshots 테이블 DDL |
| `app/api/admin_api.py` | knowledge-gaps, growth-report API 4개 추가 |
| `app/main.py` | maintenance 차단 제거, APScheduler 주간 리포트 job |
| `app/frontend/chat.html` | Admin "성장 지표" 탭 추가 |
| `app/frontend/chat.js` | loadGrowthReport, loadKnowledgeGaps, line 차트 라벨 |
| `prompts/sql_generator.txt` | 전체 SKU 271개 등재, GROUP BY 규칙, 데이터 출처 보안 |
| `app/frontend/maintenance.html` | 점검 안내 문구 정비 |
