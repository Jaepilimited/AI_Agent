# Cluster 39

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 4

## Purpose
이 클러스터는 SKIN1004 AI Agent 프로젝트의 핵심 데이터베이스 레이어인 MariaDB 인터페이스와 개인정보 비식별화(Anonymization) 및 평가 파이프라인(Eval Pipeline)의 설계 및 구현 문서를 포함합니다. 시스템의 안정적인 데이터 적재와 민감 정보 보호, 그리고 에이전트 성능 평가를 위한 기반을 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/db/mariadb.py` — 운영(Port 3000) 및 개발(Port 3001) 환경 모두에서 공통으로 사용하는 MariaDB 데이터베이스 접근 레이어 인터페이스입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/specs/2026-04-17-anonymization-and-eval-design.md` — 고객 정보 보호를 위한 비식별화 엔진과 LLM 응답 품질 측정을 위한 평가 파이프라인의 상세 설계서입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-04-17-anonymization-and-eval-pipeline.md` — 비식별화 및 평가 파이프라인의 단계별 구현 계획 및 마일스톤을 정의한 문서입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-04-17.md` — Anonymization v1.0 및 Eval Pipeline v1.0 기능이 성공적으로 반영되었음을 기록한 업데이트 로그입니다.

## Key Concepts
- **MariaDB Interface** — `fetch_all`, `fetch_one`, `execute`, `execute_lastid` 등의 표준 메서드를 통해 일관된 DB CRUD 작업을 지원합니다.
- **비식별화 (Anonymization)** — SKIN1004 고객의 개인정보(이름, 전화번호, 주소 등)가 외부 LLM API로 유출되지 않도록 마스킹하거나 가상 데이터로 치환하는 보안 프로세스입니다.
- **평가 파이프라인 (Eval Pipeline)** — 메가와리(Megawari) 프로모션 대응 등 에이전트가 생성한 답변의 정확성, 톤앤매너, 일관성을 정량적으로 평가하는 시스템입니다.

## How It Fits In
이 클러스터는 시스템의 데이터 신뢰성과 보안의 중추 역할을 합니다. 
- `app/db/mariadb.py` 파일은 데이터베이스 커넥션 풀 관리 방식(`concept:mariadb_connection_pool`, cluster_08)을 구체적으로 구현하여, 다중 세션 환경에서도 안전하고 효율적인 DB 연결을 보장합니다.
- 비식별화 및 평가 파이프라인 설계는 에이전트가 안전하게 고객 데이터를 처리하고, 지속적으로 고품질의 상담 서비스를 제공할 수 있도록 돕는 핵심 안전장치 역할을 합니다.

## Common Questions This Page Answers
- **Q1: 개발 환경과 운영 환경의 MariaDB 포트 설정은 어떻게 다른가요?**
  - 운영 환경은 3000 포트, 개발 환경은 3001 포트의 MariaDB를 사용하며 `mariadb.py`가 이를 통합 지원합니다.
- **Q2: 고객의 민감한 개인정보는 어떻게 보호되나요?**
  - `anonymization-and-eval-design.md`에 설계된 비식별화 엔진을 통해 외부 API 전송 전 민감 정보가 안전하게 마스킹 처리됩니다.
- **Q3: DB 트랜잭션 및 쿼리 실행을 위해 어떤 메서드를 사용해야 하나요?**
  - 단건 조회는 `fetch_one`, 다중 건 조회는 `fetch_all`, 쓰기/수정 작업은 `execute` 또는 `execute_lastid`를 사용합니다.