"""수동 품질 스냅샷 실행.

사용법:
  python scripts/compute_quality_snapshot.py           # 어제 기준
  python scripts/compute_quality_snapshot.py 2026-06-22  # 특정 날짜
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
from app.core.quality_monitor import compute_daily_snapshot

target = None
if len(sys.argv) > 1:
    target = date.fromisoformat(sys.argv[1])

result = compute_daily_snapshot(target)
print(f"\n=== Quality Snapshot: {result['date']} ===")
for route, m in result["routes"].items():
    flags = [k for k in ("flag_accuracy", "flag_speed", "flag_context") if m[k]]
    status = "⚠️ " + ", ".join(flags) if flags else "✅"
    print(
        f"  {route:12s}  {status}"
        f"  accuracy={m['accuracy_rate']:.2f}" if m["accuracy_rate"] is not None else f"  {route:12s}  {status}  accuracy=N/A",
        f"  ms={m['avg_response_ms']:,}  ctx={m['avg_context_len']:,}  req={m['request_count']}",
    )

if result["flags"]:
    print(f"\n❗ {len(result['flags'])}개 라우트 임계치 초과 — pm2 logs 또는 knowledge_wiki 검토 권장")
else:
    print("\n✅ 모든 라우트 정상 범위")
