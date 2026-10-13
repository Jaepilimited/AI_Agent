"""AI Knowledge Base Editor — CLAUDE.md + Memory 파일 시각화/편집 API.

AI가 이 프로젝트를 이해하는 기반이 되는 MD 파일들을 읽고, 편집하고,
섹션 간 연결 그래프를 생성합니다.
"""

import re
from pathlib import Path
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["harness"])

# ── Allowed paths ───────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CLAUDE_MD = _PROJECT_ROOT / "CLAUDE.md"
_MEMORY_DIR = Path.home() / ".claude" / "projects" / "C--Users-DB-PC-Desktop-python-bcj-AI-Agent" / "memory"
_COMMANDS_DIR = _PROJECT_ROOT / ".claude" / "commands"
_SKILLS_DIR = _PROJECT_ROOT / ".claude" / "skills"

# Allowed directories for file operations
_ALLOWED_DIRS = [_MEMORY_DIR, _COMMANDS_DIR, _SKILLS_DIR]

def _allowed_paths() -> list[Path]:
    """Whitelist of MD files the user can edit."""
    paths = []
    if _CLAUDE_MD.exists():
        paths.append(_CLAUDE_MD)
    for d in _ALLOWED_DIRS:
        if d.exists():
            for f in sorted(d.glob("*.md")):
                paths.append(f)
    return paths

def _is_allowed(p: Path) -> bool:
    rp = p.resolve()
    if rp == _CLAUDE_MD.resolve():
        return True
    for d in _ALLOWED_DIRS:
        if d.exists() and rp.parent.resolve() == d.resolve() and rp.suffix == ".md":
            return True
    return False


# ── Page route ──────────────────────────────────────────────
@router.get("/harness")
async def harness_page():
    return FileResponse("app/static/ai_harness.html", media_type="text/html")


# ── File list ───────────────────────────────────────────────
@router.get("/api/harness/files")
async def list_files():
    """List all AI knowledge MD files with metadata."""
    files = []
    for p in _allowed_paths():
        content = p.read_text(encoding="utf-8")
        # Extract frontmatter type if present
        fm_type = ""
        fm_desc = ""
        fm_match = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if fm_match:
            fm = fm_match.group(1)
            t = re.search(r"type:\s*(.+)", fm)
            d = re.search(r"description:\s*(.+)", fm)
            if t: fm_type = t.group(1).strip()
            if d: fm_desc = d.group(1).strip()

        # Count sections
        headings = re.findall(r"^#{1,3}\s+.+", content, re.MULTILINE)

        # Determine type and relative path based on directory
        rp = p.resolve()
        if rp == _CLAUDE_MD.resolve():
            rel = p.name
            auto_type = "project"
        elif _COMMANDS_DIR.exists() and rp.parent.resolve() == _COMMANDS_DIR.resolve():
            rel = f"commands/{p.name}"
            auto_type = "command"
        elif _SKILLS_DIR.exists() and rp.parent.resolve() == _SKILLS_DIR.resolve():
            rel = f"skills/{p.name}"
            auto_type = "skill"
        elif p.name == "MEMORY.md":
            rel = f"memory/{p.name}"
            auto_type = "index"
        else:
            rel = f"memory/{p.name}"
            auto_type = "memory"

        files.append({
            "name": p.name,
            "path": str(p),
            "rel": rel,
            "size": p.stat().st_size,
            "lines": content.count("\n") + 1,
            "sections": len(headings),
            "type": fm_type or auto_type,
            "desc": fm_desc,
        })
    return files


# ── Read file ───────────────────────────────────────────────
@router.get("/api/harness/file")
async def read_file(path: str):
    """Read a specific MD file."""
    p = Path(path)
    if not _is_allowed(p) or not p.exists():
        raise HTTPException(404, "File not found or not allowed")
    content = p.read_text(encoding="utf-8")
    return {"path": str(p), "name": p.name, "content": content}


# ── Save file ──────────────────────────────────────────────
class SaveRequest(BaseModel):
    path: str
    content: str

@router.put("/api/harness/file")
async def save_file(req: SaveRequest):
    """Save changes to an MD file."""
    p = Path(req.path)
    if not _is_allowed(p):
        raise HTTPException(403, "Not allowed to write this file")
    p.write_text(req.content, encoding="utf-8")
    logger.info("harness_file_saved", file=p.name, size=len(req.content))
    return {"ok": True, "name": p.name, "size": len(req.content)}


# ── Create new memory file ─────────────────────────────────
class CreateRequest(BaseModel):
    name: str
    type: str = "feedback"
    description: str = ""
    content: str = ""

@router.post("/api/harness/file")
async def create_file(req: CreateRequest):
    """Create a new memory file."""
    fname = req.name if req.name.endswith(".md") else req.name + ".md"
    fname = re.sub(r"[^a-zA-Z0-9가-힣_\-.]", "_", fname)
    p = _MEMORY_DIR / fname
    if p.exists():
        raise HTTPException(409, "File already exists")

    body = f"""---
name: {req.name.replace('.md','')}
description: {req.description}
type: {req.type}
---

{req.content}
"""
    p.write_text(body.strip() + "\n", encoding="utf-8")
    logger.info("harness_file_created", file=p.name)
    return {"ok": True, "path": str(p), "name": p.name}


# ── Delete memory file ─────────────────────────────────────
@router.delete("/api/harness/file")
async def delete_file(path: str):
    """Delete a memory file (not CLAUDE.md or MEMORY.md)."""
    p = Path(path)
    if not _is_allowed(p):
        raise HTTPException(403, "Not allowed")
    if p.name in ("CLAUDE.md", "MEMORY.md"):
        raise HTTPException(403, "Cannot delete core files")
    if not p.exists():
        raise HTTPException(404, "File not found")
    p.unlink()
    logger.info("harness_file_deleted", file=p.name)
    return {"ok": True}


# ── Graph: sections + cross-references ─────────────────────
@router.get("/api/harness/graph")
async def get_graph():
    """Build a graph from MD headings and cross-references."""
    nodes = []
    edges = []
    node_id = 0

    for p in _allowed_paths():
        content = p.read_text(encoding="utf-8")
        file_label = p.name.replace(".md", "")

        # File node
        fid = f"file_{node_id}"
        ftype = "project" if p.name == "CLAUDE.md" else (
            "index" if p.name == "MEMORY.md" else "memory")
        nodes.append({
            "id": fid, "label": file_label,
            "group": ftype, "size": 35 if p.name in ("CLAUDE.md","MEMORY.md") else 25,
            "file": p.name,
        })
        node_id += 1

        # Section nodes
        prev_sid = fid
        for m in re.finditer(r"^(#{1,3})\s+(.+)", content, re.MULTILINE):
            level = len(m.group(1))
            title = m.group(2).strip()
            sid = f"sec_{node_id}"
            nodes.append({
                "id": sid, "label": title,
                "group": "section", "size": 18 - level * 2,
                "file": p.name, "level": level,
            })
            edges.append({"from": fid, "to": sid})
            node_id += 1

        # Cross-references: [text](file.md) links
        for m in re.finditer(r"\[([^\]]+)\]\(([^)]+\.md)\)", content):
            link_text = m.group(1)
            link_target = m.group(2)
            # Find target file node
            for n in nodes:
                if n.get("file") == link_target and n["group"] != "section":
                    edges.append({"from": fid, "to": n["id"], "label": link_text})
                    break

    return {"nodes": nodes, "edges": edges}
