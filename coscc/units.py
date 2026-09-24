"""Where a workspace's work units live, and how a new one is started.

**The units are the product's, not the repository's.** `.cos/0013_.../intent.md` settled
that on 2026-09-22 in the originator's own words: a repository is a whole team's and this
loop is one person's, so nothing of coscc's goes into a shared repository's tree. Until
this module that was a decision written down; here it becomes the one enforced.

So a unit's artifacts sit under this app's data root, and the repository being worked on
receives only four things: the branch, the commits a step's own session makes to its code,
the pull request body, and — since `0021` — one review comment per review round on that
pull request. `scripts/verify_0014.py` claim 5 is the check for the tree; comments are not
in the tree, and `scripts/verify_0021.py` is the check for them.

**The review comment is a deliberate exception to `0013`/`0014`.** The pull request is
where the team reads, and a round that found a high-severity problem looked, there, exactly
like a round that found nothing (`0021` intent). Only text already in `review.md` goes out,
verbatim, as an ordinary comment the app posts under this machine's `gh` login
(`coscc/prcomment.py`). It is not an approval, and no gate reads it.

**Nothing here re-implements the loop.** `.claude/CLAUDE.md` says `cos.mjs` is the one
place it is defined and nothing may hold a second copy. Numbering a unit and validating a
slug are its job, and `create` below is a shell around `new-path` rather than a Python
version of it. The `--root` flag already exists for exactly this: `.claude/CLAUDE.md`
records that it is there so the app can read a `.cos/` elsewhere with this repository's
rules. This module only changes which directory that is.

**One function answers where units live, and that is the whole of R3.** `coscc/board.py`,
`coscc/runner.py` and `coscc/service.py` each computed a unit's path for themselves before
this. Three copies of one formula is precisely the shape of `0012`, where two modules each
worked out where `.claude/` was and one packaging omission arrived as two unrelated-looking
symptoms (`coscc/harness.py:45-48`).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence

from coscc import harness
from coscc.data import Data

# The directory `cos.mjs` reads, inside whatever root it is given.
COS_DIR = ".cos"

# Where the store sits under the data root. One level, named for what it holds.
UNITS_DIR = "units"

# `NNNN_slug`, the only shape `cos.mjs new-path` produces and the only one accepted back.
# An idea's name has the same shape (`cos.mjs new-idea`), in a sequence of its own.
UNIT_RE = re.compile(r"\d{4}_[a-z0-9]+(?:-[a-z0-9]+)*")
IDEA_RE = UNIT_RE

# Where ideas live inside `.cos/` (`0003_one-idea-is-trapped-inside-one-unit` R1).
IDEAS_DIR = "ideas"

# Seconds. `cos.mjs` reads files and prints one line; the timeout exists to turn a hung
# child into an error, not to bound the work -- the same argument as `coscc/board.py:38`.
TIMEOUT = 10.0

# How much of the workspace path's digest goes into the directory name. Twelve hex
# characters is 48 bits; these are names in one person's data directory, not a namespace
# anyone else writes into.
_DIGEST = 12


class BadUnit(ValueError):
    """A unit name, slug or workspace this module will not act on."""


class CannotCreate(RuntimeError):
    """A unit that could not be started, carrying what `cos.mjs` or the disk said."""


def key(workspace: str | os.PathLike[str]) -> str:
    """How a workspace is identified. The resolved path, and nothing else.

    The same convention `coscc/service.py:241-248` uses for the journal, and deliberately
    not a second one: a workspace declared by the environment has no name at all, and a
    path is the one identifier both kinds have. The cost is the same too — moving a
    workspace detaches its units from it.
    """
    return str(Path(workspace).expanduser().resolve())


def slot(workspace: str | os.PathLike[str]) -> str:
    """The directory name for one workspace: its basename, then a digest of its path.

    The identity is the path — the digest is a rendering of it, not a second identity.
    The basename is carried only so that a person looking in the data directory can tell
    which one is which; two workspaces with the same basename differ in the digest.
    """
    identity = key(workspace)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:_DIGEST]
    name = Path(identity).name or "workspace"
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", name)[:40]
    return f"{safe}-{digest}"


def root(workspace: str | os.PathLike[str], data_dir: str | os.PathLike[str] | None = None) -> Path:
    """The directory to hand `cos.mjs --root`. Its `.cos/` holds this workspace's units.

    `Data` turns the setting into a path, here as everywhere else — `coscc/data.py` is the
    only module allowed to do that, and borrowing it rather than expanding `~` again is
    what keeps that true.
    """
    return Data(data_dir).root / UNITS_DIR / slot(workspace)


def cos_dir(workspace: str | os.PathLike[str], data_dir: str | os.PathLike[str] | None = None) -> Path:
    return root(workspace, data_dir) / COS_DIR


def unit_dir(
    workspace: str | os.PathLike[str],
    unit: str,
    data_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """One unit's directory. The name is validated, never trusted.

    A unit name reaches this from a request. Checking its shape here means a caller cannot
    walk out of the store with one, whatever else it forgets.
    """
    if not UNIT_RE.fullmatch(unit or ""):
        raise BadUnit(f"not a work unit name: {unit!r}")
    return cos_dir(workspace, data_dir) / unit


def _cos(root_path: Path, *args: str) -> str:
    """Run `cos.mjs` against a root and return its stdout, or raise `CannotCreate`.

    The script that runs is **this app's copy**, never one found inside a workspace —
    `coscc/board.py:8-14` made that decision and `coscc/harness.py` is what keeps it true.
    Here it matters slightly less (the root is the app's own directory) and is kept
    identical anyway, because two answers to "which copy" is how one of them drifts.
    """
    script = harness.script()
    if not script.exists():
        raise CannotCreate(f"the harness script is missing: {script}")
    argv = ["node", str(script), "--root", str(root_path), *args]
    try:
        done = subprocess.run(
            argv,
            env=harness.child_env(),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except FileNotFoundError as e:
        raise CannotCreate(
            f"could not run node: {e} — PATH was {harness.child_env()['PATH']}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise CannotCreate(f"cos.mjs {' '.join(args)} did not finish in {TIMEOUT:.0f}s") from e
    if done.returncode != 0:
        # Its words, not ours. `new-path` explains a malformed slug better than a second
        # validator here could, and a second validator is what `intent.md` constraint 4
        # forbids.
        raise CannotCreate((done.stderr or done.stdout or "").strip() or "cos.mjs refused")
    return done.stdout.strip()


def branch_name(
    workspace: str | os.PathLike[str],
    unit: str,
    data_dir: str | os.PathLike[str] | None = None,
) -> str:
    """The branch this unit's `Type:` implies, from `cos.mjs unit-branch`.

    It reads `intent.md` from disk, so it cannot answer before the intent stage has run —
    and the refusal it raises says that, rather than this module guessing a name.
    """
    directory = unit_dir(workspace, unit, data_dir)  # validates before it reaches a command
    if not directory.is_dir():
        raise CannotCreate(f"no such work unit in this workspace: {unit}")
    if not (directory / "intent.md").is_file():
        # `cos.mjs unit-branch` answers `No such work unit` here, which is true of the
        # file it reads and false of the unit -- the directory is right there. Measured
        # 2026-09-22. Saying which of the two is missing is the difference between a
        # person fixing it and a person looking for a unit they just made.
        raise CannotCreate(
            f"{unit} has no intent.md yet, and the branch name comes from the Type: "
            "declared in it — run the intent stage first"
        )
    return _cos(root(workspace, data_dir), "unit-branch", unit)


def create(
    workspace: str | os.PathLike[str],
    slug: str,
    brief: str = "",
    data_dir: str | os.PathLike[str] | None = None,
    reserve_from: Sequence[str | os.PathLike[str]] = (),
    idea: str = "",
) -> dict[str, Any]:
    """Start a work unit: allocate the number, make the directory, link it to its idea.

    `reserve_from` names directories whose `.cos/` numbers count as taken — the host
    repository, from `coscc/service.py`. They reach `cos.mjs` as `--reserve-from`, which
    reads them; this module neither lists them nor compares a number. The flags go
    **before** `new-path` on purpose: an older `cos.mjs` would read a trailing flag as
    nothing and hand out a duplicate number silently, whereas a leading one is taken for the
    command, refused with exit 2, and arrives on the page as `CannotCreate`.

    `brief` is the originator's own words. Until `0003_one-idea-is-trapped-inside-one-unit`
    it was written as the unit's `idea.md`, which tied one idea to one unit; it is now an
    idea of its own, `.cos/ideas/NNNN_<slug>.md`, numbered by `cos.mjs new-idea`, and the
    unit is listed under its `## Units`. `idea` names an idea that already exists, and opens
    another unit from it the same way. One or the other, never both.

    The order of the writes is `plan.md`'s decision (`spec.md` C6): both numbers first, then
    the unit's directory, then the idea file in one write with its `## Units` already in
    it. If that write fails the empty directory is removed, so the only half-made state
    left is an empty, unlinked directory when the removal fails too — which `cos.mjs`
    reports as `no intent.md`, exactly as a unit made with no brief.
    """
    text = str(brief or "").strip()
    idea = str(idea or "").strip()
    if text and idea:
        raise BadUnit("send a brief or an idea, not both")
    store = root(workspace, data_dir)
    (store / COS_DIR).mkdir(parents=True, exist_ok=True)
    ideas = store / COS_DIR / IDEAS_DIR
    if idea:
        if not IDEA_RE.fullmatch(idea):
            raise CannotCreate(f"not an idea name: {idea!r}")
        if not (ideas / f"{idea}.md").is_file():
            raise CannotCreate(f"no such idea in this workspace: {IDEAS_DIR}/{idea}.md")

    reserve = [a for d in reserve_from for a in ("--reserve-from", str(Path(d).expanduser().resolve()))]
    idea_path: Path | None = ideas / f"{idea}.md" if idea else None
    if text:
        printed = _cos(store, *reserve, "new-idea", str(slug or "").strip())
        named = printed.splitlines()[-1].strip() if printed else ""
        idea_path = (store / named).resolve() if named else None
        if idea_path is None or idea_path.parent != (store / COS_DIR / IDEAS_DIR).resolve():
            raise CannotCreate(f"cos.mjs named an idea outside the store: {named!r}")
        if idea_path.suffix != ".md" or not IDEA_RE.fullmatch(idea_path.stem):
            raise CannotCreate(f"cos.mjs named something that is not an idea: {named}")

    printed = _cos(store, *reserve, "new-path", str(slug or "").strip())
    relative = printed.splitlines()[-1].strip() if printed else ""
    if not relative:
        raise CannotCreate("cos.mjs new-path printed nothing")

    # `new-path` prints a path **relative to the root** (measured 2026-09-22 with
    # `--root`: `.cos/0001_first-problem`). Joining is therefore right and checking the
    # join is not paranoia -- `plan.md` Risk 6 is this line.
    directory = (store / relative).resolve()
    if store.resolve() not in directory.parents:
        raise CannotCreate(f"cos.mjs named a path outside the store: {relative}")

    unit = directory.name
    if not UNIT_RE.fullmatch(unit):
        raise CannotCreate(f"cos.mjs named something that is not a unit: {unit}")

    directory.mkdir(parents=True, exist_ok=False)
    if idea_path is not None:
        try:
            if text:
                idea_path.parent.mkdir(parents=True, exist_ok=True)
                with idea_path.open("x", encoding="utf-8") as f:
                    f.write(_idea(idea_path.stem, text) + _units_section(unit))
            else:
                _append_unit(idea_path, unit)
        except OSError as e:
            try:
                directory.rmdir()
            except OSError:
                pass
            raise CannotCreate(f"could not record {unit} in {IDEAS_DIR}/{idea_path.name}: {e}") from e
    return {
        "unit": unit,
        "path": str(directory),
        "brief": bool(text),
        "idea": idea_path.stem if idea_path is not None else "",
    }


def host_unit_count(workspace: str | os.PathLike[str]) -> int:
    """How many directories in the host repository's own `.cos/` are named like units.

    Read-only, and names only: no file is opened, so a malformed unit over there still
    counts. It exists so the empty board can say *why* it is empty when the repository
    plainly is not (`0001_product-describes-a-state-it-is-not-in` R6). Counted on every
    call and never cached — that is R8, and the cost on a large `.cos/` is unmeasured.
    """
    directory = Path(key(workspace)) / COS_DIR
    try:
        return sum(1 for e in directory.iterdir() if e.is_dir() and _HOST_UNIT_RE.match(e.name))
    except OSError:
        return 0


# `^\d{4}_`, the spec's wording (R6). Looser than `UNIT_RE` on purpose: a directory whose
# slug the grammar would refuse is still a unit that somebody made there.
_HOST_UNIT_RE = re.compile(r"\d{4}_")


def _units_section(unit: str) -> str:
    return f"\n## Units\n\n- {unit}\n"


def _append_unit(path: Path, unit: str) -> None:
    """List one more unit under an idea's `## Units`, appending only.

    `spec.md ## Answers, câu 2`: `## Units` is the app's second way of writing into a file
    under `.cos/`, and like `## Answers` it only ever appends — nothing above is rewritten.
    When the file's last `## ` heading is `## Units`, the line goes under it; otherwise, a
    `## Answers` having been appended since, a new `## Units` section follows. `cos.mjs`
    reads every one of them.
    """
    raw = path.read_text(encoding="utf-8")
    headings = [ln.rstrip() for ln in raw.splitlines() if ln.startswith("## ")]
    sep = "" if raw.endswith("\n") or not raw else "\n"
    if headings and headings[-1] == "## Units":
        addition = f"{sep}- {unit}\n"
    else:
        addition = sep + _units_section(unit)
    with path.open("a", encoding="utf-8") as f:
        f.write(addition)


def _idea(name: str, brief: str) -> str:
    """The brief as an idea file the loop can read.

    `Status: accepted.` because these are the originator's words, and there is nothing for
    an agent to accept about them. `cos.mjs` accepts `draft`, `accepted` or `rejected` for
    an idea; no gate reads it (`0003_one-idea-is-trapped-inside-one-unit`).
    """
    title = name.split("_", 1)[-1].replace("-", " ")
    return (
        f"# Idea: {title}\n"
        f"Author: the originator. Status: accepted.\n\n"
        f"## In their own words\n\n{brief.strip()}\n"
    )
