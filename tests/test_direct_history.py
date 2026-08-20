"""Regression tests for direct-route conversation history."""

from app.agents.orchestrator import (
    _DIRECT_HISTORY_CHAR_BUDGET,
    _clean_messages_for_history,
    _content_to_text,
)


def test_short_multiturn_history_keeps_the_first_turn() -> None:
    """Many short turns must not evict useful early context by message count."""
    secret = "BLUE-LANTERN-4729"
    messages = [
        {"role": "user", "content": f"Remember this code: {secret}"},
        {"role": "assistant", "content": "I will remember it."},
    ]
    for index in range(15):
        messages.extend(
            [
                {"role": "user", "content": f"Distractor question {index}"},
                {"role": "assistant", "content": f"Distractor answer {index}"},
            ]
        )
    messages.append({"role": "user", "content": "What code did I give you first?"})

    cleaned = _clean_messages_for_history(messages)

    assert len(cleaned) == len(messages)
    assert any(secret in str(message["content"]) for message in cleaned)


def test_oversized_history_is_bounded_but_keeps_anchors() -> None:
    """A huge session keeps the opening context and latest request within budget."""
    first_context = "ORIGINAL-CONTEXT-9137"
    latest_question = "LATEST-QUESTION-2468"
    messages = [
        {"role": "user", "content": f"Opening context: {first_context}"},
        {"role": "assistant", "content": "Opening context acknowledged."},
    ]
    for index in range(80):
        messages.extend(
            [
                {"role": "user", "content": f"Question {index}: " + ("u" * 2_000)},
                {"role": "assistant", "content": f"Answer {index}: " + ("a" * 2_000)},
            ]
        )
    messages.append({"role": "user", "content": latest_question})

    cleaned = _clean_messages_for_history(messages)
    total_chars = sum(len(_content_to_text(message["content"])) for message in cleaned)

    assert total_chars <= _DIRECT_HISTORY_CHAR_BUDGET
    assert first_context in _content_to_text(cleaned[0]["content"])
    assert latest_question in _content_to_text(cleaned[-1]["content"])
