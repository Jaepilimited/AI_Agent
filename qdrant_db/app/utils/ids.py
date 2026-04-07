"""
Qdrant point_id 생성

규칙: uuid5(DNS namespace, "{hub_id}:{page_id}:{chunk_index}:{content_sha256}")
→ 같은 page 재색인 시 동일 content면 동일 ID, 다르면 다른 ID → 자연스러운 upsert
"""

import uuid


_NAMESPACE = uuid.NAMESPACE_DNS


def make_point_id(hub_id: str, page_id: str, chunk_index: int, content_sha256: str) -> str:
    key = f"{hub_id}:{page_id}:{chunk_index}:{content_sha256}"
    return str(uuid.uuid5(_NAMESPACE, key))
