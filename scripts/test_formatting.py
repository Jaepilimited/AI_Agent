"""Test response formatting quality after prompt improvements."""
import requests
import time
import json
import re

API_URL = "http://localhost:8100/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}

tests = [
    # BigQuery - should have 요약/상세 데이터/분석 및 인사이트 sections
    ("BQ", "skin1004-Search", "2025년 팀별 매출 순위 top 5 알려줘"),
    ("BQ-CHART", "skin1004-Search", "2025년 대륙별 매출 차트로 보여줘"),
    
    # Notion - should have 주요 내용/관련 세부 사항 sections
    ("NOTION", "skin1004-Search", "노션에서 해외 출장 가이드북 보여줘"),
    
    # GWS - should have structured table/list format
    ("GWS-CAL", "skin1004-Search", "이번주 남은 일정 보여줘"),
    ("GWS-MAIL", "skin1004-Search", "최근 받은 중요 메일 보여줘"),
    
    # Direct - should have structured format for complex topics, simple for simple
    ("DIRECT-COMPLEX", "skin1004-Search", "B2B와 B2C의 차이점은?"),
    ("DIRECT-SIMPLE", "skin1004-Search", "안녕하세요"),
    
    # Product routing
    ("PROD", "skin1004-Search", "제품 리스트 알려줘"),
]

results = []
full_outputs = []
print(f"{'='*70}")
print(f"Response Formatting QA Test — {len(tests)} queries")
print(f"{'='*70}")

for tag, model, q in tests:
    print(f"\n{'='*70}")
    print(f"[{tag}] {q}")
    print(f"{'='*70}")
    t0 = time.time()
    try:
        resp = requests.post(API_URL, json={
            "model": model,
            "messages": [{"role": "user", "content": q}]
        }, headers=HEADERS, timeout=180)
        elapsed = time.time() - t0
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
        
        # Check formatting quality
        has_headers = bool(re.findall(r'^#{1,4} ', answer, re.MULTILINE))
        has_bold = '**' in answer
        has_table = '|' in answer and '---' in answer
        has_bullet = '- ' in answer
        has_blockquote = '> ' in answer
        has_chart = '![chart]' in answer or '![Chart]' in answer
        
        fmt_tags = []
        if has_headers: fmt_tags.append("HEADERS")
        if has_bold: fmt_tags.append("BOLD")
        if has_table: fmt_tags.append("TABLE")
        if has_bullet: fmt_tags.append("BULLETS")
        if has_blockquote: fmt_tags.append("QUOTE")
        if has_chart: fmt_tags.append("CHART")
        
        print(f"Time: {elapsed:.1f}s | Chars: {len(answer)} | Format: {', '.join(fmt_tags)}")
        print(f"\n{answer}\n")
        
        results.append({
            "tag": tag, "query": q, "time": elapsed, "chars": len(answer),
            "headers": has_headers, "bold": has_bold, "table": has_table,
            "bullet": has_bullet, "blockquote": has_blockquote,
            "chart": has_chart, "status": "OK"
        })
        full_outputs.append({"tag": tag, "query": q, "answer": answer})
    except Exception as e:
        elapsed = time.time() - t0
        print(f"EXCEPTION ({elapsed:.1f}s): {e}")
        results.append({"tag": tag, "query": q, "time": elapsed, "chars": 0, "status": "ERROR"})
        full_outputs.append({"tag": tag, "query": q, "answer": f"ERROR: {e}"})

# Summary
print(f"\n{'='*70}")
print("FORMATTING SUMMARY")
print(f"{'='*70}")
for r in results:
    fmts = []
    if r.get("headers"): fmts.append("H")
    if r.get("bold"): fmts.append("B")
    if r.get("table"): fmts.append("T")
    if r.get("chart"): fmts.append("C")
    fmt_str = "+".join(fmts) if fmts else "plain"
    print(f"  [{r['status']}] {r['tag']:15s} {r['time']:6.1f}s {r['chars']:5d}ch  fmt={fmt_str}")

ok = sum(1 for r in results if r["status"] == "OK")
headers_count = sum(1 for r in results if r.get("headers"))
bold_count = sum(1 for r in results if r.get("bold"))
table_count = sum(1 for r in results if r.get("table"))
print(f"\nTotal: {ok}/{len(results)} OK | Headers: {headers_count} | Bold: {bold_count} | Tables: {table_count}")

# Save full output with all response texts
output_path = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\test_formatting_result.txt"
with open(output_path, "w", encoding="utf-8") as f:
    f.write(f"Formatting QA Test Results\n{'='*70}\n\n")
    
    for i, fo in enumerate(full_outputs):
        f.write(f"{'='*70}\n")
        f.write(f"[{fo['tag']}] {fo['query']}\n")
        f.write(f"{'='*70}\n")
        r = results[i]
        f.write(f"Status: {r['status']} | Time: {r['time']:.1f}s | Chars: {r['chars']}\n")
        fmts = []
        if r.get("headers"): fmts.append("HEADERS")
        if r.get("bold"): fmts.append("BOLD")
        if r.get("table"): fmts.append("TABLE")
        if r.get("bullet"): fmts.append("BULLETS")
        if r.get("blockquote"): fmts.append("QUOTE")
        if r.get("chart"): fmts.append("CHART")
        f.write(f"Formatting: {', '.join(fmts) if fmts else 'plain'}\n\n")
        f.write(fo["answer"])
        f.write(f"\n\n")
    
    f.write(f"\n{'='*70}\n")
    f.write("FORMATTING SUMMARY\n")
    f.write(f"{'='*70}\n")
    for r in results:
        fmts = []
        if r.get("headers"): fmts.append("H")
        if r.get("bold"): fmts.append("B")
        if r.get("table"): fmts.append("T")
        if r.get("chart"): fmts.append("C")
        fmt_str = "+".join(fmts) if fmts else "plain"
        f.write(f"  [{r['status']}] {r['tag']:15s} {r['time']:6.1f}s {r['chars']:5d}ch  fmt={fmt_str}\n")
    
    f.write(f"\nTotal: {ok}/{len(results)} OK | Headers: {headers_count} | Bold: {bold_count} | Tables: {table_count}\n")

print(f"\nFull results saved to {output_path}")
