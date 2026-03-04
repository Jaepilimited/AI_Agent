# SKIN1004 AI System - Security & Authentication Architecture

> **문서 버전**: v1.0 | **작성일**: 2026-03-04
> **대상 독자**: IT팀, 보안 담당자, 시스템 관리자
> **목적**: 시스템의 인증, 권한, 데이터 보호, 네트워크 보안 구조를 상세히 설명

---

## 1. 시스템 개요 (High-Level Architecture)

```
                            ┌─────────────────────────────────────────────┐
                            │           INTERNET / INTRANET               │
                            └──────────────────┬──────────────────────────┘
                                               │
                                        HTTPS (Port 3000)
                                               │
                            ┌──────────────────▼──────────────────────────┐
                            │          FastAPI Application Server          │
                            │         (Single Process, Uvicorn)            │
                            │                                              │
                            │  ┌──────────────────────────────────────┐   │
                            │  │        Middleware Layer               │   │
                            │  │  ┌──────────┐  ┌─────────────────┐  │   │
                            │  │  │   CORS   │  │ Request Logging │  │   │
                            │  │  └──────────┘  └─────────────────┘  │   │
                            │  └──────────────────────────────────────┘   │
                            │                    │                         │
                            │  ┌─────────────────▼────────────────────┐   │
                            │  │       Authentication Layer            │   │
                            │  │  ┌──────────┐  ┌──────────────────┐  │   │
                            │  │  │ JWT Auth │  │ Google OAuth 2.0 │  │   │
                            │  │  │ (Cookie) │  │  (GWS per-user)  │  │   │
                            │  │  └──────────┘  └──────────────────┘  │   │
                            │  └──────────────────────────────────────┘   │
                            │                    │                         │
                            │  ┌─────────────────▼────────────────────┐   │
                            │  │        Authorization Layer            │   │
                            │  │  ┌──────────┐  ┌──────────────────┐  │   │
                            │  │  │ Role     │  │ Model Access     │  │   │
                            │  │  │ (RBAC)   │  │ Control (MAC)    │  │   │
                            │  │  └──────────┘  └──────────────────┘  │   │
                            │  └──────────────────────────────────────┘   │
                            │                    │                         │
                            │  ┌─────────────────▼────────────────────┐   │
                            │  │       Business Logic Layer            │   │
                            │  │                                       │   │
                            │  │  ┌─────────┐ ┌──────┐ ┌──────────┐  │   │
                            │  │  │  SQL    │ │ RAG  │ │   GWS    │  │   │
                            │  │  │ Agent   │ │Agent │ │  Agent   │  │   │
                            │  │  └────┬────┘ └──┬───┘ └────┬─────┘  │   │
                            │  │       │         │          │         │   │
                            │  │  ┌────▼─────────▼──────────▼─────┐  │   │
                            │  │  │      Safety Layer              │  │   │
                            │  │  │ SQL Validation │ CircuitBreaker│  │   │
                            │  │  │ Maintenance    │ Rate Control  │  │   │
                            │  │  └────────────────────────────────┘  │   │
                            │  └──────────────────────────────────────┘   │
                            └──────────┬──────────┬──────────┬────────────┘
                                       │          │          │
                            ┌──────────▼───┐ ┌────▼────┐ ┌───▼──────────┐
                            │  SQLite DB   │ │ BigQuery│ │ Google APIs  │
                            │ (로컬 인증DB)│ │  (GCP)  │ │ (Gmail/Drive)│
                            └──────────────┘ └─────────┘ └──────────────┘
```

---

## 2. 인증 시스템 (Authentication)

### 2.1 JWT 쿠키 기반 인증

사용자 로그인/회원가입은 JWT(JSON Web Token)을 httpOnly 쿠키에 저장하는 방식으로 구현됩니다.

```
┌──────────┐                    ┌───────────────────┐                  ┌──────────┐
│  Browser │                    │   FastAPI Server   │                  │ SQLite   │
│ (Client) │                    │    (Port 3000)     │                  │   DB     │
└────┬─────┘                    └────────┬───────────┘                  └────┬─────┘
     │                                   │                                   │
     │  1. POST /api/auth/signin         │                                   │
     │  { email, password }              │                                   │
     │──────────────────────────────────>│                                   │
     │                                   │  2. SELECT * FROM users           │
     │                                   │     WHERE email = ?               │
     │                                   │──────────────────────────────────>│
     │                                   │                                   │
     │                                   │  3. bcrypt.checkpw(password, hash)│
     │                                   │     (서버 내부 비교)                │
     │                                   │                                   │
     │  4. Set-Cookie: token=<JWT>       │                                   │
     │     httponly; samesite=lax;        │                                   │
     │     max-age=604800; path=/        │                                   │
     │<──────────────────────────────────│                                   │
     │                                   │                                   │
     │  5. GET /v1/chat/completions      │                                   │
     │  Cookie: token=<JWT>              │                                   │
     │──────────────────────────────────>│                                   │
     │                                   │  6. jwt.decode(token, SECRET)     │
     │                                   │     → user_id 추출                │
     │                                   │  7. SELECT * FROM users           │
     │                                   │     WHERE id = user_id            │
     │                                   │──────────────────────────────────>│
     │                                   │                                   │
     │  8. AI 응답 (SSE Stream)          │                                   │
     │<──────────────────────────────────│                                   │
```

#### JWT 토큰 구조

```json
{
  "header": {
    "alg": "HS256",        // HMAC-SHA256 서명 알고리즘
    "typ": "JWT"
  },
  "payload": {
    "user_id": "a1b2c3...",     // SQLite User.id (UUID hex)
    "email": "user@example.com",
    "exp": 1741276800           // 만료: 발급일 + 7일 (UTC)
  },
  "signature": "HMAC-SHA256(header.payload, JWT_SECRET_KEY)"
}
```

#### 보안 설정 상세

| 항목 | 설정값 | 설명 |
|------|--------|------|
| **알고리즘** | HS256 | 대칭키 HMAC-SHA256 |
| **Secret Key** | `jwt_secret_key` (.env) | 서버 환경변수에서 로드 |
| **만료 시간** | 7일 (604,800초) | `_TOKEN_EXPIRE_DAYS = 7` |
| **저장 위치** | httpOnly Cookie | JavaScript 접근 불가 (XSS 방어) |
| **SameSite** | Lax | CSRF 기본 방어 |
| **Secure 플래그** | 미설정 (개발환경) | **프로덕션에서는 `secure=True` 필요** |
| **Path** | `/` | 전체 도메인에서 유효 |

#### 파일 위치 및 함수

| 파일 | 함수/클래스 | 역할 |
|------|-----------|------|
| `app/api/auth_api.py` | `signup()` | 회원가입: bcrypt 해싱 → DB 저장 → JWT 발급 |
| `app/api/auth_api.py` | `signin()` | 로그인: bcrypt 검증 → JWT 발급 → Cookie 설정 |
| `app/api/auth_api.py` | `logout()` | 로그아웃: Cookie 삭제 |
| `app/api/auth_api.py` | `me()` | 현재 사용자 조회 |
| `app/api/auth_api.py` | `_create_token()` | JWT 생성 (PyJWT 라이브러리) |
| `app/api/auth_api.py` | `_set_cookie()` | httpOnly 쿠키 설정 |
| `app/api/auth_middleware.py` | `get_current_user()` | 모든 인증 필요 API의 FastAPI Dependency |
| `app/api/auth_middleware.py` | `get_optional_user()` | 인증 선택적 (없으면 None 반환) |

---

### 2.2 비밀번호 보안

```
┌────────────────────────────────────────────────────────────────┐
│                    Password Hashing Flow                        │
│                                                                  │
│  사용자 입력:  "myPassword123"                                   │
│       │                                                          │
│       ▼                                                          │
│  bcrypt.gensalt()  →  랜덤 Salt 생성 (16 bytes)                 │
│       │                                                          │
│       ▼                                                          │
│  bcrypt.hashpw(password.encode(), salt)                          │
│       │                                                          │
│       ▼                                                          │
│  결과: "$2b$12$LJ3m4ykL8vKG..."  (60자 해시)                    │
│       │                                                          │
│       ▼                                                          │
│  DB 저장: users.password = "$2b$12$LJ3m4ykL8vKG..."             │
│                                                                  │
│  ─── 로그인 시 검증 ───                                          │
│                                                                  │
│  bcrypt.checkpw(입력.encode(), DB해시.encode())                   │
│       │                                                          │
│       ▼                                                          │
│  True → 로그인 성공  /  False → 401 에러                         │
└────────────────────────────────────────────────────────────────┘
```

| 항목 | 설정 |
|------|------|
| **해싱 라이브러리** | `bcrypt` (Python) |
| **Cost Factor** | 12 (기본값, 약 250ms/hash) |
| **Salt** | 자동 생성, 해시에 내장 |
| **최소 비밀번호 길이** | 4자 |
| **원문 저장** | 절대 없음 (해시만 저장) |

---

### 2.3 Google OAuth 2.0 (GWS 연동)

Google Workspace(Gmail, Drive, Calendar) 접근을 위한 별도의 OAuth 2.0 인증 흐름입니다.
**시스템 로그인과 별개**로 동작하며, 사용자별로 독립 관리됩니다.

```
┌──────────┐          ┌───────────────┐          ┌──────────────┐          ┌────────────┐
│  Browser │          │  FastAPI      │          │   Google     │          │ Token File │
│ (Client) │          │  Server       │          │  OAuth2 API  │          │  (Local)   │
└────┬─────┘          └──────┬────────┘          └──────┬───────┘          └─────┬──────┘
     │                       │                          │                        │
     │ 1. 채팅 UI에서         │                          │                        │
     │  "Google 연결" 클릭    │                          │                        │
     │──────────────────────>│                          │                        │
     │                       │                          │                        │
     │ 2. GET /auth/google/login                        │                        │
     │    ?user_email=xxx    │                          │                        │
     │──────────────────────>│                          │                        │
     │                       │  3. Flow.authorization_url()                      │
     │                       │     state = user_email   │                        │
     │                       │─────────────────────────>│                        │
     │                       │                          │                        │
     │ 4. 302 Redirect       │                          │                        │
     │    → Google 동의 화면  │                          │                        │
     │<──────────────────────│                          │                        │
     │                       │                          │                        │
     │ 5. 사용자: 권한 승인   │                          │                        │
     │──────────────────────────────────────────────────>│                        │
     │                       │                          │                        │
     │ 6. Redirect:          │                          │                        │
     │  /auth/google/callback│                          │                        │
     │  ?code=AUTH_CODE      │                          │                        │
     │  &state=user_email    │                          │                        │
     │──────────────────────>│                          │                        │
     │                       │  7. flow.fetch_token(code)│                       │
     │                       │     → access_token        │                       │
     │                       │     → refresh_token       │                       │
     │                       │─────────────────────────>│                        │
     │                       │                          │                        │
     │                       │  8. 토큰 JSON 저장        │                       │
     │                       │─────────────────────────────────────────────────>│
     │                       │   data/gws_tokens/        │                       │
     │                       │   user_at_email_com.json   │                      │
     │                       │                          │                        │
     │ 9. 인증 완료 HTML      │                          │                        │
     │  (3초 후 자동 닫힘)    │                          │                        │
     │<──────────────────────│                          │                        │
```

#### OAuth 2.0 설정 상세

| 항목 | 설정값 |
|------|--------|
| **Provider** | Google OAuth 2.0 |
| **Grant Type** | Authorization Code |
| **Access Type** | `offline` (refresh_token 발급) |
| **Prompt** | `consent` (매번 동의 요청) |
| **Redirect URI** | `http://localhost:3000/auth/google/callback` |
| **Scopes** | `gmail.readonly`, `drive.readonly`, `calendar.readonly` |
| **State Parameter** | `user_email` (CSRF 방어 겸 사용자 식별) |

#### 토큰 저장 구조

```
data/gws_tokens/
├── jeffrey_at_skin1004korea_com.json
├── user2_at_gmail_com.json
└── ...
```

각 파일 내용:
```json
{
  "token": "<access_token>",          // 1시간 유효
  "refresh_token": "<refresh_token>", // 장기 유효 (만료 시 재인증)
  "token_uri": "https://oauth2.googleapis.com/token",
  "client_id": "<Google OAuth Client ID>",
  "client_secret": "<Google OAuth Client Secret>",
  "scopes": ["gmail.readonly", "drive.readonly", "calendar.readonly"],
  "google_email": "user@gmail.com"    // 연결된 Google 계정
}
```

#### 토큰 보안 주의사항

| 위험 | 현재 상태 | 권장 조치 |
|------|----------|----------|
| **토큰 파일 평문 저장** | `client_secret` 포함 | Fernet 암호화 또는 Secret Manager |
| **파일 시스템 접근** | OS 파일 권한에 의존 | 600 퍼미션 설정 |
| **토큰 갱신** | `creds.refresh(Request())` 자동 | refresh_token 만료 시 재인증 필요 |

---

## 3. 권한 관리 (Authorization)

### 3.1 RBAC (Role-Based Access Control)

```
┌───────────────────────────────────────────────────────────────┐
│                      Role Hierarchy                            │
│                                                                 │
│   ┌─────────┐     모든 모델 접근                                │
│   │  admin  │──── 사용자 관리 (모델 권한 부여/해제)              │
│   │         │──── 유저 역할 변경                                 │
│   └────┬────┘──── Admin 패널 접근                               │
│        │                                                        │
│   ┌────▼────┐     허용된 모델만 접근                             │
│   │  user   │──── 채팅 기능                                      │
│   │         │──── 대화 이력 관리                                  │
│   └─────────┘──── Google 계정 연결                               │
│                                                                  │
└───────────────────────────────────────────────────────────────┘
```

#### 역할별 접근 권한 매트릭스

| API Endpoint | user | admin | 인증 불필요 |
|-------------|------|-------|----------|
| `POST /api/auth/signup` | - | - | O |
| `POST /api/auth/signin` | - | - | O |
| `POST /api/auth/logout` | - | - | O |
| `GET /api/auth/me` | O | O | - |
| `POST /v1/chat/completions` | O (허용 모델만) | O (전체) | - |
| `GET /api/conversations` | O (자기 것만) | O (자기 것만) | - |
| `GET /api/admin/users` | **403** | O | - |
| `PUT /api/admin/users/{id}/models` | **403** | O | - |
| `GET /auth/google/login` | - | - | O |
| `GET /auth/google/callback` | - | - | O |
| `GET /health` | - | - | O |
| `GET /safety/status` | - | - | O |
| `GET /login` | - | - | O |
| `GET /` | 쿠키 검사 → 리다이렉트 | 쿠키 검사 | O |

### 3.2 모델 접근 제어 (MAC)

사용자별로 접근 가능한 AI 모델을 관리합니다.

```
┌────────────────────────────────────────────────────┐
│              Model Access Control Flow               │
│                                                      │
│  사용자 요청:                                         │
│  POST /v1/chat/completions                           │
│  { "model": "skin1004-Analysis" }                    │
│       │                                              │
│       ▼                                              │
│  ┌─────────────────────────────────────┐             │
│  │ 1. JWT 검증 → User 객체 로드       │             │
│  └────────────┬────────────────────────┘             │
│               │                                      │
│       ┌───────▼───────┐                              │
│       │ user.role?    │                              │
│       └───┬───────┬───┘                              │
│     admin │       │ user                             │
│           │       │                                  │
│    ┌──────▼──┐  ┌─▼─────────────────────┐           │
│    │ 전체    │  │ allowed_models 확인   │           │
│    │ 허용    │  │ DB: "skin1004-Search" │           │
│    └─────────┘  └──────┬────────────────┘           │
│                         │                            │
│              ┌──────────▼──────────┐                 │
│              │ "Analysis" ∈       │                 │
│              │ allowed_models?    │                 │
│              └──┬──────────┬──────┘                 │
│             Yes │          │ No                      │
│                 │          │                         │
│          ┌──────▼──┐  ┌───▼──────┐                  │
│          │ 요청    │  │ 403      │                  │
│          │ 처리    │  │ Forbidden│                  │
│          └─────────┘  └──────────┘                  │
└────────────────────────────────────────────────────┘
```

#### 모델 목록

| 모델 ID | 내부 LLM | 용도 |
|---------|---------|------|
| `skin1004-Analysis` | Gemini 3 Pro | 복합 분석, 고품질 응답 |
| `skin1004-Search` | Claude Sonnet | 검색, 기본 질문 |

#### DB 스키마

```sql
-- users 테이블의 모델 접근 제어 컬럼
allowed_models TEXT DEFAULT 'skin1004-Search'
-- 예: "skin1004-Analysis,skin1004-Search" (콤마 구분)
```

### 3.3 Admin 자동 승격

서버 시작 시 `jeffrey@skin1004korea.com` 계정을 자동으로 admin으로 승격합니다.

```python
# app/main.py → _ensure_admin()
def _ensure_admin():
    user = db.query(User).filter(
        User.email == "jeffrey@skin1004korea.com"
    ).first()
    if user:
        user.role = "admin"
        user.allowed_models = "skin1004-Analysis,skin1004-Search"
```

| 파일 | 함수 | 역할 |
|------|------|------|
| `app/main.py` | `_ensure_admin()` | 서버 시작 시 admin 보장 |
| `app/api/admin_api.py` | `_require_admin()` | FastAPI Dependency: admin 여부 검증 |
| `app/api/admin_api.py` | `update_user_models()` | 사용자별 모델 권한 변경 |

---

## 4. SQL 보안 (SQL Injection Prevention)

Text-to-SQL Agent가 생성하는 SQL은 실행 전 **2단계 검증**을 거칩니다.

### 4.1 검증 파이프라인

```
┌──────────────────────────────────────────────────────────────────┐
│                  SQL Security Pipeline                             │
│                                                                    │
│  사용자 질문: "태국 매출 보여줘"                                    │
│       │                                                            │
│       ▼                                                            │
│  ┌─────────────────────────────────────────┐                      │
│  │  Stage 1: LLM SQL Generation            │                      │
│  │  (Gemini Flash)                          │                      │
│  │  "SELECT SUM(Sales1_R) FROM ..."         │                      │
│  └────────────────┬────────────────────────┘                      │
│                   │                                                │
│       ┌───────────▼───────────┐                                   │
│       │  Stage 2: sanitize_sql()  │  ← app/core/security.py      │
│       │  - 마크다운 코드블록 제거    │                               │
│       │  - SQL 추출                 │                               │
│       │  - LIMIT 강제 추가          │                               │
│       └───────────┬───────────┘                                   │
│                   │                                                │
│       ┌───────────▼───────────┐                                   │
│       │  Stage 3: validate_sql()  │  ← app/core/security.py      │
│       │                           │                                │
│       │  ✓ SELECT/WITH만 허용     │                                │
│       │  ✗ INSERT/UPDATE/DELETE   │                                │
│       │  ✗ DROP/ALTER/CREATE      │                                │
│       │  ✗ TRUNCATE/MERGE         │                                │
│       │  ✗ GRANT/REVOKE           │                                │
│       │  ✗ EXEC/EXECUTE/CALL      │                                │
│       │  ✗ INTO (SELECT INTO 차단)│                                │
│       │                           │                                │
│       │  ✓ 테이블 화이트리스트     │                                │
│       │  ✗ SQL Injection 패턴     │                                │
│       │  ⚠ LIMIT 누락 경고        │                                │
│       └───────────┬───────────┘                                   │
│                   │                                                │
│       ┌───────────▼───────────┐                                   │
│       │  Stage 4: QueryVerifier  │  ← app/agents/query_verifier.py│
│       │  (Claude Sonnet LLM)      │                                │
│       │                           │                                │
│       │  - BigQuery 문법 검증      │                                │
│       │  - 스키마 일관성 검증      │                                │
│       │  - 컬럼 매핑 규칙 검증     │                                │
│       │  - 날짜 필터 검증          │                                │
│       │  - SQL 자동 수정 (필요시)  │                                │
│       └───────────┬───────────┘                                   │
│                   │                                                │
│       ┌───────────▼───────────┐                                   │
│       │  Stage 5: BigQuery 실행  │  ← app/core/bigquery.py       │
│       │                          │                                 │
│       │  - Timeout: 30초         │                                 │
│       │  - Max Rows: 10,000      │                                 │
│       │  - READ-ONLY 계정        │                                 │
│       └──────────────────────┘                                    │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 SQL Injection 방어 패턴

```python
# app/core/security.py에서 탐지하는 패턴
INJECTION_PATTERNS = [
    r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE)",  # Stacked Queries
    r"--\s",                    # SQL 주석 주입
    r"/\*.*?\*/",               # 블록 주석 주입
    r"xp_\w+",                  # SQL Server 확장 프로시저
    r"INFORMATION_SCHEMA",      # 메타데이터 탐색
    r"sys\.\w+",                # 시스템 테이블 접근
]
```

### 4.3 테이블 화이트리스트

```
┌─────────────────────────────────────────────────┐
│              Table Whitelist                      │
│                                                   │
│  허용된 테이블 (READ ONLY):                       │
│  ┌─────────────────────────────────────────────┐ │
│  │ skin1004-319714.Sales_Integration           │ │
│  │   .SALES_ALL_Backup                         │ │
│  │                                             │ │
│  │ skin1004-319714.Sales_Integration           │ │
│  │   .Product                                  │ │
│  └─────────────────────────────────────────────┘ │
│                                                   │
│  차단 예시:                                       │
│  ✗ skin1004-319714.AI_RAG.rag_embeddings         │
│  ✗ skin1004-319714.other_dataset.any_table       │
│  ✗ other-project.any.table                       │
└─────────────────────────────────────────────────┘
```

---

## 5. 데이터 보안 (Data Protection)

### 5.1 데이터 흐름도

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Data Flow & Storage                           │
│                                                                       │
│  ┌──────────┐     httpOnly       ┌──────────────┐                    │
│  │  Client  │────Cookie (JWT)───>│  FastAPI     │                    │
│  │ Browser  │<───Set-Cookie──────│  Server      │                    │
│  └──────────┘                    └──────┬───────┘                    │
│       │                                 │                             │
│       │ (이미지는 base64               │                             │
│       │  인라인 전송,                   │                             │
│       │  서버에 파일 저장 안 함)         │                             │
│       │                                 │                             │
│       │                    ┌────────────┼────────────────┐           │
│       │                    │            │                │           │
│       │               ┌────▼─────┐  ┌───▼──────┐  ┌─────▼────┐     │
│       │               │ SQLite   │  │ BigQuery │  │ Token    │     │
│       │               │ (로컬)   │  │  (GCP)   │  │ Files    │     │
│       │               └──────────┘  └──────────┘  └──────────┘     │
│       │                    │            │                │           │
│       │               저장 항목:     저장 항목:       저장 항목:      │
│       │               - users       - SALES_ALL     - access_token  │
│       │               - password      _Backup       - refresh_token │
│       │                 (bcrypt)    - Product        - client_secret │
│       │               - conversations               - google_email  │
│       │               - messages                                     │
│       │               - allowed_models                               │
│       │                                                              │
│       │               ⚠ 이미지 미저장                                │
│       │               ⚠ SQL 결과 미저장                              │
│       │               ⚠ API 키 미저장                                │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 민감 데이터 관리

| 데이터 | 저장 위치 | 보호 방식 |
|--------|----------|----------|
| **비밀번호** | SQLite `users.password` | bcrypt 해시 (원문 저장 안 함) |
| **JWT Secret** | `.env` 파일 | 환경변수, 코드에 하드코딩 안 함 |
| **GCP Service Account Key** | `C:/json_key/...` | 로컬 파일, 프로덕션은 Secret Manager |
| **Gemini API Key** | `.env` 파일 | 환경변수 |
| **Anthropic API Key** | `.env` 파일 | 환경변수 |
| **Google OAuth Client Secret** | `.env` 파일 | 환경변수 |
| **Notion API Token** | `.env` 파일 | 환경변수 |
| **OAuth Access Token** | `data/gws_tokens/*.json` | 파일 시스템 |
| **사용자 대화 이력** | SQLite `messages.content` | 텍스트만 (이미지 미저장) |

### 5.3 환경변수 로딩 체계

```
┌────────────────────────────────────────────────────┐
│                 Config Loading Chain                 │
│                                                      │
│  .env 파일                                           │
│  ┌─────────────────────────────────────────┐        │
│  │ GEMINI_API_KEY=AIzaSy...                │        │
│  │ ANTHROPIC_API_KEY=sk-ant...             │        │
│  │ JWT_SECRET_KEY=my-secret...             │        │
│  │ GOOGLE_OAUTH_CLIENT_ID=123...           │        │
│  │ GOOGLE_OAUTH_CLIENT_SECRET=GOC...       │        │
│  │ NOTION_MCP_TOKEN=ntn_...                │        │
│  │ ...                                     │        │
│  └─────────────────┬───────────────────────┘        │
│                    │                                 │
│                    ▼                                 │
│  pydantic-settings (BaseSettings)                    │
│  ┌─────────────────────────────────────────┐        │
│  │ class Settings(BaseSettings):           │        │
│  │     gemini_api_key: str = ""            │        │
│  │     jwt_secret_key: str = "change-me"   │        │
│  │     ...                                 │        │
│  └─────────────────┬───────────────────────┘        │
│                    │                                 │
│                    ▼                                 │
│  @lru_cache()                                        │
│  def get_settings() → Settings                       │
│  (앱 전체에서 싱글톤으로 사용)                         │
│                                                      │
│  ⚠ os.getenv() 직접 사용 금지                        │
│  ⚠ 코드에 API 키 하드코딩 금지                       │
└────────────────────────────────────────────────────┘
```

---

## 6. 네트워크 보안 (Network Security)

### 6.1 CORS 설정

```python
# app/api/middleware.py
CORSMiddleware(
    allow_origins=["*"],        # ⚠ 모든 오리진 허용 (개발용)
    allow_credentials=True,     # 쿠키 포함 허용
    allow_methods=["*"],        # 모든 HTTP 메서드 허용
    allow_headers=["*"],        # 모든 헤더 허용
)
```

> **프로덕션 권장**: `allow_origins`를 특정 도메인으로 제한
> 예: `["https://ai.skin1004.com"]`

### 6.2 네트워크 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                    Network Architecture                          │
│                                                                   │
│  ┌──────────────────────────────────────────────────────┐        │
│  │                  Internal Network                     │        │
│  │                                                       │        │
│  │  ┌──────────┐          ┌──────────────────────────┐  │        │
│  │  │  Client  │──:3000──>│  FastAPI Server           │  │        │
│  │  │ Browser  │          │  (0.0.0.0:3000)           │  │        │
│  │  └──────────┘          │                            │  │        │
│  │                        │  Endpoints:                 │  │        │
│  │                        │  /login          (공개)     │  │        │
│  │                        │  /api/auth/*     (공개)     │  │        │
│  │                        │  /health         (공개)     │  │        │
│  │                        │  /v1/*           (인증필요) │  │        │
│  │                        │  /api/admin/*    (관리자)   │  │        │
│  │                        │  /api/conversations/* (인증)│  │        │
│  │                        │  /auth/google/*  (공개)     │  │        │
│  │                        │  /safety/status  (공개)     │  │        │
│  │                        │  /docs           (공개)     │  │        │
│  │                        └───────┬──────────────────┘  │        │
│  └────────────────────────────────┼─────────────────────┘        │
│                                   │                               │
│  ┌────────────────────────────────┼──────────────────────────┐   │
│  │              External Services │ (HTTPS Outbound)          │   │
│  │                                │                            │   │
│  │  ┌────────────────────────┐   │   ┌────────────────────┐  │   │
│  │  │  Google Cloud Platform │<──┼──>│  Google APIs       │  │   │
│  │  │  - BigQuery            │   │   │  - Gmail API       │  │   │
│  │  │  - Cloud Storage       │   │   │  - Drive API       │  │   │
│  │  └────────────────────────┘   │   │  - Calendar API    │  │   │
│  │                               │   │  - OAuth2          │  │   │
│  │  ┌────────────────────────┐   │   └────────────────────┘  │   │
│  │  │  Anthropic API        │<──┤                            │   │
│  │  │  (Claude)              │   │   ┌────────────────────┐  │   │
│  │  └────────────────────────┘   │   │  Notion API        │  │   │
│  │                               │   │  (Document search) │  │   │
│  │  ┌────────────────────────┐   │   └────────────────────┘  │   │
│  │  │  Google AI (Gemini)   │<──┘                            │   │
│  │  └────────────────────────┘                                │   │
│  └────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 외부 통신 상세

| 대상 | 프로토콜 | 인증 방식 | 포트 | 용도 |
|------|---------|----------|------|------|
| BigQuery | HTTPS | GCP Service Account JSON Key | 443 | SQL 실행, 스키마 조회 |
| Gemini API | HTTPS | API Key | 443 | LLM 추론, SQL 생성 |
| Anthropic API | HTTPS | API Key (Bearer) | 443 | Claude 추론, SQL 검증 |
| Google OAuth | HTTPS | Client ID + Secret | 443 | 사용자 인증 |
| Gmail API | HTTPS | OAuth2 Bearer Token | 443 | 메일 검색 |
| Drive API | HTTPS | OAuth2 Bearer Token | 443 | 파일 검색 |
| Calendar API | HTTPS | OAuth2 Bearer Token | 443 | 일정 조회 |
| Notion API | HTTPS | Integration Token (Bearer) | 443 | 문서 검색 |
| Google Sheets | HTTPS | GCP Service Account | 443 | CS Q&A 데이터 |

---

## 7. 안전 장치 (Safety Systems)

### 7.1 CircuitBreaker (서비스별 차단기)

외부 서비스 장애 시 연쇄 실패를 방지합니다.

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
│   │  시도    │<──────────────────│HALF_OPEN │             │
│   │  성공    │   1회 시도 허용    │ (테스트) │             │
│   └──────────┘                   └──────────┘             │
│                                       │                    │
│                                  실패 │                    │
│                                       │                    │
│                                  ┌────▼─────┐             │
│                                  │   OPEN   │             │
│                                  │ (재차단) │             │
│                                  └──────────┘             │
│                                                            │
│   설정값:                                                  │
│   - failure_threshold: 3 (3회 실패 → 차단)                │
│   - cooldown_seconds: 60 (60초 후 시도)                   │
│                                                            │
│   적용 서비스:                                             │
│   - bigquery (SQL 실행)                                   │
│   - notion (문서 검색)                                    │
│   - gws (Google Workspace)                                │
└──────────────────────────────────────────────────────────┘
```

### 7.2 MaintenanceManager (자동 점검 감지)

BigQuery 테이블 업데이트를 자동으로 감지하고 쿼리를 차단합니다.

```
┌──────────────────────────────────────────────────────────────┐
│              Maintenance Auto-Detection Loop                   │
│                                                                │
│   서버 시작 (10초 대기 후)                                     │
│        │                                                       │
│        ▼                                                       │
│   ┌────────────────────────────────────────┐                  │
│   │  __TABLES__ 메타데이터 폴링 (매 60초)    │                  │
│   │                                          │                  │
│   │  SQL (무료 쿼리):                         │                  │
│   │  SELECT row_count,                       │                  │
│   │    TIMESTAMP_DIFF(...) as modified_ago   │                  │
│   │  FROM __TABLES__                         │                  │
│   │  WHERE table_id = 'SALES_ALL_Backup'     │                  │
│   └─────────────┬────────────────────────────┘                  │
│                 │                                               │
│       ┌─────────▼──────────┐                                   │
│       │   감지 조건 확인    │                                   │
│       └──┬──────────┬──────┘                                   │
│          │          │                                           │
│    ┌─────▼─────┐  ┌─▼───────────────┐                         │
│    │ 최근 수정  │  │ Row count 변동  │                         │
│    │ < 180초?   │  │ > 5% 감소?      │                         │
│    └──┬────┬───┘  └──┬──────┬───────┘                         │
│    Yes│    │No    Yes│      │No                                │
│       │    │         │      │                                  │
│  ┌────▼────▼─────────▼──┐  │                                  │
│  │  "updating" 상태     │  │                                  │
│  │  → SQL 쿼리 차단     │  │                                  │
│  │  → UI에 경고 표시     │  │                                  │
│  └──────────────────────┘  │                                  │
│                             │                                  │
│                    ┌────────▼────────┐                         │
│                    │  Row count      │                         │
│                    │  >= 98% 회복?   │                         │
│                    └──┬─────────┬────┘                         │
│                   Yes │         │ No                           │
│                       │         │                              │
│              ┌────────▼──┐  ┌───▼──────┐                      │
│              │  정상 복귀 │  │ 계속     │                      │
│              │  baseline │  │ 모니터링 │                      │
│              │  업데이트  │  └──────────┘                      │
│              └───────────┘                                     │
│                                                                │
│   _UPDATE_WINDOW_SECONDS = 180 (3분)                          │
│   polling interval = 60초                                      │
└──────────────────────────────────────────────────────────────┘
```

### 7.3 Safety Status API

```
GET /safety/status → 전체 시스템 상태 반환

응답 예시:
{
  "maintenance": {
    "active": false,
    "reason": "",
    "manual": false
  },
  "services": {
    "BigQuery 매출": { "status": "ok", "detail": "SALES_ALL_Backup" },
    "BigQuery 제품": { "status": "ok", "detail": "Product" },
    "Notion 문서": { "status": "ok", "detail": "10 pages" },
    "CS Q&A": { "status": "ok", "detail": "739 entries" },
    "Google Workspace": { "status": "ok", "detail": "OAuth ready" },
    "Gemini API": { "status": "ok", "detail": "Flash" },
    "Claude API": { "status": "ok", "detail": "Sonnet" },
    "GWS Token": { "status": "ok", "detail": "3 users" }
  },
  "circuits": {
    "bigquery": { "state": "closed", "failure_count": 0 },
    "notion": { "state": "closed", "failure_count": 0 }
  }
}
```

---

## 8. Request Logging & Monitoring

### 8.1 요청 로깅

모든 HTTP 요청은 구조화된 JSON 로그로 기록됩니다.

```
┌────────────────────────────────────────────────────┐
│              Request Logging Pipeline                │
│                                                      │
│  모든 요청:                                          │
│  ┌──────────────────────────────────────┐           │
│  │  RequestLoggingMiddleware            │           │
│  │                                      │           │
│  │  1. Request ID 생성 (UUID[:8])       │           │
│  │  2. JWT에서 user_email 추출          │           │
│  │  3. 요청 시작 로그:                   │           │
│  │     {                                │           │
│  │       "event": "request_started",    │           │
│  │       "request_id": "a1b2c3d4",     │           │
│  │       "method": "POST",             │           │
│  │       "path": "/v1/chat/completions",│           │
│  │       "client": "192.168.1.100",    │           │
│  │       "user_email": "user@ex.com"   │           │
│  │     }                                │           │
│  │                                      │           │
│  │  4. 응답 완료 로그:                   │           │
│  │     {                                │           │
│  │       "event": "request_completed",  │           │
│  │       "request_id": "a1b2c3d4",     │           │
│  │       "status_code": 200,           │           │
│  │       "latency_ms": 1523            │           │
│  │     }                                │           │
│  └──────────────────────────────────────┘           │
│                                                      │
│  제외 경로 (노이즈 방지):                            │
│  - /health                                           │
│  - /admin/maintenance/status                         │
│  - /safety/status                                    │
│                                                      │
│  응답 헤더 추가:                                     │
│  - X-Request-ID: a1b2c3d4                            │
│  - X-Latency-Ms: 1523                               │
└────────────────────────────────────────────────────┘
```

---

## 9. 데이터베이스 보안 (Database)

### 9.1 SQLite (로컬 인증 DB)

```
┌────────────────────────────────────────────────────────────────┐
│                   SQLite Database Schema                         │
│                                                                  │
│  경로: C:/Users/DB_PC/.open-webui/data/skin1004_chat.db         │
│                                                                  │
│  ┌──────────────────────────────────────────────────────┐       │
│  │  users                                                │       │
│  │  ┌────────────────┬──────────┬───────────────────┐   │       │
│  │  │ Column         │ Type     │ 보안 특성          │   │       │
│  │  ├────────────────┼──────────┼───────────────────┤   │       │
│  │  │ id             │ CHAR(32) │ UUID, PK          │   │       │
│  │  │ email          │ VARCHAR  │ UNIQUE, NOT NULL   │   │       │
│  │  │ name           │ VARCHAR  │ NOT NULL           │   │       │
│  │  │ password       │ VARCHAR  │ bcrypt hash only   │   │       │
│  │  │ role           │ VARCHAR  │ "admin" or "user"  │   │       │
│  │  │ allowed_models │ TEXT     │ CSV model list     │   │       │
│  │  │ created_at     │ DATETIME │ UTC                │   │       │
│  │  └────────────────┴──────────┴───────────────────┘   │       │
│  └──────────────────────────────────────────────────────┘       │
│                          │ 1:N                                   │
│  ┌──────────────────────▼───────────────────────────────┐       │
│  │  conversations                                        │       │
│  │  ┌────────────────┬──────────┬───────────────────┐   │       │
│  │  │ id             │ CHAR(32) │ UUID, PK          │   │       │
│  │  │ user_id        │ CHAR(32) │ FK → users.id     │   │       │
│  │  │ title          │ VARCHAR  │ 대화 제목          │   │       │
│  │  │ model          │ VARCHAR  │ 사용 모델          │   │       │
│  │  │ created_at     │ DATETIME │ UTC                │   │       │
│  │  │ updated_at     │ DATETIME │ UTC                │   │       │
│  │  └────────────────┴──────────┴───────────────────┘   │       │
│  └──────────────────────────────────────────────────────┘       │
│                          │ 1:N                                   │
│  ┌──────────────────────▼───────────────────────────────┐       │
│  │  messages                                             │       │
│  │  ┌────────────────┬──────────┬───────────────────┐   │       │
│  │  │ id             │ CHAR(32) │ UUID, PK          │   │       │
│  │  │ conversation_id│ CHAR(32) │ FK → conversations│   │       │
│  │  │ role           │ VARCHAR  │ "user"/"assistant" │   │       │
│  │  │ content        │ TEXT     │ 텍스트만 (no img) │   │       │
│  │  │ created_at     │ DATETIME │ UTC                │   │       │
│  │  └────────────────┴──────────┴───────────────────┘   │       │
│  └──────────────────────────────────────────────────────┘       │
│                                                                  │
│  CASCADE 정책:                                                   │
│  - User 삭제 → Conversations 자동 삭제 → Messages 자동 삭제     │
│                                                                  │
│  접근 제어:                                                      │
│  - 사용자는 자신의 conversation만 조회 가능                      │
│  - Admin도 다른 사용자 대화 내용 조회 불가                       │
│  - Admin은 사용자 목록 + 모델 권한만 관리                        │
└────────────────────────────────────────────────────────────────┘
```

### 9.2 BigQuery (GCP)

| 항목 | 설정 |
|------|------|
| **인증** | Service Account JSON Key |
| **프로젝트** | `skin1004-319714` |
| **접근 모드** | READ-ONLY (SELECT만 허용) |
| **Timeout** | 30초 |
| **Max Rows** | 10,000행 |
| **IAM 역할** | BigQuery Data Viewer + Job User |

---

## 10. 보안 위험 평가 및 프로덕션 권장사항

### 10.1 현재 위험 항목

```
┌──────────────────────────────────────────────────────────────────┐
│                    Security Risk Assessment                       │
│                                                                    │
│  ⬛⬛⬛ HIGH                                                      │
│  ┌──────────────────────────────────────────────────────┐        │
│  │ 1. CORS allow_origins=["*"]                           │        │
│  │    → 모든 오리진에서 API 호출 가능                     │        │
│  │    → 프로덕션: 특정 도메인으로 제한 필수              │        │
│  │                                                       │        │
│  │ 2. JWT Secret Key 기본값 노출                         │        │
│  │    → config.py: "skin1004-ai-secret-change-me"        │        │
│  │    → .env에서 강력한 키로 변경 필수                    │        │
│  │                                                       │        │
│  │ 3. GWS 토큰 파일 평문 저장                            │        │
│  │    → client_secret 포함, 암호화 없음                  │        │
│  │    → Fernet 암호화 또는 Secret Manager 사용           │        │
│  │                                                       │        │
│  │ 4. Cookie secure=False (HTTP 전송 허용)               │        │
│  │    → HTTPS 환경에서 secure=True 필수                  │        │
│  └──────────────────────────────────────────────────────┘        │
│                                                                    │
│  ⬛⬛ MEDIUM                                                       │
│  ┌──────────────────────────────────────────────────────┐        │
│  │ 5. /docs (Swagger UI) 공개 접근                       │        │
│  │    → 프로덕션: docs_url=None으로 비활성화             │        │
│  │                                                       │        │
│  │ 6. /safety/status 인증 없이 접근 가능                 │        │
│  │    → 시스템 상태 정보 노출                            │        │
│  │    → 내부 네트워크에서만 접근하도록 제한              │        │
│  │                                                       │        │
│  │ 7. 비밀번호 최소 길이 4자                             │        │
│  │    → 최소 8자 + 복잡도 규칙 권장                      │        │
│  │                                                       │        │
│  │ 8. Rate Limiting 미적용                               │        │
│  │    → Brute Force 공격에 취약                          │        │
│  │    → slowapi 또는 nginx rate limit 적용               │        │
│  └──────────────────────────────────────────────────────┘        │
│                                                                    │
│  ⬛ LOW                                                            │
│  ┌──────────────────────────────────────────────────────┐        │
│  │ 9. SQLite 파일 기반 DB                                │        │
│  │    → 동시 쓰기 제한, 대규모 사용 시 PostgreSQL 전환   │        │
│  │                                                       │        │
│  │ 10. OAuth state에 이메일 평문 사용                    │        │
│  │     → 서명된 state 또는 서버 세션 사용 권장           │        │
│  │                                                       │        │
│  │ 11. Admin 하드코딩 이메일                             │        │
│  │     → jeffrey@skin1004korea.com 자동 승격             │        │
│  │     → 환경변수로 관리 권장                            │        │
│  └──────────────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────────┘
```

### 10.2 프로덕션 체크리스트

```
□ CORS allow_origins를 실제 도메인으로 제한
□ JWT_SECRET_KEY를 32바이트 이상 랜덤 키로 변경
□ Cookie에 secure=True 추가 (HTTPS 필수)
□ Swagger UI 비활성화 (docs_url=None, redoc_url=None)
□ GCP Service Account Key → Secret Manager 이전
□ GWS 토큰 파일 암호화 (Fernet)
□ 비밀번호 정책 강화 (8자+, 특수문자, 대소문자)
□ Rate Limiting 적용 (로그인 엔드포인트 필수)
□ HTTPS/TLS 적용 (nginx reverse proxy 또는 Cloud Run)
□ /safety/status 접근 제한 (내부 네트워크 only)
□ 로그에 민감 정보 마스킹 (이메일, 토큰)
□ SQLite → PostgreSQL 마이그레이션 (사용자 증가 시)
□ Admin 이메일 환경변수 관리
□ CSP (Content-Security-Policy) 헤더 추가
□ HSTS 헤더 추가
```

---

## 11. 전체 보안 구조 요약도

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    SKIN1004 AI - Full Security Map                        │
│                                                                           │
│   Client (Browser)                                                        │
│   ┌─────────────────────────────────────────────────────────────┐        │
│   │  ① httpOnly Cookie (JWT) — XSS 방어                        │        │
│   │  ② SameSite=Lax          — CSRF 기본 방어                  │        │
│   │  ③ 이미지 base64 인라인   — 파일 업로드 없음               │        │
│   └─────────────────────────────────────────┬───────────────────┘        │
│                                              │                            │
│   FastAPI Server (:3000)                     │                            │
│   ┌──────────────────────────────────────────▼──────────────────┐        │
│   │  ④ CORS Middleware        — 오리진 제어 (현재: *)           │        │
│   │  ⑤ Request Logging       — JSON 구조화 로그 + Request ID   │        │
│   │  ⑥ JWT Validation        — HS256, 7일 만료                 │        │
│   │  ⑦ RBAC                  — admin / user 역할               │        │
│   │  ⑧ Model Access Control  — 사용자별 모델 제한              │        │
│   │  ⑨ SQL Validation        — SELECT ONLY + 화이트리스트      │        │
│   │  ⑩ SQL Injection Defense — 패턴 매칭 + LLM 이중 검증      │        │
│   │  ⑪ CircuitBreaker        — 3회 실패 → 60초 차단            │        │
│   │  ⑫ MaintenanceManager    — 테이블 업데이트 자동 감지       │        │
│   │  ⑬ Query Timeout         — BigQuery 30초 제한              │        │
│   │  ⑭ Row Limit             — 최대 10,000행                   │        │
│   └──────────────┬─────────────────────┬────────────────────────┘        │
│                  │                     │                                  │
│   ┌──────────────▼──────┐  ┌──────────▼──────────────────────┐          │
│   │  SQLite (Local)      │  │  External APIs (HTTPS)          │          │
│   │  ⑮ bcrypt password  │  │  ⑯ GCP Service Account Key     │          │
│   │  ⑮ UUID primary key │  │  ⑰ API Key (환경변수)           │          │
│   │  ⑮ CASCADE delete   │  │  ⑱ OAuth2 per-user token       │          │
│   │  ⑮ 사용자별 데이터   │  │  ⑲ Notion Integration Token    │          │
│   │     격리 (user_id)   │  │  ⑳ Token auto-refresh          │          │
│   └─────────────────────┘  └──────────────────────────────────┘          │
│                                                                           │
│   Config & Secrets                                                        │
│   ┌─────────────────────────────────────────────────────────────┐        │
│   │  ㉑ pydantic-settings    — .env 파일에서 로드              │        │
│   │  ㉒ @lru_cache singleton — 설정 객체 캐싱                  │        │
│   │  ㉓ 코드 하드코딩 금지    — 모든 키는 환경변수             │        │
│   └─────────────────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 12. 파일 참조 (Quick Reference)

| 보안 영역 | 파일 경로 | 핵심 함수/클래스 |
|----------|----------|----------------|
| JWT 인증 | `app/api/auth_api.py` | `signup()`, `signin()`, `_create_token()` |
| JWT 검증 | `app/api/auth_middleware.py` | `get_current_user()` |
| OAuth2 Flow | `app/api/auth_routes.py` | `google_login()`, `google_callback()` |
| OAuth2 Manager | `app/core/google_auth.py` | `GoogleAuthManager` |
| Admin 권한 | `app/api/admin_api.py` | `_require_admin()`, `update_user_models()` |
| SQL 안전장치 | `app/core/security.py` | `validate_sql()`, `sanitize_sql()` |
| SQL 이중검증 | `app/agents/query_verifier.py` | `QueryVerifierAgent` |
| CircuitBreaker | `app/core/safety.py` | `CircuitBreaker`, `get_circuit()` |
| Maintenance | `app/core/safety.py` | `MaintenanceManager`, `maintenance_auto_detect_loop()` |
| 요청 로깅 | `app/api/middleware.py` | `RequestLoggingMiddleware` |
| CORS 설정 | `app/api/middleware.py` | `setup_middleware()` |
| 환경변수 | `app/config.py` | `Settings`, `get_settings()` |
| DB 스키마 | `app/db/models.py` | `User`, `Conversation`, `Message` |
| DB 초기화 | `app/db/database.py` | `init_db()`, `_migrate()` |
| 서버 시작 | `app/main.py` | `create_app()`, `_ensure_admin()` |

---

> **문서 끝** | SKIN1004 Enterprise AI Security Architecture v1.0
