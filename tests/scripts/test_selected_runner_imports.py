"""Guard the selected-test runner against half-applied constant removals.

History
-------
``scripts/_test_selection.py`` used to export ``LEGACY_FRONTEND``. Removing
the constant while the runner still used it broke the DoD gate twice, in two
different ways:

1. the runner raised ``NameError`` at the use site, so
   ``test_baseline.sh --scope auto|frontend|backend`` could not start at all;
2. the runner's own test module imported the same name, so the whole test
   file failed at collection — which meant the functional test that *would*
   have caught (1) never ran.

A functional test only covers the branches it exercises, and it cannot report
anything at all when its own module fails to import. These checks are
therefore static: they read the two modules' sources and prove that every
name the runner references is really bound, and that every name it imports
from the selector really exists there.

The tests below also mutate copies of the sources to prove the checks
actually turn red on the historical breakage — a guard that cannot fail is
not a guard.
"""

from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
RUNNER_PATH = PROJECT / "scripts" / "_run_selected_tests.py"
SELECTOR_PATH = PROJECT / "scripts" / "_test_selection.py"


def _undefined_names(source: str) -> set[str]:
    """Names the module reads but never binds (module scope or a function).

    ``symtable`` resolves every scope, so a name that is bound at module
    level, bound locally, or a builtin is excluded; what is left is exactly
    the "constant was deleted, the use site stayed" case that raises
    ``NameError`` at runtime.
    """
    table = symtable.symtable(source, "<runner>", "exec")
    module_bound = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported()
    }
    scopes = [table, *table.get_children()]
    undefined: set[str] = set()
    for scope in scopes:
        for symbol in scope.get_symbols():
            name = symbol.get_name()
            if not symbol.is_global() or symbol.is_assigned():
                continue
            if name in module_bound or hasattr(builtins, name):
                continue
            undefined.add(name)
    return undefined


def _names_from_selector(source: str) -> list[str]:
    """The names the runner imports from the selector module."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "_test_selection":
            return [alias.name for alias in node.names]
    return []


def _defined_in_selector(source: str) -> set[str]:
    """Every name the selector module binds at module level."""
    defined: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                defined.update(
                    child.id for child in ast.walk(target) if isinstance(child, ast.Name)
                )
        elif isinstance(node, ast.AnnAssign):
            defined.update(
                child.id
                for child in ast.walk(node.target)
                if isinstance(child, ast.Name)
            )
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])
    return defined


def test_runner_references_only_names_that_are_bound() -> None:
    """Every name the runner reads must be bound, imported, or a builtin."""
    undefined = _undefined_names(RUNNER_PATH.read_text(encoding="utf-8"))
    assert not undefined, f"runner references undefined names: {sorted(undefined)}"


def test_runner_imports_only_names_the_selector_defines() -> None:
    """An import the selector no longer provides must not reach the runner."""
    imported = _names_from_selector(RUNNER_PATH.read_text(encoding="utf-8"))
    defined = _defined_in_selector(SELECTOR_PATH.read_text(encoding="utf-8"))
    missing = sorted(name for name in imported if name not in defined)
    assert not missing, f"runner imports names the selector does not define: {missing}"


def test_guard_turns_red_when_a_constant_is_deleted_but_its_use_stays() -> None:
    """Destructive check: reproduce the historical breakage and expect red."""
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert "COMPONENTS" in source, "fixture drifted; pick another imported name"

    # Same shape as the incident: the import line goes away, the use site stays.
    broken = source.replace("    COMPONENTS,\n", "", 1)
    assert "COMPONENTS," not in broken.split("def ", 1)[0]
    assert "COMPONENTS" in broken, "the use site must survive to be undefined"

    assert "COMPONENTS" in _undefined_names(broken)


def test_import_guard_turns_red_when_the_selector_loses_a_name() -> None:
    """Destructive check: a selector that no longer defines an imported name."""
    imported = _names_from_selector(RUNNER_PATH.read_text(encoding="utf-8"))
    assert imported, "the runner is expected to import from the selector"

    selector_source = SELECTOR_PATH.read_text(encoding="utf-8")
    defined = _defined_in_selector(selector_source)
    victim = next(name for name in imported if name in defined)
    stripped = _defined_in_selector(
        "\n".join(
            line
            for line in selector_source.splitlines()
            if not line.strip().startswith(victim)
        )
    )
    assert victim not in stripped
