# 셀라 Entra 전체 직원 조회 설정

셀라는 Entra 로그인과 권한 보존을 구현했습니다. 첫 로그인 계정 생성과 별도로,
아직 로그인하지 않은 직원까지 Entra 전체 명단을 주기적으로 가져오기 위한 설정입니다.

1. 셀라에서 사용하는 Entra 앱 등록에 Microsoft Graph **User.Read.All — Application** 권한을 부여하고 관리자 동의를 완료합니다.
2. WAS `10.1.150.5`와 APP `10.1.150.105`가 사내 프록시 `10.1.50.2:3128`을 통해 `login.microsoftonline.com:443`과 `graph.microsoft.com:443`에 접근할 수 있도록 허용합니다.
3. 설정 후 관리자 화면의 **Entra 동기화**를 실행하고 성공 인원·시각을 확인합니다.

2026-09-08 점검: 앱 토큰 발급은 성공했지만 WAS Graph 연결은 ProxyError,
DB_PC에서의 Graph 조회는 HTTP 403 `Authorization_RequestDenied`였습니다.
현재 Entra 전체 명단 동기화가 성공한 것으로 표시하지 않습니다.

조회 항목은 직원 ID·이름·업무 메일·로그인 UPN·부서·Member/Guest 구분·활성 상태입니다.
셀라의 손익·방문자·SK/DD 그룹 권한은 셀라에서 관리하므로 Graph 그룹 읽기 권한은 필요하지 않습니다.

근거: [Microsoft Graph 사용자 목록 조회 권한](https://learn.microsoft.com/en-us/graph/api/user-list?view=graph-rest-1.0).

이 문서는 요청안이며 IT팀에 발송하지 않았습니다.
