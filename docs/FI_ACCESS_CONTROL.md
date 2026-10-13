# 재무 손익(FI) 열람 권한 통제 — 구현 계획

**작성일**: 2026-08-03
**목적**: `FI_LLM_Flat`(재무 연결손익) 데이터를 지정된 소수만 조회할 수 있게 제한한다.
**허용 대상 (8명)** — 2026-08-03 `ad_users` 조회로 검증 완료, **동명이인 없음**:

| 이름 | AD username | 부서 | `users` 가입 |
|---|---|---|---|
| 임재필 | `jeffrey` | Data Business > 데이터분석 | ✅ (admin) |
| 양승민 | `smyang` | Data Business > 데이터분석 | ✅ |
| 이형섭 | `chris` | Brand Division > Operations Dept | ✅ |
| 진한얼 | `hejin` | Corporate Strategy > 경영관리 | ❌ 미가입 |
| 남혜림 | `hlnam` | Corporate Strategy > 경영관리 | ❌ 미가입 |
| 우지윤 | `chloe_woo` | Corporate Division > Finance·IT Dept | ❌ 미가입 |
| 곽인승 | `kwak` | Brand Division | ❌ 미가입 |
| 강승찬 | `sckang` | Corporate Strategy Dept | ❌ 미가입 |

권한 부여 스크립트는 **이름이 아니라 위 `username` 기준**으로 UPDATE 한다
(AD 동기화로 `display_name` 이 바뀌어도 안전).

---

## 0. 설계 결정

**권한 플래그는 `ad_users.can_view_fi` 에 둔다** (`users` 아님).

- 대상 8명 중 5명(진한얼·남혜림·우지윤·곽인승·강승찬)은 아직 `users` 행이 없다.
  AD 계정만 있는 상태다. `users`에 두면 이 5명은 **가입할 때까지 권한을 줄 수 없고**,
  가입할 때마다 관리자가 다시 체크해야 한다.
- `user_groups`(그룹 권한)도 이미 `ad_user_id` 를 키로 쓴다 — 일관된다.
- Admin 탭의 "AD 사용자" 목록이 이미 `ad_users` 를 렌더링하므로 UI 를 그대로 확장할 수 있다.

**AD 동기화 안전성 확인 완료**: `scripts/sync_ad_users.py:342-348` 의
`ON DUPLICATE KEY UPDATE` 는 갱신 컬럼을 명시적으로 나열한다
(`display_name/email/department/full_dn/is_active/synced_at`).
`can_view_fi` 는 목록에 없으므로 **매일 22:00 동기화에 덮어써지지 않는다.**

---

## 1. DB 스키마

### 1-1. 컬럼 추가

```sql
ALTER TABLE ad_users
  ADD COLUMN can_view_fi TINYINT(1) NOT NULL DEFAULT 0
  COMMENT '재무 손익(FI_LLM_Flat) 열람 허용';
```

`app/db/mariadb.py` 에 기존 패턴대로 idempotent 함수를 추가한다
(`ensure_knowledge_wiki_table` 이 `ALTER ... ADD COLUMN` 을 try/except 로 감싸는 방식과 동일):

```python
def ensure_fi_permission_column():
    """Add ad_users.can_view_fi if missing (idempotent)."""
```

`app/main.py:117-130` 의 `_ensure_fn` 튜플에 등록 → 앱 기동 시 로컬·신규 서버 DB 양쪽에 자동 적용.
별도 마이그레이션 스크립트 불필요.

### 1-2. 초기 권한 부여

`scripts/grant_fi_access.py` 신규 작성.

- 하드코딩된 8명의 **`username`**(AD 계정) 목록으로 매칭해 `can_view_fi = 1`
  (`display_name` 은 AD 동기화로 바뀔 수 있으므로 매칭 키로 쓰지 않는다)
- `--dry-run` 으로 매칭 결과 먼저 확인 (미매칭 검출)
- 매칭 실패 시 중단하고 해당 계정 출력 — 조용히 넘어가지 않는다
- 재실행 안전(idempotent). 회수는 `--revoke` 로

---

## 2. 권한 조회 — DB 단일 소스

**JWT 클레임에 담지 않는다.** 토큰 TTL 동안 값이 고여 권한 회수가 즉시 반영되지 않는다.
`brand_filter` 가 이미 "항상 DB에서" 원칙(`app/api/routes.py:110`)을 쓰므로 동일하게 간다.

### 2-1. 채팅 요청 경로

`app/api/routes.py:113-127` 의 기존 brand_filter 조회 쿼리를 확장 — **쿼리 추가 없이 1회 조회**:

```sql
SELECT u.role, a.can_view_fi, g.brand_filter
FROM users u
LEFT JOIN ad_users a ON u.ad_user_id = a.id
LEFT JOIN user_groups ug ON u.ad_user_id = ug.ad_user_id
LEFT JOIN access_groups g ON ug.group_id = g.id AND g.brand_filter IS NOT NULL
WHERE u.id = %s LIMIT 1
```

⚠️ 기존 쿼리의 `AND u.role != 'admin'` 조건을 WHERE 에서 빼고 파이썬에서 분기해야 한다
(admin 도 `can_view_fi` 판정이 필요하므로 행을 받아야 함). **brand_filter 의미는 그대로 유지**:

```python
is_admin = row["role"] == "admin"
brand_filter = None if is_admin else (row.get("brand_filter") or None)
can_view_fi = is_admin or bool(row.get("can_view_fi"))
```

- admin → 무조건 True
- `ad_user_id` 가 없는 계정(로컬 전용) → False

### 2-2. `/api/auth/me`

응답에 `can_view_fi` 필드 추가 (`app/api/auth_api.py:387` 의 `me`).
프론트 UI 노출 제어용 — **보안 경계가 아니다.**

---

## 3. 차단 — 3중

### (a) 라우트 진입 전 게이트 — 사용자 경험

`app/agents/orchestrator.py` — FI 키워드(이미 1337-1338 줄에 존재:
영업이익·매출총이익·매출원가·판관비·손익·이익률·원가율·광고선전비)에 걸리고
`can_view_fi=False` 면 **LLM·BigQuery 호출 없이 즉시 응답**:

> 재무 손익 데이터는 별도 열람 권한이 필요합니다.
> 필요하시면 임재필에게 요청해 주세요.

(2026-08-03 확정. 상수 하나로 관리해 문구 교체가 한 곳에서 되게 한다.)

`route_and_execute`(313) / `route_and_stream`(514) 양쪽에 적용.
`enabled_sources` 에 `"손익"` 이 들어온 경우도 같은 지점에서 차단.

### (b) SQL 생성 단계 — LLM이 테이블을 모르게

`app/agents/sql_agent.py`:

- `_allowed_tables_from_sources`(293) — `enabled_sources` 가 `None`(전체 허용)일 때도
  `can_view_fi=False` 면 `settings.allowed_tables` 에서 FI 를 뺀 집합을 반환하도록 변경.
  즉 반환값이 `None` 이 될 수 있는 경로를 권한 없는 사용자에 대해 제거한다.
- 프롬프트 마스킹: `prompts/sql_generator.txt` 의 FI 관련 두 부분을 로드 후 제거
  - 33행 라우팅 표의 `FI_LLM_Flat` 행
  - 1277-1325행 `## 테이블 14: FI_LLM_Flat` 섹션 전체 (다음 `## 테이블` 헤더 직전까지)

  `_load_prompt` 캐시와 충돌하지 않도록 **캐시 키를 `(filename, can_view_fi)` 로 분리**한다.
  헤더 문자열이 바뀌면 조용히 실패할 수 있으므로 테스트로 고정한다(6번 참조).

### (c) 실행 직전 하드 게이트 — 최종 권위

`app/core/security.py:36` `validate_sql(sql)` → `validate_sql(sql, allowed_tables=None)` 로 확장.
`None` 이면 기존대로 `settings.allowed_tables` 사용(하위 호환).
sql_agent 의 validate 노드가 **사용자별 집합을 넘긴다.**

→ 라우터를 우회하든 프롬프트 마스킹이 뚫리든, FI 를 참조하는 SQL 은 여기서 실행 전에 막힌다.
차단 시 "허용되지 않은 테이블입니다" 대신 (a)와 동일한 권한 안내 문구로 치환한다.

`_cache_lookup`(150)은 이미 `allowed_tables` 를 검사하므로(178-183) 캐시 경유 유출 없음.

### 파라미터 전달 경로

`brand_filter` 와 동일한 경로로 `can_view_fi: bool` 을 흘린다:

```
routes.py:194 route_and_execute / routes.py:270 route_and_stream
  → orchestrator 핸들러 (_handle_bigquery 1452, 1718, 1877, 1914)
  → run_sql_agent(1488, 1499) / run_sql_agent_stream(584, 815) / run_sql_agent_unlimited(1616)
  → sql_agent state["can_view_fi"]
  → sql_tool_agent.py:99 (BQ_TOOL_LOOP=1 경로도 동일 처리)
```

**기본값은 `False` (fail-closed)**. 전달을 빠뜨린 코드 경로가 있으면 "권한 없음"으로
눈에 띄게 실패하지, 조용히 열리지 않는다.
`harness_api.py` / `eval_api.py` / `scripts/_meta_sql_test_50.py` 등 내부 경로는
명시적으로 `can_view_fi=True` 를 넘겨 기존 동작을 유지한다 (구현 시 각각 확인).

---

## 4. Admin UI

기존 "AD 사용자" 탭을 확장한다 (새 탭 신설 없음).

- `app/api/admin_group_api.py:227` `list_ad_users` — SELECT 에 `a.can_view_fi`,
  그리고 가입 여부 표시를 위해 `LEFT JOIN users u ON u.ad_user_id = a.id` 로 `u.id AS user_id` 추가
- 신규 `PUT /api/admin/ad/users/{ad_user_id}/fi` (admin 전용, body `{can_view_fi: bool}`)
  → `logger.info("admin_update_fi", target=..., by=admin.email)` 감사 로그
- `app/frontend/chat.js:4358-4377` 렌더 루프 — 행마다 손익 토글 체크박스 추가,
  미가입자는 "미가입" 뱃지를 함께 표시(권한은 부여 가능)
- 상단에 "손익 허용 N명" 카운터, 필터 셀렉트에 "손익 허용" 옵션 추가
- `chat.html` 의 `chat.js?v=` **증가 필수** (CLAUDE.md 캐시 규칙)

---

## 5. 프론트엔드 데이터소스 칩

`app/frontend/chat.js:370, 387, 2839` — `"손익"` 소스 칩을 `can_view_fi=false` 면 렌더에서 제외.
`/api/auth/me` 응답 기준. **UX 정리용이며 서버 게이트가 실제 경계다.**

---

## 6. 테스트

`tests/test_fi_permission.py` 신규:

1. `validate_sql(fi_sql, allowed_tables=<FI 제외 집합>)` → 차단
2. `validate_sql(fi_sql)` (기본값) → 통과 (하위 호환)
3. 프롬프트 마스킹 — 결과 문자열에 `FI_LLM_Flat` 없음 **AND** 테이블 13·15 섹션은 온전히 남음
   (헤더 포맷 변경 시 조용히 깨지는 것 방지)
4. 권한 없는 사용자의 "영업이익 알려줘" → LLM 호출 없이 안내 문구 (orchestrator 게이트, mock)
5. 권한 있는 사용자 → 기존 경로 그대로 동작

---

## 7. 배포

1. 로컬 dev(3001)에서 확인 — 권한 있는 계정/없는 계정 각각 손익 질문
2. `python scripts/deploy_new_server.py was` — **실제 서비스 반영**
3. 기동 시 `ensure_fi_permission_column()` 이 신규 서버 DB(10.1.200.5)에 컬럼 자동 생성
4. 신규 서버에서 `scripts/grant_fi_access.py` 실행해 8명 권한 부여
   (또는 로컬에서 신규 서버 DB 대상으로 실행)
5. `journalctl -u ai-craver -n 50 --no-pager` 로 기동/마이그레이션 로그 확인

리다이렉트 껍데기(172.16.1.250)는 이번 변경과 무관 — `pm2 restart` 불필요.

---

## 8. 확인 완료 / 남은 사항

- ✅ **동명이인** (2026-08-03 검증): 8명 전원 `ad_users` 에 존재하고 중복 없음.
  다만 매칭 키는 안전하게 `username` 으로 확정 (1-2 참조).
- ✅ **거부 문구** (2026-08-03 확정): 3-(a) 의 문구로 확정.
- **권한 없는 사용자의 기존 대화**: 이미 답변에 남아 있는 손익 수치는 소급 삭제하지 않는다
  (필요하면 별건으로 처리).
