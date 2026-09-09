# Cluster 07

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 7

## Purpose
Cluster 07은 SKIN1004 AI Agent의 핵심 비즈니스 로직 및 검색 신뢰성을 보장하는 핵심 유틸리티 모듈 모음입니다. LLM의 환각(Hallucination)이나 한국어 형태소 분석의 한계로 인해 발생할 수 있는 오답을 방지하고, 정형/비정형 데이터 조회 결과를 규칙 기반(Rule-based) 코드로 안전하게 정제하여 사용자에게 전달하는 역할을 합니다.

## Key Files
- `app/core/coa_finder.py` — 공유드라이브에서 롯트(Lot) 번호 기반으로 COA 및 MSDS 문서를 규칙 기반으로 정확하게 매칭하여 탐색합니다. (LLM 미사용)
- `app/core/notice_result.py` — SQL 조회 결과가 시스템 미연동 안내문 등 단일 안내 메시지인 경우, LLM을 거치지 않고 즉시 답변으로 반환합니다.
- `app/core/qty_coverage.py` — 판매수량 집계가 불가능한 특정 브랜드에 대해 데이터 미집계 상태를 인지하고 0개로 잘못 답변하는 것을 방지합니다.
- `app/core/query_keywords.py` — 한국어 교착어 특성을 고려하여 질문에서 불용어와 조사를 제거하고 검색용 핵심 키워드를 단일 지점에서 추출합니다.
- `app/core/result_truncation.py` — 대용량 조회 결과가 잘려서 LLM에 전달될 때, 전체 데이터가 아님을 코드가 직접 명시하여 사용자 오해를 방지합니다.
- `app/core/team_link_index.py` — `team_resources` 테이블의 시트 및 드라이브 링크를 벡터 색인(Qdrant)에 동기화하여 팀별 자료 링크 카드를 제공합니다.
- `app/core/textmatch.py` — 한국어 조사나 긴 단어 내에 짧은 단어(예: '인도', '인')가 잘못 매칭되는 현상을 방지하는 낱말 경계 매칭을 수행합니다.

## Key Concepts
- **규칙 기반 문서 매칭 (Rule-based Matching)**: 규제 및 인증 문서(COA/MSDS)는 잘못된 파일을 제공할 경우 리스크가 매우 크기 때문에, LLM에 의존하지 않고 `app/core/coa_finder.py` 내의 엄격한 코드 규칙으로만 파일을 선택합니다.
- **한국어 낱말 경계 매칭**: 한국어는 띄어쓰기가 불분명하고 교착어적 특성이 있어 `"외부 요인도"`에서 국가 `"인도"`를 추출하는 등의 오작동이 발생하기 쉽습니다. `app/core/textmatch.py`와 `app/core/query_keywords.py`는 이러한 한국어 특화 검색 노이즈를 제거합니다.

## How It Fits In
- **Cluster 12 연결**: `app/core/result_truncation.py`는 대용량 데이터 조회 시 상위 일부 행만 요약하여 LLM에 전달하는 `concept:smart_preview` (Cluster 12) 메커니즘을 구현하며, 데이터가 생략되었다는 사실을 프롬프트가 아닌 코드가 직접 공시하도록 강제합니다.

## Common Questions This Page Answers
- **Q. 특정 브랜드의 판매수량이 실제와 다르게 0개로 답변되는 문제를 어떻게 해결하나요?**
  - `app/core/qty_coverage.py`를 통해 수량 미집계 브랜드에 대해 잘못된 수치(0개)가 나가는 것을 방지하고 예외 처리를 수행합니다.
- **Q. "인플루언서"나 "요인도"라는 단어에서 국가 "인도"가 검색 필터로 걸리는 현상을 막으려면?**
  - `app/core/textmatch.py`의 낱말 경계 매칭 로직을 사용하여 텍스트가 독립된 단어로 쓰였는지 검증합니다.