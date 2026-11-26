"""Bounded static checks for the standalone PRD prototype, without a Vite build.

HTMLParser checks document metadata and references, not HTML conformance. CSS
uses the project's existing tinycss2 dependency (BSD-2-Clause, >=1.3,<2);
JavaScript uses Node's syntax-only --check and never executes prototype code.
https://doc.courtbouillon.org/tinycss2/stable/api_reference.html
https://nodejs.org/api/cli.html#-c---check
"""

from __future__ import annotations

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest
import tinycss2

pytestmark = [pytest.mark.layer_unit]
PROTOTYPES = Path(__file__).resolve().parents[2] / "prd/prototypes"


class PrototypeDocument(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.tags: set[str] = set()
        self.title = ""
        self.viewport = False
        self._in_title = False
        self._inline_script: list[str] | None = None
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.tags.add(tag)
        self._in_title = tag == "title" or self._in_title
        if tag == "meta" and attributes.get("name") == "viewport":
            self.viewport = bool(attributes.get("content"))
        for attribute in ("href", "src", "poster"):
            if attributes.get(attribute):
                self.references.append(str(attributes[attribute]))
        if tag == "script" and not attributes.get("src"):
            self._inline_script = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._inline_script is not None:
            self.scripts.append("".join(self._inline_script))
            self._inline_script = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._inline_script is not None:
            self._inline_script.append(data)


def _document(source: str) -> PrototypeDocument:
    document = PrototypeDocument()
    document.feed(source)
    document.close()
    assert {"html", "head", "body", "title"} <= document.tags
    assert document.title.strip(), "prototype page needs a nonempty title"
    assert document.viewport, "prototype page needs viewport metadata"
    return document


def _check_reference(source: Path, reference: str, root: Path) -> None:
    url = urlsplit(reference)
    if url.scheme or url.netloc or not url.path:
        return
    target = (source.parent / unquote(url.path)).resolve()
    assert target.is_relative_to(root.resolve()), f"{source.name}: reference leaves prototype: {reference}"
    assert target.is_file(), f"{source.name}: missing local reference: {reference}"


def _css_parts(css: str) -> list[Any]:
    """Parse declarations and nested media rules as well as the top-level sheet."""
    pending = list(tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True))
    parts = []
    while pending:
        part = pending.pop()
        assert part.type != "error", f"CSS line {part.source_line}: {part.message}"
        parts.append(part)
        content = getattr(part, "content", None)
        if part.type == "qualified-rule" or (
            part.type == "at-rule" and part.lower_at_keyword in {"font-face", "page"}
        ):
            pending.extend(tinycss2.parse_declaration_list(content, skip_comments=True, skip_whitespace=True))
        elif part.type == "at-rule" and content is not None:
            pending.extend(tinycss2.parse_rule_list(content, skip_comments=True, skip_whitespace=True))
        for attribute in ("prelude", "value", "arguments"):
            tokens = getattr(part, attribute, None)
            if isinstance(tokens, list):
                pending.extend(tokens)
    return parts


def _check_javascript(source: str) -> None:
    node = shutil.which("node")
    assert node, "prototype JavaScript checks require Node.js"
    result = subprocess.run(
        [node, "--check"], input=source, text=True, encoding="utf-8",
        capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_prototype_pages_have_metadata_and_resolvable_local_references() -> None:
    paths = sorted(PROTOTYPES.rglob("*.html"))
    assert paths, "prototype pages must not disappear silently"
    for path in paths:
        document = _document(path.read_text(encoding="utf-8"))
        for reference in document.references:
            _check_reference(path, reference, PROTOTYPES)
        for script in document.scripts:
            _check_javascript(script)


def test_prototype_stylesheets_parse_and_import_existing_assets() -> None:
    paths = sorted(PROTOTYPES.rglob("*.css"))
    assert paths, "prototype stylesheets must not disappear silently"
    for path in paths:
        parts = _css_parts(path.read_text(encoding="utf-8"))
        assert any(part.type == "qualified-rule" for part in parts), path
        for part in parts:
            if part.type == "url":
                _check_reference(path, part.value, PROTOTYPES)
            elif part.type == "function" and part.lower_name == "url":
                value = tinycss2.serialize(part.arguments).strip().strip("\"'")
                _check_reference(path, value, PROTOTYPES)
            elif part.type == "at-rule" and part.lower_at_keyword == "import":
                tokens = [token for token in part.prelude if token.type != "whitespace"]
                if tokens and tokens[0].type == "string":
                    _check_reference(path, tokens[0].value, PROTOTYPES)


def test_prototype_javascript_has_valid_syntax() -> None:
    paths = sorted(PROTOTYPES.rglob("*.js"))
    assert paths, "prototype scripts must not disappear silently"
    for path in paths:
        _check_javascript(path.read_text(encoding="utf-8"))


def test_missing_or_escaping_local_asset_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "TEST_page.html"
    for reference in ("TEST_missing.css?v=1", "../TEST_outside.js", "%2e%2e/TEST_outside.js"):
        with pytest.raises(AssertionError):
            _check_reference(source, reference, tmp_path)
    (tmp_path / "TEST_present.css").write_text("body { color: red; }", encoding="utf-8")
    _check_reference(source, "TEST_present.css?v=1#TEST_fragment", tmp_path)


@pytest.mark.parametrize("css", ["}", "body { TEST_invalid }", "@media screen { body { TEST_invalid } }"])
def test_malformed_stylesheet_or_nested_declaration_is_rejected(css: str) -> None:
    with pytest.raises(AssertionError):
        _css_parts(css)


def test_invalid_prototype_javascript_is_rejected() -> None:
    with pytest.raises(AssertionError):
        _check_javascript("const TEST_broken = ;")
