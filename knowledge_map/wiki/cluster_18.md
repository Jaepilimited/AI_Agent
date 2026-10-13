# Cluster 18

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 12

## Purpose
이 클러스터는 SKIN1004 AI Agent 시스템의 운영 관리 API와 프로젝트 주요 이력(업데이트 로그), 라우터 및 성능 최적화 설계 문서를 다룹니다. 관리자용 사용자·모델 권한 관리 엔드포인트(`admin_api.py`)와 함께, 시스템 버전 진화 과정(v8.1~v9.0 및 2026년 하반기 성능/라우터 개편)을 기록한 명세 문서들을 포함합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/admin_api.py`: 사용자 관리 및 모델 접근 제어를 처리하는 관리자 엔드포인트 (MariaDB 연동)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-07-09-performance-fixes-phase2.md`: 성능 최적화 Phase 2(상위 10개 수정 항목) 구현 계획 문서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/specs/2026-09-07-router-source-gate-design.md`: 라우터 1단계인 "소스가 필요한가?" 여부를 판단하는 게이트 설계 명세서
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-03-18.md`: v8.1 컨텍스트 무제한 확장 및 SQL 프롬프트 대규모 수정 로그
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-03-19.md`: v8.3 SQL 복잡 쿼리 개선, Playwright E2E QA, jp2 이슈 수정 기록
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-03-23.md`: v8.5~v9.0 속도 대폭 개선, 실시간 스트리밍 도입 및 QA 강화 내역
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-04-20.md`: BigQuery 파티션 필터, AD 동기화 고도화, Jandi 연동 로그
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-08-19.md`: 알림함 기능, 조용한 오답 사냥 및 데이터 정합성 강화 내역
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/update_log_2026-02-20.md`, `2026-04-03.md`, `2026-04-09.md`, `2026-06-24.md`: 기간별 정기 기능 릴리스 및 안정화 업데이트 로그

## Key Concepts
- **admin_management**: `admin_api.py`를 통해 MariaDB 기반으로 시스템 사용자 및 모델 접근 권한을 제어하는 백오피스 운영 기능.
- **router_source_gate**: 쿼리 유입 시 소스 조회가 실제로 필요한지 1단계에서 판정하는 라우팅 메커니즘 (`2026-09-07-router-source-gate-design.md`).
- **ad_user_sync**: 사내 Active Directory 계정 정보를 에이전트 시스템 권한 체계와 연동하는 기능 (`2026-04-20.md`).
- **performance_phase2**: 응답 지연 해소와 부하 분산을 위한 10대 수정 과제 중심의 성능 튜닝 계획 (`2026-07-09-performance-fixes-phase2.md`).

## How It Fits In
- **cluster_25 연계**:
  - `docs/superpowers/specs/2026-09-07-router-source-gate-design.md`는 cluster_25의 `concept:intent_routing` 명세를 직접 구현합니다.
  - `docs/update_log_2026-04-20.md`의 AD 동기화 고도화 내역은 cluster_25의 `concept:ad_user_sync`와 직접 연결됩니다.
- **운영 및 히스토리 허브**: `app/api/admin_api.py`를 제외한 대부분의 문서 파일은 프로젝트의 과거 아키텍처 결정 사항(SQL 쿼리 개선, BigQuery 파티션, Jandi 알림 등)을 추적하는 기준 자료 역할을 합니다.

## Common Questions This Page Answers
- MariaDB 기반의 관리자 사용자 및 모델 권한 API는 어디에 정의되어 있는가?
- 쿼리 라우팅 1단계("소스 필요 여부 게이트") 설계 내용은 어느 문서를 참조해야 하는가?
- v8.1~v9.0 및 2026년 상/하반기 성능 개선(Phase 2), AD 동기화, BigQuery 파티션 최적화 변경 이력은 어디서 확인할 수 있는가?