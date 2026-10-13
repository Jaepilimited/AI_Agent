"""Read-only candidate probes. Suppress SQL example/cache writes during verification."""
import json
from pathlib import Path
import sys

import feedback_release_20260908 as access

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "qa_artifacts/cs_metrics_release"
CASES = {
    "domestic": ("2026년 8월 국내 CS 처리 기록은 총 몇 건이야?", None),
    "overseas": ("2026년 8월 해외 CS 국가별 환불 건수와 USD 환불액, 원화 미환산 건수 알려줘", ["해외CS"]),
    "items": ("2026년 8월 접수 상품별 처리 기록 수와 상품수량 알려줘", ["국내CS"]),
    "intake": ("이번 달 글로벌 자사몰 CS 인입 건수 알려줘", None),
    "reason": ("2026년 8월 환불 사유별 건수 알려줘", ["해외CS"]),
    "country": ("2026년 8월 해외 CS 미국 환불 완료 건수 알려줘", ["해외CS"]),
    "both": ("2026년 8월 국내 CS 처리 기록 수와 해외 CS 환불 건수를 각각 비교해줘", ["국내CS", "해외CS"]),
}


def run(case):
    info = json.loads((STAGE / "remote_stage.json").read_text())
    query, sources = CASES[case]
    # ASCII escaped literals survive the Windows PowerShell stdin encoding.
    code = """
import json,sys,time,os
from pathlib import Path
stage=Path(STAGE_PATH)
sys.path.insert(0,str(stage))
from app.agents import sql_agent as agent
from app.agents.orchestrator import OrchestratorAgent
from app.core import value_lists,sql_examples
assert str(agent.__file__).startswith(str(stage))
agent._cache_lookup=lambda *a,**k:None
agent._cache_store=lambda *a,**k:None
sql_examples.record=lambda *a,**k:None
original=value_lists._cached
live={}
def cached(name):
 if name.startswith(('DomesticCS','OverseasCS')):
  if name not in live:
   live[name]=value_lists._fetch_live(name)
  return live[name]
 return original(name)
value_lists._cached=cached
query=QUERY_VALUE
sources=SOURCES_VALUE
state={'query':query,'enabled_sources':sources,'can_view_fi':False,'brand_filter':None,'conversation_context':''}
t=time.perf_counter()
state.update(agent.generate_sql(state))
state.update(agent.validate_sql_node(state))
if state.get('sql_valid'):
 state.update(agent.execute_sql(state))
out={'case':CASE_VALUE,'query':query,'sources':sources,'route':OrchestratorAgent()._keyword_classify_ex(query),
 'sql':state.get('generated_sql'),'valid':state.get('sql_valid'),'error':state.get('error'),
 'rows':state.get('sql_result'),'seconds':round(time.perf_counter()-t,2),
 'fast_answer':os.getenv('BQ_FAST_ANSWER'),'tool_loop':os.getenv('BQ_TOOL_LOOP')}
if CASE_VALUE in ('intake','overseas','both') and not state.get('error'):
 out['answer']=agent.format_answer(state).get('answer')
print(json.dumps(out,default=str,ensure_ascii=True))
"""
    for key, value in {"STAGE_PATH": info["stage"], "QUERY_VALUE": query, "SOURCES_VALUE": sources, "CASE_VALUE": case}.items():
        code = code.replace(key, ascii(value))
    result = access.remote(code)
    (STAGE / ("probe_" + case + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run(sys.argv[1])
