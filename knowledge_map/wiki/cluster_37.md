# Cluster 37

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 1

## Purpose
`cluster_37`은 시스템의 건강성과 데이터 무결성 회귀를 매일 자동으로 감지하는 자가 점검(self-check) 메커니즘을 담당합니다. 크론 작업이 겉으로는 정상 실행되더라도 내부적인 실패나 설정 오류로 인해 방치되는 사각지대를 방지하고 능동적으로 이상 징후를 보고합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/self_check.py` — 시스템 건강성 및 데이터 무결성 자가 점검 로직 수행

## Key Concepts
- **self_check**: 시스템 내부에서 매일 데이터 무결성과 핵심 프로세스의 정상 동작 여부를 직접 검증하는 자가 점검 체계입니다.
- **Silent Failure Prevention**: 2026-08-04 서버 이관 당시 APP 서버 `.env`의 DB 비밀번호 오기입으로 인해 AD 동기화가 6일간 매일 밤 실패했음에도 로그를 보지 않아 인지하지 못했던 장애 사례를 방지하기 위해 고안되었습니다.

## How It Fits In
이 클러스터는 일일 자가 점검 과정에서 발견된 오류 및 시스템 건강성 상태를 외부로 알리기 위해 알림 체계와 연동됩니다:
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/self_check.py` 파일은 잔디 웹훅(`concept:jandi_webhook`, cluster_18)을 참조하여 점검 결과나 장애 상황을 담당자에게 전달합니다.

## Common Questions This Page Answers
- 시스템 자가 점검(self-check) 모듈은 어떤 파일에 구현되어 있는가?
- 왜 별도의 자가 점검 모듈이 도입되었으며 어떤 장애 사례(2026-08-04 AD 동기화 실패)를 방지하는가?
- 자가 점검 모듈은 알림 통지를 위해 어떤 외부 컴포넌트(`concept:jandi_webhook`)를 사용하는가?