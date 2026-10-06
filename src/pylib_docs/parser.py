"""Static extraction of API data from a Python source file.

The source file is parsed with the :mod:`ast` module, so it is never
executed: no imports are triggered, no code runs, no memory is spent on
building object graphs.
"""

from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ClassInfo",
    "FunctionInfo",
    "ModuleData",
    "format_signature",
    "parse_python_file",
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
# Source discovery
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
