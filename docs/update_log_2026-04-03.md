# Update Log — 2026-04-03

## 변경 사항

### 1. [기능] @@ 데이터소스 선택 시스템
- `@@매출`, `@@광고`, `@@인플루언서`, `@@피플`, `@@bp`, `@@cs`, `@@gws` 등 직접 데이터소스 지정
- `@@전체`/`@@ALL` — 모든 소스 활성화
- `@@전체해제`/`@@none` — 비활성화 (직접 대화만)
- `@@목록` — 사용 가능한 전체 데이터소스 목록
- 입력창에서 `@@` 입력 시 그룹화된 자동완성 드롭다운 (SVG 아이콘)
- `/api/datasources` API 엔드포인트 추가
- `_DB_REGISTRY`에 항목 추가로 확장 가능

### 2. [기능] 인플루언서 데이터 개선
- **Team → add_part 전환**: 데이터학습.xlsb 기준, Team 컬럼 무시하고 add_part를 팀으로 사용
- add_part 유효 값: East1_파트1~3, East2_파트1, West_파트1~5, JBT, KBT, BCM, B2B2, B2B3 등
- Media 영→한 자동 치환: Instagram→인스타그램, TikTok→틱톡 (DB 실제 값 한국어)
- 유가/무가 협업 용어 매핑: Cost > 0 = 유가, Cost = 0 = 무가/시딩

### 3. [기능] 팀별자료(노션) 검색 개선
- 한국어 붙여쓰기 대응: "성과급대상자는" → 2-3글자 chunk 슬라이딩 윈도우 매칭
- PEOPLE 팀 가중치: HR/IT 키워드 감지 시 PEOPLE 리소스 우선
- 쿼리 확장: 복지포인트→사내근로복지기금, 와이파이→Wi-Fi 등
- Notion toggle 블록 펼치기: Playwright 크롤러가 접힌 내용까지 수집
- sync 후 자동 Playwright enrichment (sync_team_resources.py 통합)

### 4. [버그] 버그 수정
- **탭 전환 멈춤 (Issue 7)**: loadConversation/newChat에서 _stopTokenDrain() 미호출 → 추가
- **도메인 가드레일 (Issue 1)**: 항공권/호텔/맛집 → 코드 레벨 hard block (LLM 무시 방지)
- **라우팅 충돌**: 팀 키워드 + 데이터 키워드 동시 존재 시 BQ 우선
- **3002 dev 서버**: SQLite 모드 제거, MariaDB 직접 연결

### 5. [기능] PM2 프로세스 관리
- skin1004-prod (3000): 프로덕션, 절대 kill 금지
- skin1004-dev (3002): 개발/테스트용
- pm2-windows-startup: Windows 부팅 시 자동 실행
- ecosystem.config.js: 양쪽 서버 설정 통합

## 테스트 결과

| 테스트 | 결과 | 비고 |
|--------|------|------|
| PEOPLE 50문항 QA | 98% (49/50) | 성과급, 퇴사, 연차, 복지 등 |
| IT 50문항 QA | 86% (43/50) | VPN, 프린터, Wi-Fi, 메일 등 |
| CS 50문항 QA | 84% (42/50) | 제품 성분, 교환반품, 라인 등 |
| Playwright E2E | 5/5 PASS | 탭전환, 금액, 분기, 스크롤, 수정 |
| 인플루언서 20문항 | 90% (18/20) | 국가별, 티어별, 플랫폼별 |

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| app/agents/orchestrator.py | @@ 시스템, 도메인 가드레일, 라우팅 개선 |
| app/agents/sql_agent.py | Media 영→한 치환, 인플루언서 SQL 개선 |
| app/agents/team_agent.py | 붙여쓰기 매칭, PEOPLE 가중치, 쿼리 확장 |
| app/api/routes.py | /api/datasources 엔드포인트 |
| app/db/mariadb.py | DEV_MODE 항상 MariaDB |
| app/frontend/chat.html | 캐시 버스팅 v127/v140 |
| app/frontend/chat.js | @@ 자동완성 UI, 탭전환 fix, source label |
| app/static/style.css | @@ 드롭다운 그리드 스타일 |
| ecosystem.config.js | PM2 prod+dev 설정 |
| prompts/sql_generator.txt | add_part 팀 매핑, Media 한국어, 용어 매핑 |
| scripts/crawl_notion_pages.py | toggle 펼치기, 재크롤 임계값 |
| scripts/sync_team_resources.py | sync() 함수, Playwright enrichment |
