# 성능 최적화 감사 (Phase 1: 감사 & 베이스라인) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 챗봇 응답 속도(SQL 에이전트/오케스트레이터 경로)와 서버 리소스(메모리/CPU) 병목을 정적 코드 감사로 찾아내고, pm2 리소스 베이스라인을 기록해 우선순위가 매겨진 수정 후보 리스트를 만든다.

**Architecture:** 병렬 Explore 에이전트 4개(에이전트 경로/API·라우팅/DB 레이어/BigQuery·SQL 경로)를 동시에 투입해 각 클러스터의 구체적 병목 후보(파일:라인, 영향도, 리스크)를 수집한 뒤, 하나의 findings 문서로 종합한다. 이 계획은 감사만 다루며, 실제 수정(Phase 2)은 감사 결과가 나온 뒤 별도로 계획한다 — 어떤 항목을 고칠지는 지금 알 수 없기 때문이다.

**Tech Stack:** Explore agent(Agent tool), pm2 CLI, 기존 코드베이스(Python/FastAPI/LangGraph)

## Global Constraints

- 프로덕션(`skin1004-prod`, 포트 3000, PM2)은 실사용자가 쓰는 라이브 서버 — 이번 Phase 1에서는 코드를 변경하지 않는다(읽기 전용 감사).
- 배포가 필요해지는 Phase 2 이후에도 `pm2 restart`만 사용, `pm2 reload` 금지 (Windows fork 모드 고아 프로세스 위험, 2026-07-06 장애 이력).
- `skin1004-prod` kill/stop/delete 절대 금지.
- 회귀 검증은 `scripts/qa_team_150.py` 사용.
- 감사 산출물은 `docs/superpowers/plans/2026-07-09-performance-optimization-findings.md`에 누적 기록.

---

### Task 1: pm2 리소스 베이스라인 기록

**Files:**
- Create: `docs/superpowers/plans/2026-07-09-performance-optimization-findings.md`

**Interfaces:**
- Produces: findings 문서의 `## Baseline (측정: 2026-07-09)` 섹션 — Task 2~4가 이어서 같은 파일에 섹션을 추가함.

- [ ] **Step 1: pm2 상태 캡처**

Run: `pm2 jlist`
Expected: JSON 배열, `skin1004-prod` 항목에 `pm2_env.restart_time`, `monit.memory`, `monit.cpu` 필드 포함.

- [ ] **Step 2: 사람이 읽기 쉬운 상태도 함께 캡처**

Run: `pm2 status`
Expected: 테이블 출력, ↺(restart) 카운터와 메모리 컬럼 확인 가능.

- [ ] **Step 3: findings 문서 생성 및 베이스라인 기록**

```markdown
# 성능 최적화 감사 결과 (2026-07-09)

## Baseline (측정: 2026-07-09)

- pm2 restart_time (skin1004-prod): <Step 1 결과값>
- pm2 memory (skin1004-prod): <Step 1 결과값 MB>
- pm2 cpu (skin1004-prod): <Step 1 결과값 %>
- `pm2 status` 원본 출력:
\`\`\`
<Step 2 출력 붙여넣기>
\`\`\`
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-07-09-performance-optimization-findings.md
git commit -m "docs(perf): record pm2 baseline before optimization audit"
```

---

### Task 2: 클러스터별 병렬 정적 감사

**Files:**
- Modify: `docs/superpowers/plans/2026-07-09-performance-optimization-findings.md` (감사 섹션 4개 추가)

**Interfaces:**
- Consumes: 없음 (Task 1과 독립적으로 실행 가능하나, 문서에는 이어서 추가)
- Produces: findings 문서에 `## 1. 에이전트 경로`, `## 2. API/라우팅`, `## 3. DB 레이어`, `## 4. BigQuery/SQL 실행 경로` 4개 섹션. 각 섹션은 항목당 `파일:라인 — 문제 설명 — 예상 영향도(상/중/하) — 수정 리스크(상/중/하)` 형식의 불릿 리스트.

- [ ] **Step 1: 4개 Explore 에이전트를 하나의 메시지에서 병렬로 디스패치**

각 에이전트에게 아래 프롬프트를 전달 (파일 경로와 확인 항목을 정확히 명시):

에이전트 A (에이전트 경로):
```
app/agents/orchestrator.py, app/agents/sql_agent.py, app/agents/cs_agent.py,
app/agents/gws_agent.py, app/agents/query_verifier.py 를 읽고 성능 병목
후보를 찾아라. 확인할 것:
1. 순차 실행되는 LLM 호출 중 병렬화 가능한 것 (asyncio.gather로 묶을 수
   있는데 순차 await로 되어있는 경우)
2. 같은 목적의 LLM 호출이 중복 발생하는 곳 (예: 검증을 위해 같은 내용을
   두 번 묻는 경우)
3. 대화 이력(messages)이 잘리지 않고 무한정 누적되는 경로가 있는지
4. app/api/routes.py 와 app/agents/orchestrator.py 에 status_callback 같은
   조기 피드백 메커니즘이 있는지 확인하고, 없다면 어디에 추가하면 되는지
   구체적 삽입 지점(파일:라인)을 짚어라

각 발견 항목을 "파일:라인 — 문제 설명 — 예상 영향도(상/중/하) — 수정
리스크(상/중/하)" 형식으로 정리해서 보고하라. 코드는 수정하지 말고 읽기만
하라.
```

에이전트 B (API/라우팅):
```
app/api/routes.py, app/main.py, app/api/admin_api.py,
app/api/admin_group_api.py, app/api/auth_api.py, app/api/conversation_api.py
를 읽고 성능 병목 후보를 찾아라. 확인할 것:
1. async def 핸들러 안에서 동기(blocking) I/O 호출(예: requests 라이브러리,
   동기 DB 드라이버 호출, time.sleep)이 이벤트 루프를 막고 있는지
2. 매 요청마다 반복되는 무거운 초기화(모델 로드, 커넥션 생성 등)가 있는지
3. 인증/RBAC 체크가 요청마다 중복 DB 조회를 발생시키는지

각 발견 항목을 "파일:라인 — 문제 설명 — 예상 영향도(상/중/하) — 수정
리스크(상/중/하)" 형식으로 정리해서 보고하라. 코드는 수정하지 말고 읽기만
하라.
```

에이전트 C (DB 레이어):
```
app/db/mariadb.py 및 관련 스키마/모델 파일을 읽고 성능 병목 후보를 찾아라.
확인할 것:
1. 커넥션 풀 설정(pool_size, max_overflow 등)이 있는지, 매 요청마다 새
   커넥션을 만드는지
2. N+1 쿼리 패턴 (루프 안에서 개별 쿼리를 반복 실행하는 코드)
3. 자주 호출되는 쿼리인데 인덱스가 없을 것으로 보이는 WHERE/JOIN 컬럼

각 발견 항목을 "파일:라인 — 문제 설명 — 예상 영향도(상/중/하) — 수정
리스크(상/중/하)" 형식으로 정리해서 보고하라. 코드는 수정하지 말고 읽기만
하라.
```

에이전트 D (BigQuery/SQL 실행 경로):
```
app/agents/sql_agent.py, app/agents/query_verifier.py, prompts/sql_generator.txt
를 읽고 성능 병목 후보를 찾아라. 확인할 것:
1. SQL 생성→검증→실행 단계에서 불필요하거나 중복된 LLM 호출이 있는지
2. BigQuery 결과셋을 전체 로드하는지, 아니면 필요한 만큼만 가져오는지
3. 기존에 구현된 _enforce_partition_filter (파티션 필터 강제)가 실제로
   모든 SQL 생성 경로에서 호출되는지, 빠진 경로가 있는지

각 발견 항목을 "파일:라인 — 문제 설명 — 예상 영향도(상/중/하) — 수정
리스크(상/중/하)" 형식으로 정리해서 보고하라. 코드는 수정하지 말고 읽기만
하라.
```

- [ ] **Step 2: 각 에이전트 응답 검증**

Expected: 각 응답이 최소 1개 이상의 "파일:라인 — 문제 설명 — 영향도 —
리스크" 형식 항목을 포함. 항목이 없거나 "특별한 문제 없음"으로만 끝난
클러스터는 그대로 "특이사항 없음"으로 기록 (억지로 문제를 만들어내지
않는다).

- [ ] **Step 3: findings 문서에 4개 섹션 추가**

각 에이전트의 원문 보고 내용을 해당 섹션(`## 1. 에이전트 경로` 등)에
그대로 붙여넣는다.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-07-09-performance-optimization-findings.md
git commit -m "docs(perf): add cluster audit findings (agents/api/db/bigquery)"
```

---

### Task 3: 우선순위 종합 및 다음 단계 제안

**Files:**
- Modify: `docs/superpowers/plans/2026-07-09-performance-optimization-findings.md` (`## 우선순위 목록` 섹션 추가)

**Interfaces:**
- Consumes: Task 2에서 기록된 4개 섹션의 모든 발견 항목
- Produces: `## 우선순위 목록` — 영향도×리스크로 정렬된 테이블, Phase 2(수정) 계획 수립 시 이 테이블을 그대로 입력으로 사용

- [ ] **Step 1: 전체 발견 항목을 하나의 표로 취합**

```markdown
## 우선순위 목록

| # | 클러스터 | 파일:라인 | 문제 | 영향도 | 리스크 | 권장 조치 |
|---|---------|-----------|------|--------|--------|-----------|
| 1 | ... | ... | ... | 상 | 하 | ... |
```

영향도(상) × 리스크(하) 조합을 표 최상단에 오도록 정렬한다.

- [ ] **Step 2: findings 문서 마무리 커밋**

```bash
git add docs/superpowers/plans/2026-07-09-performance-optimization-findings.md
git commit -m "docs(perf): finalize prioritized findings from performance audit"
```

- [ ] **Step 3: 사용자에게 결과 요약 및 Phase 2 방향 확인**

우선순위 1~3위 항목을 텍스트로 요약해 사용자에게 제시하고, 어떤 항목부터
수정할지, 수정 계획(Phase 2)을 새로 작성할지(1~2개 파일짜리 사소한 수정은
바로 진행 가능) 확인받는다.

---

## Phase 2 안내 (이 계획의 범위 밖)

Task 3에서 나온 우선순위 목록에 따라 실제 수정을 진행한다. 수정 대상이
확정되기 전까지는 구체적 TDD 단계를 미리 쓸 수 없으므로(스펙에서도 "감사
결과에 따라 우선순위 결정" 명시), Phase 2는 감사 완료 후 별도
plan(`docs/superpowers/plans/YYYY-MM-DD-performance-fixes-phaseN.md`)으로
작성한다. 1~2파일짜리 사소한 수정은 별도 계획 없이 바로 진행할 수 있다.
