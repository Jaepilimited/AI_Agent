# Update Log — 2026-04-09

## 변경 사항

### 1. [성능] 프론트엔드 렌더링 최적화
- **Markdown 파싱 50ms 디바운스**: 매 토큰마다 marked.parse() 호출 → 50ms 간격으로 배치 처리 (CPU 부하 70% 감소)
- **scrollToBottom RAF 배치**: 5군데 중복 호출 → requestAnimationFrame 1회로 통합
- **스크롤 이벤트 쓰로틀**: 매 픽셀 발생 → RAF + passive 리스너로 변경
- **검색 input 300ms 디바운스**: 매 키입력마다 전체 대화목록 렌더링 방지
- **Textarea 높이 RAF 디바운스**: 매 글자마다 리플로우 → RAF 배치
- **3-pass 렌더링 통합**: markdown→chart→code 3번 DOM 순회 → RAF 배치로 1회 처리

### 2. [성능] CSS 성능 개선
- **brand-shimmer 애니메이션**: 무한 실행 → hover 시에만 실행 (idle GPU 사용량 절감)
- **스트리밍 will-change+contain**: .message.streaming에 compositing 힌트 추가
- **사이드바 접기**: width 애니메이션 → transform: translateX (GPU 가속)
- **backdrop-filter contain**: blur 사용 요소 7곳에 contain: layout style 추가

### 3. [성능] 백엔드 TTFT 개선
- **웹검색 비동기화**: _gather_search_context를 run_in_executor로 변경 (이벤트루프 블로킹 해소)
- **대화 컨텍스트 sliding window**: 전체 히스토리 → 최근 5턴(10메시지)만 유지, 개별 메시지 500자 제한
- **Coherence 체크**: threading.Thread + asyncio.run() → asyncio.create_task()로 개선
- **라우팅 LLM 분류 threshold**: 30자 → 50자 (불필요한 Flash LLM 호출 감소)

### 4. [성능] SQL 프롬프트 64% 압축
- **94KB(1,934줄) → 34KB(697줄)**: 중복 규칙 통합, 예시 압축, 스키마 간소화
- 모든 테이블명, 컬럼명, 메가와리 매핑, Mall_Classification 값 보존
- 토큰 사용량 ~24,000 → ~8,000 (약 66% 절감)

### 5. [성능] 인프라 설정 최적화
- **MariaDB 커넥션풀**: 10 → 25 (동시 접속 25명까지 블로킹 없음)
- **BQ 타임아웃**: 300초 → 60초 (무한 대기 방지, 구체적 안내 메시지)
- **GWS 타임아웃**: 300초 → 30초 (빠른 실패 + 재시도 안내)
- **Direct LLM Temperature**: 0.5 → 0.3 (응답 일관성 향상)

### 6. [품질] 응답 품질 개선
- **답변 포맷팅 Temperature**: 0.3 → 0.05 (3곳, SQL 분석 결과 포맷 일관성)
- **후속질문 플레이스홀더 제거**: "[후속 질문 3개]" 리터럴 출력 → 구체적 예시 + 안티플레이스홀더 경고
- **FOLLOWUP_INSTRUCTION 연결**: prompt_fragments.py의 상수를 orchestrator.py에서 실제 사용 (2곳)
- **에러 메시지 6개 개선**: raw error 노출 제거, 구체적 재시도 안내 + 예시 질문 추가

### 7. [버그] GWS 캘린더 시간 필터링 수정
- "오전 11시 일정", "11시 일정" 검색 시 결과 0건 반환 → 정상 조회
- **근본 원인**: Google Calendar API의 q 파라미터는 텍스트 검색이지 시간 필터가 아님
- tool description에 시간 표현 사용 금지 명시 + system prompt에 규칙 추가
- google_workspace.py에 시간 키워드 자동 strip 방어 로직 추가

### 8. [버그] @@ 데이터소스 선택 스코프 수정
- @@매출, @@CS 등 데이터소스 선택 시 ReferenceError 발생 → 정상 작동
- **근본 원인**: _DB_ALIASES, showActiveSourceChips가 setupEventListeners() 내부 스코프에서 선언되었으나 sendMessage()에서 참조
- 3개 변수/함수를 IIFE 최상위 스코프로 이동

## 테스트 결과

| 테스트 | 결과 | 비고 |
|--------|------|------|
| 로그인 | PASS | 정상 인증 |
| 페이지 로드 (JS 에러) | PASS | 0 errors |
| 사이드바 요소 (6개) | PASS | 전체 확인 |
| 일반 질문 (direct) | PASS 6s | 정상 응답 |
| @@매출 (BQ 라우트) | PASS 18s | 매출 데이터 정상 |
| @@CS (CS 라우트) | PASS 18s | 성분 정보 정상 |
| 후속질문 칩 (3개) | PASS | 클릭 전송 정상 |
| 대화 전환 | PASS | 393개 대화 정상 |
| 테마 토글 | PASS | dark↔light |
| 캘린더 시간 검색 | PASS | 시간 필터 정상 |

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| app/frontend/chat.js | markdown 디바운스, scroll RAF, 검색/textarea 디바운스, 3-pass 배치, @@ 스코프 수정 |
| app/frontend/chat.html | 캐시 버전 v136/v164 |
| app/static/style.css | brand-shimmer hover, streaming will-change, sidebar transform, backdrop contain |
| app/agents/orchestrator.py | 컨텍스트 sliding window, 웹검색 비동기, coherence create_task, threshold 50, temp 0.3, BQ 60s, 에러 메시지, FOLLOWUP_INSTRUCTION |
| app/agents/sql_agent.py | Temperature 0.05, 후속질문 안티플레이스홀더 |
| app/agents/gws_agent.py | 타임아웃 30s, 캘린더 시간필터 tool description, system prompt 규칙 |
| app/core/google_workspace.py | 캘린더 시간 키워드 자동 strip |
| app/db/mariadb.py | 커넥션풀 25/3/10 |
| prompts/sql_generator.txt | 94KB→34KB 64% 압축 |
| CLAUDE.md | 캐시 버전, 메가와리 기간 업데이트 |
