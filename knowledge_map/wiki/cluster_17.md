# Cluster 17

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 2

## Purpose
SKIN1004 AI Agent 프로젝트에서 사용자의 질문을 바탕으로 상세한 보고서(Report)를 생성하고 관리하는 파이프라인을 제공합니다. 채팅 인터페이스의 가독성을 유지하기 위해 본문을 직접 출력하는 대신, 요약 정보와 링크만을 생성하여 사용자에게 전달하는 역할을 수행합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/README.md` — 보고서 생성 파이프라인의 전체적인 흐름과 아키텍처를 설명하는 문서입니다.
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/service.py` — 채팅 서비스 등 외부 진입점에서 호출되어 질문 분석부터 보고서 생성, 저장, 요약까지의 전체 비즈니스 로직을 실행하는 핵심 서비스 파일입니다.

## Key Concepts
- **보고서 파이프라인 (Report Pipeline)**: 질문 매칭(`match`) → 캐시 확인 → 병렬 데이터 조회 → 품질 게이트(Quality Gate) 검증 → 파생 데이터 생성 → 렌더링 → 저장 → 요약 생성으로 이어지는 일련의 보고서 빌드 프로세스입니다.
- **채팅 최적화 요약 (Chat-optimized Summary)**: 보고서 본문(표, 상세 텍스트 등)을 채팅창에 그대로 출력하면 UI가 깨지거나 가독성이 떨어집니다. 이를 방지하기 위해 LLM을 활용하여 페이로드(Payload) 기반의 짧은 요약 문장과 보고서 다운로드 링크만을 생성하여 반환합니다.

## How It Fits In
이 클러스터는 SKIN1004 AI Agent의 사용자 인터페이스와 데이터 처리 레이어를 연결하는 가교 역할을 합니다. 
- **캐싱 시스템 연동**: `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/service.py`는 보고서 생성 성능을 최적화하고 중복 연산을 방지하기 위해 `concept:report_caching` (cluster_05) 메커니즘을 구현하여 활용합니다.

## Common Questions This Page Answers
- 사용자의 질문으로부터 보고서가 생성되어 최종 전달되기까지의 전체 흐름은 어떻게 되나요?
- 채팅창에 길고 복잡한 보고서 본문이 그대로 노출되지 않도록 제어하는 방법은 무엇인가요?
- 보고서 생성 시 캐싱 레이어는 어떻게 연동되어 동작하나요?