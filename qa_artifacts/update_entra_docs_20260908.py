from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "CLAUDE.md"
source = path.read_text(encoding="utf-8")
start = source.index("## Entra ID(OIDC) 로그인")
end = source.index("## 비밀번호 복구", start)
assert 1500 < end - start < 4000
section = """## Entra ID 로그인과 셀라 사용자 관리 (2026-09-08)

- **로그인은 Entra ID만 사용한다.** `password_login_enabled=False`가 기본이다.
  기존 비밀번호 로그인·회원가입·재설정 API는 차단하며, 아래 비밀번호 복구 설명은
  롤백용 과거 구현에 대한 기록이다.
- **미가입 직원도 첫 로그인 시 계정이 생성된다.** 회사 tenant·서명·issuer·audience·
  만료·oid·nonce를 검증하고, 게스트·신원 충돌·비활성 계정을 거절한다.
- **권한의 원본은 `directory_users`다.** 기존 `ad_users`의 ID·손익·방문자 권한과
  SK/DD 그룹을 한 번 복사하고, `users.ad_user_id`·`user_groups.ad_user_id`의 외래 키를
  새 표로 연결한다. 컬럼명은 호환성을 위해 유지한다. `users.id`도 보존한다.
- **연결은 tenant+oid가 우선이다.** 최초 연결만 기존 회사 명단의 유일한 계정 로컬파트로
  찾고, 이미 다른 oid에 연결되었거나 후보가 여럿이면 중단한다. 로그인 UPN의 도메인이
  바뀌어도 기존 업무 메일 주소를 덮어써 Google 연결을 끊지 않는다.
- **미가입자에게도 관리자에서 손익·방문자·그룹을 미리 배정할 수 있다.** 첫 로그인은
  그 직원의 기록을 재사용하며 권한을 초기화하거나 관리자 권한을 자동 부여하지 않는다.
- **새 로그인 계정은 데이터 그룹 배정 후 질문·보고서를 이용한다.** 기존 계정의 접근은
  유지한다. `requires_group_assignment`는 신규 계정에만 1로 저장하며, 실제 접근은
  현재 SK/DD 등 브랜드 필터 그룹의 존재로 판단한다. 회수 후 전체 브랜드가 열리지 않는다.
- **전체 Entra 명단 조회에는 별도 Graph 권한·통신이 필요하다.** 9/8 실측으로 WAS 프록시
  통신이 막혔고, DB_PC에서 Graph 조회도 `403 Authorization_RequestDenied`였다.
  `User.Read.All` Application 권한과 관리자 동의, WAS/APP의 Graph HTTPS 허용이 필요하다.
  첫 로그인 계정 생성은 이 일괄 조회와 독립적으로 동작한다.
- **관리자 API는 `/api/admin/directory/*`다.** 화면은 셀라 사용자·Entra 동기화로 표시한다.
  동기화 실패 시 기존 명단·권한을 유지하고 실패 원인을 표시한다.
- **`ad_users`는 롤백 보관본이다.** 마이그레이션 완료 이후 권한 판단이나 정기 동기화에
  사용하지 않는다. 재시작 시 다시 복사하지 않으므로 회수한 권한이 되살아나지 않는다.
- **주요 파일**: `app/core/user_directory.py`, `user_directory_migration.py`,
  `app/core/entra_auth.py`, `app/api/entra_routes.py`, `app/api/admin_group_api.py`.
- **회신 URL은 설정값을 사용한다.** Host 헤더로 만들지 않으며 HTTPS가 필요하다
  (localhost 예외). state·nonce·PKCE verifier는 서버에서 일회성으로 관리한다.

"""
source = source[:start] + section + source[end:]
start = source.index("## AD 동기화 규칙")
old = source[start:]
assert 700 < len(old) < 2500 and old.rstrip().endswith("```")
source = source[:start] + """## Entra 사용자 동기화 규칙 (2026-09-08)

- **스크립트**: `scripts/sync_entra_users.py`. APP 서버 크론에서 매일 22:00 실행한다.
  `scripts/sync_ad_users.py` 실행도 새 스크립트로 연결하는 호환 진입점이다.
- 전체 Graph 페이지를 정상 조회한 뒤에만 트랜잭션으로 명단을 반영한다. 부분 조회·권한 오류·
  통신 실패 때는 직원 비활성화나 권한 덮어쓰기를 하지 않는다.
- 이름·메일·부서·Entra 활성 상태만 갱신하며 손익·방문자·그룹 권한은 관리자의 설정을 유지한다.
- **모니터링**: `directory_sync_state`와 `entra_directory_sync` 작업 이력.
  Graph 권한·네트워크 미설정 상태를 성공이나 최신 상태로 표시하지 않는다.
- **서버 실행 환경**: 앱 `.env`와 사내 HTTP/HTTPS 프록시 환경을 함께 제공한다.
- `python scripts/sync_entra_users.py --dry-run`은 마이그레이션 상태만 읽으며 DB를 수정하지 않는다.
- 회사 AD 계정 자체를 삭제하지 않는다. 셀라의 AD 보관 표 삭제 여부는 이관 검증 후 결정한다.
"""
source = source.replace("권한은 `ad_users.can_view_fi`", "권한은 `directory_users.can_view_fi`")
path.write_text(source, encoding="utf-8", newline="\n")
print("Updated Entra documentation")
