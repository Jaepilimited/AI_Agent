"""fid 21-23 유형 SQL이 현재 validate_sql을 통과하는지 확인 (임시)"""
import sys, io, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from app.core.security import validate_sql

# fid 22 유형: CTE + 제품 SKU 필터 + FORMAT_DATETIME
sql1 = """WITH t AS (
  SELECT Date, Total_Qty, Sales1_R
  FROM `skin1004-319714.Sales_Integration.Product`
  WHERE SKU = 'SK_Hyalucica_Waterfit_Sun_Serum_OTC_50ml'
)
SELECT
  FORMAT_DATETIME('%Y-%m', t.Date) AS month,
  SUM(t.Total_Qty) AS monthly_quantity,
  SUM(t.Sales1_R) AS monthly_revenue
FROM t
GROUP BY month ORDER BY month"""

# fid 23 유형: CASE WHEN + 한글 라벨 + LIKE 패턴
sql2 = """SELECT
  CASE
    WHEN t.SKU LIKE '%SK_Centella_Hyalucica_Waterfit_Sun_Serum_50ml%' THEN '워터핏 선 세럼 50ml'
    WHEN t.SKU LIKE '%SK_Centella_Hyalucica_Waterfit_Sun_Serum_UV_50ml_OTC%' THEN '스킨1004 마다가스카르 센텔라 히알루-시카 워터핏 선 세럼 유브이 50ml_OTC'
    ELSE 'Other Hyalu-Cica Sunscreen'
  END AS product_name,
  SUM(t.Total_Qty) AS total_quantity,
  SUM(t.Sales1_R) AS total_revenue
FROM `skin1004-319714.Sales_Integration.Product` t
GROUP BY product_name
LIMIT 100"""

# 백틱이 닫히지 않은 비정형 케이스 (LLM 실수 시나리오)
sql3 = """SELECT SUM(Sales1_R) FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup WHERE Brand = 'SK' LIMIT 10"""

for name, sql in [("fid22-type", sql1), ("fid23-type", sql2), ("unbalanced-backtick", sql3)]:
    ok, err = validate_sql(sql)
    print(f"{name}: valid={ok} err={err[:120]}")

