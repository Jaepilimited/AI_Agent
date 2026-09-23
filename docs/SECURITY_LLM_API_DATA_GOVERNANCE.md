# 셀라(AI Craver) 외부 LLM API 보안 대응 문서

| | |
|---|---|
| 작성일 | 2026-09-10 |
| 대상 | 보안 검토자 · IT팀 · 법무 · 개발 |
| 범위 | Anthropic Claude API · Google Gemini API 호출 경로의 데이터 보호 현황과 계약 조건 |
| 상태 | **검토 요청** — 4절의 "확인 필요" 항목이 닫히면 확정 |
| 관련 코드 | `app/core/llm.py` · `app/core/security.py` · `app/agents/sql_agent.py` · `app/agents/orchestrator.py` |

---

## 1. 요약

**지적사항**: "클로드·제미나이 API를 가져다 쓰는데 보안 가드레일이 없다."

**판정**: 절반은 맞고 절반은 틀리다.

- **접근 통제·데이터 범위 통제는 코드로 강제되어 있다.** 인증 없는 호출 불가, SQL은 SELECT만, 테이블·행 단위 화이트리스트, 손익 데이터 5겹 방어, 출력 마스킹, 사내 프록시 화이트리스트. 이 부분에서 "없다"는 사실이 아니다.
- **LLM 고유 위협에 대한 방어는 비어 있다.** 프롬프트 인젠션 필터, 외부 전송 전 개인정보 마스킹, 출력 모더레이션, 사용자별 호출 한도가 없다. 이 부분에서 지적은 맞다.
- **외부 API 계약 조건은 유료 기준으로 "학습 미사용"이 기본값이지만 "미보존"은 아니다.** Anthropic 30일, Google 55일 보존. 둘 다 별도 신청(ZDR)으로만 줄일 수 있다.
- **우리 계정의 실제 설정 두 가지가 미확인이다.** Gemini 프로젝트의 유료 티어 여부, Anthropic 콘솔의 보존 기간 설정. 이 둘이 이 문서에서 가장 먼저 닫아야 할 항목이다.

---

## 2. 현재 보호 수단 (코드 강제)

프롬프트 지시가 아니라 **코드가 막는 것**만 적었다. 프롬프트는 확률이고 보증은 코드라는 것이 이 프로젝트의 원칙이다.

| 층 | 무엇을 막나 | 구현 위치 |
|---|---|---|
| 인증 | 로그인(Entra ID) 없이는 채팅 엔드포인트에 닿을 수 없다. 공통 미들웨어가 화면·API를 보호한다 | `app/api/routes.py:111` · `app/core/session_auth.py` |
| SQL 실행 | SELECT만 허용. INSERT/UPDATE/DELETE/DROP 등 차단. 스택 쿼리·블록 주석·`INFORMATION_SCHEMA` 차단. 결과 1만 행·30초 상한 | `app/core/security.py` (`validate_sql`) |
| 테이블 범위 | 사용자·데이터소스별 테이블 화이트리스트. 목록 밖 테이블은 실행 전 거절 | `security._validate_tables` · `sql_agent._allowed_tables_from_sources` |
| 행 범위 | 브랜드 그룹(SK/DD)으로 조회 범위를 좁힌다. 그룹 미배정 신규 계정은 조회 불가 | `sql_agent` `brand_filter` · `app/core/user_directory.py` |
| 손익(FI) | 승인자만. 키워드 감지 거절 → 프롬프트 스키마 제거 → 화이트리스트 제외 → 실행 직전 재검사 → 프론트 숨김, 5겹 | `orchestrator._requests_fi_data` · `security.validate_sql` 등 |
| 출력 | 내부 테이블 경로 마스킹, 수치가 조회 결과에 없으면 표시·교정, 통화 혼재 합계 차단, 보고서는 LLM이 숫자를 쓸 수 없는 구조 | `sql_agent._mask_internal_paths` · `app/core/answer_check.py` · `app/reports/` |
| 망 | WAS → 외부는 사내 프록시(10.1.50.2:3128) 화이트리스트만 통과. 임의 도메인 전송 불가 | 인프라 (IT팀) |
| 동시성 | 공급자 호출 동시 상한 Gemini 30 · Claude 20 | `app/core/llm.py:32-33` |
| 감사 | 사용자·경로·질문·모델·소요시간 기록 | `audit_logs` (`app/api/routes.py:407`) |
| 비밀 | API 키는 `.env`에만 있고 배포 전송에서 제외된다 | `scripts/deploy_new_server.py` `EXCLUDE_FILES` |

---

## 3. 외부로 나가는 데이터

| 경로 | 수신자 | 전송 내용 | 개인정보 포함 |
|---|---|---|---|
| 채팅 직접 대화(direct) | Anthropic (Claude Opus 5) | 사용자 질문·대화 이력·시스템 프롬프트·첨부 이미지 | 사용자가 붙여넣는 만큼 |
| Text-to-SQL | Google (Gemini) · Anthropic | 질문·스키마·조회 결과 행(매출·원가·수량 등) | 거래처명 등 기업 정보. 직원 개인정보는 원칙상 없음 |
| 검색 그라운딩 | Google (Gemini Pro) | **사용자 질문 텍스트만** | 질문에 든 만큼 |
| 출근 브리핑 | Google (Gemini Flash) | Gmail 발신자 표시명·제목·미리보기 500자, 일정 제목·장소·설명 300자 | **있음** — 직원·거래 상대방 이름, 메일 내용 일부 |
| 사내 문서 검색(RAG) | Google · Anthropic | 노션 문서 청크, CS 시트 Q&A, 제품정보 | 문서에 든 만큼 |
| 보고서 해석 | Anthropic | 집계 결과(숫자는 코드가 만든다) | 없음 |

- 메일·일정은 **본문·첨부를 받지 않는다** (`format="metadata"`). 미리보기(snippet)까지만이다.
- 검색 그라운딩 경로에는 매출 행·메일이 들어가지 않는다. 질문 텍스트만 간다 (`orchestrator._gather_search_context`).
- 모든 전송은 **처리 위치가 해외**다. 두 공급자 모두 한국 리전을 제공하지 않는다.

---

## 4. 외부 API 데이터 처리 계약 조건 (2026-09-10 공식 문서 기준)

| 항목 | Anthropic API (Claude Opus 5) | Google Gemini Developer API |
|---|---|---|
| 학습 사용 | **금지** — 상업 약관 B조 "Anthropic may not train models on Customer Content from Services" | **유료 티어만 금지** — "Google doesn't use your prompts or responses to improve our products". **무료 티어는 제품 개선·학습에 사용하고 사람이 읽을 수 있다** |
| 콘텐츠 소유 | 입력은 고객 보유, 출력은 고객 소유. 고객 콘텐츠 = 고객의 기밀정보 | 유료: Cloud DPA 하에서 Google이 수탁자(processor) |
| 기본 보존 | 입력·출력 **30일** 후 자동 삭제 | 남용 감시용 **55일** (프롬프트·컨텍스트·응답) |
| 보존 예외 | 자동 안전 시스템 플래그 시 최대 **2년**, 분류 점수 **7년**. 법적 요구 | 플래그 시 Google 직원이 검토 |
| ZDR (무보존) | Sales 통해 **조직 단위** 신청·승인. Opus 5는 Covered Model(Mythos 5·Fable 5/5.1)이 아니라 **대상 가능**. 승인돼도 안전 분류기 결과는 보존 | **프로젝트 단위** 신청·승인. 승인 시 콘텐츠·식별 메타데이터를 로깅 전 삭제. **단 Google Search 그라운딩 사용분은 30일 저장, 비활성화 불가** |
| 처리 역할 | Anthropic = 수탁자, 고객 = 관리자. DPA가 약관에 자동 편입 | 유료: Google = 수탁자 |
| 보안 조치 | AES-256(저장) · TLS 1.2+(전송) · MFA · SIEM. 침해 **48시간 내** 통지 | Google Cloud 표준 |
| 데이터 위치 | 명시 없음 (DPA 준거법 아일랜드). 한국 리전 없음 | 명시 없음. 한국 리전 없음 |
| 서브프로세서 | anthropic.com/subprocessors | Google Cloud 서브프로세서 목록 |
| 설정 확인 위치 | Console > Settings > Privacy Controls > Data retention period | AI Studio > API keys 페이지 > Plan 열 (`Paid` 표시) |

### 우리 환경에 적용하면

- **Claude**: 현재 `claude-opus-5`(`ANTHROPIC_OPUS_MODEL`)를 쓴다. 학습 미사용은 약관 기본값. 보존 30일은 ZDR 신청으로 줄일 수 있다.
- **Gemini**: `gemini-3.1-pro-preview`(검색 그라운딩) · `gemini-3.8-flash`(내부 작업)를 **API 키 방식(Developer API)** 으로 쓴다. **유료 티어 여부가 보호 수준을 결정한다.** 무료면 브리핑 경로의 메일 제목·미리보기가 제품 개선에 쓰일 수 있다.
- **검색 그라운딩 30일 보존은 ZDR로도 남는다.** 다만 그 경로에는 질문 텍스트만 가므로 노출 범위는 좁다.

### 사내 LLM 게이트웨이 경유 (2026-09-23 추가)

IT팀이 보안 지적에 대한 답으로 LLM 게이트웨이(`llmgw.cravercorp.com`, 노션 `llm-chatbot-integration`)를 세웠다. Claude 호출이 이곳을 거치면 **가드레일 탐지·Langfuse 감사 로깅·키 단위 사용량 집계가 코드 수정 없이 붙는다.** 5절의 G4(인젝션 필터)·G6(모더레이션)·G5(호출 한도)가 이 한 번의 전환으로 상당 부분 닫힌다.

| | 상태 |
|---|---|
| 코드 | 전환 완료. `app/core/llm_gateway.py` 전송층 + `LLM_GATEWAY_URL`·`LLM_GATEWAY_KEY` 설정. 둘 다 있을 때만 켜지고, 없으면 종전대로 Anthropic 직접 호출 |
| 프로덕션 | **꺼짐.** 발급된 가상 키가 아직 모델 접근 권한이 없다 (2026-09-23 실측: 8가지 요청 전부 `403 team not allowed to access model`). 운영자가 키에 `claude-opus-5`·`claude-sonnet-5` 접근과 웹서치용 `default_tools` 그룹을 부여해야 한다 |
| 감시 | 자가 점검 `llm_gateway`가 매일 작은 요청 1회로 키·통신을 확인한다. 403이면 채팅이 통째로 서므로 재시도·폴백 없이 즉시 드러나게 두었다 |
| 범위 | **Claude만.** Gemini(SQL 생성·라우팅·브리핑·검색 그라운딩)는 여전히 Google 직접 호출이다. 4절의 Gemini 조건은 그대로 유효하다 |

---

## 5. 식별된 공백과 위험

| # | 공백 | 위험 | 현재 완화 요소 | 우선순위 |
|---|---|---|---|---|
| G1 | **Gemini 유료 티어 미확인** | 무료면 메일 제목·미리보기·질문이 학습·인간 검토 대상 | 없음 | **즉시** |
| G2 | **Anthropic 보존 설정 미확인** | 기본 30일 보존. 플래그 시 2년 | 학습 미사용은 약관 보장 | **즉시** |
| G3 | 외부 전송 전 개인정보 마스킹 없음 | 브리핑 경로의 직원·거래상대 이름, 메일 내용 일부가 해외 처리 | 메타데이터만 받음(본문·첨부 제외), 읽기 전용 | 단기 |
| G4 | 프롬프트 인젹션 필터 없음 | 노션·메일·시트 내용에 심긴 지시문이 답변을 조작할 수 있다 | 도구가 읽기 전용, SQL은 실행 전 검증, 쓰기 동작은 노션 저장 하나뿐 | 단기 |
| G5 | 사용자별 호출 한도 없음 | 한 계정이 비용을 폭주시킬 수 있다 | 로그인 필수(사내 직원만), 전역 동시성 상한 | 단기 |
| G6 | 출력 콘텐츠 모더레이션 없음 | 유해 답변 억제가 프롬프트 한 줄에 의존 | 사내 BI 도구라 노출 범위가 좁다 | 중기 |
| G7 | 국외 처리 위탁 고지 | 직원 개인정보(메일·일정)가 해외 처리자에게 간다 | — | 법무 확인 |
| G8 | **LLM 게이트웨이 키에 모델 접근 권한 없음** | 게이트웨이 전환(G4·G5·G6 완화)이 켜지지 못한다 | 코드는 준비됨, 직접 호출로 정상 서비스 중 | **즉시** |

---

## 6. 조치 계획

### 즉시 (이번 주)

| # | 조치 | 담당 | 완료 확인 |
|---|---|---|---|
| A1 | AI Studio API keys 페이지에서 Gemini 프로젝트 Plan이 `Paid`인지 확인. 아니면 Cloud Billing 연결 | 개발 | 화면 캡처 첨부 |
| A2 | Anthropic Console > Privacy Controls에서 보존 기간 확인·기록 | 개발 | 화면 캡처 첨부 |
| A3 | 이 문서를 보안 검토자에게 전달하고 지적사항의 범위를 확정 (접근 통제 vs LLM 고유 위협) | 프로젝트 오너 | 회신 |
| A4 | 플랫폼 운영자에게 게이트웨이 키의 모델 접근(`claude-opus-5`·`claude-sonnet-5`)과 `default_tools` 그룹 부여 요청. 권한이 열리면 WAS `.env`에 `LLM_GATEWAY_URL`·`LLM_GATEWAY_KEY`를 넣고 재기동 → 골든 전체 런 → Langfuse 로깅 확인 | 프로젝트 오너 · 개발 | 자가 점검 `llm_gateway` 정상 |

### 단기 (1개월)

| # | 조치 | 담당 | 완료 확인 |
|---|---|---|---|
| B1 | Anthropic ZDR 신청 (Opus 5 대상 가능). 조직 단위 | 프로젝트 오너 | Console 설정이 ZDR로 표시 |
| B2 | Gemini ZDR 신청 여부 결정. 그라운딩 30일은 남는다는 점을 전제로 | 프로젝트 오너 | 결정 기록 |
| B3 | 브리핑 경로에서 LLM에 넘기는 메일 필드 최소화 검토 — 발신자 표시명을 제거하거나 해시로 대체, 미리보기 길이 축소 | 개발 | `work_briefing.py` 변경 + 회귀 테스트 |
| B4 | 사용자별 일일 호출 상한(예: 건수·토큰) 도입. 초과 시 안내 | 개발 | `audit_logs` 기준 집계 + 차단 테스트 |
| B5 | 프롬프트에 삽입되는 외부 텍스트(노션·메일·시트)에 지시문 패턴 탐지·표시. 막지 않고 로그와 답변 공시부터 | 개발 | `search_empty`와 같은 WARNING 로그 + 공시 문구 |

### 중기 (분기)

| # | 조치 | 담당 |
|---|---|---|
| C1 | 국외 처리 위탁 고지 필요성 법무 검토 (개인정보보호법 기준). 필요하면 사내 고지 문구 작성 | 법무 |
| C2 | 출력 모더레이션 필요성 재평가. 사내 도구 범위에서 실제 사고 사례가 없으면 보류 결정 기록 | 프로젝트 오너 |
| C3 | 분기마다 두 공급자의 약관·보존 정책 변경 확인. 2026-06-09 Anthropic Covered Model 정책처럼 조용히 바뀐다 | 개발 |

---

## 7. 확인 필요 (이 문서가 단정하지 않은 것)

- Gemini 프로젝트의 티어 (A1)
- Anthropic 조직의 보존 설정 (A2)
- 두 공급자의 실제 처리 리전 — 문서에 명시가 없다
- 국외 이전 고지 의무 해당 여부 — 법무 판단
- 게이트웨이가 thinking 깊이(`reasoning_effort`)와 시스템 프롬프트 캐시 블록을 받는지 — 키 권한이 없어 실측 못 함 (A4)
- WAS 프록시가 `llmgw.cravercorp.com`(Cloudflare 경유 공개 호스트)을 통과시키는지 — 같은 이유로 실측 못 함

이 항목들이 닫히기 전까지 이 문서는 "검토 요청" 상태다.

---

## 8. 출처

- Anthropic Commercial Terms of Service — https://www.anthropic.com/legal/commercial-terms
- Anthropic Data Processing Addendum — https://www.anthropic.com/legal/data-processing-addendum
- Anthropic 개인정보 보존 기간 — https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-personal-data
- Anthropic ZDR 적용 범위 — https://privacy.claude.com/en/articles/8956058-i-have-a-zero-data-retention-agreement-with-anthropic-what-products-does-it-apply-to
- Anthropic Covered Models 보존 정책 — https://privacy.claude.com/en/articles/15425996-data-retention-practices-for-covered-models
- The Register, "Anthropic promises zero data retention – but customers must check it worked" (2026-09-02) — https://www.theregister.com/ai-and-ml/2026/09/02/anthropic-promises-zero-data-retention-but-customers-must-check-it-worked/5293789
- Gemini API Terms of Service — https://ai.google.dev/gemini-api/terms
- Gemini API Abuse monitoring (55일) — https://ai.google.dev/gemini-api/docs/usage-policies
- Gemini API Data logging and sharing — https://ai.google.dev/gemini-api/docs/logs-policy
- Gemini API Zero data retention — https://ai.google.dev/gemini-api/docs/zdr
- Gemini API Billing (티어 확인) — https://ai.google.dev/gemini-api/docs/billing

---

## 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-09-10 | 초안. 보안 지적사항 대응을 위해 코드 실사·공식 문서 조사 결과 정리 |
| 2026-09-23 | IT 게이트웨이 가이드 반영. Claude 전송층을 게이트웨이 경유로 준비(코드 완료, 키 권한 대기). G8·A4 추가 |
