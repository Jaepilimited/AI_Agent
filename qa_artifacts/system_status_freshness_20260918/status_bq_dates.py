"""Column-limited SELECT-only BigQuery freshness measurements."""
import json
from pathlib import Path
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa_artifacts"))
import feedback_release_20260908 as access

meta = json.loads(Path(__file__).with_name("status_bq_metadata.json").read_text(encoding="utf-8"))
columns = {
    "export_logistics": ["order_date", "created_at", "updated_at"],
    "raw_data": ["Date"],
    "Korea_mall_Review": ["collected_date", "review_date"],
    "Oversea_mall_Review": ["collected_date", "review_date"],
    "Store_Review": ["review_date"],
    "Exchange_Rate": ["Year_month"],
    "FI_LLM_Flat": ["Year_Month"],
    "Product": ["Date"],
    "SALES_ALL_Backup": ["Date"],
    "meta data_test": ["collect_date"],
    "domestic_cs_records": ["received_at", "completed_at", "loaded_at", "ingested_at", "source_fetched_at"],
    "overseas_cs_refunds": ["received_at", "completed_at", "refund_created_at", "refund_processed_at", "shopify_updated_at", "ingested_at"],
    "Integrated_marketing_cost": ["Date"],
    "amazon_search_analytics_catalog_performance": ["Week_Start_Date_Sunday"],
    "influencer_input_ALL_TEAMS": ["Contact_date", "Content_upload_date", "Measurement_date", "Request_date"],
    "integrated_ad": ["date"],
    "shopify_analysis_sales": ["Date"],
    "promotion": ["start_date", "end_date", "created_at", "updated_at", "synced_at"],
}
queries = []
for table in meta["tables"]:
    path = table["path"]
    fields = {s["name"]:s["type"] for s in table["schema"]}
    aggregates = ["COUNT(*) AS n"]
    for col in columns[path.split(".")[-1]]:
        typ = fields[col]
        aggregates.extend([f"MIN(`{col}`) AS min_{col}", f"MAX(`{col}`) AS max_{col}", f"COUNTIF(`{col}` IS NOT NULL) AS nonnull_{col}"])
        if typ in ("DATE", "DATETIME", "TIMESTAMP"):
            expr = f"DATE(`{col}`, 'Asia/Seoul')" if typ == "TIMESTAMP" else f"DATE(`{col}`)" if typ == "DATETIME" else f"`{col}`"
            aggregates.append(f"MAX(IF({expr}<=CURRENT_DATE('Asia/Seoul'), {expr}, NULL)) AS latest_nonfuture_{col}")
    queries.append({"id":path,"sql":"SELECT " + ", ".join(aggregates) + f" FROM `{path}`"})

batch = int(sys.argv[1])
queries = queries[batch * 6 : (batch+1)*6]
code = '''
import json, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from google.cloud import bigquery
from app.core.bigquery import get_bigquery_client
client = get_bigquery_client().client
queries = ''' + ascii(queries) + '''
def run(q):
    try:
        dry = client.query(q['sql'],job_config=bigquery.QueryJobConfig(dry_run=True,use_query_cache=False),timeout=15)
        if dry.total_bytes_processed > 2_000_000_000:
            return {'id':q['id'],'dry_bytes':dry.total_bytes_processed,'skipped':'scan limit'}
        job = client.query(q['sql'],job_config=bigquery.QueryJobConfig(maximum_bytes_billed=2_000_000_000),timeout=15)
        rows = [dict(r.items()) for r in job.result(timeout=25)]
        return {'id':q['id'],'rows':rows,'dry_bytes':dry.total_bytes_processed,'billed_bytes':job.total_bytes_billed,'query_job_id':job.job_id}
    except Exception as e:
        return {'id':q['id'],'error_type':type(e).__name__,'error':str(e)[:500]}
with ThreadPoolExecutor(max_workers=6) as executor:
    results = list(executor.map(run,queries))
print(json.dumps({'queried_at_kst':dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(),'results':results},default=str,ensure_ascii=True))
'''
value = access.remote(code)
out = Path(__file__).with_name('status_bq_dates_' + str(batch) + '.json')
assert not out.exists()
out.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(value,ensure_ascii=False))
