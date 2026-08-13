"""SKIN1004 Enterprise AI - Configuration Management."""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # GCP
    gcp_project_id: str = "skin1004-319714"
    google_application_credentials: str = "C:/json_key/skin1004-319714-60527c477460.json"

    # Gemini
    gemini_model: str = "gemini-3.1-pro-preview"
    gemini_flash_model: str = "gemini-3.5-flash"
    gemini_api_key: str = ""

    # BigQuery - Sales
    bq_dataset_sales: str = "Sales_Integration"
    bq_table_sales: str = "SALES_ALL_Backup"
    bq_table_product: str = "Product"

    # BigQuery - RAG
    bq_dataset_rag: str = "AI_RAG"
    bq_table_embeddings: str = "rag_embeddings"
    bq_table_qa_logs: str = "qa_logs"

    # Embedding
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 768

    # Anthropic (v3.0) — Opus (complex) + Sonnet (light)
    anthropic_api_key: str = ""
    anthropic_opus_model: str = "claude-opus-5"
    anthropic_sonnet_model: str = "claude-sonnet-5"
    # Opus 5 는 thinking 이 기본 ON 이다. 깊이(low|medium|high|xhigh|max)가 지연과
    # 직결되므로 값으로 둔다. **첫 청크 실측** (2026-08-13, 같은 질문 2회 최솟값):
    #     4.8(thinking 없음) 1.1s │ 5·low 1.8s │ 5·medium 2.4s │ 5·high 3.8s
    # 채팅은 첫 청크가 체감의 전부라 low 로 둔다. 올릴 땐 반드시 다시 재고 올릴 것.
    anthropic_effort: str = "low"

    # Notion MCP (v3.0)
    notion_mcp_token: str = ""

    # Google OAuth (GWS per-user auth)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:3000/auth/google/callback"
    gws_default_email: str = ""

    # Open WebUI integration (read OAuth tokens from its DB)
    openwebui_db_path: str = ""
    openwebui_secret_key: str = ""

    # CS DB (Google Spreadsheet with Q&A data)
    cs_spreadsheet_id: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 3000

    # Chart — use empty string to auto-detect from request
    chart_base_url: str = ""

    # Auth (custom frontend)
    jwt_secret_key: str = "skin1004-ai-secret-change-me"
    sqlite_db_path: str = "C:/Users/DB_PC/.open-webui/data/skin1004_chat.db"

    # Pseudonymization salt for anon_id derivation (hmac-sha256(salt, user_id)[:16]).
    # Set via ANON_SALT env var. Empty default lets the server start in dev, but
    # compute_anon_id() raises at call time if salt is missing, so production paths
    # fail loudly rather than silently producing identical hashes for every user.
    anon_salt: str = ""

    # MariaDB (AD user management)
    mariadb_host: str = "localhost"
    mariadb_port: str = "3306"
    mariadb_user: str = ""
    mariadb_password: str = ""
    mariadb_database: str = "skin1004_ai"

    # LDAP / Active Directory
    ad_server: str = ""
    ad_user: str = ""
    ad_password: str = ""
    ad_search_base: str = ""

    # 서버 이관 리다이렉트 (2026-07-30)
    # 값이 있으면 모든 사용자 요청을 이 주소로 307 리다이렉트한다. 이관 후 기존 주소로
    # 들어오는 접속 때문에 대화가 구 DB에만 쌓여 데이터가 갈라지는 것을 막는 장치.
    # 되돌리려면 .env 에서 MIGRATED_REDIRECT_URL 을 비우고 재기동.
    migrated_redirect_url: str = ""
    # 자가 점검 결과를 잔디로 보낼지. 기본 꺼짐 —
    # WAS 는 프록시에서 wh.jandi.com 이 막혀 있고, 운영상 잔디 알림을 쓰지 않기로 했다.
    # 결과는 Admin > 자가 점검 탭과 사이드바 배지로 확인한다.
    self_check_notify: bool = False

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:3001,http://localhost:8000,http://172.16.1.250:3000,http://172.16.1.250:3001"
    # Cookie
    cookie_secure: bool = False

    @property
    def sales_table_full_path(self) -> str:
        """Full BigQuery path for the sales table."""
        return f"{self.gcp_project_id}.{self.bq_dataset_sales}.{self.bq_table_sales}"

    @property
    def product_table_full_path(self) -> str:
        """Full BigQuery path for the product table."""
        return f"{self.gcp_project_id}.{self.bq_dataset_sales}.{self.bq_table_product}"

    @property
    def embeddings_table_full_path(self) -> str:
        """Full BigQuery path for the embeddings table."""
        return f"{self.gcp_project_id}.{self.bq_dataset_rag}.{self.bq_table_embeddings}"

    @property
    def qa_logs_table_full_path(self) -> str:
        """Full BigQuery path for the QA logs table."""
        return f"{self.gcp_project_id}.{self.bq_dataset_rag}.{self.bq_table_qa_logs}"

    @property
    def gws_token_dir(self) -> str:
        """Directory for storing per-user Google OAuth tokens."""
        return "data/gws_tokens"

    @property
    def allowed_tables(self) -> List[str]:
        """Tables allowed for Text-to-SQL queries."""
        return [
            # Sales
            self.sales_table_full_path,
            self.product_table_full_path,
            # Marketing / Advertising
            "skin1004-319714.marketing_analysis.integrated_ad",
            "skin1004-319714.marketing_analysis.Integrated_marketing_cost",
            "skin1004-319714.marketing_analysis.shopify_analysis_sales",
            "skin1004-319714.Platform_Data.raw_data",
            "skin1004-319714.marketing_analysis.influencer_input_ALL_TEAMS",
            "skin1004-319714.marketing_analysis.amazon_search_analytics_catalog_performance",
            # Reviews (New_ schema — 2026-04 updated)
            "skin1004-319714.Review_Data.New_Amazon_Review",
            "skin1004-319714.Review_Data.New_Qoo10_Review",
            "skin1004-319714.Review_Data.New_Shopee_Review",
            "skin1004-319714.Review_Data.New_Smartstore_Review",
            # Ad data
            "skin1004-319714.ad_data.meta data_test",
            # Promotion calendar (실행 일정 — 팀·몰별 프로모션 스케줄)
            "skin1004-319714.promotion_calendar.promotion",
            # Financial P&L (FI Dashboard — monthly consolidated income statement)
            "skin1004-319714.Sales_Integration.FI_LLM_Flat",
        ]


@lru_cache()
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
