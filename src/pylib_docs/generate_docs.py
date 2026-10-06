"""Generate Markdown API documentation from a Python source file.

The source file is parsed with the :mod:`ast` module, so it is never
executed: no imports are triggered, no code runs, no memory is spent on
building object graphs.
"""

from __future__ import annotations

import argparse
import ast
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ClassInfo",
    "FunctionInfo",
    "ModuleData",
    "format_signature",
    "generate_markdown",
    "main",
    "parse_python_file",
    "render_markdown",
]

_NO_DOC = "*No description provided.*"

# ``ast.TryStar`` only exists on 3.11+, keep the supported floor at 3.10.
_TRY_TYPES = (ast.Try,) + tuple(filter(None, (getattr(ast, "TryStar", None),)))


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(eq=False)
class FunctionInfo:
    """A module-level function or a class method."""

    name: str
    signature: str
    docstring: str
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    class_name: str | None = None

    @property
    def is_method(self) -> bool:
        return self.class_name is not None

    @property
    def is_overload(self) -> bool:
        return any(d.rsplit(".", 1)[-1] == "overload" for d in self.decorators)

    @property
    def anchor(self) -> str:
        if self.class_name is not None:
            return f"method-{self.class_name}-{self.name}"
        return f"function-{self.name}"

    @property
    def visible_decorators(self) -> list[str]:
        """Decorators worth showing, i.e. everything but ``@overload`` stubs."""
        return [d for d in self.decorators if d.rsplit(".", 1)[-1] != "overload"]


@dataclass
class ClassInfo:
    """A top-level class and its public methods."""

    name: str
    docstring: str
    methods: list[FunctionInfo] = field(default_factory=list)

    @property
    def anchor(self) -> str:
        return f"class-{self.name}"


@dataclass
class ModuleData:
    """Everything extracted from one source file."""

    docstring: str = ""
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Signatures
# --------------------------------------------------------------------------- #
def _unparse(node: ast.AST | None) -> str | None:
    return None if node is None else ast.unparse(node)


def _format_arg(arg: ast.arg, default: ast.expr | None = None) -> str:
    text = arg.arg
    annotation = _unparse(arg.annotation)
    if annotation is not None:
        text += f": {annotation}"
    if default is not None:
        # PEP 8: no spaces around "=" without an annotation, spaces with one.
        separator = " = " if annotation is not None else "="
        text += f"{separator}{ast.unparse(default)}"
    return text


def format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef, *, drop_self: bool = False) -> str:
    """Render ``name(params) -> return`` for a function definition.

    Handles positional-only (``/``), keyword-only (``*``), ``*args``,
    ``**kwargs``, defaults and annotations.  When ``drop_self`` is true the
    leading ``self``/``cls`` parameter of a method is omitted.
    """
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    posonly_count = len(args.posonlyargs)
    n_positional = len(positional)

    shift = 0
    if drop_self and positional and positional[0].arg in ("self", "cls"):
        positional.pop(0)
        if posonly_count:
            posonly_count -= 1
        shift = 1

    # ``args.defaults`` aligns with the *end* of the positional list.
    default_map: dict[int, ast.expr] = {}
    for i, default in enumerate(args.defaults):
        index = n_positional - len(args.defaults) + i - shift
        if index >= 0:
            default_map[index] = default

    parts: list[str] = []
    for i, arg in enumerate(positional):
        parts.append(_format_arg(arg, default_map.get(i)))
        if i + 1 == posonly_count:
            parts.append("/")
    if args.vararg is not None:
        parts.append("*" + _format_arg(args.vararg))
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(_format_arg(arg, default))
    if args.kwarg is not None:
        parts.append("**" + _format_arg(args.kwarg))

    text = f"{node.name}({', '.join(parts)})"
    returns = _unparse(node.returns)
    if returns is not None:
        text += f" -> {returns}"
    return text


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _iter_defs(body: list[ast.stmt]):
    """Yield function/class definitions at module (or class) level.

    Descends into control-flow statements (``if``/``try``/``with``/loops,
    which generated code often uses as guards) but never into the body of a
    function or class, so nested definitions stay nested.
    """
    for item in body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield item
        elif isinstance(item, ast.If):
            yield from _iter_defs(item.body)
            yield from _iter_defs(item.orelse)
        elif isinstance(item, _TRY_TYPES):
            yield from _iter_defs(item.body)
            for handler in item.handlers:
                yield from _iter_defs(handler.body)
            yield from _iter_defs(item.orelse)
            yield from _iter_defs(item.finalbody)
        elif isinstance(item, (ast.With, ast.AsyncWith)):
            yield from _iter_defs(item.body)
        elif isinstance(item, (ast.For, ast.AsyncFor, ast.While)):
            yield from _iter_defs(item.body)
            yield from _iter_defs(item.orelse)
        elif isinstance(item, ast.Match):
            for case in item.cases:
                yield from _iter_defs(case.body)


def _build_function(node: ast.FunctionDef | ast.AsyncFunctionDef, *, class_name: str | None = None) -> FunctionInfo:
    return FunctionInfo(
        name=node.name,
        signature=format_signature(node, drop_self=class_name is not None),
        docstring=ast.get_docstring(node) or _NO_DOC,
        decorators=[ast.unparse(d) for d in node.decorator_list],
        is_async=isinstance(node, ast.AsyncFunctionDef),
        class_name=class_name,
    )


def _build_class(node: ast.ClassDef) -> ClassInfo:
    methods: list[FunctionInfo] = []
    method_index: dict[str, int] = {}
    for sub_item in _iter_defs(node.body):
        if not isinstance(sub_item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue  # nested classes are not documented
        info = _build_function(sub_item, class_name=node.name)
        _add_function(methods, method_index, info)
    return ClassInfo(
        name=node.name,
        docstring=ast.get_docstring(node) or _NO_DOC,
        methods=methods,
    )


def _add_function(items: list[FunctionInfo], index: dict[str, int], info: FunctionInfo) -> None:
    """Add ``info``, collapsing ``@overload`` stubs into their implementation."""
    position = index.get(info.name)
    if position is None:
        index[info.name] = len(items)
        items.append(info)
        return
    previous = items[position]
    if previous.is_overload and not info.is_overload:
        items[position] = info  # the real definition replaces the stubs


def parse_python_file(file_path: str | Path) -> ModuleData:
    """Parse ``file_path`` statically and extract classes, functions and docstrings."""
    path = Path(file_path)
    # tokenize.open honours PEP 263 encoding cookies; falls back to UTF-8.
    with tokenize.open(path) as handle:
        tree = ast.parse(handle.read(), filename=str(path))

    data = ModuleData(docstring=ast.get_docstring(tree) or "")
    function_index: dict[str, int] = {}
    class_index: dict[str, int] = {}

    for node in _iter_defs(tree.body):
        if isinstance(node, ast.ClassDef):
            info = _build_class(node)
            position = class_index.get(node.name)
            if position is None:  # a redefined class keeps its first slot
                class_index[node.name] = len(data.classes)
                data.classes.append(info)
            else:
                data.classes[position] = info
        else:
            _add_function(data.functions, function_index, _build_function(node))
    return data


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #
class _AnchorRegistry:
    """Hands out unique anchor ids, deduplicating collisions with ``-2``, ``-3``…"""

    def __init__(self, reserved: tuple[str, ...] = ()) -> None:
        self._used = set(reserved)

    def reserve(self, base: str) -> str:
        anchor = base
        counter = 2
        while anchor in self._used:
            anchor = f"{base}-{counter}"
            counter += 1
        self._used.add(anchor)
        return anchor


def _anchor_tag(anchor: str) -> str:
    return f'<a id="{anchor}"></a>'


def render_markdown(data: ModuleData) -> str:
    """Convert extracted data into a Markdown document with a working TOC."""
    anchors = _AnchorRegistry(reserved=("classes", "global-functions"))

    classes = [(cls, anchors.reserve(cls.anchor)) for cls in data.classes]
    functions = [(fn, anchors.reserve(fn.anchor)) for fn in data.functions]
    method_anchors = {
        method: anchors.reserve(method.anchor)
        for cls, _ in classes
        for method in cls.methods
    }

    lines: list[str] = []
    add = lines.append

    add("# API Reference Documentation")
    add("")
    if data.docstring:
        add(data.docstring)
        add("")

    if classes or functions:
        add("## Table of Contents")
        add("")
        if classes:
            add("- [Classes](#classes)")
            for cls, anchor in classes:
                add(f"  - [{cls.name}](#{anchor})")
        if functions:
            add("- [Global Functions](#global-functions)")
            for func, anchor in functions:
                add(f"  - [{func.name}()](#{anchor})")
        add("")
        add("---")
        add("")

    if classes:
        add(_anchor_tag("classes"))
        add("## Classes")
        add("")
        for cls, anchor in classes:
            add(_anchor_tag(anchor))
            add(f"### `class` {cls.name}")
            add("")
            add(cls.docstring)
            add("")
            if cls.methods:
                add("#### Methods")
                add("")
                for method in cls.methods:
                    add(_anchor_tag(method_anchors[method]))
                    keyword = "async def " if method.is_async else ""
                    add(f"##### `{keyword}{method.signature}`")
                    add("")
                    if method.visible_decorators:
                        add("*Decorators:* " + " ".join(f"`@{d}`" for d in method.visible_decorators))
                        add("")
                    add(method.docstring)
                    add("")
            add("---")
            add("")

    if functions:
        add(_anchor_tag("global-functions"))
        add("## Global Functions")
        add("")
        for func, anchor in functions:
            add(_anchor_tag(anchor))
            keyword = "async def" if func.is_async else "def"
            add(f"### `{keyword}` {func.signature}")
            add("")
            if func.visible_decorators:
                add("*Decorators:* " + " ".join(f"`@{d}`" for d in func.visible_decorators))
                add("")
            add(func.docstring)
            add("")
            add("---")
            add("")

    return "\n".join(lines).rstrip() + "\n"


def generate_markdown(data: ModuleData, output_file: str | Path) -> Path:
    """Render ``data`` and write it to ``output_file``."""
    output = Path(output_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(data), encoding="utf-8")
    return output


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _discover_quantlib_source() -> Path | None:
    """Locate ``QuantLib.py`` inside an installed ``quantlib`` package, if any."""
    from importlib import util

    for module_name in ("QuantLib", "quantlib"):
        try:
            spec = util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError, ValueError):
            continue
        if spec is None:
            continue
        if spec.origin is not None:
            origin = Path(spec.origin)
            if origin.name == "QuantLib.py" and origin.is_file():
                return origin
        for location in spec.submodule_search_locations or []:
            root = Path(location)
            direct = root / "QuantLib.py"
            if direct.is_file():
                return direct
            found = sorted(root.rglob("QuantLib.py"))
            if found:
                return found[0]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qlib-doc",
        description="Generate a Markdown API reference from a Python source file, without executing it.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Python file to document (default: locate QuantLib.py from an installed quantlib package)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output Markdown path (default: ./<source_name>_docs.md)",
    )
    args = parser.parse_args(argv)

    if args.source is None:
        source = _discover_quantlib_source()
        if source is None:
            parser.error("no source file given and QuantLib.py could not be found; "
                         "pass a path or install the quantlib package")
    else:
        source = Path(args.source)

    if not source.is_file():
        parser.error(f"source file not found: {source}")

    output = args.output if args.output is not None else Path.cwd() / f"{source.stem}_docs.md"

    print(f"Parsing {source} ...")
    try:
        data = parse_python_file(source)
    except SyntaxError as exc:
        print(f"error: {source} is not valid Python: {exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {source}: {exc}", file=sys.stderr)
        return 1

    generate_markdown(data, output)
    print(
        f"Wrote {output} "
        f"({len(data.classes)} classes, {sum(len(c.methods) for c in data.classes)} methods, "
        f"{len(data.functions)} functions)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
