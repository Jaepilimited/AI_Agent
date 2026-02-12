"""Extract all unique product names from SET column."""
import sys
sys.path.insert(0, "C:/Users/DB_PC/Desktop/python_bcj/AI_Agent")

from app.core.bigquery import get_bigquery_client

bq = get_bigquery_client()

sql = """
SELECT DISTINCT single_product
FROM (
  SELECT TRIM(product) as single_product
  FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`,
  UNNEST(SPLIT(`SET`, ' + ')) as product
  WHERE `SET` IS NOT NULL AND `SET` != ''
)
ORDER BY single_product
"""

results = bq.execute_query(sql, timeout=60.0, max_rows=5000)

with open("C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/all_products.txt", "w", encoding="utf-8") as f:
    for r in results:
        f.write(r["single_product"] + "\n")
    f.write(f"\nTotal: {len(results)} unique products")

print(f"Total unique products: {len(results)}")
