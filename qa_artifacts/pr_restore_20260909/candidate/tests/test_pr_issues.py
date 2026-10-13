"""PR list ingestion: source boundaries, row preservation and recoverable writes."""
from __future__ import annotations

import copy
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.core import pr_issues as pr


HEADER = ["", "구분", "작성월", "작성팀 (내부용)", "지역",
          "요청사항 (해인)", "내용", "비고", "자료"]


def row(content="행사 안내", *, month="2026-09", team="JBT", category="행사",
        region="일본", note="", material="", request=""):
    return ["", category, month, team, region, request, content, note, material]


def values(*rows):
    return [["안내"], [], HEADER, *rows]


class FakeSheets:
    def __init__(self, data):
        self.data = data
        self.requests = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, **kwargs):
        self.requests.append(kwargs)
        return self

    def execute(self):
        if isinstance(self.data, Exception):
            raise self.data
        return {"values": self.data}


class FakeCloud:
    def __init__(self):
        self.points = {}
        self.upserts = []
        self.deletions = []
        self.fail_upserts = 0
        self.fail_deletes = 0
        self.payload_schema = {"team": SimpleNamespace(data_type="keyword")}
        self.index_calls = []
        self.fail_indexes = 0
        self.events = []

    def get_collection(self, *, collection_name):
        return SimpleNamespace(payload_schema=copy.deepcopy(self.payload_schema))

    def create_payload_index(self, *, collection_name, field_name, field_schema, wait):
        assert field_schema == "keyword"
        assert wait is True
        self.index_calls.append(field_name)
        self.events.append(("index", field_name))
        if self.fail_indexes:
            self.fail_indexes -= 1
            raise RuntimeError("payload index creation failed")
        self.payload_schema[field_name] = SimpleNamespace(data_type="keyword")
        return SimpleNamespace(status="completed")

    def retrieve(self, *, collection_name, ids, with_payload, with_vectors):
        return [SimpleNamespace(id=pid, payload=copy.deepcopy(self.points[pid]["payload"]))
                for pid in ids if pid in self.points]

    def upsert(self, *, collection_name, points, wait):
        assert wait is True
        self.events.append(("upsert", len(points)))
        self.upserts.append([str(p.id) for p in points])
        # A timeout may happen after the service has already applied its write.
        for point in points:
            self.points[str(point.id)] = {"id": str(point.id),
                                          "payload": copy.deepcopy(point.payload),
                                          "vector": list(point.vector)}
        if self.fail_upserts:
            self.fail_upserts -= 1
            raise RuntimeError("cloud upsert timed out after applying")
        return SimpleNamespace(status="completed")

    def delete(self, *, collection_name, points_selector, wait):
        assert wait is True
        conditions = points_selector.filter.must
        ids = next(c.has_id for c in conditions if hasattr(c, "has_id"))
        matches = {c.key: c.match.value for c in conditions if hasattr(c, "key")}
        assert matches == {"team": "PR", "source_kind": "pr_issues",
                           "source_sheet_id": pr.SHEET_ID}
        if any(key not in self.payload_schema or self.payload_schema[key].data_type != "keyword"
               for key in matches):
            raise RuntimeError("strict mode: payload index required")
        removed = []
        for pid in ids:
            old = self.points.get(str(pid))
            if old and all(old["payload"].get(k) == v for k, v in matches.items()):
                removed.append(str(pid))
                del self.points[str(pid)]
        self.deletions.append(removed)
        if self.fail_deletes:
            self.fail_deletes -= 1
            raise RuntimeError("cloud delete timed out after applying")
        return SimpleNamespace(status="completed")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    import google
    from app.agents import qdrant_agent
    from app.core import data_freshness
    from datetime import datetime

    sheet = FakeSheets(values(row()))
    cloud = FakeCloud()
    embedded = []
    replies = []

    def embed_content(*, model, contents, config):
        embedded.extend(contents)
        if replies:
            result = replies.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 2.0, 3.0])
                                           for _ in contents])

    monkeypatch.setattr(pr, "LOCAL_JSON", tmp_path / "pr_vectors.json")
    monkeypatch.setattr(pr, "_sheets_service", lambda: sheet)
    monkeypatch.setattr(qdrant_agent, "_get_client", lambda: cloud)
    monkeypatch.setattr(qdrant_agent, "EMBEDDING_DIM", 3)
    genai = ModuleType("google.genai")
    genai.Client = lambda **kwargs: SimpleNamespace(models=SimpleNamespace(embed_content=embed_content))
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setattr(google, "genai", genai, raising=False)
    qdrant = ModuleType("qdrant_client")
    qdrant.models = ModuleType("qdrant_client.models")
    for name in ("PointStruct", "FieldCondition", "Filter", "FilterSelector", "HasIdCondition", "MatchValue"):
        setattr(qdrant.models, name, SimpleNamespace)
    qdrant.models.PayloadSchemaType = SimpleNamespace(KEYWORD="keyword")
    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant)
    monkeypatch.setitem(sys.modules, "qdrant_client.models", qdrant.models)
    monkeypatch.setattr(data_freshness, "_drive_modified", lambda _: datetime(2026, 9, 9, 10))
    return SimpleNamespace(sheet=sheet, cloud=cloud, embedded=embedded,
                           replies=replies, tmp_path=tmp_path)


def manifest():
    return json.loads(pr.LOCAL_JSON.read_text(encoding="utf-8"))


def test_header_moves_and_content_column_link_follows_it():
    shifted = [["안내"]] * 25 + [["지역", "내용", "작성팀 (내부용)", "구분", "작성월"],
                                  ["일본", "도쿄 행사", "JBT", "행사", "2026-9"]]
    item, = pr.parse_rows(shifted)
    assert item["issue_month"] == "2026-09"
    assert item["row_number"] == 27
    assert item["page_url"] == pr.SHEET_URL + "&range=B27"
    assert item["chunk_index"] == 0
    assert "일본사업팀" in item["text"]


def test_header_requires_content_and_axes_in_same_row():
    with pytest.raises(ValueError, match="헤더"):
        pr.parse_rows([["구분"], ["내용", "작성월", "작성팀"]])


def test_month_exact_inferred_unknown_and_year_sections():
    parsed = pr.parse_rows(values(
        row(month="2024-12"), ["", "2025년"],
        row(month="8월"), row(month="지영님"), row(month=""),
        ["", "", "26년 1월 이슈"], row(month="2월 이슈"),
        row(month="2026-13"), row(month="2026-11"),
    ))
    assert [p["issue_month"] for p in parsed] == [
        "2024-12", "2025-08", None, None, "2026-02", None, "2026-11"]
    assert [p["month_inferred"] for p in parsed] == [False, True, False, False, True, False, False]
    assert parsed[1]["month_raw"] == "8월"
    assert "작성월 추정" in parsed[1]["text"]
    assert "작성월 미상" in parsed[2]["text"]


def test_month_without_known_year_remains_unknown():
    item, = pr.parse_rows(values(row(month="8월")))
    assert item["issue_month"] is None
    assert item["month_inferred"] is False


def test_empty_content_is_excluded_but_long_notes_are_searchable():
    items = pr.parse_rows(values(row(" ", note="메모만"), row("출시", note="긴 설명 " * 100)))
    assert len(items) == 1
    assert items[0]["text"].count("긴 설명") == 100


def test_unknown_team_retained_and_material_never_in_embedding_text():
    item, = pr.parse_rows(values(row(team="KM + DD", material="https://private.example/material")))
    assert item["author_team"] == "KM + DD"
    assert item["team"] == "PR"
    assert item["author_team_known"] is False
    assert "KM + DD" in item["text"]
    assert item["materials"] == "https://private.example/material"
    assert "private.example" not in item["text"]
    assert item["source_kind"] == "pr_issues"
    assert item["source_sheet_id"] == pr.SHEET_ID


@pytest.mark.parametrize("team,label", [("JBT", "일본사업팀"), ("b2b2", "영업2팀"),
                                       ("GM EAST", "글로벌마케팅 동부"), ("BCM", "브랜드커뮤니케이션팀")])
def test_known_team_has_existing_korean_label(team, label):
    item, = pr.parse_rows(values(row(team=team)))
    assert item["author_team"] == team
    assert item["author_team_known"] is True
    assert label in item["text"]


def test_fetch_only_explicit_allowed_tab():
    service = FakeSheets(values(row()))
    with pytest.raises(ValueError, match="허용되지 않은.*지정한 탭"):
        pr._fetch(service, "임의 작업지")
    assert service.requests == []
    assert pr._fetch(service, "리스트") == values(row())
    assert service.requests[0]["spreadsheetId"] == pr.SHEET_ID
    assert service.requests[0]["range"].startswith("'리스트'!")
    assert pr.ALLOWED_TABS == frozenset({"리스트"})


def test_initial_sync_duplicate_rows_not_lost_and_rerun_reuses_vectors(runtime):
    runtime.sheet.data = values(row(), row())
    first = pr.sync_pr_issues()
    initial = manifest()
    assert first["written"] == first["rows"] == 2
    assert len(initial["points"]) == len(runtime.cloud.points) == 2
    assert len({p["id"] for p in initial["points"]}) == 2
    assert len(runtime.embedded) == 1  # Duplicate text has the same embedding.
    second = pr.sync_pr_issues()
    assert second["written"] == 0
    assert second["reused"] == 2
    assert len(runtime.embedded) == 1
    assert len(runtime.cloud.upserts) == 1
    assert {p["id"] for p in manifest()["points"]} == {p["id"] for p in initial["points"]}


def test_filter_indexes_created_before_first_point_write_and_existing_indexes_reused(runtime):
    pr.sync_pr_issues()
    assert runtime.cloud.index_calls == ["source_kind", "source_sheet_id"]
    assert runtime.cloud.events[:2] == [("index", "source_kind"), ("index", "source_sheet_id")]
    assert runtime.cloud.events[2][0] == "upsert"
    pr.sync_pr_issues()
    assert runtime.cloud.index_calls == ["source_kind", "source_sheet_id"]


def test_absent_team_index_is_created_with_source_indexes(runtime):
    runtime.cloud.payload_schema = {}
    pr.sync_pr_issues()
    assert runtime.cloud.index_calls == ["team", "source_kind", "source_sheet_id"]


def test_index_creation_failure_preserves_existing_manifest_and_points(runtime):
    pr.sync_pr_issues()
    before = pr.LOCAL_JSON.read_bytes()
    points = copy.deepcopy(runtime.cloud.points)
    old_upserts = len(runtime.cloud.upserts)
    runtime.cloud.payload_schema.pop("source_kind", None)
    runtime.cloud.fail_indexes = 1
    runtime.sheet.data = values(row("변경된 뉴스"))
    with pytest.raises(RuntimeError, match="index creation failed"):
        pr.sync_pr_issues()
    assert pr.LOCAL_JSON.read_bytes() == before
    assert runtime.cloud.points == points
    assert len(runtime.cloud.upserts) == old_upserts
    assert pr.status()["pending_recovery"] is False


def test_existing_incompatible_index_is_not_replaced(runtime):
    runtime.cloud.payload_schema["source_kind"] = SimpleNamespace(data_type="text")
    with pytest.raises(ValueError, match="인덱스.*keyword"):
        pr.sync_pr_issues()
    assert runtime.cloud.index_calls == []
    assert runtime.cloud.payload_schema["source_kind"].data_type == "text"
    assert runtime.cloud.points == {}
    assert not pr.LOCAL_JSON.exists()


def test_row_insertion_reuses_vector_and_updates_source_row_link(runtime):
    pr.sync_pr_issues()
    old_id = manifest()["points"][0]["id"]
    runtime.sheet.data = values(row("새 소식"), row())
    result = pr.sync_pr_issues()
    existing = next(p for p in manifest()["points"] if p["id"] == old_id)
    assert result["embedded"] == 1
    assert result["reused"] == 1
    assert existing["payload"]["row_number"] == 5
    assert existing["payload"]["page_url"].endswith("&range=G5")
    assert runtime.cloud.points[old_id]["payload"] == existing["payload"]


def test_material_only_change_reuses_vector(runtime):
    pr.sync_pr_issues()
    old_id = manifest()["points"][0]["id"]
    runtime.sheet.data = values(row(material="https://example.com/new"))
    result = pr.sync_pr_issues()
    assert result["embedded"] == 0
    assert result["reused"] == 1
    assert old_id not in runtime.cloud.points
    assert len(runtime.embedded) == 1


def test_sync_repairs_missing_cloud_point_without_reembedding(runtime):
    pr.sync_pr_issues()
    runtime.cloud.points.clear()
    result = pr.sync_pr_issues()
    assert result["written"] == 1
    assert result["embedded"] == 0
    assert len(runtime.cloud.points) == 1


def test_successful_sync_refreshes_team_counts_seen_by_source_picker(runtime, monkeypatch):
    from app.agents import qdrant_agent

    notion = runtime.tmp_path / "notion_vectors_gemini.json"
    notion.write_text(json.dumps([{"payload": {"team": "JBT"}}]), encoding="utf-8")
    monkeypatch.setattr(qdrant_agent, "_LOCAL_JSON", notion)
    monkeypatch.setattr(qdrant_agent, "_PR_LOCAL_JSON", pr.LOCAL_JSON)
    monkeypatch.setattr(qdrant_agent, "_TEAM_COUNTS", {"JBT": 1, "PR": 0})
    assert qdrant_agent.index_team_counts()["PR"] == 0
    pr.sync_pr_issues()
    assert qdrant_agent.index_team_counts() == {"JBT": 1, "PR": 1}
    runtime.sheet.data.append(row("새 소식"))
    pr.sync_pr_issues()
    assert qdrant_agent.index_team_counts() == {"JBT": 1, "PR": 2}


def test_zero_rows_preserve_manifest_and_cloud(runtime):
    pr.sync_pr_issues()
    before = pr.LOCAL_JSON.read_bytes()
    points = copy.deepcopy(runtime.cloud.points)
    runtime.sheet.data = values(row(""))
    result = pr.sync_pr_issues()
    assert result["empty"] is True
    assert result["ok"] is False
    assert pr.LOCAL_JSON.read_bytes() == before
    assert runtime.cloud.points == points


def test_fetch_failure_preserves_previous_data(runtime):
    pr.sync_pr_issues()
    before = pr.LOCAL_JSON.read_bytes()
    points = copy.deepcopy(runtime.cloud.points)
    runtime.sheet.data = PermissionError("403 access revoked")
    with pytest.raises(PermissionError, match="403"):
        pr.sync_pr_issues()
    assert pr.LOCAL_JSON.read_bytes() == before
    assert runtime.cloud.points == points


def test_embedding_failure_does_not_touch_cloud_or_manifest(runtime):
    pr.sync_pr_issues()
    before = pr.LOCAL_JSON.read_bytes()
    points = copy.deepcopy(runtime.cloud.points)
    runtime.sheet.data = values(row("변경된 뉴스"))
    runtime.replies.append(RuntimeError("embedding unavailable"))
    with pytest.raises(RuntimeError, match="embedding"):
        pr.sync_pr_issues()
    assert pr.LOCAL_JSON.read_bytes() == before
    assert runtime.cloud.points == points


@pytest.mark.parametrize("vectors", [[], [[1, 2]], [[1, float("nan"), 3]], [[0, 0, 0]],
                                     [[1, 2, 3], [1, 2, 3]]])
def test_invalid_embedding_batch_prevents_any_cloud_write(runtime, vectors):
    runtime.replies.append(SimpleNamespace(embeddings=[SimpleNamespace(values=v) for v in vectors]))
    with pytest.raises(ValueError, match="임베딩"):
        pr.sync_pr_issues()
    assert not pr.LOCAL_JSON.exists()
    assert runtime.cloud.points == {}


def test_later_embedding_batch_failure_prevents_earlier_batch_publication(runtime, monkeypatch):
    monkeypatch.setattr(pr, "_BATCH_SIZE", 2)
    runtime.sheet.data = values(row("첫째"), row("둘째"), row("셋째"))
    runtime.replies.extend([
        SimpleNamespace(embeddings=[SimpleNamespace(values=[1, 2, 3]), SimpleNamespace(values=[2, 3, 4])]),
        SimpleNamespace(embeddings=[SimpleNamespace(values=[float("nan"), 2, 3])]),
    ])
    with pytest.raises(ValueError, match="임베딩"):
        pr.sync_pr_issues()
    assert len(runtime.embedded) == 3
    assert runtime.cloud.upserts == []
    assert not pr.LOCAL_JSON.exists()


@pytest.mark.parametrize("operation", ["upsert", "delete"])
def test_cloud_timeout_after_apply_rolls_back_previous_snapshot(runtime, operation):
    pr.sync_pr_issues()
    before = pr.LOCAL_JSON.read_bytes()
    points = copy.deepcopy(runtime.cloud.points)
    runtime.sheet.data = values(row("변경된 뉴스"))
    if operation == "upsert":
        runtime.cloud.fail_upserts = 1
    else:
        runtime.cloud.fail_deletes = 1
    with pytest.raises(RuntimeError, match="timed out"):
        pr.sync_pr_issues()
    assert pr.LOCAL_JSON.read_bytes() == before
    assert runtime.cloud.points == points
    assert pr.status()["pending_recovery"] is False


def test_failed_rollback_is_reported_and_recovered_on_next_sync(runtime):
    pr.sync_pr_issues()
    before = pr.LOCAL_JSON.read_bytes()
    runtime.sheet.data = values(row("변경된 뉴스"))
    runtime.cloud.fail_upserts = 2
    with pytest.raises(RuntimeError, match="timed out"):
        pr.sync_pr_issues()
    assert pr.LOCAL_JSON.read_bytes() == before
    assert pr.status()["pending_recovery"] is True
    runtime.sheet.data = values(row())
    pr.sync_pr_issues()
    assert pr.status()["pending_recovery"] is False
    assert len(runtime.cloud.points) == 1
    assert {p["id"] for p in manifest()["points"]} == set(runtime.cloud.points)


def test_missing_filter_indexes_are_restored_before_pending_cloud_recovery(runtime):
    pr.sync_pr_issues()
    runtime.sheet.data = values(row("변경된 뉴스"))
    runtime.cloud.fail_upserts = 2
    with pytest.raises(RuntimeError, match="timed out"):
        pr.sync_pr_issues()
    runtime.cloud.payload_schema = {"team": SimpleNamespace(data_type="keyword")}
    runtime.cloud.index_calls.clear()
    runtime.cloud.events.clear()
    runtime.sheet.data = values(row())
    pr.sync_pr_issues()
    assert runtime.cloud.events[:2] == [("index", "source_kind"), ("index", "source_sheet_id")]
    assert pr.status()["pending_recovery"] is False
    assert len(runtime.cloud.points) == 1


def test_dry_run_does_not_create_indexes_even_with_pending_recovery(runtime):
    pr.sync_pr_issues()
    runtime.sheet.data = values(row("변경된 뉴스"))
    runtime.cloud.fail_upserts = 2
    with pytest.raises(RuntimeError, match="timed out"):
        pr.sync_pr_issues()
    before = pr.LOCAL_JSON.read_bytes()
    points = copy.deepcopy(runtime.cloud.points)
    runtime.cloud.payload_schema = {}
    runtime.cloud.index_calls.clear()
    runtime.sheet.data = values(row())
    pr.sync_pr_issues(dry_run=True)
    assert runtime.cloud.index_calls == []
    assert runtime.cloud.payload_schema == {}
    assert pr.LOCAL_JSON.read_bytes() == before
    assert runtime.cloud.points == points
    assert pr.status()["pending_recovery"] is True


@pytest.mark.parametrize("source_failure", [False, True])
def test_pending_recovery_restores_previous_points_before_empty_or_failed_source(runtime, source_failure):
    pr.sync_pr_issues()
    before = pr.LOCAL_JSON.read_bytes()
    points = copy.deepcopy(runtime.cloud.points)
    runtime.sheet.data = values(row("변경된 뉴스"))
    runtime.cloud.fail_upserts = 2
    with pytest.raises(RuntimeError, match="timed out"):
        pr.sync_pr_issues()
    runtime.cloud.payload_schema = {}
    runtime.cloud.index_calls.clear()
    runtime.sheet.data = PermissionError("403") if source_failure else values(row(""))
    if source_failure:
        with pytest.raises(PermissionError, match="403"):
            pr.sync_pr_issues()
    else:
        assert pr.sync_pr_issues()["empty"] is True
    assert runtime.cloud.index_calls == ["team", "source_kind", "source_sheet_id"]
    assert pr.LOCAL_JSON.read_bytes() == before
    assert runtime.cloud.points == points
    assert pr.status()["pending_recovery"] is False


def test_empty_source_without_pending_recovery_does_not_create_indexes(runtime):
    runtime.cloud.payload_schema = {}
    runtime.sheet.data = values(row(""))
    assert pr.sync_pr_issues()["empty"] is True
    assert runtime.cloud.index_calls == []
    assert runtime.cloud.payload_schema == {}
    assert not pr.LOCAL_JSON.exists()


def test_manifest_replace_failure_rolls_back_cloud(runtime, monkeypatch):
    pr.sync_pr_issues()
    before = pr.LOCAL_JSON.read_bytes()
    points = copy.deepcopy(runtime.cloud.points)
    runtime.sheet.data = values(row("변경된 뉴스"))
    real_replace = pr.os.replace

    def fail_manifest(source, target):
        if target == pr.LOCAL_JSON:
            raise OSError("manifest disk failure")
        return real_replace(source, target)

    monkeypatch.setattr(pr.os, "replace", fail_manifest)
    with pytest.raises(OSError, match="disk failure"):
        pr.sync_pr_issues()
    assert pr.LOCAL_JSON.read_bytes() == before
    assert runtime.cloud.points == points


def test_cleanup_only_deletes_previously_owned_pr_points(runtime):
    pr.sync_pr_issues()
    old_id = manifest()["points"][0]["id"]
    foreign = {"id": "notion-document", "vector": [1.0, 2.0, 3.0],
               "payload": {"team": "JBT", "source_kind": "notion"}}
    unowned = {"id": "unowned-pr-document", "vector": [1.0, 2.0, 3.0],
               "payload": {"team": "PR", "source_kind": "pr_issues", "source_sheet_id": pr.SHEET_ID}}
    runtime.cloud.points[foreign["id"]] = copy.deepcopy(foreign)
    runtime.cloud.points[unowned["id"]] = copy.deepcopy(unowned)
    runtime.sheet.data = values(row("변경된 뉴스"))
    pr.sync_pr_issues()
    assert old_id not in runtime.cloud.points
    assert runtime.cloud.points[foreign["id"]] == foreign
    assert runtime.cloud.points[unowned["id"]] == unowned


def test_foreign_payload_at_old_owned_id_is_not_deleted(runtime):
    pr.sync_pr_issues()
    old_id = manifest()["points"][0]["id"]
    runtime.cloud.points[old_id]["payload"] = {"team": "JBT", "source_kind": "notion"}
    foreign = copy.deepcopy(runtime.cloud.points[old_id])
    runtime.sheet.data = values(row("변경된 뉴스"))
    pr.sync_pr_issues()
    assert runtime.cloud.points[old_id] == foreign


def test_foreign_payload_at_candidate_id_is_never_overwritten(runtime):
    pr.sync_pr_issues()
    pid = manifest()["points"][0]["id"]
    runtime.cloud.points[pid]["payload"] = {"team": "JBT", "source_kind": "notion"}
    foreign = copy.deepcopy(runtime.cloud.points[pid])
    with pytest.raises(ValueError, match="소유"):
        pr.sync_pr_issues()
    assert runtime.cloud.points[pid] == foreign


def test_mass_removal_is_refused_before_embedding_or_cloud_write(runtime):
    runtime.sheet.data = values(*(row(str(i)) for i in range(30)))
    pr.sync_pr_issues()
    before = pr.LOCAL_JSON.read_bytes()
    points = copy.deepcopy(runtime.cloud.points)
    embeds = len(runtime.embedded)
    runtime.sheet.data = values(row("완전히 달라진 한 행"))
    result = pr.sync_pr_issues()
    assert result["cleanup_refused"] == 30
    assert result["ok"] is False
    assert len(runtime.embedded) == embeds
    assert pr.LOCAL_JSON.read_bytes() == before
    assert runtime.cloud.points == points


def test_dry_run_has_stats_but_no_embeddings_cloud_or_manifest(runtime):
    result = pr.sync_pr_issues(dry_run=True)
    assert result["rows"] == 1
    assert result["written"] == 0
    assert runtime.embedded == []
    assert runtime.cloud.upserts == []
    assert runtime.cloud.index_calls == []
    assert not pr.LOCAL_JSON.exists()


def test_status_is_local_and_keeps_initial_unknown_month_baseline(runtime, monkeypatch):
    runtime.sheet.data = values(row(month="2026-8"), row(month="9월", team="BOP"), row(month=""))
    pr.sync_pr_issues()
    runtime.sheet.data.append(row("새 소식", month=""))
    pr.sync_pr_issues()
    monkeypatch.setattr(pr, "_sheets_service", lambda: pytest.fail("status must not read Sheets"))
    result = pr.status()
    assert result["count"] == 4
    assert result["unknown_teams"] == {"BOP": 1}
    assert result["month_unknown"] == 2
    assert result["month_inferred"] == 1
    assert result["baseline_month_unknown"] == 1
    assert result["baseline_month_inferred"] == 1
    assert result["sheet_updated_at"] == "2026-09-09T10:00:00"
    assert result["synced_at"]


def test_metadata_unavailable_is_explicit_status(runtime, monkeypatch):
    from app.core import data_freshness
    monkeypatch.setattr(data_freshness, "_drive_modified", lambda _: None)
    pr.sync_pr_issues()
    result = pr.status()
    assert result["sheet_updated_at"] is None
    assert result["sheet_metadata_available"] is False
    assert "확인 불가" in result["sheet_metadata_error"]


def test_corrupt_or_foreign_manifest_never_becomes_deletion_authority(runtime):
    pr.LOCAL_JSON.write_text(json.dumps({"points": [{"id": "foreign", "payload": {"team": "JBT"}}]}))
    before = pr.LOCAL_JSON.read_bytes()
    with pytest.raises(ValueError, match="소유|manifest"):
        pr.sync_pr_issues()
    assert pr.LOCAL_JSON.read_bytes() == before
    assert runtime.cloud.upserts == []
    assert pr.status()["error"]


def test_notion_file_ownership_is_separate(runtime):
    notion = runtime.tmp_path / "notion_vectors_gemini.json"
    notion.write_text('[{"id":"original-notion-point"}]', encoding="utf-8")
    pr.sync_pr_issues()
    assert notion.read_text(encoding="utf-8") == '[{"id":"original-notion-point"}]'
    assert pr.LOCAL_JSON.name == "pr_vectors.json"


def test_concurrent_sync_is_rejected_and_lock_releases_after_error(runtime):
    with pr._sync_lock():
        with pytest.raises(RuntimeError, match="동기화.*실행 중"):
            pr.sync_pr_issues()
    runtime.sheet.data = PermissionError("failed")
    with pytest.raises(PermissionError):
        pr.sync_pr_issues()
    runtime.sheet.data = values(row())
    assert pr.sync_pr_issues()["written"] == 1
