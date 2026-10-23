"""Exercise the build-artifact packager using synthetic files, never live assets."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_unit]
ROOT = Path(__file__).resolve().parents[2]


def _package(source: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(ROOT / "frontend/scripts/package-build.mjs"), str(source), str(target)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_package_is_content_versioned_and_preserves_previous_release(tmp_path: Path) -> None:
    source = tmp_path / "TEST_dist"
    target = tmp_path / "TEST_published"
    (source / ".vite").mkdir(parents=True)
    (source / "assets").mkdir()
    (source / "assets/main.js").write_text("/* TEST_first */")
    (source / ".vite/manifest.json").write_text(json.dumps({
        "src/main.tsx": {"isEntry": True, "file": "assets/main.js"},
    }))
    first = _package(source, target)
    assert first.returncode == 0, first.stderr
    release = json.loads((target / "release.json").read_text())["releaseId"]
    assert (target / release / "assets/main.js").read_text() == "/* TEST_first */"
    assert _package(source, target).returncode == 0
    assert json.loads((target / "release.json").read_text())["releaseId"] == release
    (source / "assets/main.js").write_text("/* TEST_second */")
    assert _package(source, target).returncode == 0
    second = json.loads((target / "release.json").read_text())["releaseId"]
    assert second != release
    assert (target / release / "assets/main.js").read_text() == "/* TEST_first */"


def test_bad_package_never_replaces_active_metadata(tmp_path: Path) -> None:
    source = tmp_path / "TEST_broken"
    target = tmp_path / "TEST_published"
    source.mkdir()
    target.mkdir()
    previous = '{"releaseId":"TEST_previous"}'
    (target / "release.json").write_text(previous)
    result = _package(source, target)
    assert result.returncode != 0
    assert (target / "release.json").read_text() == previous
