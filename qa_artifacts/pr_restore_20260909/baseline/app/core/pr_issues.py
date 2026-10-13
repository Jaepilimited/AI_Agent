"""Read the approved PR list tab into its own recoverable Qdrant manifest.

Each nonempty content row remains a separate chunk. The local manifest owns only
this source's points; it never shares a file or deletion scope with Notion.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

import structlog

logger = structlog.get_logger(__name__)

SHEET_ID = "1MLuwW74Nvv33YW6cVG5qW4uJLGuj3miUqcky9r20A8c"
SHEET_TAB = "리스트"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=0"
ALLOWED_TABS = frozenset({SHEET_TAB})
LOCAL_JSON = Path(__file__).resolve().parents[2] / "data" / "pr_vectors.json"
FRESHNESS_MAX_HOURS = 30
_SOURCE_KIND = "pr_issues"
_BATCH_SIZE = 50
_HEADERS = {
    "구분": "category", "작성월": "month_raw", "작성팀": "author_team",
    "작성팀 (내부용)": "author_team", "지역": "region", "내용": "content",
    "요청사항 (해인)": "request", "비고": "note", "자료": "materials",
}
_REQUIRED = {"category", "month_raw", "author_team", "region", "content"}
_YEAR_ONLY = re.compile(r"^(20\d{2})\s*년$")
_FULL_MONTH = re.compile(r"^(20\d{2})[-./](\d{1,2})$")
_KOREAN_MONTH = re.compile(
    r"^(20\d{2}|\d{2})\s*년\s*(\d{1,2})\s*월(?:\s*이슈(?:\s*\(마감\))?)?$")
_MONTH_ONLY = re.compile(r"^(\d{1,2})\s*월(?:\s*이슈)?$")


def _cell(value) -> str:
    return "" if value is None else str(value).strip()


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def _team_vocabulary() -> dict[str, str]:
    """Use existing team aliases and verified names; unfamiliar cells stay raw."""
    from app.agents.qdrant_agent import TEAM_MAP
    from app.core.org_structure import TEAM_CODE2KR

    labels = {**TEAM_CODE2KR, "[GM]EAST": "글로벌마케팅 동부", "[GM]WEST": "글로벌마케팅 서부"}
    vocabulary = {}
    for alias, code in TEAM_MAP.items():
        vocabulary[_team_key(alias)] = labels.get(code, "")
        vocabulary[_team_key(code)] = labels.get(code, "")
    for code, korean in TEAM_CODE2KR.items():
        vocabulary[_team_key(code)] = korean
        vocabulary[_team_key(korean)] = korean
    return vocabulary


def _team_key(value: str) -> str:
    # Same spacing/case equivalence as qdrant_agent.resolve_team_filter().
    return value.lower().replace(" ", "").replace("_", "").replace("-", "")


def _column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _month(raw: str, known_year: int | None) -> tuple[str | None, bool, int | None]:
    exact = _FULL_MONTH.fullmatch(raw) or _KOREAN_MONTH.fullmatch(raw)
    if exact:
        year, month = map(int, exact.groups())
        if year < 100:
            year += 2000
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}", False, year
        return None, False, known_year
    short = _MONTH_ONLY.fullmatch(raw)
    if short and known_year is not None and 1 <= int(short[1]) <= 12:
        return f"{known_year:04d}-{int(short[1]):02d}", True, known_year
    return None, False, known_year


def parse_rows(values: list[list]) -> list[dict]:
    """Find the header, preserve one chunk per content row, and label uncertain months."""
    columns = None
    for header_index, header in enumerate(values):
        candidate = {_HEADERS[_cell(label)]: i for i, label in enumerate(header)
                     if _cell(label) in _HEADERS}
        if _REQUIRED <= candidate.keys():
            columns = candidate
            break
    if columns is None:
        raise ValueError("PR 헤더를 찾지 못했다 — 구분·작성월·작성팀·지역·내용 열이 필요하다")

    vocabulary = _team_vocabulary()
    known_year = None
    chunks = []
    occurrences = Counter()
    for row_number, source_row in enumerate(values[header_index + 1:], header_index + 2):
        cells = [_cell(c) for c in source_row]
        while cells and not cells[-1]:
            cells.pop()
        rec = {key: cells[i] if i < len(cells) else "" for key, i in columns.items()}
        if not rec["content"]:
            # Year/month separator rows carry chronology even though they are not chunks.
            year_heading = _YEAR_ONLY.fullmatch(rec["category"]) or _YEAR_ONLY.fullmatch(rec["month_raw"])
            if year_heading:
                known_year = int(year_heading[1])
            _, _, known_year = _month(rec["month_raw"], known_year)
            continue
        issue_month, inferred, known_year = _month(rec["month_raw"], known_year)
        month_label = issue_month or "작성월 미상"
        if inferred:
            month_label += f" (작성월 추정; 원문: {rec['month_raw']})"
        elif issue_month is None and rec["month_raw"]:
            month_label += f" (원문: {rec['month_raw']})"
        team_key = _team_key(rec["author_team"])
        team_label = rec["author_team"] or "작성팀 미상"
        korean = vocabulary.get(team_key)
        if korean and korean != rec["author_team"]:
            team_label += f" ({korean})"
        text_lines = [f"[{rec['category'] or '구분 미상'}] {month_label} · {team_label} · "
                      f"{rec['region'] or '지역 미상'}", rec["content"]]
        for key, label in (("note", "비고"), ("request", "요청사항")):
            if rec.get(key):
                text_lines.append(f"{label}: {rec[key]}")
        text = "\n".join(text_lines)
        # Physical row position is excluded, so inserting a row preserves existing IDs.
        # Include every source cell, then distinguish fully identical duplicate rows.
        identity = _sha([cells, issue_month, inferred])
        occurrence = occurrences[identity]
        occurrences[identity] += 1
        chunks.append({
            "team": "PR", "author_team": rec["author_team"],
            "author_team_known": bool(team_key) and team_key in vocabulary,
            "category": rec["category"], "region": rec["region"],
            "issue_month": issue_month, "month_inferred": inferred,
            "month_raw": rec["month_raw"], "content": rec["content"],
            "note": rec.get("note", ""), "request": rec.get("request", ""),
            "materials": rec.get("materials", ""),
            "page_title": f"PR 이슈 · {month_label} · {rec['category'] or '구분 미상'}",
            "page_url": f"{SHEET_URL}&range={_column_letter(columns['content'] + 1)}{row_number}",
            "source_sheet_id": SHEET_ID, "source_kind": _SOURCE_KIND,
            "source_tab": SHEET_TAB, "text": text,
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "row_sha256": identity, "row_occurrence": occurrence,
            "chunk_index": len(chunks), "row_number": row_number,
        })
    return chunks


def _sheets_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from app.config import get_settings

    creds = Credentials.from_service_account_file(
        get_settings().google_application_credentials,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _fetch(service, tab: str) -> list[list]:
    if tab not in ALLOWED_TABS:
        raise ValueError(f"허용되지 않은 시트 탭: {tab!r}. 지정한 탭만 학습한다 "
                         f"(허용: {sorted(ALLOWED_TABS)})")
    from app.core.retrying import with_retry

    # An open row range prevents silent truncation as the approved list grows.
    return with_retry(
        lambda: service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{tab}'!A:Z").execute().get("values", []),
        what="pr_issues_sheet", attempts=2, first_delay=1.0,
    )


def _read_sheet() -> list[list]:
    return _fetch(_sheets_service(), SHEET_TAB)


def _pending_path() -> Path:
    return LOCAL_JSON.with_suffix(".pending.json")


@contextmanager
def _sync_lock():
    """An OS lock releases on process death; a leftover file cannot wedge the job."""
    LOCAL_JSON.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCAL_JSON.with_suffix(".lock").open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("PR 동기화가 이미 실행 중이다") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("PR 동기화가 이미 실행 중이다") from exc
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _point_id(payload: dict) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                         f"{_SOURCE_KIND}:{SHEET_ID}:{SHEET_TAB}:"
                         f"{payload['row_sha256']}:{payload['row_occurrence']}"))


def _owns(payload: dict) -> bool:
    return (payload.get("team") == "PR" and payload.get("source_kind") == _SOURCE_KIND
            and payload.get("source_sheet_id") == SHEET_ID)


def _valid_vector(vector, dimension: int) -> bool:
    return (isinstance(vector, list) and len(vector) == dimension
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in vector) and any(vector))


def _validate_manifest(data: dict) -> dict:
    if (not isinstance(data, dict) or data.get("source_kind") != _SOURCE_KIND
            or data.get("source_sheet_id") != SHEET_ID or data.get("source_tab") != SHEET_TAB
            or not isinstance(data.get("points"), list)
            or not isinstance(data.get("embedding_dim"), int) or data["embedding_dim"] <= 0):
        raise ValueError("PR manifest 소유권 또는 형식이 잘못되었다")
    ids = set()
    for point in data["points"]:
        payload = point.get("payload", {})
        if (not _owns(payload) or not payload.get("row_sha256")
                or not isinstance(payload.get("row_occurrence"), int)
                or point.get("id") != _point_id(payload) or point["id"] in ids
                or not _valid_vector(point.get("vector"), data["embedding_dim"])
                or hashlib.sha256(payload.get("text", "").encode("utf-8")).hexdigest()
                != payload.get("content_sha256")):
            raise ValueError("PR manifest 포인트 소유권·벡터·행 식별자를 검증하지 못했다")
        ids.add(point["id"])
    return data


def _load_manifest() -> dict:
    if not LOCAL_JSON.exists():
        return {"points": [], "stats": {}}
    return _validate_manifest(json.loads(LOCAL_JSON.read_text(encoding="utf-8")))


def _write_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _row_stats(rows: list[dict]) -> dict:
    months = sorted(p["issue_month"] for p in rows if p["issue_month"])
    return {
        "rows": len(rows), "count": len(rows),
        "unknown_teams": dict(sorted(Counter(p["author_team"] or "(미기재)" for p in rows
                                              if not p["author_team_known"]).items())),
        "month_unknown": sum(p["issue_month"] is None for p in rows),
        "month_inferred": sum(p["month_inferred"] for p in rows),
        "first_month": months[0] if months else None,
        "last_month": months[-1] if months else None,
    }


def _embed_rows(rows: list[dict], old: dict) -> tuple[list[dict], int, int]:
    from app.agents.qdrant_agent import EMBEDDING_DIM, EMBEDDING_MODEL

    cache = {}
    if old.get("embedding_model") == EMBEDDING_MODEL and old.get("embedding_dim") == EMBEDDING_DIM:
        cache = {p["payload"]["content_sha256"]: p["vector"] for p in old["points"]}
    reused = sum(p["content_sha256"] in cache for p in rows)
    needed = {p["content_sha256"]: p["text"] for p in rows if p["content_sha256"] not in cache}
    if needed:
        from google import genai
        from app.config import get_settings
        from app.core.retrying import with_retry

        embedder = genai.Client(api_key=get_settings().gemini_api_key)
        items = list(needed.items())
        for offset in range(0, len(items), _BATCH_SIZE):
            batch = items[offset:offset + _BATCH_SIZE]
            result = with_retry(
                lambda: embedder.models.embed_content(
                    model=EMBEDDING_MODEL, contents=[text for _, text in batch],
                    config={"output_dimensionality": EMBEDDING_DIM}),
                what="pr_issues_embedding", attempts=2, first_delay=1.0,
            )
            embeddings = result.embeddings or []
            if len(embeddings) != len(batch):
                raise ValueError("PR 임베딩 응답 개수가 요청과 다르다")
            for (digest, _), embedding in zip(batch, embeddings):
                vector = list(embedding.values or [])
                if not _valid_vector(vector, EMBEDDING_DIM):
                    raise ValueError("PR 임베딩 차원 또는 숫자 값이 잘못되었다")
                cache[digest] = vector
    points = [{"id": _point_id(payload), "payload": payload,
               "vector": cache[payload["content_sha256"]]} for payload in rows]
    return points, len(needed), reused


def _cloud_payloads(client, ids: list[str]) -> dict:
    from app.agents.qdrant_agent import COLLECTION

    found = {}
    for offset in range(0, len(ids), _BATCH_SIZE):
        for point in client.retrieve(collection_name=COLLECTION, ids=ids[offset:offset + _BATCH_SIZE],
                                     with_payload=True, with_vectors=False):
            found[str(point.id)] = point.payload or {}
    return found


def _completed(result) -> None:
    status_value = getattr(result, "status", None)
    if getattr(status_value, "value", status_value) != "completed":
        raise RuntimeError("PR cloud 작업 완료를 확인하지 못했다")


def _ensure_payload_indexes(client) -> None:
    """Strict-mode filtering needs keyword indexes before cleanup or recovery can run."""
    from qdrant_client.models import PayloadSchemaType
    from app.agents.qdrant_agent import COLLECTION

    schema = client.get_collection(collection_name=COLLECTION).payload_schema or {}
    fields = ("team", "source_kind", "source_sheet_id")
    for field in fields:
        if field in schema:
            data_type = schema[field].data_type
            if getattr(data_type, "value", data_type) != "keyword":
                raise ValueError(f"PR 필터 인덱스 {field!r}가 keyword가 아니므로 변경하지 않는다")
    for field in fields:
        if field not in schema:
            _completed(client.create_payload_index(
                collection_name=COLLECTION, field_name=field,
                field_schema=PayloadSchemaType.KEYWORD, wait=True))


def _upsert(client, points: list[dict]) -> None:
    from qdrant_client.models import PointStruct
    from app.agents.qdrant_agent import COLLECTION

    for offset in range(0, len(points), _BATCH_SIZE):
        _completed(client.upsert(collection_name=COLLECTION, wait=True,
                                 points=[PointStruct(**p) for p in points[offset:offset + _BATCH_SIZE]]))


def _delete_owned(client, ids: list[str]) -> None:
    from qdrant_client.models import FieldCondition, Filter, FilterSelector, HasIdCondition, MatchValue
    from app.agents.qdrant_agent import COLLECTION

    if ids:
        # BOTH local ownership IDs and source attributes must match. Never delete by team alone.
        conditions = [HasIdCondition(has_id=ids)] + [
            FieldCondition(key=key, match=MatchValue(value=value)) for key, value in
            (("team", "PR"), ("source_kind", _SOURCE_KIND), ("source_sheet_id", SHEET_ID))]
        _completed(client.delete(collection_name=COLLECTION, wait=True,
                                 points_selector=FilterSelector(filter=Filter(must=conditions))))


def _restore_cloud(client, previous: dict, candidate: dict) -> None:
    old_ids = {p["id"] for p in previous["points"]}
    current = _cloud_payloads(client, list(old_ids))
    # A separately owned point must remain untouched even if an ID was externally replaced.
    _upsert(client, [p for p in previous["points"] if p["id"] not in current or _owns(current[p["id"]])])
    _delete_owned(client, [p["id"] for p in candidate["points"] if p["id"] not in old_ids])


def sync_pr_issues(dry_run: bool = False) -> dict:
    """Sync only PR-owned points; any interrupted cloud write has a durable recovery journal."""
    from app.agents import qdrant_agent
    from app.core.data_freshness import _drive_modified

    with _sync_lock():
        previous = _load_manifest()
        if _pending_path().exists() and not dry_run:
            pending = _validate_manifest(json.loads(_pending_path().read_text(encoding="utf-8")))
            client = qdrant_agent._get_client()
            _ensure_payload_indexes(client)
            _restore_cloud(client, previous, pending)
            _pending_path().unlink()
        read_started = datetime.now().replace(microsecond=0).isoformat()
        values = _read_sheet()
        rows = parse_rows(values) if values else []
        stat = {**_row_stats(rows), "written": 0, "deleted": 0, "embedded": 0, "reused": 0,
                "empty": not rows, "ok": bool(rows), "dry_run": dry_run, "cleanup_refused": 0}
        if not rows:
            logger.warning("pr_issues_empty_sheet", tab=SHEET_TAB)
            return stat
        new_ids = {_point_id(payload) for payload in rows}
        removed = [p["id"] for p in previous["points"] if p["id"] not in new_ids]
        if len(removed) > max(10, len(previous["points"]) * 0.5):
            stat.update(ok=False, cleanup_refused=len(removed))
            logger.error("pr_issues_cleanup_refused", removed=len(removed), rows=len(rows))
            return stat
        if dry_run:
            return stat

        modified = _drive_modified(SHEET_ID)
        stat.update(synced_at=read_started,
                    sheet_updated_at=modified.replace(microsecond=0).isoformat() if modified else None,
                    sheet_metadata_available=modified is not None,
                    sheet_metadata_error=None if modified else "원본 수정 시각 확인 불가")
        for key in ("month_unknown", "month_inferred"):
            baseline = "baseline_" + key
            stat[baseline] = previous.get("stats", {}).get(baseline, stat[key])
        points, stat["embedded"], stat["reused"] = _embed_rows(rows, previous)
        client = qdrant_agent._get_client()
        _ensure_payload_indexes(client)
        existing = _cloud_payloads(client, [p["id"] for p in points])
        if any(not _owns(payload) for payload in existing.values()):
            raise ValueError("PR candidate ID에 다른 자료의 소유권이 있어 덮어쓰지 않는다")
        changed = [p for p in points if existing.get(p["id"]) != p["payload"]]
        # If the embedding model changed, payload equality does not imply vector equality.
        if (previous.get("embedding_model") != qdrant_agent.EMBEDDING_MODEL
                or previous.get("embedding_dim") != qdrant_agent.EMBEDDING_DIM):
            changed = points
        stat.update(written=len(changed), deleted=len(removed))
        candidate = {"version": 1, "source_kind": _SOURCE_KIND, "source_sheet_id": SHEET_ID,
                     "source_tab": SHEET_TAB, "synced_at": read_started,
                     "sheet_updated_at": stat["sheet_updated_at"],
                     "embedding_model": qdrant_agent.EMBEDDING_MODEL,
                     "embedding_dim": qdrant_agent.EMBEDDING_DIM, "stats": stat, "points": points}
        _validate_manifest(candidate)
        if changed or removed:
            # Validate and serialize ALL embeddings before the first cloud mutation.
            _write_atomic(_pending_path(), candidate)
            try:
                _upsert(client, changed)
                _delete_owned(client, removed)
                _write_atomic(LOCAL_JSON, candidate)
            except Exception:
                try:
                    _restore_cloud(client, previous, candidate)
                    _pending_path().unlink(missing_ok=True)
                except Exception as rollback_error:
                    logger.error("pr_issues_recovery_pending", error_type=type(rollback_error).__name__)
                raise
            _pending_path().unlink(missing_ok=True)
        else:
            _write_atomic(LOCAL_JSON, candidate)
        qdrant_agent._TEAM_COUNTS = None
        logger.info("pr_issues_synced", rows=len(rows), written=stat["written"], deleted=len(removed))
        return stat


def status() -> dict:
    """Local manifest state only: this function never contacts Sheets or Qdrant."""
    empty = {"count": 0, "rows": 0, "loaded": False, "synced_at": None,
             "sheet_updated_at": None, "unknown_teams": {}, "month_unknown": 0,
             "month_inferred": 0, "baseline_month_unknown": 0, "baseline_month_inferred": 0,
             "sheet_metadata_available": False, "sheet_metadata_error": "원본 수정 시각 확인 불가",
             "pending_recovery": _pending_path().exists(), "error": None}
    try:
        saved = _load_manifest()
        if not saved["points"]:
            return empty
        return {**empty, **saved.get("stats", {}), "count": len(saved["points"]), "loaded": True,
                "synced_at": saved.get("synced_at"), "sheet_updated_at": saved.get("sheet_updated_at"),
                "pending_recovery": _pending_path().exists()}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {**empty, "error": f"PR manifest 확인 실패: {type(exc).__name__}"}
