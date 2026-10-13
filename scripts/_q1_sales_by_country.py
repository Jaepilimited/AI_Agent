"""2026년 1분기 국가별 매출 조회"""
import os, sys, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "C:/json_key/skin1004-319714-60527c477460.json")

from google.cloud import bigquery

client = bigquery.Client(project="skin1004-319714")

sql = """
SELECT
  CASE
    WHEN Country = '\ub�국' THEN '라벨오류'
    ELSE Country
  END as c,
  Country,
  ROUND(SUM(Sales1_R) / 100000000, 1) AS rev
FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
WHERE Date BETWEEN '2026-01-01' AND '2026-03-31'
  AND Brand != 'ETC'
GROUP BY Country
ORDER BY rev DESC
LIMIT 30
"""

# simpler: just get all countries
sql2 = "SELECT DISTINCT Country FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` WHERE Date BETWEEN '2026-01-01' AND '2026-03-31' AND Brand != 'ETC' ORDER BY Country LIMIT 30"

rows = list(client.query(sql2).result())
for r in rows:
    val = str(r[0])
    safe = val.encode('ascii', 'replace').decode('ascii')
    print(repr(val), safe)
