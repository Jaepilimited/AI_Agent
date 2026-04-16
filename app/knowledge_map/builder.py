"""Knowledge Map build orchestrator — discover → cache → parse → flash → graph → export."""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from app.knowledge_map.ast_parser import parse_python_file, PythonNode
from app.knowledge_map.cache import FileCache, file_fingerprint
from app.knowledge_map.config import (
    CACHE_FILE,
    EXCLUDE_FRAGMENTS,
    GRAPH_JSON,
    INCLUDE_EXTENSIONS,
    REPORT_MD,
    SOURCE_ROOTS,
    WIKI_DIR,
    WIKI_INDEX,
    WIKI_LOG,
)
from app.knowledge_map.exporters import (
    append_wiki_log,
    write_graph_json,
    write_graph_report,
    write_wiki_index,
)
from app.knowledge_map.graph import Edge, KnowledgeGraph, Node
from app.knowledge_map.md_parser import parse_markdown_file, MarkdownNode
from app.knowledge_map.semantic import SemanticFacts, extract_semantic_facts_batch

logger = structlog.get_logger(__name__)


def _is_excluded(path: Path) -> bool:
    s = str(path).replace("\\", "/")
    return any(frag in s for frag in EXCLUDE_FRAGMENTS)


def discover_source_files() -> list[Path]:
    """Walk SOURCE_ROOTS, filter by extension and exclude patterns."""
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in INCLUDE_EXTENSIONS:
                continue
            if _is_excluded(p):
                continue
            files.append(p)
    return sorted(files)


def _current_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _python_facts_to_nodes(py: PythonNode) -> tuple[list[Node], list[Edge]]:
    """Convert AST result → confidence-1.0 nodes and edges."""
    nodes: list[Node] = []
    edges: list[Edge] = []
    file_id = str(py.path).replace("\\", "/")
    nodes.append(Node(
        id=file_id,
        type="file",
        file=file_id,
        summary=py.module_doc or "",
        confidence=1.0,
    ))
    for cls in py.classes:
        cid = f"{file_id}::{cls.name}"
        nodes.append(Node(
            id=cid,
            type="class",
            file=file_id,
            lines=[cls.line_start, cls.line_end],
            summary=cls.docstring or "",
            confidence=1.0,
        ))
        edges.append(Edge(src=file_id, dst=cid, type="documented_in", confidence=1.0))
    for fn in py.functions:
        fid = f"{file_id}::{fn.name}"
        nodes.append(Node(
            id=fid,
            type="function",
            file=file_id,
            lines=[fn.line_start, fn.line_end],
            summary=fn.docstring or "",
            confidence=1.0,
        ))
        edges.append(Edge(src=file_id, dst=fid, type="documented_in", confidence=1.0))
    for imp in py.imports:
        edges.append(Edge(src=file_id, dst=imp, type="imports", confidence=1.0))
    return nodes, edges


def _md_facts_to_nodes(md: MarkdownNode) -> tuple[list[Node], list[Edge]]:
    nodes: list[Node] = []
    edges: list[Edge] = []
    file_id = str(md.path).replace("\\", "/")
    nodes.append(Node(
        id=file_id,
        type="doc",
        file=file_id,
        summary=md.title or "",
        tags=[md.filename_date] if md.filename_date else [],
        confidence=1.0,
    ))
    for link in md.links:
        if not link.target.startswith("http"):
            edges.append(Edge(src=file_id, dst=link.target, type="references", confidence=0.7))
    return nodes, edges


def _merge_semantic_into_graph(
    graph: KnowledgeGraph,
    file_id: str,
    facts: SemanticFacts,
) -> None:
    if facts.parse_error:
        return
    node = graph.get_node(file_id)
    if not node.summary and facts.summary:
        node.summary = facts.summary
    if facts.tags:
        node.tags = list(set(node.tags + facts.tags))
    for concept in facts.concepts:
        cid = f"concept:{concept}"
        if cid not in {n.id for n in graph.nodes()}:
            graph.add_node(Node(id=cid, type="concept", summary=concept, confidence=0.8))
        graph.add_edge(Edge(src=file_id, dst=cid, type="implements", confidence=0.8))
    for rel in facts.relations:
        target = rel.get("target")
        rtype = rel.get("type", "related_to")
        conf = float(rel.get("confidence", 0.6))
        if target:
            graph.add_edge(Edge(src=file_id, dst=str(target), type=str(rtype), confidence=conf))


async def build(
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the full build pipeline. Returns a stats dict."""
    started = datetime.now()
    logger.info("knowledge_map.build.start", force=force, dry_run=dry_run)

    files = discover_source_files()
    logger.info("knowledge_map.discovered", count=len(files))

    cache = FileCache(CACHE_FILE)
    previous = {} if force else cache.load()
    changed = [f for f in files if cache.is_changed(f, previous)]
    logger.info("knowledge_map.changed", count=len(changed), total=len(files))

    if dry_run:
        return {
            "files": len(files),
            "changed": len(changed),
            "estimated_flash_calls": len(changed),
            "duration_sec": (datetime.now() - started).total_seconds(),
        }

    graph = KnowledgeGraph()
    semantic_items: list[tuple[Path, str, dict[str, Any]]] = []

    for f in files:
        if f.suffix == ".py":
            py = parse_python_file(f)
            if py is None:
                continue
            nodes, edges = _python_facts_to_nodes(py)
            for n in nodes:
                graph.add_node(n)
            for e in edges:
                graph.add_edge(e)
            if f in changed:
                semantic_items.append((f, "python", {"classes": [c.name for c in py.classes], "functions": [fn.name for fn in py.functions]}))
        elif f.suffix == ".md":
            md = parse_markdown_file(f)
            nodes, edges = _md_facts_to_nodes(md)
            for n in nodes:
                graph.add_node(n)
            for e in edges:
                graph.add_edge(e)
            if f in changed:
                semantic_items.append((f, "markdown", {"title": md.title, "headings": [h.text for h in md.headings[:10]]}))

    flash_calls = 0
    if semantic_items:
        logger.info("knowledge_map.flash.start", count=len(semantic_items))
        results = await extract_semantic_facts_batch(semantic_items)
        for (path, _, _), facts in zip(semantic_items, results):
            file_id = str(path).replace("\\", "/")
            _merge_semantic_into_graph(graph, file_id, facts)
        flash_calls = len(semantic_items)

    graph.compute_clusters()

    commit = _current_commit()
    write_graph_json(
        graph,
        GRAPH_JSON,
        commit=commit,
        file_count=len(files),
        extra_stats={
            "flash_calls": flash_calls,
            "cache_hits": len(files) - len(changed),
            "build_duration_sec": round((datetime.now() - started).total_seconds(), 2),
        },
    )
    write_wiki_index(graph, WIKI_INDEX)

    report_body = (
        f"# SKIN1004 AI Agent — Knowledge Map\n"
        f"**Generated**: {datetime.now().astimezone().isoformat()} · "
        f"**Files**: {len(files)} · **Nodes**: {len(graph.nodes())} · "
        f"**Edges**: {len(graph.edges())} · **Commit**: {commit}\n\n"
        f"## Clusters\n"
    )
    for cluster, cnt in sorted(graph.cluster_counts().items()):
        report_body += f"- **{cluster}** — {cnt} nodes\n"
    report_body += "\n## God Nodes\n"
    for n in graph.god_nodes(top_n=8):
        report_body += f"- `{n.id}` ({n.type}) — {n.summary[:80] or 'no summary'}\n"
    report_body += "\n## How to navigate\nRead this file first, then open graph.json and follow wiki_page fields. Never Grep without consulting this map.\n"
    write_graph_report(report_body, REPORT_MD)

    new_cache = {str(f): file_fingerprint(f) for f in files}
    cache.save(new_cache)

    append_wiki_log(WIKI_LOG, f"build complete · files={len(files)} changed={len(changed)} flash={flash_calls}")

    stats = {
        "files": len(files),
        "changed": len(changed),
        "nodes": len(graph.nodes()),
        "edges": len(graph.edges()),
        "clusters": len(graph.cluster_counts()),
        "flash_calls": flash_calls,
        "duration_sec": round((datetime.now() - started).total_seconds(), 2),
    }
    logger.info("knowledge_map.build.done", **stats)
    return stats
