# Update Log — 2026-03-18 (v8.1 컨텍스트 무제한 + SQL 프롬프트 대규모 수정)

## 변경 사항

### 1. 채팅 화면 마키 제거
- `app/frontend/chat.html`에서 craver-bg 마키 배경(텍스트+이미지) 전체 삭제
- 채팅 화면 배경 정리 — 깔끔한 인터페이스로 전환

### 2. 컨텍스트 길이 무제한 확장
- `routes.py`: MAX_CONTEXT_MESSAGES 제한 완전 제거 (기존 50개 → 무제한)
- `orchestrator.py`: `_build_conversation_context()` 턴 제한 제거 + 잘림(truncation) 제거
- Gemini 1M 토큰 컨텍스트 윈도우 전체 활용 가능

### 3. Stop Hook 추가
- `.claude/settings.json`에 Stop hook 설정
- Claude 응답 종료 시 port 3000 health check 자동 실행
- 서버 다운 감지 시 재시작 강제

### 4. MariaDB PATH 등록
- 시스템 PATH에 `C:\Program Files\MariaDB 11.7\bin` 추가
- mysql 명령어 터미널에서 직접 사용 가능

### 5. MariaDB 비밀번호 변경
- `.env` MARIADB_PASSWORD 업데이트 → skin1004!

### 6. 메타 광고 SQL 프롬프트 대규모 수정 (prompts/sql_generator.txt)
- QA 시트7 검증 결과 82건 실패 분석 → 16가지 반복 패턴 추출
- 필수 규칙 16개 추가:
  - brand 소문자 비교
  - publisher_platform LIKE 사용
  - 불필요 WHERE 절 금지
  - UNNEST / ROW_NUMBER / snapshot JSON 처리 규칙
- 예시 쿼리 8개 → 14개로 확장
- 메타 광고 테이블 전용 규칙 체계화

### 7. Notion 사용자 가이드 작성
- 처음 사용자용 AI Agent 가이드 페이지 Notion 업로드
- 가이드 내용: 가입/로그인, 질문 유형별 예시, ChatGPT와 다른 점, 고급 기능 활용법

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/frontend/chat.html` | craver-bg 마키 배경 전체 삭제 |
| `app/api/routes.py` | MAX_CONTEXT_MESSAGES 제한 제거 (무제한) |
| `app/agents/orchestrator.py` | _build_conversation_context() 턴/잘림 제한 제거 |
| `.claude/settings.json` | Stop hook 추가 (health check + 재시작) |
| `prompts/sql_generator.txt` | 메타 광고 필수 규칙 16개 + 예시 14개 확장 |
