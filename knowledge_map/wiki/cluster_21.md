# Cluster 21

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 5

## Purpose
스프레드시트 데이터를 기반으로 이메일을 안전하게 대량 발송하는 전용 메일 발송 시스템 및 관련 운영 문서를 관리합니다. LLM을 배제하고 순수 검증 로직과 안전 장치 중심의 백그라운드 발송 작업을 제공하여 중복 발송 및 권한 이슈로 인한 연속 실패를 방지합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/mail_sender_api.py` — 메일 발송 전용 엔드포인트 (`/mail-sender`). 사전 점검, 미리보기, 테스트 발송, 발송 작업 제어 제공 (LLM 미사용).
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/mail_sender.py` — 시트 행 및 탭 목록 파싱, 발송 전 데이터 유효성 검증을 수행하는 순수 함수 모듈.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/mail_sender_jobs.py` — 데몬 스레드 기반 메일 발송 비동기 작업 실행 및 MariaDB 상태 기록 엔진.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/specs/2026-09-30-mail-sender-design.md` — 메일 자동 발송 전용 페이지 (`/mail-sender`)의 기능 및 아키텍처 설계 문서.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/MIGRATION_AI_CRAVER.md` — AI Craver 서버 마이그레이션 런북 (2026-07-27 ~ 07-30).

## Key Concepts
- **발송 안전 원칙 (Fail-Safe Principles)**: 
  - *중복 발송 방지 우선*: 동일 수신자에게 중복 발송되는 것을 방지하는 것이 단순 실패보다 중요하게 취급됩니다.
  - *재시작 후 자동 재개 금지*: 서버 재시작 시 `sending` 상태였던 행의 실제 발송 여부를 확정할 수 없으므로 작업을 자동으로 재개하지 않습니다.
  - *연속 오류 중단 (`MAX_SAME_ERROR`)*: 동일 원인 실패가 연속 발생하면 작업을 즉시 중단하여 권한 문제 등으로 전 행이 실패 처리되는 사태를 차단합니다.
- **Pure Function Validation**: 외부 I/O나 LLM 없이 입력 시트 데이터만을 검증하여 오발송 가능성을 사전에 차단하는 순수 점검 로직입니다.
- **Daemon Thread & MariaDB Job Tracking**: 실제 발송 루프는 백그라운드 데몬 스레드로 실행되며, 작업의 진행 상태와 결과 이력은 MariaDB에 영구 기록됩니다.

## How It Fits In
단독으로 구성된 유틸리티/운영 클러스터로, 다른 분석 및 LLM 에이전트 클러스터와 직접적인 의존성 없이 독립된 백오피스 엔드포인트와 인프라 런북을 구성합니다.

## Common Questions This Page Answers
- 서버가 예기치 않게 재시작되었을 때 진행 중이던 메일 발송 작업은 어떻게 처리되는가?
- 권한 오류나 SMTP 설정 오류 발생 시 대량 실패를 방지하는 중단 기준은 무엇인가?
- 메일 발송 전 템플릿과 시트 데이터를 어떻게 미리보고 검증할 수 있는가?