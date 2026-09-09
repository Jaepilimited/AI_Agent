# Cluster 32

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 12

## Purpose
본 클러스터는 SKIN1004 AI Agent 프로젝트의 핵심 설정(Configuration), 배포 전 안정성 검증(Preflight Check), 그리고 정적 코드 및 문서를 분석하여 지식 그래프를 구축하는 **Knowledge Map** 시스템을 포함합니다. 추가적으로 CS 답변의 정확도를 높이기 위한 제품 라인 판정 로직과 외부 의존성을 최소화한 얼굴 임베딩 추론 모듈을 제공합니다.

## Key Files
- `app/config.py` — SKIN1004 Enterprise AI 시스템 전반의 환경 설정 관리.
- `app/core/deploy_preflight.py` — 배포 직전 구문 오류 및 코드 구조 깨짐을 감지하는 preflight 점검 도구.
- `app/core/face_embed.py` — C++ 컴파일 의존성 없이 `buffalo_l` ONNX 모델을 직접 사용하는 얼굴 임베딩 추론 모듈.
- `app/core/product_lines.py` — SKIN1004 제품 라인(예: 히알루테카 vs 히알루시카)의 오인식을 방지하는 결정적 판정기.
- `app/knowledge_map/ast_parser.py` — Python AST를 파싱하여 클래스, 함수, 임포트 관계를 추출하는 도구.
- `app/knowledge_map/graph.py` — NetworkX 및 Louvain 알고리즘 기반의 코드베이스 지식 그래프 생성 및 커뮤니티 탐지.
- `app/knowledge/wiki_insights.py` — 지식 그래프 내 God node 및 고립 엔티티(Orphan)를 분석하는 위키 인사이트 레이어.

## Key Concepts
- **Knowledge Map**: 런타임 대화 사실 마이닝(`app.knowledge`)과 분리되어, Claude Code 세션을 위해 전체 코드베이스와 문서를 정적 분석하여 시각화 및 구조화하는 시스템입니다.
- **Deploy Preflight**: 작업 트리 혼선으로 인해 소스 코드가 비정상적으로 병합되는 사고(예: 클래스 내부에 함수가 잘못 삽입되는 현상)를 배포 전에 차단하는 검증 프로세스입니다.
- **제품 라인 판정 (Product Lines)**: SKIN1004의 유사한 제품 라인명(예: '히알루테카'와 '히알루시카')을 명확히 구분하여 CS 검색 및 답변의 정확도를 보장합니다.

## How It Fits In
이 클러스터는 시스템의 안정적인 배포와 코드베이스 이해도를 극대화하는 메타 인프라 역할을 합니다. `app/knowledge_map`은 정적 코드 분석을 통해 개발 환경을 지원하고, `app/core` 산하의 모듈들은 실제 프로덕션 환경에서 발생할 수 있는 오작동(잘못된 제품 라인 매칭, 배포 시 구문 오류, 라이브러리 빌드 이슈)을 방지하는 안전장치 역할을 수행합니다.

## Common Questions This Page Answers
- **Q. `insightface` 패키지 없이 어떻게 얼굴 임베딩을 수행하나요?**
  - `app/core/face_embed.py`에서 Python 3.12 환경의 C++ 빌드 도구 부재 문제를 해결하기 위해, ONNX 런타임을 통해 `buffalo_l` 모델을 직접 로드하여 추론합니다.
- **Q. 배포 전 코드 구조가 깨졌는지 어떻게 확인하나요?**
  - `app/core/deploy_preflight.py`가 단순 `/health` 체크로 잡지 못하는 모듈 레벨의 구문 및 구조 왜곡을 배포 직전에 검증합니다.
- **Q. 유사한 SKIN1004 제품 라인 오인식 문제는 어떻게 해결하나요?**
  - `app/core/product_lines.py`를 통해 사용자의 질문이 어떤 구체적인 제품 라인을 지목했는지 결정적으로 판정하여 잘못된 CS 답변이 나가는 것을 방지합니다.