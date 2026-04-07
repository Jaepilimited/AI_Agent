"""
Heading-aware markdown chunker

H1/H2/H3 경계를 기준으로 section을 나누고,
큰 section은 RecursiveCharacterTextSplitter로 추가 분할.
"""

import re
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings
from app.chunking.models import Chunk


# 토큰 ≈ 글자 수 / 1.5 (한국어 기준 근사값)
_CHARS_PER_TOKEN = 1.5


def _tokens_to_chars(tokens: int) -> int:
    return int(tokens * _CHARS_PER_TOKEN)


@dataclass
class _Section:
    path: str   # "제목1 > 제목2" 형태
    content: str


def _split_by_headings(markdown: str) -> list[_Section]:
    """
    H1/H2/H3 기준으로 섹션 분리.
    각 섹션의 path는 현재까지의 heading 경로를 추적.
    """
    heading_re = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

    sections: list[_Section] = []
    # 헤딩 없는 앞부분 처리
    first_match = heading_re.search(markdown)
    if first_match and first_match.start() > 0:
        preamble = markdown[: first_match.start()].strip()
        if preamble:
            sections.append(_Section(path="", content=preamble))

    current_h = {1: "", 2: "", 3: ""}
    current_content_start = None
    current_path = ""

    matches = list(heading_re.finditer(markdown))

    for i, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()

        # 이전 섹션 content 저장
        if current_content_start is not None:
            content = markdown[current_content_start : match.start()].strip()
            if content:
                sections.append(_Section(path=current_path, content=content))

        # heading 경로 업데이트
        current_h[level] = title
        # 하위 heading 초기화
        for l in range(level + 1, 4):
            current_h[l] = ""

        parts = [current_h[l] for l in (1, 2, 3) if current_h[l]]
        current_path = " > ".join(parts)
        current_content_start = match.end()

    # 마지막 섹션
    if current_content_start is not None:
        content = markdown[current_content_start:].strip()
        if content:
            sections.append(_Section(path=current_path, content=content))

    # heading만 있고 content가 없는 경우에도 heading text를 content로 추가
    if not sections and markdown.strip():
        sections.append(_Section(path="", content=markdown.strip()))

    return sections


def chunk_markdown(
    markdown: str,
    page_id: str,
    page_title: str,
    breadcrumb: str,
    page_url: str,
) -> list[Chunk]:
    """
    markdown을 heading-aware chunk로 분할.

    Args:
        markdown: 정규화된 페이지 markdown
        page_id: Notion page_id
        page_title: 페이지 제목
        breadcrumb: "팀명 > 페이지제목" 경로
        page_url: 페이지 URL

    Returns:
        Chunk 리스트
    """
    if not markdown.strip():
        return []

    target_chars = _tokens_to_chars(settings.chunk_target_tokens)
    overlap_chars = _tokens_to_chars(settings.chunk_overlap_tokens)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=target_chars,
        chunk_overlap=overlap_chars,
        separators=["\n\n", "\n", ". ", ", ", " ", ""],
        length_function=len,
    )

    sections = _split_by_headings(markdown)
    chunks: list[Chunk] = []
    chunk_index = 0

    for section in sections:
        if not section.content.strip():
            continue

        # section이 target_chars 이하면 그대로 1개 chunk
        if len(section.content) <= target_chars:
            chunks.append(
                Chunk(
                    text=section.content,
                    section_path=section.path,
                    chunk_index=chunk_index,
                    page_title=page_title,
                    breadcrumb=breadcrumb,
                    page_url=page_url,
                    page_id=page_id,
                )
            )
            chunk_index += 1
        else:
            # 큰 section은 추가 분할
            pieces = splitter.split_text(section.content)
            for piece in pieces:
                if not piece.strip():
                    continue
                chunks.append(
                    Chunk(
                        text=piece,
                        section_path=section.path,
                        chunk_index=chunk_index,
                        page_title=page_title,
                        breadcrumb=breadcrumb,
                        page_url=page_url,
                        page_id=page_id,
                    )
                )
                chunk_index += 1

    return chunks
