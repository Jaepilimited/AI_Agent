# Cluster 13

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 3

## Purpose
이 클러스터는 SKIN1004 AI Agent의 데이터 서빙, 입력 변환, 코드 무결성 검증을 위한 핵심 코어 유틸리티 모음입니다. 대용량 SQL 쿼리 결과의 CSV 다운로드 제공, 사용자 업로드 엑셀/CSV 파일의 텍스트 표 변환, 에러 없이 발생하는 잠재적 결함(silent failure)을 방지하는 정적 검사 기능을 담당합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/sql_result_store.py` — SQL 쿼리 실행 결과 전체를 임시 보관하여 사용자가 잘리지 않은 전체 데이터를 CSV로 다운로드할 수 있도록 지원합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/static_checks.py` — 코드와 정적 자산을 분석해 런타임 에러를 뿜지 않고 조용히 실패하는 고장(silent failure)을 찾아내는 공통 검사 로직을 제공합니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/table_attachment.py` — 사용자가 첨부한 엑셀(.xlsx) 및 CSV 파일을 복사-붙여넣기한 표와 동일한 형태인 TSV 텍스트로 파싱하여 에이전트 입력으로 전달합니다.

## Key Concepts
- **Full SQL Result Export (`sql_result_store.py`)**: 채팅 인터페이스(`app/agents/sql_agent.py`의 `_fast_table_markdown`)는 상위 N행(예: 146행 중 15행)만 마크다운 표로 미리보기하므로, 전체 데이터를 확인해야 하는 사용자(예: 인도네시아 월별 매출 전체 조회)를 위해 CSV 다운로드 경로를 제공합니다.
- **Silent Failure Prevention (`static_checks.py`)**: 개발 중 실행되는 테스트(`tests/test_no_silent_failures.py`)와 운영 서버 자가 점검이 동일한 단일 검사 함수를 호출하도록 일원화하여, 규칙 중복 구현으로 인한 점검 누락 문제를 방지합니다.
- **Table Attachment Normalization (`table_attachment.py`)**: 기존에 이미지 업로드만 지원되어 사용자가 표를 캡처해 올리던 문제를 해결하기 위해, 엑셀/CSV 업로드 파일을 파싱해 직접 텍스트 기반 TSV 형태로 변환합니다.

## How It Fits In
코어 계층(`app/core/`)에 위치하며 독립적인 데이터 처리 및 검증 책임을 갖습니다:
- `sql_result_store.py`는 `app/agents/sql_agent.py`의 표 출력 제약을 보완합니다.
- `table_attachment.py`는 채팅 인터페이스의 파일 입력 경로와 연결되어 멀티모달 입력 대신 구조화된 텍스트 테이블 입력을 처리합니다.
- `static_checks.py`는 개발 환경(`tests/`)과 운영 서버의 일일 점검 양쪽에서 공통 기준선으로 활용됩니다.

## Common Questions This Page Answers
- SQL 쿼리 결과가 상위 15행만 표시되어 전체 기간 데이터가 누락되어 보일 때 어떻게 전체 데이터를 다운로드할 수 있나요?
- 사용자가 캡처 이미지가 아닌 실제 엑셀 파일이나 CSV 파일을 첨부했을 때 에이전트는 이를 어떤 형식으로 변환해 읽나요?
- 개발 환경의 pytest와 운영 서버의 일일 자가 점검 스크립트가 동일한 정적 검사 규칙을 적용받도록 하려면 어디를 수정해야 하나요?