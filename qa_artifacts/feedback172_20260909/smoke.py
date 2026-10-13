"""Post-publication semantic smoke check; two bounded read-only BigQuery jobs.

No conversations, SQL examples, caches, feedback, or notifications are written.
The original streamed application path runs with a bounded BigQuery adapter and
factories that fail if a language model or SQL cache is unexpectedly invoked.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys

import release


REMOTE = r'''
import datetime as dt
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import pymysql
from google.cloud import bigquery
from app.config import get_settings

payload = json.loads(__PAYLOAD_JSON__)
started = time.perf_counter()
kst = ZoneInfo('Asia/Seoul')
current_date = dt.datetime.now(kst).date()
result = {'started_at_kst': dt.datetime.now(kst).isoformat(),
          'scope': 'two bounded read-only BigQuery queries; no conversation or database writes',
          'query': '이번달 전사 예상실적이 얼마로 마감할거 같아?',
          'incident_as_of': '2026-09-08', 'current_as_of': current_date.isoformat(),
          'executions': [], 'guards': {'model_attempts': 0, 'cache_attempts': 0,
              'mariadb_attempts': 0, 'sql_example_record_suppressed': 0}}
client = None
try:
    result['source_hashes'] = {
        name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in payload['expected_hashes']}
    assert result['source_hashes'] == payload['expected_hashes'], 'Live files differ from published plan'
    s = get_settings()
    assert s.mariadb_host == '10.1.200.5', 'Expected production environment'

    def forbidden_db(*args, **kwargs):
        result['guards']['mariadb_attempts'] += 1
        raise RuntimeError('MariaDB access is prohibited during this semantic smoke check')
    pymysql.connect = forbidden_db
    pymysql.connections.Connection.connect = forbidden_db
    import app.db.mariadb as db
    for name in ('execute', 'executemany', 'fetch_all', 'fetch_one', 'get_connection'):
        if hasattr(db, name):
            setattr(db, name, forbidden_db)

    import app.core.sql_examples as examples
    def suppress_record(*args, **kwargs):
        result['guards']['sql_example_record_suppressed'] += 1
    examples.record = suppress_record
    import app.core.llm as llm
    def forbidden_model(*args, **kwargs):
        result['guards']['model_attempts'] += 1
        raise RuntimeError('A language model was unexpectedly invoked for the deterministic outlook')
    llm.get_flash_client = forbidden_model
    llm.get_llm_client = forbidden_model

    from app.core import sales_outlook as core
    from app.agents import sql_agent as agent
    agent.get_flash_client = forbidden_model
    agent.get_llm_client = forbidden_model
    def forbidden_cache(*args, **kwargs):
        result['guards']['cache_attempts'] += 1
        raise RuntimeError('SQL cache access is prohibited during this semantic smoke check')
    for name in ('_cache_lookup', '_cache_store', '_cache_forget'):
        setattr(agent, name, forbidden_cache)

    client = bigquery.Client(project=s.gcp_project_id)
    class BoundedBigQuery:
        def execute_query(self, sql, timeout=30, max_rows=1000):
            assert len(result['executions']) < 2, 'Two-query budget exceeded'
            assert sql.lstrip().upper().startswith('SELECT'), 'Only SELECT is allowed'
            assert '`skin1004-319714.Sales_Integration.SALES_ALL_Backup`' in sql
            entry = {'sql': sql, 'started_at_kst': dt.datetime.now(kst).isoformat(),
                     'maximum_bytes_billed': 5 * 1024 * 1024 * 1024, 'timeout_seconds': 30}
            result['executions'].append(entry)
            tick = time.perf_counter()
            config = bigquery.QueryJobConfig(maximum_bytes_billed=entry['maximum_bytes_billed'])
            job = client.query(sql, job_config=config, retry=None, job_retry=None, timeout=10)
            rows = [dict(row) for row in job.result(timeout=30, retry=None, job_retry=None)]
            assert len(rows) <= min(max_rows, 1000), 'Unexpected result size'
            entry.update(rows=rows, elapsed_seconds=round(time.perf_counter() - tick, 3),
                         job_id=job.job_id, total_bytes_processed=job.total_bytes_processed,
                         total_bytes_billed=job.total_bytes_billed, cache_hit=job.cache_hit)
            return rows
    bounded = BoundedBigQuery()
    agent.get_bigquery_client = lambda: bounded
    query = result['query']
    incident_date = dt.date(2026, 9, 8)
    replay_sql = core.build_company_sql(query, today=incident_date)
    assert replay_sql, 'Incident question did not select deterministic SQL'
    replay_rows = bounded.execute_query(replay_sql)
    replay_answer = core.render_answer(query, replay_sql, replay_rows, today=incident_date)
    result['incident_replay'] = {'answer': replay_answer, 'rows': replay_rows,
        'note': 'Current source data with the incident cutoff; prior probe values are comparison evidence.'}
    print(json.dumps({'progress': 'incident_replay_query_complete'}), flush=True)

    chunks = list(agent.run_sql_agent_stream(query))
    current_answer = ''.join(chunks)
    result['current_stream'] = {'answer': current_answer, 'chunk_count': len(chunks)}
    assert len(result['executions']) == 2, 'Real stream did not execute exactly one query'
    current_rows = result['executions'][1]['rows']
    assert len(replay_rows) == len(current_rows) == 1, 'Unexpected outlook result shape'
    replay_row, current_row = replay_rows[0], current_rows[0]
    current_body = current_answer.split('<details>', 1)[0]
    period_end = dt.date.fromisoformat(str(current_row['outlook_period_end']))
    exclusive_end = (period_end + dt.timedelta(days=1)).isoformat()
    def reconciles(row):
        through = Decimal(str(row['revenue_through_as_of']))
        after = Decimal(str(row['revenue_after_as_of']))
        total = Decimal(str(row['revenue_in_period']))
        return abs(through + after - total) <= Decimal('2')
    result['semantic_checks'] = {
        'incident_date': str(replay_row['outlook_as_of']) == '2026-09-08',
        'incident_period_preserved': str(replay_row['outlook_period_start']) == '2026-09-01'
            and str(replay_row['outlook_period_end']) == '2026-09-30',
        'incident_amounts_reconcile': reconciles(replay_row),
        'current_date': str(current_row['outlook_as_of']) == current_date.isoformat(),
        'current_amounts_reconcile': reconciles(current_row),
        'current_date_visible': current_date.isoformat() in current_answer,
        'current_period_end_visible': period_end.isoformat() in current_body,
        'exclusive_query_boundary_not_in_prose': exclusive_end not in current_body,
        'all_three_amount_labels_visible': all(label in current_answer for label in
            ('기준일까지 누적매출', '이후 날짜 등록분', '월 전체 등록 합계')),
        'registered_total_not_presented_as_forecast': '확정된 마감 예상치가 아닙니다' in current_answer,
        'no_bad_prior_estimate_or_elapsed_date': not any(marker in replay_answer + current_answer
            for marker in ('1,231', '1231.0', '123,103,744', '16일간')),
        'real_sql_in_stream_answer': result['executions'][1]['sql'] in current_answer,
        'model_not_called': result['guards']['model_attempts'] == 0,
        'cache_not_called': result['guards']['cache_attempts'] == 0,
        'mariadb_not_called': result['guards']['mariadb_attempts'] == 0,
        'two_queries_only': len(result['executions']) == 2,
    }
    comparisons = {}
    for key, expected in payload['incident_expected'].items():
        actual = Decimal(str(replay_row[key]))
        comparisons[key] = {'expected_krw': str(expected), 'actual_krw': str(actual),
                            'matches_within_two_won': abs(actual - Decimal(str(expected))) <= Decimal('2')}
    result['prior_probe_comparison'] = comparisons
    result['incident_expected_display_present'] = all(number in replay_answer
        for number in ('122.0', '534.6', '656.6'))
    table = client.get_table(s.sales_table_full_path, retry=None, timeout=10)
    result['table_modified_at'] = table.modified
    result['passed'] = all(result['semantic_checks'].values())
    result['incident_figures_still_match'] = result['incident_expected_display_present'] and all(
        item['matches_within_two_won'] for item in comparisons.values())
except Exception as exc:
    result['passed'] = False
    result['error'] = {'type': type(exc).__name__, 'message': str(exc)[:1200]}
finally:
    if client is not None:
        client.close()
    result['finished_at_kst'] = dt.datetime.now(kst).isoformat()
    result['elapsed_seconds'] = round(time.perf_counter() - started, 3)
print(json.dumps(result, default=str, ensure_ascii=True))
'''


def main():
    published = release.read_json(release.ARTIFACT / "release_publish_result.json")
    assert published.get("published") and published.get("restart_completed_at_utc"), (
        "This smoke check may only run after successful publication and restart")
    plan_bytes = release.PLAN.read_bytes()
    assert release.digest(plan_bytes) == published["plan_sha256"], "Published plan changed"
    plan = json.loads(plan_bytes)
    baseline = release.read_json(release.ARTIFACT / "production_evidence.json")["current_bigquery_crosscheck"]
    payload = {
        "expected_hashes": {name: row["after"] for name, row in plan["files"].items() if row["deploy"]},
        "incident_expected": {
            "revenue_through_as_of": baseline["through_incident_date"]["sales_krw"],
            "revenue_after_as_of": baseline["after_incident_date"]["sales_krw"],
            "revenue_in_period": baseline["full_september"]["sales_krw"],
        },
    }
    code = REMOTE.replace("__PAYLOAD_JSON__", repr(json.dumps(payload)))
    result = release.remote(code)
    output = release.ARTIFACT / "smoke_evidence.json"
    release.write_json(output, result)
    print(json.dumps({"evidence": str(output), "passed": result.get("passed"),
                      "incident_figures_still_match": result.get("incident_figures_still_match"),
                      "semantic_checks": result.get("semantic_checks"),
                      "guards": result.get("guards"), "error": result.get("error"),
                      "elapsed_seconds": result.get("elapsed_seconds")}, ensure_ascii=True))
    if not result.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
