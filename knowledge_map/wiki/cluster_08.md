# Cluster 08

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 18

## Purpose
SKIN1004 AI Agent의 핵심 비즈니스 도메인 지식(사내 은어, 제품 전성분, 모델 초상권, OP 재고, 수상 내역 등)을 정형 데이터 및 지식 그래프 형태로 관리하고, 시스템의 자율적 성장과 사용자 피드백을 추적하는 코어 모듈 모음입니다. 단순 벡터 검색의 한계를 극복하기 위해 관계형 데이터베이스(MariaDB) 기반의 정확한 수치/조건 조회와 지식 그래프 컴파일러를 제공합니다.

## Key Files
- `app/core/term_aliases.py` — "센앰", "프바시" 등 사내 은어 및 오타를 LLM 호출 전에 정식 명칭으로 보정하는 용어 사전
- `app/core/model_rights.py` — 모델 초상권 사용 가능 매체, 지역, 기간 정보를 조회하여 마케팅 리스크를 방지하는 모듈
- `app/core/inventory.py` — 운영팀의 구글 스프레드시트 재고 데이터를 MariaDB에 적재하고 정확한 수치로 조회하는 모듈
- `app/core/ingredients.py` — 제품 전성분 데이터를 기반으로 특정 성분 포함 여부를 정확하게 판별하는 모듈
- `app/core/awards.py` — 마케팅 활용 가능 여부 및 수상/랭킹 데이터를 관리하고 조회하는 모듈
- `app/core/feedback_inbox.py` — 사용자의 👎(붐따) 피드백과 코멘트를 수집하여 개선 대상 데이터로 처리하는 모듈
- `app/core/growth_report.py` — SQL 캐시 히트율, 신규 패턴 등 시스템의 자율적 성장 지표를 측정하는 모듈
- `app/core/usage_meter.py` — LLM 및 BigQuery 사용량을 계측하여 운영 비용 대비 가치를 정량화하는 모듈
- `app/knowledge/wiki_graph.py` — Gemini Flash를 활용해 위키 사실로부터 엔티티 관계(src, relation, dst)를 추출하는 지식 그래프 모듈
- `app/knowledge/entity_pages.py` — 추출된 엔티티 정보를 Karpathy의 "LLM wiki" 방식으로 컴파일하는 모듈
- `docs/superpowers/plans/2026-09-08-notion-briefing-phase2.md` — 출근 전 자동 브리핑 배송을 위한 노션 저장 2단계 구현 계획서

## Key Concepts
- **정형 데이터 조회 (Table-based Query)** — OP 재고(`inventory.py`)나 수상 내역(`awards.py`)처럼 숫자와 판정(O/X)이 중요한 데이터는 임베딩 벡터 검색 대신 MariaDB 표 조회를 사용하여 오답을 방지합니다.
- **사내 용어 보정 (Term Aliases)** — 외부 LLM이 알 수 없는 SKIN1004 고유의 축약어("포마", "프바시")나 오타를 정식 제품명으로 매핑합니다.
- **지식 그래프 및 엔티티 페이지** — 위키 데이터를 기반으로 엔티티 간의 관계를 추출하고, 이를 구조화된 마크다운 페이지로 컴파일하여 에이전트의 지식원으로 활용합니다.

## How It Fits In
- **데이터 적재 및 연결**: `awards.py`는 `cluster_02`의 Google Sheets API를 통해 원본 데이터를 가져오며, `term_aliases.py`는 `cluster_09`의 MariaDB 연결을 활용해 용어를 보정합니다.
- **피드백 및 성장**: `feedback_inbox.py`는 `cluster_29`로 피드백 데이터를 전달하고, `growth_report.py`는 `cluster_12`의 지식 갭(Knowledge Gaps) 및 성장 스냅샷 개념을 구현합니다.
- **지식 그래프**: `wiki_graph.py`와 `wiki_communities.py`는 `cluster_32` 및 `cluster_09`의 위키 데이터베이스와 네트워크 그래프 라이브러리(NetworkX)를 연동합니다.
- **외부 연동**: 노션 브리핑 계획서는 `cluster_10`의 Notion API와 `cluster_35`의 아웃박스 패턴을 기반으로 동작합니다.

## Common Questions This Page Answers
- "센앰", "프바시" 같은 사내 은어가 입력되었을 때 에이전트가 어떻게 올바른 제품을 찾아내나요? (`term_aliases.py`)
- 왜 재고 수량이나 모델 초상권 기간 조회에 벡터 임베딩 대신 RDB 테이블 조회를 사용하나요? (`inventory.py`, `model_rights.py`)
- 사용자가 남긴 👎(붐따) 피드백 코멘트는 어디서 어떻게 처리되나요? (`feedback_inbox.py`)
- AI Agent 운영에 들어가는 LLM 및 BigQuery 비용은 어떻게 측정하나요? (`usage_meter.py`)