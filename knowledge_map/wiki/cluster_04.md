# Cluster 04

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 10

## Purpose
SKIN1004 AI Agent의 핵심 비즈니스 로직, 데이터 포맷팅, 그리고 사용자 편의 기능을 담당하는 코어 유틸리티 클러스터입니다. 사용자의 질문 이력 분석, 수출 물류 금액 계산 오류 방지, 대용량 SQL 결과의 CSV 다운로드 제공, 그리고 멀티모달 이미지/얼굴 검색 등 실무에서 발생하는 다양한 예외 상황과 요구사항을 결정론적(Deterministic)이고 신뢰할 수 있는 방식으로 처리합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/file_request.py` — "엑셀로 뽑아줘" 요청 시 실제 다운로드 가능한 파일이나 링크를 생성하여 제공하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/sql_result_store.py` — 채팅창에 일부만 표시되고 잘린 대용량 SQL 조회 결과 전체를 임시 보관하여 CSV 다운로드로 연동하는 저장소
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/logistics_amount.py` — 수출 물류 금액 계산 시 유상 금액과 무상 금액을 합산하여 누락 없이 정확한 값을 산출하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/query_profile.py` — 사용자의 빈번한 질문 이력을 분석하여 화면에 제안할 질문을 결정론적으로 추출하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/face_clip_agent.py` — CLIP 및 InsightFace 인덱스를 기반으로 인물 및 제품 사진 검색을 수행하는 에이전트
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/table_attachment.py` — 사용자가 업로드한 엑셀/CSV 파일을 시스템이 인식할 수 있는 TSV 텍스트 형태로 변환하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/response_formatter.py` — 에이전트의 답변을 프론트엔드 렌더링에 적합한 일관된 마크다운 형식으로 가공하는 포맷터
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/org_structure.py` — 공식 팀명 및 국가별 담당 범위 등 검증된 조직도 정보를 제공하는 Single Source of Truth
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/calendar_stats.py` — 사용자의 전체 이벤트 이력에서 미팅 횟수 통계를 계산하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/dashboard_links.py` — 대시보드 탭 링크 카탈로그 및 결정론적 채팅 답변 매핑 관리

## Key Concepts
- **유상 및 무상 합산 (Logistics Amount)** — 수출 물류 데이터 분석 시 `amount` 컬럼(유상)만 단순 합산하면 무상 샘플 등의 금액이 누락되므로, 반드시 유상과 무상을 합산하여 정확한 실적을 도출합니다.
- **결정론적 질문 제안 (Query Profile)** — 사용자가 자주 묻는 질문 제안 생성 시 LLM의 확률적 생성에 의존하지 않고, 실제 질문 이력과 필터 추출 규칙을 기반으로 명확한 근거를 가지고 추출합니다.
- **TSV 변환 (Table Attachment)** — 사용자가 표 데이터를 이미지로 캡처하여 올리는 대신 엑셀/CSV 파일 자체를 업로드할 수 있도록 지원하며, 이를 내부적으로 다루기 쉬운 TSV 형태로 변환합니다.

## How It Fits In
- **Cluster 03 연계**: `face_clip_agent.py`는 이미지 검색 성능 향상을 위해 `concept:ocr_reranking` (Cluster 03) 기술을 구현 및 활용합니다.
- **Cluster 12 연계**: `response_formatter.py`는 에이전트의 최종 출력 품질을 보장하기 위해 `concept:response_formatting` (Cluster 12) 표준 규격을 구현합니다.
- **Cluster 31 연계**: `query_profile.py`는 사용자 행동 분석을 위해 `concept:audit_logs` (Cluster 31)의 감사 로그 데이터를 활용하여 자주 묻는 질문을 추출합니다.

## Common Questions This Page Answers
- "엑셀로 뽑아줘"라는 사용자 요청에 대해 실제로 다운로드 가능한 파일을 어떻게 생성하고 전달하나요?
- SQL 조회 결과가 너무 길어서 채팅창에서 잘릴 때, 전체 데이터를 사용자가 다운로드하게 하려면 어떻게 해야 하나요?
- 수출 물류 금액을 계산할 때 무상 수출 건이 누락되는 문제를 어떻게 방지하고 있나요?
- 사용자가 업로드한 엑셀 파일 데이터를 에이전트가 텍스트 형태로 정확히 읽게 하려면 어떻게 처리하나요?