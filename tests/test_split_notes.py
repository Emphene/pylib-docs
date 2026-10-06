"""Tests for pylib_docs.split_notes.

A small Python fixture (mimicking SWIG-generated output) is parsed and
rendered straight into notes — no intermediate ``*_docs.md`` document — and
the linking rules are asserted on the result.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pylib_docs.generate_docs import ClassInfo, FunctionInfo, ModuleData, parse_python_file
from pylib_docs.split_notes import collect_names, main, write_notes

# --------------------------------------------------------------------------- #
# Fixture
# --------------------------------------------------------------------------- #
SOURCE = '''\
"""Fixture module mimicking SWIG-generated output."""


class Date:
    """Proxy of C++ Date class."""

    def add(self, p):
        """add(Date self, Period p) -> Date"""


class DateVector:
    """Vector of dates."""


class Holder:
    """Holds a DateVector of known Date values."""

    def close(self):
        """close(Holder self) -> bool"""


def daysBetween(a, b):
    """daysBetween(Date a, Date b) -> Time"""


def close(*args):
    """close(Real x, Real y) -> bool"""
'''


@pytest.fixture()
def source_file(tmp_path: Path) -> Path:
    path = tmp_path / "fixture.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


@pytest.fixture()
def out(tmp_path: Path, source_file: Path) -> Path:
    data = parse_python_file(source_file)
    folder = tmp_path / "notes"
    classes, functions = write_notes(data, folder)
    assert classes == ["Date", "DateVector", "Holder"]
    assert functions == ["daysBetween", "close"]
    return folder


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #
def test_one_note_per_entity_plus_index(out: Path) -> None:
    files = {p.name for p in out.glob("*.md")}
    assert files == {"Date.md", "DateVector.md", "Holder.md", "daysBetween.md", "close.md", "Home.md"}


def test_no_intermediate_document_is_written(tmp_path: Path, source_file: Path) -> None:
    data = parse_python_file(source_file)
    write_notes(data, tmp_path / "notes")
    # only the source file and the notes folder, no *_docs.md stepping stone
    assert {p.name for p in tmp_path.iterdir()} == {"fixture.py", "notes"}


def test_notes_use_native_headings_without_single_doc_residue(out: Path) -> None:
    text = out.joinpath("Date.md").read_text(encoding="utf-8")
    assert text.startswith("# Date\n")
    assert "## Methods" in text
    assert "### `add(p)`" in text
    assert "<a id=" not in text
    assert "### `class`" not in text
    assert "\n---\n" not in text


def test_index_links_every_note(out: Path) -> None:
    index = out.joinpath("Home.md").read_text(encoding="utf-8")
    assert "## Classes" in index and "## Global Functions" in index
    for name in ("Date", "DateVector", "Holder", "daysBetween", "close"):
        assert f"[[{name}]]" in index


# --------------------------------------------------------------------------- #
# Linking rules
# --------------------------------------------------------------------------- #
def test_references_are_wikilinked_in_prose_and_signatures(out: Path) -> None:
    assert "[[DateVector]]" in out.joinpath("Holder.md").read_text(encoding="utf-8")
    assert "daysBetween([[Date]] a, [[Date]] b)" in out.joinpath("daysBetween.md").read_text(
        encoding="utf-8"
    )


def test_self_name_is_never_linked(out: Path) -> None:
    assert "[[Date]]" not in out.joinpath("Date.md").read_text(encoding="utf-8")


def test_longest_name_wins_over_shorter_prefix(out: Path) -> None:
    holder = out.joinpath("Holder.md").read_text(encoding="utf-8")
    assert "[[DateVector]]" in holder
    assert "[[Date]]Vector" not in holder


def test_method_declaration_heading_is_not_linked(out: Path) -> None:
    holder = out.joinpath("Holder.md").read_text(encoding="utf-8")
    assert "### `close()`" in holder
    assert "[[close]]" not in holder


def test_async_method_declaration_heading_is_not_linked(tmp_path: Path) -> None:
    src = tmp_path / "async_cls.py"
    src.write_text(
        "class Date:\n"
        "    '''D.'''\n"
        "\n"
        "\n"
        "class Widget:\n"
        "    '''W.'''\n"
        "\n"
        "    async def refresh(self, Date):\n"
        "        '''refresh(Widget self, Date d) -> Date'''\n",
        encoding="utf-8",
    )
    write_notes(parse_python_file(src), tmp_path / "notes")
    text = (tmp_path / "notes" / "Widget.md").read_text(encoding="utf-8")
    # the declaration heading stays plain, the docstring echo line is linked
    assert "### `async def refresh(Date)`" in text
    assert "refresh(Widget self, [[Date]] d) -> [[Date]]" in text


def test_every_link_resolves_to_an_existing_note(out: Path) -> None:
    files = {p.stem for p in out.glob("*.md")}
    for p in out.glob("*.md"):
        for target in re.findall(r"\[\[([^\]|#]+)", p.read_text(encoding="utf-8")):
            assert target in files, f"{p.name}: broken link [[{target}]]"


# --------------------------------------------------------------------------- #
# Name collection & validation
# --------------------------------------------------------------------------- #
def test_collect_names(source_file: Path) -> None:
    classes, functions = collect_names(parse_python_file(source_file))
    assert classes == ["Date", "DateVector", "Holder"]
    assert functions == ["daysBetween", "close"]


def test_collect_names_rejects_class_function_collision() -> None:
    data = ModuleData(
        classes=[ClassInfo(name="Thing", docstring="")],
        functions=[FunctionInfo(name="Thing", signature="Thing()", docstring="")],
    )
    with pytest.raises(ValueError, match="both as a class and as a function"):
        collect_names(data)


def test_index_filename_collision_is_rejected_before_writing(tmp_path: Path) -> None:
    data = ModuleData(classes=[ClassInfo(name="Home", docstring="Docs.")])
    with pytest.raises(ValueError, match="collides with an entity note"):
        write_notes(data, tmp_path / "notes")
    assert not (tmp_path / "notes").exists()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_main_roundtrip(tmp_path: Path, source_file: Path, capsys: object) -> None:
    out = tmp_path / "vault"
    assert main([str(source_file), "-o", str(out)]) == 0
    assert "5 notes + Home.md" in capsys.readouterr().out  # type: ignore[attr-defined]
    assert (out / "Date.md").is_file()


def test_main_missing_source(tmp_path: Path) -> None:
    try:
        main([str(tmp_path / "nope.py")])
    except SystemExit as exc:
        assert exc.code == 2  # argparse parser.error
    else:
        raise AssertionError("expected SystemExit for missing source")


def test_main_rejects_markdown_source(tmp_path: Path, capsys: object) -> None:
    doc = tmp_path / "QuantLib_docs.md"
    doc.write_text("# API Reference Documentation\n", encoding="utf-8")
    try:
        main([str(doc)])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected SystemExit for a Markdown source")
    assert "reads the Python" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_main_syntax_error_returns_1(tmp_path: Path, capsys: object) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def nope(:\n", encoding="utf-8")
    assert main([str(broken)]) == 1
    assert "not valid Python" in capsys.readouterr().err  # type: ignore[attr-defined]
