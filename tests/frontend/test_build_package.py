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


@pytest.mark.parametrize("broken", ["lazy", "css", "font", "import-key", "escaping", "invalid-list", "null-list", "script-type", None])
def test_package_validates_reachable_graph_before_selection(tmp_path: Path, broken: str | None) -> None:
    source, target = tmp_path / "TEST_dist", tmp_path / "TEST_published"
    (source / ".vite").mkdir(parents=True)
    (source / "assets").mkdir()
    for name in ("main.js", "shared.js", "lazy.js", "lazy.css", "font.woff"):
        (source / "assets" / name).write_text("TEST_asset")
    manifest = {
        "src/main.tsx": {"isEntry": True, "file": "assets/main.js", "imports": ["shared"], "dynamicImports": ["lazy"]},
        "shared": {"file": "assets/shared.js", "imports": ["src/main.tsx"]},
        "lazy": {"file": "assets/lazy.js", "css": ["assets/lazy.css"], "assets": ["assets/font.woff"]},
    }
    if broken in {"lazy", "css", "font"}:
        (source / "assets" / {"lazy": "lazy.js", "css": "lazy.css", "font": "font.woff"}[broken]).unlink()
    elif broken == "import-key":
        del manifest["lazy"]
    elif broken == "escaping":
        (tmp_path / "TEST_outside.woff").write_text("TEST_outside")
        manifest["lazy"]["assets"] = ["../TEST_outside.woff"]
    elif broken == "invalid-list":
        manifest["lazy"]["css"] = "assets/lazy.css"
    elif broken == "null-list":
        manifest["lazy"]["assets"] = None
    elif broken == "script-type":
        manifest["lazy"]["file"] = "assets/lazy.css"
    (source / ".vite/manifest.json").write_text(json.dumps(manifest))
    target.mkdir()
    previous = '{"releaseId":"TEST_previous"}'
    (target / "release.json").write_text(previous)
    result = _package(source, target)
    if broken:
        assert result.returncode != 0, f"broken {broken} build was selected"
        assert (target / "release.json").read_text() == previous
        assert list(target.iterdir()) == [target / "release.json"]
    else:
        assert result.returncode == 0, result.stderr
        assert (target / "release.json").read_text() != previous
