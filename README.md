# pylib_docs

Generate an [Obsidian](https://obsidian.md/) vault of API notes from a Python
source file — **without executing it**.

`pylib-split-notes` reads the target file with Python's built-in [`ast`](https://docs.python.org/3/library/ast.html)
module instead of importing it, so nothing in the file ever runs: no imports are
triggered, no functions are called, and a multi-megabyte generated file (such as
SWIG's `QuantLib.py`) is scanned safely as structure rather than as code.

The output folder contains:

- **one Markdown note per class and per global function**
  (`notes/Period.md`, `notes/daysBetween.md`, ...), with the class's methods
  kept as sections inside the class note, precise signatures (positional-only
  (`/`) and keyword-only (`*`) parameters, `*args`/`**kwargs`, defaults, type
  annotations and return types — leading `self`/`cls` are omitted), `async`
  definitions marked as such, and `@overload` stubs collapsed into the
  implementation signature,
- `notes/Home.md`, an index note with a `[[wikilink]]` to every other note.

## Installation

```bash
uv sync
```

## Usage

```bash
# With no arguments, pylib-split-notes looks for QuantLib.py inside an
# installed `quantlib` package and writes the notes to ./notes/
uv run pylib-split-notes

# Explicit paths
uv run pylib-split-notes path/to/QuantLib.py -o vault/quantlib
```

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
3. Each entity is rendered straight into its own note at its final heading
   depth, with every documented name rewritten as a wikilink.

## Development

```bash
uv sync          # install with the pytest dev group
uv run pytest    # run the test suite
```
