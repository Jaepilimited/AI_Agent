"""얼굴/제품 사진 검색 — Drive 인덱스 기반 CLIP + InsightFace.

data/face_clip_index.npy + face_clip_meta.json (CLIP 768 또는 512차원)
data/face_index.npy + face_meta.json (InsightFace 512차원)
"""
from __future__ import annotations

import asyncio
import io
import json
import threading
import unicodedata
from pathlib import Path
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"

INDEX_CLIP = DATA / "face_clip_index.npy"
META_CLIP = DATA / "face_clip_meta.json"
INDEX_FACE = DATA / "face_index.npy"
META_FACE = DATA / "face_meta.json"

CLIP_MODEL = "google/siglip-base-patch16-256-multilingual"  # 768-dim, multilingual incl. Korean

# Lazy-loaded singletons
_clip_vecs: Optional[np.ndarray] = None
_clip_meta: list[dict] = []
_face_vecs: Optional[np.ndarray] = None
_face_meta: list[dict] = []
_index_loaded = False
_index_lock = threading.Lock()

_clip_model = None     # transformers SigLIP model (text + image in same space)
_clip_proc = None      # transformers AutoProcessor
_face_app = None
_model_lock = threading.Lock()


def _load_index_once():
    global _clip_vecs, _clip_meta, _face_vecs, _face_meta, _index_loaded
    if _index_loaded:
        return
    with _index_lock:
        if _index_loaded:
            return
        if INDEX_CLIP.exists() and META_CLIP.exists():
            _clip_vecs = np.load(INDEX_CLIP).astype(np.float32)
            _clip_meta = json.loads(META_CLIP.read_text())
            _normalize_meta_strings(_clip_meta)
            logger.info("face_clip_index_loaded", count=len(_clip_meta), dim=_clip_vecs.shape[1])
        if INDEX_FACE.exists() and META_FACE.exists():
            _face_vecs = np.load(INDEX_FACE).astype(np.float32)
            _face_meta = json.loads(META_FACE.read_text())
            _normalize_meta_strings(_face_meta)
            logger.info("face_index_loaded", count=len(_face_meta))
        _index_loaded = True


def _normalize_meta_strings(meta: list[dict]) -> None:
    """폴더/경로/라벨을 NFC로 통일 — Drive 메타가 macOS 업로드라 NFD인 경우 한글 검색 매칭 실패."""
    for m in meta:
        for k in ("folder", "path", "person_label", "name"):
            v = m.get(k)
            if isinstance(v, str):
                m[k] = unicodedata.normalize("NFC", v)


def _get_clip():
    """Returns (model, processor) — SigLIP supports both text and image in one model."""
    global _clip_model, _clip_proc
    if _clip_model is None:
        with _model_lock:
            if _clip_model is None:
                from transformers import AutoModel, AutoProcessor
                logger.info("siglip_loading", model=CLIP_MODEL)
                _clip_model = AutoModel.from_pretrained(CLIP_MODEL)
                _clip_model.eval()
                _clip_proc = AutoProcessor.from_pretrained(CLIP_MODEL)
                logger.info("siglip_loaded", dim=_clip_model.config.text_config.hidden_size)
    return _clip_model, _clip_proc


def _pool_features(out):
    import torch
    if isinstance(out, torch.Tensor):
        return out
    if hasattr(out, 'pooler_output') and out.pooler_output is not None:
        return out.pooler_output
    return out.last_hidden_state[:, 0]


def _get_face_app():
    global _face_app
    if _face_app is None:
        with _model_lock:
            if _face_app is None:
                from insightface.app import FaceAnalysis
                logger.info("insightface_loading")
                app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
                app.prepare(ctx_id=0, det_size=(640, 640))
                _face_app = app
    return _face_app


def _l2_normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


# ─── Embedding ───────────────────────────────────────────────────────────

def embed_text(query: str) -> np.ndarray:
    import torch
    model, proc = _get_clip()
    inputs = proc(text=[query], return_tensors="pt", padding="max_length", max_length=64, truncation=True)
    with torch.no_grad():
        feats = _pool_features(model.get_text_features(**inputs))
    feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].cpu().numpy().astype(np.float32)


def embed_image_bytes(img_bytes: bytes) -> np.ndarray:
    import torch
    from PIL import Image
    model, proc = _get_clip()
    img = Image.open(io.BytesIO(img_bytes))
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    inputs = proc(images=[img], return_tensors="pt")
    with torch.no_grad():
        feats = _pool_features(model.get_image_features(**inputs))
    feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].cpu().numpy().astype(np.float32)


def embed_face_bytes(img_bytes: bytes) -> Optional[np.ndarray]:
    """returns face embedding of largest face (already L2-normalized) or None."""
    from PIL import Image
    app = _get_face_app()
    img = Image.open(io.BytesIO(img_bytes))
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    arr = np.array(img)[:, :, ::-1]
    faces = app.get(arr)
    if not faces:
        return None
    faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
    return faces[0].normed_embedding.astype(np.float32)


# ─── Search ──────────────────────────────────────────────────────────────

def search_clip(qvec: np.ndarray, top_k: int = 12, folder_filter: Optional[str] = None) -> list[dict]:
    _load_index_once()
    if _clip_vecs is None or _clip_vecs.size == 0:
        return []
    sims = _clip_vecs @ qvec
    if folder_filter:
        ff = unicodedata.normalize("NFC", folder_filter).lower()
        mask = np.array([ff in (m.get("folder") or "").lower() for m in _clip_meta])
        sims = np.where(mask, sims, -1.0)

    k = min(top_k, len(sims))
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]
    return [{"score": float(sims[i]), **_clip_meta[i]} for i in idx]


def search_face(qvec: np.ndarray, top_k: int = 12) -> list[dict]:
    _load_index_once()
    if _face_vecs is None or _face_vecs.size == 0:
        return []
    sims = _face_vecs @ qvec
    k = min(top_k, len(sims))
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]
    return [{"score": float(sims[i]), **_face_meta[i]} for i in idx]


def index_stats() -> dict:
    _load_index_once()
    return {
        "clip_count": len(_clip_meta),
        "face_count": len(_face_meta),
        "clip_dim": int(_clip_vecs.shape[1]) if _clip_vecs is not None else 0,
    }


# ─── Answer derivation (label extraction + majority voting) ──────────────

import re

_NUM_PREFIX = re.compile(r"^\s*\d+\.\s*")
_DATE_PREFIX = re.compile(r"^\d{6}_")
_NOISE_SEGMENTS = {"기본", "저해상", "고해상", "JPG", "PSD", "old", ""}


def _strip_num(s: str) -> str:
    """'07. 백송민 (May)' -> '백송민 (May)'."""
    return _NUM_PREFIX.sub("", s).strip()


def extract_label(folder_path: str) -> tuple[str, str]:
    """폴더 경로에서 의미 있는 라벨 추출.

    Returns: (label_type, label_text)
      label_type: 'person' | 'product' | 'event' | 'other'
    """
    if not folder_path:
        return ("other", "")
    parts = [p for p in folder_path.split("/") if p]

    # 1) Model 다음 의미 있는 세그먼트 = 인물명 (noise/잡음 폴더 스킵)
    for i, p in enumerate(parts):
        if re.match(r"^\d*\.?\s*Model$", p, re.IGNORECASE):
            for j in range(i + 1, len(parts)):
                seg = parts[j]
                if seg in _NOISE_SEGMENTS:
                    continue
                stripped = _strip_num(seg)
                if not stripped or stripped in _NOISE_SEGMENTS:
                    continue
                # 인물 폴더는 보통 'NN. 이름' 형식 (number prefix 있음) — 없으면 잡음일 수 있음
                if not _NUM_PREFIX.match(seg):
                    continue
                return ("person", stripped)
            break

    # 2) Product / Transparent Background / Teca / Lab in nature 라인 = 제품
    has_product_anchor = any(
        re.match(r"^\d*\.?\s*Product$", p, re.IGNORECASE)
        or "Transparent Background" in p
        or "Teca" in p
        or "Lab in nature" in p
        for p in parts
    )
    if has_product_anchor:
        # 라인명 찾기 (Centella, Hyalu-Cica, Retinol 등)
        line = None
        for p in parts:
            stripped = _strip_num(p)
            if stripped in _PRODUCT_LINES:
                line = stripped
                break

        # 사이즈 변형(Mini 등) 추출
        size_mod = None
        for p in parts:
            if _strip_num(p) == "Mini":
                size_mod = "Mini"
                break

        # 제품 폴더명 찾기 — 카테고리/노이즈/라인 단독은 제외
        category_segs = {"Lab in nature", "The Untouched Nature", "Teca", "Flagship Store", "Image"}
        skip_segs = _NOISE_SEGMENTS | category_segs | {
            "Together", "Mini", "etc.", "Etc", "etc",
            "Texture", "Box", "정방형", "가로형", "세로형",
        }
        product_name = None
        for p in reversed(parts):
            if p in skip_segs:
                continue
            stripped = _strip_num(p)
            if not stripped or stripped in skip_segs:
                continue
            if "Product" in stripped or "Transparent" in stripped or "Texture" in stripped:
                continue
            if line and stripped == line:
                continue
            product_name = stripped
            break

        # 라벨 결합 — line과 product_name의 단어 겹침을 제거해 "Centella Teca Teca Cream" 같은 중복 방지
        final = None
        if product_name and line:
            if line in product_name:
                final = product_name
            else:
                lw, pw = line.split(), product_name.split()
                overlap = 0
                for k in range(min(len(lw), len(pw)), 0, -1):
                    if lw[-k:] == pw[:k]:
                        overlap = k
                        break
                final = " ".join(lw + pw[overlap:])
        elif product_name:
            final = product_name
        elif line:
            final = line

        if final and size_mod and size_mod not in final:
            final = f"{final} ({size_mod})"

        if final:
            return ("product", final)

        # fallback (라인/제품 둘 다 없을 때)
        for p in reversed(parts):
            if p in _NOISE_SEGMENTS:
                continue
            stripped = _strip_num(p)
            if stripped and "Product" not in stripped and "Transparent" not in stripped:
                return ("product", stripped)

    # 3) Flagship 이벤트
    if any("Flagship" in p for p in parts):
        for p in parts:
            if _DATE_PREFIX.match(p):
                return ("event", _strip_num(p))

    # 4) 기본: 가장 깊은 비-잡음 세그먼트
    for p in reversed(parts):
        if p in _NOISE_SEGMENTS:
            continue
        stripped = _strip_num(p)
        if stripped:
            return ("other", stripped)

    return ("other", "")


_PRODUCT_LINES = [
    "Centella", "Hyalu-Cica", "Hyalu-Teca", "Centella Teca", "Tone Brightening",
    "Tea-Trica", "Probio-Cica", "Poremizing", "Zombie Beauty",
    "Niacinamide", "Matrixyl", "Retinol", "Azelaic acid",
]


def _extract_product_line(folder: str) -> Optional[str]:
    """폴더 경로에서 SKIN1004 제품 라인 이름 추출."""
    for line in _PRODUCT_LINES:
        if line in folder:
            return line
    return None


def _folder_of(r: dict) -> str:
    """결과 항목에서 폴더 경로 도출 — clip_meta는 'folder' 키, face_meta는 'path'만 가짐."""
    f = r.get("folder")
    if f:
        return f
    p = r.get("path", "")
    if "/" in p:
        return "/".join(p.lstrip("/").rstrip("/").split("/")[:-1])
    return ""


def derive_answer(results: list[dict], consider_top: int = 5, min_score: float = 0.15) -> Optional[dict]:
    """Top-K 결과에서 score-weighted vote로 답변 도출 + 자연어 문장 생성.

    개선점 (Phase 2):
      1) 라벨 추출 실패한 항목은 분모에서 제외 (이전엔 len(top) 그대로라 confidence 부당하게 낮음)
      2) score-weighted voting (top1=0.99도 1점 취급하던 단순 count 대신)
      3) strong top1 boost: top1 score가 매우 높으면 거의 확실로 간주
      4) score < min_score 항목은 vote 제외 (노이즈 컷)

    Returns:
        {type, label, confidence, avg_score, match_count, considered, sentence, related_lines}
        또는 None (결과 없음)
    """
    if not results:
        return None

    # 라벨 추출 + min_score 필터
    labeled: list[tuple[dict, str, str, float]] = []  # (r, ltype, label, score)
    for r in results[:consider_top]:
        score = r.get("score", 0.0)
        if score < min_score:
            continue
        ltype, label = extract_label(_folder_of(r))
        if not label:
            continue
        labeled.append((r, ltype, label, max(0.0, score)))

    if not labeled:
        return None

    counts: dict[tuple[str, str], int] = {}
    weights: dict[tuple[str, str], float] = {}
    score_sums: dict[tuple[str, str], float] = {}
    for _, ltype, label, score in labeled:
        key = (ltype, label)
        counts[key] = counts.get(key, 0) + 1
        weights[key] = weights.get(key, 0.0) + score
        score_sums[key] = score_sums.get(key, 0.0) + score

    # weight 1순위, count 2순위 tiebreak
    best = max(counts.keys(), key=lambda k: (weights[k], counts[k]))
    ltype, label = best

    # confidence = best 라벨의 weight 점유율
    total_weight = sum(weights.values()) or 1.0
    confidence = weights[best] / total_weight

    # Strong top1 boost: top1이 best와 같은 라벨이면 top1 score를 confidence에 반영
    top1_r, top1_type, top1_label, top1_score = labeled[0]
    if (top1_type, top1_label) == best:
        if top1_score >= 0.95:
            confidence = max(confidence, 0.95)
        elif top1_score >= 0.85:
            confidence = max(confidence, min(0.9, 0.5 + top1_score * 0.5))
        elif top1_score >= 0.7:
            confidence = max(confidence, 0.6)

    confidence = min(1.0, confidence)
    avg_score = score_sums[best] / counts[best]

    # Context: 관련 제품 라인 (Top-K에서 추출)
    related_lines: list[str] = []
    for r, _, _, _ in labeled:
        line = _extract_product_line(_folder_of(r))
        if line and line not in related_lines:
            related_lines.append(line)

    # 라인 이미 라벨에 포함된 경우 related_lines에서 제외
    related_filtered = [l for l in related_lines if l not in label]

    # Natural language sentence
    conf_pct = int(confidence * 100)
    if ltype == "person":
        if related_filtered:
            sentence = f"이 사진의 모델은 **{label}**입니다. {', '.join(related_filtered[:3])} 라인 촬영본으로 보입니다. (신뢰도 {conf_pct}%)"
        else:
            sentence = f"이 사진의 모델은 **{label}**입니다. (신뢰도 {conf_pct}%)"
    elif ltype == "product":
        sentence = f"이 제품은 **{label}**입니다. (신뢰도 {conf_pct}%)"
    elif ltype == "event":
        sentence = f"이 사진은 **{label}** 행사/이벤트 사진입니다. (신뢰도 {conf_pct}%)"
    else:
        sentence = f"가장 비슷한 항목: **{label}** (신뢰도 {conf_pct}%)"

    # 신뢰도별 안내
    if confidence < 0.3:
        sentence += " ⚠️ 매우 낮은 신뢰도 — 정확한 식별이 어렵습니다. 다른 사진과 혼합된 결과입니다."
    elif confidence < 0.5:
        sentence += " ⚠️ 낮은 신뢰도 — 결과를 검토하세요."

    return {
        "type": ltype,
        "label": label,
        "confidence": confidence,
        "avg_score": avg_score,
        "match_count": counts[best],
        "considered": len(labeled),
        "sentence": sentence,
        "related_lines": related_lines[:5],
    }
