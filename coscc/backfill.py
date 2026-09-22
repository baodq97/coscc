"""Turning a repository's git history of `.cos/` into transitions.

This is **a way of producing rows for the log, not a way of reading state.** The
distinction is the whole of `spec.md` C1 and `plan.md` Risk 1: it infers a state by
reading the `Status:` line of a blob, which is the mechanism R1 rejects — legitimate here
because it runs once, over history that has already happened, and its output is checked
against an independent count in `scripts/verify_0013.py`. If this ever became the way the
product answers "where is this unit now", R1 would have lost.

**It only reads.** `intent.md`'s amendment of 2026-09-22 settled that nothing of coscc's
goes into the target repository's tree, and this is the one component that touches somebody
else's checkout at all. Every command below is a read: `rev-parse`, `log`, `cat-file`.

**The commit author is deliberately not recorded as the actor.** Git knows who authored a
commit and this throws that away, which looks like losing information until you ask what
the field means. A transition's actor is whoever moved it; almost every artifact in this
repository was written by a Claude session and committed under one person's git identity,
so filling `actor` from the commit would assert a human did work an agent did. `spec.md` C1
chose `unknown` for exactly that reason, and `source` carries `commit:<sha>` so the author
is one command away for anyone who wants it.

**Renames are not followed, and that is a decision with a visible cost.** `f326765`
renumbered six units, so `.cos/0002_no-session-management/` became
`.cos/0001_no-session-management/`. With `--no-renames` the old path ends and the new one
begins, which means that unit's history is split across two names rather than being carried
over under a number it did not have at the time. The alternative — following renames — would
rewrite history under names that did not exist yet, and `.cos/RENAMES.md` is already the
place this repository records that mapping for people.

**Retired units are imported too.** `f506aae` deleted five units; their artifacts are gone
from the working tree but their history is not. Dropping them would be this unit's own
mistake repeated: keeping only what still exists is how 42 events went missing in the first
place. A caller that wants only the units present today filters on that; the log keeps both.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from coscc import states
from coscc.gitops import child_env
from coscc.history import UNKNOWN, History
from coscc.states import Machine

# The directory a unit's artifacts live in, inside the repository being read.
COS_DIR = ".cos"

# Seconds. Reading the whole history of one directory is bounded work, but an unbounded
# wait on a child process is how a page hangs instead of failing -- `coscc/board.py:41`
# makes the same argument for `cos.mjs`.
TIMEOUT = 120.0

# Record separators for `git log`. Start-of-heading begins a commit, unit separator splits
# its fields. Neither can appear in a path, a hash or a date, and `--name-status` output is
# otherwise line-oriented in a way a path containing a newline would break.
#
# Not NUL, which would be the obvious choice and cannot be used: it goes into the
# `--format=` argument, and an argv element may not contain one -- Python refuses it with
# `ValueError: embedded null byte` before git is even reached.
_COMMIT = "\x01"
_FIELD = "\x1f"


class NotAGitCheckout(RuntimeError):
    """The path given is not a git repository, or git could not be run.

    Its own type because `scripts/verify_0013.py` exits 2 on it: an environment that cannot
    answer is not a claim that came back false.
    """


def _git(repo: Path, *args: str, binary: bool = False, stdin: bytes | None = None):
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            env=child_env(),
            capture_output=True,
            input=stdin,
            timeout=TIMEOUT,
            check=True,
        )
    except FileNotFoundError as e:
        raise NotAGitCheckout(f"could not run git for {repo}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise NotAGitCheckout(
            f"git {' '.join(args)} in {repo} did not finish in {TIMEOUT:.0f}s"
        ) from e
    except subprocess.CalledProcessError as e:
        # The path goes in every message. `0012` paid for this lesson in a different
        # module: `coscc/board.py:104-115` says "could not run node" **and the PATH it
        # looked on**, because the message without it sends a reader looking for the wrong
        # thing. A refusal that does not name the directory it refused is the same bug.
        detail = (e.stderr or b"").decode("utf-8", "replace").strip()
        raise NotAGitCheckout(f"git {' '.join(args)} failed in {repo}: {detail}") from e
    return done.stdout if binary else done.stdout.decode("utf-8", "replace")


def require_checkout(repo: str | Path) -> Path:
    """The repository root containing `repo`, or `NotAGitCheckout` naming what went wrong.

    `--show-toplevel` rather than `--git-dir`, so that a path inside a checkout resolves to
    the checkout rather than being scanned as though `.cos/` sat beside it. The alternative
    is worse than an error: `--git-dir` succeeds from any subdirectory, so a caller who
    passed the wrong path would get an empty history and no complaint.
    """
    path = Path(repo).expanduser().resolve()
    if not path.is_dir():
        raise NotAGitCheckout(f"{path} is not a directory")
    top = _git(path, "rev-parse", "--show-toplevel").strip()
    if not top:
        raise NotAGitCheckout(f"{path} is not inside a git checkout")
    return Path(top).resolve()


def _utc(stamp: str) -> str:
    """One timestamp format for the whole table.

    `%aI` carries whatever offset the commit was made in, and this repository's history
    has more than one. Comparing those as text sorts them wrongly, so they are all moved
    to UTC at second resolution — the format `coscc/data.now()` writes.
    """
    try:
        return (
            datetime.fromisoformat(stamp)
            .astimezone(timezone.utc)
            .isoformat(timespec="seconds")
        )
    except ValueError:
        return stamp


def _commits(repo: Path, cos_dir: str) -> Iterator[tuple[str, str, list[tuple[str, str]]]]:
    """`(sha, when, [(letter, path), ...])` for every commit touching `cos_dir`, oldest first.

    `--no-renames` is load-bearing; see the module docstring. `--reverse` is what makes the
    resulting transitions chain in the order they happened, which is what lets the log
    derive each `from_state` from the row before it.
    """
    raw = _git(
        repo,
        "log",
        "--reverse",
        "--no-renames",
        f"--format={_COMMIT}%H{_FIELD}%aI",
        "--name-status",
        "--",
        cos_dir,
    )
    for block in raw.split(_COMMIT):
        block = block.strip("\n")
        if not block:
            continue
        head, _, rest = block.partition("\n")
        sha, _, when = head.partition(_FIELD)
        changes = []
        for line in rest.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            changes.append((parts[0][:1], parts[-1]))
        yield sha.strip(), _utc(when.strip()), changes


def _split(path: str, cos_dir: str, machine: Machine) -> tuple[str, str] | None:
    """`(unit, artifact)` for a path this state set recognises, else `None`.

    Anything that is not `<cos_dir>/<unit>/<artifact>` is skipped, which is what keeps
    `.cos/RENAMES.md` out of the log without naming it here.
    """
    parts = Path(path).parts
    if len(parts) != 3 or parts[0] != cos_dir:
        return None
    unit, artifact = parts[1], parts[2]
    return (unit, artifact) if machine.knows(artifact) else None


def _blobs(repo: Path, wanted: list[str]) -> dict[str, str]:
    """`{"<sha>:<path>": text}` for many blobs, in one child process.

    `git show` per blob is the obvious way and it is 103 processes on this repository.
    `cat-file --batch` speaks a small protocol instead: a request line, then a header
    `<oid> <type> <size>`, then exactly `size` bytes and a newline. A missing object
    answers `<request> missing`, which is an ordinary case here — a commit that deleted a
    file has no blob for it.
    """
    if not wanted:
        return {}
    payload = ("\n".join(wanted) + "\n").encode("utf-8")
    raw = _git(repo, "cat-file", "--batch", binary=True, stdin=payload)

    out: dict[str, str] = {}
    at = 0
    for request in wanted:
        end = raw.find(b"\n", at)
        if end < 0:
            break
        header = raw[at:end].decode("utf-8", "replace")
        at = end + 1
        if header.endswith(" missing") or header.endswith(" ambiguous"):
            continue
        try:
            size = int(header.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            break
        out[request] = raw[at : at + size].decode("utf-8", "replace")
        at += size + 1
    return out


def _status_of(text: str, machine: Machine, artifact: str) -> str | None:
    """The state this blob declares, or `None` when it declares none this set knows.

    Deliberately the same shape of read as `.claude/scripts/cos.mjs:49-55`: the first
    `Status:` in the file, case-insensitively. It is not the same code, and it cannot be —
    that one is JavaScript. Measured 2026-09-22: all 103 artifact blobs in this
    repository's history carry a status this set accepts, so `None` has no instances here
    and exists for repositories that are not this one.
    """
    for line in text.splitlines():
        marker = line.lower().find("status:")
        if marker < 0:
            continue
        word = line[marker + len("status:") :].strip().split()
        if not word:
            continue
        candidate = word[0].strip(". ").lower()
        if machine.allows(artifact, candidate):
            return candidate
        return None
    return None


def scan(
    repo: str | Path,
    machine: Machine | None = None,
    cos_dir: str = COS_DIR,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Every transition git can see, oldest first. Reads; writes nothing anywhere.

    Each row is shaped for `History.record_many`, and deliberately carries **no**
    `from_state`: the log derives that from the row before it, so the chain stays
    consistent with what was actually stored even if this is run twice or interrupted.
    """
    machine = machine or states.default()
    root = require_checkout(repo)
    key = workspace if workspace is not None else str(root)

    pending: list[tuple[str, str, str, str, str]] = []  # sha, when, path, unit, artifact
    wanted: list[str] = []
    for sha, when, changes in _commits(root, cos_dir):
        for letter, path in changes:
            split = _split(path, cos_dir, machine)
            if split is None:
                continue
            unit, artifact = split
            pending.append((sha, when, path, unit, artifact))
            if letter != "D":
                wanted.append(f"{sha}:{path}")

    blobs = _blobs(root, wanted)

    rows: list[dict[str, Any]] = []
    for sha, when, path, unit, artifact in pending:
        request = f"{sha}:{path}"
        if request in blobs:
            state = _status_of(blobs[request], machine, artifact)
            if state is None:
                # A file this set cannot read a state from. Skipped rather than guessed:
                # inventing a state here would put a number into the log that no command
                # can contradict.
                continue
        else:
            # No blob at that commit: the artifact was deleted. Leaving nothing is a
            # transition like any other, and it is the only way a retired unit reads as
            # retired rather than as frozen at its last status.
            state = machine.absent
        rows.append(
            {
                "workspace": key,
                "unit": unit,
                "artifact": artifact,
                "to_state": state,
                "actor": UNKNOWN,
                "session": UNKNOWN,
                "source": f"commit:{sha}",
                "at": when,
                "once_key": f"git:{key}:{sha}:{path}",
            }
        )
    return rows


def run(
    history: History, repo: str | Path, cos_dir: str = COS_DIR, workspace: str | None = None
) -> dict[str, Any]:
    """Scan and store. Returns what it found and what it actually added.

    `added` is lower than `scanned` on a second run and zero on a third, because every row
    carries a `once_key` derived from its commit and path. That is what makes this safe to
    run again after an interruption, which `spec.md` open question 4 left open and this
    answers: it is re-runnable, and running it twice is not how the log doubles.
    """
    root = require_checkout(repo)
    key = workspace if workspace is not None else str(root)
    rows = scan(root, history.machine, cos_dir, workspace=key)
    stored = history.record_many(rows)
    return {
        "repo": str(root),
        "workspace": key,
        "scanned": len(rows),
        "added": len(stored),
        "units": sorted({row["unit"] for row in rows}),
    }
