"""Tests for qlib_doc.split_notes.

A small in-memory fixture mimicking the qlib-doc output structure is written
to a temp file, split into notes, and the linking rules are asserted.
"""

from __future__ import annotations

import re
from pathlib import Path

from qlib_doc.split_notes import collect_names, main, split_into_notes

# --------------------------------------------------------------------------- #
# Fixture
# --------------------------------------------------------------------------- #
SOURCE = """\
# API Reference Documentation

## Table of Contents

- [Classes](#classes)
  - [Date](#class-Date)

## Classes

<a id="class-Date"></a>
### `class` Date

Proxy of C++ Date class.

#### Methods

<a id="method-Date-add"></a>
##### `add(p)`

add(Date self, Period p) -> Date

---

<a id="class-DateVector"></a>
### `class` DateVector

Vector of dates.

---

<a id="class-Holder"></a>
### `class` Holder

Holds a DateVector of known Date values.

#### Methods

<a id="method-Holder-close"></a>
##### `close()`

close(Holder self) -> bool

---

<a id="global-functions"></a>
## Global Functions

<a id="function-daysBetween"></a>
### `def` daysBetween(a, b)

daysBetween(Date a, Date b) -> Time

---

<a id="function-close"></a>
### `def` close(*args)

close(Real x, Real y) -> bool

---
"""


def split(tmp_path: Path) -> Path:
    source = tmp_path / "fixture_docs.md"
    source.write_text(SOURCE, encoding="utf-8")
    out = tmp_path / "notes"
    classes, functions = split_into_notes(source, out)
    assert classes == ["Date", "DateVector", "Holder"]
    assert functions == ["daysBetween", "close"]
    return out


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #
def test_one_note_per_entity_plus_index(tmp_path: Path) -> None:
    out = split(tmp_path)
    files = {p.name for p in out.glob("*.md")}
    assert files == {"Date.md", "DateVector.md", "Holder.md", "daysBetween.md", "close.md", "Home.md"}


def test_headings_are_demoted_and_anchors_dropped(tmp_path: Path) -> None:
    text = split(tmp_path).joinpath("Date.md").read_text(encoding="utf-8")
    assert text.startswith("# Date\n")
    assert "## Methods" in text
    assert "### `add(p)`" in text
    assert "<a id=" not in text
    assert "### `class`" not in text
    assert "\n---\n" not in text


def test_index_links_every_note(tmp_path: Path) -> None:
    index = split(tmp_path).joinpath("Home.md").read_text(encoding="utf-8")
    assert "## Classes" in index and "## Global Functions" in index
    for name in ("Date", "DateVector", "Holder", "daysBetween", "close"):
        assert f"[[{name}]]" in index


# --------------------------------------------------------------------------- #
# Linking rules
# --------------------------------------------------------------------------- #
def test_references_are_wikilinked_in_prose_and_signatures(tmp_path: Path) -> None:
    out = split(tmp_path)
    assert "[[DateVector]]" in out.joinpath("Holder.md").read_text(encoding="utf-8")
    assert "daysBetween([[Date]] a, [[Date]] b)" in out.joinpath("daysBetween.md").read_text(
        encoding="utf-8"
    )


def test_self_name_is_never_linked(tmp_path: Path) -> None:
    out = split(tmp_path)
    assert "[[Date]]" not in out.joinpath("Date.md").read_text(encoding="utf-8")


def test_longest_name_wins_over_shorter_prefix(tmp_path: Path) -> None:
    holder = split(tmp_path).joinpath("Holder.md").read_text(encoding="utf-8")
    assert "[[DateVector]]" in holder
    assert "[[Date]]Vector" not in holder


def test_method_declaration_heading_is_not_linked(tmp_path: Path) -> None:
    holder = split(tmp_path).joinpath("Holder.md").read_text(encoding="utf-8")
    assert "### `close()`" in holder
    assert "[[close]]" not in holder


def test_every_link_resolves_to_an_existing_note(tmp_path: Path) -> None:
    out = split(tmp_path)
    files = {p.stem for p in out.glob("*.md")}
    for p in out.glob("*.md"):
        for target in re.findall(r"\[\[([^\]|#]+)", p.read_text(encoding="utf-8")):
            assert target in files, f"{p.name}: broken link [[{target}]]"


# --------------------------------------------------------------------------- #
# Name collection & CLI
# --------------------------------------------------------------------------- #
def test_collect_names(tmp_path: Path) -> None:
    classes, functions = collect_names(SOURCE)
    assert classes == ["Date", "DateVector", "Holder"]
    assert functions == ["daysBetween", "close"]


def test_main_roundtrip(tmp_path: Path, capsys: object) -> None:
    source = tmp_path / "fixture_docs.md"
    source.write_text(SOURCE, encoding="utf-8")
    out = tmp_path / "notes"
    assert main([str(source), "-o", str(out)]) == 0
    assert "5 notes + Home.md" in capsys.readouterr().out  # type: ignore[attr-defined]
    assert (out / "Date.md").is_file()


def test_main_missing_source(tmp_path: Path) -> None:
    try:
        main([str(tmp_path / "nope.md")])
    except SystemExit as exc:
        assert exc.code == 2  # argparse parser.error
    else:
        raise AssertionError("expected SystemExit for missing source")
