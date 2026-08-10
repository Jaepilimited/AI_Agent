"""얼굴 임베딩 — buffalo_l ONNX 직접 추론 (insightface 패키지 불필요).

왜 insightface 를 안 쓰나:
    WAS 는 Python 3.12 인데 insightface 0.7.3 은 소스 빌드(C++ 컴파일)가 필요하고
    서버에 빌드 도구가 없다. 대신 insightface 가 쓰는 것과 **동일한 모델 파일**
    (buffalo_l: det_10g.onnx SCRFD 탐지 + w600k_r50.onnx ArcFace 임베딩)을
    onnxruntime 으로 직접 돌린다 — 임베딩이 기존 face_index.npy(GCP 빌드본)와
    호환된다 (같은 모델이므로).

메모리:
    onnxruntime 세션 로드 시 ~400MB. WAS(RAM 2GB)에서는 **앱 프로세스에 로드하지
    말고** scripts/identify_model_face.py 서브프로세스로 돌린다 (끝나면 반환).

검증:
    GCP insightface 임베딩과 같은 이미지 코사인 유사도 0.99+ 확인 후 사용
    (2026-08-10, scripts 참조).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_DIR = ROOT / "data" / "face_models"
DET_MODEL = MODEL_DIR / "det_10g.onnx"
REC_MODEL = MODEL_DIR / "w600k_r50.onnx"

# ArcFace 112x112 표준 5점 랜드마크 (insightface arcface_dst 와 동일)
_ARCFACE_DST = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

_det_sess = None
_rec_sess = None


def _sessions():
    global _det_sess, _rec_sess
    if _det_sess is None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        _det_sess = ort.InferenceSession(str(DET_MODEL), so, providers=["CPUExecutionProvider"])
        _rec_sess = ort.InferenceSession(str(REC_MODEL), so, providers=["CPUExecutionProvider"])
    return _det_sess, _rec_sess


def _nms(dets: np.ndarray, thresh: float = 0.4) -> list[int]:
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        ovr = (w * h) / (areas[i] + areas[order[1:]] - w * h)
        order = order[np.where(ovr <= thresh)[0] + 1]
    return keep


def detect_faces(img_bgr: np.ndarray, det_thresh: float = 0.5):
    """SCRFD det_10g — [(bbox(4), score, kps(5,2)), ...] 반환 (원본 좌표계)."""
    det, _ = _sessions()
    input_size = 640
    h0, w0 = img_bgr.shape[:2]
    scale = input_size / max(h0, w0)
    nh, nw = int(round(h0 * scale)), int(round(w0 * scale))
    import cv2
    resized = cv2.resize(img_bgr, (nw, nh))
    canvas = np.zeros((input_size, input_size, 3), dtype=np.uint8)
    canvas[:nh, :nw] = resized

    blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128, (input_size, input_size),
                                 (127.5, 127.5, 127.5), swapRB=True)
    outs = det.run(None, {det.get_inputs()[0].name: blob})

    strides = [8, 16, 32]
    fmc = 3
    all_dets, all_kps = [], []
    for idx, stride in enumerate(strides):
        scores = outs[idx].reshape(-1)
        bbox = outs[idx + fmc].reshape(-1, 4) * stride
        kps = outs[idx + fmc * 2].reshape(-1, 10) * stride
        side = input_size // stride
        cx, cy = np.meshgrid(np.arange(side), np.arange(side))
        centers = np.stack([cx, cy], axis=-1).reshape(-1, 2).astype(np.float32) * stride
        centers = np.repeat(centers, 2, axis=0)  # num_anchors=2
        pos = np.where(scores >= det_thresh)[0]
        if pos.size == 0:
            continue
        c, b, s, k = centers[pos], bbox[pos], scores[pos], kps[pos]
        boxes = np.stack([c[:, 0] - b[:, 0], c[:, 1] - b[:, 1],
                          c[:, 0] + b[:, 2], c[:, 1] + b[:, 3]], axis=-1)
        pts = k.reshape(-1, 5, 2) + c[:, None, :]
        all_dets.append(np.concatenate([boxes, s[:, None]], axis=1))
        all_kps.append(pts)
    if not all_dets:
        return []
    dets = np.concatenate(all_dets)
    kpss = np.concatenate(all_kps)
    keep = _nms(dets)
    out = []
    for i in keep:
        out.append((dets[i, :4] / scale, float(dets[i, 4]), kpss[i] / scale))
    return out


def _umeyama(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """유사 변환(회전+등방 스케일+이동) 추정 — skimage SimilarityTransform 과 동일 공식.

    insightface 정렬과 같은 결과를 내야 기존 인덱스 임베딩과의 유사도가 보존된다
    (estimateAffinePartial2D 로는 코사인 0.96 수준 — umeyama 로 0.99+).
    """
    n, d = src.shape
    mu_s, mu_d = src.mean(0), dst.mean(0)
    ss, dd = src - mu_s, dst - mu_d
    cov = dd.T @ ss / n
    U, S, Vt = np.linalg.svd(cov)
    sgn = np.ones(d)
    if np.linalg.det(cov) < 0:
        sgn[-1] = -1
    R = U @ np.diag(sgn) @ Vt
    var_s = (ss ** 2).sum() / n
    scale = (S * sgn).sum() / var_s
    t = mu_d - scale * (R @ mu_s)
    M = np.zeros((2, 3), dtype=np.float32)
    M[:, :2] = scale * R
    M[:, 2] = t
    return M


def embed_face(img_bgr: np.ndarray, kps: np.ndarray) -> np.ndarray:
    """5점 정렬 → ArcFace 512차원 L2 정규화 임베딩."""
    import cv2
    _, rec = _sessions()
    M = _umeyama(kps.astype(np.float64), _ARCFACE_DST.astype(np.float64))
    aligned = cv2.warpAffine(img_bgr, M, (112, 112), borderValue=0.0)
    blob = cv2.dnn.blobFromImage(aligned, 1.0 / 127.5, (112, 112),
                                 (127.5, 127.5, 127.5), swapRB=True)
    emb = rec.run(None, {rec.get_inputs()[0].name: blob})[0].reshape(-1)
    return emb / (np.linalg.norm(emb) + 1e-9)


def embed_largest_face(img_bytes: bytes) -> Optional[dict]:
    """이미지 바이트 → 가장 큰 얼굴의 임베딩. 얼굴 없으면 None.

    부분 크롭(얼굴이 프레임 대부분)은 SCRFD 앵커 범위를 벗어나 미검출된다 —
    여백을 둘러 얼굴 비중을 줄인 뒤 재시도한다 (2026-08-10 검증에서 실측).
    """
    import cv2
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None

    candidates = [img]
    h, w = img.shape[:2]
    padded = cv2.copyMakeBorder(img, h // 2, h // 2, w // 2, w // 2,
                                cv2.BORDER_CONSTANT, value=(0, 0, 0))
    candidates.append(padded)

    for thresh in (0.5, 0.3):
        for cand in candidates:
            faces = detect_faces(cand, det_thresh=thresh)
            if faces:
                bbox, score, kps = max(
                    faces, key=lambda f: (f[0][2] - f[0][0]) * (f[0][3] - f[0][1]))
                return {"embedding": embed_face(cand, kps), "det_score": score,
                        "n_faces": len(faces)}
    return None
