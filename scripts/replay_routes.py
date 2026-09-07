# -*- coding: utf-8 -*-
"""실사용 질문을 라우터에 다시 통과시켜 **경로 분포**를 찍는다.

usage: python scripts/replay_routes.py questions.json

⚠️ `_keyword_classify_ex` 는 현재 문장만 받는다 — 대화 맥락을 물려받는 후속
   발화는 재현되지 않는다. 의심 건은 **프로덕션 답변 원문**으로 확인할 것
   (2026-09-07 에 이 방법으로 오판 하나를 잡았다).
"""
import io
import json
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.agents.orchestrator import OrchestratorAgent

agent = OrchestratorAgent.__new__(OrchestratorAgent)
questions = json.load(open(sys.argv[1], encoding="utf-8"))

routes, conf = Counter(), Counter()
for item in questions:
    try:
        route, confident = OrchestratorAgent._keyword_classify_ex(agent, item["q"])
    except Exception:
        continue
    routes[route] += 1
    conf[bool(confident)] += 1

total = sum(routes.values())

if not total:
    print(f"질문 {len(questions)}건을 읽었지만 **한 건도 분류되지 않았다** — "
          f"입력 형식이나 임포트를 확인할 것")
    sys.exit(1)

print(f"질문 {total}건")
print("경로 :", dict(routes))
print(f"확신 : {conf[True]}건 ({conf[True] / total * 100:.1f}%) · "
      f"LLM 위임 {conf[False]}건")
