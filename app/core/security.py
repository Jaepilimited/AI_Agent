"""SQL safety validation for Text-to-SQL Agent.

All generated SQL must pass these checks before execution.
"""

import re
from typing import List, Tuple

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

ALLOWED_STATEMENTS = {"SELECT"}

BLOCKED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "MERGE", "GRANT", "REVOKE", "EXEC", "EXECUTE",
    "CALL", "INTO",  # blocks SELECT INTO as well
}

# Patterns that indicate potential SQL injection
INJECTION_PATTERNS = [
    r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE)",  # stacked queries
    r"--\s",          # SQL comments
    r"/\*.*?\*/",     # block comments
    r"xp_\w+",        # extended procedures
    r"INFORMATION_SCHEMA",
    r"sys\.\w+",
]

MAX_TIMEOUT_SECONDS = 30
MAX_RESULT_ROWS = 10000


def validate_sql(sql: str) -> Tuple[bool, str]:
    """Validate SQL query for safety.

    Args:
        sql: The SQL query to validate.

    Returns:
        Tuple of (is_valid, error_message).
        If valid, error_message is empty string.
    """
    if not sql or not sql.strip():
        return False, "빈 SQL 쿼리입니다."

    normalized = sql.strip().upper()

    # 1. Check that it starts with SELECT or WITH (CTE)
    if not (normalized.startswith("SELECT") or normalized.startswith("(SELECT") or normalized.startswith("WITH")):
        return False, "SELECT 문만 허용됩니다. 다른 SQL 문은 실행할 수 없습니다."

    # 2. Check for blocked keywords
    # Tokenize to avoid matching substrings (e.g., "UPDATE" in column name)
    tokens = set(re.findall(r'\b[A-Z_]+\b', normalized))
    blocked_found = tokens & BLOCKED_KEYWORDS
    if blocked_found:
        return False, f"금지된 키워드가 포함되어 있습니다: {', '.join(blocked_found)}"

    # 3. Check for injection patterns
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return False, f"잠재적 SQL 인젝션 패턴이 감지되었습니다."

    # 4. Check table whitelist
    settings = get_settings()
    table_valid, table_error = _validate_tables(sql, settings.allowed_tables)
    if not table_valid:
        return False, table_error

    # 5. Check for LIMIT clause (warn if missing, but don't block)
    if "LIMIT" not in normalized:
        logger.warning("sql_missing_limit", sql=sql[:200])

    logger.info("sql_validation_passed", sql=sql[:200])
    return True, ""


def _validate_tables(sql: str, allowed_tables: List[str]) -> Tuple[bool, str]:
    """Validate that only allowed tables are referenced.

    Args:
        sql: The SQL query.
        allowed_tables: List of allowed full table paths.

    Returns:
        Tuple of (is_valid, error_message).
    """
    # Extract table references from FROM and JOIN clauses
    # Matches backtick-quoted table names: `project.dataset.table`
    table_pattern = r'`([^`]+\.[^`]+\.[^`]+)`'
    referenced_tables = re.findall(table_pattern, sql)

    if not referenced_tables:
        # Also check for unquoted table references
        from_pattern = r'(?:FROM|JOIN)\s+(\S+\.\S+\.\S+)'
        referenced_tables = re.findall(from_pattern, sql, re.IGNORECASE)

    for table in referenced_tables:
        table_clean = table.strip('`').strip()
        if table_clean not in allowed_tables:
            return False, f"허용되지 않은 테이블입니다: {table_clean}"

    return True, ""


def sanitize_sql(sql: str) -> str:
    """Clean up SQL query formatting and extract SQL from mixed responses.

    Args:
        sql: Raw SQL query (may contain markdown, JSON, or explanatory text).

    Returns:
        Cleaned SQL query, or empty string if no valid SQL found.
    """
    if not sql or not sql.strip():
        return ""

    # Remove markdown code blocks if present
    sql = re.sub(r'```sql\s*', '', sql)
    sql = re.sub(r'```\s*', '', sql)

    # Strip whitespace
    sql = sql.strip()

    # If response starts with JSON-like content, try to extract SQL
    if sql.startswith('{') or sql.startswith('['):
        logger.warning("sanitize_sql_json_response", preview=sql[:200])
        return ""

    # Find SQL statement if response contains explanatory text
    # Look for WITH ... or SELECT ... FROM pattern
    upper_sql = sql.upper().strip()
    if upper_sql.startswith('WITH') or upper_sql.startswith('SELECT') or upper_sql.startswith('(SELECT'):
        # Already starts with valid SQL — keep as-is
        pass
    else:
        # Try to extract WITH or SELECT from mixed text
        with_match = re.search(
            r'(WITH\s+\w+\s+AS\s*\([\s\S]*?FROM\s+[\s\S]*?)(?:```|$)',
            sql,
            re.IGNORECASE,
        )
        select_match = re.search(
            r'(\(?SELECT\s+[\s\S]*?FROM\s+[\s\S]*?)(?:```|$)',
            sql,
            re.IGNORECASE,
        )
        if with_match:
            sql = with_match.group(1).strip()
        elif select_match:
            sql = select_match.group(1).strip()
        else:
            logger.warning("sanitize_sql_no_select", preview=sql[:200])
            return ""

    # Strip whitespace again
    sql = sql.strip()

    # Remove trailing explanatory text after the SQL
    # Look for common patterns that indicate end of SQL
    end_patterns = [
        r'\n\n이\s',  # Korean explanation starting with "이"
        r'\n\n위\s',  # Korean explanation starting with "위"
        r'\n\n참고',  # Korean "참고" (note)
        r'\n\nNote:',
        r'\n\nThis query',
    ]
    for pattern in end_patterns:
        match = re.search(pattern, sql, re.IGNORECASE)
        if match:
            sql = sql[:match.start()].strip()

    # Ensure LIMIT exists
    normalized = sql.upper()
    if "LIMIT" not in normalized:
        sql = f"{sql}\nLIMIT {MAX_RESULT_ROWS}"

    return sql
