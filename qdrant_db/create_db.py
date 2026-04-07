from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from config import get_logger, setup_logging


setup_logging()
logger = get_logger(__name__)


client = QdrantClient(host="localhost", port=6333)
model = SentenceTransformer("all-MiniLM-L6-v2")
collection_name = "test_hwibin"

client.recreate_collection(
    collection_name=collection_name,
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
)

docs = [
    {"text": "벡터 db 생성", "meta": {"tag": "skin1004", "year": 2023}},
    {"text": "노션 데이터 벡터 임베딩해볼 예정", "meta": {"tag": "db", "year": 2024}},
    {"text": "오늘은 26년 2월 5일", "meta": {"tag": "food", "year": 2024}},
]

points = []
for index, doc in enumerate(docs):
    vector = model.encode(doc["text"]).tolist()
    points.append(
        {
            "id": index,
            "vector": vector,
            "payload": doc,
        }
    )

client.upsert(collection_name=collection_name, points=points)
logger.info("Sample vector collection populated | collection=%s points=%s", collection_name, len(points))
print("벡터 컬렉션 데이터 저장 완료!")
