# SKIN1004 AI Agent 업데이트 로그

---

# 2026년 2월 10일 (v5.0.0)

## 1. Dual LLM 아키텍처 도입

### 1.1 모델 구성
Open WebUI 모델 선택에 따라 자동으로 LLM이 결정되는 이중 모델 구조 도입.

| 모델 ID | LLM | 용도 |
|---------|-----|------|
| skin1004-Search | Gemini 2.5 Pro | 매출 조회, Google 검색, 일반 질문 |
| skin1004-Analysis | Claude Sonnet 4.5 | 심층 분석, 복합 추론 |

### 1.2 Flash 모델 경량 작업 분리
Gemini 2.5 Flash를 SQL 생성, 차트 설정, 라우팅 분류 등 속도 민감 작업에 전용 배치.

### 1.3 속도 최적화 결과
| 항목 | 이전 | 변경 후 |
|------|------|---------|
| SQL 쿼리 응답 시간 | 38-42초 | **11-13초** |
| 분류 방식 | LLM 매번 호출 | 키워드 우선 → LLM 폴백 |
| 답변+차트 | 순차 처리 | **병렬 처리** (ThreadPoolExecutor) |
| 스키마 | 매번 BigQuery 조회 | **글로벌 캐시** |

### 1.4 수정된 파일
| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `app/core/llm.py` | 전면 재작성 | GeminiClient + ClaudeClient 이중 클라이언트, `resolve_model_type()` |
| `app/models/agent_models.py` | 수정 | 전 에이전트 Claude Sonnet 4.5 통일 |
| `app/agents/orchestrator.py` | 수정 | `get_llm_client(model_type)` 기반 동적 LLM 선택 |
| `app/agents/router.py` | 수정 | 키워드 우선 분류, Flash 사용 |
| `app/agents/sql_agent.py` | 수정 | Flash로 SQL 생성/포맷, 병렬 차트 생성 |
| `app/api/routes.py` | 수정 | 모델명 skin1004-Search / skin1004-Analysis |

---

## 2. Google Search Grounding 통합

### 2.1 구현 내용
Gemini 2.5 Pro의 네이티브 Google Search grounding 기능 활용. 별도 API 키 불필요.

- `GeminiClient.generate_with_search()` — 외부 정보가 필요한 질문에 실시간 웹 검색 결합
- Multi-source handler: Google Search(외부) + BigQuery(내부) 결과를 합성하여 답변 생성
- 질문 재작성: 복합 질문을 데이터 전용 BigQuery 쿼리와 외부 검색 쿼리로 분리

### 2.2 수정된 파일
| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `app/core/llm.py` | 수정 | `generate_with_search()`, `generate_with_search_history()` 추가 |
| `app/agents/orchestrator.py` | 수정 | multi-source 핸들러, 쿼리 재작성 로직 |

---

## 3. Google Workspace 개별 사용자 OAuth2

### 3.1 MCP → 직접 API 호출 전환
기존 MCP 서버 기반 단일 사용자 GWS 접근을 **개별 OAuth2 인증 + Google API 직접 호출**로 전면 교체.

| 항목 | 이전 (v3.0) | 변경 후 (v5.0) |
|------|------------|----------------|
| 인증 방식 | MCP 단일 환경변수 | 개별 OAuth2 (사용자별) |
| API 호출 | MCP 프록시 | `googleapiclient` 직접 호출 |
| 지원 서비스 | Drive, Gmail, Calendar | Gmail, Drive, Calendar |
| 사용 가능 인원 | 1명 | **전 직원** |

### 3.2 지원 기능
- **Gmail 검색**: `search_gmail(creds, query)` — 메일 제목, 발신자, 날짜, 스니펫
- **Drive 검색**: `search_drive(creds, query)` — 파일명, 유형, 수정일, 링크
- **Calendar 조회**: `list_calendar_events(creds, query)` — 일정 제목, 시간, 장소

### 3.3 신규/수정 파일
| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `app/core/google_auth.py` | **신규** | OAuth2 토큰 관리 (저장/갱신/삭제) |
| `app/core/google_workspace.py` | **신규** | Gmail/Drive/Calendar API 래퍼 |
| `app/agents/gws_agent.py` | 전면 재작성 | MCP → ReAct + 직접 API (Claude Sonnet) |
| `app/api/auth_routes.py` | **신규** | OAuth 엔드포인트 (/login, /callback, /status, /revoke) |

---

## 4. Open WebUI 단일 Google 로그인으로 GWS 통합 (SSO)

### 4.1 구현 내용
Open WebUI의 Google OAuth 로그인 한 번으로 Gmail, Calendar, Drive까지 접근 가능하도록 통합.
별도의 GWS 인증 절차가 불필요해짐.

### 4.2 기술 구현
| 항목 | 설명 |
|------|------|
| OAUTH_SCOPES 확장 | `openid email profile` + `gmail.readonly` + `calendar.readonly` + `drive.readonly` |
| oauth.py 패치 | `authorize_redirect`에 `access_type=offline`, `prompt=consent` 추가 → refresh_token 획득 |
| 토큰 읽기 | FastAPI가 Open WebUI SQLite DB에서 Fernet 암호화 토큰을 복호화하여 사용 |
| 데이터 공유 | Docker bind mount (`C:/openwebui-data`) → 호스트 FastAPI에서 DB 직접 접근 |

### 4.3 인증 흐름
```
사용자 → Open WebUI 로그인 (Google) → Gmail/Calendar/Drive 권한 동의 (최초 1회)
→ 토큰 DB 저장 (refresh_token 포함) → FastAPI가 DB에서 토큰 읽기
→ "이번주 일정?" → GWS Agent가 해당 사용자 토큰으로 Calendar API 호출
```

### 4.4 수정된 파일
| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `app/core/google_auth.py` | 수정 | `_get_credentials_from_openwebui()` 추가 — DB 토큰 읽기 |
| `app/agents/gws_agent.py` | 수정 | 인증 안내 메시지 → "재로그인" 안내로 변경 |
| `app/config.py` | 수정 | `openwebui_db_path`, `openwebui_secret_key` 설정 추가 |
| `.env` | 수정 | `OPENWEBUI_DB_PATH`, `OPENWEBUI_SECRET_KEY` 추가 |

---

## 5. Open WebUI 브랜딩 커스터마이징

### 5.1 로고/파비콘 교체
Open WebUI의 모든 기본 아이콘을 SKIN1004 브랜딩으로 교체.
- favicon.ico, favicon.png, favicon-96x96.png, favicon-dark.png
- logo.png, logo-dark.png, splash.png, splash-dark.png

### 5.2 로그인 페이지 디자인
cravercorp.com 벤치마킹 CSS 애니메이션 적용 (CSS-only, 커스텀 JS 없음):
- **배경**: 검정 + 그라데이션 애니메이션
- **텍스트 애니메이션**: "WHAT" / "DO YOU" / "CRAVE?" 3단 슬라이드 등장 (CSS keyframes)
- **로그인 카드**: 글라스모피즘 (blur 50px, 반투명 검정 배경)
- **악센트 컬러**: #e89200 (골드)

### 5.3 앱 이름
`SKIN1004 AI (Open WebUI)` → **`SKIN1004 AI`** (env.py 패치로 접미사 제거)

### 5.4 사용자 이메일 전달
Open WebUI → FastAPI 간 사용자 이메일 전달 활성화.
- `ENABLE_FORWARD_USER_INFO_HEADERS=true` 설정
- 미들웨어에서 `X-OpenWebUI-User-Email` 헤더 + body `user` JSON 파싱

### 5.5 관련 파일
| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `custom_login.css` | **신규** | cravercorp 벤치마킹 로그인 CSS |
| `app/api/middleware.py` | 수정 | user_email 추출 (헤더 + body JSON 파싱) |
| Docker 컨테이너 내부 | 패치 | env.py (이름 접미사), oauth.py (access_type), 로고/CSS |

---

## 6. Docker 구성 변경

### 6.1 Open WebUI 컨테이너 재구성
| 항목 | 이전 | 변경 후 |
|------|------|---------|
| 데이터 저장 | Docker volume (`open-webui-data`) | **Bind mount** (`C:/openwebui-data`) |
| Secret Key | 자동 생성 (파일) | **명시적 설정** (env var) |
| OAuth Scopes | `openid email profile` | + `gmail.readonly calendar.readonly drive.readonly` |
| 재시작 정책 | -- | `--restart unless-stopped` |

---

## 7. 현재 구현 현황

| 항목 | 상태 | 비고 |
|------|------|------|
| Phase 1: 인프라 및 환경 구성 | **DONE** | BigQuery, Gemini, FastAPI, Docker |
| Phase 2: Text-to-SQL Agent | **DONE** | LangGraph, SQL 생성/검증/실행/포맷, 차트 |
| Phase 2+: 차트 시각화 | **DONE** | ChatGPT 라이트 테마, 30색, 레전드 정렬 |
| Phase 2+: 스키마 관리 | **DONE** | Excel 기반 26개 컬럼 화이트리스트 |
| Phase 2+: 속도 최적화 | **DONE** | 38s → 11s (키워드 분류, Flash, 병렬, 캐시) |
| Phase 3: RAG 파이프라인 | TODO | Docling 파서, BGE-M3 임베딩, BigQuery 벡터 인덱스 |
| Phase 4: API + 프론트엔드 | **DONE** | FastAPI + Open WebUI + Google SSO 완료 |
| Phase 4+: Dual LLM | **DONE** | Gemini 2.5 Pro + Claude Sonnet 4.5 |
| Phase 4+: Google Search | **DONE** | Gemini 네이티브 grounding |
| Phase 4+: GWS 개별 OAuth | **DONE** | 단일 Google 로그인으로 Gmail/Drive/Calendar |
| Phase 4+: 브랜딩 커스텀 | **DONE** | 로고, 로그인 CSS, 모델명 |
| Phase 5: 테스트 및 최적화 | TODO | RAGAS 평가, 부하 테스트 |

---
---

# 2026년 2월 6일 (v4.0.0)

## 1. 차트 시각화 개선

### 1.1 ChatGPT 스타일 디자인 적용
- **배경색:** 흰색 (#FFFFFF)
- **글꼴:** Arial (깔끔한 가독성)
- **그리드:** 연한 회색 점선

### 1.2 데이터 레이블 표시
- 차트 위에 데이터 값 직접 표시
- **숫자 포맷:**
  - 일반 숫자: 소수점 없음 (예: 1,234,567)
  - 축약 표시: K(천), M(백만), B(십억)
  - 퍼센트: 소수점 1자리 (예: 12.5%)

### 1.3 색상 팔레트 확장
- 기존 10색 → **30색**으로 확장
- 중복 없는 고유 색상 사용
- 데이터 시리즈 구분 명확화

### 1.4 레전드 개선
- **위치:** 차트 오른쪽 (겹침 방지)
- **정렬:** 매출 높은 순 (내림차순)
- **동적 이미지 크기:** 레전드 10개 이상 시 1300x700px

### 1.5 수정된 파일
| 파일 | 변경 내용 |
|------|----------|
| `app/core/chart.py` | Plotly 기반 차트 생성, 스타일링, 색상 팔레트 |
| `app/agents/sql_agent.py` | PNG 이미지 마크다운 출력 |

---

## 2. 데이터 조회 제한 확대

### 2.1 변경 내용
SQL 쿼리 결과 최대 행 수를 확대하여 CSV 다운로드 시 전체 데이터 제공

| 설정 | 이전 | 변경 후 |
|------|------|---------|
| MAX_RESULT_ROWS | 1,000행 | **10,000행** |
| BigQuery max_rows | 1,000행 | **10,000행** |
| SQL Agent max_rows | 1,000행 | **10,000행** |

### 2.2 수정된 파일
| 파일 | 변경 내용 |
|------|----------|
| `app/core/security.py` | MAX_RESULT_ROWS = 10000 |
| `app/core/bigquery.py` | execute_query 기본값 max_rows=10000 |
| `app/agents/sql_agent.py` | execute_query 호출 시 max_rows=10000 |

---

## 3. 기술 상세

### 3.1 차트 색상 팔레트 (30색)
```python
COLORS = [
    "#6366f1",  # Indigo
    "#f59e0b",  # Amber
    "#10b981",  # Emerald
    "#ef4444",  # Red
    "#8b5cf6",  # Violet
    "#06b6d4",  # Cyan
    "#f97316",  # Orange
    "#84cc16",  # Lime
    "#ec4899",  # Pink
    "#14b8a6",  # Teal
    # ... 총 30색
]
```

### 3.2 숫자 포맷 함수
```python
def _format_short(val: float) -> str:
    if abs(val) >= 1e9:
        return f"{int(val / 1e9)}B"
    elif abs(val) >= 1e6:
        return f"{int(val / 1e6)}M"
    elif abs(val) >= 1e3:
        return f"{int(val / 1e3)}K"
    return str(int(val))
```

### 3.3 레전드 정렬 로직
```python
# 그룹별 총합 계산 후 내림차순 정렬
group_totals = {g: sum(values) for g, values in data.items()}
groups = sorted(groups, key=lambda g: group_totals[g], reverse=True)
```

---

## 4. 테스트 방법

### 4.1 차트 테스트
Open WebUI에서 다음 질문 테스트:
- "2025년 몰별 월별 매출 그래프"
- "올해 채널별 매출 비교 차트"

### 4.2 CSV 데이터 테스트
- 대용량 데이터 조회 후 CSV 다운로드
- 10,000행 이하 데이터 전체 반환 확인

---

## 5. 주의사항

- 10,000행 초과 데이터는 자동으로 잘림
- 대용량 조회 시 응답 시간 증가 가능
- 차트는 PNG 이미지로 제공 (Open WebUI 호환)

---

**작성자:** Claude AI
**검토일:** 2026-02-06
