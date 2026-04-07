"""
Chunking 결과 모델
"""

from dataclasses import dataclass


@dataclass
class Chunk:
    """단일 chunk 데이터"""
    text: str
    section_path: str   # "H1 > H2 > H3" 형태
    chunk_index: int
    page_title: str
    breadcrumb: str     # "팀명 > 페이지제목"
    page_url: str
    page_id: str
