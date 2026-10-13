# Cluster 19

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 7

## Purpose
외부 서비스 연동(Google Workspace, Notion 저장)과 보고서 생성 전후 처리(질의 확인, 동적 UI 결합), 그리고 API 스키마를 담당하는 모듈 그룹입니다. 무거운 연산이나 LLM 호출 전 사용자 의도를 명확히 검증하고, 생성된 결과물을 메일 또는 노션 등 외부 채널로 안전하게 전달하는 브릿지 역할을 수행합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/google_workspace.py` — 자격 증명을 받아 Gmail, Google Drive, Calendar API를 호출하는 무상태(stateless) 래퍼 함수군
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/mail_template.py` — 구글 Docs 내보내기 zip HTML을 Gmail 규격(클래스를 인라인 style로 변환, 만료되는 contentUri 대신 zip 내 이미지를 cid로 첨부)으로 변환하는 규칙 기반 템플릿 처리기
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/notion_save.py` — 라우터보다 먼저 실행되어 일반 노션 검색과 노션 저장 의도를 분리해 처리하는 진입 관문
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/clarify.py` — 보고서 생성 전 다중 조회를 방지하기 위해 핵심 기준을 묻고, 직전 assistant 숨은 표식으로 답변을 병합하는 사전 확인 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/share_ui.py` — 파일에 정적으로 굽지 않고 `read_report` 응답 시점에 사용자별 권한/상태에 맞춰 동적으로 삽입하는 보고서 공유 막대 UI
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/reports/specs/__init__.py` — 보고서 명세 패키지 초기화 파일
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/models/schemas.py` — OpenAI 호환 엔드포인트를 위한 Pydantic 요청/응답 스키마 정의

## Key Concepts
- `notion_save_intent` — `노션` 키워드가 단순 사내 문서 검색 라우트로 빠지지 않고, 저장 동사가 결합되었을 때 라우터 이전 단계에서 가로채는 저장 전용 의도 판별
- `clarify_before_report` — 비용이 큰 보고서 플래닝 및 다중 조회를 시작하기 전, 세션 DB 없이 직전 메시지의 숨은 표식을 활용해 최소한의 확인 질문을 거치는 메커니즘
- `dynamic_share_bar` — 정적 HTML 파일에 공유 버튼을 하드코딩하지 않고 응답 서빙 시점에 동적으로 주입하여, 열람자 권한 왜곡(공유받은 사람에게 공유 버튼이 노출되는 문제)을 방지하는 렌더링 방식
- `gmail_html_inlining` — Gmail의 제한적인 `<style>` 태그 처리를 극복하기 위해 CSS 클래스를 인라인 스타일로 전개하고 만료 URL 대신 cid 이미지를 첨부하는 변환 규칙

## How It Fits In
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/notion_save.py`는 `cluster_33`의 `concept:notion_save_intent`를 구현하여, 노션 검색 라우터 진입 전에 저장 트리거를 우선 가로챕니다.
- `clarify.py`에서 확인된 기준은 메인 플래너 및 기간·필터 추출기로 전달되어 보고서 명세(`specs`) 생성 파이프라인의 입력이 됩니다.
- 완성된 보고서는 `share_ui.py`를 거쳐 동적 UI와 함께 응답되거나, `mail_template.py` 및 `google_workspace.py`를 통해 Gmail/외부 채널로 발행됩니다.

## Common Questions This Page Answers
- "노션에 저장해줘"라는 명령이 왜 사내 노션 검색 라우트로 빠지지 않고 바로 저장 처리가 되는가?
- 보고서 HTML에 공유 버튼이 정적으로 저장되지 않고 매번 동적으로 끼워 넣어지는 이유는 무엇인가?
- 보고서 생성 전에 사용자에게 추가 질문을 던지는 기준과 이전 맥락을 기억하는 방식은 무엇인가?
- 구글 문서 내보내기 결과물을 Gmail 본문으로 보낼 때 스타일과 이미지가 깨지지 않도록 어떻게 처리하는가?