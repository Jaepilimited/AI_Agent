"""실패한 쿼리 재시도 — 인코딩 우회 + meta 테이블 백틱 수정"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "C:/json_key/skin1004-319714-60527c477460.json")

from google.cloud import bigquery

PROJECT = "skin1004-319714"
client = bigquery.Client(project=PROJECT)

def run(sql, label=""):
    try:
        rows = list(client.query(sql).result())
        vals = sorted([str(r[0]) for r in rows if r[0] is not None and str(r[0]).strip() not in ("", "None", "nan")])
        print(f"  [{label}] {len(vals)}개: {vals[:80]}")
        return vals
    except Exception as e:
        print(f"  [{label}] ERROR: {e}")
        return []

results = {}

print("=== integrated_ad country ===")
A = f"`{PROJECT}.marketing_analysis.integrated_ad`"
results["ad_country"] = run(f"SELECT DISTINCT country FROM {A} WHERE country IS NOT NULL ORDER BY country", "ad_country")

print("=== Integrated_marketing_cost country ===")
MC = f"`{PROJECT}.marketing_analysis.Integrated_marketing_cost`"
results["mc_Country"] = run(f"SELECT DISTINCT Country FROM {MC} WHERE Country IS NOT NULL ORDER BY Country LIMIT 100", "mc_Country")

print("=== Platform_Data.raw_data ===")
RD = f"`{PROJECT}.Platform_Data.raw_data`"
results["rd_Category_B"] = run(f"SELECT DISTINCT Category_B FROM {RD} WHERE Category_B IS NOT NULL ORDER BY Category_B LIMIT 50", "Category_B")
results["rd_Category_M"] = run(f"SELECT DISTINCT Category_M FROM {RD} WHERE Category_M IS NOT NULL ORDER BY Category_M LIMIT 100", "Category_M")
results["rd_Category_S"] = run(f"SELECT DISTINCT Category_S FROM {RD} WHERE Category_S IS NOT NULL ORDER BY Category_S LIMIT 100", "Category_S")
results["rd_Brand_Name"] = run(f"SELECT DISTINCT Brand_Name FROM {RD} WHERE Brand_Name IS NOT NULL ORDER BY Brand_Name LIMIT 100", "Brand_Name")
results["rd_Currency"] = run(f"SELECT DISTINCT Currency FROM {RD} WHERE Currency IS NOT NULL ORDER BY Currency", "Currency")

print("=== SALES_ALL Order_Status ===")
T = f"`{PROJECT}.Sales_Integration.SALES_ALL_Backup`"
results["Order_Status"] = run(f"SELECT DISTINCT Order_Status FROM {T} WHERE Order_Status IS NOT NULL AND Order_Status != '' ORDER BY Order_Status LIMIT 50", "Order_Status")

print("=== meta data_test ===")
# 테이블명에 공백 — 데이터셋.테이블 전체를 백틱으로 감싸야 함
META = "`skin1004-319714.ad_data.meta data_test`"
results["meta_brand"] = run(f"SELECT DISTINCT brand FROM {META} WHERE brand IS NOT NULL AND brand != '' AND brand != 'unknown' ORDER BY brand LIMIT 100", "meta_brand")
results["meta_country_name"] = run(f"SELECT DISTINCT country_name FROM {META} WHERE country_name IS NOT NULL AND country_name != '' ORDER BY country_name LIMIT 100", "meta_country_name")
results["meta_ad_type"] = run(f"SELECT DISTINCT ad_type FROM {META} WHERE ad_type IS NOT NULL ORDER BY ad_type", "meta_ad_type")
results["meta_publisher_platform_sample"] = run(f"SELECT DISTINCT publisher_platform FROM {META} WHERE publisher_platform IS NOT NULL ORDER BY publisher_platform LIMIT 30", "meta_publisher_platform")

# 기존 JSON에 병합
base_path = os.path.join(os.path.dirname(__file__), "_bq_distinct_results.json")
with open(base_path, "r", encoding="utf-8") as f:
    base = json.load(f)
base.update(results)
with open(base_path, "w", encoding="utf-8") as f:
    json.dump(base, f, ensure_ascii=False, indent=2)
print(f"\n결과 병합 저장: {base_path}")
