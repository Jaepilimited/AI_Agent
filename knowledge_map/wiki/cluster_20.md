# Cluster 20

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 5

## Purpose
사용자 질문으로부터 생성되는 상세 보고서의 라이프사이클(조회·렌더링·보안 격리·공유)과 인앱 및 잔디(Jandi) 알림 발송 파이프라인을 담당합니다. 또한 실행 노드와 실제 레지스트리를 연결하는 워크플로우 명세를 선언하여 에이전트의 구조화된 실행 흐름을 정의합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/service.py` — 질문 분석부터 병렬 조회, 품질 검증, 렌더링, 저장 및 채팅용 요약 추출까지 수행하는 보고서 생성 진입점
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/reports_api.py` — 보고서 생성, 목록, 열람, 공유 API 엔드포인트 및 엄격한 사용자 권한 검증 구현
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/api/notifications_api.py` — 인앱 알림 API (공유받은 보고서, 붐따 처리 상태, 업데이트 공지)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/jandi_notify.py` — 출근 브리핑 및 셀라 알림을 잔디 웹훅 대기열로 중계하는 알림 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/flow/spec.py` — 실제 실행 함수 및 레지스트리와 매핑되는 워크플로우 노드/엣지 선언 명세

## Key Concepts
- **Strict Report Access Control**: 보고서는 원가·마진·거래처별 FOC율 등 민감 정보를 포함하므로 admin 예외 없이 작성자와 지정된 대상만 접근 가능합니다. 판정은 `store.get_for_user(report_id, user_id)` 단일 지점에서 강제됩니다.
- **Payload-based Summary & Link Delivery**: 채팅창에는 본문 전체를 노출하지 않고 payload 기반의 요약 몇 줄과 보고서 열람 링크만 반환하여 가독성과 표 깨짐 문제를 방지합니다.
- **In-App Notifications over SMTP**: WAS·APP 서버의 SMTP 포트(25/587) 차단 및 메일 발송 인프라 부재로 인해 외부 메일 대신 인앱 알림을 기본 수단으로 사용합니다.
- **Jandi Notification Relay**: 출근 브리핑 열람률 및 질문 전환율을 높이기 위해 브리핑과 동일한 릴레이 대기열을 공유하여 잔디로 알림을 전송합니다.
- **Runtime-extracted Flow Specification**: 흐름 선언 시 가상의 엣지를 임의 작성하지 않으며, LangGraph 서브그래프(`subgraph=`)를 통해 런타임에서 하위 단계를 검증 및 추출합니다.

## How It Fits In
- **Cluster 06 (`concept:html_report_rendering`)**: `app/api/reports_api.py` 및 `app/reports/service.py`에서 생성된 보고서 데이터를 시각화하고 HTML 렌더링 파이프라인과 연계합니다.
- **Cluster 18 (`concept:query_routing`)**: `app/flow/spec.py`가 선언한 노드 체계가 질문 라우팅 엔진의 실행 단계와 연결됩니다.
- **Cluster 24 (`concept:jwt_auth`)**: `app/api/notifications_api.py`와 `app/api/reports_api.py`에서 사용자 인증 및 보고서 소유권 검증 시 JWT 인증 체계를 참조합니다.

## Common Questions This Page Answers
- Q: 관리자(admin) 계정으로 다른 사용자의 보고서를 열람할 수 있나요?
  - A: 불가합니다. 2026-08-12 결정에 따라 원가 및 마진 보호를 위해 admin도 예외 없이 작성자가 공유한 사람만 열람할 수 있습니다.
- Q: 이메일 알림이 전송되지 않는 이유는 무엇인가요?
  - A: WAS/APP 전 구간 SMTP 차단 및 로컬 MTA 부재로 인해 메일 발송이 불가능하여 `notifications_api.py`를 통한 인앱 알림 및 잔디 웹훅을 사용합니다.
- Q: 채팅 인터페이스에 보고서 전체 테이블이 직접 출력되지 않는 이유는 무엇인가요?
  - A: 대화창 표 깨짐을 방지하기 위해 `service.py`에서 LLM을 거치지 않고 payload에서 추출한 핵심 요약과 링크만 전달합니다.