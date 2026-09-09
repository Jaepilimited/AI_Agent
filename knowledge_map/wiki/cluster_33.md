# Cluster 33

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 2

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트 내에서 지식 맵(Knowledge Map)을 구축하고 시각화 및 문서화 형태로 내보내는 핵심 오케스트레이터와 내보내기(Exporter) 도구들로 구성되어 있습니다. 프로젝트 소스 코드와 문서를 탐색하여 지식 구조를 발견하고, 이를 정형화된 그래프 데이터 및 마크다운 위키 문서로 변환하는 역할을 수행합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/builder.py` — 지식 맵 구축의 전체 흐름(탐색 → 캐싱 → 파싱 → 플래시 → 그래프 생성 → 내보내기)을 제어하는 오케스트레이터 클래스입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/exporters.py` — 구축된 지식 데이터를 `graph.json`, `GRAPH_REPORT.md`, 그리고 `wiki/index.md`, `wiki/log.md`를 포함한 `wiki/*.md` 파일 등 다양한 포맷의 출력물로 작성하는 라이터(Writer) 모듈입니다.

## Key Concepts
- **Knowledge Map Build Pipeline**: `builder.py`가 주도하는 파이프라인으로, 프로젝트의 메타데이터와 코드 관계를 분석하여 구조화된 지식 그래프를 형성하는 일련의 프로세스(discover → cache → parse → flash → graph → export)입니다.
- **Multi-format Exporters**: 분석된 지식 맵을 기계가 읽을 수 있는 JSON 형식(`graph.json`)과 사용자가 읽을 수 있는 마크다운 위키 문서(`wiki/*.md`, `GRAPH_REPORT.md`)로 동시에 변환하여 프로젝트의 문서화를 자동화하는 기능입니다.

## How It Fits In
이 클러스터는 독립적인 지식 맵 생성 및 문서화 도구 세트로 동작합니다. 프로젝트 내의 다른 코드 베이스나 문서들을 정적 분석하여 시각화 가능한 그래프 데이터와 위키 페이지를 생성함으로써, 개발자와 AI Agent가 전체 시스템 구조를 빠르게 파악할 수 있도록 돕는 메타 도구 역할을 합니다.

## Common Questions This Page Answers
- 프로젝트의 전체 구조를 분석하여 시각화용 JSON 그래프나 마크다운 위키로 자동 생성하려면 어떤 모듈을 실행해야 하나요?
- 지식 맵 빌드 파이프라인의 구체적인 단계(discover, cache, parse 등)는 어떻게 제어되나요?
- 생성되는 위키 문서(`wiki/index.md`, `wiki/log.md` 등)와 리포트 파일들은 어디에서 정의되고 작성되나요?