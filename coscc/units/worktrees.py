"""Where a unit's working tree is, and the one place that makes, prepares and removes it.

Each unit gets a `git worktree` outside the workspace; **the workspace itself stays on
`main` and is never worked in**. Artifacts stay in the store, so `cos.mjs` is asked with
`--root <store> --repo <worktree>`. The tree is `<data root>/worktrees/<slot>/<unit>`, a
pure function of workspace and unit, found again through `git worktree list`. What
preparing it said is `<tree>.prepare.json`, beside the tree so it is not in `git status`.
Nothing here decides a stage.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any, Awaitable, Callable

import asyncio

import coscc
from coscc import config, units
from coscc.agent import harness
from coscc.git import fetches, gh, gitops
from coscc.data import Data
from coscc.frontend import WEB_WORKDIR_VAR
from coscc.git.gitops import GitError
from coscc.units import BadUnit

WORKTREES_DIR = "worktrees"

# # Seconds, per preparing command. Chosen, not measured; it turns a hung install into a
# # reported failure.
PREPARE_TIMEOUT = 600.0

# # How much of a failed command's output is kept for the page.
TAIL_CHARS = 2000


def _package_dir() -> Path:
    return Path(coscc.__file__).resolve().parent


def path(
    workspace: str | os.PathLike[str], unit: str, data_dir: str | os.PathLike[str] | None = None
) -> Path:
    """The unit's worktree. Deterministic in (workspace, unit); refused anywhere unsafe."""
    if not units.UNIT_RE.fullmatch(unit or ""):
        raise BadUnit(f"not a work unit name: {unit!r}")
    where = (Data(data_dir).root / WORKTREES_DIR / units.slot(workspace) / unit).resolve()
    for forbidden in (Path(units.key(workspace)), _package_dir()):
        if where == forbidden or forbidden in where.parents:
            raise BadUnit(f"a unit's worktree would land inside {forbidden}: {where}")
    return where


RELEASE_TREE = gitops.RELEASE_TREE


def release_path(
    workspace: str | os.PathLike[str], data_dir: str | os.PathLike[str] | None = None
) -> Path:
    """The workspace's one release worktree, beside its units' trees. `release` is not a
    `NNNN_slug`, so no unit's tree can be named the same.
    """
    where = (Data(data_dir).root / WORKTREES_DIR / units.slot(workspace) / RELEASE_TREE).resolve()
    for forbidden in (Path(units.key(workspace)), _package_dir()):
        if where == forbidden or forbidden in where.parents:
            raise BadUnit(f"the release worktree would land inside {forbidden}: {where}")
    return where


def prepare_record(tree: Path) -> Path:
    return tree.parent / f"{tree.name}.prepare.json"


def read_prepare(tree: Path) -> dict[str, Any] | None:
    """What the last preparation of this tree said, or None if it was never prepared."""
    try:
        return json.loads(prepare_record(tree).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None


async def find(
    workspace: str | os.PathLike[str], unit: str, data_dir: str | os.PathLike[str] | None = None
) -> dict[str, str] | None:
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

    Both paths that open a tree onto an existing branch call this and agree on when that is
    safe. Returns the `fetches` result `{outcome, attempts, age}` for `_base_against_origin`.
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

    Never refuses and never rebases: a branch behind is reported, and `gh pr update-branch
    --rebase` is what `reason` points to. `fresh` also needs `fetch` younger than
    `fetches.REUSE_SECONDS`.
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
    """Empty when the fetch behind a `sha` is young enough to call it fresh."""
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

    Returns `{path, branch, created, switched}`, and also `base` when this call opened the tree
    onto an existing branch. `switched` is true when the workspace was moved back to `main`,
    the one touch of the workspace's own tree, only when clean and only after the needed fetch
    has succeeded, so a later refusal never follows a workspace that already moved.

    Raises `GitError` when the needed fetch failed, or the workspace is dirty and standing on
    the branch this unit needs.
    """
    root = Path(units.key(workspace))
    where = path(workspace, unit, data_dir)
    found = await find(workspace, unit, data_dir)
    wanted = bool(branch) and await _branch_exists(root, branch)
    if found is not None and (found["branch"] or not wanted):
        return {"path": str(where), "branch": found["branch"], "created": False, "switched": False}

    if found is not None and branch:
        # A detached tree made before the unit had a branch. Fetch inside the tree first (a separate
        # directory from the workspace); only after that succeeds or finds nothing to move does
        # `_step_aside` touch the workspace.
        tree = Path(found["path"])
        fetched = await _fetch_or_refuse(tree, branch)
        switched = await _step_aside(root, branch)
        await gitops.switch_existing(tree, branch)
        branch_sha = await gitops.rev_parse(tree, "HEAD")
        base = await _base_against_origin(tree, branch, branch_sha, fetched)
        return {
            "path": str(where),
            "branch": branch,
            "created": False,
            "switched": switched,
            "base": base,
        }

    where.parent.mkdir(parents=True, exist_ok=True)
    if wanted:
        # No tree yet but the branch exists: the fetch runs in the workspace, through the same
        # `_fetch_or_refuse`, and still before `_step_aside`.
        fetched = await _fetch_or_refuse(root, branch)
        switched = await _step_aside(root, branch)
        await gitops.worktree_add(root, where, branch)
        branch_sha = await gitops.rev_parse(root, f"refs/heads/{branch}")
        base = await _base_against_origin(root, branch, branch_sha, fetched)
        found = await find(workspace, unit, data_dir) or {"branch": ""}
        return {
            "path": str(where),
            "branch": found["branch"],
            "created": True,
            "switched": switched,
            "base": base,
        }
    sha = await gitops.rev_parse(root, f"refs/heads/{gitops.TRUNK}")
    await gitops.worktree_add(root, where, sha)
    found = await find(workspace, unit, data_dir) or {"branch": ""}
    return {"path": str(where), "branch": found["branch"], "created": True, "switched": False}


async def _step_aside(root: Path, branch: str) -> bool:
    """Move the workspace to `main` when it is standing on `branch`, freeing `branch` for a
    unit's worktree. `False` when the workspace was standing on something else.

    Called only after the fetch has succeeded, so everything that can still refuse has refused
    before this one mutation runs.
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

    Never raises: a step on a detached tree runs whether or not the fetch succeeds. Returns
    `{ref, sha, fresh, reason, fetch}`: `sha` is the short remote tip once known, `fresh` is
    whether the tree ended up there (and the fetch is younger than `fetches.REUSE_SECONDS`),
    `reason` is empty exactly when `fresh`, `fetch` is `failed` with `age` None when there was
    none. When the tree moves, the record `prepare()` left is deleted, as it describes the old
    commit.
    """
    ref = f"origin/{gitops.TRUNK}"
    found = await find(workspace, unit, data_dir)
    if found is None:
        return {
            "ref": ref,
            "sha": "",
            "fresh": False,
            "reason": "no worktree to refresh",
            "fetch": {"outcome": "failed", "attempts": 0, "age": None},
        }
    tree = Path(found["path"])
    try:
        fetched = await fetches.fetch(tree)
    except fetches.FetchFailed as e:
        return {
            "ref": ref,
            "sha": "",
            "fresh": False,
            "reason": str(e),
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


# # --- preparing ---------------------------------------------------------------


def commands(tree: Path) -> list[list[str]]:
    """What makes this tree able to run its tests, guessed from the files in it.

    `uv.lock` gives `uv sync --frozen`, `package-lock.json` gives `npm ci`, and the frontend is
    built when `coscc-build` is under `[project.scripts]`.
    """
    out: list[list[str]] = []
    if (tree / "uv.lock").is_file():
        out.append(["uv", "sync", "--frozen"])
    if (tree / "package-lock.json").is_file():
        out.append(["npm", "ci"])
    try:
        project = tomllib.loads((tree / "pyproject.toml").read_text(encoding="utf-8"))
    except OSError, ValueError:
        project = {}
    if "coscc-build" in ((project.get("project") or {}).get("scripts") or {}):
        out.append(["uv", "run", "coscc-build"])
    return out


def prepare_env(
    tree: Path,
    workspace: str | os.PathLike[str] | None,
    data_dir: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """The environment a preparing command runs in. Built from nothing.

    `VIRTUAL_ENV` and `REFLEX_WEB_WORKDIR` point into the tree: a build reading this process's
    `REFLEX_WEB_WORKDIR` compiles into the installed package. `config.PROTECTED_DB_VAR` names
    this app's `cos.db` so a `Data` in the branch's install scripts refuses to open it.
    `data_dir` is the app's data root, `None` meaning `~/.cos`.

    No `CLAUDE*`, `ANTHROPIC*`, `COS_*` or `__REFLEX_*` name reaches the command because none of
    the names below is one; nothing filters. `test_worktrees.py` asserts this.
    """
    return {
        "PATH": harness.clean_path(workspace),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LC_ALL": "C.UTF-8",
        "VIRTUAL_ENV": str(tree / ".venv"),
        WEB_WORKDIR_VAR: str(tree / ".web"),
        config.PROTECTED_DB_VAR: config.protect(Data(data_dir).db_path),
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
    tree: Path,
    workspace: str | os.PathLike[str] | None = None,
    run: Run | None = None,
    data_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Run `commands(tree)` in order, stop at the first that fails, and record the result.

    Returns and writes `{ok, command, exit_code, tail, commands}`; `command` is the one that
    failed, or empty. Never raises for a failing command: it is shown.
    """
    tree = Path(tree)
    run = run or _run
    todo = commands(tree)
    result: dict[str, Any] = {
        "ok": True,
        "command": "",
        "exit_code": 0,
        "tail": "",
        "commands": [" ".join(c) for c in todo],
    }
    env = prepare_env(tree, workspace, data_dir)
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


# # --- removing ----------------------------------------------------------------


async def remove_if_finished(
    workspace: str | os.PathLike[str],
    unit: str,
    board_unit: dict[str, Any],
    data_dir: str | os.PathLike[str] | None = None,
    run: gh.Run | None = None,
) -> dict[str, Any]:
    """Remove a finished unit's worktree and its local branch. Never raises.

    All four must hold, or nothing is touched:

    1. `cos.mjs` says the unit is `finished` (read from `board_unit`, never inferred);
    2. `gh pr view` in the worktree says the pull request is `MERGED`;
    3. the worktree is clean;
    4. where the local branch still exists, it points at the head GitHub merged.

    Returns `{removed, reason}`.
    """
    run = run or gh.run
    try:
        if str(board_unit.get("next") or "") != "finished":
            return {"removed": False, "reason": "not finished"}
        found = await find(workspace, unit, data_dir)
        if found is None:
            return {"removed": False, "reason": "no worktree"}
        tree = Path(found["path"])
        pr_url = str((board_unit.get("pr") or {}).get("url") or "")
        if not gh.PR_URL_RE.fullmatch(pr_url):
            return {"removed": False, "reason": "no pull request url"}
        # Before `gh`: `board()` calls this on every read, and a dirty tree would otherwise cost a
        # network call each time.
        if not await gitops.is_clean(tree):
            return {"removed": False, "reason": "worktree has uncommitted changes"}
        said = await gh.call(
            run, ["pr", "view", pr_url, "--json", "state,headRefOid"], str(tree), None
        )
        if isinstance(said, str):
            return {"removed": False, "reason": said}
        code, out, err = said
        if code != 0:
            return {"removed": False, "reason": gh.said(code, out, err)}
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
