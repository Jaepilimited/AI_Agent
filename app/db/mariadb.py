"""DB layer — MariaDB backend for all environments.

Both production (3000) and development (3001) use MariaDB.

Interface: fetch_all, fetch_one, execute, execute_lastid.
"""
import os

import pymysql
from dbutils.pooled_db import PooledDB
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

_PORT = int(os.environ.get("PORT", "0")) or get_settings().port
logger.info("db_mode_mariadb", port=_PORT)


# ===========================================================================
# MariaDB backend
# ===========================================================================
_pool = None


def _get_pool() -> PooledDB:
    """Get or create MariaDB connection pool (singleton)."""
    global _pool
    if _pool is None:
        s = get_settings()
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=40,
            mincached=5,
            maxcached=15,
            blocking=True,
            host=s.mariadb_host,
            port=int(s.mariadb_port),
            user=s.mariadb_user,
            password=s.mariadb_password,
            database=s.mariadb_database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
        logger.info("mariadb_pool_initialized", max=40, cached=5)
    return _pool


def _maria_fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _maria_fetch_one(sql: str, params: tuple = ()) -> dict | None:
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def _maria_execute(sql: str, params: tuple = ()) -> int:
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount
    finally:
        conn.close()


def _maria_execute_lastid(sql: str, params: tuple = ()) -> int:
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()


# ===========================================================================
# Public interface
# ===========================================================================

fetch_all = _maria_fetch_all
fetch_one = _maria_fetch_one
execute = _maria_execute
execute_lastid = _maria_execute_lastid


def get_maria_conn():
    """Get a pooled MariaDB connection."""
    return _get_pool().connection()


def ensure_fi_permission_column():
    """Add ad_users.can_view_fi if missing (idempotent)."""
    try:
        execute(
            "ALTER TABLE ad_users "
            "ADD COLUMN can_view_fi TINYINT(1) NOT NULL DEFAULT 0 "
            "COMMENT '재무 손익(FI_LLM_Flat) 열람 허용'"
        )
    except Exception:
        pass  # column already exists


# ===========================================================================
# team_resources table DDL
# ===========================================================================
_TEAM_RESOURCES_DDL = """
CREATE TABLE IF NOT EXISTS team_resources (
    id INT AUTO_INCREMENT PRIMARY KEY,
    parent_id INT DEFAULT NULL COMMENT '부모 노드 ID (NULL=팀 루트)',
    team VARCHAR(50) NOT NULL COMMENT '팀명',
    node_type ENUM('team','folder','sheet','page','database','text') NOT NULL DEFAULT 'folder' COMMENT '노드 유형',
    name VARCHAR(500) NOT NULL COMMENT '노드 이름',
    url TEXT DEFAULT NULL COMMENT '링크 URL (리프 노드)',
    description TEXT DEFAULT '' COMMENT '페이지 본문 / 비고',
    resource_type ENUM('google_sheet','notion','google_drive','other') DEFAULT 'other',
    depth INT NOT NULL DEFAULT 0 COMMENT '트리 깊이 (0=팀 루트)',
    sort_order INT DEFAULT 0 COMMENT '같은 parent 내 정렬',
    notion_block_id VARCHAR(36) DEFAULT NULL COMMENT 'Notion 블록 ID',
    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '마지막 동기화',
    CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES team_resources(id) ON DELETE CASCADE,
    INDEX idx_team (team),
    INDEX idx_parent (parent_id),
    INDEX idx_team_depth (team, depth)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_team_resources_table():
    """Create team_resources table if not exists."""
    try:
        execute(_TEAM_RESOURCES_DDL)
    except Exception as e:
        logger.warning("team_resources_table_error", error=str(e))


# ===========================================================================
# knowledge_wiki table DDL — persistent fact store
# ===========================================================================
_KNOWLEDGE_WIKI_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_wiki (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    domain VARCHAR(32) NOT NULL COMMENT '매출|마케팅|제품|팀|노션|기타',
    entity VARCHAR(255) NOT NULL COMMENT '주체 (제품명, 팀명, 지표명 등)',
    canonical_entity VARCHAR(255) DEFAULT NULL COMMENT 'normalized entity name',
    period VARCHAR(64) DEFAULT NULL COMMENT '시점 (YYYY-MM, YYYY-Q#, permanent)',
    metric VARCHAR(128) DEFAULT NULL COMMENT '측정 차원 (sales_usd, mom_growth, category_rank)',
    value TEXT DEFAULT NULL COMMENT '값 (문자열 또는 JSON)',
    summary TEXT NOT NULL COMMENT '자연어 한두 문장 요약',
    embedding JSON DEFAULT NULL COMMENT 'Gemini embedding (768 floats) for semantic search',
    review_status ENUM('none','needs_review','resolved') NOT NULL DEFAULT 'none',
    source_conversation_id VARCHAR(36) DEFAULT NULL,
    source_message_id INT DEFAULT NULL,
    source_route VARCHAR(32) DEFAULT NULL,
    confidence FLOAT NOT NULL DEFAULT 0.5,
    thumbs_up INT NOT NULL DEFAULT 0,
    thumbs_down INT NOT NULL DEFAULT 0,
    status ENUM('pending','active','archived') NOT NULL DEFAULT 'pending',
    extracted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    validated_at DATETIME DEFAULT NULL,
    INDEX idx_domain_entity (domain, entity),
    INDEX idx_canonical (canonical_entity),
    INDEX idx_entity (entity),
    INDEX idx_period (period),
    INDEX idx_status (status),
    INDEX idx_review_status (review_status),
    INDEX idx_extracted_at (extracted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_knowledge_wiki_table():
    """Create knowledge_wiki table if not exists. Also adds optional columns
    on existing tables (canonical_entity, embedding)."""
    try:
        execute(_KNOWLEDGE_WIKI_DDL)
        # Add columns that may not exist on tables created by an older DDL
        for alter in (
            "ALTER TABLE knowledge_wiki ADD COLUMN canonical_entity VARCHAR(255) DEFAULT NULL",
            "ALTER TABLE knowledge_wiki ADD COLUMN embedding JSON DEFAULT NULL",
            "ALTER TABLE knowledge_wiki ADD COLUMN review_status ENUM('none','needs_review','resolved') NOT NULL DEFAULT 'none'",
            "ALTER TABLE knowledge_wiki ADD COLUMN conflict_with_id BIGINT DEFAULT NULL",
            "ALTER TABLE knowledge_wiki ADD COLUMN conflict_reason VARCHAR(255) DEFAULT NULL",
            "ALTER TABLE knowledge_wiki ADD INDEX idx_canonical (canonical_entity)",
            "ALTER TABLE knowledge_wiki ADD INDEX idx_review_status (review_status)",
            "ALTER TABLE knowledge_wiki ADD INDEX idx_conflict (conflict_with_id)",
        ):
            try:
                execute(alter)
            except Exception:
                pass  # column or index already exists
    except Exception as e:
        logger.warning("knowledge_wiki_table_error", error=str(e))


_WIKI_EXTRACTION_LOG_DDL = """
CREATE TABLE IF NOT EXISTS wiki_extraction_log (
    message_id INT NOT NULL PRIMARY KEY COMMENT 'assistant messages.id',
    extracted_count INT NOT NULL DEFAULT 0,
    skipped_reason VARCHAR(64) DEFAULT NULL COMMENT 'route_filter|empty|llm_failed|parse_failed',
    processed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_wiki_extraction_log_table():
    """Create wiki_extraction_log table — prevents re-processing same pair."""
    try:
        execute(_WIKI_EXTRACTION_LOG_DDL)
    except Exception as e:
        logger.warning("wiki_extraction_log_table_error", error=str(e))


_WIKI_ENTITY_ALIASES_DDL = """
CREATE TABLE IF NOT EXISTS wiki_entity_aliases (
    alias VARCHAR(255) NOT NULL PRIMARY KEY COMMENT 'normalized alias form',
    canonical VARCHAR(255) NOT NULL COMMENT 'preferred canonical name',
    source VARCHAR(32) NOT NULL DEFAULT 'manual' COMMENT 'manual|rule|llm',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_canonical (canonical)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_wiki_entity_aliases_table():
    try:
        execute(_WIKI_ENTITY_ALIASES_DDL)
    except Exception as e:
        logger.warning("wiki_entity_aliases_table_error", error=str(e))


_WIKI_GRAPH_EDGES_DDL = """
CREATE TABLE IF NOT EXISTS wiki_graph_edges (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    src_entity VARCHAR(255) NOT NULL,
    dst_entity VARCHAR(255) NOT NULL,
    relation VARCHAR(64) NOT NULL COMMENT 'owns|belongs_to|mentions|compares_to|sells_in|part_of|linked',
    weight FLOAT NOT NULL DEFAULT 1.0,
    source_wiki_ids TEXT DEFAULT NULL COMMENT 'JSON array of knowledge_wiki.id',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_edge (src_entity, dst_entity, relation),
    INDEX idx_src (src_entity),
    INDEX idx_dst (dst_entity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_wiki_graph_edges_table():
    try:
        execute(_WIKI_GRAPH_EDGES_DDL)
        for alter in (
            "ALTER TABLE wiki_graph_edges ADD COLUMN edge_type ENUM('extracted','inferred','ambiguous') NOT NULL DEFAULT 'inferred'",
            "ALTER TABLE wiki_graph_edges ADD COLUMN source_confidence FLOAT NOT NULL DEFAULT 0.5",
            "ALTER TABLE wiki_graph_edges ADD COLUMN community_id INT DEFAULT NULL",
            "ALTER TABLE wiki_graph_edges ADD INDEX idx_edge_type (edge_type)",
            "ALTER TABLE wiki_graph_edges ADD INDEX idx_community (community_id)",
        ):
            try:
                execute(alter)
            except Exception:
                pass
    except Exception as e:
        logger.warning("wiki_graph_edges_table_error", error=str(e))


_WIKI_ENTITY_PAGES_DDL = """
CREATE TABLE IF NOT EXISTS wiki_entity_pages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    canonical_entity VARCHAR(255) NOT NULL UNIQUE,
    domain VARCHAR(32) NOT NULL,
    markdown MEDIUMTEXT NOT NULL COMMENT 'compiled entity page body',
    fact_count INT NOT NULL DEFAULT 0,
    period_span VARCHAR(128) DEFAULT NULL COMMENT 'earliest~latest period',
    community_id INT DEFAULT NULL,
    community_label VARCHAR(128) DEFAULT NULL,
    last_fact_at DATETIME DEFAULT NULL,
    compiled_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_domain (domain),
    INDEX idx_community (community_id),
    INDEX idx_compiled_at (compiled_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_wiki_entity_pages_table():
    try:
        execute(_WIKI_ENTITY_PAGES_DDL)
    except Exception as e:
        logger.warning("wiki_entity_pages_table_error", error=str(e))


_WIKI_COMMUNITIES_DDL = """
CREATE TABLE IF NOT EXISTS wiki_communities (
    id INT AUTO_INCREMENT PRIMARY KEY,
    label VARCHAR(128) NOT NULL,
    size INT NOT NULL DEFAULT 0,
    density FLOAT DEFAULT NULL,
    top_entities JSON DEFAULT NULL,
    detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_wiki_communities_table():
    try:
        execute(_WIKI_COMMUNITIES_DDL)
    except Exception as e:
        logger.warning("wiki_communities_table_error", error=str(e))


# ===========================================================================
# Anonymization — pseudonymous ownership columns on conversations / feedback
# ===========================================================================
_ANON_COLUMN_TARGETS = (
    ("conversations", "anon_id", "VARCHAR(32) NOT NULL DEFAULT ''"),
    ("message_feedback", "anon_id", "VARCHAR(32) NOT NULL DEFAULT ''"),
    ("message_feedback", "comment", "TEXT NULL"),
)


# ===========================================================================
# Eval pipeline — per-question review table for Playwright batch runs
# ===========================================================================
_EVAL_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NULL,
    total INT NOT NULL,
    done INT NOT NULL DEFAULT 0,
    notes TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

_EVAL_QA_DDL = """
CREATE TABLE IF NOT EXISTS eval_qa (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id INT NOT NULL,
    team VARCHAR(100) NOT NULL,
    question TEXT NOT NULL,
    answer MEDIUMTEXT,
    route VARCHAR(32),
    response_time_ms INT,
    conversation_id VARCHAR(36),
    message_id INT,
    source ENUM('real','synthetic') NOT NULL DEFAULT 'synthetic',
    verdict ENUM('pending','good','bad','skip') NOT NULL DEFAULT 'pending',
    reviewed_at DATETIME NULL,
    reviewed_by_anon VARCHAR(32),
    INDEX idx_eval_qa_run (run_id),
    INDEX idx_eval_qa_verdict (verdict),
    INDEX idx_eval_qa_team (team),
    FOREIGN KEY (run_id) REFERENCES eval_runs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_eval_tables():
    """Create eval_runs + eval_qa if not present."""
    try:
        execute(_EVAL_RUNS_DDL)
        execute(_EVAL_QA_DDL)
    except Exception as e:
        logger.warning("eval_tables_error", error=str(e))


_AGENT_SKILLS_DDL = """
CREATE TABLE IF NOT EXISTS agent_skills (
    id INT AUTO_INCREMENT PRIMARY KEY,
    route VARCHAR(32) NOT NULL,
    query_text TEXT NOT NULL,
    response_summary TEXT NOT NULL,
    message_id INT NULL,
    is_negative TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_route_created (route, created_at),
    INDEX idx_route_negative (route, is_negative)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_agent_skills_table():
    try:
        execute(_AGENT_SKILLS_DDL)
        # Add is_negative column to existing tables (idempotent)
        existing = fetch_one(
            "SELECT 1 AS ok FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'agent_skills' AND COLUMN_NAME = 'is_negative'",
        )
        if not existing:
            execute("ALTER TABLE agent_skills ADD COLUMN is_negative TINYINT(1) NOT NULL DEFAULT 0")
            execute("ALTER TABLE agent_skills ADD INDEX idx_route_negative (route, is_negative)")
    except Exception as e:
        logger.warning("agent_skills_table_error", error=str(e))


_QUALITY_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS quality_snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    route VARCHAR(32) NOT NULL,
    accuracy_rate FLOAT DEFAULT NULL COMMENT '👍 / (👍 + 👎)',
    feedback_count INT NOT NULL DEFAULT 0,
    avg_response_ms INT DEFAULT NULL,
    avg_context_len INT DEFAULT NULL,
    request_count INT NOT NULL DEFAULT 0,
    flag_accuracy TINYINT(1) NOT NULL DEFAULT 0,
    flag_speed TINYINT(1) NOT NULL DEFAULT 0,
    flag_context TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_date_route (snapshot_date, route),
    INDEX idx_snapshot_date (snapshot_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_quality_snapshots_table():
    try:
        execute(_QUALITY_SNAPSHOTS_DDL)
    except Exception as e:
        logger.warning("quality_snapshots_table_error", error=str(e))


_KNOWLEDGE_GAPS_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id INT AUTO_INCREMENT PRIMARY KEY,
    question TEXT NOT NULL,
    route VARCHAR(32) NOT NULL DEFAULT 'cs',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed TINYINT(1) NOT NULL DEFAULT 0,
    INDEX idx_kg_route_created (route, created_at),
    INDEX idx_kg_reviewed (reviewed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_knowledge_gaps_table():
    try:
        execute(_KNOWLEDGE_GAPS_DDL)
    except Exception as e:
        logger.warning("knowledge_gaps_table_error", error=str(e))


def ensure_anon_columns():
    """Add anon_id columns + indexes to conversations and message_feedback.

    Idempotent: checks INFORMATION_SCHEMA before ALTERing so repeated startups
    are no-ops. Follows the same pattern as ensure_knowledge_wiki_table().
    """
    for table, col, definition in _ANON_COLUMN_TARGETS:
        try:
            existing = fetch_one(
                "SELECT 1 AS ok FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                (table, col),
            )
            if not existing:
                execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            # Index (separate try — index creation is also idempotent-ish but
            # raises on duplicate name)
            try:
                execute(
                    f"ALTER TABLE {table} ADD INDEX idx_{table}_{col} ({col})"
                )
            except Exception:
                pass  # index already exists
        except Exception as e:
            logger.warning(
                "anon_column_error", table=table, col=col, error=str(e)
            )
