# -*- coding: utf-8 -*-
"""노션 **BigQuery 데이터베이스 정의서** → BigQuery 컬럼 설명 동기화.

⛔ **컬럼의 뜻이 앱에 전달될 경로가 없었다** (2026-08-18 확인).
   앱이 보던 것은 두 가지뿐이었다:
     1. `INFORMATION_SCHEMA` — 컬럼 **이름과 타입**만. 뜻은 없다
     2. `prompts/sql_generator.txt` — 사람이 손으로 적은 설명. **반드시 낡는다**

   그래서 `Store_Review.shopname` 이 매장명이라는 걸 알 방법이 없었고, LLM 이
   `channel`(구글맵·네이버 플레이스)로 매장을 찾아 **"뉴욕 플래그십 2026년 0건"**
   이라고 답했다 (실제 95건 · 이주훈 님 제보 2026-08-14).
   같은 날 국내몰 리뷰는 통합 테이블을 몰라 1/10 만 세고 있었다.

   뜻은 노션 정의서에 **이미 정확히 적혀 있었다** — 읽는 코드가 없었을 뿐이다.
       컬럼명 shopname · 설명 "매장명(명동 플래그십 or 뉴욕 플래그십)"

**왜 BigQuery 컬럼 설명에 심는가**
   앱은 질문마다 `INFORMATION_SCHEMA` 를 이미 읽는다. 거기에 설명을 채우면
   **코드 변경 없이** 뜻이 따라온다. 프롬프트에 손으로 적는 방식(낡는다)을
   대체하고, 정의서가 단일 소스가 된다.

⚠️ 이 모듈은 **BigQuery 스키마를 수정한다**(설명만). 데이터는 건드리지 않는다.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

# 노션 '정의서' 데이터베이스 (테이블 1개 = 페이지 1개, 하위에 '컬럼 정의' DB)
DEFINITION_DB_ID = "3062b4283b0080029932ce786a111ca9"
PROJECT = "skin1004-319714"
_NOTION_VER = "2022-06-28"
# 설명이 너무 길면 스키마 프롬프트가 부풀어 조회가 느려진다. 앞부분만 쓴다
_MAX_DESC = 300
# 컬럼명·데이터셋명에 쓸 수 있는 문자만 통과시킨다 (SQL 조립 전 검증)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _token() -> str:
    from app.config import get_settings
    return get_settings().notion_mcp_token or os.getenv("NOTION_MCP_TOKEN", "")


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", "Notion-Version": _NOTION_VER,
            "Content-Type": "application/json"}


def _plain(prop: Optional[dict]) -> str:
    """노션 속성에서 문자열을 꺼낸다 (title/rich_text/select 모두 처리)."""
    if not prop:
        return ""
    t = prop.get("type")
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in prop.get(t, []) or []).strip()
    if t == "select":
        return ((prop.get("select") or {}).get("name") or "").strip()
    return ""


def _query_all(client, db_id: str) -> List[dict]:
    """페이지네이션을 끝까지 따라간다 — 100개에서 잘리면 조용히 일부만 동기화된다."""
    out, cursor = [], None
    while True:
        body: Dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = client.post(f"https://api.notion.com/v1/databases/{db_id}/query", json=body)
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("results", []))
        if not data.get("has_more"):
            return out
        cursor = data.get("next_cursor")


def _column_db_id(client, page_id: str) -> Optional[str]:
    """테이블 페이지 안의 '컬럼 정의' 하위 데이터베이스 id."""
    r = client.get(f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100")
    if r.status_code != 200:
        return None
    for b in r.json().get("results", []):
        if b.get("type") == "child_database":
            return b["id"]
    return None


def fetch_definitions() -> List[Dict[str, Any]]:
    """정의서를 읽어 `{dataset, table, table_desc, columns:{name: desc}}` 목록으로."""
    import httpx

    if not _token():
        raise RuntimeError("NOTION_MCP_TOKEN 미설정")
    out: List[Dict[str, Any]] = []
    with httpx.Client(timeout=40, headers=_headers()) as cl:
        for page in _query_all(cl, DEFINITION_DB_ID):
            props = page.get("properties", {})
            dataset = _plain(props.get("Dataset"))
            table = _plain(props.get("테이블명"))
            if not dataset or not table:
                continue                       # 아직 안 채워진 행
            col_db = _column_db_id(cl, page["id"])
            columns: Dict[str, str] = {}
            if col_db:
                for row in _query_all(cl, col_db):
                    p = row.get("properties", {})
                    name = _plain(p.get("컬럼명"))
                    desc = _plain(p.get("설명"))
                    if name and desc:
                        columns[name] = desc[:_MAX_DESC]
            out.append({"dataset": dataset, "table": table,
                        "table_desc": _plain(props.get("설명"))[:_MAX_DESC],
                        "columns": columns})
    logger.info("schema_docs_fetched", tables=len(out),
                with_columns=sum(1 for d in out if d["columns"]))
    return out


def _lit(value: str) -> str:
    """BigQuery 문자열 리터럴로 안전하게 감싼다.

    ⚠️ `execute_query()` 가 파라미터 바인딩을 지원하지 않아 직접 이스케이프한다.
       역슬래시·따옴표·개행을 처리하고, **식별자는 여기로 보내지 않는다**
       (테이블·컬럼명은 `_IDENT` 정규식으로 따로 검증한다).
    """
    v = (value or "").replace("\\", "\\\\").replace("'", "\\'")
    v = v.replace(chr(10), " ").replace(chr(13), " ")   # 실제 개행 (리터럴 백슬래시-n 아님)
    return "'" + v + "'"


def _existing(bq, dataset: str, table: str) -> Dict[str, str]:
    """BigQuery 의 현재 컬럼 → 설명. 테이블이 없으면 빈 dict."""
    rows = bq.execute_query(
        f"SELECT column_name, IFNULL(description, '') d "
        f"FROM `{PROJECT}.{dataset}`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS "
        f"WHERE table_name = {_lit(table)}") or []
    return {r["column_name"]: r["d"] for r in rows}


def sync(dry_run: bool = False, only_dataset: Optional[str] = None) -> Dict[str, Any]:
    """정의서 → BigQuery 컬럼 설명. **이미 같은 값이면 건너뛴다** (불필요한 DDL 방지).

    ⚠️ 데이터는 절대 건드리지 않는다 — `ALTER COLUMN ... SET OPTIONS(description=)` 뿐이다.
    """
    from app.core.bigquery import get_bigquery_client

    bq = get_bigquery_client()
    defs = fetch_definitions()
    stats = {"tables_seen": len(defs), "tables_matched": 0, "updated": 0,
             "skipped_same": 0, "missing_in_bq": 0, "errors": 0}
    changes: List[str] = []

    for d in defs:
        dataset, table = d["dataset"], d["table"]
        if only_dataset and dataset != only_dataset:
            continue
        if not (_IDENT.match(dataset) and _IDENT.match(table)):
            continue                          # 식별자가 아니면 SQL 을 만들지 않는다
        if not d["columns"]:
            continue
        try:
            current = _existing(bq, dataset, table)
        except Exception as e:
            logger.warning("schema_docs_table_read_failed", table=table, error=str(e)[:120])
            stats["errors"] += 1
            continue
        if not current:
            stats["missing_in_bq"] += 1       # 정의서에는 있는데 BigQuery 에 없는 테이블
            continue
        stats["tables_matched"] += 1
        for col, desc in d["columns"].items():
            if col not in current:
                continue                      # 정의서에만 있는 컬럼 — 건드리지 않는다
            if current[col].strip() == desc.strip():
                stats["skipped_same"] += 1
                continue
            if not _IDENT.match(col):
                continue
            if dry_run:
                stats["updated"] += 1
                changes.append(f"{dataset}.{table}.{col}")
                continue
            try:
                bq.execute_query(
                    f"ALTER TABLE `{PROJECT}.{dataset}.{table}` "
                    f"ALTER COLUMN `{col}` SET OPTIONS(description={_lit(desc)})")
                stats["updated"] += 1
                changes.append(f"{dataset}.{table}.{col}")
            except Exception as e:
                # 뷰·외부테이블은 ALTER 가 안 된다 — 건너뛰고 계속한다
                logger.warning("schema_docs_alter_failed", table=table, column=col,
                               error=str(e)[:120])
                stats["errors"] += 1

    stats["changed"] = changes[:40]
    logger.info("schema_docs_synced", **{k: v for k, v in stats.items() if k != "changed"})
    return stats
