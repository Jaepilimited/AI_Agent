"""Shared Dashboard-tab link catalog and deterministic chat answers."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any


_CATALOG_PATH = Path(__file__).parents[1] / "static" / "dashboard-catalog.json"
_LINK_INTENT_RE = re.compile(
    r"(?:링크|url|주소|접속|바로가기|열어\s*줘|열어줘|오픈|"
    r"어디\s*(?:서|에|야|있어|있나요)|어디서\s*봐|보는\s*곳|띄워\s*줘)",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_QUERY_STOPWORDS = {
    "링크", "url", "주소", "접속", "바로가기", "열어", "열어줘", "오픈",
    "어디", "어디서", "어디에", "어디야", "있어", "있나요", "봐", "보는",
    "곳", "띄워줘", "보여줘", "알려", "알려줘", "주세요", "줘", "좀", "부탁",
}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return " ".join(_TOKEN_RE.findall(value))


@lru_cache(maxsize=1)
def load_dashboard_catalog() -> dict[str, Any]:
    """Load the same static JSON rendered by the Dashboard tab."""
    with _CATALOG_PATH.open(encoding="utf-8") as catalog_file:
        data = json.load(catalog_file)
    if not isinstance(data, dict):
        raise ValueError("dashboard catalog root must be an object")
    return data


@lru_cache(maxsize=1)
def iter_dashboard_links() -> tuple[dict[str, str], ...]:
    """Return normalized flat entries while retaining display order."""
    links: list[dict[str, str]] = []
    for category, sections in load_dashboard_catalog().items():
        if not isinstance(sections, dict):
            continue
        for section, items in sections.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("t") or "").strip()
                url = str(item.get("u") or "").strip()
                if not title or not url:
                    continue
                links.append({
                    "category": str(category),
                    "section": str(section),
                    "title": title,
                    "description": str(item.get("d") or "").strip(),
                    "url": url,
                    "type": str(item.get("type") or "web"),
                    "icon": str(item.get("icon") or ""),
                })
    return tuple(links)


def find_dashboard_links(query: str, limit: int = 5) -> list[dict[str, str]]:
    """Rank Dashboard entries mentioned in a natural-language query."""
    if limit <= 0:
        return []

    query_norm = _normalize(query)
    if not query_norm:
        return []

    # A full displayed title is decisive. This prevents a specific request such as
    # "프로모션 캘린더 주소" from being diluted by other promotion-related links.
    exact = [
        link for link in iter_dashboard_links()
        if _normalize(link["title"]) in query_norm
    ]
    if exact:
        # If one displayed title contains another (for example
        # "SKIN1004 CS 대시보드" vs "CS 대시보드"), keep the more specific
        # title. Unrelated titles explicitly named together are still returned.
        maximal = [
            link for link in exact
            if not any(
                _normalize(link["title"]) != _normalize(other["title"])
                and _normalize(link["title"]) in _normalize(other["title"])
                for other in exact
            )
        ]
        return maximal[:limit]

    query_tokens = [
        token for token in _TOKEN_RE.findall(query_norm)
        if token not in _QUERY_STOPWORDS and len(token) >= 2
    ]
    if not query_tokens:
        return []

    ranked: list[tuple[float, int, dict[str, str]]] = []
    for order, link in enumerate(iter_dashboard_links()):
        title_norm = _normalize(link["title"])
        description_norm = _normalize(link["description"])
        haystack = " ".join((
            title_norm,
            description_norm,
            _normalize(link["category"]),
            _normalize(link["section"]),
        ))
        hits = [token for token in query_tokens if token in haystack]
        if not hits:
            continue
        title_hits = sum(token in title_norm for token in hits)
        score = (len(hits) / len(query_tokens)) * 100 + title_hits * 20
        if " ".join(query_tokens) in title_norm:
            score += 80
        ranked.append((score, -order, link))

    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [link for _, _, link in ranked[:limit]]


def answer_dashboard_link_query(query: str, limit: int = 5) -> str | None:
    """Return a clickable answer only when the user is explicitly asking for a link."""
    if not _LINK_INTENT_RE.search(query or ""):
        return None

    # 붙여 쓴 소스명도 실제 CS 화면으로 연결한다. 숫자와 링크를 함께 요청한
    # 질문은 조회 경로가 답과 같은 조건의 링크를 만들도록 남겨 둔다.
    if re.search(r"(?:국내|한국|해외|글로벌)\s*(?:자사몰\s*)?cs", query, re.I):
        if re.search(r"얼마|몇\s*건|집계|분석|비교|합계|건수|금액|환불액|보상액", query):
            return None
        from app.core.cs_metrics import DASHBOARD_URL
        links = []
        if re.search(r"국내|한국", query):
            path, title = "dashboard", "국내 CS 대시보드"
            for word, suffix, label in (("상품", "product", "상품별"), ("제품", "product", "상품별"),
                                         ("사유", "reason", "사유별"), ("채널", "channel", "판매처별"),
                                         ("판매처", "channel", "판매처별")):
                if word in query:
                    path, title = "reports/" + suffix, "국내 CS " + label + " 시각화"
                    break
            links.append(f"[{title}]({DASHBOARD_URL}{path})")
        if re.search(r"해외|글로벌", query):
            links.append(f"[해외 CS 대시보드]({DASHBOARD_URL}global/dashboard)")
        if links:
            return " · ".join(links) + "\n\n화면에서 기간과 조회 조건을 선택할 수 있습니다."

    matches = find_dashboard_links(query, limit=limit)
    if not matches:
        return None

    if len(matches) == 1:
        link = matches[0]
        lines = [
            f"**{link['title']}**",
            "",
            f"[{link['title']} 열기]({link['url']})",
        ]
        if link["description"]:
            lines.extend(("", link["description"]))
        lines.extend(("", "> 대시보드 탭에 등록된 주소입니다."))
        return "\n".join(lines)

    lines = ["대시보드 탭에서 관련 링크를 찾았습니다.", ""]
    for link in matches:
        suffix = f" — {link['description']}" if link["description"] else ""
        lines.append(f"- [{link['title']}]({link['url']}){suffix}")
    if len(matches) == limit:
        lines.extend(("", f"> 관련 항목이 더 있을 수 있어 상위 {limit}개만 표시했습니다."))
    return "\n".join(lines)
