"""Drive 폴더의 이미지를 CLIP + InsightFace로 임베딩하여 로컬 인덱스 구축.

Usage:
    venv/bin/python scripts/face_clip_sync.py --folder-id <FID> --sample 100
    venv/bin/python scripts/face_clip_sync.py --folder-id <FID> --full
    venv/bin/python scripts/face_clip_sync.py --folder-id <FID> --resume

Output:
    data/face_clip_index.npy       (N × 512, float32, L2-normalized — CLIP image embeddings)
    data/face_clip_meta.json       parallel metadata: drive_id/path/label/has_face/face_bbox
    data/face_index.npy            (M × 512, float32, normalized — InsightFace embeddings, M ≤ N)
    data/face_meta.json            parallel: drive_id, person_label (from folder)
    data/face_drive_manifest.json  drive_id → modifiedTime (sync 재개/증분용)
"""
from __future__ import annotations

import argparse
import io
import json
import random
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image, UnidentifiedImageError

# ─────────────────────────────── Config ───────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

KEY_PATH = "/home/skin1004/keys/skin1004-319714-60527c477460.json"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
ALLOWED_MIMES = {"image/jpeg", "image/png", "image/jpg"}
MAX_DIM = 1024            # downscale longest side
CHECKPOINT_EVERY = 50     # save manifest every N images
CLIP_MODEL = "google/siglip-base-patch16-256-multilingual"  # 768-dim, multilingual incl. Korean
DOWNLOAD_WORKERS = 8       # parallel Drive downloads
CHUNK_SIZE = 16            # process this many at a time (download in parallel, embed in batch)

# Path output files
INDEX_CLIP = DATA / "face_clip_index.npy"
META_CLIP = DATA / "face_clip_meta.json"
INDEX_FACE = DATA / "face_index.npy"
META_FACE = DATA / "face_meta.json"
MANIFEST = DATA / "face_drive_manifest.json"


# ─────────────────────────────── Label helpers ───────────────────────────────

_MODEL_SEG = re.compile(r"^\d*\.?\s*Model$", re.IGNORECASE)


def extract_person_label(folder: str) -> str:
    """폴더 경로에서 인물 이름 추출. 'XX. Model/NN. 이름/...' 구조에서 'NN. 이름' 우선.

    Model anchor가 없으면 빈 값 — 인물 폴더가 아닌 곳(제품/이벤트 폴더에 모델 등장)에서
    face_meta의 person_label이 제품명으로 잘못 저장되는 것을 방지.
    """
    parts = [p for p in folder.split("/") if p]
    for i, p in enumerate(parts):
        if _MODEL_SEG.match(p):
            if i + 1 < len(parts):
                return parts[i + 1]
            return ""
    return ""


def nfc(s: str) -> str:
    """macOS 업로드 폴더는 NFD라 한글 검색이 실패함 → 저장은 항상 NFC로."""
    return unicodedata.normalize("NFC", s) if isinstance(s, str) else s


# ─────────────────────────────── Drive helpers ───────────────────────────────

def get_drive():
    creds = service_account.Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# Thread-local Drive client — googleapiclient's http isn't thread-safe across requests
_thread_local = threading.local()

def get_thread_drive():
    if not hasattr(_thread_local, "drive"):
        _thread_local.drive = get_drive()
    return _thread_local.drive


def list_recursive(drive, root_id: str) -> list[dict]:
    """모든 하위 이미지 + folder path 수집."""
    images: list[dict] = []
    queue = [(root_id, "")]
    while queue:
        fid, path = queue.pop(0)
        page = None
        while True:
            resp = drive.files().list(
                q=f"'{fid}' in parents and trashed=false",
                fields="nextPageToken, files(id,name,mimeType,size,modifiedTime)",
                pageSize=1000, pageToken=page,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            for f in resp.get("files", []):
                full_path = f"{path}/{f['name']}"
                if f["mimeType"] == "application/vnd.google-apps.folder":
                    queue.append((f["id"], full_path))
                elif f["mimeType"] in ALLOWED_MIMES:
                    f["_path"] = full_path
                    f["_folder"] = path
                    images.append(f)
            page = resp.get("nextPageToken")
            if not page:
                break
    return images


def download_bytes(drive, file_id: str) -> bytes:
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req, chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


# ─────────────────────────────── Embedders (lazy) ───────────────────────────────

@dataclass
class Embedders:
    clip_model = None     # transformers AutoModel (SigLIP)
    clip_proc = None      # transformers AutoProcessor
    face_app = None       # insightface.app.FaceAnalysis

    def load_clip(self):
        if self.clip_model is None:
            from transformers import AutoModel, AutoProcessor
            print(f"[load] SigLIP multilingual ({CLIP_MODEL})...", flush=True)
            self.clip_model = AutoModel.from_pretrained(CLIP_MODEL)
            self.clip_model.eval()
            self.clip_proc = AutoProcessor.from_pretrained(CLIP_MODEL)
            print(f"[load] dim={self.clip_model.config.text_config.hidden_size}", flush=True)
        return self.clip_model

    def load_face(self):
        if self.face_app is None:
            from insightface.app import FaceAnalysis
            print("[load] InsightFace buffalo_l...", flush=True)
            app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=0, det_size=(640, 640))
            self.face_app = app
        return self.face_app


EMB = Embedders()


def _pool_features(out):
    """SigLIP feature output normalization across transformers versions."""
    import torch
    if isinstance(out, torch.Tensor):
        return out
    if hasattr(out, 'pooler_output') and out.pooler_output is not None:
        return out.pooler_output
    return out.last_hidden_state[:, 0]


# ─────────────────────────────── Image processing ───────────────────────────────

def load_and_resize(img_bytes: bytes) -> Optional[Image.Image]:
    try:
        img = Image.open(io.BytesIO(img_bytes))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > MAX_DIM:
            ratio = MAX_DIM / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        return img
    except (UnidentifiedImageError, OSError) as e:
        print(f"  ⚠️  cannot decode: {e}", flush=True)
        return None


def embed_image_clip(img: Image.Image) -> np.ndarray:
    return embed_image_clip_batch([img])[0]


def embed_image_clip_batch(imgs: list[Image.Image]) -> np.ndarray:
    import torch
    EMB.load_clip()
    inputs = EMB.clip_proc(images=imgs, return_tensors="pt")
    with torch.no_grad():
        feats = _pool_features(EMB.clip_model.get_image_features(**inputs))
    feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.cpu().numpy().astype(np.float32)


def embed_face(img: Image.Image) -> Optional[tuple[np.ndarray, list[float]]]:
    """returns (embedding, bbox) of largest face, or None."""
    app = EMB.load_face()
    arr = np.array(img)[:, :, ::-1]  # PIL RGB → cv2 BGR
    faces = app.get(arr)
    if not faces:
        return None
    # pick largest face by bbox area
    faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
    f = faces[0]
    emb = f.normed_embedding.astype(np.float32)
    bbox = [float(x) for x in f.bbox]
    return emb, bbox


# ─────────────────────────────── Sync core ───────────────────────────────

@dataclass
class SyncState:
    clip_vecs: list[np.ndarray] = field(default_factory=list)
    clip_meta: list[dict] = field(default_factory=list)
    face_vecs: list[np.ndarray] = field(default_factory=list)
    face_meta: list[dict] = field(default_factory=list)
    manifest: dict[str, dict] = field(default_factory=dict)


def load_state() -> SyncState:
    s = SyncState()
    if INDEX_CLIP.exists() and META_CLIP.exists():
        arr = np.load(INDEX_CLIP)
        s.clip_vecs = [arr[i] for i in range(arr.shape[0])]
        s.clip_meta = json.loads(META_CLIP.read_text())
    if INDEX_FACE.exists() and META_FACE.exists():
        arr = np.load(INDEX_FACE)
        s.face_vecs = [arr[i] for i in range(arr.shape[0])]
        s.face_meta = json.loads(META_FACE.read_text())
    if MANIFEST.exists():
        s.manifest = json.loads(MANIFEST.read_text())
    return s


def save_state(s: SyncState):
    if s.clip_vecs:
        arr = np.vstack(s.clip_vecs).astype(np.float32)
        np.save(INDEX_CLIP, arr)
        META_CLIP.write_text(json.dumps(s.clip_meta, ensure_ascii=False))
    if s.face_vecs:
        arr = np.vstack(s.face_vecs).astype(np.float32)
        np.save(INDEX_FACE, arr)
        META_FACE.write_text(json.dumps(s.face_meta, ensure_ascii=False))
    MANIFEST.write_text(json.dumps(s.manifest, ensure_ascii=False))


def stratified_sample(images: list[dict], n: int, seed: int = 42) -> list[dict]:
    """top-level 폴더별 균등 샘플링."""
    by_top: dict[str, list[dict]] = {}
    for f in images:
        top = f["_path"].split("/")[1] if "/" in f["_path"] else f["_path"]
        by_top.setdefault(top, []).append(f)
    rnd = random.Random(seed)
    per_bucket = max(1, n // max(1, len(by_top)))
    sampled: list[dict] = []
    for top, lst in by_top.items():
        rnd.shuffle(lst)
        sampled.extend(lst[:per_bucket])
    if len(sampled) > n:
        rnd.shuffle(sampled)
        sampled = sampled[:n]
    return sampled


def _download_and_decode(f: dict) -> tuple[dict, Optional[Image.Image], Optional[str]]:
    """Worker: download + resize. Returns (meta_input, img_or_None, error_str_or_None)."""
    try:
        drive = get_thread_drive()
        raw = download_bytes(drive, f["id"])
        img = load_and_resize(raw)
        return f, img, None
    except Exception as e:
        return f, None, f"{type(e).__name__}: {str(e)[:120]}"


def process_chunk(images_chunk: list[dict], state: SyncState) -> dict:
    """Download chunk in parallel → batch embed → face check sequentially.

    Returns counts dict: {ok, skip, decode_fail, error, with_face}
    """
    counts = {"ok": 0, "skip": 0, "decode_fail": 0, "error": 0, "with_face": 0}

    # Filter out already-processed (manifest hit)
    todo = []
    for f in images_chunk:
        fid = f["id"]
        if fid in state.manifest and state.manifest[fid].get("modifiedTime") == f.get("modifiedTime"):
            counts["skip"] += 1
        else:
            todo.append(f)
    if not todo:
        return counts

    # Parallel download + decode
    decoded: list[tuple[dict, Image.Image]] = []  # (file_meta, img)
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        futures = [ex.submit(_download_and_decode, f) for f in todo]
        for fut in as_completed(futures):
            f, img, err = fut.result()
            if err is not None:
                counts["error"] += 1
                print(f"  ❌ {f['name']}: {err}", flush=True)
            elif img is None:
                counts["decode_fail"] += 1
            else:
                decoded.append((f, img))

    if not decoded:
        return counts

    # Batch CLIP embedding (huge speedup vs 1-by-1)
    clip_vecs = embed_image_clip_batch([img for _, img in decoded])

    # Per-image: face check + state append
    for (f, img), clip_vec in zip(decoded, clip_vecs):
        folder = nfc((f["_folder"] or "").lstrip("/"))
        path = nfc(f["_path"])
        name = nfc(f["name"])
        person = extract_person_label(folder)
        state.clip_vecs.append(clip_vec)
        clip_meta = {
            "drive_id": f["id"],
            "name": name,
            "path": path,
            "folder": folder,
            "size": int(f.get("size", 0)),
            "modifiedTime": f.get("modifiedTime"),
            "has_face": False,
        }
        state.clip_meta.append(clip_meta)

        face_result = embed_face(img)
        if face_result is not None:
            face_vec, bbox = face_result
            state.face_vecs.append(face_vec)
            state.face_meta.append({
                "drive_id": f["id"],
                "person_label": person,
                "name": name,
                "path": path,
                "bbox": bbox,
            })
            clip_meta["has_face"] = True
            clip_meta["face_bbox"] = bbox
            counts["with_face"] += 1

        state.manifest[f["id"]] = {
            "modifiedTime": f.get("modifiedTime"),
            "name": name,
            "path": path,
            "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        counts["ok"] += 1

    return counts


# ─────────────────────────────── CLI ───────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder-id", required=True)
    ap.add_argument("--sample", type=int, help="stratified sample size (PoC)")
    ap.add_argument("--full", action="store_true", help="process all images")
    ap.add_argument("--resume", action="store_true", help="skip already-processed (auto via manifest)")
    ap.add_argument("--max-mb", type=float, default=20.0, help="skip files larger than this")
    args = ap.parse_args()

    if not args.sample and not args.full:
        ap.error("either --sample N or --full is required")

    print(f"[init] data dir: {DATA}", flush=True)
    state = load_state()
    print(f"[init] existing index: {len(state.clip_meta)} clip, {len(state.face_meta)} faces, {len(state.manifest)} manifest entries", flush=True)

    drive = get_drive()
    print(f"[scan] listing folder {args.folder_id}...", flush=True)
    t0 = time.perf_counter()
    images = list_recursive(drive, args.folder_id)
    print(f"[scan] {len(images)} images found in {time.perf_counter()-t0:.1f}s", flush=True)

    images = [f for f in images if int(f.get("size", 0)) <= args.max_mb * 1024 * 1024]
    print(f"[filter] {len(images)} after size <= {args.max_mb}MB", flush=True)

    if args.sample:
        images = stratified_sample(images, args.sample)
        print(f"[sample] {len(images)} stratified", flush=True)

    counts = {"ok": 0, "skip": 0, "decode_fail": 0, "error": 0, "with_face": 0}
    t_start = time.perf_counter()
    total = len(images)
    processed = 0
    last_checkpoint = 0
    try:
        for chunk_start in range(0, total, CHUNK_SIZE):
            chunk = images[chunk_start:chunk_start + CHUNK_SIZE]
            chunk_counts = process_chunk(chunk, state)
            for k, v in chunk_counts.items():
                counts[k] = counts.get(k, 0) + v
            processed = chunk_start + len(chunk)
            rate = processed / max(time.perf_counter() - t_start, 0.001)
            eta = (total - processed) / rate if rate > 0 else 0
            print(f"  [{processed}/{total}] ok={counts['ok']} skip={counts['skip']} faces={counts['with_face']} decode_fail={counts['decode_fail']} err={counts['error']} | {rate:.1f}/s ETA {eta/60:.1f}m", flush=True)

            if processed - last_checkpoint >= CHECKPOINT_EVERY:
                save_state(state)
                last_checkpoint = processed
                print(f"  💾 checkpoint @ {processed}", flush=True)
    except KeyboardInterrupt:
        print("[abort] interrupted, saving partial state...", flush=True)
        save_state(state)
        sys.exit(1)

    save_state(state)
    print(f"\n[done] {counts}", flush=True)
    print(f"[done] total elapsed: {(time.perf_counter()-t_start)/60:.1f}m", flush=True)
    print(f"[done] index sizes: clip={len(state.clip_meta)}, face={len(state.face_meta)}", flush=True)


if __name__ == "__main__":
    main()
