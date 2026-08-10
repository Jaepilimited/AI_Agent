"""사진 → 등록된 초상권 모델 식별 (서브프로세스 전용).

사용: venv/bin/python scripts/identify_model_face.py <이미지 경로>
출력: 마지막 줄에 JSON 한 줄
  {"match": "라리사"|null, "score": 0.72, "second": "...", "second_score": ...,
   "det_score": ..., "n_faces": 2, "enrolled": 5}

⚠️ 앱 프로세스에서 import 하지 말 것 — onnxruntime 세션이 ~400MB 라
WAS(2GB)에서는 반드시 단명 서브프로세스로만 실행한다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MATCH_THRESHOLD = 0.40   # ArcFace 코사인 — 이 이상이면 동일 인물로 판정
MAYBE_THRESHOLD = 0.30   # 애매 구간 — 후보로만 제시


def main() -> None:
    import numpy as np

    from app.core.face_embed import embed_largest_face
    from app.db.mariadb import fetch_all

    img_path = sys.argv[1]
    img_bytes = Path(img_path).read_bytes()

    res = embed_largest_face(img_bytes)
    rows = fetch_all("SELECT model_name, embedding FROM model_faces")
    out = {"match": None, "score": 0.0, "second": None, "second_score": 0.0,
           "det_score": 0.0, "n_faces": 0, "enrolled": len({r["model_name"] for r in rows})}
    if res is None:
        print(json.dumps(out, ensure_ascii=False))
        return
    out["det_score"] = round(res["det_score"], 3)
    out["n_faces"] = res["n_faces"]
    if not rows:
        print(json.dumps(out, ensure_ascii=False))
        return

    q = res["embedding"].astype(np.float32)
    # 모델별 최고 유사도 (한 모델에 여러 등록 사진)
    best: dict[str, float] = {}
    for r in rows:
        v = np.frombuffer(r["embedding"], dtype=np.float32)
        v = v / (np.linalg.norm(v) + 1e-9)
        s = float(np.dot(q, v))
        if s > best.get(r["model_name"], -1):
            best[r["model_name"]] = s
    ranked = sorted(best.items(), key=lambda x: -x[1])
    name1, s1 = ranked[0]
    out["score"] = round(s1, 3)
    if len(ranked) > 1:
        out["second"], out["second_score"] = ranked[1][0], round(ranked[1][1], 3)
    if s1 >= MATCH_THRESHOLD:
        out["match"] = name1
    elif s1 >= MAYBE_THRESHOLD:
        out["maybe"] = name1
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
