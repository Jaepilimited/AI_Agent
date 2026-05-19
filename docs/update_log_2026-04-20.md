# Update Log — 2026-04-20 (BigQuery 파티션 필터 + AD 동기화 고도화 + Jandi 연동)

## 변경 사항

### 1. [성능] BigQuery 파티션 필터 자동 강제 (_enforce_partition_filter)

날짜 필터 없는 대형 테이블 쿼리가 전체 스캔을 유발하는 문제 해결.
SQL Agent가 생성한 쿼리를 실행 직전에 가로채어 날짜 조건을 자동 주입.

- 신규 함수 `_enforce_partition_filter(sql: str, today: date) → str` (`app/agents/sql_agent.py`):
  - 대상 테이블 3개: `SALES_ALL`, `SALES_DOMESTIC`, `SALES_ALL_Backup`
  - WHERE 절이 없거나 날짜 컬럼(`Date`, `Order_Date`, `Shipping_Date`)이 전혀 없으면 **90일 기본 범위** 자동 삽입
  - 날짜 조건이 이미 있으면 무수정 통과 (기존 쿼리 의도 보존)
  - 파티션 필터 > full-history 기본 규칙 — 우선순위 명확화
- LangGraph `enforce_partition_filter` 노드 제거 → `execute_sql` 노드 내부 직접 호출로 단순화
- `prompts/sql_generator.txt`: 파티션 규칙 2개 추가 + `SALES_ALL_Backup` 예시에서 오해 유발하는 `Date >= '2019-01-01'` 기본값 제거
- `app/agents/orchestrator.py`:
  - BQ 단일 소스 경로에서 `wiki_context` 미주입 이유 주석 추가
  - **조기 SSE 전송**: wiki lookup 전에 `[source: bigquery]` 먼저 yield → 응답 시작 지연 해소
- `tests/test_sql_agent.py`: 112줄 테스트 추가
  - SALES_ALL / SALES_DOMESTIC / SALES_ALL_Backup 3개 테이블 자동 필터 주입 검증
  - 날짜 조건 이미 있는 경우 무수정 통과 검증

### 2. [AD 연동] sync_ad_users.py 2-step 파이프라인 고도화

AD 이름이 영문인 사용자를 한글로 자동 교정하는 구조 도입.

- `scripts/sync_ad_users.py` 전면 리팩토링:
  - **STEP 1**: AD → MariaDB `ad_users` upsert (362명, `_NAME_OVERRIDES` 오버라이드 적용)
  - **STEP 2**: 이름 자동 보정 — `users.display_name`(한글) → `ad_users.display_name` 역반영 (이미 가입한 사용자 자동 heal)
  - **`--heal-only`** 옵션 신규: AD LDAP 동기화 없이 이름 보정만 즉시 실행
  - **`_NAME_OVERRIDES`** 딕셔너리: 미등록 사용자 중 AD displayName이 영문인 경우 수동 매핑 (11명)
  - `get_db_connection()` 헬퍼 분리, `step()` / `ok()` / `info()` 출력 유틸 추가
- `CLAUDE.md`: AD 동기화 규칙 섹션 추가 (2-step 파이프라인, 오버라이드 규칙, 절대 금지 사항)

### 3. [알림] Jandi 웹훅 연동 스크립트 신규

Claude Code 작업 완료 시 마지막 응답을 Jandi 팀 채팅으로 자동 전송.

- **신규** `scripts/jandi_webhook.py`:
  - Claude Code `Stop` hook 이벤트 수신 (stdin JSON)
  - `transcript_path` JSONL에서 마지막 assistant 텍스트 추출 (최대 3000자)
  - Jandi Connect API로 POST — 오렌지 accent 색상 + session ID 표시
  - 모든 예외 묵음 처리 (hook 실패가 Claude Code를 블록하지 않음)

## 테스트 결과

| 테스트 | 결과 | 비고 |
|--------|------|------|
| `tests/test_sql_agent.py` (파티션 필터) | 6/6 pass | 3테이블 자동 주입 + 기존 조건 보존 + mock 격리 |
| 코드 리뷰 (superpowers:code-reviewer) | PASS | 최종 리뷰 이슈 전부 반영 완료 |

## 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/agents/sql_agent.py` | `_enforce_partition_filter` 신규 + execute_sql 내 직접 호출, LangGraph 노드 제거 |
| `app/agents/orchestrator.py` | 조기 SSE source 전송 + 주석 추가 |
| `prompts/sql_generator.txt` | 파티션 필터 규칙 2개 추가, 예시 date 기본값 제거 |
| `tests/test_sql_agent.py` | 파티션 필터 자동 주입 테스트 112줄 추가 |
| `scripts/sync_ad_users.py` | 2-step 파이프라인, --heal-only, _NAME_OVERRIDES, 헬퍼 함수 |
| `scripts/jandi_webhook.py` | **신규** — Claude Code Stop hook → Jandi 웹훅 릴레이 |
| `CLAUDE.md` | AD 동기화 규칙 섹션 추가 |
| `docs/superpowers/specs/2026-04-20-bigquery-performance-design.md` | **신규** — BigQuery 성능 개선 설계 문서 |
| `docs/superpowers/plans/2026-04-20-bigquery-performance.md` | **신규** — BigQuery 성능 개선 구현 플랜 (4 tasks) |

## 배포

- **Dev (3001)**: 변경 배포 완료
- **Prod (3000)**: 반영 대기 (주인님 확인 후 `pm2 reload skin1004-prod`)
