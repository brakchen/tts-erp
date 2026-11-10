"""The undefined-name gate must actually catch an undefined name.

A gate that cannot go red is worse than no gate: it reports "checked"
while the defect walks through. These tests pin both directions — a
planted undefined name is found, and a correctly bound one is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from check_undefined_names import undefined_names  # noqa: E402


def test_planted_undefined_name_is_reported() -> None:
    source = (
        "def handler(payload):\n"
        "    return render_html(payload)\n"
    )
    found = undefined_names(source, "planted.py")
    assert found == [(2, "render_html")]


def test_bound_name_is_not_reported() -> None:
    source = (
        "from markupsafe import Markup\n"
        "\n"
        "def handler(payload):\n"
        "    return Markup(payload)\n"
    )
    assert undefined_names(source, "bound.py") == []


def test_local_assignment_counts_as_a_binding() -> None:
    source = (
        "def handler(payload):\n"
        "    helper = payload\n"
        "    return helper\n"
    )
    assert undefined_names(source, "local.py") == []


def test_annotation_only_name_is_reported() -> None:
    """A name used only in an annotation is still a missing binding.

    ``from __future__ import annotations`` keeps the annotation from
    raising at import time, which is exactly why these defects survive
    until someone calls the function.
    """
    source = (
        "from __future__ import annotations\n"
        "\n"
        "def handler() -> MissingType:\n"
        "    return None\n"
    )
    found = undefined_names(source, "annotation.py")
    assert found == [(3, "MissingType")]


def test_implicit_names_are_never_reported() -> None:
    source = "PATH = __file__\nNAME = __name__\n"
    assert undefined_names(source, "implicit.py") == []


def test_repository_tree_has_no_undefined_names() -> None:
    """The whole ``tts_erp_v2`` tree must be clean.

    This is the check that would have caught the retired
    ``_TrustedHtml`` reference in ``api/v2/pages.py``.
    """
    from check_undefined_names import scan

    root = Path(__file__).resolve().parents[2] / "tts_erp_v2"
    issues = scan(root)
    rendered = "\n".join(f"{path}:{line}: {name}" for path, line, name in issues)
    assert not issues, f"发现未定义名:\n{rendered}"


@pytest.mark.parametrize(
    "marker",
    ["__file__", "__name__"],
)
def test_script_runs_clean_on_the_repository(marker: str) -> None:
    """The CLI entry point must report success on the current tree."""
    import subprocess

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "check_undefined_names.py"), "tts_erp_v2"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
