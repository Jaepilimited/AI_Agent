# Cluster 23

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 3

## Purpose
SKIN1004 AI Agent 커스텀 프론트엔드 및 내부 서비스를 지원하는 공통 MariaDB 데이터베이스 계층과 관련 구현 계획을 관리합니다. 운영 환경(포트 3000)과 개발 환경(포트 3001) 모두 MariaDB 백엔드를 사용하며, 쿼리 실행 및 팀별 자료 관리를 위한 기반 인터페이스를 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/db/__init__.py` — SKIN1004 AI 커스텀 프론트엔드를 위한 Database 패키지 초기화 파일.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/db/mariadb.py` — 모든 환경에서 MariaDB를 다루기 위한 DB 계층 구현체(`fetch_all`, `fetch_one`, `execute`, `execute_lastid` 인터페이스 제공).
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-04-02-db-hub-team-resources.md` — DB HUB 팀별 자료 시스템 구현 계획 문서.

## Key Concepts
- `MariaDB Interface` — `fetch_all`, `fetch_one`, `execute`, `execute_lastid`로 구성된 일관된 데이터베이스 쿼리 및 트랜잭션 실행 인터페이스.
- `Dual Environment Backend` — 운영(3000) 및 개발(3001) 환경 구분 없이 동일하게 MariaDB 백엔드를 적용하는 아키텍처.
- `DB HUB Team Resources` — DB HUB 기반의 사내 팀별 자료 시스템 구축 및 관리 방안.

## How It Fits In
- `cluster_18`: `mariadb.py`는 `cluster_18`의 MariaDB 커넥션 풀(`concept:mariadb_connection_pool`) 구조를 구현하여 DB 연결을 효율적으로 관리합니다.
- `cluster_09`: `mariadb.py`는 데이터베이스 스키마 마이그레이션(`concept:schema_migration`) 및 지식 위키(`concept:knowledge_wiki`) 기능 구현에 활용되어 데이터 지속성과 문서 연동을 지원합니다.

## Common Questions This Page Answers
- 운영(3000)과 개발(3001) 환경에서 데이터 조회를 위해 공통으로 사용하는 DB 인터페이스 메서드는 무엇인가요?
- MariaDB를 통해 쿼리 실행 후 방금 생성된 ID를 가져오려면 어떤 함수(`execute_lastid`)를 사용해야 하나요?
- DB HUB 팀별 자료 시스템의 구체적인 구현 계획은 어느 문서(`2026-04-02-db-hub-team-resources.md`)에 정리되어 있나요?