# Cluster 09

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 3

## Purpose
이 클러스터는 SKIN1004 AI Agent가 수집한 원천 데이터(일정, 메일, 대화 기록 등)를 가공하여 신뢰할 수 있는 지식 자산으로 변환하고 사용자에게 브리핑하는 역할을 합니다. LLM의 환각 현상(Hallucination)을 원천 차단하면서, 과거 대화 맥락에서 유용한 사실(Fact)을 추출하여 지속 가능한 지식 저장소(Knowledge Base)를 구축하는 데 초점을 맞춥니다.

## Key Files
- `app/core/work_briefing.py` — 일정과 메일 데이터를 연동하여 출근 브리핑 문서를 생성하며, LLM이 임의의 사실을 왜곡·조작하지 못하도록 엄격한 검증 파이프라인을 제공합니다.
- `app/knowledge/__init__.py` — 지속적인 사실 추출 및 검색을 담당하는 Knowledge layer의 진입점입니다.
- `app/knowledge/wiki_extractor.py` — 과거 대화 기록(Q&A)에서 재사용 가능한 지식(Fact)을 추출하여 MariaDB 기반의 `knowledge_wiki` 테이블에 저장하는 Gemini Flash 기반 모듈입니다. (1주차 범위에서는 shadow-mode로 동작)

## Key Concepts
- **출근 브리핑 (Work Briefing)**: 사용자가 업무를 시작할 때 필요한 일정과 메일 정보를 한 장의 문서로 요약하여 제공하는 기능입니다. 첫 화면 및 잔디(Jandi) 공용 본문으로 발송됩니다.
- **환각 방지 (Hallucination Prevention)**: LLM이 존재하지 않는 가상의 일정이나 메일을 지어내지 못하도록 제어하는 설계 원칙입니다. LLM은 오직 문장을 선택하는 역할만 수행하며, 모든 항목은 실제 존재하는 `event_id` 또는 `message_id`와 매핑되어야만 사용자에게 노출됩니다. 매핑되지 않은 항목은 코드가 판정하여 즉시 폐기합니다.
- **지식 추출 (Knowledge Extraction)**: 과거 대화 내역(`messages` 테이블)에서 유용한 비즈니스 사실을 추출하여 구조화된 지식 위키(`knowledge_wiki`)로 영속화하는 프로세스입니다.

## How It Fits In
이 클러스터는 시스템의 신뢰성과 지속 가능성을 보장하는 핵심 레이어입니다.
- `app/core/work_briefing.py`는 **cluster_07**의 `concept:hallucination_prevention` 설계 원칙을 직접 구현([implements])합니다. LLM의 자유도를 제한하고 코드 레벨에서 ID 매핑을 강제함으로써, 메가와리(Megawari) 등 SKIN1004의 민감한 비즈니스 데이터가 왜곡되어 브리핑되는 현상을 방지합니다.
- 추출된 지식은 향후 에이전트가 사용자 질문에 답변할 때 RAG(Retrieval-Augmented Generation)의 신뢰할 수 있는 참조 데이터로 활용됩니다.

## Common Questions This Page Answers
- **출근 브리핑 생성 시 LLM의 환각 현상을 어떻게 방지하나요?**
  - LLM은 문장 선택 역할만 수행하며, 생성된 모든 항목은 실제 `event_id` 및 `message_id`와 일치하는지 코드가 검증합니다. 일치하지 않는 가짜 항목은 즉시 버려집니다.
- **과거 대화에서 지식을 추출하는 과정은 어떻게 진행되나요?**
  - `wiki_extractor.py`가 Gemini Flash를 활용해 `messages` 테이블의 Q&A 쌍을 분석하고, 유의미한 사실을 추출하여 MariaDB의 `knowledge_wiki`에 저장합니다. 초기에는 안정성을 위해 shadow-mode로 동작합니다.