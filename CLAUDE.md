# SKIN1004 AI Agent — 개발 규칙

## 🧠 Knowledge Map (먼저 읽기 — 필수)

**모든 작업 전에 다음 순서를 지켜라**:

1. **먼저** `knowledge_map/GRAPH_REPORT.md`를 읽는다. 한 페이지에 프로젝트 전체 구조·중심 노드·최근 변경이 요약돼 있다.
2. 필요하면 `knowledge_map/graph.json`을 읽어 관련 노드 2~3개만 골라낸다 (id, cluster, wiki_page 필드).
3. 골라낸 노드의 `wiki_page` 경로(`knowledge_map/wiki/**.md`)만 Read한다.
4. **그래도 부족할 때만** 원본 파일(`app/**`, `docs/**`)을 Read하거나 Grep한다.

**금지 행동**:
- GRAPH_REPORT.md를 건너뛰고 바로 Grep/Glob하지 마라. 토큰 낭비다.
- `knowledge_map/` 디렉토리를 무시하지 마라. 매일 03:00 자동 업데이트되는 신뢰 가능한 소스다.
- 지도가 낡았다고 판단되면 `python scripts/build_knowledge_graph.py --force` 실행을 제안하라.

**지도가 커버하지 못하는 영역**:
- `tests/`, `scripts/` 일회성 파일, `backup_*`, `logs/`, `temp_*`, `app/frontend/`, `app/static/` — 이들은 지도에 없다. 필요시 직접 탐색.

## 배포 규칙 (최우선) — 2026-07-30 신규 서버 이관 완료

### ⚠️ 프로덕션은 더 이상 172.16.1.250 이 아니다

- **프로덕션 = `http://10.1.100.5` (사내 신규 서버, IT팀 관리 VM)**
  - Web `10.1.100.5` (nginx) → WAS `10.1.150.5` (앱, systemd `ai-craver`)
  - APP `10.1.150.105` (배치 크론) / DB `10.1.200.5` (MariaDB 10.11, DB명 `ai`)
- **172.16.1.250:3000 은 리다이렉트 껍데기다.** 앱은 떠 있지만 모든 요청을 307로
  신규 서버에 넘기고 배치 스케줄러도 꺼져 있다. **여기에만 배포하면 아무것도 반영되지 않는다.**

### 배포 흐름 (두 곳 모두 반영 — 컷오버 후 병행 기간)

```
python scripts/deploy_new_server.py was    # ★ 실제 서비스. 코드 전송 + systemd 재기동 + 헬스체크
pm2 restart skin1004-prod                  # 리다이렉트 껍데기(172.16.1.250). 리다이렉트 로직 바꿀 때만
```
- `CRAVER_SSH_PW` 환경변수 필요 (계정 `jeffrey`, 비밀번호는 노션 "AI Craver" 페이지)
- 신규 서버는 git 저장소가 아니라 **SFTP 전송본**이다. git pull 로 갱신되지 않는다.
- **패키지(requirements) 변경 시**엔 휠을 다시 받아 올려야 한다 (런북 참조)
- 배치 크론을 바꿨으면 `deploy_new_server.py app` 도 실행

### 상태 확인·롤백

- 전체 점검: `python scripts/verify_migration.py` (SSH·프록시·DB·서비스 한 번에)
- 신규 서버 로그: `journalctl -u ai-craver -n 50 --no-pager` (SSH 접속 후)
- **롤백**: 172.16.1.250 의 `.env` 에서 `MIGRATED_REDIRECT_URL` 줄 삭제 → `pm2 restart skin1004-prod`
  → 기존 서버가 즉시 원래대로 서비스하고 스케줄러도 자동 재개
- 172.16.1.250 은 롤백 대비로 유지 중. **kill / stop / delete 절대 금지**
- `git push jaepilimited master` 는 코드 백업용으로 유지

### 이관 상세

`docs/MIGRATION_AI_CRAVER.md` 가 단일 소스 — 검증 결과·오프라인 구축 절차·컷오버·롤백 전부 여기 있다.

### 아직 172.16.1.250 에 남아 있는 것

- **CRM 서비스** (`:3100`) — 전용 서버로 별도 이관 예정. 이것 때문에 DB_PC 를 끌 수 없다.
- `SKIN1004-Watchdog`, `SKIN1004-PM2-AutoStart`, `SKIN1004-Git-Push-Daily` (예약 작업)
- AD 동기화·지식맵 예약 작업은 APP 서버 크론으로 이관했고 **DB_PC 쪽은 비활성화**했다
  (중복 실행 방지). 롤백 시 `schtasks /change /tn SKIN1004-AD-Sync-Daily /enable` 로 되살릴 것.
- **GCP(34.64.99.179)** 는 2026-06-12부로 AI Agent 서비스 중지 — CRM 만 운영 중

## 서버 관리

- **신규 프로덕션 (실제 서비스)**
  - SSH: `jeffrey@10.1.100.5` / `10.1.150.5` / `10.1.150.105` (172.16.1.250 에서만 접속 허용)
  - 앱 재기동: `sudo systemctl restart ai-craver` (WAS)
  - 로그: `journalctl -u ai-craver -n 50 --no-pager`
  - nginx: `sudo nginx -t && sudo systemctl reload nginx` (Web)
  - DB 접속: WAS 경유 터널링만 가능 (DB 직접 접근 차단)
- **기존 서버 172.16.1.250 (리다이렉트 껍데기 + CRM)**
  - 반영: `pm2 restart skin1004-prod` / 로그: `pm2 logs skin1004-prod --lines 30 --nostream`
  - ⚠️ Windows fork 모드에서 `pm2 reload` 는 고아 프로세스를 만든다(2026-07-06 장애) — 반드시 `restart`
  - `pm2 status` 의 ↺ 가 수십 회 이상이면 포트 점유 고아 의심 (delete → 포트 킬 → start)
  - 서버 켜기(재부팅 후): `pm2 start ecosystem.windows.config.js`
- **로컬 개발** (포트 3001): `pm2 restart skin1004-dev`
- GCP SSH (CRM 확인용): `ssh -i C:/Users/DB_PC/.ssh/gcp_skin1004 skin1004@34.64.99.179`

## BigQuery 데이터 규칙 (SQL 로직 기준)

- **매출** → `SALES_ALL_Backup.Sales1_R` (원화 환산, 항상 이 컬럼)
- **판매수량** → **무조건 `Product.Total_Qty`** (2026-07-15 확정 — SALES_ALL_Backup.Total_Qty는 세트를 1개로 세어 부정확, 수량 답변에 사용 절대 금지. 실제 오답 사고로 규칙 강화)
- **매출+수량 동시 질문** → 매출은 SALES_ALL_Backup, 수량은 Product 서브쿼리로 각각 조회
- `Product` 테이블은 `SALES_ALL_Backup`의 세트 제품(SET에 `+` 연결)을 개별 SKU로 분해한 테이블 (필터 컬럼은 SALES_ALL과 동일: Country, Date, Brand, Mall_Classification 등)
- **신제품 정의 (2026-07-27 확정)** → **첫 판매일(데이터 최초 등장일)로부터 6개월 이하**인 제품
  - SQL 구현: 제품별 `MIN(Date)`로 첫 판매일을 구하고, 기준 시점(보통 오늘)으로부터 6개월 이내인 제품만 필터 — 예: `HAVING MIN(Date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH)`
  - 첫 판매일 판정은 세트 분해된 `Product` 테이블 기준 (SKU 단위 정확성)
- **브랜드 = 스킨천사 · 우마(UM) · 좀비뷰티 · 커먼랩스** (2026-08-05 확정)
  - **주력은 스킨천사·UM**, 좀비뷰티·커먼랩스는 아주 소량

    | 브랜드 | 조회 조건 | 규모(2025~) |
    |---|---|---|
    | 스킨천사 | `Brand IN ('SK','CBT') AND (Line!='ZB' OR Line IS NULL)` | 약 11,112억 |
    | 우마 | `Brand='UM'` | 3,252억 |
    | 좀비뷰티 | `Brand='SK' AND Line='ZB'` | 18.7억 |
    | 커먼랩스 | `Brand='CL'` | 2.9억 |

  - ⛔ **`Brand='CBT'`(659.0억)는 스킨천사 매출이다** (2026-08-05 확정). `Brand` 컬럼에 팀 값이
    잘못 들어간 것 — `Team_NEW` 의 CBT 와 건수·금액이 완전히 일치한다 (1,919,601건).
    브랜드별 집계 시 **SK 와 합산**할 것. 별도 행으로 내면 팀이 브랜드 표에 섞인다
  - **좀비뷰티는 `Brand` 에 없다** — `Brand='SK'` 안에 `Line='ZB'` 로 들어 있어, 브랜드별로 나눌 때
    스킨천사에서 빼지 않으면 이중 계상된다
  - **CBT·JBT·KBT·EAST·WEST·DT·B2B 는 팀** → `Team_NEW` 로 답할 것. 브랜드로 나열 금지
  - ⚠️ `Product` 테이블에만 있는 `DD`(356건)는 정체 미확인
- **제품 전성분은 사내 스프레드시트에 있다** (BigQuery 에는 없다 — 2026-08-06 구축)
  - 출처: 시트 `11gX_Gg7...` → 탭 `01. 제품정보_내수통합용(품목기준)`, 7행부터.
    **AG=전성분(KR, 2025-07 이후 최신) / AH=전성분(EN)**, G열이 BigQuery 매칭 키.
    AC~AF 는 구버전이니 쓰지 말 것
  - 적재: `app/core/ingredients.py` → `product_ingredients` / `product_ingredient_map`.
    매일 04:00 `ingredient_sync_daily` 자동 갱신 (자가 점검 EXPECTED_JOBS 등록됨).
    수동은 `python scripts/sync_product_ingredients.py [--dry-run]`
  - 커버리지: BigQuery 243종 중 **115종 매칭**(종수 47%) — **판매수량 기준 89.7%**.
    미매칭은 대부분 Sachet·기획세트
  - ⛔ **"성분 미상"과 "성분 미포함"을 절대 섞지 마라.** 시트에 없는 제품은 성분을 *모르는* 것이지
    *안 들어간* 것이 아니다. 뭉개면 원래 오답(제품명 `LIKE '%RETINOL%'` 매칭으로 나이아신아마이드가
    든 제품이 "미포함 1위")이 그대로 재현된다. 미매칭은 결과에서 빼고 커버리지를 답변에 명시한다
  - ⛔ **LLM 에 성분 SQL 을 맡기지 마라** — 다시 제품명 문자열 매칭으로 흘러간다.
    `orchestrator._handle_ingredient_query()` 가 제품 목록을 먼저 확정하고 그 목록으로만 집계한다
  - 검증법: 같은 성분의 포함/미포함 목록 **교집합이 0** 이어야 한다
  - 개별 제품 전성분 설명은 `@@BP`(제품 Q&A) 경로가 계속 담당한다
- **대륙 = `Continent1` 기본** (2026-08-04 오답 사고로 규칙화)
  - `Continent2` 에는 **'유럽'·'아시아'·'동유럽' 값이 아예 없다.** 광역 대륙을 거기서 찾으면 0건이 난다
  - 광역(유럽/아시아/북미/남미/중미/중동/아프리카/오세아니아/CIS) → `Continent1`
  - 세부 권역(서유럽/북유럽/동남유럽/동남아시아/동아시아/서남아시아/북아프리카/남아메리카/중앙아메리카) → `Continent2`
  - 예외는 "동남아"뿐 — `Continent1` 에 없어서 `Continent2 = '동남아시아'` 를 쓴다
- **`GROUP BY` 없는 집계(`SUM`/`COUNT`)는 0건이어도 NULL 한 행을 돌려준다.** 빈 결과 판정을
  `if not results:` 로만 하면 이 케이스를 놓쳐 LLM이 원인을 지어낸다. 전 컬럼 NULL 단일 행은
  빈 결과로 정규화할 것 (`format_answer` 에 구현됨)

## 자가 점검 (self-check) — 2026-08-04

- **파일**: `app/core/self_check.py` / **실행**: 매일 07:30 (APScheduler `self_check_daily`)
- **화면**: Admin > `자가 점검` 탭 (즉시 실행·추세 확인)
- **API**: `GET /api/admin/self-check`, `POST /api/admin/self-check/run`, `GET /api/admin/self-check/trend/{id}`
- **왜 만들었나**: AD 동기화가 **6일간 매일 밤 실패**했는데 아무도 몰랐다. 크론은 돌았고
  로그도 남았지만 읽는 사람이 없었다. `quality_monitor` 는 답변 품질만 본다 — 배치가 죽었는지,
  데이터가 썩었는지, 권한이 뚫렸는지는 아무도 감시하지 않았다.
- **검사 12종**: batch(신선도 3) / integrity(고아·이메일·인코딩 4) / permission(FI 방어선·인원·admin 3) / datasource(BQ·Qdrant 2)
- **원칙 — 새 검사 추가 시 지킬 것**:
  - 검사(`fn`)는 **부작용이 없어야** 한다. 고치는 것은 `repair` 로 분리
  - 자가치유는 **되돌릴 수 있는 것만**. DB 스키마 변경·삭제는 절대 자동화 금지
  - 활성 AD 계정의 `display_name` 은 치유 대상이 아니다 (다음 sync 가 덮어씀)
  - 치유 후 **재검사해서 정말 나았을 때만** 성공으로 기록
  - 알림은 **상태가 바뀐 것만** (정상→실패, 실패→정상). 매일 같은 알림은 곧 무시당한다
- ⚠️ `LIKE '%...%'` 를 파라미터 없이 쓰지 마라 — pymysql 이 `%` 를 포맷 지시자로 읽어 터진다.
  백슬래시 매칭은 `LOCATE(CONCAT(CHAR(92),'u'), col)` 처럼 회피할 것

### 배치 건강성은 부수효과가 아니라 실행 기록으로 판정한다

- **모든 스케줄 잡은 `track_job("<job_id>")` 으로 감싼다.** 실행 자체가 `job_runs` 에 남아야
  "할 일이 없어서 안 돈 것"과 "죽어서 못 돈 것"이 구분된다. 테이블에 행이 늘었는지로
  판정하면 한산한 밤을 고장으로 오탐한다 (2026-08-05 실제 발생)
- 새 잡을 추가하면 `EXPECTED_JOBS` 에 `(허용시간, 라벨)` 을 등록한다 — 그것만으로 감시 대상이 된다

### 서버별 프록시 허용 범위가 다르다 (2026-08-05 실측)

| | Jandi | Gemini/BigQuery/Claude/Notion |
|---|---|---|
| **WAS** 10.1.150.5 | ❌ 403 | ✅ |
| **APP** 10.1.150.105 | ✅ | ❌ |

- **잔디 알림은 WAS 에서 못 보낸다.** 자가 점검이 WAS 에서 돌므로 알림이 나가지 않는다
  → `alert_channel` 검사가 이 상태를 잡는다. IT 에 `wh.jandi.com` 오픈 요청 필요
- **지식맵 빌드는 Gemini 를 호출한다** (`app/knowledge_map/semantic.py`). APP 크론에 있는데
  APP 은 Gemini 가 막혀 있어 돌 수 없다. 크론 주석의 "외부 호출 없음" 기재는 **오류**다
  → IT 에 오픈 요청하거나 WAS 스케줄러로 옮겨야 한다
- ⚠️ `deploy_new_server.py` 의 `EXCLUDE_DIRS` 는 **디렉토리 이름**으로 거른다. 이름이 겹치는
  소스 패키지가 통째로 빠지므로 최상위 산출물은 `EXCLUDE_PATHS` 에 쓸 것
  (`knowledge_map` 을 이름으로 걸러 `app/knowledge_map/` 이 배포에서 누락됐던 사고)

## 코드 규칙 — 재발 방지

- ⛔ **`ThreadPoolExecutor` 를 `with` 블록으로 감싸고 `future.result(timeout=N)` 을 쓰지 마라.**
  블록을 빠져나갈 때 `shutdown(wait=True)` 가 걸려 **타임아웃이 무의미해진다** —
  워커가 끝날 때까지 그대로 기다린다. 실제로 "차트 8초 넘으면 건너뛴다"는 로직이
  통째로 무력화돼 있었다 (2026-08-04 발견).
  ```python
  pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
  try:
      answer = pool.submit(fn).result(timeout=8.0)
  finally:
      pool.shutdown(wait=False)   # ← 반드시 wait=False
  ```
  스트리밍 경로(`run_sql_agent_stream` 등)는 원래 이 방식이다. 새 코드도 여기 맞출 것.
- **차트 축 라벨은 전치(transpose) 여부에 따라 의미가 바뀐다.** 전치하면 x축은 기간이 되므로
  원래 x축 제목(예: '국가')을 그대로 두면 축이 거짓말을 한다. 컬럼명을 눈금으로 쓸 때는
  공통 접두사뿐 아니라 **접미사도** 제거할 것 (`Q1 sales`/`Q2 sales` → `Q1`/`Q2`)

## 재무 손익(FI) 열람 권한 — 2026-08-04

- **테이블**: `skin1004-319714.Sales_Integration.FI_LLM_Flat` (월별 연결 손익, `Record_Type='PL'`)
- **보유 기간은 2026-01 ~ 2026-06 뿐이다.** 범위 밖 질문엔 빈 결과 대신 보유 기간을 먼저 안내한다.
- **승인된 사람만 조회 가능**. 권한은 `ad_users.can_view_fi` (users 가 **아니다**)
  - 미가입자에게도 미리 부여해둘 수 있고, 가입하면 자동 적용된다
  - `user_groups` 도 `ad_user_id` 를 키로 쓰므로 일관됨
  - admin 은 플래그와 무관하게 항상 허용
- **관리**: Admin > AD 사용자 탭의 `손익` 체크박스 (`PUT /api/admin/ad/users/{id}/fi`)
  - 일괄 부여/회수 스크립트: `python scripts/grant_fi_access.py [--dry-run|--revoke]`
- **권한 판정은 반드시 서버에서 DB 조회로** 한다. JWT·프론트 값은 stale 위험이 있어 신뢰하지 않는다
- **방어선을 줄이지 말 것** (하나가 뚫려도 나머지가 막는 구조):
  1. `orchestrator._requests_fi_data()` — 손익 키워드/소스 감지 시 LLM 호출 전 거절
  2. `sql_agent._load_prompt(can_view_fi=False)` — 프롬프트에서 FI 스키마 섹션 제거
  3. `_allowed_tables_from_sources()` — 테이블 화이트리스트에서 FI 제외
  4. `security.validate_sql()` — 실행 직전 FI 참조 재검사 (대소문자 무관)
  5. 프론트 — 손익 데이터소스 칩 숨김
- 컬럼은 `ensure_fi_permission_column()` 이 앱 기동 시 자동 추가 (idempotent)

## 노션 데이터 규칙

- 사용자가 **노션을 명시적으로 언급하지 않는 한** 노션 데이터를 답변에 포함하지 않음
- 노션 데이터는 **노션 트리 기능**(채팅, System Status, @@ 데이터소스 선택)에서만 활용
- BigQuery 질문에 노션 데이터를 섞지 않을 것

## 메가와리 기간 (큐텐 Qoo10 전용)

- **2023년**: Q1(3/1~3/12), Q2(6/1~6/12), Q3(9/1~9/12), Q4(11/22~12/3)
- **2024년**: Q1(3/1~3/12), Q2(6/1~6/12), Q3(8/31~9/12), Q4(11/15~11/27)
- **2025년**: Q1(2/28~3/12), Q2(5/31~6/12), Q3(8/31~9/12), Q4(11/21~12/3)
- **2026년**: Q1(2/27~3/11)
- 메가와리 질문 시 `Mall_Classification LIKE '%Q10%'` 필터 필수

## 오케스트레이터 컨텍스트 규칙 (2026-06-01)

- **파일**: `app/agents/orchestrator.py`
- **시스템 프롬프트 컨텍스트 원칙**:
  - 사용자가 "아까", "그거", "방금", "다시" 등으로 이전 답변을 참조하면 → 반드시 컨텍스트 활용
  - 질문 자체가 이전 대화와 완전히 무관한 경우 → 컨텍스트 무시해도 됨
  - "이전 맥락을 무조건 무시하라"는 지시는 금지 (참조형 질문까지 끊김)
- **`_clean_messages_for_history()`**: `generate_with_history_stream` 호출 전 assistant 메시지에서 차트 JSON·SQL `<details>` 블록 제거 (텍스트는 전체 보존, 1500자 제한 없음)
  - 직접 `messages`를 넘기지 말고 반드시 이 함수를 거칠 것
- **`_build_conversation_context()`**: BigQuery·Notion·CS 등 비-direct 라우트용 텍스트 요약
  - 최근 10개 메시지, assistant 1500자 제한, chart/SQL 노이즈 제거
  - direct 라우트에는 사용하지 않음 (full history 사용)

## 캐시 버전

- CSS/JS 변경 시 `chat.html`의 `?v=` 번호 증가 필수
- 현재: style.css?v=139, chat.js?v=216

## AD 동기화 규칙

- **스크립트**: `scripts/sync_ad_users.py`
- **자동 실행**: 매일 22:00 (Task Scheduler `SKIN1004-AD-Sync-Daily`)
- **2-step 파이프라인**:
  1. STEP 1 — AD → MariaDB upsert (362명, `_NAME_OVERRIDES` 적용)
  2. STEP 2 — 이름 자동 보정: `users.display_name`(한글)을 `ad_users.display_name`에 역반영
- **이름 오버라이드**: AD displayName이 영문인 미등록 사용자는 `_NAME_OVERRIDES` 딕셔너리에 추가
  - 이미 가입한 사람은 auto-heal이 자동 처리 — 오버라이드 추가 불필요
- **절대 금지**: `ad_users.display_name` DB 직접 수정 — 다음 sync에 덮어씌워짐
- **즉시 이름 반영**: `python scripts/sync_ad_users.py --heal-only`
- **사용법**:
  ```
  python scripts/sync_ad_users.py             # 전체 sync (매일 자동)
  python scripts/sync_ad_users.py --heal-only # 이름 보정만 즉시
  python scripts/sync_ad_users.py --dry-run   # 미리보기
  ```
