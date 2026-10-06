"""Render one Obsidian note per class / global function from a Python source file.

The source file is parsed statically with :func:`pylib_docs.generate_docs.parse_python_file`
(so it is never executed) and every class and every module-level function
becomes its own Markdown file in an output folder.  No intermediate
``*_docs.md`` document is produced: the notes are rendered directly from the
parsed data, at their final heading depth.

Every occurrence of a documented name is rewritten as an Obsidian wikilink
(``[[Name]]``) so the vault graph stays fully connected.

Linking rules:

- Every occurrence of a documented name is wrapped, including inside
  signature lines.
- A note never links to itself (its own name stays plain).
- Method declaration headings are never linked: they declare the method,
  they do not reference another entity.
- Names are matched longest-first (``DateVector`` wins over ``Date``) and on
  identifier boundaries, so no link ever lands mid-word.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .generate_docs import (
    ClassInfo,
    FunctionInfo,
    ModuleData,
    _discover_quantlib_source,
    parse_python_file,
)

__all__ = ["collect_names", "main", "write_notes"]

_INDEX_NAME = "Home.md"  # ``Index`` is itself a class in the generated docs


# --------------------------------------------------------------------------- #
# Name collection
# --------------------------------------------------------------------------- #
def collect_names(data: ModuleData) -> tuple[list[str], list[str]]:
    """Return ``(class_names, function_names)`` declared in *data*, in document order."""
    classes = [cls.name for cls in data.classes]
    functions = [fn.name for fn in data.functions]
    if len(classes) != len(set(classes)):
        raise ValueError("duplicate class names in source file")
    if len(functions) != len(set(functions)):
        raise ValueError("duplicate function names in source file")
    if set(classes) & set(functions):
        raise ValueError("a name is used both as a class and as a function")
    return classes, functions


def _compile_name_pattern(names: set[str]) -> re.Pattern[str]:
    # longest first so "DateVector" wins over "Date"
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(r"(?<![A-Za-z0-9_])(" + alt + r")(?![A-Za-z0-9_])")


# --------------------------------------------------------------------------- #
# Wikilinking
# --------------------------------------------------------------------------- #
def _wikilink(content: str, self_name: str, name_re: re.Pattern[str]) -> str:
    """Rewrite documented names as wikilinks, one line at a time.

    Method declaration headings *and* their signature echo lines
    (``close(Holder self) -> bool``) are declarations of the current note's
    own method, not references, so their leading identifier is protected.
    """
    state: dict[str, str | None] = {"method": None}

    def sub(m: re.Match[str]) -> str:
        return m.group(0) if m.group(1) == self_name else f"[[{m.group(1)}]]"

    def sub_line(line: str) -> str:
        if line.startswith("#"):
            decl = re.match(r"^#{2,6} `(?:(?:async )?def )?([A-Za-z_]\w*)\(", line)
            if decl is not None:
                state["method"] = decl.group(1)
                return line  # method declaration heading: never a reference
            state["method"] = None  # H1 title or a section header
            return name_re.sub(sub, line)
        if state["method"] is not None and line.startswith(state["method"] + "("):
            head = state["method"]
            return head + name_re.sub(sub, line[len(head):])
        return name_re.sub(sub, line)

    return "\n".join(sub_line(line) for line in content.split("\n"))


# --------------------------------------------------------------------------- #
# Note rendering
# --------------------------------------------------------------------------- #
def _decorators_line(items: list[str]) -> list[str]:
    return ["*Decorators:* " + " ".join(f"`@{d}`" for d in items), ""]


def _render_class(cls: ClassInfo) -> str:
    """Render one class note, headings at their final depth (before wikilinking)."""
    lines = [f"# {cls.name}", "", cls.docstring, ""]
    if cls.methods:
        lines += ["## Methods", ""]
        for method in cls.methods:
            keyword = "async def " if method.is_async else ""
            lines += [f"### `{keyword}{method.signature}`", ""]
            if method.visible_decorators:
                lines += _decorators_line(method.visible_decorators)
            lines += [method.docstring, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_function(func: FunctionInfo) -> str:
    """Render one global-function note (before wikilinking)."""
    lines = [f"# {func.signature}", ""]
    if func.visible_decorators:
        lines += _decorators_line(func.visible_decorators)
    lines += [func.docstring, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- #
# Notes
# --------------------------------------------------------------------------- #
def write_notes(data: ModuleData, output: Path) -> tuple[list[str], list[str]]:
    """Render one note per entity in *output* and return ``(classes, functions)``.

    The index note is written to ``output/Home.md``.
    """
    classes, functions = collect_names(data)
    if _INDEX_NAME[:-3] in set(classes) | set(functions):
        raise ValueError(f"index filename {_INDEX_NAME!r} collides with an entity note")
    name_re = _compile_name_pattern(set(classes) | set(functions))

    output.mkdir(parents=True, exist_ok=True)
    for cls in data.classes:
        note = _wikilink(_render_class(cls), cls.name, name_re)
        (output / f"{cls.name}.md").write_text(note, encoding="utf-8")
    for func in data.functions:
        note = _wikilink(_render_function(func), func.name, name_re)
        (output / f"{func.name}.md").write_text(note, encoding="utf-8")

    index = ["# QuantLib API Index", "", "## Classes", ""]
    index += [f"- [[{n}]]" for n in classes]
    index += ["", "## Global Functions", ""]
    index += [f"- [[{n}]]" for n in functions]
    (output / _INDEX_NAME).write_text("\n".join(index) + "\n", encoding="utf-8")

    return classes, functions


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pylib-split-notes",
        description="Render one Obsidian note per class/function from a Python source file.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="Python file to document (default: locate QuantLib.py from an installed quantlib package)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("notes"),
        help="output folder for the notes (default: ./notes)",
    )
    args = parser.parse_args(argv)

    if args.source is None:
        source = _discover_quantlib_source()
        if source is None:
            parser.error("no source file given and QuantLib.py could not be found; "
                         "pass a path or install the quantlib package")
    else:
        source = args.source

    if not source.is_file():
        parser.error(f"source file not found: {source}")
    if source.suffix == ".md":
        parser.error(
            f"{source} is a Markdown file; pylib-split-notes reads the Python "
            "source directly, pass the .py file instead"
        )

    print(f"Parsing {source} ...")
    try:
        data = parse_python_file(source)
    except SyntaxError as exc:
        print(f"error: {source} is not valid Python: {exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {source}: {exc}", file=sys.stderr)
        return 1

    try:
        classes, functions = write_notes(data, args.output)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {len(classes) + len(functions)} notes + {_INDEX_NAME} to {args.output}/ "
        f"({len(classes)} classes, {len(functions)} functions)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
