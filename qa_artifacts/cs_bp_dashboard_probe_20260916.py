"""Read-only candidate/live probes; save only registry and answer evidence."""
import json
from pathlib import Path
import sys

import feedback_release_20260908 as access

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "qa_artifacts/cs_bp_dashboard_release"


def probe(mode):
    prefix = ""
    if mode == "candidate":
        info = json.loads((STAGE / "remote_stage.json").read_text())
        prefix = "import sys\nsys.path.insert(0," + repr(info["stage"]) + ")\n"
    code = prefix + """
import asyncio,json,time
from app.api.routes import list_datasources
from app.agents import cs_agent
from app.agents.orchestrator import OrchestratorAgent
from app.core.cs_dashboard import answer_query_sync
async def check():
 started=time.monotonic()
 entries=await list_datasources()
 keys={e['key'] for e in entries}
 assert 'CS' not in keys
 wanted=""" + ascii(["BP", "국내CS", "해외CS"]) + """
 registry=[e for e in entries if e['key'] in wanted]
 for key in ('CS','BP'):
  entry,clean=OrchestratorAgent.parse_db_prefix('@@'+key+' question')
  assert entry['key']=='BP' and entry['route']=='cs'
 query=""" + ascii("센텔라 앰플 사용법과 제품정보를 알려줘") + """
 bp_answer=await cs_agent.run(query)
 assert 'https://docs.google.com/spreadsheets/d/' in bp_answer
 assert 'https://www.notion.so/' in bp_answer
 dashboard={}
 for region in """ + ascii(["국내", "해외"]) + """:
  question='2026'+""" + ascii("년 8월 ") + "+region+" + ascii(" CS 건수와 원본 링크 알려줘") + """
  dashboard[region]=await asyncio.to_thread(answer_query_sync,question)
  assert dashboard[region] and '34.64.99.254:8061/' in dashboard[region]
  assert 'startDate=2026-08-01' in dashboard[region]
 return {'registry':registry,'bp_question':query,'bp_answer':bp_answer,'dashboard':dashboard,'elapsed_seconds':round(time.monotonic()-started,2)}
print(json.dumps(asyncio.run(check()),ensure_ascii=False))
"""
    result = access.remote(code)
    (STAGE / (mode + "_probe.json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"saved": mode + "_probe.json", "keys": list(result),
                      "elapsed_seconds": result.get("elapsed_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    probe(sys.argv[1])
