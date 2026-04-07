"""
Batch embedding client using OpenAI.
"""

from openai import OpenAI

from config import get_logger, settings


logger = get_logger(__name__)


class BatchEmbedder:
    """Generate embeddings in batches."""

    def __init__(self, model: str = None, batch_size: int = None):
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = model or settings.embedding_model
        self._batch_size = batch_size or settings.embedding_batch_size

    def embed_single(self, text: str) -> list[float]:
        response = self._client.embeddings.create(model=self._model, input=text)
        return response.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        all_embeddings = []

        for index in range(0, len(texts), self._batch_size):
            batch = texts[index:index + self._batch_size]
            response = self._client.embeddings.create(model=self._model, input=batch)
            batch_embeddings = sorted(response.data, key=lambda item: item.index)
            all_embeddings.extend([item.embedding for item in batch_embeddings])

            if index + self._batch_size < len(texts):
                logger.info(
                    "Embedding batch complete | batch=%s embedded=%s total=%s",
                    index // self._batch_size + 1,
                    len(all_embeddings),
                    len(texts),
                )

        return all_embeddings

    def embed_with_titles(self, texts: list[str], titles: list[str]) -> list[list[float]]:
        combined = [f"{title}\n{text}" if title else text for title, text in zip(titles, texts)]
        return self.embed_batch(combined)
