# -*- coding: utf-8 -*-
"""질의 모델과 **다른 공간에 있는 벡터**를 Gemini 로 다시 임베딩한다.

⛔ 2026-08-25 실측으로 드러난 가장 큰 결함이다. 컬렉션 `Craver` 에 **서로 다른 모델로
   만든 벡터가 섞여** 있었다:

     저장 벡터 ↔ 같은 텍스트의 Gemini 임베딩 코사인
       `Database (FAQ, Terms)` (source=PEOPLE-hub)  → 1.000   ← Gemini
       `광고소재 미팅`          (source=notion)      → 0.013   ← 다른 모델
       `복리후생`              (source=notion)      → 0.020   ← 다른 모델

   앱은 Gemini(`gemini-embedding-001`, 1536)로 질의한다. 다른 모델로 만든 벡터는
   **같은 공간에 있지 않으므로 유사도가 사실상 무작위**다 — 검색에 영영 안 걸린다.
   표본 24개 중 15개(62%)가 그 상태였고, `source` 로 정확히 갈렸다:

       `*-hub` · `team_resources`  → Gemini      (scripts/notion_qdrant_pipeline.py)
       `notion` · `google_sheets`  → 다른 모델   (qdrant_db/, OpenAI text-embedding-3-small)

   그래서 **무엇을 물어도 Gemini 로 넣은 몇 문서만 나왔다.** 야근 식대 질문에 표현을
   네 가지로 바꿔도 전부 같은 FAQ 가 나온 이유다 (붐따 #105) — 순위 문제가 아니라
   **경쟁자가 없었던 것**이다.

⚠️ 에러가 나지 않는 고장이다. 검색은 200 을 주고 답도 나온다. 근거만 무작위다.

사용:
    python scripts/reembed_legacy_vectors.py              # 진단만 (표본 코사인)
    python scripts/reembed_legacy_vectors.py --apply      # 실제 재임베딩
    python scripts/reembed_legacy_vectors.py --apply --source notion

⚠️ payload 는 그대로 두고 **벡터만** 바꾼다 (같은 point id 로 upsert).
   중간에 끊겨도 다시 돌리면 이어서 된다 — 이미 Gemini 인 것은 건너뛴다.
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

COLLECTION = "Craver"
# 앱(`app/agents/qdrant_agent.py`)이 쓰는 것과 같은 기본값 — env 가 있으면 그쪽이 이긴다
_DEFAULT_QDRANT_URL = ("https://bf41bcbe-af68-416f-9d26-1b3d64f7bed0"
                       ".us-east-1-1.aws.cloud.qdrant.io:6333")
_DEFAULT_QDRANT_KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3Vi"
                       "amVjdCI6ImFwaS1rZXk6OTFkOGVkZWYtNTFkNi00ODNhLTg0MDItZTdjNjI0"
                       "ZjA2NThmIn0.K0zdMdpnbIMl_yfXV8EJfcClpPnkoPa_SS_XbDI1kv4")
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 1536
BATCH = 16
# 이 source 들이 다른 모델로 만들어졌다 (2026-08-25 실측)
LEGACY_SOURCES = {"notion", "google_sheets"}
SAME_SPACE = 0.85          # 코사인이 이 이상이면 이미 Gemini 로 본다


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _clients():
    from dotenv import load_dotenv
    load_dotenv(PROJ / ".env")
    from google import genai
    from qdrant_client import QdrantClient

    # ⚠️ `app.agents.qdrant_agent` 를 import 하지 않는다 — structlog 등 앱 의존성이
    #    딸려 와서, 격리 venv 로 이 스크립트만 돌릴 때 막힌다. 값은 같은 곳(env)에서 읽는다.
    url = os.getenv("QDRANT_URL") or _DEFAULT_QDRANT_URL
    key = os.getenv("QDRANT_API_KEY") or _DEFAULT_QDRANT_KEY

    # ⚠️ 공용 클라이언트(timeout=15)로는 scroll·upsert 가 자주 끊긴다
    q = QdrantClient(url=url, api_key=key, timeout=180)
    g = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return q, g


def _embed(g, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        r = g.models.embed_content(
            model=EMBEDDING_MODEL, contents=texts[i:i + BATCH],
            config={"output_dimensionality": EMBEDDING_DIM})
        out.extend(e.values for e in r.embeddings)
    return out


def _scan(q, source_filter: str | None):
    """대상 포인트를 모은다 (id, payload, vector)."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    flt = None
    if source_filter:
        flt = Filter(must=[FieldCondition(key="source",
                                          match=MatchValue(value=source_filter))])
    out, nxt = [], None
    while True:
        pts, nxt = q.scroll(collection_name=COLLECTION, limit=256, offset=nxt,
                            scroll_filter=flt, with_payload=True, with_vectors=True)
        if not pts:
            break
        for p in pts:
            pl = p.payload or {}
            if source_filter is None and str(pl.get("source")) not in LEGACY_SOURCES:
                continue
            if str(pl.get("text", "")).strip():
                out.append(p)
        if nxt is None:
            break
    return out


def main() -> int:
    apply = "--apply" in sys.argv
    src = None
    if "--source" in sys.argv:
        src = sys.argv[sys.argv.index("--source") + 1]

    q, g = _clients()
    print(f"===== 벡터 공간 복구 ({'적용' if apply else '진단만'}) =====\n")
    pts = _scan(q, src)
    print(f"대상 청크: {len(pts)}개"
          + (f" (source={src})" if src else f" (source in {sorted(LEGACY_SOURCES)})"))
    if not pts:
        print("대상 없음."); return 0

    # 표본으로 정말 다른 공간인지 먼저 확인한다 — 멀쩡한 것을 다시 굽지 않기 위해
    probe = pts[: min(3, len(pts))]
    probe_vecs = _embed(g, [str(p.payload.get("text", ""))[:1500] for p in probe])
    sims = [_cos(p.vector, v) for p, v in zip(probe, probe_vecs)]
    print(f"표본 코사인: {[round(s, 3) for s in sims]}")
    if all(s > SAME_SPACE for s in sims):
        print("이미 Gemini 공간이다 — 할 일 없음."); return 0
    if not apply:
        print("\n--apply 를 붙이면 재임베딩한다. payload 는 그대로, 벡터만 바꾼다.")
        return 0

    from qdrant_client.models import PointStruct

    t0, done, skipped = time.time(), 0, 0
    for i in range(0, len(pts), BATCH):
        group = pts[i:i + BATCH]
        vecs = _embed(g, [str(p.payload.get("text", ""))[:8000] for p in group])
        upserts = []
        for p, v in zip(group, vecs):
            if _cos(p.vector, v) > SAME_SPACE:     # 이미 Gemini — 건너뛴다 (재실행 안전)
                skipped += 1
                continue
            upserts.append(PointStruct(id=p.id, vector=v, payload=p.payload))
        if upserts:
            q.upsert(collection_name=COLLECTION, points=upserts)
            done += len(upserts)
        print(f"  {i + len(group):>5}/{len(pts)}  재임베딩 {done} · 건너뜀 {skipped} "
              f"({time.time() - t0:.0f}s)")

    print(f"\n완료: 재임베딩 {done} · 이미 정상 {skipped} · {time.time() - t0:.0f}초")
    print("검증: 같은 명령을 --apply 없이 다시 돌려 표본 코사인이 1.0 인지 볼 것")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
