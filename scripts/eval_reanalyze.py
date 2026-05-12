"""기존 eval JSON의 답변(got_label)은 그대로 두고, ground truth만 새 규칙으로 재계산해 비교 일관성 확보.

평가 셋의 GT 추출이 바뀐 경우, 옛 결과 JSON을 그대로 다시 계산하면
같은 답변에 대해 새 GT 기준 정확도를 측정할 수 있다 (dev 재호출 불필요).

Usage:
    venv/bin/python scripts/eval_reanalyze.py logs/face_eval/eval_phase2.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_face_search import gt_for_item, label_match  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
META_CLIP = DATA / "face_clip_meta.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eval_path", help="logs/face_eval/eval_*.json")
    args = ap.parse_args()

    src = json.loads(Path(args.eval_path).read_text())
    clip_meta = json.loads(META_CLIP.read_text())
    by_id = {m["drive_id"]: m for m in clip_meta}

    # 새 GT로 expected_* 재계산 + label_match 재평가
    new_results = []
    skipped = 0
    for r in src["results"]:
        item = by_id.get(r["drive_id"])
        if not item:
            continue
        new_gt = gt_for_item(item)
        if new_gt is None:
            skipped += 1
            continue
        new_type, new_label = new_gt
        nr = {**r}
        nr["expected_type"] = new_type
        nr["expected_label"] = new_label
        if r.get("ok") and r.get("got_label") is not None:
            nr["type_match"] = (r.get("got_type") == new_type)
            nr["label_match"] = label_match(new_label, r.get("got_label") or "")
        new_results.append(nr)

    # summarize 호출
    from eval_face_search import summarize
    cat_counts = Counter(r["expected_type"] for r in new_results)
    new_summary = summarize(new_results, cat_counts)

    print(f"=== Re-analyzed: {args.eval_path} ===")
    print(f"original n={len(src['results'])}, new GT n={len(new_results)} (skipped {skipped} noise items)\n")
    print(f"{'category':<10} {'n':>4} {'type%':>7} {'label%':>8} {'~label%':>9} {'conf':>6}")
    for cat, b in new_summary["per_cat"].items():
        print(f"{cat:<10} {b['n']:>4} {b['type_acc']*100:>6.1f}% {b['label_exact_acc']*100:>7.1f}% {b['label_partial_or_exact_acc']*100:>8.1f}% {b['avg_confidence']:>6.2f}")
    o = new_summary["overall"]
    print(f"{'OVERALL':<10} {o['n']:>4} {o['type_acc']*100:>6.1f}% {o['label_exact_acc']*100:>7.1f}% {o['label_partial_or_exact_acc']*100:>8.1f}% {o['avg_confidence']:>6.2f}")


if __name__ == "__main__":
    main()
