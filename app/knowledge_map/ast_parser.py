"""Python AST parser — extracts classes, functions, imports (confidence 1.0)."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FunctionNode:
    name: str
    line_start: int
    line_end: int
    docstring: Optional[str]
    is_async: bool
    args: list[str] = field(default_factory=list)


@dataclass
class ClassNode:
    name: str
    line_start: int
    line_end: int
    docstring: Optional[str]
    methods: list[FunctionNode] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)


@dataclass
class PythonNode:
    """All AST-extracted facts for a single .py file."""
    path: Path
    module_doc: Optional[str]
    imports: list[str] = field(default_factory=list)
    classes: list[ClassNode] = field(default_factory=list)
    functions: list[FunctionNode] = field(default_factory=list)
    parse_error: Optional[str] = None


def _func_from_node(node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionNode:
    return FunctionNode(
        name=node.name,
        line_start=node.lineno,
        line_end=node.end_lineno or node.lineno,
        docstring=ast.get_docstring(node),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        args=[a.arg for a in node.args.args],
    )


def _import_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    # ImportFrom
    mod = node.module or ""
    return [f"{mod}.{alias.name}" if mod else alias.name for alias in node.names]


def parse_python_file(path: Path) -> Optional[PythonNode]:
    """Parse a .py file and return structured AST facts. None on fatal error."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return PythonNode(path=path, module_doc=None, parse_error=f"read: {e}")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        return PythonNode(path=path, module_doc=None, parse_error=f"syntax: {e}")

    result = PythonNode(path=path, module_doc=ast.get_docstring(tree))

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            result.imports.extend(_import_names(node))
        elif isinstance(node, ast.ClassDef):
            cls = ClassNode(
                name=node.name,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                docstring=ast.get_docstring(node),
                bases=[ast.unparse(b) for b in node.bases],
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cls.methods.append(_func_from_node(child))
            result.classes.append(cls)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.functions.append(_func_from_node(node))

    return result
