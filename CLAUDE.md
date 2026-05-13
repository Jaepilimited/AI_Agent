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

## 배포 규칙 (최우선)

- **프로덕션 = GCP 서버 포트 3000 (skin1004-prod)**: 사용자가 사용 중. 직접 수정/reload/restart 절대 금지
- **개발 = 로컬 Windows 포트 3001 (skin1004-dev)**: 모든 코드 변경은 여기서 테스트
- **배포 흐름**: 코드 수정(로컬) → `git push jaepilimited master` → GCP에서 `git pull origin master` → `pm2 reload skin1004-prod`
- 프로덕션 반영은 반드시 주인님의 명시적 허락 후에만 실행
- GCP prod에는 `pm2 reload` 사용 (restart 아님 — 무중단 반영)
- 프로덕션 서버 kill, stop, delete 절대 금지

## 서버 관리

- **GCP 프로덕션** (SSH: `ssh -i C:/Users/DB_PC/.ssh/gcp_skin1004 skin1004@34.64.99.179`)
  - 프로덕션 reload: `pm2 reload skin1004-prod` (주인님 허락 후)
  - 로그: `pm2 logs skin1004-prod --lines 30 --nostream`
- **로컬 Windows 개발** (포트 3001)
  - 개발 restart: `pm2 restart skin1004-dev`
  - 로그: `pm2 logs skin1004-dev --lines 30 --nostream`
- 상태 확인: `pm2 status`

## BigQuery 데이터 규칙 (SQL 로직 기준)

- **매출** → `SALES_ALL_Backup.Sales1_R` (원화 환산, 항상 이 컬럼)
- **판매수량** → `SALES_ALL_Backup.Total_Qty` (일반 집계)
- **SKU 단위 수량** → `Product.Total_Qty` (개별 제품 단위 정밀 조회 시)
- `Product` 테이블은 `SALES_ALL_Backup`의 세트 제품(SET에 `+` 연결)을 개별 SKU로 분해한 테이블

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

## 캐시 버전

- CSS/JS 변경 시 `chat.html`의 `?v=` 번호 증가 필수
- 현재: style.css?v=139, chat.js?v=202

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
