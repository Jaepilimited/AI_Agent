# SKIN1004 AI Agent — Knowledge Map
**Generated**: 2026-08-10T03:00:26.007576+09:00 · **Files**: 144 · **Nodes**: 1323 · **Commit**: be6012c

## What this project is
This project is the SKIN1004 Enterprise AI Agent platform, a FastAPI-based system designed to integrate custom AI capabilities with internal enterprise systems. It features a robust Text-to-SQL engine powered by LangGraph to query corporate databases, Active Directory (AD) synchronization for user and department management, and an OpenAI-compatible API layer for seamless Open WebUI integration. The system also includes a self-healing diagnostic suite (`self_check.py`) to ensure continuous data integrity and system health.

## Top-level Clusters
1. **cluster_00** (70 files): Core database schemas, migration scripts, and connection pools for MariaDB.
2. **cluster_01** (16 files): Active Directory (AD) synchronization services and LDAP connection utilities.
3. **cluster_02** (6 files): PDF and document parsing utilities for internal knowledge base ingestion.
4. **cluster_03** (33 files): LangGraph state definitions, node configurations, and agentic workflow helpers.
5. **cluster_04** (124 files): Frontend static assets, UI components, and custom dashboard templates.
6. **cluster_05** (134 files): Unit, integration, and regression test suites for API endpoints and agent workflows.
7. **cluster_06** (44 files): Logging, telemetry, and performance monitoring middleware.
8. **cluster_07** (94 files): Prompt templates, system instructions, and LLM configuration files.
9. **cluster_08** (44 files): Background task workers, Celery configurations, and cron job definitions.
10. **cluster_09** (56 files): Vector database connectors (Chroma/Qdrant) and embedding generation pipelines.
11. **cluster_10** (80 files): Text-to-SQL agent workflows, SQL validation logic, and execution sandboxes.
12. **cluster_11** (177 files): Core API routing, user authentication, and OpenAI-compatible chat endpoints.
13. **cluster_12** (43 files): Admin management APIs for user access control and model permissions.
14. **cluster_13** (10 files): Rate limiting, CORS, and security middleware configurations.
15. **cluster_14** (30 files): Multi-language translation utilities and localization dictionaries.
16. **cluster_15** (24 files): File upload, storage adapters (S3/Local), and media processing pipelines.
17. **cluster_16** (56 files): Self-check diagnostic scripts, system health monitors, and alert dispatchers.
18. **cluster_17** (5 files): Docker, deployment scripts, and environment configuration templates.
19. **cluster_18** (25 files): Cache management utilities using Redis and memory-backed stores.
20. **cluster_19** (14 files): External API integrations (ERP, logistics, and shipping carriers).
21. **cluster_20** (25 files): Knowledge Map generation, code parsing, and documentation indexing tools.
22. **cluster_21** (29 files): Application entry points, server initializers, and global state managers.
23. **cluster_22** (2 files): Legacy migration scripts and deprecated utility wrappers.
24. **cluster_23** (6 files): Custom exception handlers and HTTP error formatters.
25. **cluster_24** (176 files): Auto-generated API documentation, OpenAPI schemas, and developer guides.

## God Nodes (highest edge count — most central)
- `app/agents/sql_agent.py` (10.md) — Text-to-SQL Agent using LangGraph with a structured generate → validate → execute → format workflow.
- `app/api/routes.py` (11.md) — Core OpenAI-compatible API endpoints facilitating integration with Open WebUI.
- `app/api/auth_api.py` (11.md) — Authentication endpoints (signup, signin, me, logout) linking MariaDB with AD department structures.
- `app/main.py` (21.md) — FastAPI application entry point hosting both the AI backend and custom frontend on port 3000.
- `app/api/admin_api.py` (12.md) — Admin endpoints managing user permissions and model access control lists in MariaDB.
- `app/core/self_check.py` (16.md) — Self-diagnostic system designed to catch silent failures like AD sync issues and database drift.
- `app/knowledge_map/builder.py` (20.md) — Knowledge Map build orchestrator managing code discovery, parsing, and graph exports.
- `app/api/admin_group_api.py` (11.md) — Admin endpoints for managing Active Directory users, groups, and department mappings.

## Suggested Questions This Map Can Answer Instantly
1. How does the Text-to-SQL agent validate generated SQL queries before execution? (`app/agents/sql_agent.py`)
2. Where are the OpenAI-compatible chat endpoints defined for Open WebUI integration? (`app/api/routes.py`)
3. How does the system map Active Directory (AD) departments to local MariaDB users during authentication? (`app/api/auth_api.py`)
4. What port and host configurations are used to launch the unified FastAPI server? (`app/main.py`)
5. How can an administrator restrict specific LLM models for certain user groups? (`app/api/admin_api.py`)
6. What daily checks does the self-diagnostic system run to prevent silent AD sync failures? (`app/core/self_check.py`)
7. How is the codebase parsed and indexed to generate the interactive Knowledge Map? (`app/knowledge_map/builder.py`)
8. Where are the API endpoints for managing AD group memberships and permissions? (`app/api/admin_group_api.py`)
9. How are database connection pools configured for MariaDB queries? (`knowledge_map/wiki/cluster_00.md`)
10. Where should I add a new background cron job for data synchronization? (`knowledge_map/wiki/cluster_08.md`)

## Recent Changes
- 2026-08-10 · Added automated Knowledge Map builder to track codebase architecture changes.
- 2026-08-04 · Implemented `self_check.py` to resolve silent Active Directory synchronization failures.
- 2026-07-28 · Integrated LangGraph-based Text-to-SQL agent with multi-step query validation.
- 2026-07-15 · Created OpenAI-compatible API routes to support custom Open WebUI pipelines.

## How to navigate
Read this file first. Then open graph.json and find the 2-3 nodes most relevant to your question. Read only those nodes' wiki_page values (`knowledge_map/wiki/**/*.md`). Only read original source files if the wiki page doesn't answer. Never Grep without consulting this map first.