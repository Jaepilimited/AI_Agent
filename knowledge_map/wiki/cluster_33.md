# Cluster 33

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 6

## Purpose
노션(Notion) 연동을 통해 출근 브리핑의 자동 배송과 채팅 질의응답 저장을 처리하는 모듈 및 설계 문서 모음이다. 잔디(Jandi) 릴레이 방식과 달리 WAS가 프록시를 통해 직접 노션에 쓰는 저장 엔진과 발송 큐 메커니즘을 정의한다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/notion_briefing.py` — 출근 브리핑 노션 전달, 사용자별 대상 페이지 및 발송 대기열 관리
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/notion_export.py` — 노션 쓰기 전용 단일 저장 엔진 (브리핑 배송 및 채팅 저장 공용)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/notion_unshared_pages.md` — 2026-08-25 실측 기준 노션 인테그레이션 미공유 페이지 목록
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-09-07-notion-export-phase1.md` — 저장 엔진 및 채팅 저장 경로 구현 계획 (Phase 1)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-09-08-notion-briefing-phase2.md` — 출근 브리핑 자동 배송 구현 계획 (Phase 2)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/specs/2026-09-07-notion-export-design.md` — 노션 연동 전체 기능 설계서 (2026-09-07)

## Key Concepts
- **Notion Export Engine**: `notion_export.py` 기반의 공용 저장 레이어로, 채팅 답변 저장과 브리핑 배송이 동일한 인프라를 사용한다.
- **Notion API Version `2025-09-03`**: 구버전(`2022-06-28`)의 `parent: {database_id}` 방식은 단일 소스 DB에 추가 소스가 붙을 경우 행 생성이 실패하므로, 다중 데이터 소스 호환을 위해 반드시 `2025-09-03` 헤더를 유지한다.
- **Direct WAS Writes vs. Jandi Relay**: 잔디는 DB_PC 릴레이가 `pending()`을 통해 대기열을 가져가 웹훅으로 쏘는 구조지만, 노션은 WAS가 프록시를 통해 API를 직접 호출하므로 릴레이에 의존하지 않고 전송 대기열을 완전히 분리한다.

## How It Fits In
- **Cluster 08 (Scheduling & Outbox)**: `notion_briefing.py`는 Cluster 08의 `outbox_pattern`과 `briefing_scheduling` 개념을 구체적으로 구현하여 예약된 출근 브리핑을 노션 타깃 페이지로 안정적으로 밀어 넣는다.

## Common Questions This Page Answers
- 잔디 발송 큐와 노션 발송 큐를 하나의 테이블로 합치지 않고 분리하는 이유는 무엇인가?
- 노션 API 요청 시 버전 헤더를 `2022-06-28` 대신 `2025-09-03`으로 고정해야 하는 이유는 무엇인가?
- 채팅 저장과 출근 브리핑 배송이 공유하는 핵심 쓰기 모듈은 무엇인가?