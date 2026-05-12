"""Holdout 검증 — 인덱스 사진을 자동 변형(crop/brightness/rotate)해서 query.

자기 자신 매칭 cos≈1.0이 깨지고, 같은 폴더의 다른 컷이 top-K로 와야 정답.
외부 사진 없이 일반화 정확도를 측정한다.

Usage:
    venv/bin/python scripts/eval_holdout.py --per-cat 20 --tag holdout
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_face_search import (  # noqa: E402
    build_eval_set, fetch_thumb, post_query, label_match,
    summarize, print_summary, REPORTS, THUMB_CACHE,
)


def augment(img_bytes: bytes, seed: int, crop_min: float = 0.65, crop_max: float = 0.85,
            rotate_max: float = 8.0) -> bytes:
    """무작위 crop + brightness shift + 약한 회전. 인덱스의 자기 자신 매칭 cos≈1을 깨뜨림."""
    from PIL import Image, ImageEnhance
    rnd = random.Random(seed)
    img = Image.open(io.BytesIO(img_bytes))
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    # 1) 무작위 crop
    cw = int(w * rnd.uniform(crop_min, crop_max))
    ch = int(h * rnd.uniform(crop_min, crop_max))
    x = rnd.randint(0, max(0, w - cw))
    y = rnd.randint(0, max(0, h - ch))
    img = img.crop((x, y, x + cw, y + ch))

    # 2) 밝기 ±25%
    img = ImageEnhance.Brightness(img).enhance(rnd.uniform(0.75, 1.25))

    # 3) 채도 ±15%
    img = ImageEnhance.Color(img).enhance(rnd.uniform(0.85, 1.15))

    # 4) 약한 회전 (얼굴 인식이 너무 안되지 않도록 작게)
    angle = rnd.uniform(-rotate_max, rotate_max)
    img = img.rotate(angle, expand=False, fillcolor=(255, 255, 255))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def run_holdout(base: str, per_cat: int, tag: str) -> dict:
    samples = build_eval_set(per_cat)
    print(f"[holdout] samples: {len(samples)} (per_cat={per_cat})", flush=True)
    by_cat = Counter(s["expected_type"] for s in samples)
    print(f"[holdout] breakdown: {dict(by_cat)}", flush=True)

    # 변형 이미지 캐시 별도 (원본 캐시는 그대로 활용)
    aug_dir = Path("/tmp/face_eval_aug")
    aug_dir.mkdir(exist_ok=True)

    results: list[dict] = []
    t_start = time.perf_counter()
    for i, s in enumerate(samples, 1):
        t0 = time.perf_counter()
        rec: dict = {**s, "ok": False}
        try:
            orig = fetch_thumb(base, s["drive_id"], size=1600)
            # 동일 seed로 결정적 변형
            seed = hash(s["drive_id"]) & 0x7FFFFFFF
            aug = augment(orig, seed)
            aug_file = aug_dir / f"{s['drive_id']}.jpg"
            aug_file.write_bytes(aug)

            resp = post_query(base, aug, s["name"])
            a = resp.get("answer") or {}
            rec.update({
                "ok": True,
                "got_type": a.get("type"),
                "got_label": a.get("label"),
                "confidence": a.get("confidence"),
                "source": a.get("source"),
                "elapsed_ms": resp.get("elapsed_ms"),
                "type_match": (a.get("type") == s["expected_type"]),
                "label_match": label_match(s["expected_label"], a.get("label") or ""),
                "top1_clip_folder": (resp.get("clip_results") or [{}])[0].get("folder", ""),
                "top1_clip_score": (resp.get("clip_results") or [{}])[0].get("score"),
                "top1_face_label": (resp.get("face_results") or [{}])[0].get("person_label") if resp.get("face_results") else None,
                "top1_face_score": (resp.get("face_results") or [{}])[0].get("score") if resp.get("face_results") else None,
            })
        except Exception as e:
            rec.update({"error": f"{type(e).__name__}: {str(e)[:200]}"})
        dt = (time.perf_counter() - t0) * 1000
        rec["wall_ms"] = round(dt, 1)
        results.append(rec)

        mark = "✓" if rec.get("label_match") == "exact" else ("~" if rec.get("label_match") == "partial" else "✗")
        eta = (time.perf_counter() - t_start) / i * (len(samples) - i) / 60
        top1s = rec.get("top1_clip_score") or 0
        print(f"  [{i:>3}/{len(samples)}] {mark} {s['expected_type']:>7} | exp='{s['expected_label'][:25]:25s}' | got='{(rec.get('got_label') or '-')[:25]:25s}' | top1={top1s:.2f} | {dt:>5.0f}ms | ETA {eta:.1f}m", flush=True)

    summary = summarize(results, by_cat)
    out = {
        "tag": tag,
        "base": base,
        "per_cat": per_cat,
        "augmented": True,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_start)),
        "elapsed_min": round((time.perf_counter() - t_start) / 60, 2),
        "summary": summary,
        "results": results,
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:3001")
    ap.add_argument("--per-cat", type=int, default=20)
    ap.add_argument("--tag", default="holdout_" + time.strftime("%Y%m%d_%H%M%S"))
    args = ap.parse_args()

    out = run_holdout(args.base, args.per_cat, args.tag)
    print_summary(out)
    out_path = REPORTS / f"eval_{args.tag}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n📄 saved → {out_path}")


if __name__ == "__main__":
    main()
