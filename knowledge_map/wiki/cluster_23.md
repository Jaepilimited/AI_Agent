# Cluster 23

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 2

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트의 전체 코드베이스 구조를 분석하고 시각화하기 위한 **Knowledge Map 빌드 및 내보내기(Export) 오케스트레이터** 역할을 수행합니다. 프로젝트 내 파일들을 탐색하고 구문 분석하여 의존성 그래프를 생성하고, 이를 다양한 문서 형식(Markdown, JSON 등)으로 출력하는 핵심 파이프라인을 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/builder.py` — 파일 탐색, 캐싱, 구문 분석, 그래프 생성 및 내보내기 단계를 순차적으로 실행하는 Knowledge Map 빌드 오케스트레이터입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/exporters.py` — 생성된 지식 그래프 데이터를 `graph.json`, `GRAPH_REPORT.md`, 개별 위키 문서(`wiki/*.md`), 그리고 위키 인덱스 및 로그 파일로 변환하여 저장하는 출력 라이터입니다.

## Key Concepts
- **Knowledge Map Builder**: 전체 소스 코드를 스캔하여 파일 간의 관계를 추적하고, 이를 구조화된 지식 맵으로 빌드하는 전체 공정(Discover → Cache → Parse → Flash → Graph → Export)을 제어합니다.
- **Exporters**: 빌드된 그래프 데이터를 기반으로 개발자와 AI Agent가 쉽게 읽을 수 있는 Markdown 형식의 위키 문서군과 시각화용 JSON 데이터를 생성하는 컴포넌트입니다.

## How It Fits In
이 클러스터는 프로젝트의 정적 분석 및 문서화 자동화의 중심 축입니다. 
- `builder.py`는 코드의 구조적 관계를 파악하기 위해 **cluster_07**의 `concept:ast_parsing`을 구현하여 개별 파일의 구문을 분석합니다.
- 분석된 의존성 데이터를 바탕으로 모듈 간의 연관 관계를 그룹화하기 위해 **cluster_07**의 `concept:graph_clustering` 알고리즘을 활용하여 클러스터를 식별하고 시각화 맵을 구성합니다.

## Common Questions This Page Answers
- **프로젝트의 전체 의존성 그래프와 위키 문서는 어떻게 자동으로 생성되나요?**
  - `builder.py`가 전체 파이프라인을 실행하며, 최종적으로 `exporters.py`를 통해 `wiki/` 디렉토리 아래에 Markdown 문서와 `graph.json` 파일로 내보내집니다.
- **Knowledge Map 빌드 과정에서 캐싱과 플래싱은 어떤 단계에서 일어나나요?**
  - `builder.py` 내에서 파일 탐색(Discover) 후 변경 사항을 캐싱(Cache)하고, 파싱(Parse)된 데이터를 디스크에 기록(Flash)한 뒤 그래프를 구축하는 흐름으로 진행됩니다.