"""
Notion RAG 시스템 - 재귀적 페이지 크롤링 + 벡터 임베딩

참고: https://techblog.ahnlabcloudmate.com/notion-rag-geomsaeg-siseutem-gucug-goro-guhyeonhaneun-gaein-jisig-beiseu-gucuggi/

사용법:
    python notion_rag.py --reload    # 데이터 로드 및 임베딩
    python notion_rag.py             # 검색 모드
"""

import os
import argparse
import time
from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError, HTTPResponseError
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct

load_dotenv()

# ========== 설정 ==========
ROOT_PAGE_ID = "00f9408d25c747948cacdc997e482b62"  # 최상위 페이지 ID (하위 페이지 자동 상속)
COLLECTION_NAME = "notion_dbteam_data"
CHUNK_SIZE = 1000  # 청킹 단위 (자)
EMBEDDING_MODEL = "text-embedding-3-small"  # OpenAI 임베딩 모델
EMBEDDING_DIM = 1536  # text-embedding-3-small 차원

# ========== 클라이언트 초기화 ==========
notion = Client(auth=os.environ["NOTION_TOKEN"])
qdrant = QdrantClient(host="localhost", port=6333)
openai_client = OpenAI()  # OPENAI_API_KEY 환경변수 자동 사용


def get_embedding(text: str) -> list:
    """OpenAI 임베딩 생성"""
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text
    )
    return response.data[0].embedding


def format_uuid(id_str: str) -> str:
    """32자리 ID를 UUID 형식으로 변환"""
    id_str = id_str.replace("-", "")
    if len(id_str) != 32:
        return id_str
    return f"{id_str[:8]}-{id_str[8:12]}-{id_str[12:16]}-{id_str[16:20]}-{id_str[20:]}"


def retry_request(func, max_retries=3, delay=2):
    """API 에러 시 재시도"""
    for attempt in range(max_retries):
        try:
            return func()
        except HTTPResponseError as e:
            if e.status in [502, 503, 504, 429] and attempt < max_retries - 1:
                wait_time = delay * (attempt + 1)
                print(f"    서버 에러 {e.status}, {wait_time}초 후 재시도...")
                time.sleep(wait_time)
            else:
                raise


def get_block_text(block: dict) -> str:
    """블록에서 텍스트 추출"""
    block_type = block.get("type", "")

    text_block_types = [
        "paragraph", "heading_1", "heading_2", "heading_3",
        "bulleted_list_item", "numbered_list_item",
        "quote", "callout", "toggle", "to_do"
    ]

    if block_type in text_block_types:
        rich_text = block.get(block_type, {}).get("rich_text", [])
        return "".join([t.get("plain_text", "") for t in rich_text])

    return ""


def get_page_content(page_id: str) -> str:
    """페이지의 모든 블록 내용 가져오기"""
    texts = []
    cursor = None
    formatted_id = format_uuid(page_id)

    while True:
        params = {"block_id": formatted_id}
        if cursor:
            params["start_cursor"] = cursor

        try:
            response = retry_request(
                lambda: notion.blocks.children.list(**params)
            )
        except Exception as e:
            print(f"    블록 조회 실패: {e}")
            break

        for block in response.get("results", []):
            text = get_block_text(block)
            if text:
                texts.append(text)

            # 하위 블록이 있으면 재귀적으로 가져오기
            if block.get("has_children"):
                child_text = get_page_content(block["id"])
                if child_text:
                    texts.append(child_text)

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return "\n".join(texts)


def get_page_title(page: dict) -> str:
    """페이지 제목 추출"""
    # properties에서 title 찾기
    properties = page.get("properties", {})
    for prop in properties.values():
        if prop.get("type") == "title":
            title_list = prop.get("title", [])
            if title_list:
                return title_list[0].get("plain_text", "Untitled")

    # child_page 블록인 경우
    if page.get("type") == "child_page":
        return page.get("child_page", {}).get("title", "Untitled")

    return "Untitled"


def crawl_pages_recursive(page_id: str, depth: int = 0) -> list:
    """
    페이지와 하위 페이지를 재귀적으로 크롤링

    Returns:
        list of dict: [{"id", "title", "content", "url", "depth"}, ...]
    """
    pages = []
    formatted_id = format_uuid(page_id)
    indent = "  " * depth

    # 1. 현재 페이지 정보 가져오기
    try:
        page = retry_request(lambda: notion.pages.retrieve(page_id=formatted_id))
        title = get_page_title(page)
        url = page.get("url", "")
        print(f"{indent}📄 {title}")
    except Exception as e:
        print(f"{indent}페이지 조회 실패 ({page_id}): {e}")
        return pages

    # 2. 페이지 내용 가져오기
    content = get_page_content(formatted_id)

    if content.strip():
        pages.append({
            "id": page_id,
            "title": title,
            "content": content,
            "url": url,
            "depth": depth
        })

    # 3. 하위 블록에서 child_page, child_database 찾기
    cursor = None
    while True:
        params = {"block_id": formatted_id}
        if cursor:
            params["start_cursor"] = cursor

        try:
            response = retry_request(
                lambda: notion.blocks.children.list(**params)
            )
        except Exception as e:
            print(f"{indent}  하위 블록 조회 실패: {e}")
            break

        for block in response.get("results", []):
            block_type = block.get("type")

            # 하위 페이지 발견 -> 재귀 크롤링
            if block_type == "child_page":
                child_pages = crawl_pages_recursive(block["id"], depth + 1)
                pages.extend(child_pages)

            # 하위 데이터베이스 발견 -> DB 내 항목들 크롤링
            elif block_type == "child_database":
                db_id = block["id"]
                db_title = block.get("child_database", {}).get("title", "Untitled DB")
                print(f"{indent}  📊 [DB] {db_title}")

                db_pages = crawl_database_items(db_id, depth + 1)
                pages.extend(db_pages)

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return pages


def crawl_database_items(database_id: str, depth: int = 0) -> list:
    """데이터베이스 내 모든 항목(페이지) 크롤링"""
    pages = []
    formatted_id = format_uuid(database_id)
    cursor = None
    indent = "  " * depth

    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        try:
            response = retry_request(
                lambda: notion.request(
                    path=f"databases/{formatted_id}/query",
                    method="POST",
                    body=body
                )
            )
        except Exception as e:
            print(f"{indent}DB 쿼리 실패: {e}")
            break

        for item in response.get("results", []):
            # DB 항목도 페이지이므로 재귀 크롤링
            child_pages = crawl_pages_recursive(item["id"], depth)
            pages.extend(child_pages)

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return pages


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list:
    """텍스트를 chunk_size 단위로 분할"""
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def create_collection():
    """Qdrant 컬렉션 생성"""
    collections = [c.name for c in qdrant.get_collections().collections]

    if COLLECTION_NAME in collections:
        qdrant.delete_collection(collection_name=COLLECTION_NAME)
        print(f"기존 컬렉션 '{COLLECTION_NAME}' 삭제")

    qdrant.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )
    print(f"컬렉션 '{COLLECTION_NAME}' 생성 완료")


def reload_data():
    """Notion 데이터 로드 및 임베딩"""
    if not ROOT_PAGE_ID:
        print("ROOT_PAGE_ID를 설정해주세요.")
        print("노션 페이지 URL에서 ID 복사: https://notion.so/페이지제목-{PAGE_ID}")
        return

    print(f"\n{'='*50}")
    print("1. Notion 페이지 크롤링 시작")
    print(f"{'='*50}")

    pages = crawl_pages_recursive(ROOT_PAGE_ID)
    print(f"\n총 {len(pages)}개 페이지 수집 완료")

    if not pages:
        print("수집된 페이지가 없습니다.")
        return

    print(f"\n{'='*50}")
    print("2. 텍스트 청킹 및 임베딩")
    print(f"{'='*50}")

    create_collection()

    points = []
    point_id = 0

    for page in pages:
        chunks = chunk_text(page["content"])

        for i, chunk in enumerate(chunks):
            # 제목 + 청크 결합
            text_to_embed = f"{page['title']}\n{chunk}"
            vector = get_embedding(text_to_embed)

            points.append(PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "page_id": page["id"],
                    "title": page["title"],
                    "chunk_index": i,
                    "text": chunk[:500],  # 미리보기용
                    "full_text": chunk,
                    "url": page["url"]
                }
            ))
            point_id += 1

        print(f"  {page['title']}: {len(chunks)}개 청크")

    if points:
        # 배치로 upsert (100개씩)
        batch_size = 100
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            qdrant.upsert(collection_name=COLLECTION_NAME, points=batch)

        print(f"\n총 {len(points)}개 벡터 저장 완료!")
        print(f"대시보드: http://localhost:6333/dashboard")


def search(query: str, top_k: int = 5):
    """벡터 검색"""
    query_vector = get_embedding(query)

    results = qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        limit=top_k,
        score_threshold=0.3  # 유사도 임계값
    )

    return results


def interactive_search():
    """대화형 검색 모드"""
    print(f"\n{'='*50}")
    print("Notion RAG 검색 모드")
    print("종료하려면 'quit' 입력")
    print(f"{'='*50}\n")

    while True:
        query = input("질문: ").strip()

        if query.lower() in ["quit", "exit", "q"]:
            print("종료합니다.")
            break

        if not query:
            continue

        results = search(query)

        if not results:
            print("관련 문서를 찾지 못했습니다.\n")
            continue

        print(f"\n📚 검색 결과 (상위 {len(results)}개):\n")

        for i, result in enumerate(results, 1):
            payload = result.payload
            print(f"[{i}] {payload['title']} (유사도: {result.score:.3f})")
            print(f"    {payload['text'][:100]}...")
            print(f"    URL: {payload['url']}\n")


def main():
    parser = argparse.ArgumentParser(description="Notion RAG 시스템")
    parser.add_argument("--reload", action="store_true", help="데이터 다시 로드")
    args = parser.parse_args()

    if args.reload:
        reload_data()
    else:
        interactive_search()


if __name__ == "__main__":
    main()
