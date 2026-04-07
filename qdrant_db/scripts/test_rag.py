"""
RAG 파이프라인 대화형 테스트

실행: python scripts/test_rag.py
종료: quit / q / exit
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.rag.retrieve import retrieve
from app.rag.answer import generate_answer


def main():
    print("=" * 60)
    print("Notion RAG 테스트 챗봇")
    print("종료: quit / q / exit")
    print("=" * 60)
    print()

    while True:
        query = input("질문: ").strip()

        if query.lower() in ["quit", "exit", "q"]:
            print("종료합니다.")
            break

        if not query:
            continue

        # 검색
        results = retrieve(query=query)

        if not results:
            print("\n관련 문서를 찾지 못했습니다.\n")
            continue

        # LLM 답변
        print("\n답변 생성 중...")
        result = generate_answer(query=query, results=results)

        print(f"\n{'='*60}")
        print("답변:")
        print(result["answer"])

        print(f"\n참고 문서:")
        for s in result["sources"]:
            team = s.get("team", "")
            title = s.get("page_title", "")
            url = s.get("page_url", "")
            print(f"  - [{team}] {title}" + (f"  {url}" if url else ""))
        print()


if __name__ == "__main__":
    main()
