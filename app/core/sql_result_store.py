# -*- coding: utf-8 -*-
"""요청한 SQL 조회 결과 전체를 잠시 보관해 CSV 다운로드로 내준다.

채팅 표(`_fast_table_markdown`, `app/agents/sql_agent.py`)는 상위 N행만 보여준다.
잘린 나머지를 사용자가 받을 방법이 없었다 — "인도네시아의 2020~2026 월별 매출"
질문이 146행 중 15행만 보여 "2025년부터 데이터가 있다"는 잘못된 결론으로 이어졌고,
사용자는 2분 뒤 "RAW 파일을 줘"라고 다시 물었다 (2026-08-31). 이 저장소가 그 요청에
답한다 — 표가 자른 것과 무관하게 항상 **조회 결과 전체**를 들고 있는다.

메모리 인메모리 저장이다. 프로세스가 systemd 단일 워커로 도는 것을 전제한다
(`deploy/ai-craver.service` — uvicorn 기본 워커 1개). ThreadPoolExecutor 스레드는
같은 프로세스 메모리를 공유하므로 문제 없다. **다중 워커로 바뀌면 이 저장소도
공유 저장소(Redis 등)로 옮겨야 한다.**

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
import secrets
import threading
import time
from typing import Any, Optional

logger_name = __name__

_TTL_SECONDS = 3600.0   # 1시간 — 세션 동안 클릭하기엔 충분, 무한정 쌓이지 않는다
_MAX_ENTRIES = 300      # 절대 상한 — TTL 전에도 메모리를 못 박는다

_lock = threading.Lock()
_store: dict[str, dict[str, Any]] = {}  # token -> {user_id, columns, rows, labels, saved_at}


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
    with _lock:
        _sweep_locked(now)
        _store[token] = {
            "user_id": user_id,
            "columns": list(columns),
            "rows": rows,
            "labels": dict(labels or {}),
            "saved_at": now,
        }
    return token


def get(token: str, user_id: int) -> Optional[dict]:
    """소유자만 결과를 꺼낼 수 있다. 없음·만료·남의 토큰은 모두 None."""
    with _lock:
        _sweep_locked(time.time())
        entry = _store.get(token)
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
