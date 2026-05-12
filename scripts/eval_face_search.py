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

_NUM_PREFIX_RE = re.compile(r"^\d+\.\s*")
_PRODUCT_NOISE_SEGS = {
    "Mini", "Together", "Etc", "Etc.", "etc", "etc.", "ETC", "ETC.",
    "Texture", "Box",
    "old", "Old", "OLD", "사용X", "단종", "구버전", "기본", "저해상", "고해상",
    "정방형", "가로형", "세로형", "JPG", "PSD",
    "Real Product : Fake Product", "Image", "기타",
    "리뉴얼 전 누끼", "리뉴얼 전", "누끼", "단체 구성 이미지",
}
# 순수 숫자 + 단위 (사이즈)
_SIZE_NUM_RE = re.compile(r"^\d+\s*(ml|g|kg|kit|개|매|set|p|pcs)?$", re.IGNORECASE)
_PRODUCT_LINE_SEGS = {
    "Centella", "Hyalu-Cica", "Hyalu-Teca", "Centella Teca",
    "Tone Brightening", "Tea-Trica", "Probio-Cica", "Poremizing", "Zombie Beauty",
    "Niacinamide", "Matrixyl", "Retinol", "Azelaic acid",
    "JBT",
}


def _strip_num_prefix(s: str) -> str:
    """'08. Soothing Cream' → 'Soothing Cream'."""
    return _NUM_PREFIX_RE.sub("", s).strip()


def _is_product_noise(s: str) -> bool:
    """잡음 segment(라벨로 부적합) 판정 — 잡음 키워드, 사이즈 숫자.
    LINE_SEGS는 잡음이 아님(폴더 끝이 line 단독인 케이스도 그 line 자체가 product 라벨).
    """
    s_clean = _strip_num_prefix(s)
    if not s_clean or s_clean in _PRODUCT_NOISE_SEGS:
        return True
    if "Transparent" in s_clean or s_clean.endswith(" Product"):
        return True
    if _SIZE_NUM_RE.match(s_clean):
        return True
    if "리뉴얼" in s_clean or "사용X" in s_clean or "단종" in s_clean:
        return True
    return False


def gt_for_item(item: dict) -> tuple[str, str] | None:
    """clip_meta 한 항목 → (type, expected_label) 또는 None.

    Product 라벨은 'NN.' prefix 제거 + 잡음 sub-folder 제외 + line 보강.
    """
    folder = nfc(item.get("folder", ""))
    parts = [p for p in folder.split("/") if p]
    if not parts:
        return None

    person = extract_person_label(folder)
    has_model_anchor = any(re.match(r"^\d*\.?\s*Model$", p, re.IGNORECASE) for p in parts)
    if has_model_anchor and person and re.match(r"^\d+\.\s+\S", person) and item.get("has_face"):
        return ("person", _strip_num_prefix(person))

    if any("Flagship" in p for p in parts):
        for p in parts:
            if _DATE_PREFIX.match(p):
                return ("event", p)

    is_product = any(
        "Transparent Background" in p
        or re.match(r"^\d*\.?\s*Product$", p, re.IGNORECASE)
        for p in parts
    )
    if is_product:
        # 잡음 폴더면 평가 셋에서 아예 제외 (라벨로 부적합)
        if parts and _strip_num_prefix(parts[-1]) in _PRODUCT_NOISE_SEGS:
            return None
        # 가장 깊은 의미 있는 segment + line 보강
        product_name = None
        for p in reversed(parts):
            if _is_product_noise(p):
                continue
            product_name = _strip_num_prefix(p)
            break
        if not product_name:
            return None
        line = next((seg for seg in parts if _strip_num_prefix(seg) in _PRODUCT_LINE_SEGS), None)
        if line:
            line_clean = _strip_num_prefix(line)
            if line_clean not in product_name:
                # word-merge로 "Centella Teca Teca Soothing Toner" 같은 중복 방지
                lw, pw = line_clean.split(), product_name.split()
                overlap = 0
                for k in range(min(len(lw), len(pw)), 0, -1):
                    if lw[-k:] == pw[:k]:
                        overlap = k
                        break
                product_name = " ".join(lw + pw[overlap:])
        return ("product", product_name)

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
