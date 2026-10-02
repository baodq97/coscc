"""`new-path`, `new-idea` and `meta`: where a unit or an idea goes, and what a store's files say.

A port of `.claude/scripts/cos.mjs` (`unitMeta`, `parseIdea`, `readIdeas`, `nextNumber`,
`cmdMeta`, `cmdNewPath`, `refuseSlug`, `cmdNewIdea`), line for line. Nothing here writes a file
or decides a stage: `new-path` and `new-idea` print a path, `meta` prints what the parsers read.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from collections.abc import Callable
from typing import Any

from coscc.loop import (
    ARTIFACTS,
    IDEAS,
    JS_SPACE,
    SLUG_MAX,
    SLUG_RE,
    UNIT_RE,
    VALID,
    js,
    read_text,
    stringify,
    trim,
    trim_end,
)
from coscc.loop.model import (
    IDEA_FILE_RE,
    hold_blocks,
    parse_answers,
    parse_links,
    parse_questions,
    parse_status,
    parse_type,
    parse_unit_ref,
    section,
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
        meta["links"] = parse_links(intent_text)
        meta["holds"] = hold_blocks(intent_text)
    return meta


# --- ideas ------------------------------------------------------------------------------

# JavaScript's `\S`, `.` and `$` (no `m` flag), which are not Python's.
_NS = f"[^{re.escape(JS_SPACE)}]"
_DOT = "[^\\n\\r  ]"
UNIT_LINE = re.compile(rf"^- ({_NS}+?)(?:\. Depends on: ({_DOT}+?))?\.?\Z")
IDEA_TITLE = re.compile(rf"(?<![^\r\n  ])# Idea:[ \t]*({_DOT}+)(?=[\r\n  ]|\Z)")
_DEPS_SPLIT = re.compile(rf",[{re.escape(JS_SPACE)}]*")


def _has_ws(ref: str) -> bool:
    ref_of = parse_unit_ref(ref)
    return bool(ref_of and ref_of["ws"])


def parse_idea(text: str) -> dict[str, object]:
    """An idea file, with every line under `## Units` it cannot read reported, not guessed."""
    units: list[Any] = []
    problems: list[str] = []
    status = parse_status(text)
    if status is None:
        problems.append("carries no Status line")
    lines = section(text, "Units")
    if lines is None:
        problems.append("has no ## Units section")
    for line in lines or []:
        if not trim(line):
            continue
        m = UNIT_LINE.match(trim_end(line))
        deps = _DEPS_SPLIT.split(m[2]) if m and m[2] else []
        if not m or not _has_ws(m[1]) or any(not _has_ws(d) for d in deps):
            problems.append(
                f'## Units: "{trim(line)}" is not "- <ws>/NNNN_<slug>", '
                'with ". Depends on: <ws>/NNNN_<slug>" or not'
            )
            continue
        units.append({"ref": m[1], "dependsOn": deps})
    title = IDEA_TITLE.search(text)
    return {
        "title": trim(title[1]) if title else None,
        "status": status,
        "units": units,
        "problems": problems,
    }


# ICU's root order for ASCII punctuation, which `localeCompare` sorts before the digits.
_PUNCT = "\t\n\v\f\r _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$"


def _collation(name: str) -> tuple[list[int], list[tuple[int, ...]], list[int]]:
    """An approximation of `String.prototype.localeCompare` for what a file name holds: the
    letters first, ignoring accents and case; then the accents; then lower before upper case."""
    primary: list[int] = []
    accents: list[tuple[int, ...]] = []
    tertiary: list[int] = []
    for c in unicodedata.normalize("NFD", name.replace("ß", "ss")):
        if unicodedata.combining(c):
            if accents:
                accents[-1] = (*accents[-1], ord(c))
            continue
        if c in _PUNCT:
            weight = _PUNCT.index(c)
        elif "0" <= c <= "9":
            weight = 100 + ord(c)
        elif c.isalpha() and c.lower() in "abcdefghijklmnopqrstuvwxyz":
            weight = 2000 + 10 * ord(c.lower())
        elif c.lower() == "đ":
            weight = 2000 + 10 * ord("d") + 5
        elif ord(c) < 32 or ord(c) == 127:
            continue
        else:
            weight = 1_000_000 + ord(c)
        primary.append(weight)
        accents.append(())
        tertiary.append(1 if c.isupper() else 0)
    return primary, accents, tertiary


def _scan(dir_: str) -> list[os.DirEntry[str]]:
    """`readdirSync(dir, { withFileTypes: true })`, which libuv hands back sorted by byte."""
    with os.scandir(dir_) as it:
        return sorted(it, key=lambda e: os.fsencode(e.name))


def read_ideas(cos_dir: str) -> list[dict[str, object]]:
    """Every file under `<cos_dir>/ideas/`, by name. Only called when that directory exists."""
    dir_ = os.path.join(cos_dir, IDEAS)
    ideas: list[dict[str, object]] = []
    for e in sorted(_scan(dir_), key=lambda e: _collation(e.name)):
        if not e.is_file(follow_symlinks=False) or not IDEA_FILE_RE.fullmatch(e.name):
            ideas.append(
                {
                    "id": e.name,
                    "title": None,
                    "status": None,
                    "units": [],
                    "problems": ["not a file named NNNN_<slug>.md"],
                }
            )
            continue
        ideas.append(
            {"id": e.name[: -len(".md")], **parse_idea(read_text(os.path.join(dir_, e.name)))}
        )
    return ideas


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
        ideas = read_ideas(cos_dir) if os.path.exists(os.path.join(cos_dir, IDEAS)) else None
        units = {n: units[n] for n in _object_order(list(units))}
        out(stringify({"units": units, "ideas": ideas}))
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
        err(f"usage: cos.mjs {cmd} <slug>")
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
