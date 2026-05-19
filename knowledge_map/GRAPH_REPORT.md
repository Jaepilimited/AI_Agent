# SKIN1004 AI Agent — Knowledge Map
**Generated**: 2026-05-19T03:00:21.678073+09:00 · **Files**: 126 · **Nodes**: 558 · **Edges**: 1048 · **Commit**: 1c04c52

## Clusters
- **cluster_00** — 1 nodes
- **cluster_01** — 1 nodes
- **cluster_02** — 20 nodes
- **cluster_03** — 1 nodes
- **cluster_04** — 1 nodes
- **cluster_05** — 1 nodes
- **cluster_06** — 1 nodes
- **cluster_07** — 1 nodes
- **cluster_08** — 1 nodes
- **cluster_09** — 1 nodes
- **cluster_10** — 1 nodes
- **cluster_11** — 40 nodes
- **cluster_12** — 1 nodes
- **cluster_13** — 1 nodes
- **cluster_14** — 1 nodes
- **cluster_15** — 21 nodes
- **cluster_16** — 1 nodes
- **cluster_17** — 1 nodes
- **cluster_18** — 1 nodes
- **cluster_19** — 1 nodes
- **cluster_20** — 37 nodes
- **cluster_21** — 1 nodes
- **cluster_22** — 1 nodes
- **cluster_23** — 40 nodes
- **cluster_24** — 42 nodes
- **cluster_25** — 1 nodes
- **cluster_26** — 1 nodes
- **cluster_27** — 27 nodes
- **cluster_28** — 1 nodes
- **cluster_29** — 1 nodes
- **cluster_30** — 1 nodes
- **cluster_31** — 1 nodes
- **cluster_32** — 1 nodes
- **cluster_33** — 1 nodes
- **cluster_34** — 90 nodes
- **cluster_35** — 1 nodes
- **cluster_36** — 39 nodes
- **cluster_37** — 1 nodes
- **cluster_38** — 1 nodes
- **cluster_39** — 1 nodes
- **cluster_40** — 1 nodes
- **cluster_41** — 20 nodes
- **cluster_42** — 1 nodes
- **cluster_43** — 1 nodes
- **cluster_44** — 26 nodes
- **cluster_45** — 1 nodes
- **cluster_46** — 1 nodes
- **cluster_47** — 1 nodes
- **cluster_48** — 1 nodes
- **cluster_49** — 1 nodes
- **cluster_50** — 1 nodes
- **cluster_51** — 13 nodes
- **cluster_52** — 1 nodes
- **cluster_53** — 22 nodes
- **cluster_54** — 1 nodes
- **cluster_55** — 1 nodes
- **cluster_56** — 41 nodes
- **cluster_57** — 1 nodes
- **cluster_58** — 1 nodes
- **cluster_59** — 1 nodes
- **cluster_60** — 1 nodes
- **cluster_61** — 1 nodes
- **cluster_62** — 1 nodes
- **cluster_63** — 17 nodes
- **cluster_64** — 1 nodes
- **cluster_65** — 1 nodes
- **cluster_66** — 1 nodes
- **cluster_67** — 1 nodes
- **cluster_68** — 1 nodes
- **cluster_69** — 1 nodes
- **cluster_70** — 1 nodes
- **cluster_71** — 1 nodes
- **cluster_72** — 1 nodes
- **cluster_73** — 1 nodes
- **cluster_74** — 1 nodes
- **cluster_75** — 1 nodes
- **cluster_76** — 1 nodes
- **cluster_77** — 1 nodes

## God Nodes
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/routes.py` (file) — OpenAI-compatible API endpoints for Open WebUI integration.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/auth_api.py` (file) — Authentication endpoints: signup, signin, me, logout.

Uses MariaDB for user sto
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/sql_agent.py` (file) — Text-to-SQL Agent using LangGraph.

Workflow: generate_sql → validate_sql → exec
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/main.py` (file) — SKIN1004 Enterprise AI - FastAPI application entry point.

Single server on port
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/builder.py` (file) — Knowledge Map build orchestrator — discover → cache → parse → flash → graph → ex
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/admin_group_api.py` (file) — Admin endpoints: AD user & group management (MariaDB).
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/conversation_api.py` (file) — Conversation CRUD API for chat history (MariaDB).
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/face_clip_agent.py` (file) — 얼굴/제품 사진 검색 — Drive 인덱스 기반 CLIP + InsightFace.

data/face_clip_index.npy + face_

## How to navigate
Read this file first, then open graph.json and follow wiki_page fields. Never Grep without consulting this map.
