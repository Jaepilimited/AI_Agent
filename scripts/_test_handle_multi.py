"""_handle_multi 직접 호출 — 컨텍스트 주입 + 소요시간 검증 (임시)"""
import sys, io, os, time, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.agents.orchestrator import OrchestratorAgent

CTX = """[사용자] 2026년 1분기 미국 매출 알려줘
[AI] 2026년 1분기(1월~3월) 미국 총 매출은 약 420.7억원입니다."""

QUERY = "방금 그 매출이 미국 K뷰티 시장 트렌드 대비 어떤 수준인지 외부 요인과 함께 분석해줘"

async def main():
    orch = OrchestratorAgent()
    t0 = time.time()
    result = await orch._handle_multi(QUERY, [], CTX, "gemini")
    elapsed = time.time() - t0
    ans = result.get("answer", "")
    print(f"소요: {elapsed:.0f}초 | 길이: {len(ans)}")
    print("=" * 60)
    print(ans[:1200])
    print("=" * 60)
    print("컨텍스트 반영(420 언급):", "420" in ans)

asyncio.run(main())
