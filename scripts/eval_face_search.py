"""Phase 1 — face-search 정확도 평가.

인덱스 메타에서 person/product/event 카테고리별 50장씩 ground truth를 자동 추출하고,
/face-search/query 엔드포인트에 thumb 이미지를 업로드해 답변과 비교한다.

Usage:
    venv/bin/python scripts/eval_face_search.py
    venv/bin/python scripts/eval_face_search.py --per-cat 30 --base http://localhost:3001
    venv/bin/python scripts/eval_face_search.py --tag baseline   # 결과 파일 이름에 태그
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "logs" / "face_eval"
REPORTS.mkdir(parents=True, exist_ok=True)
THUMB_CACHE = Path("/tmp/face_eval_cache")
THUMB_CACHE.mkdir(parents=True, exist_ok=True)

# sync 헬퍼 재사용
sys.path.insert(0, str(Path(__file__).resolve().parent))
from face_clip_sync import extract_person_label, nfc  # noqa: E402

META_CLIP = DATA / "face_clip_meta.json"

_DATE_PREFIX = re.compile(r"^\d{6}_")


# ─────────────────────────── ground-truth 추출 ───────────────────────────

def gt_for_item(item: dict) -> tuple[str, str] | None:
    """clip_meta 한 항목 → (type, expected_label) 또는 None."""
    folder = nfc(item.get("folder", ""))
    parts = [p for p in folder.split("/") if p]
    if not parts:
        return None

    # person: Model 다음 NN. 이름 (Together 같은 sub-folder 제외)
    person = extract_person_label(folder)
    has_model_anchor = any(re.match(r"^\d*\.?\s*Model$", p, re.IGNORECASE) for p in parts)
    if has_model_anchor and person and re.match(r"^\d+\.\s+\S", person) and item.get("has_face"):
        # Together 등의 sub-folder가 있으면 마지막이 인물명이 아닐 수 있음 — Model 다음 segment만 신뢰
        return ("person", person)

    # event: Flagship Store / 매장 / NNNNNN_이벤트
    if any("Flagship" in p for p in parts):
        for p in parts:
            if _DATE_PREFIX.match(p):
                return ("event", p)

    # product: Transparent Background 또는 Product anchor → 가장 깊은 제품명
    is_product = any(
        "Transparent Background" in p
        or re.match(r"^\d*\.?\s*Product$", p, re.IGNORECASE)
        for p in parts
    )
    if is_product:
        # 마지막 의미 있는 segment를 정답으로
        for p in reversed(parts):
            if p in {"Mini", "Together", "Etc", "etc", "etc.", "Texture", "Box"}:
                continue
            if "Transparent" in p or "Product" in p:
                continue
            return ("product", p)

    return None


def build_eval_set(per_cat: int, seed: int = 42) -> list[dict]:
    """clip_meta에서 카테고리별 per_cat 장씩 균등 샘플링."""
    import random
    meta = json.loads(META_CLIP.read_text())
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for it in meta:
        gt = gt_for_item(it)
        if gt is None:
            continue
        ltype, label = gt
        by_cat[ltype].append({
            "drive_id": it["drive_id"],
            "name": it.get("name", ""),
            "folder": nfc(it.get("folder", "")),
            "expected_type": ltype,
            "expected_label": label,
            "has_face": bool(it.get("has_face")),
        })
    rnd = random.Random(seed)
    out: list[dict] = []
    for cat in ("person", "product", "event"):
        bucket = by_cat.get(cat, [])
        rnd.shuffle(bucket)
        out.extend(bucket[:per_cat])
    return out


# ─────────────────────────── HTTP helpers ───────────────────────────

def fetch_thumb(base: str, drive_id: str, size: int = 1600, timeout: float = 60.0) -> bytes:
    """Drive thumb를 디스크 캐시. 같은 이미지 두 번 다운로드 안 함 (재평가용)."""
    cache_file = THUMB_CACHE / f"{drive_id}_{size}.jpg"
    if cache_file.exists():
        return cache_file.read_bytes()
    url = f"{base}/face-search/thumb/{drive_id}?size={size}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read()
    cache_file.write_bytes(data)
    return data


def post_query(base: str, img_bytes: bytes, name: str, top_k: int = 5, timeout: float = 120.0) -> dict:
    boundary = uuid.uuid4().hex
    crlf = b"\r\n"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"top_k\"\r\n\r\n{top_k}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode("utf-8")
    body += img_bytes + crlf.join([b"", f"--{boundary}--".encode(), b""])
    req = urllib.request.Request(
        f"{base}/face-search/query",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ─────────────────────────── 라벨 매칭 ───────────────────────────

def _norm(s: str) -> str:
    """비교용 정규화 — NFC + 소문자 + 'NN.' prefix 제거 + 공백 압축."""
    s = nfc(s or "").lower().strip()
    s = re.sub(r"^\d+\.\s*", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def label_match(expected: str, got: str) -> str:
    """exact / partial / mismatch 중 하나 반환."""
    e, g = _norm(expected), _norm(got)
    if not g:
        return "mismatch"
    if e == g:
        return "exact"
    # 양방향 포함 (예: 정답 '강아인', 답변 '강아인 (Quick Calming Duo)')
    if e and (e in g or g in e):
        return "partial"
    return "mismatch"


# ─────────────────────────── 메인 평가 루프 ───────────────────────────

def prefetch_thumbs(base: str, samples: list[dict], workers: int = 6, size: int = 1600) -> None:
    """모든 thumb를 병렬로 미리 다운로드해 디스크 캐시. /thumb은 thread-local Drive client라 안전."""
    todo = [s for s in samples if not (THUMB_CACHE / f"{s['drive_id']}_{size}.jpg").exists()]
    if not todo:
        print(f"[prefetch] all {len(samples)} thumbs already cached", flush=True)
        return
    print(f"[prefetch] downloading {len(todo)}/{len(samples)} thumbs with {workers} workers...", flush=True)
    t0 = time.perf_counter()
    done_count = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_thumb, base, s["drive_id"], size): s for s in todo}
        for fut in as_completed(futs):
            s = futs[fut]
            done_count += 1
            try:
                fut.result()
            except Exception as e:
                print(f"  ⚠️ {s['drive_id']}: {type(e).__name__}: {str(e)[:100]}", flush=True)
            if done_count % 10 == 0 or done_count == len(todo):
                dt = time.perf_counter() - t0
                rate = done_count / max(dt, 0.001)
                eta = (len(todo) - done_count) / max(rate, 0.01)
                print(f"  [prefetch {done_count}/{len(todo)}] {rate:.1f}/s · ETA {eta:.0f}s", flush=True)


def run_eval(base: str, per_cat: int, tag: str) -> dict:
    samples = build_eval_set(per_cat)
    print(f"[eval] samples: {len(samples)} (per_cat={per_cat})", flush=True)
    by_cat = Counter(s["expected_type"] for s in samples)
    print(f"[eval] breakdown: {dict(by_cat)}", flush=True)

    prefetch_thumbs(base, samples)

    results: list[dict] = []
    t_start = time.perf_counter()
    for i, s in enumerate(samples, 1):
        t0 = time.perf_counter()
        rec: dict = {**s, "ok": False}
        try:
            img = fetch_thumb(base, s["drive_id"], size=1600)
            resp = post_query(base, img, s["name"])
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

        # progress 한 줄
        mark = "✓" if rec.get("label_match") == "exact" else ("~" if rec.get("label_match") == "partial" else "✗")
        elapsed = (time.perf_counter() - t_start) / 60
        eta = elapsed / i * (len(samples) - i)
        print(f"  [{i:>3}/{len(samples)}] {mark} {s['expected_type']:>7} | exp='{s['expected_label'][:30]:30s}' | got='{(rec.get('got_label') or '-')[:30]:30s}' | conf={rec.get('confidence') or '-':>5} | {dt:>6.0f}ms | ETA {eta:.1f}m", flush=True)

    summary = summarize(results, by_cat)
    out = {
        "tag": tag,
        "base": base,
        "per_cat": per_cat,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_start)),
        "elapsed_min": round((time.perf_counter() - t_start) / 60, 2),
        "summary": summary,
        "results": results,
    }
    return out


def summarize(results: list[dict], cat_counts: Counter) -> dict:
    by_cat = defaultdict(lambda: {"n": 0, "exact": 0, "partial": 0, "type_match": 0, "ok": 0, "conf_sum": 0.0, "elapsed_sum": 0.0})
    bins = [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.001)]
    cal = {f"{lo:.1f}-{hi:.1f}": {"n": 0, "exact": 0} for lo, hi in bins}
    failures: list[dict] = []

    for r in results:
        cat = r["expected_type"]
        b = by_cat[cat]
        b["n"] += 1
        if r.get("ok"):
            b["ok"] += 1
            if r.get("type_match"):
                b["type_match"] += 1
            lm = r.get("label_match")
            if lm == "exact":
                b["exact"] += 1
            elif lm == "partial":
                b["partial"] += 1
            if r.get("confidence") is not None:
                b["conf_sum"] += r["confidence"]
            if r.get("elapsed_ms") is not None:
                b["elapsed_sum"] += r["elapsed_ms"]

            conf = r.get("confidence")
            if conf is not None:
                for lo, hi in bins:
                    if lo <= conf < hi:
                        key = f"{lo:.1f}-{hi:.1f}"
                        cal[key]["n"] += 1
                        if lm == "exact":
                            cal[key]["exact"] += 1
                        break

            if lm != "exact":
                failures.append({
                    "drive_id": r["drive_id"], "type": cat,
                    "expected": r["expected_label"], "got": r.get("got_label"),
                    "label_match": lm, "confidence": r.get("confidence"),
                    "source": r.get("source"),
                    "top1_face_label": r.get("top1_face_label"),
                    "top1_face_score": r.get("top1_face_score"),
                    "top1_clip_folder": r.get("top1_clip_folder")[-80:] if r.get("top1_clip_folder") else "",
                    "top1_clip_score": r.get("top1_clip_score"),
                    "name": r.get("name"),
                })
        else:
            failures.append({"drive_id": r["drive_id"], "type": cat, "error": r.get("error")})

    summary = {"per_cat": {}, "overall": {}, "calibration": {}, "failures_n": len(failures), "failures_sample": failures[:30]}
    total = {"n": 0, "exact": 0, "partial": 0, "type_match": 0, "ok": 0, "conf_sum": 0.0, "elapsed_sum": 0.0}
    for cat, b in by_cat.items():
        n = b["n"]
        ok = b["ok"]
        summary["per_cat"][cat] = {
            "n": n,
            "ok": ok,
            "type_acc": round(b["type_match"] / n, 3) if n else 0,
            "label_exact_acc": round(b["exact"] / n, 3) if n else 0,
            "label_partial_or_exact_acc": round((b["exact"] + b["partial"]) / n, 3) if n else 0,
            "avg_confidence": round(b["conf_sum"] / ok, 3) if ok else 0,
            "avg_elapsed_ms": round(b["elapsed_sum"] / ok, 0) if ok else 0,
        }
        for k, v in b.items():
            total[k] += v
    n, ok = total["n"], total["ok"]
    summary["overall"] = {
        "n": n,
        "ok": ok,
        "type_acc": round(total["type_match"] / n, 3) if n else 0,
        "label_exact_acc": round(total["exact"] / n, 3) if n else 0,
        "label_partial_or_exact_acc": round((total["exact"] + total["partial"]) / n, 3) if n else 0,
        "avg_confidence": round(total["conf_sum"] / ok, 3) if ok else 0,
        "avg_elapsed_ms": round(total["elapsed_sum"] / ok, 0) if ok else 0,
    }
    summary["calibration"] = {
        k: {"n": v["n"], "exact_acc": round(v["exact"] / v["n"], 3) if v["n"] else 0.0}
        for k, v in cal.items()
    }
    return summary


def print_summary(s: dict) -> None:
    sm = s["summary"]
    print(f"\n=== eval[{s['tag']}] @ {s['base']} · {s['elapsed_min']}min · n={sm['overall']['n']} ===")
    print(f"{'category':<10} {'n':>4} {'type%':>7} {'label%':>8} {'~label%':>9} {'conf':>6} {'ms':>6}")
    for cat, b in sm["per_cat"].items():
        print(f"{cat:<10} {b['n']:>4} {b['type_acc']*100:>6.1f}% {b['label_exact_acc']*100:>7.1f}% {b['label_partial_or_exact_acc']*100:>8.1f}% {b['avg_confidence']:>6.2f} {b['avg_elapsed_ms']:>6.0f}")
    o = sm["overall"]
    print(f"{'OVERALL':<10} {o['n']:>4} {o['type_acc']*100:>6.1f}% {o['label_exact_acc']*100:>7.1f}% {o['label_partial_or_exact_acc']*100:>8.1f}% {o['avg_confidence']:>6.2f} {o['avg_elapsed_ms']:>6.0f}")
    print("\nconfidence calibration (실제 exact 정답률):")
    for k, v in sm["calibration"].items():
        print(f"  conf {k}: n={v['n']:>3} → exact_acc={v['exact_acc']*100:>5.1f}%")
    print(f"\n실패 {sm['failures_n']}건 (샘플 30건 dump 됨)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:3001")
    ap.add_argument("--per-cat", type=int, default=50)
    ap.add_argument("--tag", default=time.strftime("%Y%m%d_%H%M%S"))
    args = ap.parse_args()

    out = run_eval(args.base, args.per_cat, args.tag)
    print_summary(out)

    out_path = REPORTS / f"eval_{args.tag}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n📄 saved → {out_path}")


if __name__ == "__main__":
    main()
