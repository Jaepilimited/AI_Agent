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
    gemini_model: str = "gemini-3-pro-preview"
    gemini_flash_model: str = "gemini-2.5-flash"
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
    anthropic_opus_model: str = "claude-opus-4-6"
    anthropic_sonnet_model: str = "claude-sonnet-4-6"

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

    # Tavily
    tavily_api_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 3000

    # Chart — use empty string to auto-detect from request
    chart_base_url: str = ""

    # Auth (custom frontend)
    jwt_secret_key: str = "skin1004-ai-secret-change-me"
    sqlite_db_path: str = "C:/Users/DB_PC/.open-webui/data/skin1004_chat.db"

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
            "skin1004-319714.marketing_analysis.integrated_advertising_data",
            "skin1004-319714.marketing_analysis.Integrated_marketing_cost",
            "skin1004-319714.marketing_analysis.shopify_analysis_sales",
            "skin1004-319714.Platform_Data.raw_data",
            "skin1004-319714.marketing_analysis.influencer_input_ALL_TEAMS",
            "skin1004-319714.marketing_analysis.amazon_search_analytics_catalog_performance",
            # Reviews
            "skin1004-319714.Review_Data.Amazon_Review",
            "skin1004-319714.Review_Data.Qoo10_Review",
            "skin1004-319714.Review_Data.Shopee_Review",
            "skin1004-319714.Review_Data.Smartstore_Review",
            # Ad data
            "skin1004-319714.ad_data.meta data_test",
        ]


@lru_cache()
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
