"""`new-path`, `new-idea` and `meta`: where a unit or an idea goes, and what a store's files say.

`unit_meta`, `next_number` and the three commands. Nothing here writes a file
or decides a stage: `new-path` and `new-idea` print a path, `meta` prints what the parsers read.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from typing import Any

from coscc.loop import (
    ARTIFACTS,
    IDEAS,
    SLUG_MAX,
    SLUG_RE,
    UNIT_RE,
    VALID,
    js,
    read_text,
    stringify,
)
from coscc.loop.model import (
    IDEA_FILE_RE,
    hold_blocks,
    parse_answers,
    parse_questions,
    parse_status,
    parse_type,
)

Out = Callable[[str], None]

# --- unitMeta ---------------------------------------------------------------------------


def unit_meta(dir_: str, only: list[str] | None = None) -> dict[str, object]:
    """A unit's metadata as its files carry it; `status` is `None` unless `raw` is one the
    artifact may carry. `only` narrows it to the artifacts named."""
    artifacts: dict[str, Any] = {}
    answers: list[Any] = []
    intent_text = None
    for file in ARTIFACTS:
        if only and file not in only:
            continue
        path = os.path.join(dir_, file)
        if not os.path.exists(path):
            continue
        text = read_text(path)
        raw = parse_status(text)
        artifacts[file] = {
            "status": raw if raw is not None and raw in VALID[file] else None,
            "raw": raw,
            "sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
            "questions": parse_questions(text),
        }
        answers.extend({"artifact": file, **a} for a in parse_answers(text))
        if file == "intent.md":
            intent_text = text
    meta: dict[str, Any] = {"artifacts": artifacts, "answers": answers}
    if intent_text is not None:
        meta["type"] = parse_type(intent_text)
        meta["holds"] = hold_blocks(intent_text)
    return meta


def _scan(dir_: str) -> list[os.DirEntry[str]]:
    """`readdirSync(dir, { withFileTypes: true })`, which libuv hands back sorted by byte."""
    with os.scandir(dir_) as it:
        return sorted(it, key=lambda e: os.fsencode(e.name))


def next_number(numbers: list[int]) -> str:
    """`nextNumber`: the highest taken plus one, to four digits."""
    return str(max([0, *numbers]) + 1).zfill(4)


# --- commands ---------------------------------------------------------------------------


def _object_order(names: list[str]) -> list[str]:
    """The order a JavaScript object keeps its keys in: array indexes first, ascending."""

    def index(n: str) -> bool:
        return n.isascii() and n.isdigit() and (n == "0" or n[0] != "0") and int(n) < 2**32 - 1

    return sorted((n for n in names if index(n)), key=int) + [n for n in names if not index(n)]


def cmd_meta(unit_name: str | None, files: list[str], cos_dir: str, out: Out, err: Out) -> int:
    if unit_name is None:
        units: dict[str, Any] = {}
        if os.path.exists(cos_dir):
            for e in _scan(cos_dir):
                # `units["__proto__"] = x` sets a prototype, which `JSON.stringify` does not print.
                if e.is_dir(follow_symlinks=False) and e.name not in (IDEAS, "__proto__"):
                    units[e.name] = unit_meta(os.path.join(cos_dir, e.name))
        units = {n: units[n] for n in _object_order(list(units))}
        out(stringify({"units": units}))
        return 0
    dir_ = os.path.join(cos_dir, unit_name)
    if (
        re.search(r"[\\/]", unit_name)
        or unit_name in (".", "..", IDEAS)
        or not os.path.exists(dir_)
    ):
        err(f"No such work unit: {unit_name}")
        return 2
    stray = [f for f in files if f not in ARTIFACTS]
    if stray:
        err(f"not an artifact: {', '.join(stray)} — use {', '.join(ARTIFACTS)}")
        return 2
    out(stringify({"units": {unit_name: unit_meta(dir_, files or None)}}))
    return 0


def refuse_slug(slug: str | None, cmd: str, err: Out) -> bool:
    """Whether `slug` was refused, having said why. `new-path` and `new-idea` hold one rule."""
    if not slug:
        err(f"usage: python -m coscc.loop {cmd} <slug>")
        return True
    if not SLUG_RE.fullmatch(slug):
        err(f'Invalid slug "{slug}".')
        err("  Lowercase letters, digits and single hyphens only; no underscore,")
        err("  because the underscore separates the number from the slug.")
        return True
    if len(slug) > SLUG_MAX:
        err(
            f'Invalid slug "{slug}": it is {len(slug)} characters, '
            f"over the {SLUG_MAX} a branch allows."
        )
        return True
    return False


def cmd_new_path(
    slug: str | None, cos_dir: str, reserve_from: list[str], out: Out, err: Out
) -> int:
    if refuse_slug(slug, "new-path", err):
        return 2
    # Names only: a number is taken by a directory, whatever its files say.
    dirs = [cos_dir, *(os.path.join(os.path.abspath(d), ".cos") for d in reserve_from)]
    numbers = [
        int(m[1]) if (m := UNIT_RE.fullmatch(e.name)) else 0
        for d in dirs
        if os.path.exists(d)
        for e in _scan(d)
        if e.is_dir(follow_symlinks=False) and e.name != IDEAS
    ]
    out(f".cos/{next_number(numbers)}_{js(slug)}")
    return 0


def cmd_new_idea(slug: str | None, cos_dir: str, out: Out, err: Out) -> int:
    if refuse_slug(slug, "new-idea", err):
        return 2
    dir_ = os.path.join(cos_dir, IDEAS)
    taken = (
        [int(m[1]) for f in os.listdir(dir_) if (m := IDEA_FILE_RE.fullmatch(f))]
        if os.path.exists(dir_)
        else []
    )
    out(f".cos/{IDEAS}/{next_number(taken)}_{js(slug)}.md")
    return 0


def run(args, out: Out, err: Out) -> int:
    rest: list[str] = args.rest
    first = rest[0] if rest else None
    if args.cmd == "new-path":
        return cmd_new_path(first, args.cos_dir, args.reserve_from, out, err)
    if args.cmd == "new-idea":
        return cmd_new_idea(first, args.cos_dir, out, err)
    return cmd_meta(first, rest[1:], args.cos_dir, out, err)
