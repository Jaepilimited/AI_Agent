import os
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

notion = Client(auth=os.environ["NOTION_TOKEN"])
DATABASE_ID = "2a82b4283b008038bdadec375adda1e6"

# 1. 토큰 연결 테스트 - 내 정보 조회
print("=== 1. 토큰 연결 테스트 ===")
try:
    me = notion.users.me()
    print(f"연결 성공! Bot: {me.get('name', 'Unknown')}")
except Exception as e:
    print(f"연결 실패: {e}")

# 2. 접근 가능한 데이터베이스 목록 조회
print("\n=== 2. 접근 가능한 데이터베이스 목록 ===")
try:
    results = notion.search(filter={"property": "object", "value": "data_source"})
    if results["results"]:
        for db in results["results"]:
            title = db.get("title", [])
            title_text = title[0]["plain_text"] if title else "Untitled"
            db_id = db["id"]
            print(f"  - {title_text}")
            print(f"    ID: {db_id}")
            print(f"    ID(하이픈제거): {db_id.replace('-', '')}")
    else:
        print("  접근 가능한 데이터베이스가 없습니다.")
except Exception as e:
    print(f"조회 실패: {e}")

# 3. 해당 DB의 페이지 확인
print(f"\n=== 3. 페이지 검색 결과 (설정된 DB: {DATABASE_ID}) ===")
try:
    results = notion.search(filter={"property": "object", "value": "page"})
    print(f"총 {len(results['results'])}개 페이지 발견")

    for page in results["results"][:5]:  # 처음 5개만 출력
        parent = page.get("parent", {})
        parent_type = parent.get("type")
        title = "Untitled"
        for prop in page.get("properties", {}).values():
            if prop.get("type") == "title":
                title_list = prop.get("title", [])
                if title_list:
                    title = title_list[0]["plain_text"]
                    break

        print(f"  - {title}")
        print(f"    parent_type: {parent_type}")
        if parent_type == "database_id":
            parent_db = parent.get("database_id", "").replace("-", "")
            print(f"    parent_db: {parent_db}")
            print(f"    매칭여부: {parent_db == DATABASE_ID}")
except Exception as e:
    print(f"조회 실패: {e}")
