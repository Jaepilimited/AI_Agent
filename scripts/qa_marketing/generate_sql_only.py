#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate SQL for all QA questions (fast, no execution).

Calls the SQL generation step directly (Flash LLM only), skipping
BigQuery execution and answer formatting. ~5-8s per query vs ~40-60s full pipeline.
Stores generated SQL in result files as 'used_sql' field.
"""

import json
import os
import sys
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR))
os.chdir(str(PROJECT_DIR))

# Import project modules
from app.config import get_settings
from app.core.llm import get_flash_client
from app.core.bigquery import get_bigquery_client
from app.agents.sql_agent import (
    _load_prompt, _schema_cache_sales, _schema_cache_tables,
    MARKETING_TABLES, sanitize_sql,
)
import app.agents.sql_agent as sql_mod

RESULTS_DIR = Path(__file__).resolve().parent / "results_v3"
QUESTIONS_DIR = Path(__file__).resolve().parent

TABLES = [
    "advertising", "amazon_search", "influencer", "marketing_cost",
    "meta_ads", "platform", "product", "review_amazon", "review_qoo10",
    "review_shopee", "review_smartstore", "sales_all", "shopify",
]

# Warmup: load schemas
def warmup_schemas():
    """Pre-load all schemas into cache."""
    bq = get_bigquery_client()
    settings = get_settings()

    if not sql_mod._schema_cache_sales:
        try:
            schema = bq.get_table_schema(settings.sales_table_full_path)
            schema_lines = [
                f"  - {col['name']} ({col['type']}): {col['description']}"
                for col in schema
            ]
            table_short = settings.sales_table_full_path.rsplit(".", 1)[-1]
            sql_mod._schema_cache_sales = (
                f"\n\n### 실제 테이블 스키마 ({table_short})\n" + "\n".join(schema_lines)
            )
            print(f"  Sales schema loaded: {len(schema)} columns")
        except Exception as e:
            print(f"  Sales schema FAILED: {e}")

    loaded = 0
    for table_entry in MARKETING_TABLES:
        table_path = table_entry[0]
        label = table_entry[1]
        if table_path in sql_mod._schema_cache_tables:
            loaded += 1
            continue
        try:
            tbl_schema = bq.get_table_schema(table_path)
            tbl_lines = [
                f"  - {col['name']} ({col['type']}): {col['description']}"
                for col in tbl_schema
            ]
            tbl_short = table_path.rsplit(".", 1)[-1]
            sql_mod._schema_cache_tables[table_path] = (
                f"\n\n### {label} ({tbl_short})\n" + "\n".join(tbl_lines)
            )
            loaded += 1
        except Exception as e:
            sql_mod._schema_cache_tables[table_path] = ""
    print(f"  Marketing schemas loaded: {loaded}/{len(MARKETING_TABLES)}")


def generate_sql_for_query(query: str) -> str:
    """Generate SQL for a single query using Flash LLM."""
    llm = get_flash_client()
    system_prompt = _load_prompt("sql_generator.txt", can_view_fi=True)

    # Build schema context (same logic as generate_sql node)
    schema_context = sql_mod._schema_cache_sales or ""
    query_lower = query.lower()
    for table_entry in MARKETING_TABLES:
        table_path, label, keywords = table_entry[0], table_entry[1], table_entry[2]
        if not any(kw in query_lower for kw in keywords):
            continue
        schema_context += sql_mod._schema_cache_tables.get(table_path, "")

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    date_context = f"\n\n## 오늘 날짜\n{today}"

    full_prompt = f"{system_prompt}{schema_context}{date_context}\n\n## 사용자 질문\n{query}"

    try:
        sql = llm.generate(full_prompt, temperature=0.0)
        return sanitize_sql(sql)
    except Exception as e:
        return f"ERROR: {e}"


def process_table(table: str, max_workers: int = 3):
    """Generate SQL for all questions in a table."""
    qf = QUESTIONS_DIR / f"questions_v3_{table}.json"
    rf = RESULTS_DIR / f"results_v3_{table}.json"

    if not qf.exists():
        print(f"  [{table}] No question file, skipping")
        return

    questions = json.loads(qf.read_text(encoding="utf-8"))
    results = json.loads(rf.read_text(encoding="utf-8")) if rf.exists() else []

    # Build lookup: id -> result index
    id_to_idx = {r["id"]: i for i, r in enumerate(results)}

    # Find results missing SQL
    need_sql = []
    for r in results:
        if not r.get("used_sql"):
            qid = r["id"]
            # Find matching question
            q_match = next((q for q in questions if q["id"] == qid), None)
            if q_match:
                need_sql.append((r, q_match))

    if not need_sql:
        print(f"  [{table}] All {len(results)} results have SQL")
        return

    print(f"  [{table}] Generating SQL for {len(need_sql)}/{len(results)} results...")

    done = 0
    errors = 0

    def gen_one(pair):
        r, q = pair
        sql = generate_sql_for_query(q["query"])
        return r["id"], sql

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(gen_one, pair): pair for pair in need_sql}
        for future in as_completed(futures):
            try:
                qid, sql = future.result()
                idx = id_to_idx.get(qid)
                if idx is not None:
                    results[idx]["used_sql"] = sql
                done += 1
                if done % 50 == 0:
                    # Save periodically
                    rf.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
                    print(f"    [{table}] {done}/{len(need_sql)} saved...")
            except Exception as e:
                errors += 1
                print(f"    ERROR: {e}")

    # Final save
    rf.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  [{table}] Done: {done} SQL generated, {errors} errors")


def main():
    print("SQL Generator - adding used_sql to QA results")
    print("=" * 60)

    print("\nWarming up schemas...")
    warmup_schemas()

    # Process specific tables or all
    tables = sys.argv[1:] if len(sys.argv) > 1 else TABLES

    for table in tables:
        process_table(table, max_workers=3)

    # Count totals
    total = 0
    with_sql = 0
    for t in TABLES:
        rf = RESULTS_DIR / f"results_v3_{t}.json"
        if rf.exists():
            data = json.loads(rf.read_text(encoding="utf-8"))
            total += len(data)
            with_sql += sum(1 for r in data if r.get("used_sql"))
    print(f"\n=== TOTAL: {with_sql}/{total} results with SQL ===")


if __name__ == "__main__":
    main()
