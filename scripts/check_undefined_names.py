#!/usr/bin/env python3
"""Catch names that no binding provides — a text search cannot.

A deleted import leaves its name behind in the source, so ``grep`` still
finds it and every review reads as "defined". Only the syntax tree knows
that nothing binds it any more.

That is how ``tts_erp_v2/api/v2/pages.py`` kept referring to
``_TrustedHtml`` after the Jinja renderer was retired: the module still
imported fine (the annotation is never evaluated at runtime) and only a
direct call raised ``NameError``. This check turns that class of defect
into a red build.

Usage::

    python scripts/check_undefined_names.py [ROOT ...]

Exit codes: ``0`` clean, ``1`` undefined names found, ``2`` the tree
could not be parsed at all.
"""

from __future__ import annotations

import ast
import builtins
import pathlib
import sys

BUILTINS = frozenset(dir(builtins))

#: Names that are provided by the interpreter, not by a binding.
IMPLICIT = frozenset({"__file__", "__name__", "__doc__", "__package__", "__spec__",
                      "__loader__", "__builtins__", "__annotations__", "__debug__"})


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name this module binds, at any scope."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


def undefined_names(source: str, filename: str) -> list[tuple[int, str]]:
    """Names the module reads but never binds."""
    tree = ast.parse(source, filename=filename)
    bound = _bound_names(tree) | BUILTINS | IMPLICIT
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in bound and node.id not in found:
                found[node.id] = node.lineno
    return sorted((line, name) for name, line in found.items())


def scan(root: pathlib.Path) -> list[tuple[str, int, str]]:
    issues: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            for line, name in undefined_names(source, str(path)):
                issues.append((str(path), line, name))
        except SyntaxError as exc:
            issues.append((str(path), exc.lineno or 0, f"SyntaxError: {exc.msg}"))
    return issues


def main(argv: list[str]) -> int:
    roots = [pathlib.Path(arg) for arg in argv[1:]] or [pathlib.Path("tts_erp_v2")]
    issues: list[tuple[str, int, str]] = []
    for root in roots:
        if not root.exists():
            print(f"[undefined-names] path not found: {root}")
            return 2
        issues.extend(scan(root))
    if not issues:
        print("[undefined-names] OK — no undefined names")
        return 0
    for path, line, name in issues:
        print(f"{path}:{line}: undefined name {name!r}")
    print(f"[undefined-names] {len(issues)} undefined name(s) found")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
