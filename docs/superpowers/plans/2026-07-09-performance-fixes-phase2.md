# 성능 최적화 Phase 2 (상위 10개 수정) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/plans/2026-07-09-performance-optimization-findings.md`의 우선순위 목록 1~10번 항목을 수정한다.

**Architecture:** 각 항목은 독립적인 파일:라인 단위 수정이며 서로 의존성이 없다. 순서대로 하나씩 적용하고, 항목별로 가능한 검증(회귀 스크립트/코드 추론)을 거친 뒤 마지막에 한 번에 `pm2 restart skin1004-prod`로 배포한다.

**Tech Stack:** 기존 FastAPI/LangGraph 백엔드, 순수 JS 프론트엔드(chat.js)

## Global Constraints

- `skin1004-prod`는 실사용자가 쓰는 라이브 서버 — 배포는 모든 항목 수정 완료 후 **한 번의** `pm2 restart skin1004-prod`로 반영 (reload 금지).
- chat.js를 수정하는 항목(#3)은 `chat.html`의 `?v=` 캐시 버전을 반드시 올린다 (현재 `chat.js?v=222` → `223`).
- 회귀 검증: `scripts/qa_team_150.py` (BigQuery/SQL 답변 정확도 훼손 여부 확인).
- 각 수정은 findings 문서(2026-07-09-performance-optimization-findings.md)의 근거를 그대로 따른다 — 새로운 범위 확장 금지.

---

### Task 1: 차트/0건 결과 타임아웃 오타 수정 (findings #1, #5)

**Files:**
- Modify: `app/agents/sql_agent.py:790` (0건 결과), `app/agents/sql_agent.py:1003` (차트)

**Interfaces:** 없음 (상수값만 변경, 다른 태스크와 독립)

- [ ] **Step 1: 0건 결과 타임아웃 수정**

`app/agents/sql_agent.py:790`을 다음과 같이 변경:
```python
                answer = f.result(timeout=8.0)
```
(기존: `answer = f.result(timeout=300.0)`)

- [ ] **Step 2: 차트 타임아웃 수정 (주석과 일치시킴)**

`app/agents/sql_agent.py:1001-1003`을 다음과 같이 변경:
```python
            # Give chart up to 8s after answer is ready; skip if slow
            try:
                chart_markdown = chart_future.result(timeout=8.0)
```
(기존: 주석 "3s", 코드 `timeout=300.0`. 스트리밍 버전 `run_sql_agent_stream`의 `timeout=8.0`과 통일)

- [ ] **Step 3: 문법 확인**

Run: `python -c "import ast; ast.parse(open('app/agents/sql_agent.py', encoding='utf-8').read())"`
Expected: 에러 없음 (SyntaxError 없으면 통과)

- [ ] **Step 4: Commit**

```bash
git add app/agents/sql_agent.py
git commit -m "fix(perf): correct chart/no-result timeout from 300s to 8s"
```

---

### Task 2: `_handle_multi`가 파티션 필터를 우회하는 문제 수정 (findings #2)

**Files:**
- Modify: `app/agents/orchestrator.py:1731-1754` (`_bq_query_sync` 함수 및 `asyncio.gather` 호출부)

**Interfaces:**
- Consumes: `app.agents.sql_agent.run_sql_agent(query, conversation_context="", model_type=MODEL_GEMINI, brand_filter=None, enabled_sources=None) -> str` (이미 `_enforce_partition_filter`를 내부에서 호출하는 기존 함수)

현재 코드(`_bq_query_sync`)는 `sql_agent` 컴파일된 LangGraph를 `_graph.invoke(state)`로 직접 호출하는데, 이 경로는 `generate_sql → validate_sql → (execute_sql|format_answer) → format_answer`로 구성되어 있고(1276-1298행 확인됨, 별도 재시도 루프 없음) `run_sql_agent()`의 수동 시퀀스(`generate_sql → validate_sql_node → _enforce_partition_filter → execute_sql → format_answer`)와 동일한 노드 순서를 거치되 파티션 필터만 빠져있다. 즉 `run_sql_agent()`로 교체해도 동작은 동일하고 파티션 필터만 추가된다.

- [ ] **Step 1: `_bq_query_sync`를 async 함수로 교체**

`app/agents/orchestrator.py:1731-1754`의 다음 블록:
```python
        def _bq_query_sync():
            # Maintenance: only hard-block on manual maintenance
            from app.core.safety import get_maintenance_manager
            mm = get_maintenance_manager()
            if mm.active and mm.manual:
                return "", "데이터 점검 중으로 매출 데이터 조회가 일시 중단되었습니다."

            flash = get_flash_client()
            data_query = flash.generate(data_query_prompt, temperature=0.0).strip()
            logger.info("multi_data_query_rewritten", original=query[:100], rewritten=data_query[:100])
            from app.agents.sql_agent import sql_agent as _graph
            state = {
                "query": data_query,
                "route_type": "text_to_sql",
                "generated_sql": None, "sql_valid": None, "sql_result": None,
                "retrieved_docs": None, "doc_relevance": None, "web_search_results": None,
                "answer": "", "needs_retry": False, "retry_count": 0, "error": None,
                "messages": None,
                "conversation_context": conversation_context,
                "model_type": model_type,
                "brand_filter": brand_filter,
            }
            result = _graph.invoke(state)
            return data_query, result.get("answer", "")
```
를 다음으로 교체:
```python
        async def _bq_query_async():
            # Maintenance: only hard-block on manual maintenance
            from app.core.safety import get_maintenance_manager
            mm = get_maintenance_manager()
            if mm.active and mm.manual:
                return "", "데이터 점검 중으로 매출 데이터 조회가 일시 중단되었습니다."

            flash = get_flash_client()
            data_query = await asyncio.to_thread(flash.generate, data_query_prompt, temperature=0.0)
            data_query = data_query.strip()
            logger.info("multi_data_query_rewritten", original=query[:100], rewritten=data_query[:100])
            from app.agents.sql_agent import run_sql_agent
            answer = await run_sql_agent(
                data_query,
                conversation_context=conversation_context,
                model_type=model_type,
                brand_filter=brand_filter,
            )
            return data_query, answer
```

- [ ] **Step 2: `asyncio.gather` 호출부 수정**

`app/agents/orchestrator.py:1759-1764`의:
```python
        try:
            gathered = await asyncio.gather(
                asyncio.to_thread(_web_search_sync),
                asyncio.to_thread(_bq_query_sync),
                return_exceptions=True,
            )
```
를:
```python
        try:
            gathered = await asyncio.gather(
                asyncio.to_thread(_web_search_sync),
                _bq_query_async(),
                return_exceptions=True,
            )
```
로 변경.

- [ ] **Step 3: 문법 확인**

Run: `python -c "import ast; ast.parse(open('app/agents/orchestrator.py', encoding='utf-8').read())"`
Expected: 에러 없음

- [ ] **Step 4: Commit**

```bash
git add app/agents/orchestrator.py
git commit -m "fix(perf): route _handle_multi BQ query through run_sql_agent to enforce partition filter"
```

---

### Task 3: BigQuery 스트리밍 중 로딩 인디케이터가 즉시 사라지는 프론트엔드 버그 수정 (findings #3)

**Files:**
- Modify: `app/frontend/chat.js` (SSE 스트림 파싱 루프, `source:` 마커 처리부)
- Modify: `app/frontend/chat.html:283` (캐시 버전 `?v=222` → `?v=223`)

**Interfaces:** 없음 (프론트엔드 렌더링 로직만 변경)

**배경**: `<!-- source:bigquery -->` 마커가 도착하면 `typingEl.innerHTML`에 "📊 데이터 조회 중..." 텍스트를 설정하지만, 바로 다음 줄에서 무조건 `typing.remove()`가 실행되어(같은 동기 처리 루프 안, 브라우저 리페인트 전) 실제로는 화면에 렌더링될 새도 없이 사라진다. BigQuery는 이 마커 이후 실제 답변 텍스트가 오기까지 수초~수십초가 걸리는데 그 사이 로딩 표시가 전혀 없다. 이 버그는 bigquery/notion/cs/gws/multi 모든 라우트의 로딩 메시지에 공통으로 영향을 준다.

- [ ] **Step 1: 실제 콘텐츠가 push될 때만 typing indicator를 제거하도록 수정**

`app/frontend/chat.js`에서 (약 1761-1796행) 다음 블록:
```javascript
              var srcMatch = delta.content.match(/<!-- source:([\w:+\s-￿]+?) -->/);
              if (srcMatch) {
                var srcParts = srcMatch[1].split(":");
                detectedSource = srcParts[0];
                if (srcParts[1]) detectedSourceLabel = srcParts[1];
                // Route-specific loading message
                var loadingMsgs = {
                  bigquery: "📊 데이터 조회 중...",
                  notion: "📋 Notion 문서 검색 중...",
                  cs: "🧴 CS Q&A 검색 중...",
                  gws: "📧 Google Workspace 확인 중...",
                  multi: "📈 종합 분석 중...",
                };
                var typingEl = aiMsgEl.querySelector(".typing-indicator");
                if (typingEl && loadingMsgs[detectedSource]) {
                  typingEl.innerHTML = '<span class="loading-text">' + loadingMsgs[detectedSource] + '</span>';
                }
                var stripped = delta.content.replace(/<!-- source:[\w:+\s-￿]+? -->/, "");
                if (stripped) _S.queue.push(stripped);
              } else {
                // Filter out thinking/reasoning patterns from Claude
                var text = delta.content;
                // Skip lines that look like internal thinking
                if (/^(The user|I should|I need to|Let me|I'll |I can|I don't|Actually|Wait|Hmm)/i.test(text.trim())) {
                  continue;
                }
                // Strip thinking blocks
                text = text.replace(/<thinking>[\s\S]*?<\/thinking>/g, "");
                text = text.replace(/\[thinking\][\s\S]*?\[\/thinking\]/g, "");
                if (text) _S.queue.push(text);
              }
              // Start token drain animation if not running
              var typing = aiMsgEl.querySelector(".typing-indicator");
              if (typing) typing.remove();
              if (!_S.running) _startTokenDrain(contentEl);
```
를 다음으로 교체 (`pushedContent` 플래그 추가, typing 제거를 실제 콘텐츠가 있을 때로 제한):
```javascript
              var pushedContent = false;
              var srcMatch = delta.content.match(/<!-- source:([\w:+\s-￿]+?) -->/);
              if (srcMatch) {
                var srcParts = srcMatch[1].split(":");
                detectedSource = srcParts[0];
                if (srcParts[1]) detectedSourceLabel = srcParts[1];
                // Route-specific loading message
                var loadingMsgs = {
                  bigquery: "📊 데이터 조회 중...",
                  notion: "📋 Notion 문서 검색 중...",
                  cs: "🧴 CS Q&A 검색 중...",
                  gws: "📧 Google Workspace 확인 중...",
                  multi: "📈 종합 분석 중...",
                };
                var typingEl = aiMsgEl.querySelector(".typing-indicator");
                if (typingEl && loadingMsgs[detectedSource]) {
                  typingEl.innerHTML = '<span class="loading-text">' + loadingMsgs[detectedSource] + '</span>';
                }
                var stripped = delta.content.replace(/<!-- source:[\w:+\s-￿]+? -->/, "");
                if (stripped) { _S.queue.push(stripped); pushedContent = true; }
              } else {
                // Filter out thinking/reasoning patterns from Claude
                var text = delta.content;
                // Skip lines that look like internal thinking
                if (/^(The user|I should|I need to|Let me|I'll |I can|I don't|Actually|Wait|Hmm)/i.test(text.trim())) {
                  continue;
                }
                // Strip thinking blocks
                text = text.replace(/<thinking>[\s\S]*?<\/thinking>/g, "");
                text = text.replace(/\[thinking\][\s\S]*?\[\/thinking\]/g, "");
                if (text) { _S.queue.push(text); pushedContent = true; }
              }
              // Start token drain animation only once real content arrives —
              // keeps the route-specific loading indicator visible during
              // the silent SQL-generation/execution window instead of it
              // being removed on the same tick it was set.
              if (pushedContent) {
                var typing = aiMsgEl.querySelector(".typing-indicator");
                if (typing) typing.remove();
                if (!_S.running) _startTokenDrain(contentEl);
              }
```

- [ ] **Step 2: 캐시 버전 올리기**

`app/frontend/chat.html:283`을:
```html
  <script src="/frontend/chat.js?v=223"></script>
```
로 변경 (기존 `?v=222`).

- [ ] **Step 3: 문법 확인**

Run: `node --check app/frontend/chat.js`
Expected: 에러 없음 출력 (Node.js가 설치되어 있지 않다면 브라우저에서 직접 로드해 콘솔 에러 없음을 확인)

- [ ] **Step 4: Commit**

```bash
git add app/frontend/chat.js app/frontend/chat.html
git commit -m "fix(perf): keep route loading indicator visible until real content arrives"
```

---

### Task 4: direct 라우트 대화 이력 무제한 누적 캡 추가 (findings #4)

**Files:**
- Modify: `app/agents/orchestrator.py:64-77` (`_clean_messages_for_history`)

**Interfaces:**
- Consumes: 없음 (기존 함수 시그니처 `_clean_messages_for_history(messages: List[Dict]) -> List[Dict]` 유지)
- Produces: 최근 30개 메시지로 캡된 리스트 (direct 라우트 전용, 다른 라우트의 `_build_conversation_context`는 이미 20개로 캡되어 있어 영향 없음)

**설계 근거**: 스펙(2026-07-09-performance-optimization-audit-design.md)의 오케스트레이터 컨텍스트 규칙 — "아까/그거/방금" 같은 참조형 질문 처리를 위해 이력을 완전히 끊으면 안 됨. 따라서 완전 무제한 대신 최근 30개(15턴)로 캡 — 참조형 질문이 보통 직전 몇 턴 안에서 발생하므로 안전 마진을 넉넉히 둠.

- [ ] **Step 1: 캡 로직 추가**

`app/agents/orchestrator.py:64-77`의:
```python
def _clean_messages_for_history(messages: List[Dict]) -> List[Dict]:
    """Strip chart/SQL noise from assistant messages before sending to LLM history.

    Unlike _build_conversation_context, this does NOT truncate — full cleaned text
    is preserved so Claude can track conversation accurately.
    """
    cleaned = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("assistant", "model") and isinstance(content, str):
            content = _strip_assistant_noise(content)
        cleaned.append({**msg, "content": content})
    return cleaned
```
를:
```python
_DIRECT_HISTORY_CAP = 30  # 최근 15턴 — 참조형 질문("아까 그거") 안전 마진


def _clean_messages_for_history(messages: List[Dict]) -> List[Dict]:
    """Strip chart/SQL noise from assistant messages before sending to LLM history.

    Caps to the most recent _DIRECT_HISTORY_CAP messages to bound per-turn
    token cost on long sessions; full text (no 1500-char truncation) is kept
    for messages within the cap so Claude can still track conversation
    accurately within that window.
    """
    capped = messages[-_DIRECT_HISTORY_CAP:] if len(messages) > _DIRECT_HISTORY_CAP else messages
    cleaned = []
    for msg in capped:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("assistant", "model") and isinstance(content, str):
            content = _strip_assistant_noise(content)
        cleaned.append({**msg, "content": content})
    return cleaned
```

- [ ] **Step 2: 문법 확인**

Run: `python -c "import ast; ast.parse(open('app/agents/orchestrator.py', encoding='utf-8').read())"`
Expected: 에러 없음

- [ ] **Step 3: Commit**

```bash
git add app/agents/orchestrator.py
git commit -m "fix(perf): cap direct-route conversation history to bound per-turn token cost"
```

---

### Task 5: 비스트리밍 답변 생성에 타임아웃 추가 (findings #6)

**Files:**
- Modify: `app/agents/sql_agent.py:994, 1000` (`answer_future`)

**Interfaces:** 없음

- [ ] **Step 1: `answer_future.result()`에 타임아웃 추가**

`app/agents/sql_agent.py:1000`의:
```python
            answer = answer_future.result()
```
를:
```python
            answer = answer_future.result(timeout=120.0)
```
로 변경 (Flash 답변 생성이 2분 내 완료되지 않으면 TimeoutError를 던져 상위 `except`가 처리하도록 함 — 기존 `try/except` 블록이 이미 990행부터 감싸고 있으므로 별도 예외 처리 추가 불필요, 다만 `except concurrent.futures.TimeoutError`가 answer_future의 타임아웃도 함께 처리하도록 아래 Step 2에서 확인).

- [ ] **Step 2: 예외 처리 범위 확인**

`app/agents/sql_agent.py:990-1006` 주변의 `try/except` 블록이 `TimeoutError`를 캐치하는지 Read로 확인. 캐치하지 않는 광범위 `except Exception`이 있다면 그대로 두고, 없다면 `except concurrent.futures.TimeoutError:` 절을 추가해 폴백 답변을 반환하도록 한다. (정확한 처리는 파일의 현재 예외 구조를 그대로 따르며, 새로운 폴백 템플릿을 발명하지 않는다 — 기존 상위 함수의 일반 예외 처리에 위임)

- [ ] **Step 3: 문법 확인**

Run: `python -c "import ast; ast.parse(open('app/agents/sql_agent.py', encoding='utf-8').read())"`
Expected: 에러 없음

- [ ] **Step 4: Commit**

```bash
git add app/agents/sql_agent.py
git commit -m "fix(perf): add timeout to non-streaming answer generation to prevent request hangs"
```

---

### Task 6: `main.py` "/" 라우트의 동기 파일 읽기를 부팅 시 캐싱으로 교체 (findings #7)

**Files:**
- Modify: `app/main.py` (모듈 레벨 캐시 변수 추가 + `index()` 핸들러)

**Interfaces:** 없음

- [ ] **Step 1: chat.html을 부팅 시 1회 읽어 캐싱**

`app/main.py:202-210`의:
```python
    @app.get("/")
    async def index(request: Request):
        # Check if user is authenticated
        token = request.cookies.get("token")
        if not token:
            return RedirectResponse(url="/login", status_code=302)
        from fastapi.responses import HTMLResponse
        html = (_FRONTEND_DIR / "chat.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})
```
를:
```python
    _CHAT_HTML_CACHE = (_FRONTEND_DIR / "chat.html").read_text(encoding="utf-8")

    @app.get("/")
    async def index(request: Request):
        # Check if user is authenticated
        token = request.cookies.get("token")
        if not token:
            return RedirectResponse(url="/login", status_code=302)
        from fastapi.responses import HTMLResponse
        return HTMLResponse(_CHAT_HTML_CACHE, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})
```

주의: `_CHAT_HTML_CACHE`는 서버 프로세스 시작 시 1회만 읽으므로, chat.html을 수정한 뒤에는 `pm2 restart skin1004-prod`가 반드시 필요하다 (기존에도 배포 시 restart를 하므로 실무 절차 변화 없음).

- [ ] **Step 2: 문법 확인**

Run: `python -c "import ast; ast.parse(open('app/main.py', encoding='utf-8').read())"`
Expected: 에러 없음

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "fix(perf): cache chat.html at startup instead of reading it on every request"
```

---

### Task 7: admin_group_api.py N+1 INSERT/DELETE를 배치 쿼리로 교체 (findings #8)

**Files:**
- Modify: `app/api/admin_group_api.py:193-199` (`assign_users_to_group`), `app/api/admin_group_api.py:211-217` (`remove_users_from_group`)

**Interfaces:** 없음 (반환값 `added`/`removed` 카운트 의미 동일하게 유지)

- [ ] **Step 1: INSERT 배치화**

`app/api/admin_group_api.py:192-199`의:
```python
    # Batch insert new assignments
    added = 0
    for uid in new_ids:
        await _execute(
            "INSERT INTO user_groups (ad_user_id, group_id) VALUES (%s, %s)",
            (uid, group_id),
        )
        added += 1
```
를:
```python
    # Batch insert new assignments
    added = 0
    if new_ids:
        values_sql = ",".join(["(%s, %s)"] * len(new_ids))
        params = tuple(p for uid in new_ids for p in (uid, group_id))
        await _execute(
            f"INSERT INTO user_groups (ad_user_id, group_id) VALUES {values_sql}",
            params,
        )
        added = len(new_ids)
```

- [ ] **Step 2: DELETE 배치화**

`app/api/admin_group_api.py:210-217`의:
```python
    """Remove AD users from a group."""
    removed = 0
    for uid in req.ad_user_ids:
        r = await _execute(
            "DELETE FROM user_groups WHERE ad_user_id = %s AND group_id = %s",
            (uid, group_id),
        )
        removed += r
```
를:
```python
    """Remove AD users from a group."""
    removed = 0
    if req.ad_user_ids:
        placeholders = ",".join(["%s"] * len(req.ad_user_ids))
        removed = await _execute(
            f"DELETE FROM user_groups WHERE group_id = %s AND ad_user_id IN ({placeholders})",
            (group_id, *req.ad_user_ids),
        )
```

- [ ] **Step 3: `_execute`의 반환값이 영향받은 행 수(int)인지 확인**

`app/api/admin_group_api.py`에서 `_execute`가 import되는 위치를 확인하고, 정의부(`app/db/mariadb.py`의 `execute` 함수)가 `cursor.rowcount` 또는 그에 준하는 int를 반환하는지 Read로 확인한다. int를 반환하지 않는다면 Step 2의 `removed = await _execute(...)`를 `await _execute(...); removed = len(req.ad_user_ids)`로 조정한다 (배치 DELETE는 이미 존재하지 않는 조합도 안전하게 무시하므로 근사값으로 충분).

- [ ] **Step 4: 문법 확인**

Run: `python -c "import ast; ast.parse(open('app/api/admin_group_api.py', encoding='utf-8').read())"`
Expected: 에러 없음

- [ ] **Step 5: Commit**

```bash
git add app/api/admin_group_api.py
git commit -m "fix(perf): batch group member insert/delete to eliminate N+1 queries"
```

---

### Task 8: wiki_extractor.py의 `LOWER(entity)` 인덱스 우회 제거 (findings #9)

**Files:**
- Modify: `app/knowledge/wiki_extractor.py:101, 232, 260`

**배경**: `knowledge_wiki` 테이블은 `COLLATE=utf8mb4_unicode_ci`(대소문자 구분 없는 콜레이션)로 정의되어 있어(`app/db/mariadb.py:172`), `entity = %s` 비교가 이미 대소문자 구분 없이 동작하고 `idx_entity` 인덱스도 그대로 사용된다. `LOWER(entity) = LOWER(%s)`로 감싸는 것은 불필요하며 MariaDB가 함수로 감싸진 컬럼에는 일반 인덱스를 쓰지 못해 매번 풀스캔을 유발한다. `LOWER()`를 제거하는 것만으로 동일한 매칭 동작을 유지하면서 인덱스를 되살릴 수 있다.

- [ ] **Step 1: `_canonicalize_entity_sync`에서 `LOWER()` 제거**

`app/knowledge/wiki_extractor.py:100-103`의:
```python
        rows = fetch_all(
            "SELECT entity FROM knowledge_wiki WHERE LOWER(entity) = LOWER(%s) LIMIT 1",
            (entity,),
        )
```
를:
```python
        rows = fetch_all(
            "SELECT entity FROM knowledge_wiki WHERE entity = %s LIMIT 1",
            (entity,),
        )
```
로 변경 (컬럼이 `utf8mb4_unicode_ci`라 대소문자 무관 매칭은 그대로 유지됨).

- [ ] **Step 2: `_insert_facts_sync`의 dup-check에서 `LOWER()` 제거**

`app/knowledge/wiki_extractor.py:230-234`의:
```python
                dup = fetch_all(
                    "SELECT id FROM knowledge_wiki "
                    "WHERE LOWER(entity) = LOWER(%s) AND period = %s "
                    "  AND metric = %s AND value = %s AND status <> 'archived' LIMIT 1",
                    (canonical_entity, f.period, f.metric, f.value),
                )
```
를:
```python
                dup = fetch_all(
                    "SELECT id FROM knowledge_wiki "
                    "WHERE entity = %s AND period = %s "
                    "  AND metric = %s AND value = %s AND status <> 'archived' LIMIT 1",
                    (canonical_entity, f.period, f.metric, f.value),
                )
```

- [ ] **Step 3: `_flag_conflict_sync`의 sibling 조회에서 `LOWER()` 제거**

`app/knowledge/wiki_extractor.py:257-263`의:
```python
        siblings = fetch_all(
            """
            SELECT id, value FROM knowledge_wiki
            WHERE LOWER(entity) = LOWER(%s) AND period = %s AND metric = %s
              AND id <> %s AND status <> 'archived'
            LIMIT 5
            """,
            (entity, period, metric, new_id),
        )
```
를:
```python
        siblings = fetch_all(
            """
            SELECT id, value FROM knowledge_wiki
            WHERE entity = %s AND period = %s AND metric = %s
              AND id <> %s AND status <> 'archived'
            LIMIT 5
            """,
            (entity, period, metric, new_id),
        )
```

- [ ] **Step 4: 문법 확인**

Run: `python -c "import ast; ast.parse(open('app/knowledge/wiki_extractor.py', encoding='utf-8').read())"`
Expected: 에러 없음

- [ ] **Step 5: Commit**

```bash
git add app/knowledge/wiki_extractor.py
git commit -m "fix(perf): drop LOWER() wrapping on knowledge_wiki entity lookups to restore index usage"
```

---

### Task 9: admin_api.py get_metrics의 5개 순차 쿼리를 병렬화 (findings #10)

**Files:**
- Modify: `app/api/admin_api.py:181-247`

**Interfaces:** 없음 (반환 딕셔너리 구조 동일하게 유지)

- [ ] **Step 1: 4개의 독립 `_db_fetch_all` 호출을 `asyncio.gather`로 병렬화**

`app/api/admin_api.py:181-221`의 순차 호출:
```python
    # Latency — last 1h and last 24h
    latency_1h = await _db_fetch_all("""
        SELECT route,
               COUNT(*) AS cnt,
               AVG(total_ms) AS avg_ms,
               MAX(total_ms) AS max_ms
        FROM audit_logs
        WHERE created_at >= NOW() - INTERVAL 1 HOUR
        GROUP BY route
        ORDER BY cnt DESC
    """)
    latency_24h = await _db_fetch_all("""
        SELECT COUNT(*) AS cnt,
               AVG(total_ms) AS avg_ms,
               MAX(total_ms) AS max_ms
        FROM audit_logs
        WHERE created_at >= NOW() - INTERVAL 24 HOUR
    """)

    # p95 — compute in Python (MariaDB 10.x lacks PERCENTILE_CONT)
    p95_rows = await _db_fetch_all("""
        SELECT total_ms FROM audit_logs
        WHERE created_at >= NOW() - INTERVAL 1 HOUR AND total_ms IS NOT NULL
        ORDER BY total_ms
    """)
    samples = [int(r["total_ms"]) for r in p95_rows if r["total_ms"] is not None]
    if samples:
        p50 = samples[len(samples) // 2]
        p95 = samples[int(len(samples) * 0.95)]
        p99 = samples[int(len(samples) * 0.99)]
    else:
        p50 = p95 = p99 = 0

    # Top slow queries (last 1h)
    slow = await _db_fetch_all("""
        SELECT user_email, route, query, total_ms, created_at
        FROM audit_logs
        WHERE created_at >= NOW() - INTERVAL 1 HOUR
        ORDER BY total_ms DESC
        LIMIT 10
    """)
```
를:
```python
    # Latency/p95/slow-query queries are independent — run them concurrently.
    latency_1h, latency_24h, p95_rows, slow = await asyncio.gather(
        _db_fetch_all("""
            SELECT route,
                   COUNT(*) AS cnt,
                   AVG(total_ms) AS avg_ms,
                   MAX(total_ms) AS max_ms
            FROM audit_logs
            WHERE created_at >= NOW() - INTERVAL 1 HOUR
            GROUP BY route
            ORDER BY cnt DESC
        """),
        _db_fetch_all("""
            SELECT COUNT(*) AS cnt,
                   AVG(total_ms) AS avg_ms,
                   MAX(total_ms) AS max_ms
            FROM audit_logs
            WHERE created_at >= NOW() - INTERVAL 24 HOUR
        """),
        # p95 computed in Python (MariaDB 10.x lacks PERCENTILE_CONT)
        _db_fetch_all("""
            SELECT total_ms FROM audit_logs
            WHERE created_at >= NOW() - INTERVAL 1 HOUR AND total_ms IS NOT NULL
            ORDER BY total_ms
        """),
        # Top slow queries (last 1h)
        _db_fetch_all("""
            SELECT user_email, route, query, total_ms, created_at
            FROM audit_logs
            WHERE created_at >= NOW() - INTERVAL 1 HOUR
            ORDER BY total_ms DESC
            LIMIT 10
        """),
    )
    samples = [int(r["total_ms"]) for r in p95_rows if r["total_ms"] is not None]
    if samples:
        p50 = samples[len(samples) // 2]
        p95 = samples[int(len(samples) * 0.95)]
        p99 = samples[int(len(samples) * 0.99)]
    else:
        p50 = p95 = p99 = 0
```

- [ ] **Step 2: `active_rows` 쿼리는 뒤에서 `pool_state`/`gates` 계산에 의존하지 않으므로 그대로 두거나 함께 gather에 포함 — 함께 포함**

`app/api/admin_api.py:241-247`의:
```python
    # Active users (last 15 min)
    active_rows = await _db_fetch_all("""
        SELECT COUNT(DISTINCT user_email) AS cnt
        FROM audit_logs
        WHERE created_at >= NOW() - INTERVAL 15 MINUTE
    """)
    active_users = int(active_rows[0]["cnt"]) if active_rows else 0
```
Step 1의 `asyncio.gather` 튜플에 다섯 번째 쿼리로 추가하고 언패킹 변수에 `active_rows`를 포함시킨다:
```python
    latency_1h, latency_24h, p95_rows, slow, active_rows = await asyncio.gather(
        ...(위 4개와 동일)...,
        _db_fetch_all("""
            SELECT COUNT(DISTINCT user_email) AS cnt
            FROM audit_logs
            WHERE created_at >= NOW() - INTERVAL 15 MINUTE
        """),
    )
    active_users = int(active_rows[0]["cnt"]) if active_rows else 0
```
(이 경우 Step 1의 `pool_state`/`gates` 계산 — `_get_pool()`, 세마포어 읽기 — 은 DB 쿼리가 아니라 동기 로컬 호출이므로 gather와 무관하게 그대로 순서상 그 사이 또는 뒤에 둔다.)

- [ ] **Step 3: `asyncio` import 확인**

`app/api/admin_api.py` 상단에 `import asyncio`가 없다면 추가한다.

- [ ] **Step 4: 문법 확인**

Run: `python -c "import ast; ast.parse(open('app/api/admin_api.py', encoding='utf-8').read())"`
Expected: 에러 없음

- [ ] **Step 5: Commit**

```bash
git add app/api/admin_api.py
git commit -m "fix(perf): parallelize independent metrics queries in admin get_metrics"
```

---

### Task 10: 회귀 검증 및 배포

**Files:** 없음 (검증/배포 전용 태스크)

- [ ] **Step 1: 기존 회귀 테스트 실행**

Run: `python scripts/qa_team_150.py`
Expected: 기존 기준 대비 정확도 저하 없음 (Task 1~9에서 SQL 생성/실행/포맷팅 로직 자체는 건드리지 않았으므로 회귀 없어야 함 — 특히 Task 2의 `_handle_multi` 변경이 multi 라우트 답변 포맷을 깨뜨리지 않는지 확인)

- [ ] **Step 2: 배포**

Run: `pm2 restart skin1004-prod`
Expected: 재시작 성공

- [ ] **Step 3: 상태 확인**

Run: `pm2 status`
Expected: `skin1004-prod`의 ↺(restart) 카운터가 1만 증가(방금 재시작 1회), status가 `online`, 이후 30초~1분 뒤 재확인해 카운터가 더 늘지 않음(크래시루프 아님) 확인

- [ ] **Step 4: findings 문서에 Phase 2 완료 기록**

`docs/superpowers/plans/2026-07-09-performance-optimization-findings.md` 끝에 섹션 추가:
```markdown

## Phase 2 완료 (2026-07-09)

1~10번 항목 수정 완료, 배포됨. 11~17번(중간 우선순위)은 별도 Phase 3로 필요시 진행.
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-07-09-performance-optimization-findings.md
git commit -m "docs(perf): mark phase 2 fixes complete and deployed"
```
