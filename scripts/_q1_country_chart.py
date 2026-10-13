"""2026 Q1 국가별 매출 - JSON으로 저장"""
import os, sys, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "C:/json_key/skin1004-319714-60527c477460.json")

from google.cloud import bigquery
client = bigquery.Client(project="skin1004-319714")

sql = """
SELECT
  CASE
    WHEN Country = '미국' THEN '미국'
    WHEN Country = '일본' THEN '일본'
    WHEN Country IN ('중국', '대만', '홍콩') THEN '중화권'
    WHEN Country = '한국' THEN '국내'
    ELSE '기타'
  END AS country_group,
  ROUND(SUM(Sales1_R) / 100000000, 1) AS revenue
FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
WHERE Date BETWEEN '2026-01-01' AND '2026-03-31'
  AND Brand != 'ETC'
GROUP BY country_group
ORDER BY revenue DESC
"""

rows = list(client.query(sql).result())
data = {str(r[0]): float(r[1]) for r in rows}
total = sum(data.values())

out = {"total": round(total, 1), "groups": data}
out_path = os.path.join(os.path.dirname(__file__), "_q1_country_data.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"total={total:.1f}")
for k, v in data.items():
    pct = v / total * 100
    safe_k = k.encode('ascii', 'replace').decode('ascii')
    print(f"{safe_k}: {v:.1f} ({pct:.1f}%)")
print("saved:", out_path)
