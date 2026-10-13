import sys, asyncio
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env")
from app.agents.qdrant_agent import _embed_query, _search

async def test():
    vec = await _embed_query("B2B2 해외 매출 현황")
    results = _search(vec, top_k=3)
    print(f"검색 결과 {len(results)}건:")
    for r in results:
        p = r["payload"]
        score = r["score"]
        print(f"  [{score:.3f}] {p.get('team')} > {p.get('page_title')} | {p.get('text','')[:60]}")

asyncio.run(test())
