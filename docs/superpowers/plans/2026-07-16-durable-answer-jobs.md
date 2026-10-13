# Durable Answer Jobs Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure a chat request keeps running after browser disconnects, conversation switches, or SSE reconnects, and always produces a recoverable terminal outcome without an application-level elapsed-time deadline discarding the answer.

**Architecture:** Move execution ownership from the HTTP request into MariaDB-backed jobs claimed by queue-specific PM2 workers. The API atomically stores the user turn and enqueues an immutable request snapshot, while workers persist batched progress events and exactly one assistant result. The browser reconnects with an event sequence cursor; disconnecting only closes the viewer and never cancels the worker.

**Tech Stack:** FastAPI, asyncio, PyMySQL/MariaDB, PM2 on Windows, vanilla JavaScript EventSource, pytest.

---

## Contract and boundaries

- “No timeout” means no 5-second, 90-second, 120-second, HTTP-connection, or overall job deadline is allowed to discard a still-running core answer. Individual network attempts still need bounded connect/read watchdogs so one dead socket cannot prevent all future retries; hitting one advances the persisted retry/fallback state instead of ending the job.
- BigQuery and LLM work continues in a worker until success, explicit user cancellation, or an exhausted/non-retryable provider chain. Provider failure must end in a visible terminal failure record; it must never disappear as an aborted stream.
- Terminal semantics are uniform across routes: a usable answer is completed even when its verification metadata is partial/unverified; no usable answer is failed with structured error and no fabricated assistant answer; explicit user cancellation is cancelled. Each state has exactly one matching terminal event, including cancelled.
- Retrying a failed job creates an idempotent successor job that references the original user_message_id/snapshot and does not insert the user turn again. It recomputes current server authorization context before enqueue; it never mutates failed back to queued.
- The core data result is mandatory. Chart generation is an optional child artifact and must not prevent the core answer from completing.
- The browser moves only after the worker registry can execute every existing browser route: direct, bigquery, notion, cs, gws, multi, and image. The later grounded-web plan adds web to the same registry. No browser request may be enqueued into a route with no registered runner.
- Job snapshots contain only server-authoritative identity and authorization context. In particular, the client never chooses effective brand_filter, user_email, queue_name, or authorized source/resource IDs.
- The public enqueue request contains only the new user turn, not arbitrary prior messages. Inside the enqueue transaction, the server verifies conversation ownership, reloads canonical history from messages, cleans it with the existing history rules, appends the new turn, and freezes that server-built history in the job snapshot.
- Persisted answer chunks belong to a generation_attempt_id. Starting any retry/fallback after chunks were emitted must first persist answer_reset for a new attempt, and clients discard/supersede earlier attempt text. The terminal result_text/completed event is always the authoritative full answer.
- Parent answer completion, the assistant message, the terminal event, and the audit row are one transaction. The first durable rollout deliberately excludes post-answer chart generation; it returns the exact table/text result and never emits chart events after a terminal answer. Chart durability requires a separate child-artifact design and deployment.
- Production and development share MariaDB, so every job is permanently scoped to queue_name = prod or queue_name = dev. Both web processes and workers receive an explicit JOB_QUEUE_NAME environment variable; no process infers it from shared data or a default port.
- settings.job_queue_name defaults to an empty value so module imports and isolated tests remain possible, but the FastAPI lifespan and worker entry point fail fast before serving/claiming when it is not explicitly prod or dev.
- Existing POST /v1/chat/completions remains for compatibility. The first rollout moves the browser UI to the durable job API; API clients keep the legacy behavior until a later versioned migration.
- The worktree is already dirty. Before each task, inspect overlapping diffs and preserve user changes; stage only the named task files and never reset unrelated work.

### Task 1: Define job state and persistence contracts

**Files:**

- Create: app/models/job_schemas.py
- Create: tests/test_job_schemas.py
- Modify: app/db/mariadb.py
- Create: app/jobs/repository.py
- Create: tests/test_job_repository.py
- Create: tests/integration/test_job_repository_mariadb.py

**Step 1: Write failing schema tests**

Cover these invariants:

- Status transitions are queued -> running -> completed|failed|cancelled|retry_wait, retry_wait -> queued, and stale running -> queued under a new claim token.
- completed requires result_text and completed_at.
- failed requires a structured error object and completed_at.
- cancelled requires completed_at/cancellation provenance and cannot contain a newly fabricated result; usable partial/unverified content uses completed, not failed.
- public requests reject prior assistant/system messages, route-owned secrets, and client-supplied brand_filter, user_email, queue_name, or authorization grants. Image bytes are referenced by attachment rows and never duplicated into request_json.
- queue_name accepts only prod or dev.

Run: pytest tests/test_job_schemas.py -q

Expected: FAIL because job_schemas.py does not exist.

**Step 2: Implement the Pydantic contracts**

Define JobStatus, JobRequestSnapshot, AuthenticatedJobContext, JobEvent, JobSummary, JobResult, and EnqueueJobRequest. The public enqueue request includes client_request_id, conversation_id, exactly one new user turn, model, requested sources/resources, and image metadata; it cannot submit historical assistant/system turns. JobRequestSnapshot contains canonical conversation history reloaded by the server in the enqueue transaction. AuthenticatedJobContext is built only on the server and contains effective_brand_filter, normalized user_email where GWS needs it, and validated source/resource IDs. Do not store raw API keys, cookies, session IDs, or authentication tokens in request_json.

Preserve the current authorization model explicitly: brand access_groups/effective_brand_filter is the row-level ACL. There is not yet a per-user source ACL, so authenticated users may select only source IDs present in the enabled server datasource registry and only active team-resource IDs that currently exist; unknown, disabled, or deleted IDs are rejected. Do not describe that registry validation as a user-specific ACL. A future per-user source ACL requires its own schema/policy migration.

**Step 3: Write failing repository tests around a fake transaction connection**

Test:

- enqueue is idempotent on (queue_name, anon_id, client_request_id).
- reusing an idempotency key with a different canonical request fingerprint is a conflict, not a silent return of the old job.
- retry successor references a failed parent and the same user_message_id without inserting another user message; non-failed/foreign parents and purged required attachments are rejected.
- user message insert and job insert commit together.
- claim filters queue_name and uses a unique claim token.
- append_event allocates a strictly increasing per-job sequence.
- answer chunk events carry generation_attempt_id; retry/fallback atomically appends answer_reset(new_attempt_id, supersedes_attempt_id) before any new attempt chunk, and a terminal snapshot returns only authoritative result_text.
- complete_job inserts one assistant message and records its ID in the same transaction.
- a repeated completion returns the existing assistant message instead of inserting a duplicate.
- completed, failed, and cancelled each write their terminal event in the same transaction as the terminal status; completed also writes the result, assistant message, and idempotent audit row in that transaction.
- multimodal enqueue stores validated attachment rows atomically and request_json contains only attachment IDs/metadata.

Run: pytest tests/test_job_repository.py -q

Expected: FAIL because the repository is not implemented.

**Step 4: Add transaction support and DDL**

Add a transaction() context manager to app/db/mariadb.py that commits once and rolls back on every exception. Keep the existing one-statement helpers unchanged for compatibility.

Add ensure_agent_job_tables() with these logical fields:

- agent_jobs: UUID id, queue_name, anon_id, conversation_id, user_message_id, assistant_message_id, client_request_id, request_fingerprint, retry_of_job_id nullable, kind, route, status, request_json, provider_state_json, active_generation_attempt_id, result_text, result_metadata_json, error_json, worker_id, claim_token, attempt, next_attempt_at, next_event_seq, lease_expires_at, heartbeat_at, cancel_requested_at, created_at, started_at, completed_at.
- UNIQUE(queue_name, anon_id, client_request_id).
- UNIQUE(retry_of_job_id) (MariaDB permits multiple NULLs), so concurrent tabs can create at most one direct successor per failed job; a further retry chains from that failed successor. On idempotency-key reuse, compare the canonical request_fingerprint; a different conversation/turn/model/source payload returns 409 instead of returning an unrelated old job.
- INDEX(queue_name, status, next_attempt_at, created_at) for claim/retry polling; INDEX(queue_name, status, lease_expires_at) for stale recovery; INDEX(conversation_id, status, created_at) for active-conversation reads.
- agent_job_events: BIGINT id, job_id, seq, event_type, generation_attempt_id nullable, payload_json, created_at, UNIQUE(job_id, seq). Do not add a redundant second INDEX(job_id, seq) because the unique key already covers the replay query.
- agent_job_attachments: BIGINT id, job_id, ordinal, mime_type, sha256, data LONGBLOB, created_at, with UNIQUE(job_id, ordinal). Enforce the existing image MIME and 10 MB per-file limits before insertion, and purge binary data after terminal completion plus the documented short recovery window.
- messages.source_job_id nullable with a unique index and messages.response_metadata_json nullable, added idempotently. Completed assistant messages retain the terminal typed metadata envelope after detailed job events and job rows are pruned.
- audit_logs.source_job_id nullable with a unique index, added idempotently, so a worker retry cannot double-count admin performance metrics.

Because port 3000 and 3001 can start together against the same schema, wrap the migration in a named MariaDB advisory lock with a bounded startup acquisition wait. This wait protects schema migration only; it is unrelated to answer execution. Do not hide migration errors. Log the exact statement name and raise during startup if a required job table or index cannot be created.

**Step 5: Implement app/jobs/repository.py**

Create enqueue_with_user_message(), enqueue_retry_successor(), claim_next(), heartbeat(), append_events(), complete_with_assistant_message(), fail(), cancel(), request_cancel(), requeue_stale(), and purge_expired_attachments(). Event appends must accept batches. Claim and all terminal transitions are transactions.

enqueue_retry_successor() accepts only an owned failed parent, reuses its user_message_id and original canonical question/history, recomputes current brand/source/resource authorization, and creates a new queued row with retry_of_job_id and a new idempotency key without inserting a message. If required attachments have already been purged, return a typed 410 requiring the user to resend rather than running a different request.

complete_with_assistant_message() must atomically insert exactly one assistant message with source_job_id and response_metadata_json, update result/status/timestamps/result_metadata_json, append the completed event with its allocated sequence, and insert one audit_logs row keyed by source_job_id. Compute first-token time from the first persisted answer event and total time from job timestamps. fail() and cancel() atomically append their own terminal events. A retry after an ambiguous commit reads and returns the existing terminal snapshot instead of adding another message, event, or audit row.

Every running-job mutation must include WHERE id = ? AND claim_token = ? AND status = 'running'. A zero row count means the lease was lost and the old worker must stop emitting immediately. Never let a stale worker append events, heartbeat, fail, or complete after another worker has reclaimed the job.

**Step 6: Prove MariaDB concurrency against an isolated schema**

Add an opt-in integration test that requires TEST_MARIADB_URL and creates a uniquely named temporary schema (or unique table prefix when schema creation is unavailable). With two independent connections, verify the advisory migration lock, concurrent claim exclusivity, monotonic event allocation, idempotent ALTER/index creation, terminal transaction rollback, and unique-message/audit behavior under an ambiguous retry. Use MariaDB EXPLAIN to assert claim/retry polling, stale lease recovery, and conversation active-job queries choose the intended composite indexes rather than full scans. Always clean only the uniquely named test objects.

Run: pytest tests/integration/test_job_repository_mariadb.py -q

Expected: PASS when TEST_MARIADB_URL is explicitly configured; otherwise SKIP with a clear reason.

**Step 7: Run tests and commit**

Run: pytest tests/test_job_schemas.py tests/test_job_repository.py -q

Expected: PASS.

Commit: git add app/models/job_schemas.py app/db/mariadb.py app/jobs/repository.py tests/test_job_schemas.py tests/test_job_repository.py tests/integration/test_job_repository_mariadb.py && git commit -m "feat: add durable agent job repository"

### Task 2: Add atomic enqueue, status, cancellation, and replay APIs

**Files:**

- Create: app/api/job_api.py
- Create: tests/test_job_api.py
- Modify: app/main.py
- Modify: app/config.py
- Modify: app/api/conversation_api.py

**Step 1: Write failing API tests**

Using dependency overrides for authentication and repository access, assert:

- POST /api/agent-jobs verifies conversation ownership.
- POST accepts only the new user turn, reloads canonical conversation messages inside the same transaction, and ignores/rejects any attempt to inject prior assistant/system history. The stored snapshot exactly matches owned DB history plus the newly inserted user turn.
- POST ignores forged brand_filter values (including null and another brand), derives effective_brand_filter from the authenticated user exactly as the legacy chat route does, and stores only that server-derived value.
- POST derives user_email on the server. Requested source IDs must exist in the enabled server registry and requested team-resource IDs must be active/existing; unknown, disabled, or deleted IDs are rejected rather than copied. Current policy has no finer per-user source ACL, while brand access remains enforced independently.
- It returns 202 with job_id, user_message_id, status, and events_url.
- Reusing client_request_id returns the original job instead of a duplicate.
- Reusing client_request_id with any different canonical payload/conversation returns 409.
- GET /api/agent-jobs/{job_id} never exposes another user’s job.
- GET/status/events scope every read by both anon_id and the current process queue_name, so a dev process cannot read a prod job even for the same user.
- POST /api/agent-jobs/{job_id}/cancel changes only a non-terminal owned job.
- POST /api/agent-jobs/{failed_job_id}/retry with a new client_request_id creates/reuses one successor job, recomputes current authorization, references the original user_message_id, and leaves conversation message count unchanged. Foreign/non-failed parents are rejected and missing purged attachments return 410.
- GET /api/conversations/{id} includes pending_jobs so reopening a conversation can restore progress.
- Completed MessageOut entries expose source_job_id and the validated terminal response_metadata envelope, so a new tab with empty/corrupt sessionStorage can render citations/artifacts without remembering the old job ID.
- DELETE /api/conversations/{id} returns 409 while owned jobs are active unless cancel_running=true is explicit; the explicit path requests cancellation transactionally and deletes only after those jobs are terminal.

Run: pytest tests/test_job_api.py -q

Expected: FAIL because the router does not exist.

**Step 2: Implement the router**

Use the authenticated user’s anon_id as the ownership boundary. Resolve the same effective brand filter currently used by app/api/routes.py before the enqueue transaction, ignoring any client value. Resolve the authenticated email needed by GWS and intersect requested sources/resources with the server-authorized set. Store this AuthenticatedJobContext with the immutable snapshot so a later worker does not trust browser-owned authorization data.

Read queue_name from settings.job_queue_name populated by JOB_QUEUE_NAME. Keep the settings field import-safe with an empty default, but validate prod|dev in the FastAPI lifespan before accepting traffic. Tests must set the queue environment/override before lifespan startup rather than relying on a production default.

POST must call one repository transaction that locks/verifies the owned conversation, reloads its canonical messages, inserts the single new user message, builds the immutable server history/auth context, validates/stores image attachments, updates the conversation timestamp/title, and enqueues the job. The browser must no longer POST the same user message separately or send a trusted history array.

The retry endpoint calls enqueue_retry_successor() and never routes through the new-turn POST. It copies the original canonical input, refreshes only server-owned authorization fields, and exposes retry_of_job_id in JobSummary for audit/debugging.

**Step 3: Expose pending jobs on conversation load**

Extend ConversationDetail with active_jobs and their latest persisted sequence. Extend MessageOut additively with nullable source_job_id and typed response_metadata. Completed results remain ordinary assistant messages whose terminal metadata is preserved independently of event/job retention. Preserve all existing message fields for backward compatibility.

Change conversation deletion so an ordinary delete cannot race a worker’s final assistant insert. The UI must ask separately before using cancel_running=true; switching conversations is never cancellation.

**Step 4: Register DDL and router**

Call ensure_agent_job_tables() in app/main.py before accepting traffic, then include job_router.

Update the stale unauthenticated router baseline: an unauthenticated malformed chat request must remain 401 because authentication runs first; test 400 validation only with an authenticated request. Do not weaken the route to satisfy the old expectation.

**Step 5: Run tests and commit**

Run: pytest tests/test_job_api.py tests/test_router.py -q

Expected: PASS.

Commit: git add app/api/job_api.py app/api/conversation_api.py app/config.py app/main.py tests/test_job_api.py tests/test_router.py && git commit -m "feat: add durable job API"

### Task 3: Extract connection-independent execution for every browser route

**Files:**

- Create: app/jobs/answer_job_runner.py
- Create: app/jobs/sql_job_runner.py
- Create: app/agents/verified_answer.py
- Create: tests/test_answer_job_runner.py
- Create: tests/test_sql_job_runner.py
- Create: tests/test_verified_answer.py
- Modify: app/agents/orchestrator.py
- Modify: app/agents/sql_agent.py
- Modify: app/core/bigquery.py

**Step 1: Write failing verified-answer tests**

Use fixed query-result fixtures for sales sum, quantity sum, grouped ranking, period comparison, and empty results. Assert that the compiler:

- Renders exact returned numbers without arithmetic invention.
- Labels Sales1_R as KRW sales and Total_Qty as quantity.
- Preserves ordering and TOP N limits from the result rows.
- Produces a readable table even when both answer LLMs fail.
- Never includes Notion content in a BigQuery-only answer.

Run: pytest tests/test_verified_answer.py -q

Expected: FAIL.

**Step 2: Implement the deterministic result compiler**

verified_answer.py receives the validated SQL, columns, rows, route metadata, and question. It builds a factual answer/table first. Optional LLM interpretation is appended only for questions classified as comparative or explanatory and must not replace the exact table.

**Step 3: Write failing dispatcher and SQL runner tests**

Assert:

- answer_job_runner classifies from the immutable message history and dispatches direct, bigquery, notion, cs, gws, multi, and image; an unregistered route is rejected before claim/execution rather than hanging.
- direct, notion, cs, gws, multi, and image runners can be consumed completely without an HTTP Request or live SSE object and emit the common JobEvent union.
- multi delegates its BigQuery substage to sql_job_runner rather than the legacy _handle_bigquery/run_sql_agent cutoff path, checkpoints the BigQuery provider job ID in the parent job's provider_state_json, and resumes the same provider job after lease recovery.
- GWS receives only the server-derived user_email, every internal-data route receives the server-derived effective_brand_filter/authorized resources, and image data is loaded from validated attachment IDs.
- emit(progress) is called for route, SQL generation, validation, BigQuery execution, answer compilation, and completion.
- A disconnected consumer has no effect on the runner.
- Flash SQL generation failure escalates to Claude Opus 4.8.
- Answer-model failure falls back to the deterministic compiler.
- No elapsed-time Future.result(timeout=...) controls the overall core answer. A transport-attempt watchdog may raise a retryable event and advance to the next persisted attempt.
- Cancellation is checked between provider operations, not inferred from SSE disconnect.
- A multi job whose BigQuery substage exceeds the old 300/120-second limits continues polling, survives worker restart without a duplicate query, and honors explicit cancellation.

Run: pytest tests/test_answer_job_runner.py tests/test_sql_job_runner.py -q

Expected: FAIL.

**Step 4: Make BigQuery waiting resumable**

Split durable execution into submit and poll. Persist the BigQuery provider job ID in provider_state_json as soon as submission succeeds. Poll that same job with bounded per-RPC connect/read watchdogs but no overall query deadline; after worker restart or lease recovery, resume polling the existing provider job instead of submitting duplicate SQL. A poll watchdog records progress and retries the poll—it never discards or cancels the BigQuery job.

Retain explicit user cancellation and add a maximum_bytes_billed setting if one is not already configured. Cost protection must be data-volume based, not an elapsed timer that loses the answer.

**Step 5: Remove core answer deadlines and HTTP ownership in the durable path**

Refactor the SQL agent so its core stages can be invoked by sql_job_runner without the current approximately 90-second stream consumer cutoff and answer_future.result(timeout=120). Do not delete legacy behavior yet; isolate it behind the old HTTP path.

Implement answer_job_runner as the single registry/dispatcher used by the worker. Adapt the existing orchestrator route handlers so their generators are consumed inside the worker and receive AuthenticatedJobContext explicitly instead of reading request-local state. Preserve full cleaned conversation history for direct and the existing bounded conversation context for non-direct routes. Multi must call sql_job_runner for its BigQuery branch and namespace the resumable provider checkpoint inside the parent job; it must not wrap the legacy timed SQL handler. Register every current browser route before Task 6 changes the browser transport; the grounded-web plan later registers web through the same interface.

Do not start chart generation in this rollout and do not emit chart_ready/chart_failed after the parent terminal event. The durable SQL answer includes the deterministic table/text. Document chart-as-durable-child-artifact as a separate follow-up so terminal SSE semantics remain exact.

**Step 6: Run tests and commit**

Run: pytest tests/test_verified_answer.py tests/test_answer_job_runner.py tests/test_sql_job_runner.py tests/test_sql_agent.py -q

Expected: PASS.

Commit: git add app/jobs/answer_job_runner.py app/jobs/sql_job_runner.py app/agents/verified_answer.py app/agents/orchestrator.py app/agents/sql_agent.py app/core/bigquery.py tests/test_verified_answer.py tests/test_answer_job_runner.py tests/test_sql_job_runner.py && git commit -m "feat: run answers independently of HTTP"

### Task 4: Add leased PM2 workers with crash recovery

**Files:**

- Create: app/jobs/__init__.py
- Create: app/jobs/worker.py
- Create: tests/test_job_worker.py
- Modify: ecosystem.windows.config.js

**Step 1: Write failing worker tests**

Test queue isolation, the complete runner registry for direct/bigquery/notion/cs/gws/multi/image, lease heartbeat, stale claim recovery, retryable error requeue, non-retryable terminal failure, cancellation, graceful shutdown, and exactly-once completion after a simulated crash between assistant insertion and acknowledgment. Include a crash after several committed answer chunks: the replacement worker must append answer_reset for a new generation before new chunks, and replay must expose only the new active generation/final result. Add a split-brain test where worker A stalls, worker B reclaims the expired lease, and A resumes: every heartbeat/event/completion from A must be rejected by claim_token fencing while B completes once.

Also test a database outage during heartbeat and a cancel/completion race. Completion must atomically require cancel_requested_at IS NULL; whichever transaction commits first wins, and the loser observes the terminal state without inserting a second message. The winning completion transaction must contain the assistant message, result/status, completed event, and one audit_logs row; replay or worker retry must not duplicate any of them.

Run: pytest tests/test_job_worker.py -q

Expected: FAIL.

**Step 2: Implement the worker loop**

The loop claims one job, starts a heartbeat task, dispatches through answer_job_runner, batches emitted events every 500 ms or 4 KB, and completes through the repository's atomic terminal transaction. Every visible output event carries the current generation_attempt_id. If a claimed job has committed output from an incomplete attempt, persist answer_reset with the next attempt ID before invoking any provider; retries/fallbacks do the same. Use a short idle poll interval; this is not a request timeout. When heartbeat state is uncertain because MariaDB is unavailable, stop publishing and cancel/pause local provider consumption; resume only after reconnecting and proving the same claim_token is still current.

Retry retryable network/429/5xx errors with persisted attempt count and next_attempt_at. Use a configurable finite attempt budget per provider/fallback chain rather than an elapsed deadline; after exhaustion, return the deterministic data answer when rows exist or persist a terminal failure. A provider error must remain visible in status/events. Do not retry invalid user input. Periodically purge attachment bytes after 24 hours, detailed events after 7 days, and terminal job metadata after 30 days while preserving ordinary conversation messages. Make all periods settings documented in the runbook.

**Step 3: Configure separate processes**

Add:

- skin1004-job-worker-prod with JOB_QUEUE_NAME=prod.
- skin1004-job-worker-dev with JOB_QUEUE_NAME=dev.

Also set JOB_QUEUE_NAME=prod on skin1004-prod and JOB_QUEUE_NAME=dev on skin1004-dev so enqueue and claim use the same explicit namespace.

Copy the existing development behavior flags BQ_FAST_ANSWER=1 and BQ_TOOL_LOOP=0 onto skin1004-job-worker-dev. Copy the production web process's corresponding BQ/model settings onto the production worker. Do not let moving execution to a worker silently change the 3000/3001 A/B behavior.

Use the same Python 3.11 executable and hidden/background PM2 behavior. Keep settings.job_queue_name import-safe, but have the worker entry point validate prod|dev and exit nonzero before polling if missing/invalid. Never let either worker infer its queue from shared DB contents.

**Step 4: Run tests and commit**

Run: pytest tests/test_job_worker.py tests/test_job_repository.py -q

Expected: PASS.

Commit: git add app/jobs ecosystem.windows.config.js tests/test_job_worker.py && git commit -m "feat: add recoverable agent workers"

### Task 5: Implement replayable SSE as a view over persisted events

**Files:**

- Modify: app/api/job_api.py
- Create: tests/test_job_sse.py

**Step 1: Write failing SSE tests**

Assert:

- GET /api/agent-jobs/{id}/events emits id: seq, event: progress|chunk|answer_reset|completed|failed|cancelled, and JSON data.
- Last-Event-ID and after_seq replay only later events.
- A heartbeat comment is sent every 15 seconds while no event exists.
- Disconnecting the test client does not call request_cancel.
- Reconnect after completion immediately replays the terminal event.
- A requested cursor older than retained events returns a reset snapshot containing current status/result/latest_seq instead of silently creating a sequence gap.
- answer_reset changes the active generation; chunks from superseded generation_attempt_id values never contribute to the reconstructed answer, and completed.result_text replaces any accumulated chunks authoritatively.

Run: pytest tests/test_job_sse.py -q

Expected: FAIL.

**Step 2: Implement the stream**

Poll repository events after the cursor, emit persisted events, and stop only after a terminal event has been delivered. Set Cache-Control: no-cache, X-Accel-Buffering: no, and Connection: keep-alive. The SSE coroutine may end on disconnect; the job remains owned by the worker.

When detailed events have been pruned, emit event: reset with the authoritative job snapshot and continue from latest_seq. The snapshot includes active_generation_attempt_id and the authoritative terminal result/metadata when terminal. Never pretend that a noncontiguous replay was complete.

**Step 3: Run tests and commit**

Run: pytest tests/test_job_sse.py tests/test_job_api.py -q

Expected: PASS.

Commit: git add app/api/job_api.py tests/test_job_sse.py && git commit -m "feat: replay durable job events"

### Task 6: Reconnect the browser instead of aborting execution

**Files:**

- Modify: app/frontend/chat.js
- Modify: app/frontend/chat.html
- Modify: app/static/style.css
- Create: tests/frontend/test_job_reconnect.py

**Step 1: Write a failing browser-state test**

Model these transitions with the existing frontend test style:

- send -> enqueue -> subscribe.
- enqueue committed but the HTTP response is lost -> repeat the same client_request_id and recover the original job/user message.
- switch conversation -> close local EventSource only.
- reopen -> load pending_jobs -> subscribe after last sequence.
- refresh -> restore pending card and terminal result.
- duplicate completed event -> render/save assistant exactly once.
- answer_reset after a partial first attempt removes the old text/metadata before rendering the replacement generation; completed.result_text wins over accumulated chunks.
- stop button -> explicit POST cancel, not AbortController alone.
- expired auth, offline/online, two tabs, corrupt sessionStorage, sequence reset, and cancellation/completion races all converge on the server snapshot without duplicate bubbles.
- failed means no usable answer and shows the structured error; completed may carry a usable answer with a partial/unverified metadata status; cancelled is its own visible terminal state.
- failed retry -> POST the failed job's /retry endpoint with a fresh retry idempotency key, replace/watch the successor card, and do not render or persist a second user bubble. A 410 attachment response asks for an explicit resend.

Run: pytest tests/frontend/test_job_reconnect.py -q

Expected: FAIL.

**Step 2: Replace the request-bound send flow**

Generate client_request_id in the browser. POST the job request once, render the saved user message returned by the API, then subscribe with EventSource. Keep last_seq per job in memory and sessionStorage.

Conversation switches close only the viewer. The send button’s stop action calls the cancel endpoint, then closes the viewer after cancellation is acknowledged.

Remove client-side assistant POST for durable jobs; the worker already persisted the message. On completed, reload or merge the returned assistant_message_id.

**Step 3: Add progress and reconnect UI**

Show queued, running stage, reconnecting, completed, failed, and cancelled states. A failed job displays its persisted reason and retry action; retry calls the successor endpoint rather than resending the user turn, so it never duplicates the user bubble. The failed card never vanishes until the successor is visible.

Increment app/frontend/chat.html cache versions from style.css?v=147 and chat.js?v=224 to the next numbers used by the final change.

**Step 4: Run tests and commit**

Run: pytest tests/frontend/test_job_reconnect.py tests/frontend/test_edit_resend_no_duplicate.py -q

Expected: PASS.

Commit: git add app/frontend/chat.js app/frontend/chat.html app/static/style.css tests/frontend/test_job_reconnect.py && git commit -m "feat: restore answers after reconnect"

### Task 7: Prove disconnect, restart, and exactly-once behavior end to end

**Files:**

- Create: tests/integration/test_durable_answer_flow.py
- Create: tests/e2e/test_durable_chat_reconnect_playwright.py
- Create: scripts/verify_durable_jobs.py
- Modify: docs/operations.md if present, otherwise create docs/durable-jobs-runbook.md

**Step 1: Add the integration scenario**

Use the actual chat.html/chat.js bundle in Playwright with fakes for paid providers in CI:

1. Enqueue a job.
2. Read progress through sequence N.
3. Disconnect.
4. First persist partial chunks, then simulate worker death and expired lease.
5. Start a replacement worker.
6. Reconnect with Last-Event-ID=N.
7. Assert answer_reset supersedes the partial generation, completed.result_text is authoritative, and there is exactly one rendered answer bubble and one assistant message.

Repeat the flow through each registered route with route-specific provider fakes. Assert GWS receives the authenticated email, internal routes receive the effective brand scope, images are loaded by attachment ID, and audit_logs contains exactly one source_job_id row per terminal answer.

Add a failed -> retry-successor case: lose the retry HTTP response, repeat its idempotency key, and assert one successor job, the original user_message_id, no duplicate user bubble/DB row, refreshed authorization, and exactly one eventual assistant message.

The Playwright case must load the real chat.html/chat.js rather than copying frontend functions into a synthetic harness. It closes the live event connection after partial output, switches conversations, restores with after_seq, processes answer_reset, and asserts exactly one final bubble from the authoritative result.

Run: pytest tests/integration/test_durable_answer_flow.py tests/e2e/test_durable_chat_reconnect_playwright.py -q

Expected: PASS.

**Step 2: Add an opt-in real verification script**

The script targets port 3001 by default, creates an isolated test conversation, sends a known low-cost BigQuery question, intentionally closes SSE, polls status, reconnects, verifies the final message, and deletes only its own test conversation. Require --allow-paid-api for the real provider call and exactly one explicit authentication input: --cookie-file with a documented Playwright/Netscape cookie export, or --bearer-token read from an environment variable. Never embed credentials in arguments, fixtures, logs, or the repository; fail before creating data when authentication is absent/expired.

**Step 3: Full regression**

Run: pytest tests/test_job_schemas.py tests/test_job_repository.py tests/test_job_api.py tests/test_job_sse.py tests/test_answer_job_runner.py tests/test_sql_job_runner.py tests/test_job_worker.py tests/frontend/test_job_reconnect.py tests/e2e/test_durable_chat_reconnect_playwright.py tests/test_sql_agent.py tests/test_router.py -q

Run with isolated TEST_MARIADB_URL: pytest tests/integration/test_job_repository_mariadb.py tests/integration/test_durable_answer_flow.py -q

Run: python scripts/verify_durable_jobs.py --base-url http://127.0.0.1:3001 --cookie-file <local-test-cookie-file> --allow-paid-api

Expected: reconnect receives the same terminal answer and messages contains one assistant row for the job.

The verification script exits nonzero for duplicate assistant rows, sequence gaps without reset, cross-queue visibility, recovery taking more than two lease periods after a simulated worker loss, or a missing terminal outcome.

### Task 8: Deploy with queue isolation and verify production

**Files:**

- Modify as needed: docs/durable-jobs-runbook.md

**Step 1: Pre-deploy checks**

Run: git diff --check

Run: pytest tests/test_job_schemas.py tests/test_job_repository.py tests/test_job_api.py tests/test_job_sse.py tests/test_answer_job_runner.py tests/test_sql_job_runner.py tests/test_job_worker.py tests/frontend/test_job_reconnect.py tests/e2e/test_durable_chat_reconnect_playwright.py tests/test_sql_agent.py tests/test_router.py -q

**Step 2: Migrate and start processes in dependency order**

Use PM2 restart, never reload, and never assume pm2 restart can create a brand-new process entry.

1. Set JOB_QUEUE_NAME=dev on the dev web entry and restart skin1004-dev; confirm the shared MariaDB migration and indexes completed before any new worker starts.
2. Create the new dev worker with pm2 start ecosystem.windows.config.js --only skin1004-job-worker-dev, then run the full port-3001 disconnect/replay and route-matrix verification with the existing dev BQ flags preserved.
3. Create the prod worker with pm2 start ecosystem.windows.config.js --only skin1004-job-worker-prod while it has no browser jobs to claim, verify it is healthy and queue-scoped to prod, then pm2 restart skin1004-prod to switch the browser API atomically onto the already-available worker.
4. Run production smoke checks, then pm2 save so both new entries survive reboot. The runbook must distinguish these one-time pm2 start commands from later pm2 restart commands.

Do not stop, delete, or kill skin1004-prod. If the dev migration or worker verification fails, do not restart production.

**Step 3: Verify PM2 and queues**

Run: pm2 status

Confirm restart counters are sane and each worker logs only its configured queue. Inspect the last 30 lines for skin1004-prod and both workers.

**Step 4: Production smoke test**

On port 3000, enqueue one isolated low-cost request, disconnect the viewer, verify status completes, reconnect, and confirm exactly one assistant message. Do not reuse or alter a real user conversation.

**Step 5: Commit final runbook updates**

Commit: git add docs/durable-jobs-runbook.md && git commit -m "docs: add durable jobs operations runbook"

## Acceptance criteria

- Closing the tab, switching conversations, refreshing, or losing SSE never cancels a job.
- No core answer is discarded at 5, 90, 120, or 300 seconds in the durable path.
- A completed job is visible after reconnect and persisted as exactly one assistant message.
- A retry after partial output persists answer_reset and clients render only the active generation; completed.result_text and response_metadata are authoritative after replay or event pruning.
- Retrying a failed job creates one idempotent successor with the original user message and no duplicate conversation turn.
- Every current browser route has a registered connection-independent runner before the UI switches; forged client brand/email/source context cannot expand access.
- Terminal answer state, assistant message, terminal event, and audit row commit exactly once together; charts are explicitly outside this first durable rollout rather than arriving after terminal SSE closure.
- Worker restart recovers an expired lease without duplicate output.
- A hung provider socket advances through per-attempt retry/fallback while the durable job remains visible; there is no overall job deadline.
- Claim-token fencing prevents stale workers from emitting or completing after lease recovery.
- Prod and dev workers cannot cross-claim.
- Pruned event history returns an explicit reset snapshot, never a silent replay gap.
- If every provider fails, the user sees a durable terminal failure; if query rows exist but answer formatting fails, the deterministic verified answer is still shown.
