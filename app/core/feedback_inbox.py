# -*- coding: utf-8 -*-
"""붐따(👎) 처리함 — **읽히지 않던 피드백을 처리 대상으로 올린다.**

⛔ 2026-08-14 실측으로 드러난 구멍이다. 붐따는 이렇게 끝나고 있었다:

      사용자가 👎 + 코멘트 작성 → DB 저장 ✅
        → 개수만 집계 (급증하면 알림)
        → **내용은 아무도 읽지 않음** ❌

   `comment` 컬럼을 읽는 코드가 앱 전체에 하나도 없었다. 코멘트 39건이 넉 달간
   쌓여 있었고, 그중 "구글 워크스페이스쪽은 동작하지 않고 있어요"(08-05)는
   **9일 뒤** 같은 증상을 개발자가 직접 겪고서야 고쳐졌다. 제보가 닿는 경로가
   없었다.

   "매일 개선하는 시스템"(`SKIN1004-Nightly-Debug`)이 있긴 했지만 그건 **서버
   로그의 에러**를 봤지 붐따를 본 적이 없다. 게다가 7/09부터 멈춰 있었고
   `EXPECTED_JOBS` 에 없어서 그 침묵조차 감시되지 않았다.

설계 원칙 (이 프로젝트의 다른 배치와 같다):
  - **처리 상태를 기록할 곳을 만든다.** 없으면 "처리했다"를 남길 수 없어
    "인입은 됐는데 처리가 안 된 건지" 를 영영 답할 수 없다
  - 알림은 **새로 들어온 것만**. 미처리 총량을 매일 보내면 곧 무시당한다
    (자가 점검이 "상태가 바뀐 것만" 알리는 것과 같은 이유)
  - 판정·집계는 규칙이 한다. LLM 은 쓰지 않는다
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

import structlog

from app.db.mariadb import execute, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

# 처리 상태 — 노션 AI Tester 공간이 쓰는 어휘(미해결/해결완료)에 맞춘다
STATUS_NEW = "new"          # 아직 아무도 안 봄
STATUS_ACK = "ack"          # 봤고 처리 대상으로 인정
STATUS_DONE = "done"        # 고쳤음
STATUS_WONTFIX = "wontfix"  # 고치지 않기로 함 (사양·오입력·의미 없는 내용)
_STATUSES = (STATUS_NEW, STATUS_ACK, STATUS_DONE, STATUS_WONTFIX)


def _handled_note_has_encoding_loss(note: Optional[str]) -> bool:
    """Detect a note whose non-ASCII text was replaced by question marks.

    Windows PowerShell 5 uses ``us-ascii`` for native-process pipelines unless
    explicitly changed.  In #151 that converted most Korean characters to
    literal ``?`` bytes before Python and MariaDB ever received the note.
    """
    compact = "".join(ch for ch in (note or "") if not ch.isspace())
    question_marks = compact.count("?")
    return bool(compact) and question_marks >= 8 and question_marks / len(compact) >= 0.25

# 배포된 수정과 피드백 상태를 함께 움직이는 단일 목록.
#
# 코드가 고쳐져도 message_feedback.status 는 저절로 바뀌지 않아 이미 해결된 붐따가
# 계속 미처리로 보였다. 해결한 변경에는 이 목록의 (id, 신고일, 메모)를 함께 넣고,
# 앱 기동 시 날짜까지 일치하는 행만 done 으로 동기화한다. 날짜 검증은 다른 DB에서
# 같은 숫자 id 를 가진 피드백을 잘못 닫는 일을 막는다.
#
# 2026-08-26 사용자 확인: 당시 미종결 중 #152 만 실제 미해결이었다.
# #152는 조직 담당국가 결정적 응답을 프로덕션에서 검증한 뒤 아래에 별도로 닫는다.
_RESOLVED_2026_08_26 = (
    (34, "2026-05-11"), (35, "2026-05-11"),
    (36, "2026-05-12"), (37, "2026-05-12"), (38, "2026-05-12"),
    (39, "2026-05-12"), (40, "2026-05-12"), (42, "2026-05-12"),
    (43, "2026-05-12"), (44, "2026-05-12"),
    (45, "2026-05-13"), (46, "2026-05-13"), (47, "2026-05-13"),
    (48, "2026-05-13"),
    (55, "2026-06-04"), (57, "2026-06-08"),
    (66, "2026-04-13"), (68, "2026-04-15"), (69, "2026-04-15"),
    (70, "2026-04-16"), (75, "2026-04-17"), (76, "2026-04-22"),
    (77, "2026-04-27"), (81, "2026-04-27"), (82, "2026-04-27"),
    (83, "2026-04-28"), (85, "2026-04-30"), (86, "2026-04-30"),
    (88, "2026-05-04"), (89, "2026-05-04"), (90, "2026-05-04"),
    (105, "2026-07-08"), (110, "2026-07-23"),
    (112, "2026-07-27"), (113, "2026-07-27"), (115, "2026-07-28"),
    (136, "2026-08-13"), (150, "2026-08-25"),
)
_RESOLUTION_NOTE_2026_08_26 = (
    "사용자 확인(2026-08-26): 해결 완료. "
    "배포된 해결 목록과 피드백 상태를 자동 동기화했습니다."
)
DEPLOYED_FEEDBACK_RESOLUTIONS = tuple(
    {"id": feedback_id, "created_on": created_on, "note": _RESOLUTION_NOTE_2026_08_26}
    for feedback_id, created_on in _RESOLVED_2026_08_26
) + (
    {
        "id": 173,
        "created_on": "2026-09-09",
        "note": (
            "수정 완료(2026-09-09): 「@@수상으로 물어봤는데 그냥 원문만 보여줌」 하신 "
            "그 건입니다. 확인해 보니 질문의 낱말이 조건으로 하나도 쓰이지 못해 "
            "**매번 같은 기본 목록 40행**이 나가고 있었습니다. 원인은 세 가지였습니다. "
            "① 「쇼피에서」처럼 조사가 붙으면 자료에 없는 말이 되어 통째로 버려졌습니다 "
            "(「쇼피」로 찾으면 45건입니다). ② 「2026년」은 자료에 그런 글자로 없습니다 "
            "— 날짜가 「2026-01-15」로 들어 있어서 연도가 통째로 무시됐고, 그래서 "
            "「2026년만」이라고 하셔도 2017~2026년이 다 나왔습니다. ③ 「랭크되있는거」 "
            "같은 말투는 자료에 없어 구분(수상/랭킹/설문)이 사라졌습니다. "
            "이제 조사를 떼고 찾고, 연도와 구분은 별도 조건으로 걸립니다 — "
            "「2026년만 랭크되있는거」는 2건, 「쇼피에서 받은게」는 45건으로 나옵니다. "
            "그리고 그래도 좁히지 못하면 **표 위에 먼저** 「전체 목록이라 질문에 대한 "
            "답이 아니다」라고 밝히고, 어떻게 물으면 좁혀지는지 함께 안내합니다. "
            "세 번이나 같은 표를 받으시게 해 죄송하고, 알려 주셔서 감사합니다."
        ),
    },
    {
        "id": 172,
        "created_on": "2026-09-08",
        "note": (
            "수정 완료(2026-09-09): 9월 전체 등록액을 현재 누적으로 설명하고, "
            "데이터가 있는 날짜 16개를 경과 16일로 읽어 월말 예상액을 부풀린 문제를 고쳤습니다. "
            "이제 마감 질문은 기준일까지 누적매출·이후 날짜 등록분·월 전체 등록 합계를 "
            "구분해 보여줍니다. 미래 등록분을 포함한 금액을 다시 일할 확대하지 않으며, "
            "추가 매출·취소·일정 변경을 반영하지 않은 등록 합계를 최종 마감 예측으로 "
            "단정하지 않습니다. 미래 날짜 포함 안내도 실제 채팅 경로에 반영했습니다."
        ),
    },
    {
        "id": 152,
        "created_on": "2026-08-25",
        "note": (
            "수정 완료(2026-08-26): 동남아시아2팀 담당 국가를 "
            "말레이시아·싱가포르로 결정적 응답하도록 반영하고 프로덕션에서 검증했습니다."
        ),
    },
    {
        "id": 154,
        "created_on": "2026-08-27",
        "note": (
            "수정 완료(2026-08-27): 말씀하신 대로 사업자등록번호처럼 찾을 것이 정해진 "
            "질문이 문서 검색을 타면서 5~11초가 걸리고 있었습니다. 로그를 확인해 보니 "
            "여덟 분이 같은 것을 서로 다른 문장으로 물으셨고 매번 같은 지연이 있었습니다. "
            "이제 사업자등록번호·법인등록번호는 검색 없이 즉시 답합니다. "
            "값은 사내 노션 「회사 정보 Craver」에서 확인한 것을 쓰고 답변에 출처를 함께 "
            "표시합니다. 다만 사업자등록증 사본처럼 서류를 찾는 질문은 지금처럼 문서를 "
            "검색합니다. 좋은 지적 감사합니다."
        ),
    },
    # ⛔ 수량이 없는 것을 `0` 으로 적던 건 (2026-09-02 제보). 두 건이 같은 원인이라
    #    회신도 같다 — #157 은 코멘트가 없지만 대화가 남아 있어 진단이 됐다.
    {
        "id": 156,
        "created_on": "2026-09-02",
        "note": (
            "수정 완료(2026-09-03): 지적하신 대로였습니다. 우마(UM) 제품은 수량이 "
            "적재돼 있지 않은데, 답변이 그것을 「판매수량 0개」라고 적어 팔리지 않은 "
            "것처럼 보였습니다. 확인해 보니 2026년 UM 94,250행의 수량이 전부 0이고 "
            "수량 테이블에는 UM 행이 아예 없었습니다. 이제 이런 경우 0을 답으로 내지 "
            "않고 「집계할 수 없습니다 — 팔리지 않았다는 뜻이 아니라 수량을 모른다는 "
            "뜻입니다」라고 안내합니다. 매출 금액은 종전대로 정상입니다. "
            "덧붙여, 비교하신 물류 수량은 발주·출고 기준이라 판매 수량과는 다른 "
            "값입니다. 발주·출고 수량은 `@@물류` 로 물어보시면 됩니다. "
            "좋은 지적 감사합니다."
        ),
    },
    {
        "id": 157,
        "created_on": "2026-09-02",
        "note": (
            "수정 완료(2026-09-03): 같은 주문번호 조회에서 우마(UM) 수량이 「0개」로 "
            "나가던 문제를 고쳤습니다. 수량이 적재되지 않은 데이터는 0 대신 "
            "「집계할 수 없습니다」라고 안내합니다. 매출 금액은 정상입니다."
        ),
    },
    # ⛔ 표가 조회 결과 전체가 아닌데 "총 N건" 이라고 단정하던 건. 두 건이 같은 원인이다
    {
        "id": 158,
        "created_on": "2026-09-02",
        "note": (
            "수정 완료(2026-09-03): 「왜 5월만 보여줘?」라고 물어보신 그 건입니다. "
            "조회는 86건을 다 가져왔는데 답변 표에는 상위 15건만 실렸고, 잘렸다는 "
            "말이 어디에도 없었습니다. 이제 표가 전체가 아니면 답변 맨 위에 "
            "「실제로는 총 N행입니다 · 건수·합계·순위를 이 표만 보고 판단하지 "
            "마세요」라고 코드가 먼저 알려드립니다. 전체는 CSV로 받으실 수 "
            "있습니다. 지적해 주셔서 감사합니다."
        ),
    },
    {
        "id": 159,
        "created_on": "2026-09-03",
        "note": (
            "수정 완료(2026-09-03): 짚어 주신 대로 amount(유상)만 나가고 있었습니다. "
            "202606010105 로 확인하니 유상 7,555.72 + 무상 1,108.40 = 8,664.12 로 "
            "말씀하신 값과 정확히 같았습니다. 이제 금액은 "
            "IFNULL(total_amount, amount + free_amount) 로 조회합니다. "
            "제안해 주신 두 방법 중 하나만 쓰지 않고 둘을 합친 이유는, 실측해 보니 "
            "total_amount 만 쓰면 그 값이 비어 있는 1,220건(전체의 절반)의 금액이 "
            "통째로 사라지고, amount+free_amount 만 쓰면 total_amount 만 있는 35건이 "
            "빠지기 때문입니다. 둘 다 있는 행에서는 total_amount 를 씁니다. "
            "혹시 고쳐 쓰지 못한 경우에는 답변에 「유상분만입니다」라고 표시됩니다. "
            "정확한 제보 감사합니다."
        ),
    },
    {
        "id": 160,
        "created_on": "2026-09-03",
        "note": (
            "수정 완료(2026-09-03): 두 가지가 있었습니다. ① 표에는 8건만 실렸는데 "
            "실제 조회 결과는 16건이었습니다(영국 7건이 통째로 빠졌습니다). 이제 "
            "표가 전체가 아니면 답변 맨 위에 총 몇 행인지 먼저 알려드립니다. "
            "② CSV 링크가 눌러도 받아지지 않던 것은, 받은 결과를 메모리에만 "
            "두고 있어서 그 사이 서버가 재기동되면 링크가 죽었기 때문입니다. "
            "이제 디스크에 보관해 재기동을 견디고, 유효 시간도 1시간에서 24시간으로 "
            "늘렸습니다. 알려 주셔서 감사합니다."
        ),
    },
    {
        "id": 161,
        "created_on": "2026-09-03",
        "note": (
            "수정 완료(2026-09-03): 물어봐 주신 세 가지에 답을 드립니다. "
            "① 엑셀 파일 업로드는 아직 안 됩니다(이미지만 됩니다). 그래서 표를 "
            "이미지로 올리시면 값을 잘못 읽을 수 있습니다 — 엑셀에서 표를 복사해 "
            "채팅창에 그대로 붙여넣으시면 정확히 읽습니다. 엑셀 파일을 올리시면 "
            "이제 그 안내가 뜹니다. "
            "② 영어로 쓰실 필요 없습니다. 「수출신고번호」도 「면장」도 알아듣습니다. "
            "지난번에 「면장 정보가 없습니다」라고 답한 것은 **틀린 답변**이었습니다. "
            "이미지를 첨부하시면 사내 데이터 조회를 하지 않고 이미지만 읽는데, "
            "그 상태에서 「없다」고 단정해 버린 것입니다. 실제로는 수출신고번호도 "
            "OP담당자도 데이터에 있습니다. 이제 이미지가 붙은 데이터 질문에는 "
            "「데이터를 조회하지 않았으니 없다는 뜻이 아니다」라고 함께 안내하고, "
            "이미지 없이 다시 물어보시도록 알려드립니다. "
            "③ 「OP」라고 하셔도 됩니다. 「세일즈 운영팀」·「OP담당자」 모두 같은 "
            "뜻으로 알아듣도록 등록했습니다(첫 질문은 실제로 잘 조회됐습니다). "
            "덧붙여 「엑셀로 뽑아줘」라고 하셨는데 아무 파일도 안 드렸던 것도 "
            "고쳤습니다 — 이제 요청하시면 행 수와 무관하게 CSV 내려받기 링크가 "
            "붙습니다(엑셀에서 바로 열립니다. 다만 .xlsx 파일 자체를 만들지는 "
            "못합니다). 자세히 알려 주셔서 감사합니다."
        ),
    },
    {
        "id": 155,
        "created_on": "2026-09-01",
        "note": (
            "해결 완료(2026-09-03): 말씀하신 대로 상품 특징을 확인하실 수 있게 "
            "됐습니다. 당시에는 센텔라 테카 자료가 CS 자료에 없어서 "
            "「등록되어 있지 않습니다」라고 답했었는데, 자료가 채워져 지금은 "
            "핵심 성분과 함량(CENTELLA TECA™·식물성 병풀 PDRN·나이아신아마이드), "
            "제형과 사용감, 추천 피부 타입, 다른 성분과의 병행 사용까지 답합니다. "
            "프로덕션에서 「센텔라 테카 앰플의 특징을 알려줘」로 직접 확인했습니다. "
            "다시 비어 버리면 조용히 예전 답변으로 돌아가므로, 매일 도는 회귀 "
            "문항에 넣어 두었습니다. 제안해 주셔서 감사합니다."
        ),
    },
    {
        "id": 162,
        "created_on": "2026-09-04",
        "note": (
            "확인 완료(2026-09-04): 「한화로 변환이 안 된다」고 하신 그 건입니다. "
            "확인해 보니 변환이 안 된 게 아니라 **엉뚱한 값이 한화 금액 행세를 "
            "하고 있었습니다.** 「금액은 한화로 다 바꿔줘」에 대해 시스템이 수출 "
            "금액 대신 **물류비**(운임·관세·통관 수수료) 칸을 집계해 "
            "「한화 기준 수출 실적」이라는 이름으로 내놓았습니다. 그 칸은 2026년 "
            "8월 385건 중 14건(3.6%)에만 값이 있어서 미국 65건·캐나다 44건이 "
            "전부 빈칸으로 나갔고, 답변은 그 빈칸을 「원화 정산이 아직 안 된 "
            "듯하다」고 스스로 지어냈습니다. 다운로드 파일도 같은 값이었습니다. "
            "이제 두 가지를 코드가 막습니다. ① 한화로 바꿔 달라고 하시면 답변 맨 "
            "위에 「환산해 드리지 못했습니다」와 그 이유를 먼저 알려드립니다. "
            "② 물류비를 금액이라는 이름으로 내놓으면 「이건 수출 금액이 아니라 "
            "물류비」라고 코드가 함께 표시합니다. "
            "다만 **금액을 원화로 환산해 드리지는 못합니다** — 수출 건마다 통화가 "
            "다른데(USD·EUR·JPY·CNY·KRW, 통화가 비어 있는 건도 있습니다) 거래 "
            "시점 환율을 가지고 있지 않아, 환산하면 그럴듯하게 틀린 값이 됩니다. "
            "지금은 통화별 원값으로 보시고, 원화 건만 필요하시면 「통화가 KRW 인 "
            "건만」이라고 물어봐 주세요. 환율까지 붙일 수 있게 되면 그때 환산도 "
            "바로 되도록 하겠습니다. 알려 주셔서 감사합니다."
        ),
    },
    {
        "id": 153,
        "created_on": "2026-08-26",
        "note": (
            "수정 완료(2026-09-03): 두 가지가 잘못돼 있었습니다. "
            "① 「문서 찾아줘」라고 하셨는데 질문이 문서 검색이 아니라 데이터 조회로 "
            "갔습니다. 문서를 달라는 표현 중 「어디 있어」만 등록돼 있고 「문서 "
            "찾아줘」가 빠져 있었습니다. 이제 문서·자료를 찾아달라고 하시면 사내 "
            "문서에서 찾습니다(금액·수량을 함께 물으시면 종전대로 데이터를 조회합니다). "
            "② 더 문제였던 것은, 조회 결과가 「연동되어 있지 않습니다」라는 안내문 "
            "한 줄뿐이었는데 답변에 반품 6,998개·자사몰 매출 321.2억원·마케팅 예산 "
            "136.7억원 표가 붙은 것입니다. 그 숫자들은 조회 결과에 없는 값이었고, "
            "「내부 데이터베이스에서 확인할 수 있는 지표」라고 소개돼 나갔습니다. "
            "이제 안내문만 돌아온 경우에는 그 안내문을 그대로 보여주고 답변을 "
            "만들지 않습니다. 늦게 확인해 죄송하고, 알려 주셔서 감사합니다."
        ),
    },
    {
        "id": 165,
        "created_on": "2026-09-04",
        "note": (
            "수정 완료(2026-09-04): 「월별인데 시각화를 못나타냄」 하신 그 "
            "건입니다. 랩인네이처 라인을 월(10개) × 제품(19개)으로 물으셨는데, "
            "차트에 그릴 수 있는 계열 상한이 15개라 **차트가 통째로 생략**됐고 "
            "그 사실을 답변이 한 마디도 하지 않았습니다. 그래서 세 번 다시 "
            "물으시게 됐습니다. 이제 계열이 많으면 상위 9개를 그리고 나머지는 "
            "「기타 N개」로 **합쳐서** 함께 그립니다 — 합계는 그대로 보존되고, "
            "범례에 몇 개를 묶었는지 표시됩니다. 확인해 보니 상한을 통과한 "
            "11~15개짜리 차트에서도 초과분이 말없이 사라지고 있어 그것도 같이 "
            "고쳤습니다. 그리고 앞으로는 어떤 이유로든 차트를 못 그리면 "
            "「왜 못 그렸는지」를 답변에 적습니다. 다만 항목이 아주 잘게 "
            "흩어져 상위 몇 개가 전체의 10%도 안 되는 경우에는, 그리면 "
            "「기타」만 보여주는 차트가 되어 여전히 생략하고 이유를 "
            "알려드립니다. 알려 주셔서 감사합니다."
        ),
    },
    # ⛔ 두 건은 원인이 같다 — 질문에 「노션」이 들어 있다는 이유로 사내 문서
    #    검색으로 갔다. 찾아 달라는 것과 만들어 달라는 것을 가르지 못한 것이다.
    {
        "id": 163,
        "created_on": "2026-09-04",
        "note": (
            "수정 완료(2026-09-07): 「개인 업무 노션 페이지 구성할 건데 제안해 "
            "달라」고 하셨는데 사내 문서만 세 번 뒤져 CS·영업2팀·ERP 기안 가이드를 "
            "내놓았습니다. 질문에 「노션」이라는 말이 있으면 문서 검색으로 가도록 "
            "돼 있어서, **찾아 달라는 것과 만들어 달라는 것을 가르지 못했습니다.** "
            "자료가 없어서가 아니라 엉뚱한 곳을 뒤진 것입니다. 이제 제안·구성·초안·"
            "템플릿처럼 만들어 달라는 요청은 문서를 뒤지지 않고 바로 만들어 "
            "드립니다. 「노션 사용법 알려줘」처럼 자료를 찾는 질문은 종전대로 사내 "
            "문서를 검색합니다. 여러 번 다시 물으시게 해 죄송하고, 알려 주셔서 "
            "감사합니다."
        ),
    },
    {
        "id": 164,
        "created_on": "2026-09-04",
        "note": (
            "수정 완료(2026-09-07): 「Today에 뜨는 내용을 노션에 자동화해서 뜨게 "
            "할 수 없나」 물으셨는데 「[영업2팀] 타 팀 협업 요청」 문서가 나갔습니다. "
            "두 가지가 겹쳤습니다 — ① 「~할 수 없나」 같은 **부정형** 질문이 기능 "
            "문의로 인식되지 않았고(「~할 수 있어」만 등록돼 있었습니다), ② 첫 화면의 "
            "`Today`(출근 브리핑)가 셀라의 기능 목록에 아예 빠져 있었습니다. 둘 다 "
            "고쳤습니다. 답을 먼저 드리면 — **노션 자동 연동은 없습니다.** 지금 "
            "브리핑을 내보낼 수 있는 곳은 잔디뿐이고, 첫 화면 브리핑의 「잔디로 "
            "받기」에서 등록하시면 매일 원하는 시각(08:00~18:30, 30분 단위)에 "
            "받으실 수 있습니다. 일정·메일·할 일·기한·지표·환율 항목별로 끄고 켜는 "
            "것도 됩니다. 알려 주셔서 감사합니다."
        ),
    },
    {
        "id": 166,
        "created_on": "2026-09-08",
        "note": (
            "수정 완료(2026-09-08): 같은 해의 일별 차트는 눈금을 08/28처럼 월/일로 "
            "표시하고, 점 크기를 줄여 수치가 잘 보이도록 했습니다. 마우스를 올리면 "
            "전체 날짜가 보이며, 연도가 달라지는 차트는 연도를 유지합니다. "
            "기존 대화의 차트와 이미지 다운로드에도 적용됩니다."
        ),
    },
    {
        "id": 167,
        "created_on": "2026-09-08",
        "note": (
            "수정 완료(2026-09-08): 올해 미팅 횟수를 물으셨는데 다음 7일의 일정만 "
            "조회해 4건이라고 답했습니다. 이제 올해·작년·월·분기·날짜 범위의 전체 "
            "페이지를 조회하고, 제외할 미팅 제목을 적용한 뒤 월별 횟수와 합계를 "
            "직접 계산합니다. 원래 질문으로 다시 확인하실 수 있습니다. "
            "'올해 캘린더 미팅 횟수와 팀별 협업 통계'라고 물으면 참석자의 확인된 "
            "현재 소속별 공동 일정도 보여드립니다. 종일·취소·본인 거절 일정은 "
            "제외하고 반복 미팅은 회차별로 셉니다. 캘린더 등록 기준이므로 실제 "
            "참석 여부나 협업 성과를 의미하지 않으며, 소속 미확인 범위도 함께 "
            "안내합니다. 전체 조회에 실패하면 일부만 세어 총횟수라고 답하지 않습니다."
        ),
    },
)

_COLUMNS = (
    ("status", f"VARCHAR(16) NOT NULL DEFAULT '{STATUS_NEW}'"),
    ("handled_at", "DATETIME NULL"),
    ("handled_by", "VARCHAR(120) NULL"),
    ("handled_note", "TEXT NULL"),
    # 제보자가 회신을 읽었는지. ⛔ 회신이 **닿았는지 모르면** 안 한 것과 같다
    ("reply_seen_at", "DATETIME NULL"),
)


def ensure_feedback_status_columns() -> None:
    """처리 상태 컬럼을 붙인다 (앱 기동 시 idempotent — FI 권한 컬럼과 같은 방식)."""
    for col, definition in _COLUMNS:
        try:
            if not fetch_one(
                "SELECT 1 AS ok FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'message_feedback' "
                "AND COLUMN_NAME = %s", (col,)):
                execute(f"ALTER TABLE message_feedback ADD COLUMN {col} {definition}")
        except Exception as e:
            logger.warning("feedback_status_column_error", col=col, error=str(e)[:160])
    try:
        execute("ALTER TABLE message_feedback ADD INDEX idx_mf_status (status)")
    except Exception:
        pass  # 이미 있음


def list_feedback(status: Optional[str] = None, only_down: bool = True,
                  limit: int = 200) -> List[Dict[str, Any]]:
    """처리함 목록. **코멘트가 있는 것을 먼저** 보여준다 — 읽을 게 있는 쪽이 값이 크다."""
    where = ["1=1"]
    params: list = []
    if only_down:
        where.append("f.rating < 0")
    if status:
        where.append("f.status = %s")
        params.append(status)
    rows = fetch_all(
        "SELECT f.id, f.rating, f.comment, f.created_at, f.status, f.handled_at, "
        "       f.handled_by, f.handled_note, f.conversation_id, f.message_id, "
        "       u.display_name AS user_name "
        "FROM message_feedback f LEFT JOIN users u ON u.id = f.user_id "
        f"WHERE {' AND '.join(where)} "
        # 코멘트 있는 것 우선 → 미처리 우선 → 최신순
        "ORDER BY (f.comment IS NOT NULL AND f.comment <> '') DESC, "
        "         (f.status = 'new') DESC, f.created_at DESC "
        "LIMIT %s", (*params, int(limit))) or []
    for r in rows:
        # ⚠️ 오래된 행은 status 가 기본값이라 NULL 이 아니지만, 컬럼 추가 직후를 대비
        r["status"] = r.get("status") or STATUS_NEW
    return rows


# ── 처리함 = 붐따 + 만족도 설문 ────────────────────────────────────────────
# ⛔ 처리 동선은 **하나**다. 설문을 별도 탭으로 갈라 두면 "붐따처럼 개선에 반영"이
#    흐려진다 — 두 대기열은 언젠가 한쪽만 읽힌다. 행마다 `source` 로 구분한다.
SOURCE_THUMBS = "thumbs"
SOURCE_SURVEY = "survey"
_SOURCES = (SOURCE_THUMBS, SOURCE_SURVEY)


def list_inbox(status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """붐따와 설문을 합쳐 돌려준다. 코멘트 있는 것 → 미처리 → 최신 순."""
    rows = list_feedback(status=status, only_down=True, limit=limit)
    for r in rows:
        r["source"] = SOURCE_THUMBS
    try:
        from app.core.satisfaction import list_surveys
        rows = rows + list_surveys(status=status, limit=limit)
    except Exception as e:   # 설문 테이블이 아직 없어도 붐따는 보여야 한다
        logger.warning("survey_list_failed", error=str(e)[:160])

    # 최신순으로 먼저 세운 뒤, 안정 정렬로 "코멘트 있는 것 → 미처리" 를 앞으로 올린다
    # (같은 순위 안에서는 최신순이 그대로 유지된다)
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    rows.sort(key=lambda r: (
        0 if (r.get("comment") or "").strip() else 1,
        0 if (r.get("status") or STATUS_NEW) == STATUS_NEW else 1,
    ))
    return rows[:int(limit)]


def inbox_summary() -> Dict[str, Any]:
    """붐따 집계 + 설문 현황. **응답률과 대상 인원을 함께 낸다** — 팝업이 안 뜨는
    것은 에러가 아니라 침묵이라, 화면이 그것을 말할 수 있어야 한다."""
    base = summary()
    try:
        from app.core.satisfaction import eligible_users, survey_summary
        sv = survey_summary()
        base["survey"] = sv
        base["survey"]["eligible"] = eligible_users()
        base["open"] = int(base.get("open", 0)) + int(sv.get("open", 0))
    except Exception as e:
        logger.warning("survey_summary_failed", error=str(e)[:160])
        base["survey"] = None
    return base


def set_inbox_status(source: str, item_id: int, status: str, who: str,
                     note: Optional[str] = None, notify: bool = True) -> bool:
    """상태 변경을 소스별로 보낸다. 모르는 소스는 거절한다 (조용히 무시하면
    관리자가 바꾼 것이 사라진 것처럼 보인다)."""
    if source not in _SOURCES:
        raise ValueError(f"unknown source: {source}")
    if source == SOURCE_SURVEY:
        from app.core.satisfaction import set_survey_status
        return set_survey_status(item_id, status, who, note)
    return set_status(item_id, status, who, note, notify=notify)


def set_status(feedback_id: int, status: str, who: str,
               note: Optional[str] = None, notify: bool = True) -> bool:
    """처리 상태를 바꾼다. 알 수 없는 상태는 거절한다 (오타로 조용히 사라지지 않게)."""
    if status not in _STATUSES:
        raise ValueError(f"unknown status: {status}")
    if _handled_note_has_encoding_loss(note):
        raise ValueError(
            "처리 메모 인코딩이 손상되었습니다. UTF-8 입력으로 다시 작성해주세요."
        )
    done = status in (STATUS_DONE, STATUS_WONTFIX)
    execute(
        "UPDATE message_feedback SET status = %s, handled_by = %s, handled_note = %s, "
        "handled_at = " + ("NOW()" if done else "NULL") + " WHERE id = %s",
        (status, who, note, int(feedback_id)))
    logger.info("feedback_status_changed", id=feedback_id, status=status, who=who)

    # 처리를 끝냈으면 제보자에게 메일로도 알린다 — **길이 열려 있을 때만** (기본 꺼짐).
    # 앱 알림(사이드바)은 이미 뜬다. 메일은 "답이 돌아온다"를 확실히 하는 보조 경로다.
    # ⚠️ 메일 실패가 상태 변경을 되돌리면 안 된다 — 여기서 예외를 밖으로 내지 않는다
    if done and notify:
        try:
            from app.core import mailer
            if mailer.is_enabled():
                row = fetch_one(
                    "SELECT COALESCE(a.email, u.email) AS email, f.comment "
                    "FROM message_feedback f JOIN users u ON u.id = f.user_id "
                    "LEFT JOIN directory_users a ON a.id = u.ad_user_id WHERE f.id = %s",
                    (int(feedback_id),))
                if row and row.get("email"):
                    label = {STATUS_DONE: "해결됨", STATUS_WONTFIX: "고치지 않음"}[status]
                    mailer.feedback_handled(
                        row["email"], (row.get("comment") or "(내용 없음)")[:80], label, note or "")
        except Exception as e:
            logger.warning("feedback_mail_failed", id=feedback_id,
                           error=f"{type(e).__name__}: {str(e)[:200]}")
    return True


def apply_deployed_resolutions(
    resolutions: Sequence[Dict[str, Any]] = DEPLOYED_FEEDBACK_RESOLUTIONS,
    *,
    fetcher: Optional[Callable[[int, str], Optional[Dict[str, Any]]]] = None,
    setter: Optional[Callable[[int, str, str, str], Any]] = None,
) -> Dict[str, int]:
    """배포된 해결 목록을 피드백 상태에 idempotent하게 반영한다.

    피드백 id 만 믿지 않고 신고일과 rating=-1까지 DB 쿼리 안에서 검증한다. 이미
    ``done``/``wontfix`` 인 행은 건드리지 않아 프로세스 재기동 때 알림이 반복되지
    않는다. 자동 동기화는 대량 메일을 보내지 않지만 ``handled_at``은 남기므로 앱의
    기존 피드백 알림 화면에서는 해결 사실을 확인할 수 있다.
    """
    if fetcher is None:
        def fetcher(feedback_id: int, created_on: str) -> Optional[Dict[str, Any]]:
            return fetch_one(
                "SELECT id, status, handled_note FROM message_feedback "
                "WHERE id = %s AND rating = -1 AND DATE(created_at) = %s",
                (int(feedback_id), created_on),
            )
    if setter is None:
        def setter(feedback_id: int, status: str, who: str, note: str) -> Any:
            return set_status(feedback_id, status, who, note, notify=False)

    result = {"done": 0, "already_closed": 0, "missing_or_mismatched": 0}
    for item in resolutions:
        feedback_id = int(item["id"])
        created_on = str(item["created_on"])
        row = fetcher(feedback_id, created_on)
        if not row:
            result["missing_or_mismatched"] += 1
            logger.info(
                "deployed_feedback_resolution_not_found",
                id=feedback_id,
                created_on=created_on,
            )
            continue
        if (row.get("status") or STATUS_NEW) in (STATUS_DONE, STATUS_WONTFIX):
            result["already_closed"] += 1
            continue

        note = str(item.get("note") or "").strip()
        previous_note = str(row.get("handled_note") or "").strip()
        if previous_note and previous_note not in note:
            note = f"{previous_note}\n\n{note}" if note else previous_note
        setter(feedback_id, STATUS_DONE, "system:deployed-resolution", note)
        result["done"] += 1

    logger.info("deployed_feedback_resolutions_applied", **result)
    return result


def summary() -> Dict[str, Any]:
    """상태별 집계 + 처리 지연 — Admin 배지와 다이제스트가 함께 쓴다."""
    rows = fetch_all(
        "SELECT status, COUNT(*) n, SUM(comment IS NOT NULL AND comment <> '') c "
        "FROM message_feedback WHERE rating < 0 GROUP BY status") or []
    by = {r["status"] or STATUS_NEW: {"n": int(r["n"] or 0), "with_comment": int(r["c"] or 0)}
          for r in rows}
    oldest = fetch_one(
        "SELECT MIN(created_at) t FROM message_feedback "
        "WHERE rating < 0 AND status = %s AND comment IS NOT NULL AND comment <> ''",
        (STATUS_NEW,)) or {}
    return {
        "by_status": by,
        "open": sum(by.get(s, {}).get("n", 0) for s in (STATUS_NEW, STATUS_ACK)),
        "open_with_comment": sum(by.get(s, {}).get("with_comment", 0)
                                 for s in (STATUS_NEW, STATUS_ACK)),
        "oldest_unread": oldest.get("t"),
    }


def run_daily_digest(hours: int = 24) -> Dict[str, Any]:
    """매일: **새로 들어온 붐따를 읽어 로그로 올린다.**

    ⛔ 미처리 총량을 매일 알리지 않는다 — 매일 같은 알림은 곧 무시당한다
       (자가 점검이 상태 변화만 알리는 것과 같은 판단). 새로 들어온 것만 본다.

    ⚠️ 잔디는 WAS 에서 403 이라 여기서 못 보낸다 (프록시 허용 범위가 서버마다
       다르다). 그래서 **WARNING 로그 + Admin 화면**이 전달 경로다 — 프로덕션은
       INFO 를 버리므로 반드시 WARNING 이어야 한다.
    """
    fresh = fetch_all(
        "SELECT f.id, f.comment, f.created_at, u.display_name AS user_name "
        "FROM message_feedback f LEFT JOIN users u ON u.id = f.user_id "
        "WHERE f.rating < 0 AND f.created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR) "
        "ORDER BY f.created_at", (int(hours),)) or []
    with_comment = [r for r in fresh if (r.get("comment") or "").strip()]
    stats = summary()
    result = {
        "new": len(fresh),
        "new_with_comment": len(with_comment),
        "open": stats["open"],
        "open_with_comment": stats["open_with_comment"],
        "oldest_unread": str(stats.get("oldest_unread") or ""),
    }
    if with_comment:
        # 내용을 로그에 실어야 "읽히지 않는" 상태가 끝난다. 길이는 잘라 둔다
        logger.warning(
            "feedback_digest_new", **result,
            items=[{"id": r["id"], "who": r.get("user_name"),
                    "comment": (r.get("comment") or "")[:200]} for r in with_comment[:15]])
    else:
        logger.info("feedback_digest_quiet", **result)
    return result


# ── 제보자 회신 ────────────────────────────────────────────────────────────
# ⛔ **회신 경로가 없던 것이 인입량 부진의 가장 유력한 원인이다** (2026-08-18 실측).
#    노션 채널은 제보마다 답글이 달려 8월 처리·회신 100% 인데, 앱은 회신 0건이었다.
#    전휘빈 님은 5~7월에 수치 오류를 4번 제보했고 **그중 3건이 같은 두 원인**이었다 —
#    답을 못 받으니 같은 것을 계속 겪으며 계속 신고한 것이다.
#    고친 사실을 돌려주지 않으면, 제보는 "밑 빠진 독"이 되고 곧 아무도 안 쓴다.


def replies_for_user(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """내 붐따 중 **처리되고 메모가 달린 것**. 안 읽은 것이 먼저 온다."""
    return fetch_all(
        "SELECT id, comment, created_at, status, handled_at, handled_note, "
        "       (reply_seen_at IS NULL) AS unseen "
        "FROM message_feedback "
        "WHERE user_id = %s AND status IN (%s, %s) "
        "  AND handled_note IS NOT NULL AND handled_note <> '' "
        "ORDER BY unseen DESC, handled_at DESC LIMIT %s",
        (int(user_id), STATUS_DONE, STATUS_WONTFIX, int(limit))) or []


def mark_replies_seen(user_id: int) -> int:
    """읽음 처리. ⚠️ 본인 것만 — user_id 를 **SQL 안에서** 건다
    (파이썬에서 먼저 확인하고 나중에 UPDATE 하면 확인을 빠뜨린 호출부가 언젠가 생긴다)."""
    return execute(
        "UPDATE message_feedback SET reply_seen_at = NOW() "
        "WHERE user_id = %s AND reply_seen_at IS NULL "
        "  AND handled_note IS NOT NULL AND handled_note <> ''",
        (int(user_id),))


def my_feedback(user_id: int, limit: int = 10, days: int = 90) -> List[Dict[str, Any]]:
    """내가 낸 붐따 — **처리 전 것도 포함**한다.

    회신(`replies_for_user`)은 메모가 달린 것만 돌려준다. 그것만 보여주면 사용자는
    "내가 신고한 게 접수는 됐나" 를 알 수 없다 — 답이 없는 구간이 길수록 제보가 끊긴다
    (2026-08-18 회신 0건 분석). 상태를 그대로 보여준다.

    ⚠️ 붐따는 **코멘트 없이 누르는 경우가 대부분**이라(실측: 최근 것 대부분 NULL)
       제목에 코멘트만 쓰면 "(내용 없음)" 이 줄줄이 뜬다. 그때는 **내가 물었던 질문**을
       제목으로 쓴다 — 무엇에 대한 신고인지 알아야 알림이 뜻을 갖는다.
    ⚠️ 오래된 것은 빼야 한다. 100일 전 **미처리** 건이 목록을 채우면 새 소식이 묻힌다.
    ⛔ 단 **회신은 나이와 무관하게 닿아야 한다.** 창을 그대로 두면 뒤늦게 처리 표시한
       건이 `handled_at` 만 채운 채 화면에 뜨지 않는다 — 에러 없는 고장이다
       (2026-08-24 실측: 묵은 붐따 16건을 처리했는데 7건이 창 밖이라 안 보였다).
       그래서 "최근이거나, 처리됐는데 아직 안 읽은 것" 으로 넓힌다. 읽으면 다시
       빠지므로 목록이 무한히 자라지 않는다.
    ⚠️ 안 읽음 판정은 `reply_seen_at < handled_at` 도 본다. 컬럼이 하나뿐이라
       "확인함" 단계에서 읽고 나중에 "해결" 로 바뀌면 다시 안 읽음이 돼야 한다.
    """
    return fetch_all(
        "SELECT f.id, f.comment, f.created_at, f.status, f.handled_at, f.handled_note, "
        "       LEFT(COALESCE(c.title, ''), 80) AS question, "
        "       (f.handled_at IS NOT NULL AND "
        "        (f.reply_seen_at IS NULL OR f.reply_seen_at < f.handled_at)) AS unseen "
        "FROM message_feedback f "
        # ⛔ `message_feedback.conversation_id` 만 collation 이 다르게 만들어져 있다
        #    (uca1400_ai_ci vs unicode_ci — 표를 만든 시기가 달라서다). 명시하지 않으면
        #    "Illegal mix of collations" 로 **조인 자체가 터진다**. 서버마다 기본값이
        #    다를 수 있으므로 스키마를 고치는 대신 조건에 못 박는다.
        "LEFT JOIN conversations c "
        "       ON c.id = f.conversation_id COLLATE utf8mb4_unicode_ci "
        "WHERE f.user_id = %s AND f.rating = -1 "
        "  AND (f.created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "       OR (f.handled_at IS NOT NULL "
        "           AND (f.reply_seen_at IS NULL OR f.reply_seen_at < f.handled_at))) "
        "ORDER BY unseen DESC, f.created_at DESC LIMIT %s",
        (int(user_id), int(days), int(limit))) or []


def mark_my_feedback_seen(user_id: int) -> int:
    """내 붐따 알림을 읽음으로. ⚠️ 본인 것만 — user_id 를 SQL 안에서 건다."""
    return execute(
        "UPDATE message_feedback SET reply_seen_at = NOW() "
        "WHERE user_id = %s AND rating = -1 AND handled_at IS NOT NULL "
        "  AND (reply_seen_at IS NULL OR reply_seen_at < handled_at)",
        (int(user_id),))
