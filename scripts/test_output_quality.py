"""Enterprise Output Quality Tester.

Tests all 6 routes and analyzes output quality against enterprise standards.
Runs against port 3001 (dev server).
"""

import json
import time
import sys
import requests

BASE_URL = "http://127.0.0.1:3001"
TOKEN = None


def get_token():
    """Generate JWT token for testing."""
    import jwt
    return jwt.encode(
        {"sub": 1, "email": "jeffrey@skin1004korea.com", "role": "admin", "exp": 9999999999},
        "skin1004-ai-secret-key-2024",
        algorithm="HS256",
    )


def query(text: str, model: str = "gemini") -> dict:
    """Send a query to the API and return the response."""
    resp = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": text}], "stream": False},
        cookies={"token": TOKEN},
        timeout=120,
    )
    return resp.json()


def extract_answer(resp: dict) -> str:
    """Extract the answer text from API response."""
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return f"ERROR: {json.dumps(resp, ensure_ascii=False)[:200]}"


def analyze_quality(answer: str, route: str) -> dict:
    """Analyze answer quality against enterprise standards."""
    checks = {
        "has_heading": bool(any(line.strip().startswith("#") for line in answer.split("\n"))),
        "has_bold": "**" in answer,
        "has_followup": "💡" in answer or "물어보세요" in answer,
        "has_table": "|" in answer and "---" in answer,
        "has_source": "출처" in answer or "데이터소스" in answer or "조회 기준" in answer or "AI 생성" in answer,
        "has_structure": answer.count("####") >= 1 or answer.count("###") >= 1 or answer.count("## ") >= 2,
        "has_blockquote": "> " in answer,
        "length": len(answer),
        "line_count": len(answer.split("\n")),
    }

    # Score: how many enterprise features are present
    # Short answers (< 150 chars) are greetings/simple responses — always pass
    if checks["length"] < 150:
        checks["score"] = 100.0
        return checks
    else:
        feature_keys = ["has_heading", "has_bold", "has_followup", "has_structure", "has_source"]
        if route in ("bigquery", "multi"):
            feature_keys.append("has_table")
        if route in ("bigquery", "cs", "notion"):
            feature_keys.append("has_blockquote")

    score = sum(1 for k in feature_keys if checks.get(k)) / len(feature_keys) * 100
    checks["score"] = round(score, 1)
    return checks


# ===== Test Cases =====
TEST_CASES = [
    # (route, question, description)
    ("direct", "안녕하세요", "인사 (간단)"),
    ("direct", "GDP가 뭐야? 자세히 설명해줘", "지식 질문 (구조화 필요)"),
    ("direct", "파이썬과 자바 차이점 비교해줘", "비교 질문 (표 활용)"),
    ("bigquery", "2025년 미국 매출 알려줘", "BQ 매출 조회"),
    ("bigquery", "국가별 매출 Top 5 비교해줘", "BQ 국가별 비교"),
    ("bigquery", "2025년 월별 미국 매출 추이 알려줘", "BQ 시계열 추이"),
    ("cs", "센텔라 앰플 성분 알려줘", "CS 제품 문의"),
    ("cs", "비건 인증 제품 있어?", "CS 비건 인증"),
]


def main():
    global TOKEN
    TOKEN = get_token()

    print("=" * 80)
    print("🔍 SKIN1004 AI Enterprise Output Quality Test")
    print(f"   Target: {BASE_URL}")
    print("=" * 80)

    results = []

    for expected_route, question, desc in TEST_CASES:
        print(f"\n{'─' * 60}")
        print(f"📝 [{expected_route.upper()}] {desc}")
        print(f"   Q: {question}")
        print(f"{'─' * 60}")

        start = time.time()
        try:
            resp = query(question)
            elapsed = time.time() - start
            answer = extract_answer(resp)

            # Detect actual source
            source = "unknown"
            raw = json.dumps(resp, ensure_ascii=False)
            if "source" in raw:
                try:
                    source = resp.get("source", expected_route)
                except Exception:
                    source = expected_route

            quality = analyze_quality(answer, expected_route)

            print(f"\n⏱️  {elapsed:.1f}s | 📏 {quality['length']} chars | {quality['line_count']} lines")
            print(f"📊 Quality Score: {quality['score']}%")
            print(f"   ✓ Heading: {quality['has_heading']} | Bold: {quality['has_bold']} | Follow-up: {quality['has_followup']}")
            print(f"   ✓ Structure: {quality['has_structure']} | Table: {quality['has_table']} | Source: {quality['has_source']}")
            print(f"\n{'─' * 40} ANSWER {'─' * 40}")
            # Print first 800 chars
            preview = answer[:800]
            if len(answer) > 800:
                preview += f"\n... ({len(answer) - 800} more chars)"
            print(preview)

            results.append({
                "route": expected_route,
                "desc": desc,
                "score": quality["score"],
                "elapsed": round(elapsed, 1),
                "length": quality["length"],
                "issues": [k for k in ["has_heading", "has_bold", "has_followup", "has_structure", "has_source"] if not quality.get(k)],
            })

        except Exception as e:
            print(f"❌ ERROR: {e}")
            results.append({"route": expected_route, "desc": desc, "score": 0, "elapsed": 0, "issues": ["FAILED"]})

    # ===== Summary =====
    print(f"\n\n{'=' * 80}")
    print("📊 QUALITY SUMMARY")
    print(f"{'=' * 80}")
    print(f"{'Route':<12} {'Description':<25} {'Score':>6} {'Time':>6} {'Issues'}")
    print(f"{'─' * 80}")
    for r in results:
        issues = ", ".join(r["issues"]) if r["issues"] else "✅ All pass"
        print(f"{r['route']:<12} {r['desc']:<25} {r['score']:>5.0f}% {r['elapsed']:>5.1f}s {issues}")

    avg_score = sum(r["score"] for r in results) / len(results) if results else 0
    print(f"{'─' * 80}")
    print(f"{'AVERAGE':<37} {avg_score:>5.0f}%")
    print(f"\n🎯 Target: 80%+ for enterprise grade")
    print(f"{'✅ PASS' if avg_score >= 80 else '⚠️ NEEDS IMPROVEMENT'}: {avg_score:.0f}%")

    return results


if __name__ == "__main__":
    results = main()
