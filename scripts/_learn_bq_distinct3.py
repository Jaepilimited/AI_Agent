"""인코딩 문제 컬럼 — print 없이 JSON만 저장"""
import os, sys, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "C:/json_key/skin1004-319714-60527c477460.json")

from google.cloud import bigquery

PROJECT = "skin1004-319714"
client = bigquery.Client(project=PROJECT)

def run_silent(sql, label):
    try:
        rows = list(client.query(sql).result())
        vals = sorted([str(r[0]) for r in rows if r[0] is not None and str(r[0]).strip() not in ("", "None", "nan")])
        return vals
    except Exception as e:
        return [f"ERROR: {e}"]

results = {}

A  = f"`{PROJECT}.marketing_analysis.integrated_ad`"
MC = f"`{PROJECT}.marketing_analysis.Integrated_marketing_cost`"
RD = f"`{PROJECT}.Platform_Data.raw_data`"
T  = f"`{PROJECT}.Sales_Integration.SALES_ALL_Backup`"

results["ad_country"]   = run_silent(f"SELECT DISTINCT country FROM {A} WHERE country IS NOT NULL ORDER BY country", "ad_country")
results["mc_Country"]   = run_silent(f"SELECT DISTINCT Country FROM {MC} WHERE Country IS NOT NULL ORDER BY Country LIMIT 100", "mc_Country")
results["rd_Category_B"]= run_silent(f"SELECT DISTINCT Category_B FROM {RD} WHERE Category_B IS NOT NULL ORDER BY Category_B LIMIT 50", "Category_B")
results["rd_Category_M"]= run_silent(f"SELECT DISTINCT Category_M FROM {RD} WHERE Category_M IS NOT NULL ORDER BY Category_M LIMIT 100", "Category_M")
results["rd_Brand_Name"]= run_silent(f"SELECT DISTINCT Brand_Name FROM {RD} WHERE Brand_Name IS NOT NULL ORDER BY Brand_Name LIMIT 100", "Brand_Name")
results["rd_Currency"]  = run_silent(f"SELECT DISTINCT Currency FROM {RD} WHERE Currency IS NOT NULL ORDER BY Currency", "Currency")
results["Order_Status"] = run_silent(f"SELECT DISTINCT Order_Status FROM {T} WHERE Order_Status IS NOT NULL AND Order_Status != '' ORDER BY Order_Status LIMIT 50", "Order_Status")

base_path = os.path.join(os.path.dirname(__file__), "_bq_distinct_results.json")
with open(base_path, "r", encoding="utf-8") as f:
    base = json.load(f)
base.update(results)
with open(base_path, "w", encoding="utf-8") as f:
    json.dump(base, f, ensure_ascii=False, indent=2)

# 결과 요약만 출력
for k, v in results.items():
    try:
        safe = f"[{k}] {len(v)}개 수집"
        print(safe)
    except Exception:
        pass
print("Done.")
