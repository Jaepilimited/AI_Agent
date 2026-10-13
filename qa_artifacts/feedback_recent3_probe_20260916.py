"""Verify the original CS questions with real records, and the running HTTP app."""
import json
from pathlib import Path
import sys

import feedback_release_20260908 as access


STAGE = Path(__file__).resolve().parent / "feedback_recent3_20260916"
CASES = [
    {"id": 178, "query": "주문번호 2026082224631661 CS 정보좀",
     "required": ["2026-08-22", "2026-08-27", "537453096961", "573869698301", "반품"]},
    {"id": 179, "query": "주문번호 139515930570616967 CS 정보 알려줘. (접수일 언제인지 ,출고송장 번호는 뭔지 등)",
     "required": ["2025-12-16", "2025-12-22", "540658923226", "844325444023", "수거"]},
    {"id": 180, "query": "네이버 주문정보 2026081423496661 CS 데이터 알려줘.",
     "required": ["2026-08-20", "2026-08-24", "537418082563", "573867634314", "반품", "재배송", "미기재"]},
]


def verify(result):
    by_id = {case["id"]: case for case in CASES}
    for item in result["answers"]:
        answer = item["answer"]
        for needle in by_id[item["id"]]["required"]:
            assert needle in answer, (item["id"], needle, answer)
        assert "http://34.64.99.254:8061/" in answer
        assert all(word not in answer for word in ("record_id", "order_key", "조회 결과 원본", "@매출", "order_no")), answer
    assert "@@국내CS" in result["disabled"]
    assert "조회하지 않았습니다" in result["disabled"]
    assert "@@매출" not in result["disabled"]


def probe(mode):
    cases = ascii(CASES)
    if mode == "candidate":
        info = json.loads((STAGE / "remote_stage.json").read_text())
        code = "import sys\nsys.path.insert(0," + repr(info["stage"]) + ")\n" + """
import json,time
from app.core.cs_order_details import answer_query_sync
from app.agents.orchestrator import OrchestratorAgent
started=time.monotonic()
cases=""" + cases + """
agent=OrchestratorAgent()
answers=[]
for case in cases:
 assert agent._keyword_classify_ex(case['query']) == ('bigquery',True)
 answers.append({'id':case['id'],'answer':answer_query_sync(case['query'],brand_filter='SK,CL,CBT')})
print(json.dumps({'answers':answers,'elapsed_seconds':round(time.monotonic()-started,2)},ensure_ascii=False))
"""
        # Candidate source selection is covered through real entrypoint regression tests.
        result = access.remote(code)
        for item in result["answers"]:
            for needle in next(c for c in CASES if c["id"] == item["id"])["required"]:
                assert needle in item["answer"], (item["id"], needle)
    else:
        code = """
import concurrent.futures,json,time,requests
from app.core.golden_runner import _make_token
started=time.monotonic()
token=_make_token('user')
cases=""" + cases + """
def ask(case, streaming=True, sources=None):
 session=requests.Session()
 session.trust_env=False
 response=session.post('http://127.0.0.1:3000/v1/chat/completions',
  json={'model':'claude','stream':streaming,'enabled_sources':sources,
        'messages':[{'role':'user','content':case['query']}]},
  cookies={'token':token},timeout=40)
 response.raise_for_status()
 if streaming:
  pieces=[]
  for line in response.text.splitlines():
   if line.startswith('data: ') and line != 'data: [DONE]':
    payload=json.loads(line[6:])
    pieces.extend(c.get('delta',{}).get('content','') or '' for c in payload.get('choices',[]))
  answer=''.join(pieces)
 else:
  answer=response.json()['choices'][0]['message']['content']
 return {'id':case['id'],'streaming':streaming,'http':response.status_code,'answer':answer}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 answers=list(pool.map(ask,cases))
answers.append(ask(cases[1],streaming=False))
bare=dict(cases[2],query=""" + ascii("2026081423496661 CS 정보 알려줘.") + """ )
answers.append(ask(bare))
disabled=ask(cases[0],sources=[])['answer']
session=requests.Session()
session.trust_env=False
health={name:session.get('http://127.0.0.1:3000/'+path,timeout=8).status_code for name,path in {'health':'health','ready':'health/ready'}.items()}
print(json.dumps({'answers':answers,'disabled':disabled,'health':health,
 'elapsed_seconds':round(time.monotonic()-started,2)},ensure_ascii=False))
"""
        result = access.remote(code)
        (STAGE / "production_answers.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        verify(result)
        assert result["health"] == {"health": 200, "ready": 200}, result["health"]
    (STAGE / (mode + "_answers.json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verified": mode, "answers": len(result["answers"]), "seconds": result["elapsed_seconds"]}))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    probe(sys.argv[1])
