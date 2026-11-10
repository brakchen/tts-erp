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
    """The production tree must be clean.

    This is the check that would have caught the retired
    ``_TrustedHtml`` reference in ``api/v2/pages.py``.
    """
    from check_undefined_names import scan

    root = Path(__file__).resolve().parents[2] / "tts_erp_v2"
    issues = scan(root)
    rendered = "\n".join(f"{path}:{line}: {name}" for path, line, name in issues)
    assert not issues, f"发现未定义名:\n{rendered}"


def test_test_tree_has_no_new_undefined_names() -> None:
    """The test tree may carry recorded findings, but no new ones.

    Skipped legacy modules still name things they never import; those are
    listed in ``docs/testing/undefined-names-baseline.txt``. A name that
    moves or appears for the first time is a new finding and must be
    recorded deliberately, not inherited.
    """
    from check_undefined_names import _render, scan

    root = Path(__file__).resolve().parents[2]
    baseline_path = root / "docs" / "testing" / "undefined-names-baseline.txt"
    known = {
        line.strip()
        for line in baseline_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert known, "基线文件不应为空 —— 空基线说明记录没写上"

    issues = scan(root / "tests")
    new_findings = sorted(_render(item) for item in issues if _render(item) not in known)
    assert not new_findings, (
        "测试树出现新的未定义名（要么修掉，要么在基线文件里显式记录）:\n"
        + "\n".join(new_findings)
    )


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


def test_baseline_accepts_known_findings_but_not_new_ones() -> None:
    """A recorded finding passes; an unrecorded one still fails."""
    import subprocess
    import tempfile

    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "check_undefined_names.py"
    baseline = root / "docs" / "testing" / "undefined-names-baseline.txt"

    known = [
        line.strip()
        for line in baseline.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty.txt"
        empty.write_text("", encoding="utf-8")
        recorded = Path(tmp) / "recorded.txt"
        recorded.write_text("\n".join(known) + "\n", encoding="utf-8")

        # Without a baseline the known findings are reported and the run fails.
        bare = subprocess.run(
            [sys.executable, str(script), "tests"], cwd=root, capture_output=True, text=True
        )
        assert bare.returncode == 1, bare.stdout
        assert bare.stdout.count("undefined name") >= len(known)

        # With the baseline they are accepted.
        accepted = subprocess.run(
            [sys.executable, str(script), "tests", "--baseline", str(recorded)],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert accepted.returncode == 0, accepted.stdout

        # A baseline that misses one entry fails again.
        partial = Path(tmp) / "partial.txt"
        partial.write_text("\n".join(known[:-1]) + "\n", encoding="utf-8")
        rejected = subprocess.run(
            [sys.executable, str(script), "tests", "--baseline", str(partial)],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert rejected.returncode == 1, rejected.stdout
