"""Check Product table schema and sample data in BigQuery."""
import sys, json
sys.path.insert(0, "C:/Users/DB_PC/Desktop/python_bcj/AI_Agent")

from app.core.bigquery import get_bigquery_client

bq = get_bigquery_client()

# 1. Check if Product table exists and get schema
queries = [
    ("Table schema", """
        SELECT column_name, data_type, description
        FROM `skin1004-319714.Sales_Integration.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`
        WHERE table_name = 'Product'
        ORDER BY ordinal_position
    """),
    ("Sample 5 rows", """
        SELECT *
        FROM `skin1004-319714.Sales_Integration.Product`
        WHERE Date >= '2026-01-01'
        LIMIT 5
    """),
    ("Row count", """
        SELECT COUNT(*) as cnt
        FROM `skin1004-319714.Sales_Integration.Product`
        WHERE Date >= '2000-01-01'
    """),
    ("Product distinct values (top 30)", """
        SELECT DISTINCT Product, COUNT(*) as cnt
        FROM `skin1004-319714.Sales_Integration.Product`
        WHERE Product IS NOT NULL AND Product != ''
        GROUP BY Product
        ORDER BY cnt DESC
        LIMIT 30
    """),
    ("Product_Name_Base distinct (top 20)", """
        SELECT DISTINCT Product_Name_Base, COUNT(*) as cnt
        FROM `skin1004-319714.Sales_Integration.Product`
        WHERE Product_Name_Base IS NOT NULL AND Product_Name_Base != ''
        GROUP BY Product_Name_Base
        ORDER BY cnt DESC
        LIMIT 20
    """),
]

with open("C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/product_table_info.txt", "w", encoding="utf-8") as f:
    for label, sql in queries:
        try:
            results = bq.execute_query(sql, timeout=30.0, max_rows=100)
            f.write(f"=== {label} ===\n")
            for r in results:
                f.write(f"  {json.dumps(r, ensure_ascii=False, default=str)}\n")
            f.write(f"  ({len(results)} rows)\n\n")
        except Exception as e:
            f.write(f"=== {label} === ERROR: {e}\n\n")

print("Done")
