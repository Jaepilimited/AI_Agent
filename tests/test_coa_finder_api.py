"""COA 찾기 엔드포인트."""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.auth_middleware import get_current_user
from app.core import coa_finder as cf


class _User:
    email = "tester@skin1004korea.com"
    role = "user"


@pytest.fixture
def client():
    # ⛔ 개발 PC `.env` 의 MIGRATED_REDIRECT_URL 이 앱 생성 시점에 리다이렉트
    #    미들웨어로 박힌다 (tests/test_router.py 와 같은 원인). 이미 만들어진
    #    `app.main.app` 싱글턴을 그대로 쓰면 모든 요청이 307 로 튕기므로,
    #    설정을 끈 뒤 새로 만든 앱으로 테스트한다.
    from app.config import get_settings
    os.environ["MIGRATED_REDIRECT_URL"] = ""
    get_settings.cache_clear()
    from app.main import create_app
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _User()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_search_requires_google_connection(client):
    """⛔ 미연결을 '전부 없음' 으로 보여주면 안 된다."""
    with patch("app.api.coa_finder_api._credentials", return_value=None):
        r = client.post("/api/coa-finder/search",
                        data={"pasted": "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\n"})
    assert r.status_code == 409
    assert "구글" in r.json()["detail"]


def test_search_streams_one_event_per_row(client):
    payload = "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\nB\t크림\t416022\n"
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})
    assert r.status_code == 200
    body = r.text
    assert body.count("event: row") == 2
    assert "event: done" in body

    # 이벤트 개수만 세면 순서가 뒤바뀌거나 필드가 빈 통과도 놓친다 — 실제 payload 를 본다
    row_lines = [ln for blk in body.split("\n\n") if blk.startswith("event: row")
                 for ln in blk.splitlines() if ln.startswith("data: ")]
    rows = [json.loads(ln[len("data: "):]) for ln in row_lines]
    assert [r["sku"] for r in rows] == ["A", "B"]  # 입력 순서 그대로
    assert rows[0]["lot"] == "FE103C"
    assert rows[0]["coa"]["status"]
    assert rows[0]["msds"]["status"]


def test_search_stream_reports_error_without_a_fake_done(client):
    """⛔ 잘린 스트림이 완료로 보이면 안 된다."""
    payload = "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\nB\t크림\t416022\n"

    def _dies_after_one_row(creds, rows, search=None, max_workers=8):
        row = cf.Row(sku="A", description="앰플", lot="FE103C", line_no=2)
        yield cf.Result(row=row, coa=cf.Verdict(cf.FOUND, (), ""),
                        msds=cf.Verdict(cf.NONE, (), ""))
        raise RuntimeError("drive timeout")

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api.cf.find_all", side_effect=_dies_after_one_row):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})

    assert r.status_code == 200
    body = r.text
    assert body.count("event: row") == 1
    assert "event: error" in body
    assert "event: done" not in body
    error_line = next(ln for ln in body.splitlines() if ln.startswith("data: ")
                      and "completed" in ln)
    error_payload = json.loads(error_line[len("data: "):])
    assert error_payload["completed"] == 1
    assert error_payload["total"] == 2


def test_search_rejects_oversized_upload(client):
    """⛔ 상한을 넘는 순간 끊는다 — 다 받은 뒤에 재는 게 아니다.

    그래서 정확한 총량이 아니라 '적어도 이만큼' 이라고만 말한다.
    """
    oversized = b"x" * (5 * 1024 * 1024 + 1024)
    r = client.post(
        "/api/coa-finder/search",
        files={"file": ("big.xlsx", oversized, "application/octet-stream")},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "5MB" in detail          # 상한
    assert "적어도" in detail        # 잰 만큼만 말한다 — 확인 안 된 총량을 주장하지 않는다


def test_search_rejects_oversized_pasted_text(client):
    """⛔ 파일만 막고 붙여넣기는 그대로 두면 같은 구멍이 방식만 바뀌어 남는다."""
    oversized = "A" * (5 * 1024 * 1024 + 1024)
    r = client.post("/api/coa-finder/search", data={"pasted": oversized})
    assert r.status_code == 400
    assert "5MB" in r.json()["detail"]


def test_search_reports_missing_header_column(client):
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()):
        r = client.post("/api/coa-finder/search",
                        data={"pasted": "SKU\tDESCRIPTION\nA\t앰플\n"})
    assert r.status_code == 400
    assert "LOT" in r.json()["detail"]


def test_page_is_served(client):
    r = client.get("/coa-finder")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_download_rejects_too_many_items(client):
    items = [{"file_id": str(i), "sku": "A", "lot": "L", "name": "x.pdf"}
             for i in range(201)]
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()):
        r = client.post("/api/coa-finder/download", json={"items": items})
    assert r.status_code == 400
    assert "200" in r.json()["detail"]


def test_download_records_files_it_could_not_fetch(client):
    """⛔ 조용히 빠지면 아무도 모른다 — 못 받은 목록을 ZIP 안에 남긴다."""
    import io as _io
    import zipfile

    # status 는 화면의 판정이다 — 확정된 행이라야 이름에 확인필요 접두가 안 붙는다
    items = [{"file_id": "ok", "sku": "A", "lot": "FE103C", "name": "a.pdf",
              "status": "찾음", "kind": "coa"},
             {"file_id": "bad", "sku": "B", "lot": "416022", "name": "b.pdf",
              "status": "찾음", "kind": "coa"}]

    def fake_fetch(creds, file_id, budget):
        if file_id == "bad":
            raise RuntimeError("403")
        return b"%PDF-1.4 fake"

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fake_fetch):
        r = client.post("/api/coa-finder/download", json={"items": items})

    assert r.status_code == 200
    zf = zipfile.ZipFile(_io.BytesIO(r.content))
    names = zf.namelist()
    assert "A_FE103C_a.pdf" in names
    assert "_받지못한_목록.txt" in names
    assert "416022" in zf.read("_받지못한_목록.txt").decode("utf-8")


def test_download_rejects_empty_file_id(client):
    """⛔ 빈 file_id 를 그냥 넘기면 ZIP 안에 이름 없는 항목이 생긴다."""
    items = [{"file_id": "  ", "sku": "A", "lot": "L", "name": "x.pdf"}]
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()):
        r = client.post("/api/coa-finder/download", json={"items": items})
    assert r.status_code == 400
    assert "file_id" in r.json()["detail"]


def test_download_reports_cap_and_skips_remaining(client):
    """⛔ 용량 상한을 넘긴 뒤 남은 항목이 조용히 사라지면 안 된다."""
    import io as _io
    import zipfile

    items = [
        {"file_id": "1", "sku": "A", "lot": "L1", "name": "a.pdf", "status": "찾음"},
        {"file_id": "2", "sku": "B", "lot": "L2", "name": "b.pdf", "status": "찾음"},
        {"file_id": "3", "sku": "C", "lot": "L3", "name": "c.pdf", "status": "찾음"},
    ]

    def fake_fetch(creds, file_id, budget):
        # 실제로 500MB 를 만들지 않는다 — 상한 자체를 낮춰서 같은 경로를 튄다.
        # 예산은 진짜 _fetch_file 처럼 청크 사이에서 본다 (다 받은 뒤가 아니다)
        from app.api.coa_finder_api import _DownloadBudgetExceeded
        if 40 > budget:
            raise _DownloadBudgetExceeded(40)
        return b"x" * 40

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fake_fetch), \
         patch("app.api.coa_finder_api._MAX_DOWNLOAD_BYTES", 50):
        r = client.post("/api/coa-finder/download", json={"items": items})

    assert r.status_code == 200
    zf = zipfile.ZipFile(_io.BytesIO(r.content))
    names = zf.namelist()
    assert "A_L1_a.pdf" in names          # 40바이트 — 상한 50 안에 든다
    assert "B_L2_b.pdf" not in names      # 누적 80 > 50 — 여기서 상한을 넘긴다
    assert "C_L3_c.pdf" not in names      # 상한 넘긴 뒤라 건드리지 않는다
    assert "_받지못한_목록.txt" in names
    note = zf.read("_받지못한_목록.txt").decode("utf-8")
    assert "L2" in note                   # 상한을 넘긴 항목 자신
    assert "L3" in note                   # 넘긴 뒤 건너뛴 항목도 각자 한 줄씩


def _zip_of(client, items):
    import io as _io
    import zipfile

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file",
               side_effect=lambda creds, file_id, budget: b"%PDF-1.4 fake"):
        r = client.post("/api/coa-finder/download", json={"items": items})
    assert r.status_code == 200
    return zipfile.ZipFile(_io.BytesIO(r.content))


def test_download_marks_unconfirmed_verdict_in_the_zip(client):
    """⛔ 화면의 '확인필요' 가 ZIP 에서 사라지면 확정 문서로 둔갑한다.

    롯트 FE161 을 물었는데 드라이브 파일은 FE1615 인 실측 사례 — 화면은
    확인필요라고 말하지만 ZIP 은 그 파일이 FE161 것이라고 주장했다.
    ZIP 은 그대로 고객에게 전달되는 산출물이라 그 시점에 경고가 없으면 없는 것이다.
    """
    zf = _zip_of(client, [{
        "file_id": "1", "sku": "EUSKA022", "lot": "FE161",
        "name": "COA_10116720_SUN SERUM_FE1615_15643EA.pdf",
        "status": "확인필요", "kind": "coa",
    }])
    names = zf.namelist()
    assert "확인필요_EUSKA022_FE161_COA_10116720_SUN SERUM_FE1615_15643EA.pdf" in names
    assert "_확인필요_목록.txt" in names
    note = zf.read("_확인필요_목록.txt").decode("utf-8")
    assert "EUSKA022" in note and "FE161" in note
    assert "FE1615" in note                     # 원본 파일명
    assert "확인필요" in note                    # 왜 확정이 아닌지
    # ⛔ 실패와 불확실은 다른 목록이다 — 섞으면 둘 다 안 읽힌다
    assert "_받지못한_목록.txt" not in names


def test_download_treats_missing_status_as_unconfirmed(client):
    """⛔ 엔드포인트는 임의 JSON 을 받는다 — 상태가 없으면 확정으로 보면 안 된다."""
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "FE103C",
                           "name": "a.pdf"}])
    assert "확인필요_A_FE103C_a.pdf" in zf.namelist()
    assert "_확인필요_목록.txt" in zf.namelist()


def test_download_treats_unknown_status_as_unconfirmed(client):
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "FE103C",
                           "name": "a.pdf", "status": "OK"}])
    assert "확인필요_A_FE103C_a.pdf" in zf.namelist()


def test_download_keeps_confirmed_items_unprefixed(client):
    """반대 방향 — 확정된 문서까지 확인필요로 적으면 경고가 소음이 된다."""
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "FE103C",
                           "name": "a.pdf", "status": "찾음", "kind": "coa"}])
    names = zf.namelist()
    assert "A_FE103C_a.pdf" in names
    assert "_확인필요_목록.txt" not in names


def test_download_msds_entry_carries_no_lot(client):
    """⛔ MSDS 는 제품 단위 문서라 롯트가 없다 — 이름에 롯트를 심으면
    롯트가 맞는 문서인 것처럼 보인다 (스펙이 명시적으로 금지한 것)."""
    zf = _zip_of(client, [{
        "file_id": "1", "sku": "EUSKA022", "lot": "FE103C",
        "name": "SKIN1004 Madagascar Centella Cream 75ML_MSDS(WERCS).pdf",
        "status": "찾음", "kind": "msds",
    }])
    entry = next(n for n in zf.namelist() if not n.startswith("_"))
    assert "FE103C" not in entry
    assert entry.startswith("EUSKA022_MSDS_")


def test_download_unknown_kind_is_treated_as_coa(client):
    """kind 가 없으면 롯트를 지니는 쪽(COA)으로 본다 — C1 규칙이 그대로 지킨다."""
    zf = _zip_of(client, [{"file_id": "1", "sku": "A", "lot": "FE103C",
                           "name": "a.pdf", "status": "찾음"}])
    assert "A_FE103C_a.pdf" in zf.namelist()


def _done_payload(body):
    block = next(b for b in body.split("\n\n") if b.startswith("event: done"))
    line = next(ln for ln in block.splitlines() if ln.startswith("data: "))
    return json.loads(line[len("data: "):])


def test_summary_reports_rows_skipped_for_empty_sku(client):
    """⛔ 버릴 거면 버렸다고 말해야 한다 — 화면에 아무 표시가 없었다."""
    payload = ("SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\n\t크림\t416022\n")
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})
    assert r.status_code == 200
    assert r.text.count("event: row") == 1
    assert _done_payload(r.text)["skipped_no_sku"] == 1


def test_summary_counts_query_failures_separately(client):
    """⛔ 100행 중 8행이 조회 실패했는데 '확인필요 8' 로만 보이면 사용자는
    그것이 애매한 매칭인지 조회가 안 된 것인지 알 수 없다."""
    payload = "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\nB\t크림\t416022\n"

    def dying_search(creds, query, **k):
        raise RuntimeError("drive down")

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", side_effect=dying_search):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})

    done = _done_payload(r.text)
    assert done["counts"]["조회실패"] == 2      # COA 쪽
    assert done["counts"].get("확인필요", 0) == 0
    assert done["msds_failed"] == 2             # ⛔ MSDS 실패도 세지 않고 있었다


def test_fetch_file_aborts_between_chunks_once_the_budget_is_gone():
    """⛔ 다 받은 뒤에 재면 이미 통째로 메모리에 들고 있는 것이다. file_id 는
    클라이언트가 주는 임의 값이라 '실측 COA 는 350KB' 라는 전제가 성립하지 않는다."""
    from app.api import coa_finder_api as api

    class _Downloader:
        """청크마다 40바이트씩 준다. 예산을 안 보면 200바이트를 다 받는다."""
        def __init__(self, buf, req, chunksize=None):
            self._buf, self._n = buf, 0

        def next_chunk(self):
            self._n += 1
            self._buf.write(b"x" * 40)
            return None, self._n >= 5

    with patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("googleapiclient.http.MediaIoBaseDownload", _Downloader):
        with pytest.raises(api._DownloadBudgetExceeded) as e:
            api._fetch_file(MagicMock(), "huge", 50)

    # 잰 만큼만 말한다 — 확인하지 않은 총 크기를 주장하지 않는다
    assert e.value.received == 80


def test_fetch_file_returns_a_file_that_fits_the_budget():
    """반대 방향 — 예산 안에 드는 파일까지 끊으면 기능이 죽는다."""
    from app.api import coa_finder_api as api

    class _Downloader:
        def __init__(self, buf, req, chunksize=None):
            self._buf, self._n = buf, 0

        def next_chunk(self):
            self._n += 1
            self._buf.write(b"x" * 40)
            return None, self._n >= 2

    with patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("googleapiclient.http.MediaIoBaseDownload", _Downloader):
        assert api._fetch_file(MagicMock(), "ok", 500) == b"x" * 80


def test_download_records_an_item_that_blew_the_budget(client):
    """중단한 항목이 조용히 빠지면 안 된다 — 못 받은 목록에 사유가 남아야 한다."""
    import io as _io
    import zipfile

    from app.api import coa_finder_api as api

    items = [{"file_id": "huge", "sku": "A", "lot": "L1", "name": "big.pdf",
              "status": "찾음"}]

    def fake_fetch(creds, file_id, budget):
        raise api._DownloadBudgetExceeded(80)

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fake_fetch):
        r = client.post("/api/coa-finder/download", json={"items": items})

    assert r.status_code == 200
    zf = zipfile.ZipFile(_io.BytesIO(r.content))
    assert zf.namelist() == ["_받지못한_목록.txt"]
    note = zf.read("_받지못한_목록.txt").decode("utf-8")
    assert "L1" in note
    assert "적어도" in note        # 재지 않은 총 크기를 주장하지 않는다


_JS = "app/static/coa_finder.js"


def _js_source():
    with open(_JS, encoding="utf-8") as fh:
        return fh.read()


def test_frontend_sends_verdict_status_and_kind_with_each_item():
    """⛔ 프론트가 판정을 안 실어 보내면 서버는 전부 확인필요로 내보낸다 —
    맞는 방향으로 무너지지만 확정 문서까지 경고가 붙어 경고가 소음이 된다."""
    src = _js_source()
    assert "dataset.status" in src
    assert "dataset.kind" in src
    assert "status:" in src and "kind:" in src


def test_frontend_summary_states_what_was_searched():
    """⛔ '없음 41' 만 보이면 사용자는 도구가 고장났다고 읽는다."""
    src = _js_source()
    assert "COA" in src                      # 개수가 COA 기준임을 밝힌다
    assert "파일명" in src and "본문" in src   # 탐색 범위


def test_frontend_warns_when_every_row_is_none():
    """전 행이 없음이면 조용한 전멸을 시끄럽게 만든다."""
    src = _js_source()
    assert "cf-allnone" in src


def test_frontend_knows_the_query_failure_status():
    """⛔ 상태 표에 없으면 조회실패 행이 아무 표시 없이 그려진다."""
    assert "조회실패" in _js_source()


def test_frontend_states_rows_skipped_for_empty_sku():
    src = _js_source()
    assert "skipped_no_sku" in src
    assert "cf-skipped" in src


def test_zip_name_truncates_by_utf8_bytes_not_characters():
    """⛔ 180자를 그대로 자르면 ext4·macOS 의 255바이트 상한을 넘을 수 있다."""
    from app.api.coa_finder_api import DownloadItem, _MAX_ZIP_NAME_BYTES, _zip_name

    item = DownloadItem(file_id="x", sku="A", lot="L", name="가" * 200)
    name = _zip_name(item)
    encoded = name.encode("utf-8")
    assert len(encoded) <= _MAX_ZIP_NAME_BYTES
    # 글자 중간이 잘렸으면 여기서 디코딩 오류가 나거나 원문과 달라진다
    assert encoded.decode("utf-8") == name
