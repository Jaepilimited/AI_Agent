# Cluster 46

> Auto-generated 2026-10-07T03:01:58.998564+09:00 · Files: 2

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트의 코드베이스 분석 및 Knowledge Map을 구축하고 내보내는 오케스트레이션 계층입니다. 전체 소스 코드의 탐색부터 구문 분석, 그래프 생성, 그리고 최종 문서화 산출물 작성까지의 전체 파이프라인을 실행합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/builder.py` — Knowledge Map 파이프라인 오케스트레이터 (`discover` → `cache` → `parse` → `flash` → `graph` → `export` 단계 순차 수행).
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/exporters.py` — 분석 결과 산출물 작성기 (`graph.json`, `wiki/*.md`, `GRAPH_REPORT.md`, `wiki/index.md`, `wiki/log.md` 파일 생성).

## Key Concepts
- **Knowledge Map Build Pipeline**: 소스 파일 검색(`discover`), 캐싱(`cache`), 파싱(`parse`), 추출 및 그래프화(`flash`, `graph`), 결과 출력(`export`)으로 이어지는 지식 그래프 구축 흐름.
- **Artifact Exporters**: 구성된 코드베이스 관계망을 시각화 및 문서화 데이터(`graph.json`, 위키 문서, 분석 리포트)로 변환하여 파일 시스템에 기록하는 모듈.
- **Graph & Wiki Outputs**: 프로젝트 구조를 요약하고 탐색할 수 있도록 제공하는 `GRAPH_REPORT.md`, `wiki/index.md`, `wiki/log.md` 및 개별 wiki 페이지들.

## How It Fits In
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/builder.py`는 클러스터 30의 `concept:graph_clustering`을 구현([implements])합니다.
- 전체 코드베이스 구조와 의존성을 분석하여 다른 도메인 클러스터나 에이전트 시스템이 참조할 수 있는 지식 맵 산출물(JSON 및 Wiki 문서)을 공급하는 역할을 담당합니다.

## Common Questions This Page Answers
- Knowledge Map 생성 파이프라인은 어떤 단계(`discover` → `cache` → `parse` → `flash` → `graph` → `export`)로 실행되는가?
- Knowledge Map 생성 과정에서 최종적으로 어떤 파일들(`graph.json`, `wiki/*.md`, `GRAPH_REPORT.md` 등)이 출력되는가?
- 그래프 클러스터링 알고리즘 결과를 실제 위키 문서나 JSON 리포트로 변환하는 진입점은 어디인가?