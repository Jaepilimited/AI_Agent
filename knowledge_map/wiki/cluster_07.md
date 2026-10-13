# Cluster 07

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 10

## Purpose
채팅 및 보고서 파이프라인 전반에서 LLM의 환각(Hallucination)과 오탐을 방지하기 위한 핵심 결정론적 검증 및 텍스트 정제 모듈군입니다. 한국어 특화 키워드 추출/경계 검사, 제품 라인 확정, COA/MSDS 문서 탐색 등 오류가 치명적인 영역을 규칙 기반 코드로 강제 방어합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/answer_check.py` — 채팅 응답에 포함된 수치가 실제 DB 조회 결과에 존재하는지 검증하는 방어선
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/coa_finder.py` — LLM을 배제하고 규정 규칙 기반으로 드라이브에서 롯트 번호 기준 COA·MSDS를 찾는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/notice_result.py` — SQL 결과가 단일 안내문인 경우 이를 가공 없이 즉시 답변으로 확정
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/product_lines.py` — 히알루테카(Hyalu_Teca)와 히알루시카 혼동 방지 등 질문 내 제품 라인을 결정론적으로 판정
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/qty_coverage.py` — 수량 미집계 브랜드를 0개로 오답 처리하지 않도록 방어
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/query_keywords.py` — 한국어 교착어 특성을 고려한 단일 통합 키워드 및 조사 분리 추출기
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/result_truncation.py` — 조회 결과가 상위 15행 등으로 잘렸을 때 LLM 누락을 막기 위해 코드로 공시 강제
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/team_link_index.py` — `team_resources` 링크 카드를 벡터 색인에 태우는 연동 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/textmatch.py` — "요인도"에서 "인도"를 국가로 오인하는 짧은 낱말 삼킴 방지 경계 매칭
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/insight.py` — 보고서에서 Key Insights/Action Items 문장 생성 시 수치 정합성을 검증하는 유일한 LLM 서술 지점

## Key Concepts
- **결정론적 방어 (Rule-based Defense)**: COA 문서 매칭(`coa_finder.py`)이나 제품 라인 판별(`product_lines.py`) 등 규제나 오답 위험도가 높은 로직에서 LLM을 배제하고 규칙을 강제하는 방식입니다.
- **수치 무결성 검증 (Numeric Verification)**: LLM이 생성한 텍스트 속 숫자가 원천 SQL 데이터셋에 실재하는지 확인하고, 미일치 시 문장을 폐기(`answer_check.py`, `insight.py`)합니다.
- **교착어 낱말 경계 처리**: 한국어 조사 분리(`query_keywords.py`) 및 복합 명사 내부 오탐 방지(`textmatch.py`)를 통해 검색 누락과 오분류를 차단합니다.

## How It Fits In
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/team_link_index.py`는 `team_resources` 기반 시트/드라이브 링크 동기화 파이프라인(cluster_23)과 직접 연결되어 벡터 인덱스를 구축합니다.
- `answer_check.py`와 `result_truncation.py`는 보고서(`insight.py`)뿐만 아니라 챗봇 질의응답 엔진의 출력 품질을 최종 단계에서 방어합니다.

## Common Questions This Page Answers
- "외부 요인도 같이 봐줘"라는 질문에서 국가 `인도`가 잘못 필터링되는 현상은 어떻게 방지하나요? (`textmatch.py`)
- LLM이 임의로 생성한 가짜 판매 통계 수치는 어디서 걸러지나요? (`answer_check.py`, `insight.py`)
- 히알루테카 라인 질문에 히알루시카 정보가 나가지 않도록 라인을 판별하는 코드는 어디에 있나요? (`product_lines.py`)
- 규제 문서(COA/MSDS) 검색 시 LLM 환각을 완전히 배제한 탐색 방식은 어떻게 동작하나요? (`coa_finder.py`)