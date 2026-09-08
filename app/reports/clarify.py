# -*- coding: utf-8 -*-
"""보고서 생성 전 최소 확인 질문.

보고서는 한 번 만들 때 여러 조회를 실행하므로, 사용자의 첫 문장만 보고 곧바로 만들지
않는다. 핵심 판단과 드릴다운 기준을 한 번 확인한 뒤 그 답을 원 요청에 붙여 기존
기간·필터 추출기와 플래너가 함께 읽게 한다.

별도 세션 테이블은 두지 않는다. 직전 assistant 메시지의 숨은 표식만 인정하므로 오래된
보고서 요청이 나중의 일반 대화를 가로채지 않는다.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Dict, List, Optional


_MARKER_RE = re.compile(
    r"<!--\s*report-clarification-v1:([A-Za-z0-9_-]{1,16000})\s*-->"
)
_CANCEL_RE = re.compile(
    r"\s*(?:일단\s*)?(?:보고서\s*)?(?:취소|그만|중단|보류|나중에)"
    r"(?:할게|할래|해줘|합니다|하겠습니다)?[.!\s]*$"
)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: List[str] = []
    for part in content:
        if isinstance(part, dict):
            value = part.get("text") or part.get("content")
            if value:
                parts.append(str(value))
        elif part:
            parts.append(str(part))
    return "\n".join(parts)


def _encode(question: str, explicit: bool) -> str:
    raw = json.dumps(
        {"v": 1, "question": question.strip()[:2000], "explicit": bool(explicit)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(token: str) -> Optional[Dict[str, Any]]:
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    question = data.get("question") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("v") != 1:
        return None
    if not isinstance(question, str) or not question.strip():
        return None
    return {"question": question.strip(), "explicit": bool(data.get("explicit"))}


def build_prompt(question: str, *, explicit: bool = False) -> str:
    """사용자에게 한 번에 답할 최소 두 질문과 대화 상태 표식을 만든다."""
    marker = _encode(question, explicit)
    return (
        "보고서 정확도를 높이기 위해 만들기 전에 두 가지만 확인할게요.\n\n"
        "1. 분석 대상·기간과 이 보고서로 최종 확인하려는 판단은 무엇인가요?\n"
        "2. 어느 기준으로 한 단계 더 나누고, 무엇과 비교할까요?\n\n"
        "두 질문에 한 번에 답해주세요. 예: `2026년 상반기 일본 B2C의 감소 원인 / "
        "채널별로 나눠 전년 동기와 비교`\n\n"
        f"<!-- report-clarification-v1:{marker} -->"
    )


def pending(messages: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """현재 사용자 메시지 바로 앞 assistant가 보고서 확인 질문을 했는지 본다."""
    if not messages:
        return None

    history = messages[:-1] if messages[-1].get("role") == "user" else messages
    for message in reversed(history):
        role = message.get("role")
        if role not in ("assistant", "model"):
            continue
        match = _MARKER_RE.search(_content_text(message.get("content", "")))
        return _decode(match.group(1)) if match else None
    return None


def is_cancelled(answer: str) -> bool:
    return bool(_CANCEL_RE.fullmatch(answer or ""))


def enrich(question: str, answer: str) -> str:
    """원 요청과 확인 답변을 한 질문으로 묶어 기존 결정론적 추출기에 넘긴다."""
    return (
        f"{question.strip()}\n\n"
        "[보고서 생성 전 사용자 확인]\n"
        f"{answer.strip()}"
    ).strip()
