# Cluster 35

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 5

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트의 PR 리스트(PR 이슈) 및 수상/랭킹 데이터, 그리고 Notion 연동 데이터를 벡터 데이터베이스(Qdrant)에 적재하고 관리하기 위한 구현 코드와 설계 문서를 다룹니다. 승인된 스프레드시트 탭과 외부 소스를 독립된 manifest 단위로 인덱싱하여 검색 복구 가능성을 보장하고 구조화된 지식 베이스를 구축합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/pr_issues.py` — 승인된 PR 리스트 탭 데이터를 읽어 독립적인 Qdrant manifest로 적재하며, 각 행을 개별 chunk로 관리하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/PR_LIST_RELEASE_20260909.md` — PR 리스트 탭 연결 및 릴리즈 내역 문서 (2026-09-09)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/specs/2026-09-07-pr-awards-ingestion-design.md` — PR 이슈 및 수상/랭킹 데이터 적재를 위한 2개 시트 기반 분리 설계 사양서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-09-07-awards-rankings-ingestion.md` — 수상/랭킹 데이터 적재 1단계 구현 계획서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-05-14-notion-sync-skill.md` — 외부 지식 동기화를 위한 Notion Sync Skill 구현 계획서

## Key Concepts
- **PR 리스트 / PR 이슈 (PR Issues)** — 승인된 PR 관련 시트 데이터로, 각 행 단위로 chunking되어 Qdrant 포인트로 변환되는 데이터 소스
- **Recoverable Qdrant Manifest** — 특정 데이터 소스의 vector point들을 다른 소스와 공유하지 않고 자체 관리 파일 단위로 분리하여 복구 및 관리가 가능하도록 한 구조
- **수상/랭킹 적재 (Awards/Rankings Ingestion)** — 브랜드 및 제품 관련 수상·랭킹 정보를 전용 파이프라인을 통해 Qdrant에 인덱싱하는 작업
- **Notion Sync Skill** — 외부 노션 문서를 에이전트 지식 기반과 동기화하기 위한 기능 스펙

## How It Fits In
이 클러스터는 외부 데이터 소스(스프레드시트 탭, Notion 등)를 벡터 검색이 가능한 형태로 표준화하여 인덱싱하는 인제스천(Ingestion) 파이프라인의 핵심입니다. 타 클러스터와의 직접적인 의존 관계는 감지되지 않았으나, 적재된 Qdrant 포인트들은 추후 에이전트가 PR 이슈 확인 및 수상 실적 질의를 처리할 때 벡터 검색 엔진의 기반 데이터로 활용됩니다.

## Common Questions This Page Answers
- PR 리스트 시트의 데이터는 Qdrant에 어떤 방식으로 청킹(chunking) 및 저장되는가?
- PR 이슈와 수상/랭킹 데이터 적재 설계 시트의 차이점 및 요구 사양은 어디에 명시되어 있는가?
- Notion 데이터를 에이전트 시스템에 연동하기 위한 초기 구현 계획은 어떤 파일에서 확인할 수 있는가?