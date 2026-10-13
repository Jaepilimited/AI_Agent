# Cluster 45

> Auto-generated 2026-10-10T03:01:40.898398+09:00 · Files: 2

## Purpose
`cluster_45`는 SKIN1004 AI Agent 프로젝트의 지식 맵(Knowledge Map)을 구축하고 결과를 다양한 파일 형태로 출력하는 핵심 파이프라인 모듈입니다. 코드베이스 내의 파일들을 탐색, 파싱, 분석하여 그래프 구조를 형성하고, 최종 산출물로 위키 문서 및 리포트를 내보내는 역할을 담당합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/builder.py` — 전체 지식 맵 빌드 오케스트레이터 (discover → cache → parse → flash → graph → export 파이프라인 실행)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/exporters.py` — 지식 맵 결과를 `graph.json`, `wiki/*.md`, `GRAPH_REPORT.md`, `wiki/index.md`, `wiki/log.md` 등의 파일로 출력하는 라이터

## Key Concepts
- **Knowledge Map Build Pipeline**: 소스 코드를 탐색(discover)하고 캐시(cache)를 확인한 뒤, AST/코드 파싱(parse) 및 모델 처리(flash)를 거쳐 지식 그래프(graph)를 생성하고 내보내는(export) 일련의 오케스트레이션 과정입니다.
- **Output Artifacts**: 파이프라인 수행 후 생성되는 산출물로, 기계 판독용 `graph.json`, 클러스터 및 전반적인 프로젝트 구조를 설명하는 위키 문서 모음(`wiki/*.md`, `wiki/index.md`), 실행 이력(`wiki/log.md`), 요약 리포트(`GRAPH_REPORT.md`)가 포함됩니다.

## How It Fits In
이 클러스터는 지식 맵 생성 및 문서화의 핵심 실행 엔트리포인트를 제공합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/builder.py`는 `cluster_11`의 `incremental_build_cache` 개념을 구현하여 변경된 파일만 효율적으로 처리합니다.
- 또한 `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/builder.py`는 `cluster_11`의 `cluster_wiki_synthesis` 개념을 구현함으로써 클러스터 단위의 위키 문서를 종합 및 렌더링하고, 이를 `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/exporters.py`를 통해 디스크에 기록합니다.

## Common Questions This Page Answers
- Knowledge Map 생성 파이프라인은 어떤 단계(discover, cache, parse, flash, graph, export)로 실행되는가?
- 빌드 결과물(그래프 데이터, 위키 페이지, 리포트)은 어떤 파일 경로와 포맷으로 생성되는가?
- 변경 사항에 대한 증분 빌드(incremental build cache) 및 위키 문서 합성(wiki synthesis)은 어떤 모듈에서 오케스트레이션되는가?