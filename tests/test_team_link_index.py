# -*- coding: utf-8 -*-
"""팀 자료 링크 카드 — 색인에 태울 것과 버릴 것.

⛔ 이 단계의 실패는 전부 조용하다. 이름 없는 카드를 태우면 **검색 결과가 지저분해질
   뿐 에러가 없고**, 팀 값을 리졸버에 안 태우면 `@@GM EAST` 에서 이 카드들만 빠지는데
   그것도 에러가 아니라 0건이다. 그래서 규칙을 테스트로 못 박는다.
"""
from app.core.team_link_index import build_link_cards, notion_page_id


def _rows(*extra):
    """팀 → 폴더 → 자료 최소 트리."""
    base = [
        {"id": 1, "parent_id": None, "team": "B2B2", "node_type": "team",
         "name": "B2B2", "resource_type": "other", "url": "", "description": "",
         "synced_at": "2026-08-25 01:01:10"},
        {"id": 2, "parent_id": 1, "team": "B2B2", "node_type": "folder",
         "name": "B2B2 내부자료", "resource_type": "other", "url": "", "description": "",
         "synced_at": "2026-08-25 01:01:10"},
    ]
    return base + list(extra)


def _row(**kw):
    row = {"id": 10, "parent_id": 2, "team": "B2B2", "node_type": "sheet",
           "name": "B2B 업체별 담당자 정보", "resource_type": "google_sheet",
           "url": "https://docs.google.com/spreadsheets/d/abc123",
           "description": "", "synced_at": "2026-08-25 01:01:10"}
    row.update(kw)
    return row


def _identity(team):
    return team


def test_sheet_link_becomes_a_searchable_card():
    """시트·드라이브 링크가 색인에 들어가야 '그 시트 어디 있어' 가 답이 된다."""
    cards = build_link_cards(_rows(_row()), resolve_team=_identity)
    assert len(cards) == 1
    payload = cards[0]["payload"]
    assert payload["page_title"] == "B2B 업체별 담당자 정보"
    assert "docs.google.com/spreadsheets/d/abc123" in payload["text"], "링크가 본문에 없다"
    assert "B2B2 내부자료" in payload["text"], "상위 경로가 본문에 없다 — 뜻을 잃는다"
    assert payload["source"] == "team_resources", "링크 카드 표식이 없으면 나중에 못 가려낸다"


def test_nameless_rows_are_dropped_not_indexed():
    """⛔ 'Untitled'·URL 뿐인 이름을 태우면 검색 잡음이 된다 — 잡음은 답처럼 보인다."""
    rows = _rows(
        _row(id=11, name="Untitled", parent_id=None),
        _row(id=12, name="https://drive.google.com/drive/folders/1", parent_id=None),
        _row(id=13, name="CT", parent_id=None),
        _row(id=14, name="/2a32b4283b0080968e3ad4a19f3ad26c", parent_id=None),
    )
    assert build_link_cards(rows, resolve_team=_identity) == []


def test_breadcrumb_rescues_a_nameless_row():
    """이름이 부실해도 상위 폴더가 뜻을 갖고 있으면 살린다."""
    cards = build_link_cards(_rows(_row(id=15, name="Untitled")), resolve_team=_identity)
    assert len(cards) == 1
    assert cards[0]["payload"]["page_title"] == "B2B2 내부자료"


def test_team_value_goes_through_the_resolver():
    """⛔ 색인은 `[GM]EAST`, team_resources 는 `GM EAST` 다.

    리졸버를 안 거치면 `@@GM EAST` 필터에 이 카드들만 안 걸린다 — 에러 없이 빠진다.
    """
    from app.agents.qdrant_agent import resolve_team_filter

    rows = _rows(_row(id=16, team="GM EAST"))
    for r in rows:
        r["team"] = "GM EAST"
    cards = build_link_cards(rows, resolve_team=resolve_team_filter)
    assert cards[0]["payload"]["team"] == "[GM]EAST"


def test_already_indexed_notion_page_is_not_duplicated():
    """본문이 이미 색인된 노션 문서를 링크 카드로 또 넣으면 같은 문서가 두 번 잡힌다."""
    page = "3202b4283b0080428c67f84ff05533a4"
    rows = _rows(_row(id=17, name="영업2팀 신규 입사자 필독",
                      resource_type="notion",
                      url=f"https://app.notion.com/p/{page}"))
    assert build_link_cards(rows, indexed_page_ids={page}, resolve_team=_identity) == []
    # 색인에 없으면 태운다
    assert len(build_link_cards(rows, indexed_page_ids=set(), resolve_team=_identity)) == 1


def test_notion_page_id_reads_both_url_shapes():
    dashed = "https://www.notion.so/3362b428-3b00-80af-8e00-f82c182320df"
    plain = "https://www.notion.so/3362b4283b0080af8e00f82c182320df"
    assert notion_page_id(dashed) == notion_page_id(plain) == "3362b4283b0080af8e00f82c182320df"
    assert notion_page_id("https://docs.google.com/spreadsheets/d/abc") == ""


def test_same_content_keeps_the_same_hash():
    """⚠️ 재임베딩을 이 해시로 건너뛴다 — 매번 달라지면 179장을 매일 다시 임베딩한다."""
    rows = _rows(_row())
    first = build_link_cards(rows, resolve_team=_identity)[0]["payload"]
    second = build_link_cards(rows, resolve_team=_identity)[0]["payload"]
    assert first["content_sha256"] == second["content_sha256"]
    assert first["page_id"] == second["page_id"] == "teamres-10"
