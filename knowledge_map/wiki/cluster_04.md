# Cluster 04

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 14

## Purpose
애플리케이션 전역 설정(`config.py`), 코어 런타임 유틸리티(정적 자산 캐싱, 배포 점검, 응답 포맷팅), 멀티모달 얼굴/이미지 임베딩 및 검색 에이전트를 담당합니다. 또한 사용자 피드백 기반 스킬 메모리와 프로젝트 정적 분석을 위한 Knowledge Map 파서를 제공합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/config.py` — 전역 환경 설정 관리 (JWT, Entra ID, LLM 게이트웨이 연동)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/face_clip_agent.py` — Drive 인덱스 기반 CLIP 및 InsightFace 얼굴/제품 검색 에이전트
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/agents/skill_memory.py` — Hermes 스타일 사용자 피드백(👍/👎) 기반 few-shot/negative 프롬프트 메모리
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/face_embed.py` — C++ 컴파일 종속성을 우회한 buffalo_l ONNX 직접 추론 얼굴 임베딩
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/asset_version.py` — 정적 자산(CSS/JS) 캐시 무효화를 위한 버전 지문 관리
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/deploy_preflight.py` — 배포 전 구문 오류 및 코드 오염 방지 사전 점검
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/file_request.py` — 엑셀 추출 요청 감지 및 파일 다운로드 링크 강제 생성 처리
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/mail_mime.py` — 인라인 이미지(cid) 및 첨부파일 MIME 이메일 조립 유틸리티
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/org_structure.py` — 확정된 조직/팀 명칭 및 국가 스코프 기준 정보(Source of Truth)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/response_formatter.py` — 프론트엔드 렌더링 품질을 위한 마크다운 후처리
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/dashboard_links.py` — 대시보드 탭 링크 카탈로그 및 확정형 답변 생성
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/ast_parser.py` — Python AST 파서 (클래스, 함수, 임포트 추출)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/md_parser.py` — Markdown 구조 파서 (헤딩, 링크 추출)
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/knowledge_map/config.py` — Knowledge Map 빌드 설정 및 제외 패턴 정의

## Key Concepts
- **buffalo_l ONNX Inference**: Python 3.12 환경의 WAS에서 C++ 컴파일이 필요한 `insightface` 패키지 없이 동일 ONNX 가중치로 얼굴 임베딩 수행.
- **Skill Memory**: `agent_skills` 테이블을 활용하여 👍 평가는 라우트별 우수 few-shot으로, 👎 평가는 회피 패턴(negative constraints)으로 프롬프트에 주입.
- **Preflight Verification**: `/health` 엔드포인트만으로 검출하기 어려운 작업 트리 동시 편집 사고(클래스 내부 함수 유입 등)를 배포 직전 감지.
- **Deterministic Answers**: 공식 조직 구조(`org_structure.py`) 및 대시보드 링크에 대해 LLM 환각 없이 규칙 기반으로 반환.

## How It Fits In
- `app/config.py`는 `cluster_03`(JWT 인증), `cluster_16`(Entra ID 인증), `cluster_25`(LLM 게이트웨이)의 설정 기준점이 됩니다.
- `app/agents/face_clip_agent.py`는 `cluster_03`의 OCR 재순위화(`concept:ocr_reranking`)와 연결됩니다.
- `app/core/asset_version.py`는 `cluster_18`의 프론트엔드 캐시 버스팅(`concept:cache_busting`)을 지원합니다.

## Common Questions This Page Answers
- C++ 빌드 환경이 없는 서버에서 얼굴 인덱싱 및 유사도 검색을 어떻게 실행하는가?
- 사용자가 "엑셀로 뽑아줘"라고 질의할 때 텍스트 표 대신 실제 파일 링크를 제공하는 방식은 무엇인가?
- 다중 세션 편집 시 잘못된 구문 변경이 프로덕션 배포에 반영되는 것을 어떻게 사전에 차단하는가?