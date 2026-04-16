"""Sample module for AST parser tests."""
from __future__ import annotations
import json
from pathlib import Path

from app.core.llm import get_flash_client


def top_level_func(x: int) -> int:
    """Double x."""
    return x * 2


class SampleAgent:
    """Sample agent class."""

    def __init__(self) -> None:
        self.client = get_flash_client()

    def run(self, query: str) -> str:
        return f"processed: {query}"


async def async_func() -> None:
    """Async function."""
    pass
