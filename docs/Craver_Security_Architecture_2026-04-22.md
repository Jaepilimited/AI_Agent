# Craver Enterprise AI — 보안 아키텍처 문서

> 문서 버전: v3.0 | 작성일: 2026-04-22 | 대상: 보안팀 | 분류: 내부용 기밀

---

## 1. 시스템 개요 (High-Level Architecture)

```
                     ┌──────────────────────────────────────────┐
                     │         인터넷 / 사내망 (Intranet)         │
                     └──────────────────┬───────────────────────┘
                                        │
                               HTTPS / HTTP (Port 3000)
                                        │
                     ┌──────────────────▼───────────────────────┐
                     │          FastAPI Application Server        │
                     │         (Windows 11, Uvicorn, PM2)         │
                     │                                            │
                     │  ┌──────────────────────────────────────┐ │
                     │  │           Middleware Layer            │ │
                     │  │  ┌────────────┐  ┌────────────────┐  │ │
                     │  │  │    CORS    │  │ RequestLogging │  │ │
                     │  │  └────────────┘  └────────────────┘  │ │
                     │  └──────────────────────────────────────┘ │
                     │                    │                       │
                     │  ┌─────────────────▼────────────────────┐ │
                     │  │         Authentication Layer          │ │
                     │  │  ┌────────────┐  ┌────────────────┐  │ │
                     │  │  │  JWT Auth  │  │ Google OAuth2  │  │ │
                     │  │  │  (Cookie)  │  │ (GWS per-user) │  │ │
                     │  │  └────────────┘  └────────────────┘  │ │
                     │  └──────────────────────────────────────┘ │
                     │                    │                       │
                     │  ┌─────────────────▼────────────────────┐ │
                     │  │         Authorization Layer           │ │
                     │  │  ┌────────────┐  ┌────────────────┐  │ │
                     │  │  │ RBAC       │  │ Brand Filter   │  │ │
                     │  │  │(admin/user)│  │ (그룹별 데이터  │  │ │
                     │  │  └────────────┘  │  접근 제한)    │  │ │
                     │  │                  └────────────────┘  │ │
                     │  └──────────────────────────────────────┘ │
                     │                    │                       │
                     │  ┌─────────────────▼────────────────────┐ │
                     │  │         Business Logic Layer          │ │
                     │  │  ┌──────┐ ┌──────┐ ┌─────┐ ┌──────┐ │ │
                     │  │  │ SQL  │ │ RAG  │ │ GWS │ │  CS  │ │ │
                     │  │  │Agent │ │Agent │ │Agent│ │Agent │ │ │
                     │  │  └──┬───┘ └──┬───┘ └──┬──┘ └──┬───┘ │ │
                     │  │     └────────┴─────────┴────────┘     │ │
                     │  │  ┌──────────────────────────────────┐ │ │
                     │  │  │           Safety Layer           │ │ │
                     │  │  │ SQL Validation  | CircuitBreaker │ │ │
                     │  │  │ PartitionFilter | MaintenanceMgr │ │ │
                     │  │  └──────────────────────────────────┘ │ │
                     │  └──────────────────────────────────────┘ │
                     └───────┬────────────┬──────────────┬───────┘
                             │            │              │
               ┌─────────────▼──┐  ┌──────▼──────┐  ┌───▼──────────┐
               │   MariaDB      │  │   BigQuery  │  │ Google APIs  │
               │ (로컬, :3306)  │  │    (GCP)    │  │(Gmail/Drive) │
               └────────────────┘  └─────────────┘  └──────────────┘
```

### 1.1 배포 환경

| 항목 | 내용 |
|------|------|
| 운영 서버 | Windows 11 Pro (온프레미스) |
| 서버 IP | 172.16.1.250 (사내망) |
| 프로세스 관리 | PM2 (ecosystem.config.js) |
| 런타임 | Python 3.11, FastAPI + Uvicorn |
| 프로덕션 포트 | 3000 |
| 개발 포트 | 3001 |
| DB | MariaDB 로컬 (skin1004_ai, :3306) |
| 외부 도메인 | track.skin1004.app |

### 1.2 주요 외부 서비스 연동

| 서비스 | 용도 | 인증 방식 | 프로토콜 | 포트 |
|--------|------|----------|---------|------|
| Google BigQuery (GCP) | 매출/마케팅 데이터 조회 | GCP Service Account JSON | HTTPS | 443 |
| Anthropic Claude API | 사용자 대화 LLM | API Key (Bearer) | HTTPS | 443 |
| Google Gemini API | SQL 생성, 라우팅, 차트 | API Key | HTTPS | 443 |
| Notion API | 사내 문서 검색 | Integration Token | HTTPS | 443 |
| Google OAuth2 | GWS 사용자 인증 | Client ID + Secret | HTTPS | 443 |
| Gmail / Drive / Calendar | 개인 업무 데이터 조회 | OAuth2 Bearer Token | HTTPS | 443 |
| Qdrant (간접) | 사내 문서 임베딩 데이터 다운로드 (초기 1회) | API Key | HTTPS | 443 |
| Active Directory | 임직원 계정 동기화 | LDAPS 서비스 계정 | TLS | 636 |
| Tavily | 외부 웹 검색 | API Key | HTTPS | 443 |

---

## 2. 인증 시스템 (Authentication)

### 2.1 임직원 로그인 플로우 (AD 연동)

사용자는 이메일/비밀번호가 아닌 Active Directory 연동 방식으로 로그인한다.
이름으로 검색 후 소속 팀을 선택하고, 별도로 설정한 비밀번호를 입력한다.

```
┌──────────┐                    ┌───────────────────┐                  ┌──────────┐
│  Browser │                    │   FastAPI Server   │                  │ MariaDB  │
│ (Client) │                    │    (:3000)         │                  │(:3306)   │
└────┬─────┘                    └────────┬───────────┘                  └────┬─────┘
     │                                   │                                   │
     │  1. 이름 입력 (실시간 검색)        │                                   │
     │  GET /api/auth/search-name        │                                   │
     │  ?name=임재필                     │                                   │
     │──────────────────────────────────>│                                   │
     │                                   │  2. ad_users + users JOIN 조회    │
     │                                   │  COALESCE(u.display_name,         │
     │                                   │           ad.display_name)        │
     │                                   │──────────────────────────────────>│
     │  3. [{ id:5, name:"임재필",        │                                   │
     │        dept:"마케팅팀" }]          │<──────────────────────────────────│
     │<──────────────────────────────────│                                   │
     │                                   │                                   │
     │  4. 팀 선택 → 비밀번호 입력        │                                   │
     │  POST /api/auth/signin            │                                   │
     │  { name, dept, password,          │                                   │
     │    id: 5 }   <- ad_user_id 포함  │                                   │
     │──────────────────────────────────>│                                   │
     │                                   │  5. SELECT FROM ad_users          │
     │                                   │     WHERE id = 5  (id 기반)       │
     │                                   │──────────────────────────────────>│
     │                                   │  6. SELECT FROM users             │
     │                                   │     WHERE ad_user_id = 5          │
     │                                   │──────────────────────────────────>│
     │                                   │  7. bcrypt.checkpw(               │
     │                                   │       password, password_hash)    │
     │  8. Set-Cookie: token=<JWT>       │                                   │
     │     httponly; samesite=lax;        │                                   │
     │     max-age=31536000; path=/      │                                   │
     │<──────────────────────────────────│                                   │
     │  9. GET /  (인증된 요청)           │                                   │
     │  Cookie: token=<JWT>              │                                   │
     │──────────────────────────────────>│                                   │
     │                                   │  10. jwt.decode(token, SECRET)    │
     │                                   │      -> user_id 추출              │
     │  11. chat.html 반환               │                                   │
     │<──────────────────────────────────│                                   │
```

### 2.2 JWT 토큰 구성

```json
{
  "header": {
    "alg": "HS256",
    "typ": "JWT"
  },
  "payload": {
    "user_id": 42,
    "email": "jeffrey@skin1004korea.com",
    "role": "admin",
    "brand_filter": "SK,CL",
    "exp": 1777654800
  },
  "signature": "HMAC-SHA256(header.payload, JWT_SECRET_KEY)"
}
```

| 항목 | 설정값 |
|------|--------|
| 알고리즘 | HS256 (HMAC-SHA256) |
| 만료 기간 | 365일 |
| 저장 방식 | httpOnly Cookie (JavaScript 접근 불가) |
| SameSite | lax |
| Path | / |

| 파일 | 함수 | 역할 |
|------|------|------|
| app/api/auth_api.py | _create_token() | JWT 생성 (PyJWT) |
| app/api/auth_api.py | _set_cookie() | httpOnly 쿠키 발급 |
| app/api/auth_middleware.py | get_current_user() | 인증 필요 API의 FastAPI Dependency |
| app/api/auth_middleware.py | get_optional_user() | 인증 선택적 (없으면 None) |

### 2.3 비밀번호 보안

```
┌────────────────────────────────────────────────────────────────┐
│                    Password Hashing Flow                        │
│                                                                  │
│  사용자 입력:  "myPassword123"                                   │
│       │                                                          │
│       ▼                                                          │
│  bcrypt.gensalt()  ->  랜덤 Salt 생성 (16 bytes, cost=12)       │
│       │                                                          │
│       ▼                                                          │
│  bcrypt.hashpw(password.encode(), salt)                          │
│       │                                                          │
│       ▼                                                          │
│  결과: "$2b$12$LJ3m4ykL8vKG..."  (60자 해시)                    │
│       │                                                          │
│       ▼                                                          │
│  DB 저장: users.password_hash = "$2b$12$..."                    │
│                                                                  │
│  --- 로그인 시 검증 ---                                          │
│                                                                  │
│  bcrypt.checkpw(입력.encode(), DB해시.encode())                  │
│       │                                                          │
│       ▼                                                          │
│  True -> 로그인 성공  /  False -> 401 에러                       │
└────────────────────────────────────────────────────────────────┘
```

| 항목 | 설정 |
|------|------|
| 라이브러리 | bcrypt (Python) |
| Cost Factor | 12 (약 250ms/hash) |
| Salt | 자동 생성, 해시에 내장 |
| 원문 저장 | 없음 (해시만 저장) |

### 2.4 Google OAuth 2.0 (GWS 개인 연동)

Gmail, Drive, Calendar 접근을 위한 사용자별 OAuth 인증이다. 시스템 로그인과 별개로 동작한다.

```
┌──────────┐      ┌───────────────┐      ┌──────────────┐      ┌────────────┐
│  Browser │      │  FastAPI      │      │   Google     │      │ Token File │
│ (Client) │      │  (:3000)      │      │  OAuth2 API  │      │  (로컬)    │
└────┬─────┘      └──────┬────────┘      └──────┬───────┘      └─────┬──────┘
     │                   │                      │                     │
     │ 1. "Google 연결"  │                      │                     │
     │   버튼 클릭        │                      │                     │
     │──────────────────>│                      │                     │
     │ 2. GET /auth/     │                      │                     │
     │  google/login     │                      │                     │
     │  ?user_email=xxx  │                      │                     │
     │──────────────────>│ 3. authorization_url()                     │
     │                   │    state = user_email │                    │
     │                   │─────────────────────>│                     │
     │ 4. 302 -> Google  │                      │                     │
     │   동의 화면        │                      │                     │
     │<──────────────────│                      │                     │
     │ 5. 사용자: 권한승인│                      │                     │
     │──────────────────────────────────────────>│                    │
     │ 6. /auth/google/  │                      │                     │
     │  callback         │                      │                     │
     │  ?code=AUTH_CODE  │                      │                     │
     │──────────────────>│ 7. fetch_token(code) │                    │
     │                   │    -> access_token   │                     │
     │                   │    -> refresh_token  │                     │
     │                   │─────────────────────>│                     │
     │                   │ 8. 토큰 JSON 저장     │                    │
     │                   │──────────────────────────────────────────>│
     │                   │  data/gws_tokens/     │                   │
     │                   │  user_at_email.json   │                   │
     │ 9. 인증 완료       │                      │                     │
     │<──────────────────│                      │                     │
```

**OAuth 토큰 저장 구조**

```
data/gws_tokens/
├── jeffrey_at_skin1004korea_com.json
├── user2_at_skin1004korea_com.json
└── ...

각 파일 내용:
{
  "token": "<access_token>",           // 1시간 유효
  "refresh_token": "<refresh_token>",  // 장기 유효, 자동 갱신
  "client_id": "<Google OAuth Client ID>",
  "client_secret": "<Google OAuth Client Secret>",
  "scopes": ["gmail.readonly", "drive.readonly", "calendar.readonly"],
  "google_email": "user@skin1004korea.com"
}
```

| OAuth 설정 항목 | 값 |
|----------------|-----|
| Grant Type | Authorization Code |
| Access Type | offline (refresh_token 발급) |
| Redirect URI | http://localhost:3000/auth/google/callback |
| Scopes | gmail.readonly, drive.readonly, calendar.readonly |
| State Parameter | user_email (CSRF 방어 겸 사용자 식별) |

---

## 3. Active Directory (AD) 연동

### 3.1 AD 동기화 구성

| 항목 | 내용 |
|------|------|
| 프로토콜 | LDAPS (TLS 1.2, 636 포트) |
| 인증 방식 | 서비스 계정 (AD_USER, AD_PASSWORD) |
| 자격증명 저장 | .env 파일 |
| 실행 주기 | 매일 22:00 자동 (APScheduler + Windows Task Scheduler) |
| 동기화 대상 | 활성 사용자 전체 (~362명) |
| 중복 방지 | Lock 파일 (logs/ad_sync.lock, 10분 이내 재실행 차단) |
| 실패 알림 | Jandi 웹훅 자동 전송 |

### 3.2 AD Sync 2단계 파이프라인

```
┌──────────────────────────────────────────────────────────────────┐
│                    AD Sync Pipeline (22:00 Daily)                 │
│                                                                    │
│  ┌──────────┐    LDAPS:636    ┌──────────────┐                    │
│  │  AD 서버  │<──────────────>│ sync_ad_users│                    │
│  │ (사내망)  │                │ .py          │                    │
│  └──────────┘                └──────┬───────┘                    │
│                                     │                             │
│  STEP 1: AD -> MariaDB Upsert       │                             │
│  ─────────────────────────────      │                             │
│  1. AD에서 활성 사용자 LDAP 조회     │                             │
│     (sAMAccountName, displayName,   │                             │
│      mail, department, full_dn)     │                             │
│                                     │                             │
│  2. _NAME_OVERRIDES 적용            │                             │
│     (AD displayName이 영문인 경우   │                             │
│      한글 이름으로 오버라이드)       │                             │
│                                     │                             │
│  3. MariaDB ad_users Upsert         │                             │
│     ON DUPLICATE KEY UPDATE         │                             │
│     (비활성 먼저 -> 활성 재마킹)     │                             │
│                                     ▼                             │
│  STEP 2: 이름 자동 보정 (Heal)                                    │
│  ─────────────────────────────                                    │
│  4. users.display_name(한글) 기준으로                             │
│     ad_users.display_name 업데이트                                │
│     WHERE u.display_name REGEXP '[가-힣]'                         │
│       AND ad.display_name != u.display_name                       │
│                                                                    │
│  5. 결과 기록 및 알림                                              │
│     성공 -> logs/ad_sync.log (SUCCESS)                            │
│     실패 -> logs/ad_sync.log (FAILED) + Jandi 알림               │
│                                                                    │
│  종료 코드: 0=성공 | 2=AD실패 | 3=DB실패 | 4=Heal실패            │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. 권한 관리 (Authorization)

### 4.1 RBAC (역할 기반 접근 제어)

```
┌───────────────────────────────────────────────────────────────┐
│                      Role Hierarchy                            │
│                                                                 │
│   ┌─────────┐                                                  │
│   │  admin  │──── 모든 모델 접근                                │
│   │         │──── 사용자/그룹 관리 (/api/admin/*)              │
│   │         │──── 전체 브랜드 데이터 접근                       │
│   └────┬────┘                                                  │
│        │                                                        │
│   ┌────▼────┐                                                  │
│   │  user   │──── 허용된 모델만 접근                            │
│   │         │──── 자신의 대화만 조회/삭제                        │
│   │         │──── 소속 그룹의 Brand Filter 자동 적용            │
│   └─────────┘                                                  │
│                                                                  │
│   Admin 자동 승격: jeffrey@skin1004korea.com                    │
│   (서버 기동 시 _ensure_admin() 실행)                           │
└───────────────────────────────────────────────────────────────┘
```

### 4.2 Brand Filter (데이터 접근 제한)

BigQuery 조회 시 사용자의 그룹에 따라 특정 브랜드 데이터만 접근하도록 제한한다.

```
사용자 로그인 시:
  1. access_groups + user_groups JOIN 조회
  2. brand_filter = "SK,CL" (JWT 페이로드에 포함)

BigQuery 쿼리 실행 시:
  WHERE Brand IN ('SK', 'CL')  <- 자동 주입

Admin: brand_filter 없음 -> 전체 브랜드 접근
User:  그룹별 brand_filter -> 허용된 브랜드만 접근
```

### 4.3 API 접근 제어 매트릭스

| API 엔드포인트 | user | admin | 인증 불필요 |
|--------------|------|-------|------------|
| POST /api/auth/signin | - | - | O |
| POST /api/auth/signup | - | - | O |
| GET /api/auth/search-name | - | - | O |
| GET /api/auth/me | O | O | - |
| POST /v1/chat/completions | O (허용 모델만) | O | - |
| GET /api/conversations | O (자기 것만) | O (자기 것만) | - |
| GET /api/admin/users | 403 | O | - |
| GET /api/admin/groups/* | 403 | O | - |
| GET /api/admin/eval/* | 403 | O | - |
| GET /auth/google/* | - | - | O |
| GET /health | - | - | O |
| GET /safety/status | - | - | O |
| GET /docs | - | - | O |

---

## 5. SQL 보안 (Text-to-SQL Pipeline)

BigQuery 데이터 조회 시 자연어 → SQL 변환이 일어나며, 실행 전 5단계 보안 파이프라인을 통과한다.

### 5.1 SQL 보안 파이프라인

```
┌──────────────────────────────────────────────────────────────────┐
│                    SQL Security Pipeline                           │
│                                                                    │
│  사용자 질문: "태국 이번 달 매출 보여줘"                           │
│       │                                                            │
│       ▼                                                            │
│  Stage 1: LLM SQL Generation (Gemini Flash)                        │
│  "SELECT SUM(Sales1_R) FROM SALES_ALL_Backup WHERE ..."            │
│       │                                                            │
│       ▼                                                            │
│  Stage 2: sanitize_sql()                 <- app/core/security.py  │
│  - 마크다운 코드블록 제거                                          │
│  - SQL 추출                                                        │
│  - LIMIT 강제 추가                                                 │
│       │                                                            │
│       ▼                                                            │
│  Stage 3: validate_sql()                 <- app/core/security.py  │
│  - SELECT / WITH 만 허용                                           │
│  - INSERT / UPDATE / DELETE / DROP 차단                            │
│  - ALTER / CREATE / TRUNCATE / MERGE 차단                          │
│  - GRANT / REVOKE / EXEC / EXECUTE 차단                            │
│  - SQL Injection 패턴 탐지                                         │
│  - 테이블 화이트리스트 검증                                        │
│       │                                                            │
│       ▼                                                            │
│  Stage 4: _enforce_partition_filter                                │
│  - 대용량 테이블에 날짜 필터 없을 경우 자동 추가                   │
│  - 대상: SALES_ALL_Backup, integrated_ad,                          │
│          Integrated_marketing_cost                                 │
│  - Gemini Flash로 90일 조건 자동 생성 후 재검증                    │
│       │                                                            │
│       ▼                                                            │
│  Stage 5: BigQuery 실행               <- app/core/bigquery.py     │
│  - Timeout: 30초                                                   │
│  - Max Rows: 10,000                                                │
│  - READ-ONLY 서비스 계정                                           │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 SQL Injection 방어 패턴

```python
INJECTION_PATTERNS = [
    r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE)",  # Stacked Queries
    r"--\s",                    # SQL 주석 주입
    r"/\*.*?\*/",               # 블록 주석 주입
    r"xp_\w+",                  # SQL Server 확장 프로시저
    r"INFORMATION_SCHEMA",      # 메타데이터 탐색
    r"sys\.\w+",                # 시스템 테이블 접근
]
```

### 5.3 테이블 화이트리스트

```
허용된 테이블 (READ ONLY):
  skin1004-319714.Sales_Integration.SALES_ALL_Backup
  skin1004-319714.Sales_Integration.Product
  skin1004-319714.marketing_analysis.integrated_ad
  skin1004-319714.marketing_analysis.Integrated_marketing_cost
  skin1004-319714.marketing_analysis.shopify_analysis_sales
  skin1004-319714.Platform_Data.raw_data
  skin1004-319714.marketing_analysis.influencer_input_ALL_TEAMS
  skin1004-319714.marketing_analysis.amazon_search_analytics_*
  skin1004-319714.Review_Data.New_Amazon_Review
  skin1004-319714.Review_Data.New_Qoo10_Review
  skin1004-319714.Review_Data.New_Shopee_Review
  skin1004-319714.Review_Data.New_Smartstore_Review
  skin1004-319714.ad_data.meta data_test

차단 예시:
  skin1004-319714.AI_RAG.rag_embeddings  (내부 임베딩 DB)
  other-project.*                        (타 GCP 프로젝트)
```

---

## 6. LLM 데이터 처리 메커니즘

### 6.1 동작 원리 — LLM은 상태를 저장하지 않는다

```
┌─────────────────────────────────────────────────────────────────┐
│                    LLM Stateless Architecture                    │
│                                                                   │
│  대화 맥락 유지 방식:                                             │
│  이전 대화 전체를 매번 새로운 프롬프트에 합쳐서 전송              │
│                                                                   │
│  [요청 1]                                                        │
│  사내서버 -> Claude API                                          │
│  "시스템 프롬프트 + 질문A" -> "답변A"                            │
│                                                                   │
│  [요청 2]                                                        │
│  사내서버 -> Claude API                                          │
│  "시스템 프롬프트 + 질문A + 답변A + 질문B" -> "답변B"            │
│                                                                   │
│  - LLM은 요청과 요청 사이에 아무것도 기억하지 않음               │
│  - 매번 백지 상태에서 전달받은 프롬프트만 보고 답변 생성         │
│  - 당사 데이터로 모델 재학습(fine-tuning) 없음                   │
│  - 추론(inference)만 수행                                        │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 API vs 웹 서비스 차이

| 항목 | ChatGPT 웹 (chat.openai.com) | 우리 시스템 (API 호출) |
|------|------------------------------|----------------------|
| 대화 저장 | OpenAI 서버에 영구 저장 | LLM 서버에 저장 안 됨 |
| 모델 학습 | 학습에 활용 가능 (opt-out 가능) | 학습에 사용하지 않음 |
| 데이터 보존 | 계정에 영구 보존 | Anthropic: 30일 로그 후 삭제 |
| 직원 열람 | OpenAI 직원 열람 가능 | 남용 감사 목적 외 불가 |

### 6.3 각 LLM 제공사의 API 데이터 정책

**Anthropic (Claude) — 사용자 대화용 메인 LLM**

| 항목 | 내용 |
|------|------|
| 학습 사용 여부 | API 입출력 데이터를 모델 학습에 사용하지 않음 |
| 안전 로그 보존 | 요청 데이터 최대 30일간 보관 (정책 위반 감사 목적) |
| 30일 후 처리 | 자동 삭제 |
| 근거 | Anthropic API Usage Policy — "We do not train on your API inputs and outputs" |

**Google (Gemini) — SQL 생성/차트용 경량 LLM**

| 항목 | 내용 |
|------|------|
| 학습 사용 여부 | 유료 API (Vertex AI / AI Studio 유료): 학습 사용 안 함 |
| 데이터 보존 | 요청 처리 후 즉시 삭제 |
| 근거 | Google Cloud Data Processing Terms |

### 6.4 외부로 전송되는 데이터 구체적 예시

**예시 1: 매출 질문**

```
사용자: "이번 달 인도네시아 매출 알려줘"

  1단계 - Gemini에 전송 (SQL 생성용)
  내용: 테이블 스키마 + SQL 생성 규칙 + 질문 텍스트
  -> 이 단계에서 실제 매출 수치는 전송 안 됨

  2단계 - BigQuery 직접 조회 (사내 서버 -> GCP)
  결과: [{ Country: "인도네시아", revenue: 5000000000 }]

  3단계 - Gemini에 전송 (답변 포맷용)
  내용: 질문 + BigQuery 조회 결과 JSON
  -> 이 시점에서 매출 수치가 Gemini 서버에 전송됨
  -> Gemini 유료 API: 즉시 삭제 (학습 사용 안 함)

  4단계 - 사용자에게 답변 표시 + MariaDB에 텍스트 저장
  저장 내용: "인도네시아 이번 달 매출은 약 50억원입니다"
  미저장: BigQuery 조회 결과 원본 JSON (메모리에서 소멸)
```

**예시 2: GWS 일정 조회**

```
사용자: "오늘 내 일정 알려줘"

  1단계 - Google Calendar API 호출 (읽기 전용)
  사용자 OAuth 토큰으로 조회
  결과: { events: [{ summary: "팀미팅", start: "14:00" }] }

  2단계 - Claude에 전송 (답변 생성)
  내용: 일정 데이터 JSON + 질문 텍스트
  -> Anthropic 서버: 30일 로그 후 삭제

  3단계 - 사용자에게 답변 표시 + MariaDB에 텍스트 저장
  저장 내용: "오늘 오후 2시에 팀미팅이 있습니다"
  미저장: Calendar 원본 이벤트 JSON (메모리에서 소멸)
```

### 6.5 데이터 저장 현황 요약

| 위치 | 저장 여부 | 내용 | 보존 기간 |
|------|----------|------|----------|
| 사내 MariaDB | 저장 | 질문/답변 텍스트, 대화 이력 | 무기한 |
| 사내 서버 파일 | 저장 | GWS OAuth 토큰 | 토큰 만료 시까지 |
| Anthropic 서버 | 임시 보관 | API 요청/응답 로그 | 30일 후 자동 삭제 |
| Google 서버 | 미저장 | 유료 API 즉시 삭제 | 즉시 삭제 |
| LLM 모델 가중치 | 반영 안 됨 | 학습 사용 없음 | - |

---

## 7. 데이터 보안

### 7.1 데이터 흐름도

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Data Flow & Storage                           │
│                                                                       │
│  ┌──────────┐    httpOnly Cookie (JWT)    ┌──────────────┐           │
│  │  Client  │───────────────────────────>│  FastAPI     │           │
│  │ Browser  │<────── Set-Cookie ──────────│  Server      │           │
│  └──────────┘                             └──────┬───────┘           │
│       │                                          │                    │
│       │ (이미지: base64 인라인 전송,              │                    │
│       │  서버 파일로 저장 안 함)                  │                    │
│       │                            ┌─────────────┼──────────────┐   │
│       │                            │             │              │   │
│       │                      ┌─────▼──────┐  ┌───▼──────┐  ┌───▼─┐ │
│       │                      │  MariaDB   │  │ BigQuery │  │Token│ │
│       │                      │  (로컬)    │  │  (GCP)   │  │File │ │
│       │                      └────────────┘  └──────────┘  └─────┘ │
│       │                           │                │            │    │
│       │                      저장 항목:       조회만:       저장:    │
│       │                      - users         - SALES_ALL   - OAuth  │
│       │                      - password        _Backup       token  │
│       │                        (bcrypt)     - Product     - refresh │
│       │                      - conversations - 마케팅 데이터  token  │
│       │                      - messages                             │
│       │                      - ad_users                             │
│       │                      - anon_id (가명화)                      │
│       │                                                              │
│       │                      미저장:                                 │
│       │                      - 이미지 원본 (메모리 소멸)              │
│       │                      - GWS 원본 데이터 (메모리 소멸)          │
│       │                      - BigQuery 조회 결과 JSON               │
└─────────────────────────────────────────────────────────────────────┘
```

### 7.2 저장 데이터 암호화 현황

| 데이터 | 저장 위치 | 방식 |
|--------|----------|------|
| 사용자 비밀번호 | MariaDB users.password_hash | bcrypt 단방향 해시 |
| JWT 시크릿 | .env 파일 | 환경변수 |
| GCP 서비스 계정 키 | C:/json_key/*.json | 로컬 파일 |
| GWS OAuth 토큰 | data/gws_tokens/*.json | 로컬 파일 |
| AD 서비스 계정 | .env 파일 | 환경변수 |
| 대화 기록 | MariaDB messages.content | 평문 |
| 익명화 Salt | .env 파일 | 환경변수 |

### 7.3 사용자 데이터 익명화 (Pseudonymization)

대화 기록의 사용자 식별 정보를 가명화하는 시스템이 적용되어 있다.

```python
# app/core/anonymization.py
# anon_id = HMAC-SHA256(ANON_SALT, user_id)[:16]

def compute_anon_id(user_id: int) -> str:
    salt = get_settings().anon_salt   # 최소 32자 필수
    mac = hmac.new(salt.encode("utf-8"), str(user_id).encode(), sha256)
    return mac.hexdigest()[:16]       # 16자 16진수 반환
```

| 항목 | 내용 |
|------|------|
| 알고리즘 | HMAC-SHA256(ANON_SALT, user_id)[:16] |
| Salt 최소 길이 | 32자 (미설정 시 서버 기동 오류 발생) |
| 적용 범위 | conversations.anon_id, message_feedback.anon_id |
| 로그 스크럽 | 모든 로그에서 user_id, email, display_name 자동 제거 |
| 예외 로거 | audit.*, security.* (사고 대응용 — 스크럽 없이 통과) |

### 7.4 MariaDB 스키마 (보안 관련)

```
┌──────────────────────────────────────────────────────────────────┐
│                   MariaDB Schema (skin1004_ai)                    │
│                                                                    │
│  users                                                             │
│  ┌───────────────┬────────────┬────────────────────────────────┐  │
│  │ id            │ INT PK     │ Auto Increment                 │  │
│  │ email         │ VARCHAR    │ UNIQUE                         │  │
│  │ password_hash │ VARCHAR    │ bcrypt 60자 해시               │  │
│  │ display_name  │ VARCHAR    │ 한글 이름                      │  │
│  │ role          │ ENUM       │ 'admin' / 'user'               │  │
│  │ allowed_models│ TEXT       │ 허용 모델 목록 (CSV)           │  │
│  │ ad_user_id    │ INT FK     │ -> ad_users.id                 │  │
│  │ is_active     │ TINYINT    │ 활성 여부                      │  │
│  └───────────────┴────────────┴────────────────────────────────┘  │
│                          │ FK                                      │
│  ad_users                ▼                                         │
│  ┌───────────────┬────────────┬────────────────────────────────┐  │
│  │ id            │ INT PK     │                                │  │
│  │ username      │ VARCHAR    │ UNIQUE (sAMAccountName)        │  │
│  │ display_name  │ VARCHAR    │ AD 표시 이름                   │  │
│  │ email         │ VARCHAR    │ UNIQUE                         │  │
│  │ department    │ VARCHAR    │ OU 경로                        │  │
│  │ is_active     │ TINYINT    │ 매 동기화 시 갱신              │  │
│  │ synced_at     │ TIMESTAMP  │ 마지막 동기화 시각             │  │
│  └───────────────┴────────────┴────────────────────────────────┘  │
│                                                                    │
│  접근 제어:                                                        │
│  - 사용자는 자신의 conversation만 조회 가능 (user_id/anon_id)     │
│  - Admin도 다른 사용자 대화 내용 직접 조회 불가                   │
│  - Admin은 사용자 목록 + 모델 권한만 관리                         │
└──────────────────────────────────────────────────────────────────┘
```

---

## 8. 네트워크 보안

### 8.1 네트워크 구성도

```
┌─────────────────────────────────────────────────────────────────┐
│                      Network Architecture                         │
│                                                                   │
│  ┌──────────────────────────────────────────────────────┐        │
│  │                    Internal Network                   │        │
│  │                                                       │        │
│  │  ┌──────────┐           ┌─────────────────────────┐  │        │
│  │  │  Client  │──:3000──>│  FastAPI Server           │  │        │
│  │  │ Browser  │           │  (172.16.1.250:3000)     │  │        │
│  │  └──────────┘           └────────────┬──────────────┘  │        │
│  │                                      │                  │        │
│  │  ┌────────────────┐    LDAPS:636    │                  │        │
│  │  │  AD 서버        │<───────────────┘                  │        │
│  │  │  (사내망)       │                                    │        │
│  │  └────────────────┘                                    │        │
│  │                                                        │        │
│  │  ┌────────────────┐    :3306 (로컬)                   │        │
│  │  │  MariaDB       │<──────────────────────────────────┘        │
│  │  └────────────────┘                                            │
│  └────────────────────────────────────────────────────────┘       │
│                                                                    │
│  ┌────────────────────────────────────────────────────────┐       │
│  │          External Services (HTTPS Outbound Only)        │       │
│  │                                                         │       │
│  │  Google Cloud (BigQuery) │ Anthropic API (Claude)       │       │
│  │  Google AI (Gemini)      │ Notion API                   │       │
│  │  Google APIs (GWS)       │ Qdrant (초기 데이터 다운로드) │       │
│  └────────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

### 8.2 CORS 설정

```python
# app/api/middleware.py
CORSMiddleware(
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:8000",
        "http://172.16.1.250:3000",
        "http://172.16.1.250:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 8.3 외부 통신 프로토콜

| 대상 | 프로토콜 | 인증 방식 | 포트 |
|------|---------|----------|------|
| BigQuery | HTTPS | GCP Service Account JSON | 443 |
| Gemini API | HTTPS | API Key | 443 |
| Anthropic Claude | HTTPS | API Key (Bearer) | 443 |
| Google OAuth2 | HTTPS | Client ID + Secret | 443 |
| Gmail/Drive/Calendar | HTTPS | OAuth2 Bearer Token | 443 |
| Notion API | HTTPS | Integration Token | 443 |
| AD (LDAPS) | TLS 1.2 | 서비스 계정 | 636 |

---

## 9. 안전 장치 (Safety Systems)

### 9.1 CircuitBreaker (서비스별 차단기)

외부 서비스 장애 시 연쇄 실패를 방지한다.

```
┌──────────────────────────────────────────────────────────┐
│                Circuit Breaker State Machine               │
│                                                            │
│   ┌──────────┐   3회 연속 실패   ┌──────────┐             │
│   │  CLOSED  │─────────────────>│   OPEN   │             │
│   │ (정상)   │                   │ (차단)   │             │
│   └────▲─────┘                   └────┬─────┘             │
│        │                              │                    │
│   성공 │                    60초 대기 │                    │
│        │                              │                    │
│   ┌────┴─────┐                   ┌────▼─────┐             │
│   │  재시도  │<──────────────────│HALF_OPEN │             │
│   │  성공    │   1회 시도 허용    │ (테스트) │             │
│   └──────────┘                   └──────────┘             │
│                                                            │
│   설정값:                                                  │
│   - failure_threshold: 3 (3회 실패 -> 차단)               │
│   - cooldown_seconds: 60 (60초 후 재시도)                 │
│                                                            │
│   적용 서비스: bigquery, notion, gws                      │
└──────────────────────────────────────────────────────────┘
```

### 9.2 LLM API 재시도 로직

```
LLM API 호출 (Claude / Gemini)
       │
       ▼
  _retry_call() 래퍼
       │
  시도 1: 즉시 실행
       ├─ 성공 -> 결과 반환
       └─ 실패 -> 재시도 가능 에러 여부 확인
              - 429 Rate Limit
              - 500 Server Error
              - 503 Unavailable
              - ConnectionError / TimeoutError
       │
  시도 2 (1초 대기) -> 시도 3 (2초 대기) -> 최종 에러
       │
  비재시도 에러: 400, 401, 403 (즉시 에러 반환)

  설정값:
  - max_retries: 3
  - backoff_delays: [1s, 2s, 4s]
  - Claude HTTP timeout: 60초
```

### 9.3 MaintenanceManager (BigQuery 자동 점검 감지)

BigQuery 테이블 업데이트를 자동 감지하고 업데이트 중 쿼리를 일시 차단한다.

```
매 60초 폴링:
  SELECT row_count, TIMESTAMP_DIFF(...) as modified_ago
  FROM __TABLES__ WHERE table_id = 'SALES_ALL_Backup'

감지 조건:
  - 최근 180초 이내 수정됨  OR
  - Row count 5% 이상 감소

조건 충족 시:
  -> "updating" 상태 설정
  -> SQL 쿼리 요청 차단
  -> UI에 점검 중 안내 표시

Row count 98% 이상 회복 시 -> 정상 복귀
```

---

## 10. 모니터링 및 로깅

### 10.1 요청 로깅

```
┌────────────────────────────────────────────────────┐
│              Request Logging Pipeline                │
│                                                      │
│  모든 HTTP 요청 (RequestLoggingMiddleware):          │
│                                                      │
│  1. Request ID 생성 (UUID[:8])                       │
│  2. JWT에서 user_email 추출                          │
│  3. 요청 시작 로그 (JSON 구조화):                    │
│     {                                                │
│       "event": "request_started",                    │
│       "request_id": "a1b2c3d4",                     │
│       "method": "POST",                             │
│       "path": "/v1/chat/completions",               │
│       "client": "192.168.1.100",                    │
│       "user_email": "[anon_id로 대체됨]"             │
│     }                                                │
│                                                      │
│  4. 응답 완료 로그:                                  │
│     {                                                │
│       "event": "request_completed",                  │
│       "status_code": 200,                           │
│       "latency_ms": 1523                            │
│     }                                                │
│                                                      │
│  응답 헤더:                                          │
│  - X-Request-ID: a1b2c3d4                            │
│  - X-Latency-Ms: 1523                               │
│                                                      │
│  개인정보 스크럽 (log_scrub.py):                     │
│  - user_id -> anon_id로 대체                         │
│  - email, name, display_name 제거                    │
│  - audit.*, security.* 로거는 스크럽 없이 통과       │
└────────────────────────────────────────────────────┘
```

### 10.2 감사 로그 (Audit Log)

- 테이블: `audit_logs` (MariaDB)
- 기록 항목: user_email, route, query, first_token_ms, total_ms, model
- BigQuery 쿼리 실행 이력 추적 가능

### 10.3 AD Sync 모니터링

| 항목 | 내용 |
|------|------|
| 로그 파일 | logs/ad_sync.log |
| 기록 내용 | 실행 시각, SUCCESS/FAILED, 사용자 수, 오류 내용 |
| Jandi 알림 | 실패: 빨간 알림, 경고: 주황 알림 자동 전송 |
| 종료 코드 | 0=성공, 2=AD실패, 3=DB실패, 4=Heal실패 |

---

## 11. 환경변수 및 자격증명 관리

### 11.1 설정 로딩 체계

```
┌────────────────────────────────────────────────────┐
│                 Config Loading Chain                 │
│                                                      │
│  .env 파일                                           │
│  ┌─────────────────────────────────────────┐        │
│  │ ANTHROPIC_API_KEY=sk-ant-...            │        │
│  │ GEMINI_API_KEY=AIzaSy...               │        │
│  │ JWT_SECRET_KEY=...                      │        │
│  │ GOOGLE_OAUTH_CLIENT_ID=...              │        │
│  │ GOOGLE_OAUTH_CLIENT_SECRET=...          │        │
│  │ NOTION_MCP_TOKEN=...                   │        │
│  │ AD_USER=svc_ldap@skin1004.local        │        │
│  │ AD_PASSWORD=...                         │        │
│  │ MARIADB_PASSWORD=...                    │        │
│  │ ANON_SALT=<32자 이상 무작위>            │        │
│  └─────────────────┬───────────────────────┘        │
│                    │                                 │
│                    ▼                                 │
│  pydantic-settings (BaseSettings)                    │
│  .env 파일 -> 타입 검증 -> Settings 객체             │
│                    │                                 │
│                    ▼                                 │
│  @lru_cache() -> get_settings() (싱글톤)            │
│  앱 전체에서 단일 인스턴스로 사용                    │
└────────────────────────────────────────────────────┘
```

### 11.2 자격증명 저장 위치

| 서비스 | 저장 위치 | 방식 |
|--------|----------|------|
| GCP 서비스 계정 키 | C:/json_key/*.json | 로컬 JSON 파일 |
| Anthropic API Key | .env | 환경변수 |
| Gemini API Key | .env | 환경변수 |
| Notion Token | .env | 환경변수 |
| Google OAuth Client | .env | 환경변수 |
| AD 서비스 계정 | .env | 환경변수 |
| MariaDB 비밀번호 | .env | 환경변수 |
| ANON_SALT | .env | 환경변수 |
| GWS 사용자 토큰 | data/gws_tokens/*.json | 로컬 JSON 파일 |

---

## 12. 전체 보안 구조 요약도

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Craver AI — Full Security Map                          │
│                                                                           │
│   Client (Browser)                                                        │
│   ┌─────────────────────────────────────────────────────────────┐        │
│   │  [1] httpOnly Cookie (JWT)  — XSS 방어                      │        │
│   │  [2] SameSite=Lax           — CSRF 기본 방어                │        │
│   │  [3] 이미지 base64 인라인    — 파일 업로드 없음              │        │
│   └─────────────────────────────────────┬───────────────────────┘        │
│                                         │                                 │
│   FastAPI Server (:3000)                │                                 │
│   ┌─────────────────────────────────────▼──────────────────────┐         │
│   │  [4]  CORS Middleware         — 설정 기반 도메인 제한       │         │
│   │  [5]  Request Logging         — JSON 구조화 + Request ID   │         │
│   │  [6]  JWT Validation          — HS256                      │         │
│   │  [7]  RBAC                    — admin / user 역할          │         │
│   │  [8]  Brand Filter            — 그룹별 데이터 접근 제한     │         │
│   │  [9]  SQL Sanitize            — 마크다운 제거, LIMIT 강제   │         │
│   │  [10] SQL Validation          — SELECT ONLY + 화이트리스트 │         │
│   │  [11] Partition Filter        — 대용량 테이블 날짜 강제     │         │
│   │  [12] SQL Injection Defense   — 패턴 매칭 탐지             │         │
│   │  [13] CircuitBreaker          — 3회 실패 -> 60초 차단      │         │
│   │  [14] MaintenanceManager      — 테이블 업데이트 자동 감지  │         │
│   │  [15] Query Timeout           — BigQuery 30초 제한         │         │
│   │  [16] Row Limit               — 최대 10,000행              │         │
│   │  [17] Log Scrub               — PII 자동 제거              │         │
│   │  [18] Anonymization           — HMAC-SHA256 anon_id        │         │
│   └───────────────┬──────────────────────┬─────────────────────┘         │
│                   │                      │                                 │
│   ┌───────────────▼──────┐   ┌──────────▼──────────────────────┐         │
│   │  MariaDB (Local)      │   │  External APIs (HTTPS only)     │         │
│   │  [19] bcrypt password │   │  [23] GCP Service Account Key  │         │
│   │  [20] anon_id FK      │   │  [24] API Key (환경변수)        │         │
│   │  [21] user_id 격리    │   │  [25] OAuth2 per-user token    │         │
│   │  [22] AD sync Lock    │   │  [26] LLM 재시도/타임아웃      │         │
│   └───────────────────────┘   └────────────────────────────────┘         │
│                                                                           │
│   Config & Secrets                                                        │
│   ┌─────────────────────────────────────────────────────────────┐        │
│   │  [27] pydantic-settings    — .env 파일에서 로드             │        │
│   │  [28] @lru_cache singleton — 설정 객체 싱글톤 캐싱          │        │
│   └─────────────────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 부록 A. 주요 보안 파일 경로

| 파일 | 용도 |
|------|------|
| .env | 모든 시크릿 (API 키, DB, JWT, AD 자격증명) |
| C:/json_key/*.json | GCP 서비스 계정 키 |
| data/gws_tokens/*.json | 사용자별 Google OAuth 토큰 |
| logs/ad_sync.log | AD 동기화 실행 기록 |
| logs/ad_sync.lock | 동기화 중복 방지 잠금 파일 |
| app/config.py | 설정 클래스 |
| app/api/auth_api.py | 로그인/회원가입, JWT 발급 |
| app/api/auth_middleware.py | JWT 검증 |
| app/api/middleware.py | CORS, 요청 로깅 |
| app/core/anonymization.py | HMAC-SHA256 익명화 |
| app/core/log_scrub.py | 로그 PII 스크럽 |
| app/core/security.py | SQL 검증 및 인젝션 방어 |
| app/core/safety.py | CircuitBreaker, MaintenanceManager |
| scripts/sync_ad_users.py | AD 동기화 (Lock, 재시도, 알림) |

## 부록 B. 보안 관련 의존 라이브러리

| 라이브러리 | 용도 |
|-----------|------|
| PyJWT | JWT 생성/검증 |
| bcrypt | 비밀번호 해싱 |
| ldap3 | AD LDAPS 연결 |
| pymysql | MariaDB 드라이버 |
| cryptography | TLS 지원 |
| pydantic-settings | 설정 및 환경변수 검증 |
| structlog | 구조화 로깅 |

---

*본 문서는 2026-04-22 기준 시스템 코드 분석을 바탕으로 작성되었습니다.*

*문서 끝 | Craver Enterprise AI Security Architecture v3.0*
