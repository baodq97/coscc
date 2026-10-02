"""The commands that name a branch, a pull request's text, a tag or a version.

Ported 1:1 from `.claude/scripts/cos.mjs`: `TAG_RE` through `versionProblem` (2836-2869), the
by-hand reading of the version files and the commands from `cmdCheckBranch` to `cmdPrText`
(3054-3218), and the dispatch of `unit-branch`, `pr-text`, `check-branch`, `check-tag` and
`check-version` (3450-3457). `check-*` read the checkout the process stands in (`checkout()`), never
a `--root`, nor where this package is installed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable

from coscc.loop import (
    BRANCH_TYPES,
    JS_SPACE,
    UNDEFINED,
    UNIT_RE,
    checkout,
    dig,
    js,
    nullish,
    read_text,
    stringify,
    trim,
)
from coscc.loop.model import (
    NO_ENTRY,
    branch_for,
    branch_problem,
    entry_of,
    not_a_work_branch,
    pr_text,
    status_in,
    title_problem,
)

# JavaScript's `\s`, spelled out: `re`'s `\s` reads `\x1c`-`\x1f` and `\x85` as space, and not `﻿`.
_S = "[" + re.escape(JS_SPACE) + "]"
# JavaScript's `^` and `$` under the `m` flag: a line ends at `\n`, `\r`, ` ` and ` `.
_BOL = "(?<![^\\n\\r  ])"
_EOL = "(?![^\\n\\r  ])"

# `vX.Y.Z`, or `vX.Y.Z-rc.N` for a prerelease. Leading zeros are refused so that one release
# has one spelling.
TAG_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?", re.ASCII)


def tag_problem(name):
    if not name:
        return "no tag name"
    m = TAG_RE.fullmatch(name)
    if not m:
        return "expected vX.Y.Z, or vX.Y.Z-rc.N for a prerelease"
    for part in (m[1], m[2], m[3]):
        if len(part) > 1 and part.startswith("0"):
            return f'"{part}" carries a leading zero'
    if m[4] is not None and m[4].startswith("0"):
        return "the release candidate number starts at 1"
    return None


def is_prerelease(name):
    return not tag_problem(name) and "-rc." in name


def tag_version(name):
    return None if tag_problem(name) else name[1:].split("-")[0]


# `pyproject.toml` is the source and the rest are copies.
VERSION_SOURCE = "pyproject.toml"


def version_problem(found):
    source = found[VERSION_SOURCE]
    if not source:
        return f"{VERSION_SOURCE} declares no version"
    off = [
        f"{place} is {js(nullish(value, 'unreadable'))}"
        for place, value in found.items()
        if place != VERSION_SOURCE and value != source
    ]
    return f"{VERSION_SOURCE} says {js(source)}, but {'; '.join(off)}" if off else None


# --- reading the version out of four files, two formats, no parser ------------------------


def toml_string(text, key):
    m = re.search(f'{_BOL}{key}{_S}*={_S}*"([^"]*)"', text)
    return m[1] if m else None


def toml_table(text, header):
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if trim(l) == header), -1)
    if start == -1:
        return None
    rest = lines[start + 1 :]
    end = next((i for i, l in enumerate(rest) if re.match(f"{_S}*\\[", l)), -1)
    return "\n".join(rest if end == -1 else rest[:end])


def locked_version(text, name):
    """`uv.lock` holds one `[[package]]` table per dependency; only the one naming `name` counts."""
    for block in re.split(f"{_BOL}\\[\\[package\\]\\]{_S}*{_EOL}", text)[1:]:
        if toml_string(block, "name") == name:
            return toml_string(block, "version")
    return None


def _no_constant(name):
    raise ValueError(name)


def json_at(text, path):
    """`path.reduce((v, k) => v?.[k], JSON.parse(text))`, or `None` for what does not parse."""
    try:
        v = json.loads(text, parse_constant=_no_constant)
    except ValueError, RecursionError:
        return None
    for k in path:
        v = dig(v, k)
    return v


def slurp(rel):
    path = checkout() / rel
    return read_text(path) if path.exists() else ""


def declared_versions(read_file: Callable[[str], str] = slurp):
    """Five numbers in four files; `package-lock.json` carries two that can disagree."""
    pyproject = read_file("pyproject.toml")
    project = nullish(toml_table(pyproject, "[project]"), "")
    name = toml_string(project, "name")
    lock = read_file("package-lock.json")
    return {
        "pyproject.toml": toml_string(project, "version"),
        "package.json": json_at(read_file("package.json"), ["version"]),
        "uv.lock": locked_version(read_file("uv.lock"), name) if name else None,
        "package-lock.json": json_at(lock, ["version"]),
        "package-lock.json packages['']": json_at(lock, ["packages", "", "version"]),
    }


# --- commands that describe this checkout -------------------------------------------------


def _git(*args):
    """`git` run in `checkout()`, its stdout trimmed; `None` when it cannot run or fails."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=checkout(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    if r.returncode != 0:
        return None
    return trim(r.stdout.decode("utf-8", "replace"))


def cmd_check_branch(name, out, err):
    subject = nullish(name, None)
    if subject is None:
        subject = _git("rev-parse", "--abbrev-ref", "HEAD")
    if subject is None:
        err("not a git checkout, and no branch name was given")
        return 2
    problem = branch_problem(subject)
    if problem:
        err(not_a_work_branch(subject, problem))
        return 1
    out(subject)
    return 0


def cmd_check_tag(name, out, err):
    if not name:
        err("usage: cos.mjs check-tag <vX.Y.Z | vX.Y.Z-rc.N>")
        return 2
    problem = tag_problem(name)
    if problem:
        err(f'"{name}" is not a release tag: {problem}')
        return 1
    # The release workflow reads this line instead of comparing the string itself.
    out("prerelease" if is_prerelease(name) else "release")
    return 0


def cmd_check_version(out, err):
    found = declared_versions()
    problem = version_problem(found)
    if problem:
        err(f"the version is not in step: {problem}")
        for place, value in found.items():
            err(f"  {place}: {js(nullish(value, '(unreadable)'))}")
        return 1
    version = found[VERSION_SOURCE]
    # A tag on HEAD is a fifth declaration, and it only exists sometimes.
    tag = next(
        (
            t
            for t in nullish(_git("tag", "--points-at", "HEAD"), "").split("\n")
            if t and not tag_problem(t)
        ),
        None,
    )
    if tag and tag_version(tag) != version:
        err(
            f"the version is not in step: {VERSION_SOURCE} says {version}, "
            f"but the tag on HEAD is {tag}"
        )
        return 1
    out(f"{version} ({tag} on HEAD)" if tag else version)
    return 0


def _join(*parts):
    """`path.join`: the parts joined and normalised."""
    return os.path.normpath(os.path.join(*parts))


def cmd_unit_branch(unit_name, cos_dir, state, out, err):
    if not unit_name:
        err("usage: cos.mjs unit-branch <NNNN_slug>")
        return 2
    if not os.path.exists(_join(cos_dir, unit_name, "intent.md")):
        err(f"No such work unit: {unit_name}")
        return 2
    known = entry_of(state, dig(state, "workspace"), unit_name)
    made = branch_for(unit_name, nullish(dig(known, "type"), None))
    if made.get("error"):
        err(made["error"])
        return 1
    out(made["branch"])
    return 0


def cmd_pr_text(unit_name, cos_dir, state, out, err):
    """Reads `pr.md` and the `intent.md` whose `Type:` the title is checked against; prints."""
    if not unit_name:
        err("usage: cos.mjs pr-text <NNNN_slug>")
        return 2
    if not UNIT_RE.fullmatch(unit_name):
        err(f'Invalid unit name "{unit_name}": expected NNNN_slug.')
        return 2
    dir_ = _join(cos_dir, unit_name)
    if not os.path.exists(dir_):
        err(f"No such work unit: {unit_name}")
        return 1
    file = _join(dir_, "pr.md")
    if not os.path.exists(file):
        err(f"{unit_name} has no pr.md")
        return 1
    text = read_text(file)
    known = nullish(entry_of(state, dig(state, "workspace"), unit_name), NO_ENTRY)
    type_ = (
        None if not os.path.exists(_join(dir_, "intent.md")) else nullish(dig(known, "type"), None)
    )
    read = pr_text(text)
    problem = title_problem(read["title"], type_ if type_ in BRANCH_TYPES else None, unit_name[:4])
    status = status_in(known, "pr.md")
    out(
        stringify(
            {
                "unit": unit_name,
                **read,
                "status": None if status is UNDEFINED else status,
                "titleProblem": problem,
            }
        )
    )
    return 0


def run(args, out, err) -> int:
    word = args.rest[0] if args.rest else None
    match args.cmd:
        case "unit-branch":
            return cmd_unit_branch(word, args.cos_dir, args.state, out, err)
        case "pr-text":
            return cmd_pr_text(word, args.cos_dir, args.state, out, err)
        case "check-branch":
            return cmd_check_branch(word, out, err)
        case "check-tag":
            return cmd_check_tag(word, out, err)
        case _:
            return cmd_check_version(out, err)
