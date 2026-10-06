"""qlib-doc: generate Markdown API documentation from Python source files."""

from __future__ import annotations

import sys

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point (import kept lazy so ``-m`` runs cleanly)."""
    from .generate_docs import main as _main

    return _main(argv)


if __name__ == "__main__":
    sys.exit(main())
