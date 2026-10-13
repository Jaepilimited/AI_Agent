/**
 * 발표에 쓰는 수치 — **전부 프로덕션 실측** (2026-08-18).
 *
 * ⛔ 여기에 어림수를 적지 마라. 발표에서 나온 숫자는 다음 주에 누군가 다시 묻는다.
 *    실측이 아닌 값은 아예 넣지 않는 편이 낫다.
 */

export const AS_OF = "2026. 08. 18";

/** 사용 현황 */
export const USAGE = {
  adAccounts: 492,      // AD 전체 계정
  signedUp: 61,         // 가입자
  conversations: 997,   // 누적 대화
  messages: 7153,       // 누적 메시지
  questionsTotal: 3459, // 누적 질문
  questionsAug: 629,    // 8월 질문
  usersAug: 21,         // 8월 사용자
  avgSeconds: 12.6,     // 평균 응답 시간
} as const;

/** 8월 경로별 질문 분포 (무엇을 물었나) */
export const ROUTES = [
  { label: "데이터 조회", key: "bigquery", n: 352, color: "#e89200" },
  { label: "일반 대화", key: "direct", n: 111, color: "#60a5fa" },
  { label: "제품 Q&A", key: "cs", n: 76, color: "#34d399" },
  { label: "사내 문서", key: "notion", n: 42, color: "#a78bfa" },
  { label: "메일·일정", key: "gws", n: 41, color: "#f87171" },
  { label: "복합", key: "multi", n: 7, color: "#8a9ab0" },
] as const;

/** 피드백 — 오늘 조사해서 드러난 것 */
export const FEEDBACK = {
  totalDown: 102,       // 누적 붐따
  withComment: 39,      // 코멘트가 달린 것
  augItems: 21,         // 8월 인입 (앱 19 + 노션 2)
  augUsers: 5,          // 8월 제보자
  repliesBefore: 0,     // 회신 (구축 전)
  handled: 37,          // 처리 완료·보류
  repliesPending: 13,   // 제보자에게 전달 대기
  densityDenom: 29,     // 질문 N건당 피드백 1건
} as const;

/** 오늘 고친 오답 — 재현·검증까지 끝난 것만 */
export const FIXES = [
  {
    title: "국가별 매출에서 우마가 빠졌다",
    before: "미국 86.6억",
    after: "219.3억",
    why: "묻지 않은 브랜드 필터가 자동으로 붙었다",
  },
  {
    title: "국내몰 리뷰를 스마트스토어만 셌다",
    before: "4,140건",
    after: "42,427건",
    why: "리뷰 테이블이 통합됐는데 앱이 몰랐다",
  },
  {
    title: "메가와리 2분기 기간이 없어 날짜를 지어냈다",
    before: "40.2억",
    after: "62.2억",
    why: "기간표에 2026 Q2 가 등록되지 않았다",
  },
  {
    title: "플래그십 매장 리뷰를 조회하지 못했다",
    before: "0건",
    after: "95건",
    why: "매장명 컬럼의 뜻을 알 방법이 없었다",
  },
] as const;

/** 신뢰 장치 — 매일 자동으로 도는 것 */
export const GUARDS = [
  { label: "자가 점검", value: "25종", note: "매일 07:30 · 최근 22/22 통과" },
  { label: "골든셋 회귀", value: "37문항", note: "매일 05:30 · 37/37 통과" },
  { label: "코드 테스트", value: "598건", note: "배포 전 전량 실행" },
  { label: "스키마 감시", value: "12,221컬럼", note: "데이터가 바뀌면 다음 날 알림" },
] as const;

/** 오늘 새로 붙인 자동화 */
export const AUTOMATION = [
  { time: "03:40", label: "정의서 → 컬럼 설명", detail: "731개 컬럼의 뜻이 자동 반영" },
  { time: "03:50", label: "값 목록 실측 갱신", detail: "국가·대륙·팀 등 10종" },
  { time: "07:30", label: "스키마 변화 감지", detail: "테이블·컬럼이 바뀌면 보고" },
  { time: "08:00", label: "피드백 다이제스트", detail: "새 제보를 읽어 처리 대상으로" },
] as const;

/** 다음 단계 */
export const NEXT = [
  "제보에 답이 돌아가는 것을 팀이 체감하게 — 회신 13건이 대기 중입니다",
  "데이터 정의서를 채우면 매출·수량 컬럼도 자동 반영됩니다",
  "질문 32건당 1건인 피드백 밀도를 올리는 것이 품질의 상한입니다",
] as const;
