"""
Application settings.
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Environment-backed settings."""

    notion_token: str = Field(alias="NOTION_TOKEN")
    root_page_id: str = Field(
        default="d86180c9236541d6b154dcb4c4143f23",
        alias="ROOT_PAGE_ID",
    )

    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    llm_model: str = "gpt-5-mini"

    qdrant_host: str = Field(default="localhost", alias="QDRANT_HOST")
    qdrant_port: int = Field(default=6333, alias="QDRANT_PORT")
    collection_name: str = Field(default="notion_skin1004", alias="QDRANT_COLLECTION")

    chunk_size: int = 800
    chunk_overlap: int = 100
    embedding_batch_size: int = 100

    search_top_k: int = 5
    score_threshold: float = 0.5

    multi_query_enabled: bool = True
    multi_query_count: int = 4
    multi_query_threshold: float = 0.3

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_file: str = Field(default="db.log", alias="LOG_FILE")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
