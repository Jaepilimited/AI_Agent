# -*- coding: utf-8 -*-
"""노션 학습 범위 — 무엇을 배우고 무엇을 **영구 제외**하는가 (2026-09-04).

⛔ **사용자 제보**: *"제품정보 학습이 되어있는지 확인"* → 안 돼 있었다.

원인이 **둘**이었고, 둘 다 있어야 학습된다:

  1. **권한** — `Skin1004_AI` 인테그레이션이 그 DB에 연결돼 있지 않았다
     (`/pages` `/databases` `/blocks` × API 버전 2종 × 대시 유무 = 전부 404)
  2. **도달** — 파이프라인은 **DB-HUB 에서만** 크롤한다. 그 DB 는 `CS Hub (KR)`
     아래에 있었고 DB-HUB 어디에서도 참조되지 않았다

⚠️ **1번만 고치면 여전히 안 들어온다.** 실측으로 확인했다: 권한을 준 직후에도
   크롤 113페이지에 없었고, DB-HUB 의 CS 토글에 멘션을 넣자 114페이지로 늘며 잡혔다.

⚠️ 읽을 수 없는 페이지도 **크롤 목록에는 잡힌다** (실측: 113개 중 22개가 404).
   그래서 "목록에 없다" 는 권한 문제가 아니라 **도달 문제**라고 구분할 수 있었다.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _pipeline():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "notion_pipe", ROOT / "scripts" / "notion_qdrant_pipeline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 영구 제외 ───────────────────────────────────────────────────────────────

def test_stale_product_db_is_permanently_excluded():
    """⛔ 사용자 지시 (2026-09-04): *"검색에서 아예 제외되도"*.

    `SK/CL 전제품 정보` 는 2022-05-16 생성 이후 한 번도 수정되지 않았다.
    신제품(센텔라 테카 등)이 없어 최신 제품정보를 오염시킨다.
    """
    pipe = _pipeline()
    assert pipe._is_excluded("ea8558d6-49aa-4de7-b4b0-8d78f4fdae4e")
    assert pipe._is_excluded("ea8558d649aa4de7b4b08d78f4fdae4e")


def test_exclusion_ignores_dashes_and_case():
    pipe = _pipeline()
    assert pipe._is_excluded("EA8558D6-49AA-4DE7-B4B0-8D78F4FDAE4E")
    assert not pipe._is_excluded("")
    assert not pipe._is_excluded(None)


def test_every_exclusion_carries_a_written_reason():
    """⚠️ 이유가 없으면 다음 사람이 '왜 빠졌지' 를 매번 다시 조사한다."""
    pipe = _pipeline()
    assert pipe.EXCLUDED_PAGE_IDS, "제외 목록이 비었다"
    for pid, reason in pipe.EXCLUDED_PAGE_IDS.items():
        assert len(pid.replace("-", "")) == 32, pid
        assert reason and len(reason) > 6, f"{pid} 에 이유가 없다"


def test_excluded_pages_are_dropped_before_collection():
    """제외는 **수집 단계**에서 걸러야 한다 — 그래야 매일 404 를 두드리지 않는다.

    ⛔ **"기존 조각까지 지워진다" 는 조건부다** (2026-09-04 실측으로 정정).
       삭제는 `local_page_map(로컬 JSON) - notion_page_ids` 라서 **파이프라인이
       스스로 넣은 페이지만** 지운다. 다른 적재기가 넣은 옛 조각은 로컬 JSON 에
       없어 계산 대상이 아니다 — 제외해도 Qdrant 에 남는다.
    """
    src = (ROOT / "scripts" / "notion_qdrant_pipeline.py").read_text(encoding="utf-8")
    body = src[src.index("hub_pages = crawl_hub(client)"):src.index("notion_page_ids: set[str] = set()")]
    assert "_is_excluded(pid)" in body, "중복 제거 루프에서 걸러야 한다"


# ── 학습 대상 (도달) ────────────────────────────────────────────────────────

def test_pipeline_only_crawls_from_db_hub():
    """⚠️ DB-HUB 가 **단일 진입점**이다 — 코드에 별도 루트 목록을 만들지 마라.

    두 곳에서 "무엇을 배우는가" 를 정하면 반드시 갈린다. 새 자료는 노션에서
    DB-HUB 팀 토글에 **멘션으로** 걸어야 학습된다.
    """
    pipe = _pipeline()
    assert pipe.DB_HUB_ID == "2e12b4283b008011ae32e39bf73b7f7b"
    src = (ROOT / "scripts" / "notion_qdrant_pipeline.py").read_text(encoding="utf-8")
    crawl = src[src.index("def crawl_hub"):src.index("def _get_block_children")]
    assert "DB_HUB_ID" in crawl
    # 크롤이 다른 루트를 추가로 훑지 않는지 (진입점이 하나인지)
    assert crawl.count("_get_block_children(") == 1


def test_mentions_are_what_the_crawler_collects():
    """⚠️ DB-HUB 에는 **페이지 멘션**으로 걸어야 한다. 평범한 URL 링크는 수집되지 않는다
    (`_collect_from_block` 이 child_page·child_database·mention 만 본다)."""
    src = (ROOT / "scripts" / "notion_qdrant_pipeline.py").read_text(encoding="utf-8")
    body = src[src.index("def _collect_from_block"):src.index("def fetch_page_meta")]
    assert "child_page" in body and "child_database" in body and "mention" in body


# ── 미학습 자료 감시 (2026-09-04) ───────────────────────────────────────────
#
# ⛔ 파이프라인은 매일 `404스킵=20` 을 찍고 있었는데 **사람이 물어봐서야** 알았다.
#    로그는 읽는 사람이 없으면 없는 것과 같다.

def test_pipeline_records_which_pages_were_skipped():
    """⚠️ 건수만 세면 아무것도 못 한다 — 누구에게 무엇을 공유해 달라고 할지 모른다."""
    src = (ROOT / "scripts" / "notion_qdrant_pipeline.py").read_text(encoding="utf-8")
    assert "skipped_pages.append" in src
    assert src.count("skipped_pages.append") == 2, "페이지·데이터베이스 양쪽에서 모아야 한다"
    assert "from app.core.notion_watch import record" in src


def test_recording_failure_does_not_kill_the_pipeline():
    """⚠️ 기록은 부수 작업이다 — 실패해도 색인은 살아야 한다."""
    src = (ROOT / "scripts" / "notion_qdrant_pipeline.py").read_text(encoding="utf-8")
    body = src[src.index('stats["skipped_pages"] = skipped_pages'):]
    assert "try:" in body[:200] and "except Exception" in body[:400]


def test_only_new_unshared_pages_raise_an_alarm(monkeypatch):
    """⛔ 총 건수로 매일 울리면 밀린 20건 때문에 경보가 소음이 된다.
    **새로 생긴 것**만 실패로 올린다."""
    from app.core import notion_watch as NW

    pages = [{"page_id": "a" * 32, "team": "CS", "title": "옛것", "kind": "page"},
             {"page_id": "b" * 32, "team": "DB", "title": "새것", "kind": "database"}]
    monkeypatch.setattr(NW, "latest", lambda: {"pages": pages, "at": "now"})
    monkeypatch.setattr(NW, "_seen_ids", lambda: {"a" * 32})
    remembered = {}
    monkeypatch.setattr(NW, "_remember", lambda ids: remembered.update({"ids": ids}))

    r = NW.newly_unshared()
    assert r["total"] == 2
    assert [p["title"] for p in r["new"]] == ["새것"]
    # ⚠️ 본 것은 기억해 둔다 — 같은 건으로 두 번 울리지 않는다
    assert remembered["ids"] == {"a" * 32, "b" * 32}


def test_no_record_is_not_a_failure(monkeypatch):
    """⚠️ 파이프라인이 아직 안 돌았을 뿐인데 실패로 울리면 안 된다."""
    from app.core import notion_watch as NW

    monkeypatch.setattr(NW, "latest", lambda: None)
    r = NW.newly_unshared()
    assert r["known"] is False and r["new"] == []


def test_lines_are_actionable():
    """사람이 바로 누를 수 있어야 한다 — 팀·제목·링크."""
    from app.core.notion_watch import as_lines

    line = as_lines([{"page_id": "e522b4283b00827e846a8150716cd5ca",
                      "team": "CS", "title": "제품 라인업"}])[0]
    assert "[CS]" in line and "제품 라인업" in line
    assert "https://www.notion.so/e522b4283b00827e846a8150716cd5ca" in line


def test_watch_is_registered_in_self_check():
    from app.core.self_check import CHECKS

    assert "notion_unshared" in {c.id for c in CHECKS}


# ── 외부 공개 사이트는 학습할 수 없다 (2026-09-04 실측) ─────────────────────
#
# ⛔ DB-HUB 에 멘션은 걸려 있는데 열어 보면 우리 SKIN1004 워크스페이스가 아니라
#    `verbena-niece-fe4.notion.site` 라는 **별도 공개 사이트**로 리다이렉트된다.
#    우리 인테그레이션을 붙일 수 있는 대상이 아니라 **영원히 404** 다.
#    (로그인된 브라우저로만 판정된다 — 비로그인 요청은 전부 로그인 페이지를 준다)

EXTERNAL_PEOPLE_PAGES = [
    "5bbe47d1099f435eb0d3a7d4fbb1c807",  # 시설
    "1156714bbf6880b681dbce22a89b18d8",  # 복리후생
    "14c6714bbf6880d3a971f3bf9e0a5dd9",  # 퇴사(Offboarding)
    "2a86714bbf68814cbce8c0a58644c160",  # (리더용) 상반기 다면 피드백 가이드
]


@pytest.mark.parametrize("pid", EXTERNAL_PEOPLE_PAGES)
def test_external_site_pages_are_excluded(pid):
    """붙일 수 없는 것을 매일 404 로 두드리지 않는다."""
    pipe = _pipeline()
    assert pipe._is_excluded(pid)


def test_exclusions_say_they_are_external():
    """⚠️ '왜 뺐는지' 가 적혀 있어야 나중에 사내로 옮겨왔을 때 되돌릴 수 있다."""
    pipe = _pipeline()
    external = [r for r in pipe.EXCLUDED_PAGE_IDS.values() if "외부" in r]
    assert len(external) == 14, f"외부 제외가 14건이어야 한다 (현재 {len(external)})"


def test_pages_we_connected_are_not_excluded():
    """⚠️ 연결에 성공한 것까지 실수로 빼면 안 된다."""
    pipe = _pipeline()
    for pid in ("e522b4283b00827e846a8150716cd5ca",   # 스킨1004 제품 라인업
                "fffe501bc5c049649666962435430429",   # [GM WEST] 시딩 대시보드
                "2e62b4283b0080f497dbd5d00d8d1ae7",   # EAST1 자료 취합
                "2702b4283b0080378c95fb9701781a4f"):  # Affiliate 업무 캘린더
        assert not pipe._is_excluded(pid), pid


def test_deletion_only_touches_pipeline_owned_pages():
    """⛔ 지우는 쪽이 **남의 것까지 세면 안 된다** (링크 카드 177장을 통째로 지운 사고와 같은 계열).

    삭제 대상은 `local_page_map`(파이프라인이 넣은 것) 기준이다. 그래서:
      - 제외해도 **다른 적재기가 넣은 옛 조각은 남는다** (실측: 잔존 45, 로컬 소유 0)
      - 반대로 파이프라인 소유가 아닌 것을 실수로 지울 위험도 없다
    """
    src = (ROOT / "scripts" / "notion_qdrant_pipeline.py").read_text(encoding="utf-8")
    i = src.index("removed = {pid for pid in set(local_page_map.keys()) - notion_page_ids")
    body = src[i:i + 400]
    assert "teamres-" in body, "링크 카드는 삭제 계산에서 빠져야 한다"


def test_the_correction_is_written_down():
    """⚠️ 처음에 '제외하면 기존 조각도 지워진다' 고 잘못 적었다 — 정정을 남긴다."""
    src = (ROOT / "scripts" / "notion_qdrant_pipeline.py").read_text(encoding="utf-8")
    assert "조건부" in src and "로컬 JSON 에 없어서" in src
