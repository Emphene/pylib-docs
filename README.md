# qlib-doc

Generate a Markdown API reference from a Python source file — **without executing it**.

`qlib-doc` reads the target file with Python's built-in [`ast`](https://docs.python.org/3/library/ast.html)
module instead of importing it, so nothing in the file ever runs: no imports are
triggered, no functions are called, and a multi-megabyte generated file (such as
SWIG's `QuantLib.py`) is scanned safely as structure rather than as code.

The output is a single, indexed Markdown document containing:

- a **Table of Contents** with local anchor links to every class and function,
- a **Classes** section with each class docstring and its methods,
- a **Global Functions** section,
- precise **signatures**: positional-only (`/`) and keyword-only (`*`)
  parameters, `*args`/`**kwargs`, defaults, type annotations and return types
  (leading `self`/`cls` are omitted from methods),
- `async` functions and methods are marked as such, and `@overload` stubs are
  collapsed into the implementation signature.

## Installation

```bash
uv sync
```

## Usage

```bash
# Document a specific file
uv run qlib-doc path/to/QuantLib.py

# Choose the output location (default: ./QuantLib_docs.md)
uv run qlib-doc path/to/QuantLib.py -o docs/api/QuantLib.md

# With no arguments, qlib-doc looks for QuantLib.py inside an installed
# `quantlib` package
uv run qlib-doc
```

You can also run the module directly: `uv run python -m qlib_doc.generate_docs <file>`.

## Splitting into Obsidian notes

`qlib-split-notes` splits a generated `*_docs.md` document into **one Markdown
note per class and per global function**, for use as an [Obsidian](https://obsidian.md/)
vault — each `notes/*.md` file becomes a note:

```bash
# Split ./QuantLib_docs.md into ./notes/ (defaults)
uv run qlib-split-notes

# Explicit paths
uv run qlib-split-notes docs/api/QuantLib.md -o vault/quantlib
```

The output folder contains:

- one note per entity (`notes/Period.md`, `notes/daysBetween.md`, ...), with the
  class's methods kept as sections inside the class note,
- `notes/Home.md`, an index note with a `[[wikilink]]` to every other note.

Every occurrence of a documented name — in prose and in signatures — is rewritten
as an Obsidian wikilink (`length(Period self)` becomes `length([[Period]] self)`),
so the graph view connects the whole vault. Names are matched longest-first on
identifier boundaries (`DateVector` never splits into `Date` + `Vector`), a note
never links to itself, and method declaration headings are left untouched because
they declare a method rather than reference another entity.

## How it works

1. The source file is parsed statically with `ast.parse` — the file is read as
   text and never executed.
2. Top-level classes and functions are collected (including definitions inside
   `if`/`try`/`with` guards, which generated code often uses).
3. Everything is rendered into one Markdown file with explicit `<a id="...">`
   anchors, so every Table of Contents link resolves.

## Development

```bash
uv sync          # install with the pytest dev group
uv run pytest    # run the test suite
```
