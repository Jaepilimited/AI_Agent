f"""
데이터 로드 CLI

사용법:
    python reload_data.py --list                 # 소스 목록 + 포인트 수 확인
    python reload_data.py --show DB-JP           # 특정 소스 데이터 조회
    python reload_data.py --source DB-JP         # 특정 소스만 수집
    python reload_data.py --new                  # 새 소스만 수집 (포인트 0인 것)
    python reload_data.py --all                  # 전체 소스 수집
    python reload_data.py --delete-source DB-JP  # 특정 소스 삭제
    python reload_data.py --dry-run              # 테스트 (저장 안 함)
"""

import argparse
import sys
import uuid
from pathlib import Path

# 상위 디렉토리를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import get_logger, settings, setup_logging
from notion import NotionCrawler
from embedding import SemanticChunker, BatchEmbedder
from vector_store import QdrantStore


setup_logging()
logger = get_logger(__name__)


# ============================================================
# 📌📌수집 대상 정의 (여기에 database_id와 source명 추가)📌📌
# ============================================================
DATABASE_TARGETS = [
    {
        "source": "DB-JP",
        "database_id": "d86180c9236541d6b154dcb4c4143f23",
        "description": "DB 재필님 개인 페이지"
    },
    {
        "source": "DB-tablet",
        "database_id": "2532b4283b0080eba96ce35ae8ba8743",
        "description": "DB 법인 태블릿 사용법"
    },
    {
        "source": "DB-SY",
        "database_id": "12f2b4283b0080cbaf9fe103d7c91490",
        "description": "DB 소영님 개인 페이지"
    },
    {
        "source": "DB-da-part",
        "database_id": "1602b4283b0080f186cfc6425d9a53dd",
        "description": "DB 데이터 분석 파트 페이지"
    },
    # {
    #     "source": "CR-main",
    #     "database_id": "de2b80b70522483fa5fcc1ec5aed1b7f",
    #     "description": "크레이버 메인 페이지"
    # },
    # EAST
    {
        "source": "EAST-guide-archive",
        "database_id": "2e62b4283b00803a8007df0d3003705c",
        "description": "EAST 2팀 가이드 아카이브 페이지"
    },
    {
        "source": "EAST-2026-work",
        "database_id": "2e12b4283b0080b48a1dd7bbbd6e0e53",
        "description": "EAST 2026 업무파악"
    },
    {
        "source": "EAST-tiktok-access",
        "database_id": "19d2b4283b0080dc89d9e6d9c11ec1e5",
        "description": "EAST 틱톡샵 접속 방법"
    },
    {
        "source": "EAST-travel-guide",
        "database_id": "1982b4283b008039ad79ec0c1c1e38fb",
        "description": "EAST 해외 출장 가이드북"
    },
    # WEST
    {
        "source": "WEST-tiktok-dashboard",
        "database_id": "22e2b4283b008060bac6cef042c3787b",
        "description": "WEST 틱톡샵US 대시보드"
    },
    # KBT
    {
        "source": "KBT-smartstore-guide",
        "database_id": "c058d9e89e8a4780b32e866b8248b5b1",
        "description": "KBT 스마트스토어 운영방법"
    },
    {
        "source": "KBT-smartstore-work",
        "database_id": "1fb2b4283b00802883faef2df97c6f73",
        "description": "KBT 네이버 스마트스토어 업무 공유"
    },
    {
        "source": "DB-ads-input",
        "database_id": "1dc2b4283b0080cb8790cf5218896ebd",
        "description": "Daily 광고 데이터 입력"
    },
    {
        "source": "GM-ads-meeting",
        "database_id": "3032b4283b00801188e1f65eb0d46fae",
        "description": "GM 광고 인사이트 미팅"
    },
    {
        "source": "B2B-guide",
        "database_id": "07d3489594fa4db6829d1fee397ecdf1",
        "description": "B2B 신규 입사자 안내"
    }

]
# ============================================================


def test_chunking():
    """청킹 품질 테스트"""
    print("=" * 50)
    print("청킹 테스트")
    print("=" * 50)

    crawler = NotionCrawler()
    chunker = SemanticChunker()

    print(f"\n루트 페이지: {settings.root_page_id}")
    print("첫 번째 페이지만 테스트...\n")

    pages = crawler.crawl(settings.root_page_id)

    if not pages:
        print("페이지를 찾을 수 없습니다.")
        return

    page = pages[0]
    print(f"페이지: {page.title}")
    print(f"경로: {page.breadcrumb_path}")
    print(f"섹션 수: {len(page.sections)}")
    print(f"전체 길이: {len(page.content)}자")

    chunks = chunker.chunk_page(page.content, page.sections)

    print(f"\n청크 수: {len(chunks)}")
    print(f"청크 크기 설정: {settings.chunk_size}자 (오버랩: {settings.chunk_overlap}자)")

    print("\n" + "-" * 50)
    for i, chunk in enumerate(chunks[:3]):  # 처음 3개만
        print(f"\n[청크 {i + 1}] 섹션: {chunk.section_title or '(없음)'}")
        print(f"길이: {len(chunk.text)}자")
        print(f"내용 미리보기:\n{chunk.text[:200]}...")
        print("-" * 50)


def dry_run():
    """드라이런 (저장 없이 테스트)"""
    print("=" * 50)
    print("드라이런 모드 (저장 안 함)")
    print("=" * 50)

    crawler = NotionCrawler()
    chunker = SemanticChunker()
    embedder = BatchEmbedder()

    print(f"\n1. Notion 크롤링 시작...")
    pages = crawler.crawl(settings.root_page_id)
    print(f"   → {len(pages)}개 페이지 수집")

    if not pages:
        print("수집된 페이지가 없습니다.")
        return

    print(f"\n2. 청킹...")
    all_chunks = []
    for page in pages:
        chunks = chunker.chunk_page(page.content, page.sections)
        for chunk in chunks:
            all_chunks.append({
                "page": page,
                "chunk": chunk
            })

    print(f"   → {len(all_chunks)}개 청크 생성")

    print(f"\n3. 임베딩 테스트 (처음 5개만)...")
    test_texts = [item["chunk"].text for item in all_chunks[:5]]
    test_titles = [item["page"].title for item in all_chunks[:5]]

    embeddings = embedder.embed_with_titles(test_texts, test_titles)
    print(f"   → {len(embeddings)}개 임베딩 생성")
    print(f"   → 벡터 차원: {len(embeddings[0])}")

    print(f"\n예상 API 호출 횟수:")
    total_batches = (len(all_chunks) + settings.embedding_batch_size - 1) // settings.embedding_batch_size
    print(f"   → 배치 크기: {settings.embedding_batch_size}")
    print(f"   → 총 배치 수: {total_batches}")


def get_target_by_source(source_name: str) -> dict | None:
    """source명으로 타겟 정보 조회"""
    for target in DATABASE_TARGETS:
        if target["source"].upper() == source_name.upper():
            return target
    return None


def list_sources():
    """등록된 소스 목록 출력"""
    print("=" * 50)
    print("등록된 수집 대상 목록")
    print("=" * 50)

    if not DATABASE_TARGETS:
        print("\n등록된 소스가 없습니다.")
        print("DATABASE_TARGETS 리스트에 추가하세요.")
        return

    store = QdrantStore()
    store.ensure_collection()

    print(f"\n{'source':<10} {'database_id':<36} {'현재 포인트':<12} 설명")
    print("-" * 80)

    for target in DATABASE_TARGETS:
        count = store.count_by_source(target["source"])
        print(f"{target['source']:<10} {target['database_id']:<36} {count:<12} {target.get('description', '')}")

    print()


def show_source(source_name: str):
    """특정 소스 데이터 조회"""
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    store = QdrantStore()

    try:
        store.ensure_collection()
    except Exception as e:
        print(f"[ERROR] 컬렉션 접근 실패: {e}")
        return

    count = store.count_by_source(source_name)

    if count == 0:
        print(f"source '{source_name}'에 해당하는 데이터가 없습니다.")
        return

    print("=" * 70)
    print(f"source: {source_name} ({count}개 포인트)")
    print("=" * 70)

    # 데이터 조회
    results = store._client.scroll(
        collection_name=store._collection_name,
        scroll_filter=Filter(must=[FieldCondition(key="source", match=MatchValue(value=source_name))]),
        limit=count,
        with_payload=True
    )

    for i, point in enumerate(results[0], 1):
        payload = point.payload
        print(f"\n[{i}] {payload.get('page_title', '(제목 없음)')}")
        print(f"    섹션: {payload.get('section_title', '-')}")
        print(f"    경로: {payload.get('breadcrumb_path', '-')}")
        print(f"    내용: {payload.get('text_preview', '-')[:80]}...")
        if payload.get('url'):
            print(f"    URL: {payload.get('url')}")

    print()


def delete_source(source_name: str):
    """특정 소스 데이터 삭제"""
    store = QdrantStore()

    # 컬렉션 존재 확인
    try:
        store.ensure_collection()
    except Exception as e:
        print(f"[ERROR] 컬렉션 접근 실패: {e}")
        return

    # 현재 포인트 수 확인
    count = store.count_by_source(source_name)

    if count == 0:
        print(f"source '{source_name}'에 해당하는 데이터가 없습니다.")
        return

    # 삭제 확인
    print(f"source '{source_name}' 데이터 {count}개를 삭제합니다.")
    confirm = input("계속하시겠습니까? (y/N): ").strip().lower()

    if confirm != 'y':
        print("취소되었습니다.")
        return

    # 삭제 실행
    store.delete_by_source(source_name)
    print(f"삭제 완료. (삭제된 포인트: {count}개)")


def reload_source(source_name: str):
    """특정 소스만 수집 (기존 데이터 삭제 후 재수집)"""
    target = get_target_by_source(source_name)

    if not target:
        print(f"[ERROR] source '{source_name}'를 찾을 수 없습니다.")
        print("등록된 소스: " + ", ".join([t["source"] for t in DATABASE_TARGETS]))
        return

    source = target["source"]
    database_id = target["database_id"]

    print("=" * 50)
    print(f"소스 '{source}' 데이터 수집")
    print(f"Database ID: {database_id}")
    print("=" * 50)

    crawler = NotionCrawler()
    chunker = SemanticChunker()
    embedder = BatchEmbedder()
    store = QdrantStore()

    # 0. 컬렉션 확인/생성
    store.ensure_collection()

    # 1. 기존 데이터 삭제
    print(f"\n1. 기존 '{source}' 데이터 삭제...")
    old_count = store.count_by_source(source)
    if old_count > 0:
        store.delete_by_source(source)
        print(f"   -> {old_count}개 포인트 삭제")
    else:
        print(f"   -> 기존 데이터 없음")

    # 2. 크롤링
    print(f"\n2. Notion 크롤링 시작...")
    pages = crawler.crawl(database_id)
    print(f"\n총 {len(pages)}개 페이지 수집 완료")

    if not pages:
        print("수집된 페이지가 없습니다.")
        return

    # 3. 청킹
    print(f"\n3. 텍스트 청킹...")
    all_items = []

    for page in pages:
        chunks = chunker.chunk_page(page.content, page.sections)
        for chunk in chunks:
            all_items.append((page, chunk))
        print(f"  {page.title}: {len(chunks)}개 청크")

    print(f"\n총 {len(all_items)}개 청크 생성")

    # 4. 임베딩
    print(f"\n4. 임베딩 생성...")
    texts = [item[1].text for item in all_items]
    titles = [item[0].title for item in all_items]

    vectors = embedder.embed_with_titles(texts, titles)
    print(f"   -> {len(vectors)}개 임베딩 완료")

    # 5. 저장 (source 필드 포함)
    print(f"\n5. Qdrant 저장...")

    points = []
    point_ids = []
    for i, ((page, chunk), text) in enumerate(zip(all_items, texts)):
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{source}:{page.id}:{chunk.chunk_index}"))
        point_ids.append(point_id)
        points.append({
            "source": source,
            "database_id": database_id,
            "page_id": page.id,
            "page_title": page.title,
            "section_title": chunk.section_title,
            "breadcrumb_path": page.breadcrumb_path,
            "text": text,
            "text_preview": text[:200],
            "url": page.url,
            "chunk_index": chunk.chunk_index
        })

    count = store.upsert_points_with_ids(points, vectors, point_ids)

    # 6. 완료
    new_count = store.count_by_source(source)
    info = store.get_collection_info()
    print(f"\n{'=' * 50}")
    print("완료!")
    print(f"  소스: {source}")
    print(f"  추가된 포인트: {new_count}")
    print(f"  전체 포인트: {info['points_count']}")
    print(f"  대시보드: http://localhost:6333/dashboard")
    print(f"{'=' * 50}")


def reload_all():
    """모든 소스 순차 수집"""
    print("=" * 50)
    print("전체 소스 순차 수집")
    print(f"대상: {len(DATABASE_TARGETS)}개 소스")
    print("=" * 50)

    if not DATABASE_TARGETS:
        print("\n등록된 소스가 없습니다.")
        return

    for i, target in enumerate(DATABASE_TARGETS, 1):
        print(f"\n[{i}/{len(DATABASE_TARGETS)}] {target['source']} 수집 시작...")
        reload_source(target["source"])

    print("\n" + "=" * 50)
    print("전체 수집 완료!")
    print("=" * 50)


def reload_new():
    """새 소스만 수집 (포인트 수가 0인 소스만)"""
    print("=" * 50)
    print("새 소스만 수집 (기존 데이터 있는 소스는 건너뜀)")
    print("=" * 50)

    if not DATABASE_TARGETS:
        print("\n등록된 소스가 없습니다.")
        return

    store = QdrantStore()
    store.ensure_collection()

    # 새 소스 필터링
    new_targets = []
    for target in DATABASE_TARGETS:
        count = store.count_by_source(target["source"])
        if count == 0:
            new_targets.append(target)
        else:
            print(f"  [건너뜀] {target['source']} ({count}개 포인트 존재)")

    if not new_targets:
        print("\n수집할 새 소스가 없습니다.")
        return

    print(f"\n수집 대상: {len(new_targets)}개 소스")
    print("-" * 40)

    for i, target in enumerate(new_targets, 1):
        print(f"\n[{i}/{len(new_targets)}] {target['source']} 수집 시작...")
        reload_source(target["source"])

    print("\n" + "=" * 50)
    print(f"새 소스 {len(new_targets)}개 수집 완료!")
    print("=" * 50)


def quick_test():
    """빠른 테스트 (처음 3개 페이지만)"""
    print("=" * 50)
    print("빠른 테스트 (3페이지 제한, DB 건너뜀)")
    print("=" * 50)

    crawler = NotionCrawler(max_pages=3, skip_databases=True)
    chunker = SemanticChunker()
    embedder = BatchEmbedder()
    store = QdrantStore(collection_name="notion_quick_test")

    print(f"\n1. Notion 크롤링...")
    pages = crawler.crawl(settings.root_page_id)
    print(f"   -> {len(pages)}개 페이지")

    print(f"\n2. 청킹...")
    all_items = []
    for page in pages:
        chunks = chunker.chunk_page(page.content, page.sections)
        for chunk in chunks:
            all_items.append((page, chunk))
        print(f"  {page.title}: {len(chunks)}개 청크")

    print(f"\n3. 임베딩...")
    texts = [item[1].text for item in all_items]
    titles = [item[0].title for item in all_items]
    vectors = embedder.embed_with_titles(texts, titles)

    print(f"\n4. Qdrant 저장...")
    store.create_collection(recreate=True)
    points = []
    for (page, chunk), text in zip(all_items, texts):
        points.append({
            "page_id": page.id,
            "page_title": page.title,
            "section_title": chunk.section_title,
            "breadcrumb_path": page.breadcrumb_path,
            "text": text,
            "text_preview": text[:200],
            "url": page.url,
            "chunk_index": chunk.chunk_index
        })
    store.upsert_points(points, vectors)

    print(f"\n5. 검색 테스트...")
    query = "데이터"
    query_vec = embedder.embed_single(query)
    results = store.search(query_vec, top_k=3, score_threshold=0.3)
    print(f"   쿼리: '{query}'")
    print(f"   결과: {len(results)}건")
    for r in results:
        print(f"     - {r.page_title} ({r.score:.2f})")

    print("\n완료!")


def main():
    parser = argparse.ArgumentParser(description="Notion RAG 데이터 로드")
    parser.add_argument("--source", type=str, help="특정 소스만 수집 (예: DB-JP)")
    parser.add_argument("--all", action="store_true", help="전체 소스 순차 수집")
    parser.add_argument("--new", action="store_true", help="새 소스만 수집 (포인트 0인 소스)")
    parser.add_argument("--list", action="store_true", help="등록된 소스 목록")
    parser.add_argument("--show", type=str, help="특정 소스 데이터 조회 (예: DB-JP)")
    parser.add_argument("--delete-source", type=str, help="특정 소스 데이터 삭제 (예: DB-JP)")
    parser.add_argument("--dry-run", action="store_true", help="테스트 (저장 안 함)")
    parser.add_argument("--test-chunking", action="store_true", help="청킹만 테스트")
    parser.add_argument("--quick", action="store_true", help="빠른 테스트 (3페이지)")

    args = parser.parse_args()

    if args.list:
        list_sources()
    elif args.show:
        show_source(args.show)
    elif args.delete_source:
        delete_source(args.delete_source)
    elif args.source:
        reload_source(args.source)
    elif args.new:
        reload_new()
    elif args.all:
        reload_all()
    elif args.test_chunking:
        test_chunking()
    elif args.dry_run:
        dry_run()
    elif args.quick:
        quick_test()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
