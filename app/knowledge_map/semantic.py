"""Gemini Flash semantic pass — pulls concepts/relations/summary per file."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.knowledge_map.config import (
    FLASH_BACKOFF_BASE,
    FLASH_MAX_RETRIES,
    FLASH_PARALLEL,
    PROJECT_ROOT,
)


_PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "prompts" / "knowledge_map" / "extract_concepts.txt"
_WIKI_PROMPT_PATH = PROJECT_ROOT / "prompts" / "knowledge_map" / "synthesize_wiki.txt"
_REPORT_PROMPT_PATH = PROJECT_ROOT / "prompts" / "knowledge_map" / "synthesize_report.txt"
_MAX_CONTENT_CHARS = 8000


@dataclass
class SemanticFacts:
    summary: str
    concepts: list[str] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    parse_error: Optional[str] = None


def _load_prompt_template() -> str:
    return _PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")


def _build_prompt(file_path: Path, file_type: str, structural_facts: dict[str, Any], content: str) -> str:
    template = _load_prompt_template()
    truncated = content[:_MAX_CONTENT_CHARS]
    return (
        template
        .replace("{file_path}", str(file_path))
        .replace("{file_type}", file_type)
        .replace("{structural_facts}", json.dumps(structural_facts, ensure_ascii=False, indent=2))
        .replace("{content}", truncated)
    )


async def _flash_json_call(prompt: str) -> str:
    """Call Gemini Flash and return raw text. Isolated for test mocking."""
    from app.core.llm import get_flash_client
    client = get_flash_client()
    return await asyncio.to_thread(client.generate, prompt)


def _parse_response(raw: str) -> SemanticFacts:
    """Tolerant JSON parse — strips code fences, returns error node on failure."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        return SemanticFacts(summary="", parse_error=f"json: {e}")
    return SemanticFacts(
        summary=str(payload.get("summary", "")),
        concepts=list(payload.get("concepts", [])),
        relations=list(payload.get("relations", [])),
        tags=list(payload.get("tags", [])),
    )


async def extract_semantic_facts(
    file_path: Path,
    file_type: str,
    structural_facts: dict[str, Any],
) -> SemanticFacts:
    """Run the Flash semantic pass for a single file with retries."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return SemanticFacts(summary="", parse_error=f"read: {e}")

    prompt = _build_prompt(file_path, file_type, structural_facts, content)

    last_err: Optional[str] = None
    for attempt in range(FLASH_MAX_RETRIES):
        try:
            raw = await _flash_json_call(prompt)
            return _parse_response(raw)
        except Exception as e:
            last_err = f"attempt_{attempt}: {e}"
            await asyncio.sleep(FLASH_BACKOFF_BASE ** attempt)
    return SemanticFacts(summary="", parse_error=last_err)


async def extract_semantic_facts_batch(
    items: list[tuple[Path, str, dict[str, Any]]],
) -> list[SemanticFacts]:
    """Parallel batch with FLASH_PARALLEL fanout via asyncio.Semaphore."""
    sem = asyncio.Semaphore(FLASH_PARALLEL)

    async def _bounded(path: Path, ftype: str, facts: dict[str, Any]) -> SemanticFacts:
        async with sem:
            return await extract_semantic_facts(path, ftype, facts)

    return await asyncio.gather(*(_bounded(p, t, f) for p, t, f in items))


# ── Cluster wiki & report synthesis ──────────────────────────────────


async def synthesize_cluster_wiki(
    cluster_name: str,
    file_summaries: list[dict[str, str]],
    cross_cluster_relations: list[str],
    date: str,
) -> str:
    """Call Flash to write a cluster wiki page. Returns raw Markdown."""
    template = _WIKI_PROMPT_PATH.read_text(encoding="utf-8")
    cluster_title = cluster_name.replace("_", " ").title()
    file_count = len(file_summaries)
    summaries_text = "\n".join(
        f"- `{item['file']}`: {item['summary']}" for item in file_summaries
    ) or "(no file summaries)"
    relations_text = (
        "\n".join(cross_cluster_relations[:20]) or "No cross-cluster relations detected."
    )
    prompt = (
        template
        .replace("{cluster_name}", cluster_name)
        .replace("{cluster_title}", cluster_title)
        .replace("{date}", date)
        .replace("{file_count}", str(file_count))
        .replace("{file_summaries}", summaries_text)
        .replace("{cross_cluster_relations}", relations_text)
    )
    last_err: Optional[str] = None
    for attempt in range(FLASH_MAX_RETRIES):
        try:
            return await _flash_json_call(prompt)
        except Exception as e:
            last_err = f"attempt_{attempt}: {e}"
            await asyncio.sleep(FLASH_BACKOFF_BASE ** attempt)
    return f"# {cluster_name}\n\n> Auto-generated {date}\n\n*Synthesis failed: {last_err}*\n"


async def synthesize_cluster_wiki_batch(
    items: list[tuple[str, list[dict[str, str]], list[str], str]],
) -> list[tuple[str, str]]:
    """Batch cluster wiki synthesis with FLASH_PARALLEL fanout.

    Each item: (cluster_name, file_summaries, cross_cluster_relations, date)
    Returns list of (cluster_name, markdown_body).
    """
    sem = asyncio.Semaphore(FLASH_PARALLEL)

    async def _bounded(
        cluster_name: str,
        file_summaries: list[dict[str, str]],
        relations: list[str],
        date: str,
    ) -> tuple[str, str]:
        async with sem:
            body = await synthesize_cluster_wiki(cluster_name, file_summaries, relations, date)
            return (cluster_name, body)

    return list(await asyncio.gather(*(_bounded(c, fs, r, d) for c, fs, r, d in items)))


async def synthesize_graph_report(inputs_json: str) -> str:
    """Call Flash to write GRAPH_REPORT.md. Returns raw Markdown."""
    template = _REPORT_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = template.replace("{inputs_json}", inputs_json)
    last_err: Optional[str] = None
    for attempt in range(FLASH_MAX_RETRIES):
        try:
            return await _flash_json_call(prompt)
        except Exception as e:
            last_err = f"attempt_{attempt}: {e}"
            await asyncio.sleep(FLASH_BACKOFF_BASE ** attempt)
    return f"# GRAPH_REPORT\n\n*Synthesis failed: {last_err}*\n"
