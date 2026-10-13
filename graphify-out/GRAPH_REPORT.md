# Graph Report - .  (2026-05-14)

## Corpus Check
- 10 files · ~5,000 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 233 nodes · 327 edges · 14 communities detected
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 27 edges (avg confidence: 0.64)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Notion Agent Core|Notion Agent Core]]
- [[_COMMUNITY_API Routes & Scheduler|API Routes & Scheduler]]
- [[_COMMUNITY_App Startup & Lifecycle|App Startup & Lifecycle]]
- [[_COMMUNITY_Qdrant Pipeline (CrawlEmbed)|Qdrant Pipeline (Crawl/Embed)]]
- [[_COMMUNITY_Qdrant Vector Store|Qdrant Vector Store]]
- [[_COMMUNITY_Team Resources Crawl|Team Resources Crawl]]
- [[_COMMUNITY_Qdrant Sync (Legacy)|Qdrant Sync (Legacy)]]
- [[_COMMUNITY_Notion Upload|Notion Upload]]
- [[_COMMUNITY_Playwright Crawler|Playwright Crawler]]
- [[_COMMUNITY_Notion Agent v6.2|Notion Agent v6.2]]
- [[_COMMUNITY_Notion MCP (Deprecated)|Notion MCP (Deprecated)]]
- [[_COMMUNITY_Knowledge Map Viewer|Knowledge Map Viewer]]
- [[_COMMUNITY_Notion API Utils|Notion API Utils]]
- [[_COMMUNITY_embed_graph Script|embed_graph Script]]

## God Nodes (most connected - your core abstractions)
1. `NotionAgent` - 39 edges
2. `sync_source()` - 10 edges
3. `NotionAgent Class` - 9 edges
4. `crawl_target()` - 7 edges
5. `run_incremental_sync()` - 7 edges
6. `Notion REST API v1 (api.notion.com)` - 7 edges
7. `run()` - 6 edges
8. `chat_completions()` - 6 edges
9. `reload_vectors()` - 5 edges
10. `_search()` - 5 edges

## Surprising Connections (you probably didn't know these)
- `SKIN1004 Enterprise AI - FastAPI application entry point.  Single server on po` --uses--> `NotionAgent`  [INFERRED]
  app\main.py → app\agents\notion_agent.py
- `Create and configure the FastAPI application.` --uses--> `NotionAgent`  [INFERRED]
  app\main.py → app\agents\notion_agent.py
- `Ensure jeffrey@skin1004korea.com is admin with all models in MariaDB.` --uses--> `NotionAgent`  [INFERRED]
  app\main.py → app\agents\notion_agent.py
- `Create audit_logs table if it doesn't exist (MariaDB or SQLite).` --uses--> `NotionAgent`  [INFERRED]
  app\main.py → app\agents\notion_agent.py
- `Pre-load BigQuery schemas (sales + all marketing tables) into per-table cache at` --uses--> `NotionAgent`  [INFERRED]
  app\main.py → app\agents\notion_agent.py

## Hyperedges (group relationships)
- **Notion Vector Search Pipeline** — pipeline_run_pipeline, pipeline_run_incremental_sync, pipeline_embed_texts, pipeline_update_local_json, qdrant_agent_reload_vectors, qdrant_agent_local_json [EXTRACTED 1.00]
- **NotionAgent Query Flow** — notion_agent_NotionAgent, notion_agent_search_pages, notion_agent_read_blocks, notion_agent_read_database, notion_agent_generate_answer, gemini_flash_client [EXTRACTED 1.00]
- **APScheduler Daily Cron Jobs** — main_apscheduler, main_qdrant_pipeline_job, main_sync_team_resources_job, pipeline_run_pipeline [EXTRACTED 1.00]
- **FastAPI Startup Warmup Tasks** — main_lifespan, main_warmup_notion, main_warmup_qdrant, notion_agent_warmup, qdrant_agent_load_store [EXTRACTED 1.00]
- **Qdrant Cloud vs Local JSON Architecture Decision** — qdrant_agent_local_json, qdrant_cloud_collection, pipeline_upload_to_qdrant_cloud, pipeline_local_json_path [EXTRACTED 0.95]
- **All Notion API Consumers** — notion_agent_NotionAgent, notion_mcp_client, pipeline_crawl_target, qdrant_sync_sync_source, upload_to_notion_upload_log, crawl_notion_pages_playwright [INFERRED 0.85]
- **Gemini Embedding Users** — qdrant_agent_embed_query, pipeline_embed_texts, gemini_embedding_model [EXTRACTED 1.00]

## Communities

### Community 0 - "Notion Agent Core"
Cohesion: 0.07
Nodes (24): Pre-fetch Notion allowlist titles at startup so first query is fast., _warmup_notion_titles(), NotionAgent, Generate answer from Notion content using Flash for speed.          Answer forma, Close the shared client after a run() call., Execute an HTTP request with retry on transient errors.          Args:, Search Notion and generate an answer.          Args:             query: User que, Fetch real Notion titles for each allowlisted page/database.          Makes one (+16 more)

### Community 1 - "API Routes & Scheduler"
Cohesion: 0.07
Nodes (34): _get_scheduler(), chat_completions(), dashboard(), _estimate_tokens(), get_announcement(), _get_orchestrator(), health_check(), list_datasources() (+26 more)

### Community 2 - "App Startup & Lifecycle"
Cohesion: 0.06
Nodes (30): APScheduler Instance, create_app(), _ensure_admin(), _ensure_audit_table(), _extract_wiki_hourly(), FastAPI Lifespan (Startup/Shutdown), _qdrant_pipeline_job(), SKIN1004 Enterprise AI - FastAPI application entry point.  Single server on po (+22 more)

### Community 3 - "Qdrant Pipeline (Crawl/Embed)"
Cohesion: 0.14
Nodes (23): chunk_text(), crawl_database_pages(), crawl_page_blocks(), crawl_target(), embed_texts(), get_local_page_map(), main(), notion_headers() (+15 more)

### Community 4 - "Qdrant Vector Store"
Cohesion: 0.12
Nodes (23): gemini-embedding-001 (1536-dim), crawl_target(), DATABASE_TARGETS (14 Notion Sources), embed_texts() - Gemini Batch Embedding, data/notion_vectors_gemini.json (Source of Truth), run_incremental_sync(), run_pipeline(), update_local_json() (+15 more)

### Community 5 - "Team Resources Crawl"
Cohesion: 0.13
Nodes (18): get_pages_to_crawl(), crawl_page_playwright(), update_description() - MariaDB team_resources, Gemini Flash LLM Client, _warmup_notion_titles(), NotionAgent Class, Notion Allowlist (10 Pages), NotionAgent._generate_answer() (+10 more)

### Community 6 - "Qdrant Sync (Legacy)"
Cohesion: 0.21
Nodes (17): chunk_text(), crawl_database_pages(), crawl_page_blocks(), delete_source_points(), embed_texts(), get_source_count(), main(), notion_headers() (+9 more)

### Community 7 - "Notion Upload"
Cohesion: 0.27
Nodes (9): main(), md_to_blocks(), md_to_rich_text(), Upload markdown update log to Notion as a toggle block., Convert markdown inline formatting to Notion rich_text., Convert markdown content to Notion blocks., Notion Update Log Page (3252b428...), upload_update_log() (+1 more)

### Community 8 - "Playwright Crawler"
Cohesion: 0.31
Nodes (8): crawl_page_playwright(), get_pages_to_crawl(), main(), Crawl Notion page URLs via Playwright and update team_resources description., Update team_resources row with crawled content (always overwrite if new content, Get Notion page URLs with empty/short descriptions from team_resources.      A, Visit a Notion page with Playwright and extract text content., update_description()

### Community 9 - "Notion Agent v6.2"
Cohesion: 0.4
Nodes (3): _format_uuid(), Notion Sub Agent (v6.2).  Accesses Notion workspace via direct API calls (no MCP, Format a 32-char hex string as a Notion-style UUID (8-4-4-4-12).

### Community 10 - "Notion MCP (Deprecated)"
Cohesion: 0.5
Nodes (3): get_notion_mcp_tools(), Notion MCP Server connection., Return Notion MCP tools.

### Community 11 - "Knowledge Map Viewer"
Cohesion: 0.67
Nodes (3): knowledge_map/graph.json, embed_graph.py - Embed JSON into HTML Viewer, knowledge_map/graph_viewer.html

### Community 12 - "Notion API Utils"
Cohesion: 1.0
Nodes (1): Extract title from a Notion API page or database response.

### Community 13 - "embed_graph Script"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **75 isolated node(s):** `Notion Sub Agent (v6.2).  Accesses Notion workspace via direct API calls (no MCP`, `Format a 32-char hex string as a Notion-style UUID (8-4-4-4-12).`, `Return the shared httpx client, creating if needed.`, `Close the shared client after a run() call.`, `Execute an HTTP request with retry on transient errors.          Args:` (+70 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Notion API Utils`** (1 nodes): `Extract title from a Notion API page or database response.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `embed_graph Script`** (1 nodes): `embed_graph.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `NotionAgent` connect `Notion Agent Core` to `Notion Agent v6.2`, `App Startup & Lifecycle`?**
  _High betweenness centrality (0.318) - this node is a cross-community bridge._
- **Why does `run_pipeline()` connect `Qdrant Vector Store` to `App Startup & Lifecycle`?**
  _High betweenness centrality (0.297) - this node is a cross-community bridge._
- **Why does `_qdrant_pipeline_job()` connect `App Startup & Lifecycle` to `Qdrant Vector Store`?**
  _High betweenness centrality (0.295) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `NotionAgent` (e.g. with `SKIN1004 Enterprise AI - FastAPI application entry point.  Single server on po` and `Create and configure the FastAPI application.`) actually correct?**
  _`NotionAgent` has 16 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Notion Sub Agent (v6.2).  Accesses Notion workspace via direct API calls (no MCP`, `Format a 32-char hex string as a Notion-style UUID (8-4-4-4-12).`, `Return the shared httpx client, creating if needed.` to the rest of the system?**
  _75 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Notion Agent Core` be split into smaller, more focused modules?**
  _Cohesion score 0.07 - nodes in this community are weakly interconnected._
- **Should `API Routes & Scheduler` be split into smaller, more focused modules?**
  _Cohesion score 0.07 - nodes in this community are weakly interconnected._