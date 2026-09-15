"""새 로그인 계정의 기본 데이터 그룹을 부서로 정한다 (2026-09-16 사용자 지시).

배경: 정재명(IT팀)이 회원가입했는데 그룹이 비어 있어 질문을 못 했다. 그때까지는
관리자가 손으로 배정해야 했다. 사용자: *"유통본부는 DD로 하고 나머지는 SK로 그룹배정
자동으로 하게 해"*, *"앞으로 누가 회원가입해도 자동으로 배정해"*.

실측(2026-09-16 프로덕션): DD 그룹원 전원이 `유통부문`(유통1본부·유통2본부·유통SCM본부)
소속이고, SK_Brand 그룹원은 전부 `브랜드부문`이다. 부서 경로는
`Craver_Accounts > Users > 유통부문 > 유통1본부 > 리테일팀 > 리테일1파트` 꼴이다.

- ⛔ **'유통' 이라는 글자로 판정하지 마라.** `브랜드부문 > 중국사업팀 > 신규 브랜드 유통파트`
  가 걸린다. 판정은 **경로 마디**로 한다 — `유통부문`·`Distribution Division` 이거나,
  `유통…본부` 로 끝나는 마디가 있을 때만 유통이다
- ⛔ **이미 브랜드 그룹이 있으면 손대지 않는다.** 관리자가 미리 배정해 둔 사람의
  그룹을 덮으면 안 된다
- ⛔ **그룹이 없으면(이름이 바뀌었으면) 조용히 넘기지 말고 WARNING 을 남긴다.**
  그때는 예전처럼 관리자가 배정한다 — `requires_group_assignment` 가 그대로 막는다
- ⚠️ **옛 계정(`requires_group_assignment=0`)은 대상이 아니다.** 그 계정들은 그룹이
  없어도 전체 브랜드가 열려 있다(플래그가 0 이라 관문이 안 막는다). 뒤늦게 붙이면
  **접근이 조용히 좁아진다** — 호출부가 플래그로 가른다
"""
from __future__ import annotations

import re

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_GROUP = "SK_Brand"
DISTRIBUTION_GROUP = "DD"

_DIVISION = re.compile(r"유통\s*부문|distribution\s*division", re.IGNORECASE)
_DIVISION_HQ = re.compile(r"^유통.{0,6}본부$")


def _segments(department: str | None) -> list[str]:
    return [s.strip() for s in str(department or "").split(">") if s.strip()]


def default_group_name(department: str | None) -> str:
    """부서 경로 → 기본 그룹 이름. 유통부문(유통N본부·유통SCM본부)만 DD, 나머지는 SK."""
    for seg in _segments(department):
        if _DIVISION.search(seg) or _DIVISION_HQ.match(seg.replace(" ", "")):
            return DISTRIBUTION_GROUP
    return DEFAULT_GROUP


def assign_default_group(cur, ad_user_id: int, department: str | None) -> dict:
    """같은 트랜잭션 커서로 기본 그룹을 붙인다. 이미 브랜드 그룹이 있으면 그대로 둔다.

    Returns {"assigned": <그룹 이름 또는 None>, "reason": ...}.
    """
    cur.execute(
        "SELECT g.id FROM user_groups ug JOIN access_groups g ON g.id=ug.group_id "
        "WHERE ug.ad_user_id=%s AND g.brand_filter IS NOT NULL AND g.brand_filter<>'' LIMIT 1",
        (ad_user_id,),
    )
    if cur.fetchone():
        return {"assigned": None, "reason": "already_in_group"}
    name = default_group_name(department)
    cur.execute("SELECT id FROM access_groups WHERE name=%s LIMIT 1", (name,))
    row = cur.fetchone()
    if not row:
        # 그룹 이름이 바뀌었거나 아직 없다 — 관리자 배정으로 되돌아간다. 조용히 넘기지 않는다
        logger.warning("group_autoassign_missing_group", group=name,
                       ad_user_id=ad_user_id, department=str(department or "")[:120])
        return {"assigned": None, "reason": "group_missing"}
    cur.execute("INSERT INTO user_groups (ad_user_id, group_id) VALUES (%s, %s)",
                (ad_user_id, row["id"]))
    # WARNING 이다 — INFO 는 프로덕션에서 버려진다. 누가 어느 그룹에 자동으로 들어갔는지 남긴다
    logger.warning("group_autoassigned", group=name, ad_user_id=ad_user_id,
                   department=str(department or "")[:120])
    return {"assigned": name, "reason": "department"}
