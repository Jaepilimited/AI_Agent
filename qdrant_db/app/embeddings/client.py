"""
OpenAI 배치 임베딩
"""

from openai import OpenAI

from app.core.config import settings


class EmbeddingClient:
    def __init__(self):
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = settings.embedding_model
        self._batch_size = settings.embedding_batch_size

    def embed_single(self, text: str) -> list[float]:
        response = self._client.embeddings.create(model=self._model, input=text)
        return response.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            response = self._client.embeddings.create(model=self._model, input=batch)
            sorted_data = sorted(response.data, key=lambda x: x.index)
            all_embeddings.extend(e.embedding for e in sorted_data)

        return all_embeddings
