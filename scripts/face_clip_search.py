"""face_clip_index 검색 도구 (PoC).

Usage:
    venv/bin/python scripts/face_clip_search.py --text "centella ampoule"
    venv/bin/python scripts/face_clip_search.py --text "센텔라 앰플"
    venv/bin/python scripts/face_clip_search.py --image /path/to/query.jpg
    venv/bin/python scripts/face_clip_search.py --drive-id <FILE_ID>
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX_CLIP = DATA / "face_clip_index.npy"
META_CLIP = DATA / "face_clip_meta.json"

CLIP_MODEL_TEXT = "sentence-transformers/clip-ViT-B-32-multilingual-v1"
CLIP_MODEL_IMG = "clip-ViT-B-32"


def load_index():
    if not INDEX_CLIP.exists():
        sys.exit(f"인덱스 없음: {INDEX_CLIP}. 먼저 face_clip_sync.py 실행.")
    vecs = np.load(INDEX_CLIP).astype(np.float32)
    meta = json.loads(META_CLIP.read_text())
    return vecs, meta


def embed_text(query: str) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    enc = SentenceTransformer(CLIP_MODEL_TEXT)
    v = enc.encode([query], convert_to_numpy=True, normalize_embeddings=True)
    return v[0].astype(np.float32)


def embed_image(path_or_bytes) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    enc = SentenceTransformer(CLIP_MODEL_IMG)
    if isinstance(path_or_bytes, (str, Path)):
        img = Image.open(path_or_bytes)
    else:
        img = Image.open(io.BytesIO(path_or_bytes))
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    v = enc.encode([img], convert_to_numpy=True, normalize_embeddings=True)
    return v[0].astype(np.float32)


def search(qvec: np.ndarray, vecs: np.ndarray, meta: list[dict], top_k: int = 10):
    sims = vecs @ qvec
    idx = np.argpartition(-sims, min(top_k, len(sims)-1))[:top_k]
    idx = idx[np.argsort(-sims[idx])]
    return [(float(sims[i]), meta[i]) for i in idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="text query (multilingual: 한국어 OK)")
    ap.add_argument("--image", help="path to query image")
    ap.add_argument("--drive-id", help="Drive file_id of query image (downloads via service account)")
    ap.add_argument("--top-k", type=int, default=10)
    args = ap.parse_args()

    if not (args.text or args.image or args.drive_id):
        ap.error("--text or --image or --drive-id required")

    vecs, meta = load_index()
    print(f"[index] {len(meta)} entries loaded")

    if args.text:
        q = embed_text(args.text)
        label = f"text='{args.text}'"
    elif args.image:
        q = embed_image(args.image)
        label = f"image={args.image}"
    else:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
        creds = service_account.Credentials.from_service_account_file(
            "/home/skin1004/keys/skin1004-319714-60527c477460.json",
            scopes=["https://www.googleapis.com/auth/drive.readonly"])
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        req = drive.files().get_media(fileId=args.drive_id, supportsAllDrives=True)
        buf = io.BytesIO()
        d = MediaIoBaseDownload(buf, req, chunksize=4*1024*1024)
        done = False
        while not done:
            _, done = d.next_chunk()
        q = embed_image(buf.getvalue())
        label = f"drive_id={args.drive_id}"

    results = search(q, vecs, meta, top_k=args.top_k)
    print(f"\n[query] {label}")
    print(f"[results] top-{args.top_k}:\n")
    for rank, (score, m) in enumerate(results, 1):
        print(f"  {rank:2d}. {score:.3f}  {m.get('path', m.get('name'))}")
        if m.get("has_face"):
            print(f"      → face detected")


if __name__ == "__main__":
    main()
