# -*- coding: utf-8 -*-
"""고친 붐따를 골든 문항 **초안**으로 뽑는다.

⛔ **자동으로 골든셋에 넣지 않는다.** 골든 문항은 기대 문구(`contains`)가 생명이고
   그건 사람이 정해야 한다:
     · 그 답변에서만 나올 값(숫자·고유명사)이어야 한다 — 일반어를 넣으면 **되묻기
       답변에도 들어 있어 거짓 통과**가 난다 (실제로 났던 사고다)
     · 지금 답변에서 낱말을 자동으로 뽑아 넣으면 "지금 답이 맞다" 를 전제하게 되고,
       그 전제가 틀렸을 때 **문항이 오답을 굳힌다**

그래서 이 스크립트는 초안(JSON)만 만들고, 채우는 것은 사람이 한다.

사용법:
    python scripts/propose_golden_items.py                # 최근 14일
    python scripts/propose_golden_items.py --all          # 밀린 것 전부
    python scripts/propose_golden_items.py --out draft.json
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv(".env")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="기간 제한 없이 전부")
    ap.add_argument("--out", help="초안을 쓸 파일 (없으면 화면에만)")
    args = ap.parse_args()

    from app.core.failure_learning import RECENT_DAYS, draft_items, golden_candidates

    candidates = golden_candidates(None if args.all else RECENT_DAYS)
    if not candidates:
        print("[i] 회귀가 빠진 붐따가 없습니다 — 고친 것이 전부 골든으로 지켜지고 있습니다.")
        return 0

    ready = [c for c in candidates if not c.get("needs_history")]
    later = [c for c in candidates if c.get("needs_history")]
    print(f"[i] 회귀가 빠진 붐따 {len(candidates)}건 — 바로 가능 {len(ready)} · 맥락 필요 {len(later)}")
    for c in ready:
        print(f"    #{c['feedback_id']}  {c['question'][:56]}")
        print(f"          왜: {c['why'][:70]}")
    if later:
        print()
        print("[!] 아래는 **후속 질문**이라 앞 대화(`history`)를 채워야 문항이 됩니다:")
        for c in later:
            print(f"    #{c['feedback_id']}  {c['question'][:56]}")

    drafts = draft_items(ready)
    payload = json.dumps({"items": drafts}, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        print(f"[o] 초안 {len(drafts)}건 → {args.out}")
        print("[!] `contains` 가 비어 있습니다. **그 답변에서만 나올 값**으로 채운 뒤")
        print("    `data/golden_set.json` 에 옮기세요 — 일반어를 넣으면 거짓 통과가 납니다.")
    else:
        print()
        print(payload[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
