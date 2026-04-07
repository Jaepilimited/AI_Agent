"""
설정값 중앙 관리
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Notion
    notion_token: str = Field(alias="NOTION_TOKEN")
    notion_hub_page_id: str = Field(alias="NOTION_HUB_PAGE_ID")
    notion_hub_id: str = Field(default="hub_main", alias="NOTION_HUB_ID")

    # OpenAI
    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=1536, alias="EMBEDDING_DIM")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")

    # Qdrant
    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")
    qdrant_collection: str = Field(default="notion_chunks", alias="QDRANT_COLLECTION")

    # Chunking (토큰 기준)
    chunk_target_tokens: int = Field(default=600, alias="CHUNK_TARGET_TOKENS")
    chunk_overlap_tokens: int = Field(default=80, alias="CHUNK_OVERLAP_TOKENS")

    # Embedding
    embedding_batch_size: int = Field(default=100, alias="EMBEDDING_BATCH_SIZE")

    # Search
    search_top_k: int = Field(default=8, alias="SEARCH_TOP_K")
    score_threshold: float = Field(default=0.4, alias="SCORE_THRESHOLD")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }


settings = Settings()
