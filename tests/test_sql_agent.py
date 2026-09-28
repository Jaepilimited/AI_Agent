"""Tests for Text-to-SQL Agent."""

import pytest

from app.core.security import sanitize_sql, validate_sql


class TestSQLValidation:
    """Tests for SQL safety validation."""

    def test_valid_select(self):
        sql = "SELECT * FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` LIMIT 100"
        is_valid, error = validate_sql(sql)
        assert is_valid is True
        assert error == ""

    def test_block_delete(self):
        sql = "DELETE FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`"
        is_valid, error = validate_sql(sql)
        assert is_valid is False
        assert "SELECT 문만" in error

    def test_block_drop(self):
        sql = "DROP TABLE `skin1004-319714.Sales_Integration.SALES_ALL_Backup`"
        is_valid, error = validate_sql(sql)
        assert is_valid is False

    def test_block_insert(self):
        sql = "INSERT INTO `skin1004-319714.Sales_Integration.SALES_ALL_Backup` VALUES (1)"
        is_valid, error = validate_sql(sql)
        assert is_valid is False

    def test_block_update(self):
        sql = "UPDATE `skin1004-319714.Sales_Integration.SALES_ALL_Backup` SET x=1"
        is_valid, error = validate_sql(sql)
        assert is_valid is False

    def test_block_stacked_query(self):
        sql = "SELECT 1; DROP TABLE users"
        is_valid, error = validate_sql(sql)
        assert is_valid is False

    def test_block_unauthorized_table(self):
        sql = "SELECT * FROM `other-project.dataset.table` LIMIT 10"
        is_valid, error = validate_sql(sql)
        assert is_valid is False
        assert "허용되지 않은 테이블" in error

    def test_empty_sql(self):
        is_valid, error = validate_sql("")
        assert is_valid is False

    def test_valid_aggregation(self):
        sql = """
        SELECT Country, SUM(Revenue) AS total
        FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
        GROUP BY Country
        LIMIT 100
        """
        is_valid, error = validate_sql(sql)
        assert is_valid is True

    def test_valid_date_filter(self):
        sql = """
        SELECT SUM(Revenue) AS total_revenue
        FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
        WHERE EXTRACT(YEAR FROM OrderDate) = 2024
          AND EXTRACT(MONTH FROM OrderDate) = 1
        LIMIT 1000
        """
        is_valid, error = validate_sql(sql)
        assert is_valid is True


class TestSQLSanitize:
    """Tests for SQL sanitization."""

    def test_remove_markdown(self):
        sql = "```sql\nSELECT 1\n```"
        result = sanitize_sql(sql)
        assert "```" not in result
        assert "SELECT 1" in result

    def test_add_limit(self):
        sql = "SELECT * FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`"
        result = sanitize_sql(sql)
        assert "LIMIT" in result

    def test_preserve_existing_limit(self):
        sql = "SELECT * FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` LIMIT 50"
        result = sanitize_sql(sql)
        assert "LIMIT 50" in result


class TestPartitionFilter:
    """Tests for _enforce_partition_filter."""

    def test_no_change_when_date_filter_present(self):
        from app.agents.sql_agent import _enforce_partition_filter
        sql = (
            "SELECT Country, SUM(Revenue) AS total "
            "FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` "
            "WHERE Date BETWEEN '2025-01-01' AND '2025-03-31' "
            "GROUP BY Country"
        )
        result = _enforce_partition_filter(sql, "국가별 매출")
        assert result == sql  # unchanged

    def test_no_change_for_small_table(self):
        from app.agents.sql_agent import _enforce_partition_filter
        sql = (
            "SELECT Name FROM `skin1004-319714.Sales_Integration.Product` LIMIT 10"
        )
        result = _enforce_partition_filter(sql, "제품 목록")
        assert result == sql  # Product is not a large table

    def test_no_change_when_date_filter_lowercase(self):
        from app.agents.sql_agent import _enforce_partition_filter
        sql = (
            "SELECT Country, SUM(Revenue) "
            "FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` "
            "WHERE date >= '2025-01-01' GROUP BY Country"
        )
        result = _enforce_partition_filter(sql, "매출")
        assert result == sql  # has date filter (case-insensitive)

    def test_detects_missing_filter_on_sales_table(self):
        from app.agents.sql_agent import _enforce_partition_filter
        sql = (
            "SELECT Country, SUM(Revenue) AS total "
            "FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` "
            "GROUP BY Country ORDER BY total DESC LIMIT 10"
        )
        import unittest.mock as mock
        sentinel_sql = (
            "SELECT Country, SUM(Revenue) AS total "
            "FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` "
            "WHERE Date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) "
            "GROUP BY Country ORDER BY total DESC LIMIT 10"
        )
        with mock.patch("app.agents.sql_agent.get_flash_client") as mock_flash, \
             mock.patch("app.agents.sql_agent.validate_sql", return_value=(True, "")):
            mock_client = mock.MagicMock()
            mock_client.generate.return_value = sentinel_sql
            mock_flash.return_value = mock_client
            result = _enforce_partition_filter(sql, "국가별 매출")
        assert result == sentinel_sql

    def test_flash_failure_returns_original_sql(self):
        from app.agents.sql_agent import _enforce_partition_filter
        import unittest.mock as mock
        sql = (
            "SELECT Country, SUM(Revenue) AS total "
            "FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` "
            "GROUP BY Country LIMIT 10"
        )
        with mock.patch("app.agents.sql_agent.get_flash_client") as mock_flash:
            mock_flash.side_effect = Exception("network timeout")
            result = _enforce_partition_filter(sql, "매출")
        assert result == sql

    def test_detects_missing_filter_on_integrated_ad(self):
        from app.agents.sql_agent import _enforce_partition_filter
        import unittest.mock as mock
        sql = (
            "SELECT Media, SUM(Spend) AS total_spend "
            "FROM `skin1004-319714.ad_data.integrated_ad` "
            "GROUP BY Media ORDER BY total_spend DESC"
        )
        sentinel_sql = (
            "SELECT Media, SUM(Spend) AS total_spend "
            "FROM `skin1004-319714.ad_data.integrated_ad` "
            "WHERE Date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) AND CURRENT_DATE() "
            "GROUP BY Media ORDER BY total_spend DESC LIMIT 100"
        )
        with mock.patch("app.agents.sql_agent.get_flash_client") as mock_flash, \
             mock.patch("app.agents.sql_agent.validate_sql", return_value=(True, "")):
            mock_client = mock.MagicMock()
            mock_client.generate.return_value = sentinel_sql
            mock_flash.return_value = mock_client
            result = _enforce_partition_filter(sql, "광고 매체별 지출")
        assert result == sentinel_sql

    def test_detects_missing_filter_on_integrated_marketing_cost(self):
        from app.agents.sql_agent import _enforce_partition_filter
        import unittest.mock as mock
        sql = (
            "SELECT Channel, SUM(Cost) AS total_cost "
            "FROM `skin1004-319714.ad_data.Integrated_marketing_cost` "
            "GROUP BY Channel"
        )
        sentinel_sql = (
            "SELECT Channel, SUM(Cost) AS total_cost "
            "FROM `skin1004-319714.ad_data.Integrated_marketing_cost` "
            "WHERE Date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) "
            "GROUP BY Channel LIMIT 100"
        )
        with mock.patch("app.agents.sql_agent.get_flash_client") as mock_flash, \
             mock.patch("app.agents.sql_agent.validate_sql", return_value=(True, "")):
            mock_client = mock.MagicMock()
            mock_client.generate.return_value = sentinel_sql
            mock_flash.return_value = mock_client
            result = _enforce_partition_filter(sql, "마케팅 비용")
        assert result == sentinel_sql


class TestFunctionFromIsNotATable:
    """함수 안의 FROM(`EXTRACT(YEAR FROM s.Date)`)을 테이블 절로 읽어 멀쩡한 SQL 을 막던 오탐.

    2026-09-28 프로덕션 실측: `허용되지 않은 테이블입니다: skin1004-319714.s.Date` 로
    별칭 붙인 매출 SQL 이 하루 2건 죽었다. 사용자 눈에는 조회 실패로 보인다.
    """

    _T = "`skin1004-319714.Sales_Integration.SALES_ALL_Backup`"

    @pytest.mark.parametrize("sql", [
        f"SELECT EXTRACT(YEAR FROM s.Date) y, SUM(s.Sales1_R) r FROM {_T} s GROUP BY y LIMIT 100",
        f"SELECT extract(month from s.Date) m FROM {_T} s GROUP BY m LIMIT 100",
        f"SELECT SUBSTRING(t.Country FROM 1 FOR 2) c FROM {_T} t LIMIT 10",
        f"SELECT TRIM(BOTH ' ' FROM t.Country) c FROM {_T} t LIMIT 10",
        f"WITH f AS (SELECT `SET` p, MIN(Date) d FROM {_T} GROUP BY p) "
        f"SELECT EXTRACT(YEAR FROM f.d) y, COUNT(*) FROM f GROUP BY y LIMIT 100",
    ])
    def test_alias_column_after_function_from_is_allowed(self, sql):
        ok, err = validate_sql(sql)
        assert ok, err

    def test_real_table_after_from_is_still_checked(self):
        sql = ("SELECT EXTRACT(YEAR FROM s.Date) y FROM "
               "`skin1004-319714.Sales_Integration.NOT_ALLOWED` s GROUP BY y LIMIT 10")
        ok, err = validate_sql(sql)
        assert not ok and "허용되지 않은 테이블" in err

    def test_short_dataset_table_after_real_from_is_still_checked(self):
        sql = "SELECT EXTRACT(YEAR FROM s.Date) y FROM Sales_Integration.NOT_ALLOWED s GROUP BY y LIMIT 10"
        ok, err = validate_sql(sql)
        assert not ok and "NOT_ALLOWED" in err
