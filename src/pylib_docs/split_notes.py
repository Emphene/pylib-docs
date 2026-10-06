"""Split a generated ``*_docs.md`` reference into one Obsidian note per entity.

Each class and each global function of the source document becomes its own
Markdown file in an output folder, and every occurrence of a documented name
is rewritten as an Obsidian wikilink (``[[Name]]``) so the vault graph stays
fully connected.

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

__all__ = ["collect_names", "main", "split_into_notes"]

_INDEX_NAME = "Home.md"  # ``Index`` is itself a class in the generated docs


# --------------------------------------------------------------------------- #
# Name collection
# --------------------------------------------------------------------------- #
def collect_names(text: str) -> tuple[list[str], list[str]]:
    """Return ``(class_names, function_names)`` declared in a docs document."""
    classes = re.findall(r"^### `class` (.+)$", text, re.M)
    functions = re.findall(r"^### `def` ([^(]+)\(", text, re.M)
    if len(classes) != len(set(classes)):
        raise ValueError("duplicate class names in source document")
    if len(functions) != len(set(functions)):
        raise ValueError("duplicate function names in source document")
    if set(classes) & set(functions):
        raise ValueError("a name is used both as a class and as a function")
    return classes, functions


def _compile_name_pattern(names: set[str]) -> re.Pattern[str]:
    # longest first so "DateVector" wins over "Date"
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(r"(?<![A-Za-z0-9_])(" + alt + r")(?![A-Za-z0-9_])")


# --------------------------------------------------------------------------- #
# Splitting
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
            decl = re.match(r"^#{2,6} `([A-Za-z_]\w*)\(", line)
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


def _restructure_block(kind: str, name: str, raw: str) -> str:
    """Demote headings, drop anchors/separators, cut at the next section."""
    out: list[str] = []
    for line in raw.split("\n"):
        if line.startswith('<a id="'):
            continue
        if line == "---":  # separator between source blocks
            continue
        if line.startswith("## "):  # leaked "## Global Functions" header
            break
        if kind == "class" and line == f"### `class` {name}":
            out.append(f"# {name}")
            continue
        if kind == "def" and line.startswith("### `def` "):
            out.append("# " + line[len("### `def` "):])
            continue
        if line == "#### Methods":
            out.append("## Methods")
            continue
        if line.startswith("##### "):
            out.append("### " + line[len("##### "):])
            continue
        out.append(line)
    return "\n".join(out).strip("\n") + "\n"


def split_into_notes(source: Path, output: Path) -> tuple[list[str], list[str]]:
    """Split *source* into per-entity notes in *output*.

    Returns ``(class_names, function_names)`` in document order.  The index
    note is written to ``output/Home.md``.
    """
    text = source.read_text(encoding="utf-8")
    classes, functions = collect_names(text)
    name_re = _compile_name_pattern(set(classes) | set(functions))

    if "## Classes\n" not in text:
        raise ValueError(f"{source} has no '## Classes' section")
    body = text[text.index("## Classes\n") :]

    parts = re.split(r'(?=^<a id="(?:class|function)-[^"]+"></a>$)', body, flags=re.M)
    if parts[0].strip() != "## Classes":
        raise ValueError("unexpected content before the first class block")

    output.mkdir(parents=True, exist_ok=True)
    index_classes: list[str] = []
    index_functions: list[str] = []

    for part in parts[1:]:
        hm = re.match(
            r'^<a id="(?:class|function)-[^"]+"></a>\n### `(class|def)` (.+)$', part, re.M
        )
        if hm is None:
            raise ValueError(f"cannot parse block starting with: {part[:80]!r}")
        kind, heading = hm.group(1), hm.group(2)
        name = heading if kind == "class" else re.match(r"[^(]+", heading).group(0)

        content = _wikilink(_restructure_block(kind, name, part), name, name_re)
        (output / f"{name}.md").write_text(content, encoding="utf-8")
        (index_classes if kind == "class" else index_functions).append(name)

    # NB: a class named "Index" exists, so the index must not be called Index.md
    if _INDEX_NAME[:-3] in set(classes) | set(functions):
        raise ValueError(f"index filename {_INDEX_NAME!r} collides with an entity note")
    index = ["# QuantLib API Index", "", "## Classes", ""]
    index += [f"- [[{n}]]" for n in index_classes]
    index += ["", "## Global Functions", ""]
    index += [f"- [[{n}]]" for n in index_functions]
    (output / _INDEX_NAME).write_text("\n".join(index) + "\n", encoding="utf-8")

    return index_classes, index_functions


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qlib-split-notes",
        description="Split a generated *_docs.md reference into one Obsidian note per class/function.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=Path("QuantLib_docs.md"),
        help="documentation file produced by qlib-doc (default: ./QuantLib_docs.md)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("notes"),
        help="output folder for the notes (default: ./notes)",
    )
    args = parser.parse_args(argv)

    if not args.source.is_file():
        parser.error(f"source file not found: {args.source}")

    try:
        classes, functions = split_into_notes(args.source, args.output)
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
