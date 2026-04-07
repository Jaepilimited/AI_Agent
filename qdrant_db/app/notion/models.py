"""
Notion 도메인 모델
"""

from dataclasses import dataclass, field


@dataclass
class DiscoveredPage:
    """Hub discovery 결과 - 수집 대상 페이지"""
    page_id: str
    team: str           # 토글 텍스트에서 추출한 팀 이름
    hub_id: str
    # 인라인 콘텐츠 (토글 안에 페이지 링크 없이 텍스트만 있는 경우)
    is_inline: bool = False
    inline_title: str = ""      # 토글 텍스트가 제목
    inline_markdown: str = ""   # children 블록들의 텍스트
    # 공개 notion.site 페이지 (API 접근 불가, Playwright 스크래핑)
    is_public: bool = False
    public_url: str = ""        # 스크래핑 대상 URL


@dataclass
class PageMetadata:
    """Notion 페이지 메타데이터"""
    page_id: str
    title: str
    url: str
    last_edited_time: str
    breadcrumb: list[str] = field(default_factory=list)

    @property
    def breadcrumb_path(self) -> str:
        return " > ".join(self.breadcrumb)
