"""
의미 기반 청킹 - LangChain RecursiveCharacterTextSplitter
"""

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings


@dataclass
class Chunk:
    """청크 데이터"""
    text: str
    section_title: str | None
    chunk_index: int


class SemanticChunker:
    """의미 기반 텍스트 청킹"""

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None
    ):
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size or settings.chunk_size,
            chunk_overlap=chunk_overlap or settings.chunk_overlap,
            separators=["\n\n", "\n", ". ", ", ", " ", ""],
            length_function=len
        )

    def chunk_page(
        self,
        content: str,
        sections: list[dict] = None
    ) -> list[Chunk]:
        """
        페이지 내용을 청크로 분할

        Args:
            content: 전체 페이지 내용
            sections: 섹션 목록 [{"title": "...", "content": "..."}, ...]

        Returns:
            Chunk 리스트
        """
        chunks = []
        chunk_index = 0

        # 섹션이 있으면 섹션별로 청킹
        if sections:
            for section in sections:
                section_chunks = self._chunk_section(
                    section.get("content", ""),
                    section.get("title"),
                    chunk_index
                )
                chunks.extend(section_chunks)
                chunk_index += len(section_chunks)
        else:
            # 섹션 없으면 전체 내용 청킹
            section_chunks = self._chunk_section(content, None, 0)
            chunks.extend(section_chunks)

        return chunks

    def _chunk_section(
        self,
        content: str,
        section_title: str | None,
        start_index: int
    ) -> list[Chunk]:
        """단일 섹션 청킹"""
        if not content.strip():
            return []

        texts = self._splitter.split_text(content)

        return [
            Chunk(
                text=text,
                section_title=section_title,
                chunk_index=start_index + i
            )
            for i, text in enumerate(texts)
        ]

    def chunk_text(self, text: str) -> list[str]:
        """단순 텍스트 청킹 (호환성용)"""
        if not text.strip():
            return []
        return self._splitter.split_text(text)
