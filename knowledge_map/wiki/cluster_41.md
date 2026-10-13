# Cluster 41

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 7

## Purpose
Cluster 41은 Claude Code 세션을 위한 정적 코드 및 문서 지식 그래프(Knowledge Map) 파이프라인을 정의합니다. 런타임 대화 팩트 마이닝인 `app.knowledge`와 분리되어, 프로젝트 전반의 파일들을 탐색·분석하고 위키 및 그래프 산출물로 내보내는 정적 분석 인프라를 담당합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/__init__.py` — Knowledge Map 패키지 진입점 및 모듈 역할 정의
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/builder.py` — 탐색부터 캐시, 파싱, 플래시 분석, 그래프 생성, 내보내기까지의 빌드 오케스트레이터
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/semantic.py` — Gemini Flash 기반으로 파일별 개념(concepts), 관계(relations), 요약(summary)을 추출하는 시맨틱 패스
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/exporters.py` — `graph.json`, `wiki/*.md`, `GRAPH_REPORT.md`, `wiki/index.md`, `wiki/log.md` 등 산출물 작성기
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/specs/2026-04-16-knowledge-map-design.md` — SKIN1004 Knowledge Map 아키텍처 및 시스템 설계 문서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-04-16-knowledge-map.md` — Knowledge Map 구축 실행 계획 문서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-04-16.md` — Knowledge Map v1.0 릴리스 및 업데이트 로그

## Key Concepts
- **Knowledge Map**: SKIN1004 AI Agent 코드베이스 및 문서를 구조화하여 Claude Code 세션에서 참조할 수 있도록 지원하는 정적 지식 그래프.
- **Build Pipeline (`builder.py`)**: `discover → cache → parse → flash → graph → export`의 순차적 흐름으로 정적 분석을 수행하는 전체 빌드 라이프사이클.
- **Gemini Flash Semantic Pass (`semantic.py`)**: 파일 단위 파싱 후 LLM(Gemini Flash)을 통해 시맨틱 정보(핵심 개념, 의존 관계, 문서 요약)를 추출하는 단계.
- **Multi-artifact Exporters (`exporters.py`)**: 분석된 그래프 데이터를 JSON 포맷(`graph.json`) 및 다양한 마크다운 위키 페이지(`wiki/*.md`, `wiki/index.md`, `GRAPH_REPORT.md`)로 렌더링하는 모듈.

## How It Fits In
이 클러스터는 감지된 외부 클러스터 의존성이 없습니다(독립적인 정적 분석 도구 세트). 대화 런타임에서 작동하는 `app.knowledge`와 명확히 분리되어 동작하며, 코드베이스 전체 문맥을 마크다운 문서 및 지식 그래프 형태로 Claude Code에 제공하는 기반 역할을 수행합니다.

## Common Questions This Page Answers
- Knowledge Map의 빌드 단계(`builder.py`)는 어떤 순서로 진행되는가?
- 런타임 지식 마이닝(`app.knowledge`)과 `app.knowledge_map`의 차이점은 무엇인가?
- Gemini Flash 시맨틱 분석 결과는 어떤 형식의 파일들(`graph.json`, 위키 마크다운 등)로 생성되는가?
- Knowledge Map v1.0 설계 및 구현 계획은 어떤 문서에서 확인할 수 있는가?