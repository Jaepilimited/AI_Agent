"""BigQuery 전체 테이블 distinct 값 학습 스크립트.
실행: python scripts/_learn_bq_distinct.py
결과: scripts/_bq_distinct_results.txt
"""

import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "C:/json_key/skin1004-319714-60527c477460.json")

from google.cloud import bigquery

PROJECT = "skin1004-319714"
client = bigquery.Client(project=PROJECT)

def run(sql, label=""):
    try:
        rows = list(client.query(sql).result())
        vals = sorted([str(r[0]) for r in rows if r[0] is not None])
        print(f"  [{label}] {len(vals)}개: {vals}")
        return vals
    except Exception as e:
        print(f"  [{label}] ERROR: {e}")
        return []

results = {}

print("=" * 60)
print("1. SALES_ALL_Backup")
print("=" * 60)
T = f"`{PROJECT}.Sales_Integration.SALES_ALL_Backup`"

results["Brand"] = run(f"SELECT DISTINCT Brand FROM {T} WHERE Brand IS NOT NULL ORDER BY Brand", "Brand")
results["Sales_Type"] = run(f"SELECT DISTINCT Sales_Type FROM {T} WHERE Sales_Type IS NOT NULL ORDER BY Sales_Type", "Sales_Type")
results["Country"] = run(f"SELECT DISTINCT Country FROM {T} WHERE Country IS NOT NULL ORDER BY Country LIMIT 200", "Country")
results["Continent1"] = run(f"SELECT DISTINCT Continent1 FROM {T} WHERE Continent1 IS NOT NULL ORDER BY Continent1", "Continent1")
results["Continent2"] = run(f"SELECT DISTINCT Continent2 FROM {T} WHERE Continent2 IS NOT NULL ORDER BY Continent2", "Continent2")
results["Mall_Classification"] = run(f"SELECT DISTINCT Mall_Classification FROM {T} WHERE Mall_Classification IS NOT NULL ORDER BY Mall_Classification LIMIT 300", "Mall_Classification")
results["Team_NEW"] = run(f"SELECT DISTINCT Team_NEW FROM {T} WHERE Team_NEW IS NOT NULL ORDER BY Team_NEW", "Team_NEW")
results["Category"] = run(f"SELECT DISTINCT Category FROM {T} WHERE Category IS NOT NULL ORDER BY Category", "Category")
results["Line"] = run(f"SELECT DISTINCT Line FROM {T} WHERE Line IS NOT NULL ORDER BY Line LIMIT 100", "Line")
results["Sales_Currency"] = run(f"SELECT DISTINCT Sales_Currency FROM {T} WHERE Sales_Currency IS NOT NULL ORDER BY Sales_Currency", "Sales_Currency")
results["New_Flag"] = run(f"SELECT DISTINCT New_Flag FROM {T} WHERE New_Flag IS NOT NULL ORDER BY New_Flag", "New_Flag")
results["Order_Status"] = run(f"SELECT DISTINCT Order_Status FROM {T} WHERE Order_Status IS NOT NULL ORDER BY Order_Status LIMIT 50", "Order_Status")
results["FOC_or_Not"] = run(f"SELECT DISTINCT FOC_or_Not FROM {T} WHERE FOC_or_Not IS NOT NULL ORDER BY FOC_or_Not", "FOC_or_Not")
results["Type_of_Company"] = run(f"SELECT DISTINCT Type_of_Company FROM {T} WHERE Type_of_Company IS NOT NULL ORDER BY Type_of_Company LIMIT 50", "Type_of_Company")

print()
print("=" * 60)
print("2. Product 테이블")
print("=" * 60)
P = f"`{PROJECT}.Sales_Integration.Product`"

results["Product_Brand"] = run(f"SELECT DISTINCT Brand FROM {P} WHERE Brand IS NOT NULL ORDER BY Brand", "Brand")
results["Product_Category"] = run(f"SELECT DISTINCT Category FROM {P} WHERE Category IS NOT NULL ORDER BY Category", "Category")
results["Product_Line"] = run(f"SELECT DISTINCT Line FROM {P} WHERE Line IS NOT NULL ORDER BY Line LIMIT 100", "Line")
results["Product_Country"] = run(f"SELECT DISTINCT Country FROM {P} WHERE Country IS NOT NULL ORDER BY Country LIMIT 200", "Country")
results["Product_Team_NEW"] = run(f"SELECT DISTINCT Team_NEW FROM {P} WHERE Team_NEW IS NOT NULL ORDER BY Team_NEW", "Team_NEW")

print()
print("=" * 60)
print("3. integrated_ad (통합 광고)")
print("=" * 60)
A = f"`{PROJECT}.marketing_analysis.integrated_ad`"

results["ad_media"] = run(f"SELECT DISTINCT media FROM {A} WHERE media IS NOT NULL ORDER BY media", "media")
results["ad_country"] = run(f"SELECT DISTINCT country FROM {A} WHERE country IS NOT NULL ORDER BY country", "country")
results["ad_team"] = run(f"SELECT DISTINCT team FROM {A} WHERE team IS NOT NULL ORDER BY team", "team")

print()
print("=" * 60)
print("4. Integrated_marketing_cost (마케팅 비용)")
print("=" * 60)
MC = f"`{PROJECT}.marketing_analysis.Integrated_marketing_cost`"

results["mc_Field"] = run(f"SELECT DISTINCT Field FROM {MC} WHERE Field IS NOT NULL ORDER BY Field", "Field")
results["mc_Team"] = run(f"SELECT DISTINCT Team FROM {MC} WHERE Team IS NOT NULL ORDER BY Team", "Team")
results["mc_Media"] = run(f"SELECT DISTINCT Media FROM {MC} WHERE Media IS NOT NULL ORDER BY Media LIMIT 100", "Media")
results["mc_Country"] = run(f"SELECT DISTINCT Country FROM {MC} WHERE Country IS NOT NULL ORDER BY Country LIMIT 100", "Country")

print()
print("=" * 60)
print("5. influencer_input_ALL_TEAMS (인플루언서)")
print("=" * 60)
INF = f"`{PROJECT}.marketing_analysis.influencer_input_ALL_TEAMS`"

results["inf_add_part"] = run(f"SELECT DISTINCT add_part FROM {INF} WHERE add_part IS NOT NULL ORDER BY add_part", "add_part")
results["inf_Media"] = run(f"SELECT DISTINCT Media FROM {INF} WHERE Media IS NOT NULL ORDER BY Media LIMIT 100", "Media")
results["inf_Media_main"] = run(f"SELECT DISTINCT Media_main FROM {INF} WHERE Media_main IS NOT NULL ORDER BY Media_main", "Media_main")
results["inf_Influencer_tier"] = run(f"SELECT DISTINCT Influencer_tier FROM {INF} WHERE Influencer_tier IS NOT NULL ORDER BY Influencer_tier", "Influencer_tier")
results["inf_Content_type"] = run(f"SELECT DISTINCT Content_type FROM {INF} WHERE Content_type IS NOT NULL ORDER BY Content_type LIMIT 50", "Content_type")
results["inf_Brand"] = run(f"SELECT DISTINCT Brand FROM {INF} WHERE Brand IS NOT NULL ORDER BY Brand", "Brand")
results["inf_Region"] = run(f"SELECT DISTINCT Region FROM {INF} WHERE Region IS NOT NULL ORDER BY Region LIMIT 50", "Region")
results["inf_Contact_type"] = run(f"SELECT DISTINCT Contact_type FROM {INF} WHERE Contact_type IS NOT NULL ORDER BY Contact_type", "Contact_type")
results["inf_Campaign"] = run(f"SELECT DISTINCT Campaign FROM {INF} WHERE Campaign IS NOT NULL ORDER BY Campaign LIMIT 100", "Campaign")
results["inf_Influencer_language"] = run(f"SELECT DISTINCT Influencer_language FROM {INF} WHERE Influencer_language IS NOT NULL ORDER BY Influencer_language LIMIT 50", "Influencer_language")

print()
print("=" * 60)
print("6. shopify_analysis_sales (Shopify 자사몰)")
print("=" * 60)
SH = f"`{PROJECT}.marketing_analysis.shopify_analysis_sales`"

results["shopify_Country"] = run(f"SELECT DISTINCT Country FROM {SH} WHERE Country IS NOT NULL ORDER BY Country LIMIT 100", "Country")
results["shopify_Type"] = run(f"SELECT DISTINCT Type FROM {SH} WHERE Type IS NOT NULL ORDER BY Type LIMIT 50", "Type")

print()
print("=" * 60)
print("7. Platform_Data.raw_data (플랫폼 순위)")
print("=" * 60)
RD = f"`{PROJECT}.Platform_Data.raw_data`"

results["rd_Channel"] = run(f"SELECT DISTINCT Channel FROM {RD} WHERE Channel IS NOT NULL ORDER BY Channel LIMIT 100", "Channel")
results["rd_Category_B"] = run(f"SELECT DISTINCT Category_B FROM {RD} WHERE Category_B IS NOT NULL ORDER BY Category_B LIMIT 50", "Category_B")
results["rd_Category_M"] = run(f"SELECT DISTINCT Category_M FROM {RD} WHERE Category_M IS NOT NULL ORDER BY Category_M LIMIT 50", "Category_M")
results["rd_Brand_Name"] = run(f"SELECT DISTINCT Brand_Name FROM {RD} WHERE Brand_Name IS NOT NULL ORDER BY Brand_Name LIMIT 100", "Brand_Name")
results["rd_Currency"] = run(f"SELECT DISTINCT Currency FROM {RD} WHERE Currency IS NOT NULL ORDER BY Currency", "Currency")

print()
print("=" * 60)
print("8. Review 테이블들")
print("=" * 60)
for tbl, label in [
    ("Review_Data.New_Amazon_Review", "Amazon"),
    ("Review_Data.New_Qoo10_Review", "Qoo10"),
    ("Review_Data.New_Shopee_Review", "Shopee"),
    ("Review_Data.New_Smartstore_Review", "Smartstore"),
]:
    RT = f"`{PROJECT}.{tbl}`"
    results[f"review_{label}_channel"] = run(f"SELECT DISTINCT channel FROM {RT} WHERE channel IS NOT NULL ORDER BY channel LIMIT 30", f"{label} channel")
    results[f"review_{label}_brand"] = run(f"SELECT DISTINCT brand FROM {RT} WHERE brand IS NOT NULL ORDER BY brand LIMIT 30", f"{label} brand")

print()
print("=" * 60)
print("9. meta data_test (메타 광고 라이브러리)")
print("=" * 60)
META = f"`{PROJECT}.ad_data.`meta data_test``"

results["meta_brand"] = run(f"SELECT DISTINCT brand FROM {META} WHERE brand IS NOT NULL ORDER BY brand LIMIT 100", "brand")
results["meta_country_name"] = run(f"SELECT DISTINCT country_name FROM {META} WHERE country_name IS NOT NULL ORDER BY country_name LIMIT 100", "country_name")
results["meta_ad_type"] = run(f"SELECT DISTINCT ad_type FROM {META} WHERE ad_type IS NOT NULL ORDER BY ad_type", "ad_type")

# 결과 저장
out_path = os.path.join(os.path.dirname(__file__), "_bq_distinct_results.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n결과 저장: {out_path}")
