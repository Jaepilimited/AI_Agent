import json, httpx, re
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
txt = (ROOT / "scripts" / "notion_qdrant_pipeline.py").read_text(encoding="utf-8")
m = re.search(r'QDRANT_API_KEY\s*=\s*"(.+?)"', txt)
KEY = m.group(1) if m else ""
URL = "https://bf41bcbe-af68-416f-9d26-1b3d64f7bed0.us-east-1-1.aws.cloud.qdrant.io:6333"
h = {"api-key": KEY, "Content-Type": "application/json"}

# 전체 포인트 수집
all_points = []
offset = None
while True:
    body = {"limit": 100, "with_payload": True, "with_vector": False}
    if offset:
        body["offset"] = offset
    r = httpx.post(f"{URL}/collections/notion_hub_gemini/points/scroll", headers=h, json=body, timeout=15)
    data = r.json().get("result", {})
    all_points.extend(data.get("points", []))
    offset = data.get("next_page_offset")
    if not offset:
        break

# DB팀 121개 페이지 타이틀 목록
db_points = [p for p in all_points if p.get("payload", {}).get("team") == "DB"]
print(f"DB팀 포인트: {len(db_points)}개\n")

titles = Counter(p["payload"].get("page_title", "?") for p in db_points)
for title, cnt in sorted(titles.items(), key=lambda x: -x[1]):
    # 첫 번째 포인트에서 breadcrumb도 확인
    sample = next(p for p in db_points if p["payload"].get("page_title") == title)
    breadcrumb = sample["payload"].get("breadcrumb", "")
    print(f"  [{cnt}chunk] {title}  |  breadcrumb: {breadcrumb}")
