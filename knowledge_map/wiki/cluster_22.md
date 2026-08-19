# Cluster 22

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 3

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트의 핵심 데이터 저장소인 MariaDB 인터페이스와, 과거 대화 내역으로부터 유용한 지식을 추출하여 영구 저장하는 지식 관리(Knowledge) 레이어를 담당합니다. 사용자와의 대화에서 반복적으로 활용할 수 있는 사실(Fact)을 추출하고 이를 데이터베이스에 체계적으로 기록하는 기반을 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/db/mariadb.py` — 개발(3001) 및 운영(3000) 환경 모두에서 공통으로 사용하는 MariaDB 데이터베이스 연결 및 쿼리 실행 인터페이스를 제공합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/__init__.py` — 지식 추출 및 저장 레이어의 통합 진입점으로, Gemini Flash 기반의 지식 추출기와 MariaDB 기반의 지식 저장소(`knowledge_wiki` 테이블)를 연결합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/wiki_extractor.py` — 과거 대화 내역(`messages` 테이블)의 질문과 답변 쌍을 분석하여, 재사용 가능한 지식(Fact)을 추출하는 Gemini Flash 기반의 추출기입니다. (1주차 범위에서는 shadow-mode로 동작)

## Key Concepts
- **MariaDB Interface**: 데이터베이스 작업을 위해 `fetch_all`, `fetch_one`, `execute`, `execute_lastid` 등의 표준화된 메서드를 제공하는 공통 DB 레이어입니다.
- **Wiki Extractor**: 사용자의 질문(Q)과 어시스턴트의 답변(A)을 기반으로 핵심 정보를 추출하여 `knowledge_wiki` 테이블에 저장할 수 있는 형태로 변환하는 컴포넌트입니다. Gemini Flash 모델을 활용합니다.
- **Shadow-mode Extraction**: 초기 단계에서 시스템에 직접적인 영향을 주지 않고 백그라운드에서 지식을 추출하고 검증하는 안전한 실행 모드입니다.

## How It Fits In
이 클러스터는 프로젝트의 데이터 영속성을 책임지는 핵심 인프라입니다. 
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/db/mariadb.py` 파일은 데이터베이스 커넥션 풀을 관리하는 `concept:mariadb_connection_pool` (Cluster 13)을 구현하여 시스템 전반의 DB 연결 효율성을 보장합니다.
- 추출된 지식 데이터는 향후 메가와리(megawari) 이벤트나 SKIN1004 제품 관련 반복 문의에 대해 AI Agent가 더 신속하고 정확하게 답변할 수 있도록 돕는 지식 기반(Knowledge Base)의 원천이 됩니다.

## Common Questions This Page Answers
- **개발 환경과 운영 환경의 데이터베이스는 어떻게 구분되어 처리되나요?**
  - `mariadb.py`가 두 환경(운영 3000, 개발 3001) 모두를 지원하며 일관된 인터페이스를 제공합니다.
- **과거 대화에서 지식을 추출할 때 어떤 모델과 데이터를 사용하나요?**
  - `messages` 테이블에 저장된 사용자 질문과 답변을 기반으로 Gemini Flash 모델을 사용하여 핵심 사실을 추출합니다.
- **지식 추출 기능이 실제 서비스에 바로 영향을 미치나요?**
  - 1주차 범위에서는 shadow-mode로 동작하여, 기존 대화 흐름을 방해하지 않고 백그라운드에서 안전하게 지식을 추출합니다.