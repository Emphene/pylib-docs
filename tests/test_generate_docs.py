"""Tests for pylib_docs.generate_docs.

Each test parses a small in-memory fixture module (written to a temp file)
and checks the extracted data plus the rendered Markdown.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pylib_docs.generate_docs import (
    format_signature,
    main,
    parse_python_file,
    render_markdown,
)

# --------------------------------------------------------------------------- #
# Fixture
# --------------------------------------------------------------------------- #
SOURCE = '''\
"""Module docstring goes here."""

from typing import overload

CONST = 42


def plain(a: int, b: int = 7, /, c: float = 1.5, *args: int, flag: bool = False, **kwargs: object) -> bool:
    """Does a thing."""


async def fetch(url, *, timeout=30):
    """Fetches a thing."""


def _private():
    """Hidden helper."""


@overload
def merged(x: int) -> int: ...


@overload
def merged(x: str) -> str: ...


def merged(x):
    """Merged docs."""


def guarded(x):
    """Defined inside an ``if`` guard."""


if True:
    def inside_if():
        """Still top level."""

    class InsideIf:
        """Class inside a guard."""


class Widget:
    """A widget."""

    def __init__(self, name):
        """Create a widget."""

    @property
    def size(self):
        """Widget size."""

    @staticmethod
    def create(name):
        """Factory method."""

    @classmethod
    def from_json(cls, data):
        """Build from JSON."""

    async def refresh(self, force=False):
        """Refresh the widget."""


class Empty:
    """A class without methods."""
'''


@pytest.fixture()
def source_file(tmp_path: Path) -> Path:
    path = tmp_path / "fixture.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


@pytest.fixture()
def data(source_file: Path):
    return parse_python_file(source_file)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def test_extracts_module_docstring(data):
    assert data.docstring == "Module docstring goes here."


def test_extracts_top_level_names(data):
    assert [c.name for c in data.classes] == ["InsideIf", "Widget", "Empty"]
    assert [f.name for f in data.functions] == [
        "plain",
        "fetch",
        "_private",
        "merged",
        "guarded",
        "inside_if",
    ]


def test_full_signature(data):
    plain = data.functions[0]
    assert plain.signature == (
        "plain(a: int, b: int = 7, /, c: float = 1.5, *args: int, "
        "flag: bool = False, **kwargs: object) -> bool"
    )


def test_async_function_marked(data):
    fetch = next(f for f in data.functions if f.name == "fetch")
    assert fetch.is_async
    assert fetch.signature == "fetch(url, *, timeout=30)"


def test_overloads_collapsed_to_implementation(data):
    merged = [f for f in data.functions if f.name == "merged"]
    assert len(merged) == 1
    assert merged[0].docstring == "Merged docs."
    assert merged[0].signature == "merged(x)"


def test_defs_inside_guards_are_found(data):
    assert "guarded" in [f.name for f in data.functions]
    assert "inside_if" in [f.name for f in data.functions]
    assert "InsideIf" in [c.name for c in data.classes]


def test_method_signatures_drop_self_and_cls(data):
    widget = next(c for c in data.classes if c.name == "Widget")
    signatures = {m.name: m.signature for m in widget.methods}
    assert signatures["__init__"] == "__init__(name)"
    assert signatures["size"] == "size()"
    assert signatures["create"] == "create(name)"
    assert signatures["from_json"] == "from_json(data)"
    assert signatures["refresh"] == "refresh(force=False)"


def test_method_metadata(data):
    widget = next(c for c in data.classes if c.name == "Widget")
    by_name = {m.name: m for m in widget.methods}
    assert by_name["refresh"].is_async
    assert by_name["size"].visible_decorators == ["property"]
    assert by_name["create"].visible_decorators == ["staticmethod"]
    assert all(m.class_name == "Widget" for m in widget.methods)


def test_missing_docstring_placeholder(data):
    empty = next(c for c in data.classes if c.name == "Empty")
    assert empty.docstring == "A class without methods."


def test_syntax_error_is_reported(tmp_path: Path):
    broken = tmp_path / "broken.py"
    broken.write_text("def nope(:\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        parse_python_file(broken)


# --------------------------------------------------------------------------- #
# Standalone signature formatting
# --------------------------------------------------------------------------- #
def test_format_signature_positional_only():
    node = ast_parse("def tri(x, y=2, /, z=3): ...")
    assert format_signature(node) == "tri(x, y=2, /, z=3)"


def test_format_signature_keeps_self_without_drop_self():
    node = ast_parse("def f(self): ...")
    assert format_signature(node) == "f(self)"
    assert format_signature(node, drop_self=True) == "f()"


def test_format_signature_no_params():
    node = ast_parse("def f() -> None: ...")
    assert format_signature(node) == "f() -> None"


def ast_parse(source: str):
    import ast

    return ast.parse(source).body[0]


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #
def test_every_toc_link_has_a_target(data):
    markdown = render_markdown(data)
    links = set(re.findall(r"\]\(#([^)]+)\)", markdown))
    ids = set(re.findall(r'id="([^"]+)"', markdown))
    assert links, "TOC should not be empty"
    assert links <= ids, f"broken anchors: {links - ids}"


def test_toc_lists_classes_and_functions(data):
    markdown = render_markdown(data)
    assert "- [Classes](#classes)" in markdown
    assert "  - [Widget](#class-Widget)" in markdown
    assert "- [Global Functions](#global-functions)" in markdown
    assert "  - [plain()](#function-plain)" in markdown
    assert "  - [fetch()](#function-fetch)" in markdown


def test_anchor_ids_are_unique(data):
    markdown = render_markdown(data)
    ids = re.findall(r'id="([^"]+)"', markdown)
    assert len(ids) == len(set(ids))


def test_headings_are_rendered(data):
    markdown = render_markdown(data)
    assert "### `class` Widget" in markdown
    assert "##### `__init__(name)`" in markdown
    assert "##### `async def refresh(force=False)`" in markdown
    assert "### `async def` fetch(url, *, timeout=30)" in markdown
    assert "### `def` plain(" in markdown
    assert "*Decorators:* `@property`" in markdown
    assert "@overload" not in markdown  # stubs never leak into the output


def test_class_without_methods_has_no_method_section(data):
    markdown = render_markdown(data)
    empty_section = markdown.split("### `class` Empty")[1].split("---")[0]
    assert "#### Methods" not in empty_section


def test_empty_sections_are_omitted(tmp_path: Path):
    path = tmp_path / "only_classes.py"
    path.write_text("class Solo:\n    '''Docs.'''\n", encoding="utf-8")
    markdown = render_markdown(parse_python_file(path))
    assert "Global Functions" not in markdown
    assert "## Table of Contents" in markdown


def test_no_definitions_at_all(tmp_path: Path):
    path = tmp_path / "empty.py"
    path.write_text("x = 1\n", encoding="utf-8")
    markdown = render_markdown(parse_python_file(path))
    assert "Table of Contents" not in markdown


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_writes_to_cwd_by_default(source_file: Path, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([str(source_file)]) == 0
    output = tmp_path / "fixture_docs.md"
    assert output.is_file()
    assert "# API Reference Documentation" in output.read_text(encoding="utf-8")


def test_cli_respects_output_flag(source_file: Path, tmp_path: Path):
    output = tmp_path / "nested" / "custom.md"
    assert main([str(source_file), "-o", str(output)]) == 0
    assert output.is_file()


def test_cli_missing_file_exits_with_code_2(tmp_path: Path):
    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path / "nope.py")])
    assert excinfo.value.code == 2


def test_cli_syntax_error_returns_1(tmp_path: Path, capsys):
    broken = tmp_path / "broken.py"
    broken.write_text("def nope(:\n", encoding="utf-8")
    assert main([str(broken)]) == 1
    assert "not valid Python" in capsys.readouterr().err
