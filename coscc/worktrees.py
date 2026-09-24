"""Where a unit's working tree is, and the one place that makes, prepares and removes it.

`0017`. Every unit of a workspace used to share the workspace's one working tree, so
cutting one unit's branch took another unit's branch away from under it (measured
2026-09-23: `0016`'s intent ran while the tree stood on `0015`'s branch). Now each unit
gets a `git worktree` of its own, outside the workspace, and **the workspace itself stays
on `main` and is never worked in** (`0017` `intent.md ## Answers, câu 5`).

What goes where:

- **Artifacts stay in the store** (`coscc/units.py`). A worktree holds code only, so
  `cos.mjs` is asked with `--root <store> --repo <worktree>` — the store for the unit,
  the worktree for its branch and pull request. `0017` `plan.md` records why this departs
  from the spec, which wrote as if artifacts were in the repository.
- **The tree** is `<data root>/worktrees/<slot>/<unit>`: a pure function of the workspace
  and the unit, found again through `git worktree list`, never through a table this app
  keeps. It can never be inside the workspace or inside the installed package.
- **What preparing it said** is `<tree>.prepare.json`, beside the tree rather than in it,
  so it is not a file in anybody's `git status`.

Nothing here decides a stage: whether a unit is `finished` comes from `cos.mjs`.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any, Awaitable, Callable

import asyncio

import coscc
from coscc import fetches, gitops, prcomment, units
from coscc.data import Data
from coscc.frontend import WEB_WORKDIR_VAR
from coscc.gitops import GitError
from coscc.units import BadUnit

WORKTREES_DIR = "worktrees"

# Seconds, per preparing command. **Chosen, not measured**: `0017` spec Concern 8 records
# that there is no source for what a `uv sync` or an `npm ci` costs here. It exists to turn
# a hung install into a reported failure, not to bound an ordinary one.
PREPARE_TIMEOUT = 600.0

# How much of a failed command's output is kept for the page. Chosen, not measured.
TAIL_CHARS = 2000


class Unprepared(Exception):
    """A worktree whose preparation failed. Carries the command and its exit code."""


def _package_dir() -> Path:
    return Path(coscc.__file__).resolve().parent


def path(workspace: str | os.PathLike[str], unit: str, data_dir: str | os.PathLike[str] | None = None) -> Path:
    """The unit's worktree. Deterministic in (workspace, unit); refused anywhere unsafe."""
    if not units.UNIT_RE.fullmatch(unit or ""):
        raise BadUnit(f"not a work unit name: {unit!r}")
    where = (Data(data_dir).root / WORKTREES_DIR / units.slot(workspace) / unit).resolve()
    for forbidden in (Path(units.key(workspace)), _package_dir()):
        if where == forbidden or forbidden in where.parents:
            raise BadUnit(f"a unit's worktree would land inside {forbidden}: {where}")
    return where


def prepare_record(tree: Path) -> Path:
    return tree.parent / f"{tree.name}.prepare.json"


def read_prepare(tree: Path) -> dict[str, Any] | None:
    """What the last preparation of this tree said, or None if it was never prepared."""
    try:
        return json.loads(prepare_record(tree).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def find(workspace: str | os.PathLike[str], unit: str, data_dir: str | os.PathLike[str] | None = None) -> dict[str, str] | None:
    """The unit's worktree as git lists it (`path`, `branch`, `head`), or None."""
    want = path(workspace, unit, data_dir)
    root = Path(units.key(workspace))
    for tree in await gitops.worktree_list(root):
        if Path(tree["path"]).resolve() == want:
            return tree
    return None


async def _branch_exists(root: Path, name: str) -> bool:
    try:
        await gitops.rev_parse(root, f"refs/heads/{name}")
        return True
    except GitError:
        return False


async def _fetch_or_refuse(where_repo: Path, branch: str) -> dict[str, Any]:
    """Fetch `origin/main` in `where_repo`, refusing to go on when that fails.

    `0030` review round 1, F2. Neither of `ensure`'s two "open onto an existing branch"
    paths cuts `branch` — it was already cut at a terminal, the way `.claude/CLAUDE.md`
    step 4 still does it — so the reason to refuse here is not that a stale `main` would
    name the wrong pull request base (only `start_branch`, which does cut a branch, has
    that reason). It is narrower: `câu 1` asks every path that opens a tree onto an
    existing branch to agree on when doing so is safe, and a fetch that failed is the one
    thing both paths can check for without guessing. So both call this, and neither
    proceeds past it — whether or not the unit already had a (still detached) tree is not
    a reason for the two to disagree.

    Since `0048` the fetch goes through `fetches`, and what it returns — `{outcome,
    attempts, age}` — comes back for `_base_against_origin` to carry.
    """
    try:
        return await fetches.fetch(where_repo)
    except GitError as e:
        raise GitError(
            f"Could not update {gitops.TRUNK} from origin, so {branch} was not opened "
            f"in this unit's worktree. Nothing in the repository changed. git said: {e}"
        ) from e


async def _base_against_origin(
    where_repo: Path, branch: str, branch_sha: str, fetched: dict[str, Any]
) -> dict[str, Any]:
    """`{ref, sha, fresh, behind, reason, fetch}` for `branch_sha` against `origin/main`.

    Never refuses and never rebases (`intent.md ## Answers, câu 3`): a branch behind is
    reported, not fixed — `gh pr update-branch --rebase` is what the `reason` points to.
    `fetch` is what `_fetch_or_refuse` returned, and `fresh` also needs it to be younger
    than `fetches.REUSE_SECONDS` (`0048` R7).
    """
    origin_ref = f"origin/{gitops.TRUNK}"
    origin_sha = await gitops.rev_parse(where_repo, f"refs/remotes/{origin_ref}")
    behind = await gitops.count_missing(where_repo, branch_sha, origin_sha)
    if behind:
        reason = (
            f"{branch} is missing {behind} commit(s) from {origin_ref}; "
            "see `gh pr update-branch --rebase`."
        )
    else:
        reason = _stale(fetched)
    return {
        "ref": origin_ref,
        "sha": origin_sha[:7],
        "fresh": not reason,
        "behind": behind,
        "reason": reason,
        "fetch": fetched,
    }


def _stale(fetched: dict[str, Any]) -> str:
    """Empty when the fetch behind a `sha` is young enough to call it fresh (`0048` R7)."""
    if fetched["age"] < fetches.REUSE_SECONDS:
        return ""
    return (
        f"the fetch of origin/{gitops.TRUNK} this reads began {fetched['age']}s ago, "
        f"not under {fetches.REUSE_SECONDS:.0f}s, so it may not be the remote's tip"
    )


async def ensure(
    workspace: str | os.PathLike[str],
    unit: str,
    branch: str | None,
    data_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """The unit's worktree, made if it is not there yet.

    Returns `{path, branch, created, switched}`, and also `base` when this call is the one
    that opened the tree onto an existing branch (`_base_against_origin`). `switched` is
    true when the workspace was moved back to `main` to make this possible — the one thing
    here that touches the workspace's own tree, done only when that tree is clean (`0017`
    spec, câu 2) and only once the fetch this call needed has already succeeded (`0030`
    review round 2, F5) — so a refusal that follows never has to explain a workspace that
    already moved.

    Raises `GitError` with a reason a person can act on when opening onto an existing
    branch needed a fetch that failed (`_fetch_or_refuse`), or when the workspace is dirty
    and standing on the branch this unit needs.
    """
    root = Path(units.key(workspace))
    where = path(workspace, unit, data_dir)
    found = await find(workspace, unit, data_dir)
    wanted = bool(branch) and await _branch_exists(root, branch)
    if found is not None and (found["branch"] or not wanted):
        return {"path": str(where), "branch": found["branch"], "created": False, "switched": False}

    if found is not None:
        # A detached tree made when the unit was created, before it had a branch. Fetch
        # inside the tree first — it is a separate directory from the workspace, so this
        # never touches the workspace — and only once it has either succeeded or found
        # nothing to move does `_step_aside` touch the workspace (F5).
        tree = Path(found["path"])
        fetched = await _fetch_or_refuse(tree, branch)
        switched = await _step_aside(root, branch)
        await gitops.switch_existing(tree, branch)
        branch_sha = await gitops.rev_parse(tree, "HEAD")
        base = await _base_against_origin(tree, branch, branch_sha, fetched)
        return {
            "path": str(where), "branch": branch, "created": False, "switched": switched,
            "base": base,
        }

    where.parent.mkdir(parents=True, exist_ok=True)
    if wanted:
        # No tree at all yet, but the branch already exists — same situation as the block
        # above except that this unit never had a (detached) tree to fetch inside, so the
        # fetch runs in the workspace instead. `_fetch_or_refuse` is the same call either
        # way, which is the point (F2); it still runs before `_step_aside` touches the
        # workspace, for the same reason (F5).
        fetched = await _fetch_or_refuse(root, branch)
        switched = await _step_aside(root, branch)
        await gitops.worktree_add(root, where, branch)
        branch_sha = await gitops.rev_parse(root, f"refs/heads/{branch}")
        base = await _base_against_origin(root, branch, branch_sha, fetched)
        found = await find(workspace, unit, data_dir) or {"branch": ""}
        return {
            "path": str(where), "branch": found["branch"], "created": True, "switched": switched,
            "base": base,
        }
    sha = await gitops.rev_parse(root, f"refs/heads/{gitops.TRUNK}")
    await gitops.worktree_add(root, where, sha)
    found = await find(workspace, unit, data_dir) or {"branch": ""}
    return {"path": str(where), "branch": found["branch"], "created": True, "switched": False}


async def _step_aside(root: Path, branch: str) -> bool:
    """Move the workspace to `main` when it is standing on `branch`, freeing `branch` for
    a unit's worktree. `False` when the workspace was standing on something else already.

    `0030` review round 2, F5: `ensure` calls this only after the fetch it needed has
    already succeeded. Before this fix, `switch_trunk` ran first, so a fetch that then
    failed left the workspace on `main` anyway while `_fetch_or_refuse` still raised
    "Nothing in the repository changed" — true of the rest of the repository, but no
    longer of the workspace's checked-out branch. Calling this last keeps that sentence
    true: everything that can still refuse has refused before the one mutation here runs.
    """
    if await gitops.current_branch(root) != branch:
        return False
    if not await gitops.is_clean(root):
        raise GitError(
            f"{root} is on {branch}, this unit's branch, and has uncommitted changes, "
            "so its worktree cannot be opened. Commit or stash them there, then "
            f"`git switch {gitops.TRUNK}`."
        )
    await gitops.switch_trunk(root)
    return True


async def refresh_base(
    workspace: str | os.PathLike[str],
    unit: str,
    data_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Bring a unit's still-detached tree to the fetched tip of `origin/main`, and say so.

    `0030` R1, R5, R6. Unlike `ensure`, this never raises: a step on a detached tree runs
    whether or not the fetch succeeds, and this is the one place that decides what to say
    about it (`intent.md ## Answers, câu 2`). Returns `{ref, sha, fresh, reason}` — `sha`
    is the short remote tip once known, `fresh` is whether the tree ended up there, and
    `reason` is empty exactly when `fresh` is true.

    When the tree is actually moved, the record `prepare()` left is deleted: it describes
    a tree at the commit it was prepared at, and that commit just changed (R6).

    Since `0048` every answer also carries `fetch`, `{outcome, attempts, age}` from
    `fetches` — `failed` with `age` None when there was no fetch to show — and `fresh`
    also needs that fetch to be younger than `fetches.REUSE_SECONDS` (R6, R7).
    """
    ref = f"origin/{gitops.TRUNK}"
    found = await find(workspace, unit, data_dir)
    if found is None:
        return {
            "ref": ref, "sha": "", "fresh": False, "reason": "no worktree to refresh",
            "fetch": {"outcome": "failed", "attempts": 0, "age": None},
        }
    tree = Path(found["path"])
    try:
        fetched = await fetches.fetch(tree)
    except fetches.FetchFailed as e:
        return {
            "ref": ref, "sha": "", "fresh": False, "reason": str(e),
            "fetch": {"outcome": "failed", "attempts": e.attempts, "age": None},
        }
    try:
        sha = await gitops.rev_parse(tree, f"refs/remotes/{ref}")
    except GitError as e:
        return {"ref": ref, "sha": "", "fresh": False, "reason": str(e), "fetch": fetched}
    try:
        before = await gitops.rev_parse(tree, "HEAD")
    except GitError as e:
        return {"ref": ref, "sha": sha[:7], "fresh": False, "reason": str(e), "fetch": fetched}
    if before != sha:
        try:
            await gitops.advance_detached(tree, sha)
        except GitError as e:
            return {"ref": ref, "sha": sha[:7], "fresh": False, "reason": str(e), "fetch": fetched}
        try:
            prepare_record(tree).unlink()
        except OSError:
            pass
    stale = _stale(fetched)
    return {"ref": ref, "sha": sha[:7], "fresh": not stale, "reason": stale, "fetch": fetched}


# --- preparing ---------------------------------------------------------------


def commands(tree: Path) -> list[list[str]]:
    """What makes this tree able to run its tests, guessed from the files in it.

    `0017` `spec.md ## Answers, câu 1`: *"có `uv.lock` thì `uv sync --frozen`, có
    `package-lock.json` thì `npm ci`, và build frontend nếu repository có"*. The frontend
    build is recognised by one readable sign — `coscc-build` under `[project.scripts]`
    (`pyproject.toml:38-45`) — so this is only ever checked against this repository.
    """
    out: list[list[str]] = []
    if (tree / "uv.lock").is_file():
        out.append(["uv", "sync", "--frozen"])
    if (tree / "package-lock.json").is_file():
        out.append(["npm", "ci"])
    try:
        project = tomllib.loads((tree / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        project = {}
    if "coscc-build" in ((project.get("project") or {}).get("scripts") or {}):
        out.append(["uv", "run", "coscc-build"])
    return out


def _outside(entry: str, roots: list[Path]) -> bool:
    try:
        p = Path(entry).resolve()
    except (OSError, ValueError):
        return False
    return not any(p == r or r in p.parents for r in roots)


def clean_path(workspace: str | os.PathLike[str] | None) -> str:
    """`PATH` without any entry under the workspace or the installed package.

    An entry under the workspace is usually its `.venv/bin`, and a command run for a unit
    that finds the workspace's interpreter first is running the workspace's code.
    """
    roots = [_package_dir()]
    if workspace:
        roots.append(Path(units.key(workspace)))
    parts = [e for e in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep) if e]
    return os.pathsep.join(e for e in parts if _outside(e, roots))


def prepare_env(tree: Path, workspace: str | os.PathLike[str] | None) -> dict[str, str]:
    """The environment a preparing command runs in. Built from nothing.

    `VIRTUAL_ENV` and `REFLEX_WEB_WORKDIR` point into the tree: `0014`'s lesson is that a
    build reading this process's `REFLEX_WEB_WORKDIR` compiles into the installed package.

    No `CLAUDE*`, `ANTHROPIC*`, `COS_*` or `__REFLEX_*` name reaches the command because
    none of the five names below is one — not because anything filters. A sixth name added
    here is the only way one could; `worktrees_test.py` asserts on the result with those
    names set in this process. (Until the `0017` review, F5, a filter loop ran over these
    five and could never remove anything.)
    """
    return {
        "PATH": clean_path(workspace),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LC_ALL": "C.UTF-8",
        "VIRTUAL_ENV": str(tree / ".venv"),
        WEB_WORKDIR_VAR: str(tree / ".web"),
    }


Run = Callable[[list[str], Path, dict[str, str]], Awaitable[tuple[int, str]]]


async def _run(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=PREPARE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"did not finish in {PREPARE_TIMEOUT:.0f}s"
    return proc.returncode or 0, (out or b"").decode(errors="replace")


async def prepare(
    tree: Path, workspace: str | os.PathLike[str] | None = None, run: Run | None = None
) -> dict[str, Any]:
    """Run `commands(tree)` in order, stop at the first that fails, and record the result.

    Returns and writes `{ok, command, exit_code, tail, commands}`. `command` is the one
    that failed, or empty. Never raises for a failing command: R6 wants it *shown*.
    """
    tree = Path(tree)
    run = run or _run
    todo = commands(tree)
    result: dict[str, Any] = {
        "ok": True, "command": "", "exit_code": 0, "tail": "",
        "commands": [" ".join(c) for c in todo],
    }
    env = prepare_env(tree, workspace)
    for argv in todo:
        try:
            code, out = await run(argv, tree, env)
        except FileNotFoundError as e:
            code, out = 127, f"{argv[0]} is not installed or not on PATH: {e}"
        except OSError as e:
            code, out = 126, f"could not run {argv[0]}: {e}"
        if code != 0:
            result.update(ok=False, command=" ".join(argv), exit_code=code, tail=out[-TAIL_CHARS:])
            break
    try:
        prepare_record(tree).write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError:
        pass
    return result


def describe_failure(result: dict[str, Any]) -> str:
    return (
        f"preparing the worktree failed: `{result.get('command')}` exited "
        f"{result.get('exit_code')}\n{result.get('tail') or ''}".rstrip()
    )


# --- removing (`0017` R10) ---------------------------------------------------

GhRun = Callable[[list[str], str, str | None], Awaitable[tuple[int, str, str]]]


async def remove_if_finished(
    workspace: str | os.PathLike[str],
    unit: str,
    board_unit: dict[str, Any],
    data_dir: str | os.PathLike[str] | None = None,
    gh: GhRun | None = None,
) -> dict[str, Any]:
    """Remove a finished unit's worktree and its local branch. Never raises.

    All four must hold, or nothing is touched:

    1. `cos.mjs` says the unit is `finished` (read from `board_unit`, never inferred);
    2. `gh pr view` in the worktree says the pull request is `MERGED`;
    3. the worktree is clean;
    4. where the local branch still exists, it points at the head GitHub merged.

    Returns `{removed, reason}`.
    """
    gh = gh or prcomment._gh
    try:
        if str(board_unit.get("next") or "") != "finished":
            return {"removed": False, "reason": "not finished"}
        found = await find(workspace, unit, data_dir)
        if found is None:
            return {"removed": False, "reason": "no worktree"}
        tree = Path(found["path"])
        pr_url = str((board_unit.get("pr") or {}).get("url") or "")
        if not prcomment.PR_URL_RE.fullmatch(pr_url):
            return {"removed": False, "reason": "no pull request url"}
        # Before `gh`, not after: `board()` calls this on every read, and a dirty tree
        # would otherwise cost a network call each time (`0017` review, F4).
        if not await gitops.is_clean(tree):
            return {"removed": False, "reason": "worktree has uncommitted changes"}
        said = await prcomment._call(
            gh, ["pr", "view", pr_url, "--json", "state,headRefOid"], str(tree), None
        )
        if isinstance(said, str):
            return {"removed": False, "reason": said}
        code, out, err = said
        if code != 0:
            return {"removed": False, "reason": prcomment._said(code, out, err)}
        view = json.loads(out or "{}")
        if view.get("state") != "MERGED":
            return {"removed": False, "reason": f"pull request is {view.get('state')}"}
        merged = str(view.get("headRefOid") or "")
        root = Path(units.key(workspace))
        branch = found.get("branch") or ""
        if branch and found.get("head") != merged:
            return {"removed": False, "reason": f"{branch} is not at the merged head"}
        await gitops.worktree_remove(root, tree)
        deleted = False
        if branch:
            deleted = await gitops.delete_merged_branch(root, branch, merged)
        try:
            prepare_record(tree).unlink()
        except OSError:
            pass
        return {"removed": True, "reason": "", "branch_deleted": deleted}
    except (GitError, BadUnit, ValueError, OSError) as e:
        return {"removed": False, "reason": str(e)}
