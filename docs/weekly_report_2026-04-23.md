# 주간업무 보고 — 2026.04.17 ~ 04.23

## 통합 광고 테이블 Wide→Long 마이그레이션
- 광고 데이터 구조를 15컬럼 wide 테이블에서 long 형식으로 재설계
  - `integrated_advertising_data` → `integrated_ad` 완전 교체
  - media / team / account_name 차원 추가, 15개 매체 지원
  - SQL 생성 6/6, BigQuery 실행 4/4 검증 완료
- **기대효과**: 신규 매체 추가 시 스키마 변경 없이 데이터만 추가하면 됨. 팀·매체별 교차 분석 가능

## 사용자 익명화 시스템 구축 (Anonymization v1.0)
- 대화 이력에서 사용자 식별 정보를 분리하는 구조 도입
  - HMAC-SHA256 기반 anon_id 생성 (`compute_anon_id`)
  - conversations / message_feedback 테이블 anon_id 컬럼 추가 및 전환
  - 구조화 로그(structlog)에서 user_id / email 자동 스크러빙 처리
  - 기존 607개 대화 / 18개 피드백 백필 완료
- **기대효과**: 내부 로그·DB 유출 시 사용자 식별 불가. 개인정보 최소화 원칙 충족

## AI 평가 파이프라인 구축 (Eval Pipeline v1.0)
- AI 답변 품질을 정량적으로 측정·추적하는 자동화 시스템 구축
  - 질문 자동 생성: 실제 질문 데이터 → Gemini Flash 합성 → BGE-M3 임베딩 중복 제거 → 450개 JSONL
  - Playwright 자동 실행: 브라우저 로그인 → 질문 전송 → 응답 캡처 → DB 저장
  - 관리자 리뷰 UI: 팀·판정 필터, 마크다운 렌더링, 👍👎 버튼
  - 실행 성과 분석기: 팀별 p50/p95/max 응답시간 집계, 20초 초과 팀 플래그
- **기대효과**: "AI가 잘 답변하는가"를 감이 아닌 수치로 확인. 개선 전후 비교 기반 마련

## BigQuery 응답속도 개선
- 대형 테이블 조회 시 발생하던 느린 응답과 높은 비용 구조 개선
  - 날짜 필터 없는 쿼리 자동 감지 후 Gemini Flash로 재작성 (`_enforce_partition_filter`)
    - 대상 테이블: SALES_ALL_Backup, integrated_ad, Integrated_marketing_cost
  - 소스 표시(SSE)를 위키 조회보다 먼저 전송 → 체감 응답 시작 속도 개선
- **기대효과**: 파티션 필터 없는 전체 스캔 제거로 BQ 비용 절감 및 쿼리 속도 향상

## UI 리브랜딩 — SKIN1004 → Craver 전면 전환
- 앱 표시 이름을 Craver로 통일
  - HTML 타이틀, 사이드바 브랜드, 로그인 서브타이틀, 추천 칩, 시스템 프롬프트 등 전체 교체
  - DB명·PM2 프로세스명·이메일 도메인은 유지
- **기대효과**: 내부 서비스 브랜드 일관성 확보

## 로그인 display_name 불일치 장애 구조적 수정
- AD sync 타이밍에 따라 로그인이 401로 실패하던 반복 장애 제거
  - signin / signup 로직을 display_name 문자열 비교에서 ad_user_id 직접 조회로 전환
  - 부서 선택 시 프론트에서 사용자 ID를 함께 전송하도록 수정
- **기대효과**: sync·heal 타이밍·이름 불일치와 완전히 무관하게 로그인 안정 보장

## AD Sync 시스템화 및 경쟁조건 수정
- 오류 시 조용히 실패하고 동시 실행 충돌이 발생하던 구조를 완전히 제거
  - AD 연결 실패 시 5초 간격 최대 3회 재시도
  - Lock 파일로 동시 실행 방지, Jandi 웹훅으로 실패·경고 즉시 알림
  - 단계별 에러 처리 및 종료 코드 체계화 (0/2/3/4)
  - APScheduler 내 중복 트리거 제거 → Task Scheduler 단독 실행으로 통일
  - Lock을 원자적 파일 생성(`O_CREAT | O_EXCL`)으로 교체해 TOCTOU 경쟁조건 제거
  - 이름 보정(heal) 단계에 재시도 로직 추가 (MariaDB 1020 에러 대응)
- **기대효과**: 매일 22:00 AD sync 장애 완전 제거, 오류 발생 즉시 Jandi 알림으로 감지 가능

## 보안 아키텍처 문서 작성
- 보안팀 검토 대응을 위한 시스템 구성 문서 작성
  - 로그인 흐름, JWT 구조, SQL 5단계 파이프라인, LLM 데이터 처리, GWS OAuth, 네트워크 구성 등 문서화
  - PDF + Word 형식으로 배포
- **기대효과**: 보안팀 질의에 즉시 대응 가능한 기술 자료 확보
