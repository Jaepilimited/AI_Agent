"""
notion.site 공개 페이지 Playwright 스크래퍼

- 토글은 기본 collapsed 상태 → JS로 전체 클릭 후 innerText 추출
- page_id는 URL 내 32자리 hex에서 추출
"""

import re
import hashlib
from playwright.sync_api import sync_playwright

from app.core.logging import logger


# notion.site URL에서 page_id 추출 (32자리 hex)
_PUBLIC_PAGE_ID_RE = re.compile(r"([0-9a-f]{32})", re.IGNORECASE)

# 모든 토글 버튼을 JS로 일괄 클릭하는 스크립트
_EXPAND_TOGGLES_JS = """
    const buttons = document.querySelectorAll('.notion-toggle-block [role="button"]');
    let count = 0;
    buttons.forEach(btn => {
        if (btn.getAttribute('aria-expanded') === 'false') {
            btn.click();
            count++;
        }
    });
    count;
"""

# 페이지 내용 추출 JS (notion-page-content innerText)
_EXTRACT_CONTENT_JS = """
    const el = document.querySelector('.notion-page-content');
    el ? el.innerText : document.body.innerText;
"""


def extract_public_page_id(url: str) -> str:
    """URL에서 Notion page_id 추출. 없으면 URL 해시 사용."""
    clean = url.replace("-", "")
    m = _PUBLIC_PAGE_ID_RE.search(clean)
    if m:
        raw = m.group(1)
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    # fallback: URL MD5
    return "public_" + hashlib.md5(url.encode()).hexdigest()[:16]


def is_notion_public_url(url: str) -> bool:
    return "notion.site" in url.lower()


def scrape_notion_public_page(url: str) -> dict:
    """
    notion.site 공개 페이지 스크래핑.

    Returns:
        {"title": str, "text": str, "url": str}
        text가 비어있으면 수집 실패.
    """
    logger.info(f"공개 페이지 스크래핑 시작: {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # 메인 컨텐츠 로드 대기
            try:
                page.wait_for_selector(".notion-page-content", timeout=15000)
            except Exception:
                logger.warning(f"notion-page-content 로드 타임아웃: {url}")

            # 스크롤 → lazy loading 트리거
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)

            # 토글 전체 펼치기
            clicked = page.evaluate(_EXPAND_TOGGLES_JS)
            if clicked:
                logger.debug(f"토글 {clicked}개 펼침")
                page.wait_for_timeout(2000)

            # 제목 + 본문 추출
            title = page.title().strip()
            raw_text = page.evaluate(_EXTRACT_CONTENT_JS) or ""
            text = _clean_text(raw_text)

        except Exception as e:
            logger.error(f"스크래핑 실패 ({url}): {e}")
            browser.close()
            return {"title": "", "text": "", "url": url}

        finally:
            browser.close()

    logger.info(f"스크래핑 완료: '{title}' ({len(text)}자)")
    return {"title": title, "text": text, "url": url}


def _clean_text(text: str) -> str:
    """탭·과도한 공백 정리"""
    text = re.sub(r"\t+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 불필요한 Notion UI 문구 제거
    text = re.sub(r"^Skip to content\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Get Notion free\n?", "", text, flags=re.MULTILINE)
    return text.strip()
