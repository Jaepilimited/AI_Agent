"""Phase 0 — in-place 메타 보정.

face_clip_meta.json + face_meta.json + face_drive_manifest.json 의 문자열을 NFC로 통일하고,
face_meta.person_label을 'Model 다음 segment' 규칙으로 재추출한다.

임베딩(.npy)은 건드리지 않으므로 sync 재실행 불필요.

Usage:
    venv/bin/python scripts/face_meta_repair.py            # 보정 + 저장
    venv/bin/python scripts/face_meta_repair.py --dry-run  # 변경 없이 통계만
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

# scripts/face_clip_sync.py 의 헬퍼 재사용
sys.path.insert(0, str(Path(__file__).resolve().parent))
from face_clip_sync import extract_person_label, nfc  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

META_CLIP = DATA / "face_clip_meta.json"
META_FACE = DATA / "face_meta.json"
MANIFEST = DATA / "face_drive_manifest.json"

STRING_KEYS = ("folder", "path", "person_label", "name")


def folder_of(item: dict) -> str:
    """clip_meta는 folder, face_meta는 path만 가짐 — 공통적으로 폴더 경로를 도출."""
    f = item.get("folder")
    if f:
        return f
    p = item.get("path", "")
    if "/" in p:
        return "/".join(p.lstrip("/").rstrip("/").split("/")[:-1])
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print stats only, do not write")
    args = ap.parse_args()

    clip_meta = json.loads(META_CLIP.read_text())
    face_meta = json.loads(META_FACE.read_text())
    manifest = json.loads(MANIFEST.read_text())

    stats = {"clip_nfc_changed": 0, "face_nfc_changed": 0, "face_label_changed": 0, "manifest_nfc_changed": 0}

    # 1) face_clip_meta: NFC만
    for m in clip_meta:
        for k in STRING_KEYS:
            v = m.get(k)
            if isinstance(v, str):
                nv = nfc(v)
                if nv != v:
                    m[k] = nv
                    stats["clip_nfc_changed"] += 1

    # 2) face_meta: NFC + person_label 재추출
    for m in face_meta:
        for k in STRING_KEYS:
            v = m.get(k)
            if isinstance(v, str):
                nv = nfc(v)
                if nv != v:
                    m[k] = nv
                    stats["face_nfc_changed"] += 1
        # path에서 폴더를 떼어 Model 다음 segment 추출 (Model anchor 없으면 빈 값)
        folder = folder_of(m)
        new_label = extract_person_label(folder)
        if new_label != m.get("person_label", ""):
            stats["face_label_changed"] += 1
            m["person_label"] = new_label

    # 3) manifest: NFC만 (path, name)
    for k, v in manifest.items():
        if not isinstance(v, dict):
            continue
        for key in ("name", "path"):
            s = v.get(key)
            if isinstance(s, str):
                ns = nfc(s)
                if ns != s:
                    v[key] = ns
                    stats["manifest_nfc_changed"] += 1

    print("=== Phase 0 repair stats ===")
    for k, n in stats.items():
        print(f"  {k:30s} {n:>6}")

    if args.dry_run:
        print("\n(dry-run, no files written)")
        return

    META_CLIP.write_text(json.dumps(clip_meta, ensure_ascii=False))
    META_FACE.write_text(json.dumps(face_meta, ensure_ascii=False))
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False))
    print("\n✅ written:")
    print(f"  {META_CLIP}")
    print(f"  {META_FACE}")
    print(f"  {MANIFEST}")


if __name__ == "__main__":
    main()
