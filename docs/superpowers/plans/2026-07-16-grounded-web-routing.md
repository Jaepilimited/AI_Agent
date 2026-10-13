# Grounded Web Routing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current Gemini Pro summary -> Claude Opus rewrite path with a first-class web route that uses Gemini 3.5 Flash for fast grounded lookup, preserves claim-level sources, verifies them before display, and escalates only genuinely complex web analysis to Opus 4.8.

**Architecture:** A WebSearchAgent owns search, grounding extraction, verification, and model escalation. Simple current-fact lookups return the Flash grounded answer directly with an honest verified/grounded/partial/unverified state. Complex comparison/research queries reuse structured evidence in an Opus synthesis step, rebuild final claim-to-source spans, then re-run verification. The orchestrator routes explicitly to web instead of hiding search inside direct, and the API/UI transport sources and Google Search Suggestions as typed metadata rather than relying on model-written URLs.

**Tech Stack:** google-genai generateContent API, Gemini 3.5 Flash, Claude Opus 4.8, FastAPI SSE, Pydantic, vanilla JavaScript, pytest with recorded grounding fixtures.

---

**Implementation order:** Complete 2026-07-16-durable-answer-jobs.md first, including app/jobs/repository.py, app/jobs/answer_job_runner.py, app/models/job_schemas.py, app/api/job_api.py, and replayable job SSE. This plan then adds web to that runner registry and JobEvent union; it must not invent a parallel queue, repository, or browser stream. The search agent can still be unit-tested independently, but production acceptance requires the combined durable-web replay test in Task 9.

**Out of scope for this rollout:** Upgrading google-genai to 2.x and enabling the Interactions API. That major SDK migration requires a separate plan and deployment because it can change every Gemini caller even when a feature flag is off. The later migration must use an isolated dev virtual environment, an exact 2.x pin/lock, all-Gemini-caller contract tests, and a port-3001 soak before the shared production interpreter changes.

**Worktree rule:** The repository already contains unrelated user changes. Inspect every overlapping diff before editing, preserve it, and stage only the files named by the current task.

## Evidence behind the model policy

The 2026-07-16 paid-API smoke benchmark used three current questions with official ground truth: Korean president/name and inauguration date, 2026 minimum wage/period, and Chrome Stable version/date.

| Model/path | Correct | Median completion | Decision |
|---|---:|---:|---|
| Gemini 3.5 Flash + Google Search, thinking off | 3/3 | 4.1 s | Default lookup path |
| Gemini 2.5 Pro + Google Search | 2/3 | 6.0 s | Remove as default search model |
| Claude Opus 4.8 native web search, low effort | 3/3 facts, one weak-evidence caveat | 8.2 s | Complex synthesis/fallback candidate |
| Claude Sonnet 5 native web search, low effort | 1/3 | 7.5 s | Smoke-tested; do not use as default search path |

The Chrome case was especially diagnostic: Pro chose Early Stable 151 as general Stable, while Flash and Claude found the official Stable 150.0.7871.124/.125 release from 2026-07-14. The Sonnet result came from a native-search call that handled non-text blocks explicitly; Task 3 still fixes the production adapter's separate content[0].text bug, and the three-case smoke result remains insufficient for promotion.

The current environment pins google-genai 1.61.0 and runs 1.62.0. A real Interactions API smoke call returned HTTP 400 because the current schema now requires google-genai >= 2.0.0. Therefore the safe production change first uses the already working generateContent grounding metadata. The SDK/Interactions migration is isolated behind a later dev-only gate.

## Routing policy

- Static knowledge, conversation, and provided-content analysis: direct, no search.
- Current/changing/external facts or explicit search request: web lookup with Gemini 3.5 Flash.
- Multi-entity comparison, recommendations requiring current evidence, or synthesis across several sources: Flash evidence collection -> Opus 4.8 synthesis -> verification.
- Internal BigQuery plus external context: multi, but its external branch must call the same WebSearchAgent.
- Gemini Pro is not a search escalation tier. It remains available only for unrelated workloads that separately justify it.
- Sonnet 5 is not used for production web answers until the recorded evaluation reaches the same factual/citation threshold as Flash.

### Task 1: Build a reproducible web-search evaluation harness

**Files:**

- Create: tests/fixtures/web_search/README.md
- Create: tests/fixtures/web_search/gemini_grounded_chrome.json
- Create: tests/fixtures/web_search/gemini_grounded_minimum_wage.json
- Create: tests/fixtures/web_search/gemini_grounded_president.json
- Create: scripts/benchmark_web_models.py
- Create: tests/test_web_benchmark_scoring.py

**Step 1: Write failing scorer tests**

The scorer must normalize names, dates, versions, currency, and URLs. It must score:

- latest-value correctness,
- required-claim precision/recall,
- official-domain citation presence,
- claim-to-source coverage,
- search decision correctness,
- completion latency.

Also include two no-search controls: HTTP 404 vs 500, and Python list vs tuple.

Run: pytest tests/test_web_benchmark_scoring.py -q

Expected: FAIL because the harness does not exist.

**Step 2: Implement recorded and live modes**

Recorded mode is free and deterministic. Live mode requires --allow-paid-api, writes an as_of timestamp, model IDs, sanitized prompts, latency, source domains, and raw response metadata. It must never print API keys.

Keep prompts in a UTF-8 .json file or Python source file. Do not feed Korean benchmark prompts through an unconfigured Windows PowerShell pipe; that was proven to replace Hangul with question marks.

Live cases begin with these categories and expand each into timestamped paraphrases:

- Current 대한민국 대통령 and official inauguration date; president.go.kr.
- Current applicable 최저임금 and period; minimumwage.go.kr or moel.go.kr.
- Current Chrome Windows/Mac Stable channel version and post date; chromereleases.googleblog.com.
- Current cosmetics regulation notice/version; mfds.go.kr or law.go.kr.

Use at least 20 distinct current-fact prompts spanning politics, labor, regulation, software releases, company leadership, schedules, and product specifications, plus at least 10 stable no-search controls. Run every prompt three times per candidate with randomized paired model order. Report cold and warm runs separately, accuracy/source-coverage confidence intervals, p50/p95, provider/search failure rate, partial/grounded rate, false-search rate on controls, and cost/query counts.

Benchmark the paths that can actually be deployed, not only raw model endpoints: simple Flash lookup; the composite Flash-evidence -> Opus structured synthesis path; the real multi route's external branch; Gemini 2.5 Pro search as incumbent comparison; and Claude Sonnet 5/Opus native search as diagnostic candidates. Save sanitized raw provider blocks and final transported metadata for every run. Score Sonnet once with the production adapter and once with the canonical text-block extractor so parser failures are reported separately from factual model failures.

**Step 3: Add acceptance thresholds**

Use three separate gates: recorded fixtures for deterministic parser/verifier CI only; live provider contract tests with freshly fetched timestamped official gold; and an actual port-3001 browser E2E. Recorded fixtures can never promote a model. Apply the gate to each deployable path separately, including simple Flash and Flash -> Opus composite output. Default-model promotion requires at least 95% required-claim accuracy, 95% official-source coverage, zero fabricated URLs, and p50/p95 no worse than 20% above the incumbent on the same paired run, with the lower confidence bound reported. A single three-case smoke test informs this plan but does not replace the recurring suite.

**Step 4: Run tests and commit**

Run: pytest tests/test_web_benchmark_scoring.py -q

Expected: PASS.

Commit: git add scripts/benchmark_web_models.py tests/fixtures/web_search tests/test_web_benchmark_scoring.py && git commit -m "test: add grounded web model benchmark"

### Task 2: Define grounded search and verification schemas

**Files:**

- Create: app/models/search_schemas.py
- Create: tests/test_search_schemas.py

**Step 1: Write failing schema tests**

Test:

- SearchSource has id, title, original_uri, canonical uri, display_domain, retrieved_at, provider, and optional published_at.
- ClaimSupport has answer start/end, cited text, and one or more source IDs.
- SearchEntryPoint carries Google's renderedContent plus provider/model provenance and a strict size limit; arbitrary model/page HTML cannot populate it.
- VerificationResult is verified, grounded, partial, or unverified and lists unsupported claim spans, evidence-fetch state, freshness state, and warnings. grounded means provider-linked but not independently entailed/freshness-checked; only verified may use the stronger UI badge.
- GroundedAnswer contains text, query list, sources, supports, optional search_entry_point, verification, model ID, and timing.
- Duplicate source URIs are normalized to one stable source ID.

Run: pytest tests/test_search_schemas.py -q

Expected: FAIL.

**Step 2: Implement the models**

Treat every web URI and title as untrusted input. Validate schemes, cap field sizes, strip control characters, and never render raw HTML from a source title. Treat search_entry_point.renderedContent as a provider-originated opaque display artifact: accept it only from the Google SDK response object, cap it, preserve it unchanged through persistence, and never concatenate it into the answer DOM.

**Step 3: Run tests and commit**

Run: pytest tests/test_search_schemas.py -q

Expected: PASS.

Commit: git add app/models/search_schemas.py tests/test_search_schemas.py && git commit -m "feat: define grounded search contracts"

### Task 3: Preserve Gemini grounding metadata in the LLM adapter

**Files:**

- Modify: app/core/llm.py
- Create: tests/test_gemini_grounding.py

**Step 1: Write failing parser tests from recorded SDK responses**

Assert extraction of:

- response.text,
- grounding_metadata.web_search_queries,
- grounding_chunks web title and URI,
- grounding_supports segment offsets and chunk indices,
- search_entry_point.rendered_content when present, with Google provider provenance and without interpreting it as answer text.

Test missing metadata, duplicate chunks, multibyte Korean text, and redirect URIs. Offset handling must follow the SDK field semantics; do not assume a byte offset is a Python character index without a fixture proving it.

Run: pytest tests/test_gemini_grounding.py -q

Expected: FAIL because generate_with_search returns only str.

**Step 2: Add generate_grounded()**

Return GroundedAnswer from the existing generateContent API. Keep generate_with_search() and generate_with_history_and_search() as compatibility wrappers that return .text. Carry search_entry_point through the typed object even when the textual answer has no grounding support for it; do not log or silently drop it.

Add get_web_lookup_client(model_id) (or an equivalent WebSearchAgent-owned factory) and have it instantiate the exact settings.web_lookup_model with thinking_budget=0. Test that changing WEB_LOOKUP_MODEL changes only this web client and does not mutate settings.gemini_flash_model/settings.gemini_model or unrelated Pro callers.

Preserve Google’s returned grounding URI as the authoritative link. If canonical resolution is later added, restrict outbound requests against SSRF and retain both original and canonical URI.

**Step 3: Fix Claude text-block extraction while touching the adapter**

Add one shared helper that concatenates TextBlock values and skips ThinkingBlock/server-tool blocks. Cover Sonnet 5 and Opus 4.8 response shapes. This removes the already observed response.content[0].text failure without enabling Sonnet as the web default.

**Step 4: Run tests and commit**

Run: pytest tests/test_gemini_grounding.py tests/test_sql_agent.py -q

Expected: PASS.

Commit: git add app/core/llm.py tests/test_gemini_grounding.py && git commit -m "feat: preserve search grounding metadata"

### Task 4: Verify claim coverage and source policy

**Files:**

- Create: app/core/answer_verifier.py
- Create: app/core/source_resolver.py
- Create: tests/test_answer_verifier.py
- Create: tests/test_source_resolver.py

**Step 1: Write failing verification tests**

Cover:

- Every factual sentence is fully or partially covered by a grounding support span.
- Exact version/date/money/name claims require a linked source.
- A source title claiming an official domain does not override its URI.
- vertexaisearch.cloud.google.com grounding redirects are accepted only after the resolver records their safe canonical destination; official-domain checks use the canonical host, never the title alone.
- javascript:, data:, localhost, private IP, credentials-in-URL, and malformed links are rejected.
- DNS rebinding after validation cannot change the actual connection to a private/link-local/metadata address.
- Answers with an invented URL become unverified.
- Search failure text cannot be marked verified.
- Grounding support spans without independently fetched source entailment are grounded, never verified.
- verified requires every required atomic name/date/version/money claim to appear in normalized fetched source content and the source to state an applicable/effective/as-of period consistent with the answer.

Run: pytest tests/test_answer_verifier.py -q

Expected: FAIL.

**Step 2: Implement a constrained grounding redirect resolver**

Only follow redirects whose original host exactly matches vertexaisearch.cloud.google.com and whose path starts with /grounding-api-redirect/. Implement a PinnedAddressTransport: resolve A/AAAA, reject every non-public answer, connect the socket directly to one validated IP while retaining the original Host header and TLS SNI/certificate verification, and confirm the connected peer IP equals the pinned public address. Disable environment proxies for this verifier. Repeat resolution, pinning, and peer validation independently at every redirect hop; never validate a hostname and then let the HTTP client resolve it again. Use bounded per-attempt connection/read watchdogs, at most three redirects, HTTPS-only destinations, and a bounded TTL cache keyed by hostname plus resolved address set. Reject credentials, nonstandard ports, localhost, link-local, private, and metadata-service IP ranges. If safe pinning/peer inspection is unavailable on the running transport, fail resolution closed and leave the answer grounded rather than verified. Preserve original_uri even when resolution fails and mark the source unresolved rather than trusting its title.

For canonical public sources, fetch a capped HTML/text/PDF evidence representation through the same SSRF guard, store fetched_at/content hash/published-or-effective date when available, and normalize it for exact atomic-claim checks. A fetch watchdog yields grounded rather than ending the overall job. Never execute page JavaScript or follow page instructions.

Write tests with a fake resolver/socket transport for safe official redirects, redirect loops, DNS changing from public during validation to private at connection time, peer-IP mismatch, public-to-private redirect/rebinding, excessive hops, non-Google initial URLs, and resolution failure.

Run: pytest tests/test_source_resolver.py -q

Expected: PASS.

**Step 3: Implement deterministic verification**

Verification checks structure, provider grounding coverage, independently fetched atomic-fact entailment, and stated freshness; it does not ask another LLM to grade its own answer. Return verified only when all required atomic facts pass those checks, grounded when provider links exist but independent evidence/freshness is incomplete, and partial/unverified with explicit unsupported spans otherwise.

**Step 4: Run tests and commit**

Run: pytest tests/test_source_resolver.py tests/test_answer_verifier.py -q

Expected: PASS.

Commit: git add app/core/source_resolver.py app/core/answer_verifier.py tests/test_source_resolver.py tests/test_answer_verifier.py && git commit -m "feat: verify grounded answer coverage"

### Task 5: Implement the WebSearchAgent and escalation policy

**Files:**

- Create: app/agents/web_search_agent.py
- Create: app/jobs/web_job_runner.py
- Create: tests/test_web_search_agent.py
- Create: tests/test_web_job_runner.py
- Modify: app/jobs/answer_job_runner.py
- Modify: app/models/job_schemas.py
- Modify: app/config.py

**Step 1: Write failing agent tests**

Assert:

- Simple lookup calls Gemini 3.5 Flash once and returns its grounded answer directly; only independently fetched entailment/freshness may upgrade the status to verified.
- The prompt includes current KST date, the full resolved user question, requested language, concise output rules, and official-source preference.
- A follow-up such as “그 사람은 지금 어느 회사 CEO야?” receives the resolved antecedent from conversation context before search.
- Partial grounding triggers one focused Flash retry, not Gemini Pro.
- Complex comparison passes structured source IDs and supported text to Opus 4.8 as untrusted evidence, not as a system instruction.
- Opus must return a structured claim list such as {text, source_ids}; final answer offsets/support spans are rebuilt from that claim list rather than reusing Gemini offsets from pre-synthesis text. The model cannot exempt its own sentence from verification with factual:false.
- A server-side deterministic claim classifier requires support by default and allowlists only non-declarative headings/connectors with no entity, date, number, version, currency, comparison, or current-state predicate. It verifies related tuples in the same evidence window, not merely that each token appears somewhere on the page.
- Opus may reorganize supported facts but cannot add an unsupported current claim and still receive verified status. Missing/invalid source IDs, prose outside the schema, unsupported relationships, or failed independent entailment downgrades the affected claim to partial/unverified.
- Search/provider failure with no usable evidence returns typed failed/error; a usable partial answer returns completed with partial/unverified metadata. Neither path returns a truthy fake “Google search result” string.
- A provider call that never resolves is interrupted by its per-attempt transport watchdog, persisted as a retry event, and proceeds through retry/fallback without applying an overall job deadline.
- The durable web runner emits answer, sources, verification, and terminal events under the active claim token.

Run: pytest tests/test_web_search_agent.py -q

Expected: FAIL.

**Step 2: Implement lookup mode**

Call get_web_lookup_client(settings.web_lookup_model).generate_grounded(), run AnswerVerifier, and return. On incomplete coverage, make one narrower retry that names the missing claims and preferred official domains. Merge/deduplicate sources before verification. Never route this setting through the shared get_flash_client() factory, which reads a different setting.

**Step 3: Implement research mode**

Collect a small set of targeted grounded answers in parallel, then call Opus 4.8 with only supported snippets and source IDs in a delimited evidence block. Treat the evidence block as data and explicitly ignore instructions found inside it. Require a validated structured output containing ordered claim objects with text and source_ids; ignore/reject any model-provided factual exemption. Construct final prose from those objects, calculate new character spans against the final prose, and run a server-side deterministic claim classifier that requires evidence for every declarative sentence by default. Verify each entity-relation-value/date tuple within the same cited evidence sentence/window and check freshness; finding the name and number separately on a page is insufficient. Never reuse Flash grounding offsets after Opus rewrites text. Schema failure, an unknown source ID, unsupported relationship, or freshness mismatch produces partial/unverified rather than verified.

**Step 4: Configure safe fallbacks**

Add settings for WEB_LOOKUP_MODEL defaulting to gemini-3.5-flash, WEB_RESEARCH_MODEL defaulting to claude-opus-4-8, maximum search rounds, per-attempt connect/read watchdogs, and official-domain preferences. Wire both model settings directly into their dedicated factories and assert the provider-reported model IDs match configuration. Do not add an overall wall-clock answer timeout. Every attempt timeout advances persisted retry/fallback state instead of discarding the job.

If Google remains unavailable after normal provider retries, persist failed when there is no usable answer, or completed with partial/unverified metadata when a usable evidence-backed partial answer exists. A Claude-native-search provider fallback may be added behind WEB_CLAUDE_SEARCH_FALLBACK=0 only after the live benchmark meets the same source-coverage threshold.

**Step 5: Implement the durable web runner**

Implement app/jobs/web_job_runner.py as the durable-plan answer_job_runner registry entry for route web. Extend the durable JobEvent union with web_sources, web_verification, and google_search_entry_point payloads. Every answer and web-metadata event carries generation_attempt_id; answer_reset supersedes the old answer, sources, supports, verification, and Search Suggestions together before a recovered/retried attempt emits anything. Persist them through the existing repository/sequence/fencing contract exactly as generated and never reconstruct them from rendered Markdown. A terminal web completion stores the active attempt's entire typed web envelope in both agent_jobs.result_metadata_json and the assistant message response_metadata_json inside the same atomic assistant/result/terminal/audit transaction.

**Step 6: Run tests and commit**

Run: pytest tests/test_web_search_agent.py tests/test_web_job_runner.py tests/test_answer_verifier.py tests/test_gemini_grounding.py -q

Expected: PASS.

Commit: git add app/agents/web_search_agent.py app/jobs/web_job_runner.py app/jobs/answer_job_runner.py app/models/job_schemas.py app/config.py tests/test_web_search_agent.py tests/test_web_job_runner.py && git commit -m "feat: add verified Flash web search agent"

### Task 6: Promote web to an explicit orchestrator route

**Files:**

- Modify: app/agents/orchestrator.py
- Create: app/agents/query_resolver.py
- Modify: app/agents/qdrant_agent.py
- Modify: app/core/response_formatter.py
- Create: tests/test_orchestrator_web_routing.py
- Modify: tests/test_router.py

**Step 1: Write the routing matrix as failing tests**

Required results:

- “현재 대한민국 대통령은?” -> web.
- “OpenAI CEO 누구야?” -> web.
- “SKIN1004 최신 뉴스” -> web; current/latest must override the static company no-search guard.
- “회사 소개해줘” -> direct.
- “HTTP 404와 500 차이?” -> direct.
- A 30+ character stable question ending in ? -> direct.
- “2024년 유로 우승팀” -> web only when explicitly asking to verify/search; a year alone must not force search.
- “이번 달 외부 트렌드와 우리 매출 비교” -> multi.
- Context-dependent follow-up resolves history before route/search.
- None and [] source selection preserve current default routing; selecting exactly one of Notion, GWS, or CS preserves the existing single-route fast path instead of becoming {selected, web}.
- @@/team-resource selection stays internally scoped unless the user explicitly asks to combine it with external current evidence, in which case the result is multi.
- Streaming and non-streaming entry points make the same routing/search decision and preserve the same selected-source behavior.

Run: pytest tests/test_orchestrator_web_routing.py -q

Expected: FAIL.

**Step 2: Add an orthogonal web capability and context resolver**

Add web to the classifier schema, runner registry, source event, and formatter mapping, but do not append it to _allowed_routes or any set used to count explicitly selected data sources. Keep allowed_data_routes and web_allowed as separate inputs. Preserve the existing single-source fast path for Notion/GWS/CS/team resources; only construct multi when the resolved question explicitly requires both an internal route and external evidence. External current questions with no exclusive internal selection route to _handle_web(). Remove hidden search from _handle_direct() after compatibility tests pass.

Create query_resolver.py with a separate context-aware contract returning resolved_query, referenced_turn_ids, confidence, and fallback_reason. It may resolve “그 사람/아까/그거” from cleaned history, but the deterministic search classifier receives that result rather than pretending resolution itself is deterministic. At low confidence, retain the original wording plus bounded relevant history in the web prompt and mark the uncertainty; do not silently bind to an arbitrary entity.

Replace _needs_web_search() with a deterministic classification result containing needs_search, reason, and depth. Explicit current/search signals are evaluated before static-company exclusions. Remove the long-question-plus-question-mark fallback.

**Step 3: Unify multi and Notion fallback callers**

The multi external branch and the Qdrant low-score external fallback call WebSearchAgent instead of invoking get_flash_client().generate_with_search independently. This eliminates three diverging search policies.

Remove any formatter behavior that labels an answer as based on Google search when the typed search result is failed/unverified or absent. Footers and badges derive from metadata status, never from the requested route alone.

**Step 4: Run tests and commit**

Run: pytest tests/test_orchestrator_web_routing.py tests/test_router.py tests/test_web_search_agent.py -q

Expected: PASS.

Commit: git add app/agents/orchestrator.py app/agents/query_resolver.py app/agents/qdrant_agent.py app/core/response_formatter.py tests/test_orchestrator_web_routing.py tests/test_router.py && git commit -m "feat: route current external questions through web agent"

### Task 7: Transport citations and verification as typed stream metadata

**Files:**

- Modify: app/models/schemas.py
- Modify: app/api/routes.py
- Modify: app/api/job_api.py
- Create: tests/test_streaming_search_metadata.py
- Create: tests/test_openai_compat_web_metadata.py

**Step 1: Write failing stream tests**

Assert durable browser event order:

1. source route web.
2. progress/search status.
3. answer chunks only after verification for factual web answers.
4. a sources metadata event with IDs/titles/safe URIs/support spans.
5. a Google search-entry-point event when renderedContent is returned.
6. a verification metadata event.
7. terminal completion.

Search failure with no usable answer emits failed; a usable but incompletely supported answer emits completed with partial/unverified metadata. Neither case renders a verified footer.

Use one terminal rule across transports: usable answer text -> completed with verified|grounded|partial|unverified metadata; no usable answer -> failed with structured error and no invented answer; explicit stop -> cancelled. Include cancelled in durable SSE, browser state, non-stream error mapping, and OpenAI-compatible stream tests.

Also assert three explicit transport contracts:

- Native durable browser API: named SSE events replay byte-equivalent typed web metadata from agent_job_events. Each web event is attempt-scoped; answer_reset clears all superseded web metadata as well as text.
- Non-stream /v1/chat/completions: the response retains answer plus an x_skin1004.web extension containing sources, supports, search_entry_point, and verification instead of app/api/routes.py discarding everything except answer.
- Legacy OpenAI-compatible stream: every data frame remains a valid chat.completion.chunk. Put optional metadata only under the x_skin1004 extension of a valid final chunk; never interleave a standalone custom JSON shape. Exercise the endpoint through the installed OpenAI SDK consumer and assert normal text iteration still works.

Run: pytest tests/test_streaming_search_metadata.py -q

Expected: FAIL.

**Step 2: Add typed metadata without breaking compatibility**

For /api/agent-jobs/{id}/events, emit the named persisted web_sources, google_search_entry_point, and web_verification events before the terminal event, all with generation_attempt_id. Its reset/status snapshot includes active generation, result text, sources, supports, verification, and search_entry_point; after event pruning, it rebuilds this from the preserved assistant response_metadata_json. For /v1/chat/completions, preserve valid OpenAI response/chunk envelopes and use the documented x_skin1004 extension in non-stream/final-stream responses. Do not encode sources only in HTML comments or ask the final LLM to reproduce URLs. Browser code consumes the durable native API, not the compatibility stream.

**Step 3: Run tests and commit**

Run: pytest tests/test_streaming_search_metadata.py tests/test_openai_compat_web_metadata.py tests/test_job_sse.py tests/test_router.py -q

Expected: PASS.

Commit: git add app/models/schemas.py app/api/routes.py app/api/job_api.py tests/test_streaming_search_metadata.py tests/test_openai_compat_web_metadata.py && git commit -m "feat: stream grounded source metadata"

### Task 8: Render safe citations and verification state in the browser

**Files:**

- Modify: app/frontend/chat.js
- Modify: app/static/style.css
- Modify: app/frontend/chat.html
- Create: tests/frontend/test_search_citations.py
- Create: tests/e2e/test_web_job_reconnect_playwright.py

**Step 1: Write failing frontend tests**

Test:

- Sources render from metadata, not Markdown supplied by the model.
- Link labels are escaped and href accepts only safe https URLs.
- Claim markers map to the correct source card.
- When Google returns search_entry_point.renderedContent, Search Suggestions appear immediately below the answer, survive durable replay, and are not silently dropped.
- Provider renderedContent is isolated in a size-capped sandboxed iframe/srcdoc (no scripts, forms, same-origin, or top navigation; only the minimum link-opening capability). Arbitrary answer/source HTML can never enter that channel.
- verified, grounded/source-linked, partial, and unverified badges are distinct and use non-overclaiming Korean labels.
- Search in progress is shown as web, not direct.
- Replayed durable-job metadata renders identically after refresh.
- A new tab with empty/corrupt sessionStorage restores completed citations, verification, and Search Suggestions from MessageOut.response_metadata/source_job_id even after detailed events are pruned.
- answer_reset switches the active generation and removes all text, source cards, verification badges, and Search Suggestions from the superseded attempt before rendering replacement events.
- A sequence gap triggers API backfill before later events render.

Run: pytest tests/frontend/test_search_citations.py -q

Expected: FAIL.

**Step 2: Implement source cards and badges**

Keep citations compact under the answer. Partial/unverified results show their warnings adjacent to the affected answer, not only in logs. Render Google's opaque Search Suggestions exactly from the typed provider field in an isolated sandbox immediately below the corresponding answer, per the Google grounding display requirement; if the field is absent, render no placeholder. Add CSP/sandbox and provenance tests rather than feeding provider HTML into the main DOM sanitizer.

Add native browser E2E cases for: enqueue committed but the 202 response is lost (idempotent recovery), worker crash after each of web_sources/web_verification/google_search_entry_point, answer_reset into a replacement attempt, expired auth during reconnect, offline/online recovery, two tabs watching the same job, cleared/corrupt sessionStorage, completed-message restoration after event pruning, cancellation racing completion, and a sequence gap. Assert monotonic rendered sequence, only the active generation's metadata, exactly one answer bubble per tab, exactly one assistant DB row, visible login/retry recovery, and local replay restoration within two seconds p95 under the test environment.

Increment app/frontend/chat.html from style.css?v=147 and chat.js?v=224 to the next versions used by the merged durable-job and web changes.

**Step 3: Run tests and commit**

Run: pytest tests/frontend/test_search_citations.py tests/frontend/test_job_reconnect.py tests/e2e/test_web_job_reconnect_playwright.py -q

Expected: PASS.

Commit: git add app/frontend/chat.js app/static/style.css app/frontend/chat.html tests/frontend/test_search_citations.py tests/e2e/test_web_job_reconnect_playwright.py && git commit -m "feat: display grounded web citations"

### Task 9: Deploy and verify the explicit web route

**Files:**

- Create: docs/web-search-runbook.md
- Create: tests/integration/test_durable_web_replay.py
- Create: scripts/verify_grounded_web.py

**Step 1: Full regression**

Run: pytest tests/test_search_schemas.py tests/test_gemini_grounding.py tests/test_source_resolver.py tests/test_answer_verifier.py tests/test_web_search_agent.py tests/test_web_job_runner.py tests/test_orchestrator_web_routing.py tests/test_streaming_search_metadata.py tests/test_openai_compat_web_metadata.py tests/frontend/test_search_citations.py tests/e2e/test_web_job_reconnect_playwright.py tests/test_router.py tests/test_sql_agent.py -q

Run: git diff --check

**Step 2: Paid integration on development**

Before paid calls, create tests/integration/test_durable_web_replay.py: enqueue a web job, receive partial answer/source/verification/Search Suggestions events, disconnect, simulate worker lease recovery, observe answer_reset and a replacement generation, then reconnect/refresh. Assert only the active generation is rendered, the terminal MessageOut envelope and reset/status snapshot restore byte-identical final answer text/claim markers/source IDs/URIs/badge/Search Suggestions even after detailed-event pruning, event sequence remains monotonic, and exactly one assistant message exists.

Run: pytest tests/integration/test_durable_web_replay.py -q

Run: python scripts/benchmark_web_models.py --base-url http://127.0.0.1:3001 --paths flash-lookup,flash-opus-research,multi-external,gemini-2.5-pro-search,claude-opus-native-search,claude-sonnet-5-native-search --repeats 3 --randomize-order --save-raw-sanitized --allow-paid-api

Run: python scripts/verify_grounded_web.py --base-url http://127.0.0.1:3001 --allow-paid-api

Both scripts must exit nonzero when fabricated URLs exceed zero, required-search failures exceed the configured budget, a stable control searches, required-claim/source thresholds fail, Google Search Suggestions returned by the provider disappear from transport/UI, replay gaps occur, duplicate assistant rows exist, or lease recovery exceeds two lease periods. Score and gate simple, composite, and real multi paths separately; do not use raw Opus-native results as a proxy for Flash -> Opus synthesis. Do not average away a fabricated URL or unsupported current number.

**Step 3: Deploy with project policy**

Use pm2 restart, never reload. Restart skin1004-dev for final verification, then pm2 restart skin1004-prod. Do not stop, delete, or kill production.

**Step 4: Production smoke checks**

On port 3000 verify one static no-search control, one official current lookup, and one internal+external multi query. Confirm route badges, citations, verification state, and PM2 restart counters/logs.

**Step 5: Commit runbook**

Commit: git add docs/web-search-runbook.md tests/integration/test_durable_web_replay.py scripts/verify_grounded_web.py && git commit -m "docs: add grounded web search runbook"

## Acceptance criteria

- External current lookups no longer perform Gemini Pro search followed by an unconditional Opus rewrite.
- Gemini 3.5 Flash is the default web lookup model and returns structured grounding sources.
- Every displayed current factual claim is verified, grounded/source-linked, explicitly partial, or explicitly unverified; grounded is never labeled verified.
- Search metadata survives API transport, durable replay, and browser refresh.
- Search metadata is generation-scoped; retries atomically supersede old text, citations, verification, and Search Suggestions together.
- Completed assistant messages preserve their terminal typed web envelope beyond event/job retention, while failed/no-answer and cancelled remain distinct terminal states.
- Google search_entry_point metadata survives persistence and renders as isolated Search Suggestions when returned.
- Stable questions do not search merely because they are long or end with a question mark.
- SKIN1004 current/news questions do search despite the company-name static guard.
- Explicit Notion/GWS/CS/@@ selection retains its existing single-source behavior; web permission remains orthogonal and creates multi only for an explicit internal+external request.
- Sonnet 5 has a real native-search smoke result, its production block parser is separately fixed, and it is not selected as default until the larger deployable-path benchmark passes.
- The Interactions API remains out of scope until a separate SDK 2.x migration plan and port-3001 benchmark pass.
