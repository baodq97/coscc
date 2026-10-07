"""`new-path` and `new-idea`: where a unit or an idea goes.

`next_number` and the two commands. Nothing here writes a file or decides a stage: both print a
path.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from coscc.loop import IDEAS, SLUG_MAX, SLUG_RE, UNIT_RE, js
from coscc.loop.model import IDEA_FILE_RE

Out = Callable[[str], None]


def _scan(dir_: str) -> list[os.DirEntry[str]]:
    """`readdirSync(dir, { withFileTypes: true })`, which libuv hands back sorted by byte."""
    with os.scandir(dir_) as it:
        return sorted(it, key=lambda e: os.fsencode(e.name))


def next_number(numbers: list[int]) -> str:
    """`nextNumber`: the highest taken plus one, to four digits."""
    return str(max([0, *numbers]) + 1).zfill(4)


# --- commands ---------------------------------------------------------------------------


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
    return cmd_new_idea(first, args.cos_dir, out, err)
