# -*- coding: utf-8 -*-
"""공개 노션 페이지는 **내용이 바뀌면 다시 수집한다** — 붐따 #105 후속 (2026-08-25).

⛔ 지금까지는 한 번 넣으면 **다시는 읽지 않았다** (`ingest_page.py`):

        if existing_last_edited == "" and not force_public:
            return {"status": "skip", "reason": "already indexed"}

   `last_edited_time == ""` 는 공개 링크(notion.site)를 Playwright 로 긁어 넣은
   페이지라는 표식이다. 날짜를 알 수 없으니 증분 판정을 할 수 없어 **통째로 건너뛴** 것이다.

   그 결과 색인이 라이브와 벌어졌다 (2026-08-25 실측, `복리후생`):

        라이브   사내근로복지기금 **40만원** · 야근식대 **15,000원**
        색인     사내근로복지기금   30만원  · 야근식대 문장 없음

   야근 식대 답변이 2023년 FAQ 의 10,000원으로 나간 진짜 이유다 — 15,000원이 적힌
   문서는 색인에 **있었지만 그 문장이 없는 옛 사본**이었다.

⚠️ 날짜가 없어도 **내용이 바뀐 것은 안다.** payload 에 이미 `content_sha256` 이 있다.
   문서 단위 해시를 저장해 두고, 긁은 결과와 비교해 **바뀐 것만** 다시 임베딩한다.
   (스크래핑 1회는 감수한다 — 임베딩·업서트가 비싼 부분이다.)
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    # ⚠️ import 하지 않고 **텍스트로** 읽는다 — 개발 PC 에는 `qdrant_client` 가 없어
    #    import 하면 이 검사가 통째로 skip 된다. skip 되는 검사는 아무것도 안 지킨다.
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


INGEST = "qdrant_db/app/services/ingest_page.py"
STORE = "qdrant_db/app/qdrant/store.py"


def test_public_pages_are_no_longer_skipped_blindly():
    src = _read(INGEST)
    block = src.split("if is_public:")[1].split("if is_inline:")[0]
    assert "already indexed" not in block, "공개 페이지를 내용 확인 없이 건너뛴다"


def test_public_ingest_compares_document_hash():
    src = _read(INGEST)
    block = src.split("def _ingest_public(")[1]
    assert "existing_doc_hash" in block, "색인에 저장된 문서 해시를 받아야 한다"
    assert "sha256(scraped" in block, "긁은 본문의 해시를 내야 한다"
    assert "unchanged" in block, "안 바뀌었으면 skip 사유를 남겨야 한다"


def test_document_hash_is_stored_in_payload():
    """비교하려면 저장돼 있어야 한다."""
    src = _read(INGEST)
    assert '"doc_sha256"' in src.split("def _ingest_markdown(")[1]


def test_store_can_look_up_document_hashes():
    src = _read(STORE)
    assert "def get_page_doc_hashes" in src
    assert "doc_sha256" in src.split("def get_page_doc_hashes")[1]


def test_daily_sync_passes_the_stored_hash():
    """⛔ 해시를 안 넘기면 **매일 전부 다시 임베딩**한다 — 비용도 지연도 그대로 든다."""
    src = _read("qdrant_db/scripts/sync_public_pages.py")
    assert "get_page_doc_hashes" in src
    assert "existing_doc_hash=" in src
