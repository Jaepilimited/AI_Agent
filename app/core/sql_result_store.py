# -*- coding: utf-8 -*-
"""요청한 SQL 조회 결과 전체를 잠시 보관해 CSV 다운로드로 내준다.

채팅 표(`_fast_table_markdown`, `app/agents/sql_agent.py`)는 상위 N행만 보여준다.
잘린 나머지를 사용자가 받을 방법이 없었다 — "인도네시아의 2020~2026 월별 매출"
질문이 146행 중 15행만 보여 "2025년부터 데이터가 있다"는 잘못된 결론으로 이어졌고,
사용자는 2분 뒤 "RAW 파일을 줘"라고 다시 물었다 (2026-08-31). 이 저장소가 그 요청에
답한다 — 표가 자른 것과 무관하게 항상 **조회 결과 전체**를 들고 있는다.

메모리 + **디스크**다. 메모리는 빠른 길이고, 디스크가 진짜 보관이다.

⛔ **인메모리만이던 시절, 재기동 한 번에 링크가 전부 죽었다** (붐따 #160,
   2026-09-03). 사용자는 표 8행 옆에서 `CSV로 전체 16행 받기` 를 눌렀는데
   받아지지 않았다 — 그 사이(09:44:59) 앱이 재기동됐기 때문이다. TTL 은 1시간
   이라고 적혀 있었지만 **실제 수명은 「다음 배포까지」** 였고, 그건 개발이
   활발한 날엔 몇 분이다. 게다가 실패 화면은 원시 404 JSON 이라 사용자는
   무엇이 잘못됐는지도 알 수 없었다.

⚠️ 단일 워커 전제는 그대로다(`deploy/ai-craver.service` — uvicorn 워커 1개).
   다만 이제 **워커가 늘어도 디스크를 함께 보므로 조용히 깨지지는 않는다.**

경계(무한정 쌓이지 않게):
- TTL `_TTL_SECONDS` — 채팅 세션 동안 클릭할 시간은 충분하고, 그 이상은 버린다.
- 절대 상한 `_MAX_ENTRIES` — TTL 전에도 이 이상은 안 쌓인다(오래된 것부터 정리).

권한: **소유자만** 꺼낼 수 있다. 판정은 `get()` 한 곳에서만 한다 — 없음과 남의
토큰을 구분하지 않고 둘 다 None 을 돌려준다 (보고서 열람 `app/reports/store.py`
와 같은 사상 — "지목된 사람만 열리고 나머지는 404 를 본다").
"""
from __future__ import annotations

import csv
import io
import json as _json
import os as _os
import re as _re
import secrets
import threading
import time
from pathlib import Path as _Path
from typing import Any, Optional

import structlog

logger_name = __name__
_log = structlog.get_logger(__name__)

# ⚠️ 1시간이던 것을 늘렸다 — 재기동으로 죽던 시절엔 TTL 이 수명을 정하지 않았다.
#    이제는 정말 이 시간만큼 산다. 아침에 받은 링크를 오후에 눌러도 열린다
_TTL_SECONDS = 24 * 3600.0
_MAX_ENTRIES = 300      # 절대 상한 — TTL 전에도 메모리·디스크를 못 박는다

# 디스크 보관 위치. ⛔ 토큰이 파일 이름이 되므로 **반드시 형식을 검사**한다 —
#    검사 없이 쓰면 `../../` 이 든 토큰으로 아무 파일이나 읽힌다
_DIR = _Path(_os.getenv("SQL_RESULT_DIR", "data/sql_results"))
_TOKEN_RE = _re.compile(r"^[A-Za-z0-9_-]{16,64}$")

_lock = threading.Lock()
_store: dict[str, dict[str, Any]] = {}  # token -> {user_id, columns, rows, labels, saved_at}


def _path(token: str) -> Optional[_Path]:
    """토큰이 형식에 맞을 때만 경로를 준다. 아니면 None — 파일을 만지지 않는다."""
    if not _TOKEN_RE.match(token or ""):
        return None
    return _DIR / (token + ".json")


def _persist(token: str, entry: dict) -> None:
    """디스크에 굳힌다. 실패해도 메모리 사본은 살아 있으므로 답변을 막지 않는다.

    ⚠️ 값은 `_csv_cell` 로 미리 정규화해서 넣는다 — Decimal·date 는 JSON 이
       모르는 타입이고, 그대로 `str()` 로 굳히면 엑셀에서 **숫자가 문자열이 된다**
       (이 저장소가 "RAW 데이터" 로서 쓸모 있으려면 계산이 돼야 한다).
    """
    path = _path(token)
    if path is None:
        return
    cols = entry["columns"]
    payload = {
        "user_id": entry["user_id"],
        "columns": cols,
        "labels": entry["labels"],
        "saved_at": entry["saved_at"],
        # 열 순서로 눕혀 저장한다 — 행마다 컬럼 이름을 반복하지 않는다
        "rows": [[_csv_cell(r.get(c)) for c in cols] for r in entry["rows"]],
    }
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, ensure_ascii=False)
        # ⛔ 부분 기록된 파일을 남기지 않는다 — 다음에 읽을 때 조용히 깨진다
        _os.replace(tmp, path)
    except Exception as e:                    # noqa: BLE001
        _log.warning("sql_result_persist_failed", error=str(e)[:160])


def _load(token: str) -> Optional[dict]:
    """디스크에서 되살린다. 없거나 깨졌으면 None."""
    path = _path(token)
    if path is None or not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = _json.load(fh)
        cols = list(payload["columns"])
        return {
            "user_id": payload["user_id"],
            "columns": cols,
            "labels": dict(payload.get("labels") or {}),
            "saved_at": float(payload["saved_at"]),
            "rows": [dict(zip(cols, row)) for row in payload["rows"]],
        }
    except Exception as e:                    # noqa: BLE001
        _log.warning("sql_result_load_failed", error=str(e)[:160])
        return None


def _sweep_disk(now: float) -> None:
    """만료된 파일을 지운다. 상한을 넘으면 오래된 것부터."""
    try:
        files = sorted(_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    except Exception:                         # noqa: BLE001
        return
    doomed = [f for f in files if now - f.stat().st_mtime > _TTL_SECONDS]
    keep = [f for f in files if f not in doomed]
    overflow = len(keep) - _MAX_ENTRIES
    if overflow > 0:
        doomed += keep[:overflow]
    for f in doomed:
        try:
            f.unlink()
        except OSError:
            pass


def _sweep_locked(now: float) -> None:
    """만료분을 지우고, 그래도 상한을 넘으면 오래된 것부터 지운다. 호출자가 락을 쥔 상태여야 한다."""
    expired = [t for t, e in _store.items() if now - e["saved_at"] > _TTL_SECONDS]
    for t in expired:
        del _store[t]
    overflow = len(_store) - _MAX_ENTRIES
    if overflow > 0:
        oldest = sorted(_store.items(), key=lambda kv: kv[1]["saved_at"])[:overflow]
        for t, _ in oldest:
            del _store[t]


def save(
    user_id: int,
    columns: list[str],
    rows: list[dict],
    labels: Optional[dict[str, str]] = None,
) -> str:
    """결과를 저장하고 다운로드 토큰을 돌려준다.

    `rows` 는 표에 보인 상위 N행이 아니라 **BigQuery 가 돌려준 전체 결과**여야 한다
    — 잘린 사본을 저장하면 "다운로드도 잘려 있더라"는 이 기능이 고치려던 것과
    같은 사고가 재현된다.
    """
    token = secrets.token_urlsafe(24)
    now = time.time()
    entry = {
        "user_id": user_id,
        "columns": list(columns),
        "rows": rows,
        "labels": dict(labels or {}),
        "saved_at": now,
    }
    with _lock:
        _sweep_locked(now)
        _store[token] = entry
    # ⛔ 락 밖에서 쓴다 — 큰 결과의 파일 쓰기가 다른 요청을 막으면 안 된다
    _persist(token, entry)
    _sweep_disk(now)
    return token


def get(token: str, user_id: int) -> Optional[dict]:
    """소유자만 결과를 꺼낼 수 있다. 없음·만료·남의 토큰은 모두 None."""
    now = time.time()
    with _lock:
        _sweep_locked(now)
        entry = _store.get(token)
    if entry is None:
        # ⛔ 메모리에 없다고 없는 게 아니다 — 재기동하면 메모리만 비어 있다.
        #    디스크를 보고 살아 있으면 메모리에도 되살린다
        entry = _load(token)
        if entry is not None and now - entry["saved_at"] <= _TTL_SECONDS:
            with _lock:
                _store[token] = entry
        elif entry is not None:
            entry = None                      # 만료분은 없는 것으로 본다
    if not entry or entry["user_id"] != user_id:
        return None
    return entry


def _csv_cell(v: Any) -> Any:
    """csv.writer 가 그대로 못 다루는 타입만 변환한다 — 숫자는 문자열로 굳히지 않는다.

    Excel 이 열었을 때 숫자로 계산할 수 있어야 "RAW 데이터"로서 쓸모가 있다.
    `_fast_fmt_cell`(채팅 표용, 천단위 콤마 문자열)을 여기서 재사용하지 않는 이유다.
    """
    import datetime as _dt
    from decimal import Decimal

    if v is None:
        return ""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    return v


def to_csv_bytes(
    columns: list[str],
    rows: list[dict],
    labels: Optional[dict[str, str]] = None,
) -> bytes:
    """CSV 바이트를 만든다. Excel(한국어 로캘)에서 한글이 깨지지 않도록 UTF-8 BOM을 붙인다.

    BOM(`utf-8-sig`) 없이 순수 UTF-8로 저장하면 Windows Excel이 기본 코드페이지
    (cp949)로 잘못 읽어 한글이 깨진다 — 이 프로젝트 콘솔 출력이 겪는 것과 같은
    부류의 인코딩 함정이다(CLAUDE.md "콘솔이 cp949다"). BOM은 Excel이 UTF-8임을
    미리 알아채게 하는 표준적인 회피책이다.
    """
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    labels = labels or {}
    writer.writerow([labels.get(c, c) for c in columns])
    for row in rows:
        writer.writerow([_csv_cell(row.get(c)) for c in columns])
    return buf.getvalue().encode("utf-8-sig")
