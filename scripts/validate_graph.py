"""Validate knowledge_map/graph.json — schema, integrity, dangling references.

Exit code 0 = healthy, 1 = issues found.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
GRAPH_JSON = _ROOT / "knowledge_map" / "graph.json"

_REQUIRED_TOP = {"version", "generated_at", "source_commit", "stats", "nodes", "edges"}
_REQUIRED_NODE = {"id", "type"}
_REQUIRED_EDGE = {"from", "to", "type", "confidence"}
_VALID_EDGE_TYPES = {"calls", "imports", "references", "supersedes", "implements", "documented_in", "related_to"}


def validate() -> int:
    if not GRAPH_JSON.exists():
        print(f"graph.json does not exist at {GRAPH_JSON}. Run build first.")
        return 1
    try:
        data = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"graph.json is not valid JSON: {e}")
        return 1

    errors: list[str] = []
    warnings: list[str] = []

    missing = _REQUIRED_TOP - set(data.keys())
    if missing:
        errors.append(f"missing top-level keys: {missing}")

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    node_ids = {n.get("id") for n in nodes}

    for n in nodes:
        missing = _REQUIRED_NODE - set(n.keys())
        if missing:
            errors.append(f"node {n.get('id', '?')} missing {missing}")

    for e in edges:
        missing = _REQUIRED_EDGE - set(e.keys())
        if missing:
            errors.append(f"edge {e} missing {missing}")
            continue
        if e["type"] not in _VALID_EDGE_TYPES:
            errors.append(f"edge {e['from']}->{e['to']} has unknown type: {e['type']}")
        if e["from"] not in node_ids:
            warnings.append(f"dangling src: {e['from']} (edge {e['from']}->{e['to']})")

    clusters = {n.get("cluster") for n in nodes if n.get("cluster")}
    if not clusters:
        warnings.append("no clusters assigned — did compute_clusters() run?")

    print(f"Nodes:    {len(nodes)}")
    print(f"Edges:    {len(edges)}")
    print(f"Clusters: {len(clusters)}")
    print(f"Errors:   {len(errors)}")
    print(f"Warnings: {len(warnings)}")

    for err in errors[:20]:
        print(f"  ERR: {err}")
    for w in warnings[:20]:
        print(f"  WARN: {w}")

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(validate())
