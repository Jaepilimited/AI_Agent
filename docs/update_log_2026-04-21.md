# Update Log — 2026-04-21~22 (UI 리브랜딩 + 로그인 구조 개선 + AD Sync 시스템화)

## 변경 사항

### 1. [리브랜딩] UI 텍스트 SKIN1004 → Craver 전면 교체

앱 표시 이름을 SKIN1004에서 Craver로 통일.
DB명·PM2 프로세스명·이메일 도메인·제품 브랜드명은 유지.

- **HTML 타이틀**: `login.html`, `chat.html`, `eval_review.html`, `dashboard.html`, `ai_harness.html`, `presentation.html`
- **사이드바 브랜드**: `SKIN1004 AI` → `Craver AI` (chat.html)
- **로그인 서브타이틀**: `SKIN1004 Enterprise AI` → `Craver Enterprise AI`
- **추천 칩**: CS 추천 질문 2개 Craver로 교체 (chat.js)
- **AI 시스템 프롬프트**: orchestrator.py 내 사용자 노출 문자열 전체 (`당신은 SKIN1004의 AI 어시스턴트` 등)
- **에이전트 시스템 프롬프트**: `gws_agent.py`, `notion_agent.py`, `qdrant_agent.py`, `team_agent.py`
- **분석 기준 footer**: `SKIN1004 내부 데이터` → `Craver 내부 데이터` (response_formatter.py)
- **파일 주석**: `auth.js`, `chat.js`, `loader.js`, `style.css`, `custom.css`
- **FastAPI 앱 타이틀**: `main.py`

### 2. [버그수정] 로그인 display_name 불일치 장애 — 구조적 영구 수정

**원인**: `search-name` API는 `COALESCE(users.display_name, ad_users.display_name)`(한글)을
반환하지만, `signin` 은 `ad_users.display_name`(AD sync 직후 영문) 으로 매칭해 401 발생.
heal이 매일 돌아도 sync~heal 사이 시간 동안 상시 취약한 구조적 문제.

**Fix**: signin/signup 모두 `ad_user_id` 직접 조회로 전환.

- `app/api/auth_api.py`:
  - `SigninRequest` / `SignupRequest` 에 `id: int | None = None` 추가
  - signin/signup 로직: `req.id` 있으면 `WHERE ad_users.id = %s` 우선 검색
  - display_name 문자열 비교는 fallback으로만 (id 없을 때)
- `app/frontend/auth.js`:
  - submit body에 `id: selectedUser.id` 포함
- **결과**: heal 상태·AD sync 타이밍과 완전히 무관하게 로그인 안정 보장

### 3. [버그수정] AD Sync cursor closed 크래시

**원인**: `sync_to_db()` 에서 `with conn.cursor() as cursor:` 블록 종료 후 닫힌
`cursor`를 다시 `.execute()` 하는 코드 존재 → `ProgrammingError: Cursor closed`.
heal 단계 미실행 → 이름 불일치 17명 발생.

- `scripts/sync_ad_users.py`: 블록 밖 stale cursor 참조 2행 제거

### 4. [고도화] AD Sync 시스템화 — 재시도·Lock·알림·로그

에러 발생 시 조용히 실패하던 구조를 완전히 제거. 장애를 즉시 감지·알림.

- **AD 연결 재시도**: 실패 시 5초 간격 최대 3회 재시도 (네트워크 일시 장애 대응)
- **Lock 파일** (`logs/ad_sync.lock`): 동시 실행 방지, 10분 이내 중복 실행 차단
- **단계별 에러 처리**: AD 조회 실패 / DB 저장 실패 / 이름 보정 실패 각각 분리
- **Jandi 알림**: 실패 시 ❌ 빨간 알림, 경고 시 ⚠️ 주황 알림 자동 전송
- **로그 파일** (`logs/ad_sync.log`): 매 실행마다 SUCCESS/FAILED + 전체 내용 기록
- **종료 코드 체계화**: 0=성공 / 2=AD실패 / 3=DB실패 / 4=heal실패 (Task Scheduler 감지 가능)

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/frontend/login.html` | 타이틀, 로고 alt, 서브타이틀 Craver로 교체 |
| `app/frontend/chat.html` | 타이틀, 사이드바 브랜드 Craver로 교체 |
| `app/frontend/chat.js` | 파일 주석, CS 추천 칩 2개, 모델 레이블 Craver로 교체 |
| `app/frontend/auth.js` | 파일 주석 + `id` 필드 submit body 포함 |
| `app/frontend/eval_review.html` | 타이틀 Craver로 교체 |
| `app/static/dashboard.html` | 타이틀 Craver로 교체 |
| `app/static/ai_harness.html` | 타이틀 Craver로 교체 |
| `app/static/presentation.html` | 타이틀, h1, 비교표 헤더 Craver로 교체 |
| `app/static/style.css` | 파일 주석 Craver로 교체 |
| `app/static/custom.css` | 파일 주석 Craver로 교체 |
| `app/static/loader.js` | 파일 주석, console.log Craver로 교체 |
| `app/agents/orchestrator.py` | AI 정체 문자열, 사용자 메시지, 분석 기준 footer Craver로 교체 |
| `app/agents/gws_agent.py` | 시스템 프롬프트 Craver로 교체 |
| `app/agents/notion_agent.py` | 시스템 프롬프트 Craver로 교체 |
| `app/agents/qdrant_agent.py` | 시스템 프롬프트 Craver로 교체 |
| `app/agents/team_agent.py` | 시스템 프롬프트 Craver로 교체 |
| `app/core/response_formatter.py` | multi 분석 기준 footer Craver로 교체 |
| `app/main.py` | FastAPI 앱 타이틀 Craver로 교체 |
| `app/api/routes.py` | health check 서비스명 Craver로 교체 |
| `app/api/auth_api.py` | SigninRequest/SignupRequest id 필드 추가, id 기반 AD 사용자 조회 |
| `scripts/sync_ad_users.py` | cursor 버그 수정 + 재시도/Lock/Jandi알림/로그 시스템화 |

## 배포

- **Dev (3001)**: 변경 배포 완료
- **Prod (3000)**: 반영 대기 (주인님 확인 후 `pm2 reload skin1004-prod`)
