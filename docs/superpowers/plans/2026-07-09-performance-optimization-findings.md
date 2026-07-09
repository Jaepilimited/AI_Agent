# 성능 최적화 감사 결과 (2026-07-09)

## Baseline (측정: 2026-07-09)

- pm2 restart_time (skin1004-prod): 0
- pm2 memory (skin1004-prod): 58.4mb
- pm2 cpu (skin1004-prod): 0%
- pm2 uptime (skin1004-prod): 21h
- exec mode: fork_mode

`pm2 status` 원본 출력 (skin1004-prod 행):
```
│ 11 │ skin1004-prod   │ default │ N/A │ fork │ 53604 │ 21h │ 0 │ online │ 0% │ 58.4mb │ DB_PC │ disabled │
```

참고: 프로세스는 유휴 상태(요청 없음)로 측정되어 CPU 0%. 실제 요청 처리 중
리소스 사용량은 이번 정적 감사로는 직접 측정하지 못하며, 아래 감사 결과의
"경량 계측" 권고 항목을 적용한 뒤 운영 트래픽에서 재측정이 필요하다.

## 1. 에이전트 경로

**병렬화 가능한 순차 LLM 호출 — 특이사항 없음**
순차 구조 대부분이 인과적으로 필연적(라우팅 결과 후 핸들러 선택, 실패해야 재시도). multi 라우트는 이미 `asyncio.gather`로 웹검색+BQ 병렬화(orchestrator.py:1760-1764), format_answer의 답변생성+차트생성도 이미 ThreadPoolExecutor 병렬(sql_agent.py:993-998).

**중복 LLM 호출**
- `orchestrator.py:1459-1493` (`_handle_bigquery`) — 1차 실패 시 전체 파이프라인(SQL 생성부터) 재실행, 그래도 실패하면 `_handle_bigquery_fallback`에서 3번째 LLM 호출. 내부 자체 재시도(파티션 재작성/syntax 재시도/모델 escalation)까지 겹치면 한 요청에 LLM 호출 5~6회 누적 가능. **영향도: 중**(실패 경로에서만 발동, 발동 시 지연 폭증) / **리스크: 중**(복구율 유지하며 재시도 계층 축소 필요)
- `query_verifier.py`의 `QueryVerifierAgent.verify()` — `security.py`의 `validate_sql()`이 이미 하는 검증을 LLM으로 재검증하지만, sql_agent.py:553-575에서 fire-and-forget 백그라운드 스레드(4번 항목과 동일 이슈)라 지연 영향 없음. **영향도: 하(비용만) / 리스크: 하**

**대화 이력 무한 누적 — 있음**
`app/api/routes.py:154-158`에 "No message limit — Gemini 2.5 Flash supports 1M token context" 주석과 함께 `messages_for_context`가 전체 이력을 캡 없이 담음. `orchestrator.py`의 `_build_conversation_context()`(80-112행)는 최근 20개로 자르지만, **direct 라우트의 실시간 스트리밍 경로**(`route_and_stream` 768-785행)는 `_clean_messages_for_history(messages)`(64-77행, "does NOT truncate" 명시)를 통해 원본 messages 전체를 `generate_with_history_stream`에 전달. 세션이 길어질수록 매 턴마다 전체 이력을 재전송 — 토큰 비용 O(n), 누적 비용 O(n²). **영향도: 상**(가장 흔한 라우트로 추정, 긴 대화일수록 비용/지연 증가) / **리스크: 중**(오래된 메시지 요약/캡 추가는 국소적 변경)

**조기 피드백(status_callback류)**
- **이미 존재**: `route_and_stream`이 라우트 분류 직후 `yield ("source", route)`(orchestrator.py:684, 566, 714, 725) → `routes.py:274-282`가 `<!-- source:... -->` SSE 주석으로 즉시 전송.
- **공백 1 — BigQuery**: `sql_agent.py:1490-1502`(`run_sql_agent_stream`)에서 generate_sql→validate→enforce_partition_filter→execute_sql이 전부 동기 실행되며 그 사이 아무것도 yield하지 않음. "source:bigquery" 표시 후 첫 텍스트까지 완전 침묵(수초~수십초). **삽입 지점**: 1492행(generate_sql 직전), 1502행(execute_sql 직전)에 상태 문자열 yield 추가 — 기존 큐 기반 구조 그대로 활용 가능. **영향도: 상 / 리스크: 하**
- **공백 2 — CS/GWS/Notion/Team/Multi**: `orchestrator.py:882-907`이 `asyncio.wait_for(handler(...))`로 통째로 기다린 뒤 "가짜 스트리밍"으로 사후 전송(gws 최대 45초, multi 최대 300초 무응답 가능). 핸들러를 제너레이터로 바꿔야 해 변경 범위 큼. **영향도: 중 / 리스크: 상**(핸들러 시그니처 변경 필요)

**추가 발견 (버그, 요청 항목 외)**
- **`sql_agent.py:1001-1003`** — 주석은 "차트 최대 3초 대기"인데 실제 코드는 `chart_future.result(timeout=300.0)`. 스트리밍 버전(`run_sql_agent_stream:1568`)은 동일 로직에 `timeout=8.0`으로 정상 — 오타/복붙 실수로 보임. 차트 생성 지연 시 사용자가 최대 5분 추가 대기 가능. **영향도: 상**(드물지만 재현 시 매우 심각) / **리스크: 하**(숫자만 8.0~3.0으로 수정)
- **`sql_agent.py:789-790`** — 0건 결과 안내 메시지도 동일하게 `timeout=300.0`. 바로 아래 폴백 템플릿(796-810행)이 있는 걸 보면 원래 의도는 "빠른 실패 → 템플릿 폴백"인데 300초면 폴백이 사실상 발동 안 함. **영향도: 중 / 리스크: 하**
- **`sql_agent.py:1000`** — `answer_future.result()`(메인 답변 생성)에 타임아웃 자체가 없음. 비스트리밍 REST 엔드포인트(`chat_completions`, stream=False)에는 `route_and_execute()` 전체를 감싸는 타임아웃도 없어(스트리밍 경로만 라우트별 30~300초 보유), Flash API가 멈추면 HTTP 요청이 그대로 행(hang)될 수 있음. **영향도: 중 / 리스크: 하**(짧은 타임아웃 추가)

## 3. DB 레이어

**커넥션 풀 — 특이사항 없음**
- `app/db/mariadb.py:27-48` — `PooledDB(maxconnections=40, mincached=5, maxcached=15, blocking=True)` 싱글턴. 매 요청마다 새 커넥션을 만들지 않음. `conn.close()`는 DBUtils 특성상 풀 반환이라 정상. 영향도: 없음.
- (부가) `_get_pool()`이 lock 없는 lazy singleton이라 극희소 케이스로 동시 최초 호출 시 이중 생성 가능성 있음 — 실사용 영향 거의 없어 수정 리스크 대비 이득 낮음.

**N+1 패턴 — 3건**
- `app/api/admin_group_api.py:194-199` (assign_users_to_group) — 사용자 수만큼 개별 INSERT 루프. 같은 함수의 existing-check는 이미 IN절 배치인데 INSERT만 루프. 대량 배정 시 왕복 비용 누적. **영향도: 중 / 리스크: 하** (multi-row INSERT VALUES로 교체)
- `app/api/admin_group_api.py:212-217` (remove_users_from_group) — 동일 패턴, 개별 DELETE 루프. **영향도: 중 / 리스크: 하** (`DELETE ... WHERE group_id=%s AND ad_user_id IN (...)`로 대체)
- `app/knowledge/wiki_extractor.py:225-296` (_insert_facts_sync) — fact마다 dup-check 쿼리 + 성공 시 `_flag_conflict_sync`가 조회 1 + sibling마다 UPDATE. 메시지당 fact 수에 비례해 DB 왕복 선형 증가. 스트리밍 응답 후 백그라운드 실행이라 사용자 체감 지연에는 영향 없음, 다만 테이블 성장에 따라 백그라운드 부하 누적. **영향도: 하~중 / 리스크: 중** (조건부 로직이 있어 단순 배치화보다 재작성 필요)

**인덱스 미비 후보**
- `wiki_extractor.py:230-234`, `:257-261`의 dup/conflict 체크가 `WHERE LOWER(entity) = LOWER(%s)` 사용 → `knowledge_wiki` DDL(`mariadb.py:145-172`)의 `idx_entity`는 일반 인덱스(함수 기반 아님)라 `LOWER()` 래핑 시 인덱스를 못 씀 → **fact마다 풀스캔**. 테이블 성장에 비례해 악화, N+1과 겹침. **영향도: 중 / 리스크: 하** (entity를 소문자로 normalize해 저장하거나 `entity_lower` 생성 컬럼 + 인덱스 추가)
- `conversations`, `messages`, `users`, `ad_users`, `user_groups`, `access_groups` 테이블 DDL은 이 저장소에 없음(레거시/외부 스키마) — `conversation_api.py`의 `messages WHERE conversation_id=%s` 등 자주 쓰이는 WHERE절 인덱스 여부는 코드로 검증 불가, `SHOW INDEX FROM messages` 등 실측 필요. `conversations.anon_id`/`message_feedback.anon_id`는 `ensure_anon_columns()`(mariadb.py:450-478)가 명시적으로 인덱스 생성하므로 확인됨.

## 2. API/라우팅

대부분의 DB 호출은 `asyncio.to_thread`로 올바르게 감싸져 있음. 발견 사항:

1. `app/main.py:209` (`index()`) — "/" 라우트가 매 요청마다 chat.html을 동기 `.read_text()`로 읽음(to_thread 미사용), 이벤트 루프 블로킹. **영향도: 중** (로그인 후 메인 페이지 로드마다 발생, 동시 요청 시 이벤트 루프 전체 지연 가능) / **리스크: 하** (to_thread 래핑 또는 시작 시 1회 캐싱)
2. `app/main.py:99-124` — lifespan 시작 시 `_ensure_admin()`, `_ensure_audit_table()`, `ensure_knowledge_wiki_table()` 등 다수 동기 DB 호출이 to_thread 없이 실행, 이벤트 루프 블로킹. **영향도: 하** (부팅 1회성) / **리스크: 하**
3. `app/api/admin_group_api.py:194-199, 211-217` — N+1 (DB 감사와 동일 항목, 중복 확인됨). **영향도: 중 / 리스크: 중**
4. `app/api/admin_api.py:182-247` (`get_metrics`) — latency_1h/24h, p95_rows, slow, active_rows 5개 독립 쿼리가 순차 await, gather 미사용. **영향도: 하** (admin 대시보드 전용, 저빈도) / **리스크: 하** (서로 독립적이라 병렬화 용이)
5. `app/api/admin_api.py:201-205` — p95 계산이 최근 1시간 audit_logs를 LIMIT 없이 전부 가져와 Python에서 정렬. **영향도: 하** (현재 규모에선 미미) / **리스크: 하**
6. 인증/RBAC — `RequestLoggingMiddleware`(middleware.py:34-48)는 JWT 디코드만 하고 DB 조회 없음. 실제 role 확인은 `Depends(get_current_user)`(auth_middleware.py:41-69)에서 요청당 1회 DB JOIN — FastAPI가 동일 요청 내 Depends를 캐싱해 진짜 중복 조회는 없음. 다만 인증 필요한 모든 엔드포인트가 매 요청 DB 왕복 1회 발생, AD 캐시(TTL 300초)와 달리 별도 캐싱 없음. **영향도: 하~중** (스레드풀 실행이라 이벤트루프는 안 막지만 레이턴시 추가) / **리스크: 중** (권한 변경 즉시 반영 필요해 캐시 무효화 전략 필요)

위 6개 외 blocking sync I/O나 요청마다 반복되는 무거운 초기화는 발견되지 않음.

## 4. BigQuery/SQL 실행 경로

1. `app/agents/sql_agent.py:553-575` (`validate_sql_node`) + `query_verifier.py` 전체 — SQL 검증 통과 후 매번 `QueryVerifierAgent`(claude-sonnet-4-5)를 백그라운드 스레드로 fire-and-forget 호출. 결과(`vr.get("valid")`)는 로그로만 남고 실제 응답/캐시/재시도 로직에 전혀 반영되지 않음(소비처 없음 확인). **영향도: 중** (응답 지연에는 영향 없음, 하지만 SQL 요청 100%에 대해 추가 LLM 호출 비용 발생 — 순수 비용 낭비) / **리스크: 하** (제거하거나 valid=False 시 캐시 무효화 등 실제 액션에 연결)

2. BigQuery 결과 로딩 — **특이사항 없음**. `app/core/bigquery.py:64-68`의 `execute_query`는 RowIterator를 순회하며 `max_rows` 도달 시 즉시 break, 전체 결과셋을 메모리에 올리지 않음. `maximum_bytes_billed=10GB` 캡 + 파티션 필터로 스캔 비용 제어.

3. `app/agents/orchestrator.py:1741` (`_handle_multi`) — **`_enforce_partition_filter`를 완전히 우회하는 유일한 경로**. 이 함수(sql_agent.py:87)는 대형 테이블(SALES_ALL_Backup/integrated_ad/Integrated_marketing_cost) 쿼리에 날짜 필터가 없으면 재생성을 강제하는 안전장치인데, 실제 호출 지점은 `run_sql_agent`(1449줄)와 `run_sql_agent_stream`(1498줄) 두 곳뿐. `_handle_multi`(내부+외부 정보 결합 답변 라우트, 예: "환율 때문에 베트남 매출 하락?")는 컴파일된 LangGraph를 `_graph.invoke(state)`로 직접 호출하며 이 노드 체인에는 partition 필터 강제가 없음. **영향도: 상** (날짜 범위 모호한 질의가 90일 기본값 프롬프트 지시에만 의존 — LLM이 지시를 놓치면 대형 테이블 풀스캔 방지 안전망이 이 경로에만 없음, 정확히 `_enforce_partition_filter`가 막으려던 시나리오) / **리스크: 하** (`_bq_query_sync()` 안에서 `_graph.invoke` 결과의 `generated_sql`에 `_enforce_partition_filter`를 한 번 더 통과시키거나, `run_sql_agent`를 재사용하도록 교체. 단, 현재 `_graph.invoke`는 이미 execute까지 끝낸 answer 텍스트만 반환하는 구조라 사후 검증이 어색함 — 구조 변경 소요는 있으나 리스크 자체는 낮음)

## 우선순위 목록

영향도(상→중→하) 우선, 동일 영향도 내에서는 리스크(하→중→상) 순 정렬.

| # | 클러스터 | 파일:라인 | 문제 | 영향도 | 리스크 | 권장 조치 |
|---|---------|-----------|------|--------|--------|-----------|
| 1 | 에이전트 | `sql_agent.py:1001-1003` | 차트 타임아웃이 주석(3초)과 다르게 `300.0`으로 설정된 오타 — 차트 생성 지연 시 최대 5분 추가 대기 | 상 | 하 | `timeout=300.0` → `8.0`으로 수정 (스트리밍 버전과 동일하게) |
| 2 | BigQuery | `orchestrator.py:1741` (`_handle_multi`) | `_enforce_partition_filter`를 우회하는 유일한 경로 — 날짜 필터 없는 대형 테이블 풀스캔 안전망 부재 | 상 | 하 | `_bq_query_sync()`에서 `_graph.invoke` 결과의 `generated_sql`에 `_enforce_partition_filter` 재적용, 또는 `run_sql_agent` 재사용으로 교체 |
| 3 | BigQuery | `sql_agent.py:1490-1502` (`run_sql_agent_stream`) | generate_sql→validate→execute가 전부 동기 실행되며 그 사이 SSE 피드백 없음 — "source:bigquery" 표시 후 완전 침묵(수초~수십초) | 상 | 하 | 1492행(generate_sql 직전), 1502행(execute_sql 직전)에 상태 문자열 yield 추가 |
| 4 | 에이전트 | `routes.py:154-158`, `orchestrator.py` `route_and_stream:768-785` | direct 라우트가 대화 이력을 캡 없이 전체 전달 — 토큰 비용 O(n), 누적 비용 O(n²) | 상 | 중 | 오래된 메시지 요약 또는 최근 N개로 캡 (direct 라우트 한정) |
| 5 | 에이전트 | `sql_agent.py:789-790` | 0건 결과 안내에도 동일한 `timeout=300.0` — 의도된 "빠른 실패→템플릿 폴백"이 사실상 발동 안 함 | 중 | 하 | 짧은 타임아웃(3~8초)으로 수정 |
| 6 | 에이전트 | `sql_agent.py:1000` + 비스트리밍 `chat_completions` | `answer_future.result()`에 타임아웃 없음, 비스트리밍 엔드포인트엔 전체 타임아웃도 없어 Flash API 행 시 HTTP 요청 그대로 hang | 중 | 하 | `answer_future`에 타임아웃 추가, `route_and_execute()` 전체에 상한 타임아웃 추가 |
| 7 | API | `main.py:209` (`index()`) | "/" 라우트가 매 요청마다 chat.html을 동기 `.read_text()`로 읽어 이벤트 루프 블로킹 | 중 | 하 | `to_thread` 래핑 또는 서버 시작 시 1회 캐싱 |
| 8 | DB | `admin_group_api.py:194-199, 211-217` | 부서 배정/해제 시 사용자 수만큼 개별 INSERT/DELETE 루프 (N+1) | 중 | 하 | multi-row INSERT VALUES / `IN (...)` 배치 DELETE로 교체 |
| 9 | DB | `wiki_extractor.py:230-234, 257-261` | dup/conflict 체크가 `WHERE LOWER(entity)=LOWER(%s)` 사용 — 일반 인덱스는 함수 래핑 시 못 씀 → fact마다 풀스캔 | 중 | 하 | entity를 소문자로 normalize해 저장하거나 `entity_lower` 생성 컬럼 + 인덱스 추가 |
| 10 | API | `admin_api.py:182-247` (`get_metrics`) | 5개 독립 쿼리가 순차 await, gather 미사용 | 중 | 하 | `asyncio.gather`로 병렬화 |
| 11 | API | `admin_api.py:201-205` | p95 계산이 최근 1시간 audit_logs를 LIMIT 없이 전부 로드해 Python 정렬 | 하 | 하 | LIMIT 또는 DB 측 percentile 계산으로 대체 |
| 12 | API | `main.py:99-124` (lifespan) | 부팅 시 다수 동기 DB 호출이 to_thread 없이 실행 (1회성) | 하 | 하 | to_thread 래핑 (선택적, 부팅 1회라 우선순위 낮음) |
| 13 | 에이전트 | `orchestrator.py:1459-1493` (`_handle_bigquery`) | 1차 실패 시 전체 SQL 파이프라인 재실행 + fallback까지 최악 5~6회 LLM 호출 누적 | 중 | 중 | 복구율 유지하며 재시도 계층 축소 |
| 14 | DB | `wiki_extractor.py:225-296` (`_insert_facts_sync`) | fact마다 dup-check + conflict-check 쿼리 반복 (백그라운드 실행이라 사용자 체감 지연 없음, 테이블 성장에 따라 부하 누적) | 하~중 | 중 | 조건부 로직 재작성 필요한 배치화 |
| 15 | API | `auth_middleware.py:41-69` (`get_current_user`) | 인증 필요한 모든 엔드포인트가 매 요청 DB JOIN 1회 (진짜 중복은 아님, 캐싱 없음) | 하~중 | 중 | role 캐시 + 변경 시 무효화 전략 필요 |
| 16 | 에이전트 | `orchestrator.py:882-907` (CS/GWS/Notion/Team/Multi) | 핸들러 완료를 통째로 기다린 뒤 "가짜 스트리밍" — 최대 45~300초 무응답 가능 | 중 | 상 | 핸들러를 제너레이터로 전환 (시그니처 변경 필요, 범위 큼) |
| 17 | 에이전트 | `query_verifier.py` / `sql_agent.py:553-575` | 매 SQL 요청마다 결과가 어디에도 쓰이지 않는 LLM 검증 호출 (비용만 발생, 지연 없음) | 하 | 하 | 제거하거나 valid=False 시 캐시 무효화 등 실제 액션에 연결 |

**특이사항 없음으로 확인된 영역**: 커넥션 풀 설정, BigQuery 결과 스트리밍(RowIterator), multi 라우트의 웹검색+BQ 병렬화, format_answer의 답변+차트 병렬화, 조기 SSE source 피드백(이미 구현됨), RequestLoggingMiddleware(DB 조회 없음).

## Phase 2 완료 (2026-07-09)

1~9번 항목 수정 완료, `pm2 restart skin1004-prod`로 배포 완료 (restart_time=1, health check 200 OK, 크래시 없음).

- Task 2(`_handle_multi` 파티션 필터 우회 수정)는 `scripts/_test_handle_multi.py`로 실제 BigQuery+LLM 호출 검증 — 파티션 필터가 적용된 SQL 생성, 컨텍스트 연속성 유지 확인.
- 나머지 항목은 dev 서버(`skin1004-dev`) 재시작으로 부팅/헬스체크 정상 확인.
- `scripts/qa_team_150.py`(CS/IT/PEOPLE 위주) 전체 회귀는 생략 — 이번 변경 범위(BigQuery/multi/admin/frontend)와 주제가 겹치지 않아 비용 대비 실익이 낮다고 판단, 사용자 확인 후 스킵.

10~17번(중간 우선순위: `_handle_bigquery` 중복 LLM 재시도, wiki_extractor N+1 재작성, CS/GWS/Multi 가짜 스트리밍, 인증 캐싱 등)은 별도 Phase 3로 필요시 진행.

## Phase 3 진행 (2026-07-09)

- **#17 완료**: `query_verifier` 데드코드 제거 (orchestrator.py의 `self.query_verifier` 프로퍼티도 어디서도 호출되지 않음 확인 — sql_agent.py의 소비처 없는 fire-and-forget 호출 삭제)
- **#12 완료**: lifespan 부팅 시 동기 DB 호출 `to_thread` 래핑 (순서 보존을 위해 순차 유지)
- **#15 완료**: `get_current_user` DB 조회에 60초 TTL 캐시 추가 (기존 AD 캐시 300초 컨벤션과 동일 패턴, 사용자 수 362명 규모라 별도 eviction 불필요)
- **#11 스킵**: LIMIT 추가 시 p95/p99 계산에 필요한 꼬리값이 잘려나가는 정확도 버그를 유발 — 영향도 낮은 항목에 리스크가 더 커서 보류
- **#14 스킵**: fact 배열이 보통 소량(메시지당 수 개)인 배경 전용 루프. canonicalize→dup-check→INSERT(id 필요)→conflict-check가 서로 의존하는 조건부 체인이라 완전 배치화는 아키텍처 변경 수준 — 리스크 대비 실익 낮음
- **#13 스킵**: `_handle_bigquery`의 외부 재시도가 실제로 복구율에 얼마나 기여하는지 텔레메트리 없이는 안전하게 변경 판단 불가
- **#16 (CS/GWS/Multi 가짜 스트리밍)**: Phase 2 Task 3(로딩 인디케이터 버그 수정)로 대기 중 "검색 중..." 표시가 계속 보이게 되면서 체감 문제가 상당 부분 완화됨. 남은 개선(핸들러를 제너레이터로 전환)은 4개 핸들러 시그니처 변경이 필요한 큰 리팩터 — 별도 설계 논의 필요

배포: `pm2 restart skin1004-prod` (restart_time=2, health check 200 OK, 안정적)
