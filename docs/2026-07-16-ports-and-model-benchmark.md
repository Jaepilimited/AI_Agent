# 3000/3001 Architecture and Model Benchmark Review

**Date:** 2026-07-16 KST

## Executive conclusion

- Port 3000 and 3001 run the same application architecture and model configuration. The material development difference is `BQ_FAST_ANSWER=1` and `BQ_TOOL_LOOP=0` on 3001.
- The current direct external-question path is unnecessarily serial: Gemini 2.5 Pro gathers a text-only search summary, then Claude Opus 4.8 rewrites it. This blocks first output and discards Gemini grounding metadata.
- Use Gemini 3.5 Flash + Google Search as the default single-stage lookup path. For genuinely complex current research, use Flash evidence collection -> Opus 4.8 structured synthesis -> deterministic re-verification.
- Do not use Gemini 2.5 Pro or Claude Sonnet 5 as the default web-search model based on this smoke benchmark. Keep Pro only for unrelated workloads that pass their own evaluation.
- “No timeout” should be implemented as a durable server-side job with persisted replay, not one immortal browser connection. Individual dead network attempts still need watchdogs; there is no overall answer deadline that discards a running result.

## Current 3000/3001 architecture

| Area | Port 3000 production | Port 3001 development | Finding |
|---|---|---|---|
| Codebase/orchestrator | Same local code | Same local code | No architecture fork |
| Model environment | Same configured model family | Same configured model family | No model A/B by port |
| BigQuery mode | Normal production settings | `BQ_FAST_ANSWER=1`, `BQ_TOOL_LOOP=0` | Main behavioral difference |
| External direct path | Gemini 2.5 Pro search summary -> Opus 4.8 final answer | Same | Two serial model stages |
| Grounding metadata | Reduced to `response.text` | Same | Sources/support spans are lost |

Important code-review findings:

- Search failure currently becomes a truthy warning string, so the final model can answer from memory while the UI implies search occurred.
- The company-name no-search guard can suppress queries such as “SKIN1004 최신 뉴스.”
- A long stable question ending in `?`, or a year by itself, can trigger an unnecessary search.
- Direct, multi, and Qdrant fallback paths use different search policies.
- Search content is inserted into a high-trust system block instead of a delimited untrusted evidence block.

## Actual external-search API smoke benchmark

The benchmark used three Korean current-fact prompts with official ground truth: the current Korean president and inauguration date, the applicable 2026 minimum wage and period, and the current Chrome Windows/Mac Stable version and release date. Korean prompt UTF-8 integrity was verified before these runs; earlier PowerShell-corrupted `?` prompts were discarded and are not included.

| Model/path | Required facts correct | Median completion | Observed issue |
|---|---:|---:|---|
| Gemini 3.5 Flash + Google Search, thinking off | 3/3 | 4.101 s | Best speed/accuracy balance in this smoke set |
| Gemini 2.5 Pro + Google Search | 2/3 | 6.017 s | Reported Chrome Early Stable 151 as general Stable |
| Claude Sonnet 5 native web search, low effort | 1/3 | 7.453 s | Wrong inauguration date; omitted minimum-wage amount |
| Claude Opus 4.8 native web search, low effort | 3/3 facts | 8.178 s | One correct wage answer weakened itself with an evidence caveat |

Sonnet 5 was called with native `web_search_20260318`, forced official-domain search, low effort, and thinking disabled. This confirms it was actually tested; the result is a small smoke sample, not a permanent model ranking.

## Actual SQL-generation benchmark

Six SKIN1004 cases checked business rules and local SQL validation.

| Model/path | Median | Result |
|---|---:|---|
| Gemini 3.5 Flash | 2.315 s | 6/6 |
| Claude Sonnet 5 with correct text-block extraction | 3.327 s | 97.7%; one wrong 2025 Q4 Mega Wari date |
| Claude Opus 4.8 | 4.216 s | Effectively 6/6 |
| Gemini 2.5 Pro | 7.688 s | About 5/6; one truncated/missing TOP 10 answer |

The current Claude adapter assumes `response.content[0].text`; Sonnet/Opus responses may begin with a ThinkingBlock. With the production adapter unchanged, Sonnet failed 2/6 SQL cases. Model quality and adapter parsing must therefore be measured separately.

## Recommended production policy

1. Stable/internal knowledge: direct route without web search.
2. Simple changing external fact: Gemini 3.5 Flash grounded lookup returned directly with typed sources and verification state.
3. Incomplete grounding: one focused Flash retry for missing claims.
4. Complex multi-source/current analysis: parallel Flash evidence -> Opus 4.8 structured claim synthesis -> independent source/claim verification.
5. No usable evidence: visible failed result. Usable but incomplete evidence: completed answer labeled partial/unverified. Never label a memory answer as verified search.
6. Gemini Pro is not the escalation tier for web search. Sonnet 5 remains a benchmark candidate until a larger repeated suite passes.

## Runtime-test limitation

Direct provider API calls above were real. A real HTTP comparison through ports 3000 and 3001 could not be run in this session: no listener was visible on either port, and sandbox permissions denied PM2 socket/log access. The implementation plans therefore require an authenticated port-3001 browser/HTTP gate before production restart.

## Official model/tool references

- [Gemini Google Search grounding](https://ai.google.dev/gemini-api/docs/google-search)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Claude web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- [Claude effort controls](https://platform.claude.com/docs/en/build-with-claude/effort)
- [Claude Sonnet 5 release notes](https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5)

## Statistical limitation

Three current prompts are enough to reject an obviously worse default candidate, but not enough to certify a permanent winner. Promotion must use at least 20 distinct current prompts and 10 stable no-search controls, three randomized repeats per deployable path, fresh official gold, p50/p95, confidence intervals, source coverage, fabricated-URL rate, false-search rate, and cost.
