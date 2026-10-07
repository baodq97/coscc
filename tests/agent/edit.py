"""One value deep in an agent's row, as the page saves it: the whole key."""

from __future__ import annotations

import copy
from typing import Any

from coscc.agent import pack


def _at(tree: Any, rest: list[str]) -> Any:
    for part in rest:
        tree = tree.get(part) if isinstance(tree, dict) else None
    return tree


def whole(key: str, path: str, value: Any) -> tuple[str, Any]:
    """`(top key, its whole value)` with `path` (`ceilings.turns`, `variants.novel.model.id`) of
    `key`'s row set to `value`, or to the built-in's with `None`; `None` for a key left empty."""
    top, *rest = path.split(".")
    found = pack.row(key) or {}
    tree = out = copy.deepcopy(found.get(top) or {})
    if value is None:
        value = _at(found["builtin"].get(top) or {}, rest)
    for part in rest[:-1]:
        tree = tree.setdefault(part, {})
    if value is None:
        tree.pop(rest[-1], None)
    else:
        tree[rest[-1]] = value
    return top, out or None


def set_part(key: str, path: str, value: Any) -> tuple[Any, Any]:
    """`whole`, saved; `(old, new)` at `path`."""
    rest = path.split(".")[1:]
    top = path.split(".")[0]
    old = _at((pack.row(key) or {}).get(top) or {}, rest)
    pack.write(key, *whole(key, path, value))
    return old, _at((pack.row(key) or {}).get(top) or {}, rest)
