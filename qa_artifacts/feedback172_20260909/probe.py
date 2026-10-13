"""Read-only, incident-scoped production evidence for feedback 172."""

import contextlib
import io
import json
from pathlib import Path
import sys

ARTIFACT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ARTIFACT_DIR.parent))
import entra_access_count_20260908 as runner


runner.REMOTE_QUERY = r'''
import ast
import datetime as dt
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pymysql
from google.cloud import bigquery
from app.config import get_settings

s = get_settings()
assert s.mariadb_host == "10.1.200.5", "Expected production database"
kst = dt.timezone(dt.timedelta(hours=9))
result = {
    "scope": "feedback 172 and messages 14394-14397 only; read-only",
    "queried_at_kst": dt.datetime.now(kst).isoformat(),
    "incident_feedback_id": 172,
    "incident_created_at_kst": "2026-09-08T18:04:11+09:00",
}
conn = pymysql.connect(
    host=s.mariadb_host, port=int(s.mariadb_port),
    user=s.mariadb_user, password=s.mariadb_password,
    database=s.mariadb_database, charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
    connect_timeout=10, read_timeout=15, write_timeout=10,
)
try:
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        conn.begin()
        cur.execute("SELECT NOW() AS db_now, UTC_TIMESTAMP() AS db_utc, "
                    "@@session.time_zone AS time_zone, DATABASE() AS db_name")
        result["database"] = cur.fetchone()
        cur.execute("SELECT id, rating, comment, created_at, status, handled_note, "
                    "conversation_id, message_id FROM message_feedback WHERE id=%s", (172,))
        result["feedback"] = cur.fetchone()
        assert result["feedback"]["conversation_id"] == "6390b62b-4697-45a3-916d-c222628654bf"
        cur.execute("SELECT id, role, content, model, created_at FROM messages "
                    "WHERE conversation_id=%s AND id IN (14394,14395,14396,14397) "
                    "ORDER BY id", ("6390b62b-4697-45a3-916d-c222628654bf",))
        result["incident_saved_messages"] = cur.fetchall()
finally:
    conn.rollback()
    conn.close()

original = Decimal("65655330136")
result["incident_arithmetic_only"] = {
    "given_sum_krw": str(original),
    "given_distinct_date_count": 16,
    "month_calendar_days": 30,
    "given_sum_100m_krw": str(original / Decimal("100000000")),
    "linear_extrapolation_krw": str(original / 16 * 30),
    "linear_extrapolation_100m_krw": str(original / 16 * 30 / Decimal("100000000")),
    "note": "Arithmetic check of immutable incident figure; this is not a valid forecast.",
}

source_path = Path("app/agents/sql_agent.py")
source = source_path.read_text(encoding="utf-8")
tree = ast.parse(source)
function_nodes = (ast.FunctionDef, ast.AsyncFunctionDef)
functions = [node for node in tree.body if isinstance(node, function_nodes)]
source_lines = source.splitlines()
prompt_wanted = set()
for index, line in enumerate(source_lines):
    if "마감일을 지어내지 마라" in line or "조회가 어디까지였는지는 코드가" in line:
        prompt_wanted.update(range(max(0, index - 2), min(len(source_lines), index + 3)))
result["live_sql_agent"] = {
    "sha256_normalized_lf": hashlib.sha256(source_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
    "future_note_calls_per_top_level_function": {
        node.name: sum(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_future_period_note"
            for child in ast.walk(node)
        )
        for node in functions
        if node.name in ("format_answer", "_fast_answer_stream", "run_sql_agent_stream")
        or any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
               and child.func.id == "_future_period_note" for child in ast.walk(node))
    },
    "future_period_note_source": next(
        (ast.get_source_segment(source, node) for node in functions
         if node.name == "_future_period_note"), None),
    "answer_prompt_relevant_lines": [
        {"line": index + 1, "text": source_lines[index]} for index in sorted(prompt_wanted)
    ],
}
prompt_path = Path("prompts/sql_generator.txt")
if prompt_path.exists():
    prompt_text = prompt_path.read_text(encoding="utf-8")
    prompt_lines = prompt_text.splitlines()
    wanted = set()
    for index, line in enumerate(prompt_lines):
        if "미래" in line or "공시" in line or "마감" in line:
            wanted.update(range(max(0, index - 2), min(len(prompt_lines), index + 3)))
    result["live_sql_generator_prompt"] = {
        "sha256_normalized_lf": hashlib.sha256(prompt_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "relevant_lines": [{"line": index + 1, "text": prompt_lines[index]} for index in sorted(wanted)],
    }

sql = """
SELECT DATE(Date) AS sales_date, COUNT(*) AS row_count,
       SUM(Sales1_R) AS sales_krw
FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
WHERE DATE(Date) >= DATE '2026-09-01'
  AND DATE(Date) < DATE '2026-10-01'
GROUP BY sales_date
ORDER BY sales_date
"""
result["current_bigquery_crosscheck"] = {
    "warning": "Mutable data queried now; not an incident-time snapshot.",
    "queried_at_kst": dt.datetime.now(kst).isoformat(),
    "incident_cutoff": "2026-09-08",
    "sql": sql.strip(),
    "maximum_bytes_billed": 5 * 1024 * 1024 * 1024,
}
bq_result = result["current_bigquery_crosscheck"]
client = None
try:
    client = bigquery.Client(project=s.gcp_project_id)
    config = bigquery.QueryJobConfig(maximum_bytes_billed=bq_result["maximum_bytes_billed"])
    job = client.query(sql, job_config=config, retry=None, job_retry=None, timeout=10)
    rows = [dict(row) for row in job.result(timeout=30, retry=None, job_retry=None)]
    bq_result["daily_rows"] = rows
    bq_result["total_bytes_processed"] = job.total_bytes_processed
    bq_result["total_bytes_billed"] = job.total_bytes_billed
    bq_result["cache_hit"] = job.cache_hit
    bq_result["job_id"] = job.job_id
    cutoff = dt.date(2026, 9, 8)
    def aggregate(subset):
        total = sum((Decimal(str(row["sales_krw"] or 0)) for row in subset), Decimal(0))
        return {
            "sales_krw": str(total),
            "sales_100m_krw": str(total / Decimal("100000000")),
            "distinct_dates": len(subset),
            "row_count": sum(row["row_count"] for row in subset),
            "min_date": min((row["sales_date"] for row in subset), default=None),
            "max_date": max((row["sales_date"] for row in subset), default=None),
            "dates": [row["sales_date"] for row in subset],
        }
    bq_result["full_september"] = aggregate(rows)
    bq_result["through_incident_date"] = aggregate([row for row in rows if row["sales_date"] <= cutoff])
    bq_result["after_incident_date"] = aggregate([row for row in rows if row["sales_date"] > cutoff])
    bq_result["queried_finished_at_kst"] = dt.datetime.now(kst).isoformat()
    try:
        table = client.get_table("skin1004-319714.Sales_Integration.SALES_ALL_Backup", retry=None, timeout=10)
        bq_result["table_modified_at"] = table.modified
    except Exception as exc:
        bq_result["table_metadata_error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
except Exception as exc:
    bq_result["error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
finally:
    if client is not None:
        client.close()
print(json.dumps(result, default=str, ensure_ascii=True, separators=(",", ":")))
'''


def main():
    sources_only = "--sources-only" in sys.argv[1:]
    if sources_only:
        source_start = runner.REMOTE_QUERY.index('source_path = Path("app/agents/sql_agent.py")')
        source_end = runner.REMOTE_QUERY.index('\nsql = """', source_start)
        runner.REMOTE_QUERY = (
            "import ast, hashlib, json\nfrom pathlib import Path\nresult = {}\n"
            + runner.REMOTE_QUERY[source_start:source_end]
            + '\nprint(json.dumps(result, ensure_ascii=True, separators=(",", ":")))\n'
        )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = runner.main()
    if code:
        print(output.getvalue())
        return code
    data = json.loads(output.getvalue())
    destination = ARTIFACT_DIR / (
        "live_source_evidence.json" if sources_only else "production_evidence.json")
    destination.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    if sources_only:
        print(json.dumps({"evidence_path": str(destination), "live_sql_agent": data["live_sql_agent"]},
                         ensure_ascii=True))
        return 0
    bq = data["current_bigquery_crosscheck"]
    print(json.dumps({
        "evidence_path": str(destination),
        "queried_at_kst": data["queried_at_kst"],
        "feedback": data["feedback"],
        "messages": [{"id": row["id"], "role": row["role"], "created_at": row["created_at"]}
                     for row in data["incident_saved_messages"]],
        "incident_arithmetic": data["incident_arithmetic_only"],
        "live_future_note_calls": data["live_sql_agent"]["future_note_calls_per_top_level_function"],
        "current_bq": {key: value for key, value in bq.items()
                       if key not in ("sql", "daily_rows")},
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
