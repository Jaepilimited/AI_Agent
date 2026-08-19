# SKIN1004 AI Agent — Knowledge Map
**Generated**: 2026-08-19T03:00:36.499205+09:00 · **Files**: 177 · **Nodes**: 1750 · **Commit**: 0040a93

## What this project is
The SKIN1004 AI Agent is an enterprise-grade AI backend and custom frontend system designed to support internal operations through advanced LLM capabilities. It features a robust Text-to-SQL engine powered by LangGraph, Active Directory (AD) synchronization, and self-checking diagnostic routines to ensure data integrity. The application exposes OpenAI-compatible endpoints for seamless integration with Open WebUI and other internal tools.

## Top-level Clusters
1. **cluster_00** (63 files): Core database schemas, migrations, and connection pools for MariaDB.
2. **cluster_01** (44 files): Active Directory (AD) integration, user synchronization, and department mapping logic.
3. **cluster_02** (82 files): Custom frontend components, assets, and UI layout templates.
4. **cluster_03** (76 files): LangGraph state definitions, node configurations, and agentic workflow utilities.
5. **cluster_04** (100 files): Prompt templates, system instructions, and LLM model configuration files.
6. **cluster_05** (161 files): Text-to-SQL validation, query parsing, and SQL execution safety layers.
7. **cluster_06** (44 files): Logging, telemetry, and performance monitoring middleware.
8. **cluster_07** (133 files): Document processing, vector embeddings, and RAG (Retrieval-Augmented Generation) pipelines.
9. **cluster_08** (67 files): Excel/CSV report generation, data export utilities, and file download handlers.
10. **cluster_09** (12 files): Multi-language translation utilities and localization dictionaries.
11. **cluster_10** (16 files): Background task queues, Celery workers, and scheduled job definitions.
12. **cluster_11** (69 files): Core SQL Agent implementation, LangGraph workflow orchestration, and query formatting.
13. **cluster_12** (42 files): Session management, Redis caching, and rate-limiting middleware.
14. **cluster_13** (193 files): Authentication, user registration, conversation history CRUD, and OpenAI-compatible API routes.
15. **cluster_14** (34 files): FastAPI application entry points, server startup/shutdown lifecycles, and global exception handlers.
16. **cluster_15** (48 files): Admin dashboards, user access control, and model permission management.
17. **cluster_16** (38 files): Unit tests, integration tests, and mock data generators.
18. **cluster_17** (17 files): Docker configurations, deployment scripts, and environment variable templates.
19. **cluster_18** (6 files): PDF parsing, OCR processing, and unstructured data extraction.
20. **cluster_19** (37 files): Slack/Teams notification webhooks and alert dispatchers.
21. **cluster_20** (41 files): Self-check diagnostics, system health monitoring, and data integrity regression tests.
22. **cluster_21** (10 files): API rate limiting, IP whitelisting, and security middleware.
23. **cluster_22** (48 files): Knowledge map generation, AST parsing, and codebase indexing utilities.
24. **cluster_23** (25 files): Knowledge map build orchestrator, caching, and graph export pipelines.
25. **cluster_24** (63 files): Custom LLM tool definitions, external API connectors, and web search tools.
26. **cluster_25** (5 files): Static assets, brand logos, and corporate identity files.
27. **cluster_26** (2 files): Markdown documentation, user guides, and developer onboarding manuals.
28. **cluster_27** (6 files): Database backup, recovery, and archival scripts.
29. **cluster_28** (6 files): Third-party API SDK wrappers and client initializers.
30. **cluster_29** (180 files): SQL query templates, metadata definitions, and database reflection schemas.
31. **cluster_30** (82 files): Chat UI components, markdown renderers, and streaming response handlers.

## God Nodes (highest edge count — most central)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/sql_agent.py` (112 edges) — Text-to-SQL Agent using LangGraph executing the generate_sql → validate_sql → execute_sql → format_answer workflow.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/admin_api.py` (98 edges) — Admin endpoints managing user permissions, model access control, and MariaDB configurations.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/main.py` (94 edges) — FastAPI application entry point hosting the unified AI backend and custom frontend on port 3000.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/auth_api.py` (89 edges) — Authentication endpoints handling signup, signin, and AD-linked department mapping in MariaDB.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/routes.py` (85 edges) — OpenAI-compatible API endpoints facilitating integration with Open WebUI.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/self_check.py` (81 edges) — Self-check diagnostic system preventing silent failures in AD synchronization and database integrity.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/builder.py` (76 edges) — Knowledge Map build orchestrator managing codebase discovery, AST parsing, and graph exports.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/conversation_api.py` (72 edges) — Conversation CRUD API managing chat history and session persistence in MariaDB.

## Suggested Questions This Map Can Answer Instantly
1. How does the Text-to-SQL agent validate generated SQL queries before execution? (`app/agents/sql_agent.py`)
2. Where are the OpenAI-compatible endpoints defined for Open WebUI integration? (`app/api/routes.py`)
3. How does the system handle Active Directory (AD) synchronization and department mapping? (`app/api/auth_api.py`)
4. What diagnostic checks are run daily to prevent silent failures in AD synchronization? (`app/core/self_check.py`)
5. How can I add a new admin endpoint for managing model access control? (`app/api/admin_api.py`)
6. Where is the main FastAPI application initialized and configured? (`app/main.py`)
7. How is chat history saved and retrieved from the MariaDB database? (`app/api/conversation_api.py`)
8. How does the codebase indexer parse files to build the knowledge map? (`app/knowledge_map/builder.py`)

## Recent Changes
- 2026-08-19 · Updated knowledge map builder to support incremental AST caching.
- 2026-08-12 · Enhanced SQL Agent validation layer to prevent destructive queries.
- 2026-08-04 · Implemented `self_check.py` to monitor and alert on AD synchronization failures.
- 2026-07-28 · Added OpenAI-compatible streaming endpoints to `routes.py`.

## How to navigate
Read this file first. Then open graph.json and find the 2-3 nodes most relevant to your question. Read only those nodes' wiki_page values (`knowledge_map/wiki/**/*.md`). Only read original source files if the wiki page doesn't answer. Never Grep without consulting this map first.