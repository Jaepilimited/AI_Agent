"""Gemini-based embedder for knowledge_wiki rows.

Uses ``text-embedding-004`` (768-dim) via google-genai — same vendor that
produces the Notion vector store, so we stay inside one model family. The
vectors are small enough that we can load them all into a process-local
cache on demand instead of standing up a separate vector database.

Public surface:

- ``embed_text(text)``               → single-shot query embedding
- ``ensure_wiki_embeddings(limit)``  → batch-fill missing rows
- ``load_wiki_embeddings()``          → {row_id: vector} cache for search
- ``cosine(a, b)``                    → similarity helper
"""

from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from collections import OrderedDict
from typing import Any

import structlog

from app.config import get_settings
from app.db.mariadb import execute, fetch_all

logger = structlog.get_logger(__name__)


_EMBED_MODEL = "gemini-embedding-001"
_DIM = 768  # output_dimensionality=768 — small enough to cache in memory

_cache: dict[int, list[float]] | None = None
_cache_loaded_at: float = 0.0
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # seconds

# LRU cache for query embeddings — same question asked twice should hit
# memory, not Gemini.
_QUERY_CACHE_MAX = 1000
_query_cache: "OrderedDict[str, list[float]]" = OrderedDict()
_query_cache_lock = threading.Lock()

# Cap the Gemini embed call so a slow network never blocks the hot path.
_EMBED_TIMEOUT_SEC = 300.0

# Reuse the genai client — building one per call costs 100-300ms by itself.
_genai_client_singleton = None
_genai_client_lock = threading.Lock()


# ------------------------------------------------------------------
# Gemini client wrapper
# ------------------------------------------------------------------

def _get_genai_client():
    global _genai_client_singleton
    if _genai_client_singleton is not None:
        return _genai_client_singleton
    with _genai_client_lock:
        if _genai_client_singleton is None:
            from google import genai
            settings = get_settings()
            _genai_client_singleton = genai.Client(api_key=settings.gemini_api_key)
    return _genai_client_singleton


def _embed_config():
    from google.genai import types
    return types.EmbedContentConfig(output_dimensionality=_DIM)


def embed_text(text: str) -> list[float]:
    """Embed a single string. Raises on provider failure."""
    if not text or not text.strip():
        return [0.0] * _DIM
    client = _get_genai_client()
    try:
        resp = client.models.embed_content(
            model=_EMBED_MODEL,
            contents=text[:2000],
            config=_embed_config(),
        )
    except Exception as e:
        logger.warning("embed_text_failed", error=str(e)[:200])
        raise

    try:
        return list(resp.embeddings[0].values)
    except Exception:
        logger.warning("embed_text_unexpected_shape")
        return [0.0] * _DIM


_API_BATCH_LIMIT = 100  # Gemini hard cap on embedContent batch size


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed several strings. Splits internally to respect the provider's
    100-request-per-call batch cap.
    """
    if not texts:
        return []
    client = _get_genai_client()
    out: list[list[float]] = []
    for i in range(0, len(texts), _API_BATCH_LIMIT):
        chunk = [t[:2000] for t in texts[i : i + _API_BATCH_LIMIT]]
        try:
            resp = client.models.embed_content(
                model=_EMBED_MODEL,
                contents=chunk,
                config=_embed_config(),
            )
            out.extend(list(e.values) for e in resp.embeddings)
        except Exception as e:
            logger.warning(
                "embed_batch_chunk_fallback",
                chunk_index=i // _API_BATCH_LIMIT,
                error=str(e)[:200],
            )
            for t in chunk:
                try:
                    out.append(embed_text(t))
                except Exception:
                    out.append([0.0] * _DIM)
    return out


# ------------------------------------------------------------------
# Batch indexing — fill embeddings for rows that don't have one yet
# ------------------------------------------------------------------

def ensure_wiki_embeddings(limit: int = 200) -> dict[str, int]:
    """Embed wiki rows that still have NULL embedding.

    Returns counts. Idempotent — safe to run repeatedly. Designed for an
    offline script or a low-frequency cron, not the request path.
    """
    rows = fetch_all(
        """
        SELECT id, entity, summary
        FROM knowledge_wiki
        WHERE embedding IS NULL AND status <> 'archived'
        ORDER BY id
        LIMIT %s
        """,
        (limit,),
    )
    if not rows:
        return {"indexed": 0, "remaining": 0}

    texts = [f"{r['entity']} — {r['summary']}" for r in rows]
    vectors = embed_batch(texts)
    indexed = 0
    for row, vec in zip(rows, vectors):
        if len(vec) != _DIM:
            continue
        try:
            execute(
                "UPDATE knowledge_wiki SET embedding = %s WHERE id = %s",
                (json.dumps(vec), row["id"]),
            )
            indexed += 1
        except Exception as e:
            logger.warning("embed_write_failed", id=row["id"], error=str(e)[:200])

    remaining_rows = fetch_all(
        "SELECT COUNT(*) AS c FROM knowledge_wiki WHERE embedding IS NULL AND status <> 'archived'"
    )
    remaining = int(remaining_rows[0]["c"]) if remaining_rows else 0

    logger.info("wiki_embed_indexed", indexed=indexed, remaining=remaining)
    return {"indexed": indexed, "remaining": remaining}


# ------------------------------------------------------------------
# In-memory cache for search
# ------------------------------------------------------------------

def load_wiki_embeddings(force: bool = False) -> dict[int, list[float]]:
    """Load every active embedding into a process-local dict.

    Refreshes after ``_CACHE_TTL`` seconds or when ``force`` is True.
    """
    global _cache, _cache_loaded_at
    now = time.time()
    with _cache_lock:
        if not force and _cache is not None and (now - _cache_loaded_at) < _CACHE_TTL:
            return _cache

        rows = fetch_all(
            "SELECT id, embedding FROM knowledge_wiki "
            "WHERE embedding IS NOT NULL AND status <> 'archived'"
        )
        out: dict[int, list[float]] = {}
        for r in rows:
            emb = r.get("embedding")
            if isinstance(emb, str):
                try:
                    emb = json.loads(emb)
                except json.JSONDecodeError:
                    continue
            if isinstance(emb, list) and len(emb) == _DIM:
                out[int(r["id"])] = emb
        _cache = out
        _cache_loaded_at = now
        logger.info("wiki_embed_cache_loaded", count=len(out))
        return out


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom == 0:
        return 0.0
    return dot / denom


async def embed_query_async(text: str) -> list[float] | None:
    """Async wrapper for request-path embedding.

    Fast path:
    1. Check the LRU cache keyed by the trimmed query.
    2. Run Gemini in a worker thread with a hard timeout so a slow or
       unavailable API doesn't stall the orchestrator.
    """
    if not text or not text.strip():
        return None
    key = text.strip()[:512]

    with _query_cache_lock:
        cached = _query_cache.get(key)
        if cached is not None:
            _query_cache.move_to_end(key)
            return cached

    try:
        vec = await asyncio.wait_for(
            asyncio.to_thread(embed_text, key),
            timeout=_EMBED_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.info("embed_query_timeout", query_len=len(key))
        return None
    except Exception as e:
        logger.warning("embed_query_failed", error=str(e)[:200])
        return None

    if not vec:
        return None

    with _query_cache_lock:
        _query_cache[key] = vec
        _query_cache.move_to_end(key)
        while len(_query_cache) > _QUERY_CACHE_MAX:
            _query_cache.popitem(last=False)
    return vec
