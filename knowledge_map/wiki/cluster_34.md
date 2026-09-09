# Cluster 34

> Auto-generated 2026-09-09T03:00:59.448358+09:00 · Files: 2

## Purpose
SKIN1004 AI Agent 프로젝트 내에서 시스템 알림 및 메일 발송을 담당하는 기능군입니다. 현재 사내 보안 정책 및 네트워크 방화벽 제한으로 인해 실제 발송 기능은 대기 상태이며, 이를 해결하기 위한 IT 인프라 요청 문서와 메일 발송 모듈을 포함하고 있습니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/mailer.py` — 사내 SMTP 릴레이를 통한 메일 발송을 담당하는 핵심 모듈입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/IT_REQUEST_MAIL_JANDI.md` — AI Craver 알림 발송 경로 개방 및 방화벽 허용을 위해 IT 부서에 제출할 요청 사항을 정리한 문서입니다.

## Key Concepts
- **사내 SMTP 릴레이 (SMTP Relay)** — 시스템 내부에서 외부 또는 사내 수신자에게 메일을 안전하게 전달하기 위한 메일 전송 프로토콜(SMTP) 인프라입니다.
- **네트워크 방화벽 차단** — 현재 WAS 및 APP 서버 양쪽에서 SMTP 포트(25, 587)가 차단되어 있고, 로컬 MTA(Mail Transfer Agent)가 없으며, 기존 프록시 서버(`10.1.50.2:3128`)는 HTTP 전용이라 TCP 릴레이가 불가능한 기술적 제약 사항을 의미합니다.
- **AI Craver 알림** — AI Agent가 업무 수행 중 발생하는 주요 이벤트나 알림을 담당자에게 전달하기 위한 알림 채널 구축 목표입니다.

## How It Fits In
이 클러스터는 독립적인 알림 인프라 레이어를 구성합니다. 현재는 네트워크 격리 상태로 인해 기능이 비활성화되어 있으며, 향후 IT 부서에서 방화벽을 개방하고 발신 계정을 제공하는 시점에 `app/core/mailer.py` 모듈이 활성화되어 프로젝트 전체의 알림 시스템(메일 및 잔디 연동 등)과 연결될 예정입니다.

## Common Questions This Page Answers
- **현재 메일 발송 기능이 작동하지 않는 이유는 무엇인가요?**
  - WAS/APP 서버에서 SMTP 포트(25/587)가 차단되어 있고, 프록시 서버가 HTTP 전용이라 TCP 릴레이를 지원하지 않기 때문입니다.
- **메일 발송 기능을 활성화하려면 어떤 조치가 필요한가요?**
  - IT 부서에 방화벽 개방 및 발신 계정 발급을 요청해야 하며, 상세 요구사항은 `docs/IT_REQUEST_MAIL_JANDI.md` 문서에 정리되어 있습니다.