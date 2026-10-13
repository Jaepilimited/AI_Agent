# Cluster 39

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 1

## Purpose
`cluster_39`는 SKIN1004 AI Agent의 Knowledge 레이어 패키지 엔트리포인트를 정의합니다. 과거 대화 내용으로부터 지속적인 사실(persistent facts)을 추출하고 이를 저장 및 검색하는 핵심 컴포넌트들을 구성합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/__init__.py` — Knowledge layer의 진입점으로, 대화 기록 기반 팩트 추출(wiki_extractor) 및 DB 저장소(wiki_store) 모듈을 총괄하는 패키지 초기화 파일.

## Key Concepts
- `wiki_extractor` — 과거 대화(past conversations)에서 의미 있는 사실 데이터를 추출하는 Gemini Flash 기반 추출 모듈입니다.
- `wiki_store` — 추출된 지속성 지식을 `knowledge_wiki` 테이블에 읽고 쓰기 위한 MariaDB 기반 저장 및 검색 인터페이스입니다.
- `knowledge layer` — 대화 흐름 중 발생한 정보와 사실을 단기 메모리에 머무르게 하지 않고 장기적인 지식 베이스로 축적·활용하는 계층입니다.

## How It Fits In
- `cluster_09`와의 관계: `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge/__init__.py`는 `cluster_09`의 `concept:knowledge_wiki`를 구현(implements)합니다.
- 전체 아키텍처 관점: Gemini Flash를 활용해 대화 데이터로부터 팩트를 추출하고 MariaDB(`knowledge_wiki`)에 영구 보관함으로써, 다른 시스템 컴포넌트가 과거 문맥 및 축적된 도메인 지식을 질의하고 검색할 수 있도록 지원합니다.

## Common Questions This Page Answers
- 과거 대화에서 지속적인 지식(persistent facts)을 추출하고 저장하는 모듈의 진입점은 어디인가요?
- 대화 기록 기반 지식 추출(`wiki_extractor`)에는 어떤 모델이 사용되나요?
- 추출된 지식은 어떤 저장소(`wiki_store`) 및 데이터베이스 테이블에 보관되나요?