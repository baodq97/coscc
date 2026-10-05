"""Running `git` with the smallest surface that does the job.

Any path that runs a command from user text can leak the login token, so this one is built
to make that impossible:

1. **argv, never a shell.** No string is interpreted; `repo_url` is one list element.
2. **Fixed subcommands.** No flag reaches `git` from a caller, and a URL beginning with `-`
   is refused before `git` runs.
3. **A constructed environment.** The child gets `PATH`, `HOME` and the two non-interactive
   variables, never ours, so `CLAUDE_CODE_OAUTH_TOKEN` cannot reach a networked process.
4. **A deadline.** A silent host must not hold a request forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import signal
from pathlib import Path

# Measured cloning over https: about 4 MB/s, and a pull with nothing to fetch costs about a
# second whatever the size. Unverifiable beyond one network. These deadlines are derived:
# 120s covers about 480 MB of clone, 60s about 240 MB of fetch. They exist to stop a silent
# host, not to police size; lowering them makes ordinary clones fail.
CLONE_TIMEOUT = 120.0
PULL_TIMEOUT = 60.0

# # Only this scheme: `git@`, `ssh://` and `file://` reach credentials or the local disk.
ALLOWED_PREFIX = "https://"

# # Everything a caller must not smuggle into the child environment, matched by prefix so the
# # whole family is covered.
SCRUB_PREFIXES = ("CLAUDE", "COS_", "ANTHROPIC")


class GitError(Exception):
    """A git invocation that did not succeed, carrying output a caller can show."""


def check_url(repo_url: str) -> str:
    """The only validation of caller text, done before `git` exists as a process."""
    url = (repo_url or "").strip()
    if not url:
        raise GitError("repository URL is required")
    if url.startswith("-"):
        raise GitError(f"refusing a URL that could be read as a flag: {url!r}")
    if not url.startswith(ALLOWED_PREFIX):
        raise GitError(f"only {ALLOWED_PREFIX} URLs are supported in this version, got: {url!r}")
    return url


def child_env() -> dict[str, str]:
    """The environment `git` runs in. Built up, never filtered down: a new secret is excluded by default."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        # No terminal to ask on, so asking must fail fast rather than hang.
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": shutil.which("false") or "/bin/false",
        "SSH_ASKPASS": shutil.which("false") or "/bin/false",
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
    }
    # Belt and braces: turns a leaked value into a crash.
    for key in list(env):
        if any(key.startswith(p) for p in SCRUB_PREFIXES):
            del env[key]
    return env


async def kill_group(proc: asyncio.subprocess.Process) -> None:
    """Kill a child started with `process_group=0` and everything it started, then reap it.

    The group, not the child: an `ssh` or a credential helper left holding the pipe would hold
    `wait` until it exits. One that exited as the kill came is only reaped.
    """
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    await proc.wait()


async def _run(argv: list[str], timeout: float, cwd: str | None = None, strip: bool = True) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=child_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        process_group=0,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await kill_group(proc)
        raise GitError(f"git timed out after {timeout:.0f}s: {' '.join(argv[:2])}")
    except asyncio.CancelledError:
        # A cancelled caller leaves no `git` behind it: shutdown returns once this has.
        await kill_group(proc)
        raise
    text = (out or b"").decode(errors="replace")
    text = text.strip() if strip else text[:-1] if text.endswith("\n") else text
    if proc.returncode != 0:
        raise GitError(text or f"git exited {proc.returncode}")
    return text


async def _run_code(argv: list[str], timeout: float, cwd: str | None = None) -> tuple[int, str]:
    """Like `_run`, but hands the exit code back instead of raising on a non-zero one.

    For `merge-base --is-ancestor`, where exit 1 means "no", not "failed".
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=child_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        process_group=0,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await kill_group(proc)
        raise GitError(f"git timed out after {timeout:.0f}s: {' '.join(argv[:2])}")
    except asyncio.CancelledError:
        await kill_group(proc)
        raise
    text = (out or b"").decode(errors="replace").strip()
    return proc.returncode or 0, text


async def clone(repo_url: str, dest: Path, timeout: float = CLONE_TIMEOUT) -> str:
    """Clone into a directory that must not already exist."""
    url = check_url(repo_url)
    if dest.exists():
        raise GitError(f"destination already exists: {dest}")
    return await _run(["git", "clone", "--", url, str(dest)], timeout)


async def pull(path: Path, timeout: float = PULL_TIMEOUT) -> str:
    """Fast-forward only. A dirty tree or diverged branch fails, and the output comes back either way."""
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    return await _run(["git", "-C", str(path), "pull", "--ff-only"], timeout)


# # --- branches ----------------------------------------------------------------
# #
# # These write to somebody else's git and are not covered by `coscc/agent/policy.py` (which
# # limits *sessions*): they run with the app's own authority, so the limit lives here and is
# # a short list on purpose.
# #
# # The app may: fetch the trunk into `refs/remotes/origin/main`; create a branch from the
# # fetched commit with a name `coscc.loop unit-branch` produced; and, only as the functions
# # below spell out:
# #
# # - `worktree add` a unit's tree outside the workspace (detached at a full SHA or on an
# #   existing branch), `worktree list --porcelain`, and `worktree remove` **never** with
# #   `--force`, so a tree with changes stays.
# # - `worktree_discard`, only of a tree that `worktrees.ensure` half made (cancelled or failed
# #   in `worktree add`, or killed with it): `worktree remove` without `--force`, and when git
# #   refuses (a half-made tree is locked), the tree's directory and its admin directory under
# #   `<git dir>/worktrees/` deleted, then `worktree prune`. Nothing else is ever deleted.
# # - `status --porcelain` to know whether a tree is clean.
# # - `switch main` in the workspace itself, only when clean; the page is told whenever it
# #   happens.
# # - `branch -D` a unit's branch after its PR merged, only when the local branch still points
# #   at the head GitHub merged. `-d` always refuses after a squash, so the head comparison
# #   is all that stands between this and a lost commit.
# # - `switch --detach` a unit's worktree to a freshly fetched commit, only when the tree has
# #   no branch, is clean, and its HEAD is an ancestor of that commit, so an unpushed commit
# #   is never left behind. `merge-base --is-ancestor` and `rev-list --count` are the reads.
# # - `rev-parse --git-common-dir`, so fetches into a shared git dir can be coordinated.
# # - In the release worktree only (`worktrees.release_path`), through the functions under
# #   `# --- releasing ---`: fetch `main` with tags, commit the four version files on a
# #   `chore/release-X-Y-Z` branch, push it without force, push a `vX.Y.Z` tag, detach the
# #   tree, and remove it with `--force`.
# #
# # The app may **not** push, merge, commit, move `main`, or delete any other branch; those
# # belong to a step (`coscc/agent/policy.py`) or nobody.
# #
# # This is a hand-written list, not a mechanism. What keeps it narrow is that the only
# # argument reaching git from a request is a branch name, and it comes back from the loop.

BRANCH_TIMEOUT = 30.0

# # The branch a unit is cut from. Named, not taken from the checkout, or a unit's branch
# # quietly contains another unit's work.
TRUNK = "main"

# # What a branch name may look like. `coscc.loop check-branch` owns the grammar; this stops a
# # name reaching `git` as an option (`-` or `--` at the front).
_BRANCH_RE = re.compile(r"^[a-z]+/[a-z0-9]+(?:-[a-z0-9]+)*$")

# # A remote or trunk name handed to `fetch`. No leading `-`, `/` or `:`, so neither can become
# # an option or reshape the refspec.
_REF_PART_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# # The only start point `create_branch` takes.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# # Seconds. Not measured; a `pull` with nothing to fetch costs under a second. The page's
# # request waits while it runs.
FETCH_TIMEOUT = 20.0


async def current_branch(path: Path, timeout: float = BRANCH_TIMEOUT) -> str:
    """The branch this checkout is on. Empty on a detached HEAD, which is not an error."""
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    return await _run(["git", "-C", str(path), "branch", "--show-current"], timeout)


async def fetch(
    path: Path, remote: str = "origin", branch: str = TRUNK, timeout: float = FETCH_TIMEOUT
) -> str:
    """Bring `refs/remotes/<remote>/<branch>` up to date, and touch nothing else.

    The refspec is spelled out so the ref updates even without a default refspec, and no tags
    come along. No local branch moves and the working tree is not read, so a dirty checkout
    does not stop it. Raises `GitError` on failure or deadline.
    """
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    for part in (remote, branch):
        if not _REF_PART_RE.fullmatch(part or ""):
            raise GitError(f"not a remote or branch name this app will fetch: {part!r}")
    refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
    return await _run(
        ["git", "-C", str(path), "fetch", "--no-tags", "--", remote, refspec], timeout
    )


async def common_dir(path: Path, timeout: float = BRANCH_TIMEOUT) -> Path:
    """The git dir `path` shares with every other worktree of its clone, absolute.

    Every worktree writes `refs/remotes/origin/main` here, so it is what two fetches race on.
    """
    _require_repo(path)
    out = await _run(["git", "-C", str(path), "rev-parse", "--git-common-dir"], timeout)
    return (Path(path) / out.strip()).resolve()


async def rev_parse(path: Path, ref: str, timeout: float = BRANCH_TIMEOUT) -> str:
    """The full SHA a ref names. `--verify` so an unknown ref fails instead of echoing."""
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    if not ref or ref.startswith("-"):
        raise GitError(f"refusing a ref that could be read as a flag: {ref!r}")
    try:
        return await _run(
            ["git", "-C", str(path), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            timeout,
        )
    except GitError as e:
        # `--quiet` makes git say nothing, so the reason has to be ours.
        raise GitError(f"{ref} does not name a commit in {path} ({e})") from e


async def is_ancestor(
    path: Path, ancestor: str, descendant: str, timeout: float = BRANCH_TIMEOUT
) -> bool:
    """Whether `ancestor` is reachable from `descendant` (`merge-base --is-ancestor`).

    Exit 0 is yes, 1 is no, anything else is an error; hence `_run_code`. Both arguments are
    full SHAs so the answer cannot drift between the check and the caller's act.
    """
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    for sha in (ancestor, descendant):
        if not _SHA_RE.fullmatch(sha or ""):
            raise GitError(f"a full commit SHA is required, not {sha!r}")
    code, text = await _run_code(
        ["git", "-C", str(path), "merge-base", "--is-ancestor", ancestor, descendant], timeout
    )
    if code == 0:
        return True
    if code == 1:
        return False
    raise GitError(text or f"git exited {code}")


async def count_missing(path: Path, have: str, want: str, timeout: float = BRANCH_TIMEOUT) -> int:
    """How many commits `want` carries that `have` does not (`rev-list --count have..want`).

    Both arguments are full SHAs, as for `is_ancestor`.
    """
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    for sha in (have, want):
        if not _SHA_RE.fullmatch(sha or ""):
            raise GitError(f"a full commit SHA is required, not {sha!r}")
    out = await _run(["git", "-C", str(path), "rev-list", "--count", f"{have}..{want}"], timeout)
    try:
        return int(out.strip())
    except ValueError:
        raise GitError(f"rev-list did not print a count: {out!r}") from None


async def create_branch(path: Path, name: str, base: str, timeout: float = BRANCH_TIMEOUT) -> str:
    """Cut `name` from the commit `base` and switch to it. Creates nothing else, pushes nothing.

    `base` is a full SHA so what is reported as cut is exactly what was cut, even if another
    fetch lands in between. `--no-track`: cut from `origin/main` by name, git would track
    `main` and a later `git pull` would pull the trunk in unasked.

    Refuses rather than reuses when the branch already exists.
    """
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    # Before the grammar check: it keeps holding if the grammar is loosened, and gives the reason
    # rather than the shape.
    if name == TRUNK:
        raise GitError(f"{TRUNK} is the trunk and this app does not create or move it")
    if not _BRANCH_RE.fullmatch(name or ""):
        raise GitError(f"not a branch name this app will create: {name!r}")
    if not _SHA_RE.fullmatch(base or ""):
        raise GitError(f"a branch is cut from a full commit SHA, not from {base!r}")

    existing = await _run(
        ["git", "-C", str(path), "branch", "--list", "--format=%(refname:short)", name], timeout
    )
    if existing.strip():
        raise GitError(f"branch already exists: {name}")

    # `switch -c <name> <start>` cannot fall back to the current HEAD. It fails when a modified
    # working-tree file also differs between HEAD and `base`, creating no branch; that output
    # reaches the page as it is.
    return await _run(["git", "-C", str(path), "switch", "--no-track", "-c", name, base], timeout)


# # --- worktrees ---------------------------------------------------------------
# #
# # One working tree per unit, so cutting one unit's branch cannot take another's away. Paths
# # come from `coscc/units/worktrees.py` and refs passed `_SHA_RE` or `_BRANCH_RE`, so no request
# # text reaches `git`.

WORKTREE_TIMEOUT = 60.0  # WORKTREE_TIMEOUT = 60.0  # seconds; `worktree add` checks a tree out.


def _require_repo(path: Path) -> None:
    # A linked worktree has a `.git` *file*, not a directory; `exists` covers both.
    if not (Path(path) / ".git").exists():
        raise GitError(f"not a git repository: {path}")


async def is_clean(path: Path, timeout: float = BRANCH_TIMEOUT, *, untracked: bool = True) -> bool:
    """True when `status --porcelain` prints nothing: no change, staged or not, no new file.
    `untracked=False` counts only changes to tracked files."""
    _require_repo(path)
    cmd = ["git", "-C", str(path), "status", "--porcelain"]
    if not untracked:
        cmd.append("--untracked-files=no")
    out = await _run(cmd, timeout)
    return not out.strip()


async def switch_trunk(root: Path, timeout: float = BRANCH_TIMEOUT) -> str:
    """Put the workspace back on `main`. Refused unless its tree is clean.

    Plain `switch main`. A dirty tree is refused here because git would carry the change
    across, which still moves somebody's work.
    """
    _require_repo(root)
    if not await is_clean(root, timeout):
        raise GitError(f"{root} has uncommitted changes, so it was left on its branch")
    return await _run(["git", "-C", str(root), "switch", TRUNK], timeout)


async def switch_existing(tree: Path, name: str, timeout: float = BRANCH_TIMEOUT) -> str:
    """Put a unit's clean worktree on its existing branch. Never `-c`, never `--force`."""
    _require_repo(tree)
    if name == TRUNK or not _BRANCH_RE.fullmatch(name or ""):
        raise GitError(f"not a branch name this app will switch to: {name!r}")
    if not await is_clean(tree, timeout):
        raise GitError(f"{tree} has uncommitted changes, so it was left where it was")
    return await _run(["git", "-C", str(tree), "switch", name], timeout)


async def advance_detached(
    tree: Path, sha: str, timeout: float = BRANCH_TIMEOUT, *, untracked: bool = True
) -> str:
    """Move a unit's detached worktree to `sha`, refusing rather than guessing. Never `--force`.

    Checked together so no caller can skip one; any failure raises `GitError` naming which and
    leaves the tree where it was:

    - the tree carries no branch of its own (`current_branch` is empty);
    - the tree is clean (with `untracked=False`, of tracked changes only: an untracked file
      the move would overwrite still makes `switch` refuse);
    - the tree's HEAD is an ancestor of `sha`, so a commit made on the tree is never left behind.
    """
    _require_repo(tree)
    if not _SHA_RE.fullmatch(sha or ""):
        raise GitError(f"a worktree is advanced to a full commit SHA, not {sha!r}")
    branch = await current_branch(tree, timeout)
    if branch:
        raise GitError(f"{tree} is on {branch}, not detached, so it was not moved")
    if not await is_clean(tree, timeout, untracked=untracked):
        raise GitError(f"{tree} has uncommitted changes, so it was not moved")
    head = await _run(["git", "-C", str(tree), "rev-parse", "HEAD"], timeout)
    if not await is_ancestor(tree, head, sha, timeout):
        raise GitError(
            f"{tree}'s HEAD ({head[:7]}) is not an ancestor of {sha[:7]}, so it was not "
            "moved — it may carry a commit not on the remote trunk"
        )
    return await _run(["git", "-C", str(tree), "switch", "--detach", sha], timeout)


async def worktree_add(
    root: Path, path: Path, start: str, timeout: float = WORKTREE_TIMEOUT
) -> str:
    """Add a working tree at `path`: detached at a full SHA, or on an existing branch.

    Never `-b`/`-B`: creating the branch stays `create_branch`'s job.
    """
    _require_repo(root)
    if Path(path).exists():
        raise GitError(f"destination already exists: {path}")
    if _SHA_RE.fullmatch(start or ""):
        argv = ["worktree", "add", "--detach", "--", str(path), start]
    elif start != TRUNK and _BRANCH_RE.fullmatch(start or ""):
        argv = ["worktree", "add", "--", str(path), start]
    else:
        raise GitError(f"a worktree starts at a full SHA or a unit branch, not {start!r}")
    return await _run(["git", "-C", str(root), *argv], timeout)


async def worktree_list(root: Path, timeout: float = BRANCH_TIMEOUT) -> list[dict[str, str]]:
    """Every working tree of `root`'s repository, the main one first: path, branch, head, locked.

    `branch` is the short name, or empty on a detached HEAD. `locked` is empty for a tree that
    is not locked and git's reason otherwise (`initializing` while `worktree add` runs, and
    after it was killed); a lock with no reason reads `locked`.
    """
    _require_repo(root)
    out = await _run(["git", "-C", str(root), "worktree", "list", "--porcelain"], timeout)
    trees: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if current:
                trees.append(current)
            current = {}
            continue
        word, _, rest = line.partition(" ")
        if word == "worktree":
            current = {"path": rest, "branch": "", "head": "", "locked": ""}
        elif word == "HEAD":
            current["head"] = rest
        elif word == "branch":
            current["branch"] = rest.removeprefix("refs/heads/")
        elif word == "locked":
            current["locked"] = rest or "locked"
    return trees


async def worktree_admin_dir(
    root: Path, path: Path, timeout: float = BRANCH_TIMEOUT
) -> Path | None:
    """`<git dir>/worktrees/<name>` of the linked tree at `path`, or None when git has none.

    Found by the `gitdir` file each holds, not by name: git suffixes a name that is taken. One
    with no `gitdir` yet (`worktree add` killed that early) is taken by the tree's own name.
    """
    _require_repo(root)
    admins = (await common_dir(root, timeout)) / "worktrees"
    where = Path(path).resolve()
    try:
        names = sorted(admins.iterdir())
    except OSError:
        return None
    for admin in names:
        try:
            pointed = Path((admin / "gitdir").read_text(encoding="utf-8").strip())
        except OSError:
            if admin.name == where.name:
                return admin
            continue
        if pointed.parent.resolve() == where:
            return admin
    return None


async def worktree_half_made(
    root: Path, tree: dict[str, str], timeout: float = BRANCH_TIMEOUT
) -> bool:
    """Whether a `worktree_list` entry is a tree whose `worktree add` never finished: git still
    reports it `locked initializing`, or its admin directory still holds `index.lock`.
    """
    if tree.get("locked") == "initializing":
        return True
    admin = await worktree_admin_dir(root, Path(tree["path"]), timeout)
    return admin is not None and (admin / "index.lock").exists()


async def worktree_remove(root: Path, path: Path, timeout: float = WORKTREE_TIMEOUT) -> str:
    """Remove a linked working tree. Never forced: git refuses a tree with changes."""
    _require_repo(root)
    if Path(path).resolve() == Path(root).resolve():
        raise GitError("the workspace's own working tree is never removed")
    return await _run(["git", "-C", str(root), "worktree", "remove", "--", str(path)], timeout)


async def worktree_discard(root: Path, path: Path, timeout: float = WORKTREE_TIMEOUT) -> None:
    """Remove a tree whose `worktree add` was cancelled, failed or was killed part-way.

    `worktree remove` without `--force` first. Git refuses a locked tree (which a half-made one
    is), so then the directory and the admin directory are deleted and `worktree prune` run;
    the deletion is why a caller may only name a tree it knows is half made. Raises `GitError`
    when the directory is still there afterwards.
    """
    _require_repo(root)
    where = Path(path).resolve()
    here = Path(root).resolve()
    if where == here or where in here.parents:
        raise GitError("the workspace's own working tree is never removed")
    admin = await worktree_admin_dir(root, where, timeout)
    if admin is None and not where.exists():
        return
    try:
        await _run(["git", "-C", str(root), "worktree", "remove", "--", str(where)], timeout)
        return
    except GitError:
        pass
    shutil.rmtree(where, ignore_errors=True)
    if admin is not None:
        shutil.rmtree(admin, ignore_errors=True)
    await _run(["git", "-C", str(root), "worktree", "prune"], timeout)
    if where.exists():
        raise GitError(f"could not remove the half-made tree {where}")


async def delete_merged_branch(
    root: Path, name: str, expected_head: str, timeout: float = BRANCH_TIMEOUT
) -> bool:
    """`branch -D <name>`, only when the local branch is exactly `expected_head`.

    Returns False, deleting nothing, when the branch is gone or points elsewhere: a later local
    commit is somebody's unpushed work.
    """
    _require_repo(root)
    if name == TRUNK or not _BRANCH_RE.fullmatch(name or ""):
        raise GitError(f"not a branch name this app will delete: {name!r}")
    if not _SHA_RE.fullmatch(expected_head or ""):
        raise GitError(f"a merged head is a full SHA, not {expected_head!r}")
    try:
        head = await _run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
            timeout,
        )
    except GitError:
        return False
    if head.strip() != expected_head:
        return False
    await _run(["git", "-C", str(root), "branch", "-D", "--", name], timeout)
    return True


# # --- reading a failed attempt's tree ----------------------------------------
# #
# # Read-only, all four: what a stopped step left behind, in a shape that can be checked
# # against `git log` and `git status` run by hand.


async def head_and_branch(path: Path, timeout: float = BRANCH_TIMEOUT) -> tuple[str, str]:
    """The full HEAD SHA and the branch name, `"detached"` when there is none."""
    _require_repo(path)
    head = await _run(["git", "-C", str(path), "rev-parse", "HEAD"], timeout)
    branch = await _run(["git", "-C", str(path), "branch", "--show-current"], timeout)
    return head, (branch or "detached")


async def merge_base(path: Path, timeout: float = BRANCH_TIMEOUT) -> tuple[str, str]:
    """`merge-base HEAD` against the trunk, falling back when `origin/main` is absent.

    The ref is one of exactly two, fixed here, never a caller's choice.
    """
    _require_repo(path)
    ref = "refs/remotes/origin/main"
    try:
        await _run(["git", "-C", str(path), "rev-parse", "--verify", "--quiet", ref], timeout)
    except GitError:
        ref = "refs/heads/main"
    sha = await _run(["git", "-C", str(path), "merge-base", "HEAD", ref], timeout)
    return sha, ref


async def log_range(
    path: Path, base: str, head: str, timeout: float = BRANCH_TIMEOUT
) -> list[dict[str, str]]:
    """`git log base..head`, one dict per commit, in `git log` order."""
    _require_repo(path)
    for sha in (base, head):
        if not _SHA_RE.fullmatch(sha or ""):
            raise GitError(f"a full commit SHA is required, not {sha!r}")
    out = await _run(["git", "-C", str(path), "log", "--format=%H %s", f"{base}..{head}"], timeout)
    commits: list[dict[str, str]] = []
    for line in out.splitlines():
        sha_part, _, subject = line.partition(" ")
        commits.append({"sha": sha_part, "subject": subject})
    return commits


async def diff_names(path: Path, a: str, b: str, timeout: float = BRANCH_TIMEOUT) -> list[str]:
    """`git diff a..b --name-only`, verbatim.

    No `-z`, `--no-renames` or `core.quotePath`: a hand-run check must print the same list,
    quoting and renames included.
    """
    _require_repo(path)
    for sha in (a, b):
        if not _SHA_RE.fullmatch(sha or ""):
            raise GitError(f"a full commit SHA is required, not {sha!r}")
    out = await _run(["git", "-C", str(path), "diff", f"{a}..{b}", "--name-only"], timeout)
    return out.splitlines()


async def status_porcelain(path: Path, timeout: float = BRANCH_TIMEOUT) -> list[str]:
    """`git status --porcelain`, each line verbatim; the leading space of `" M a.txt"` matters, so
    `_run` is asked not to strip.
    """
    _require_repo(path)
    out = await _run(["git", "-C", str(path), "status", "--porcelain"], timeout, strip=False)
    return out.splitlines()


async def tree_state(path: Path, timeout: float = BRANCH_TIMEOUT) -> tuple[str, str]:
    """`(HEAD, git status --porcelain)`, compared before and after a spike.

    Read-only. A file `.gitignore` covers is not in `status`, so a write there is not seen.
    """
    _require_repo(path)
    head = await _run(["git", "-C", str(path), "rev-parse", "HEAD"], timeout)
    porcelain = await _run(["git", "-C", str(path), "status", "--porcelain"], timeout, strip=False)
    return head, porcelain


# # --- integrating a unit that fell behind -------------------------------------
# #
# # Every ref is a full SHA or a unit branch that passed `_BRANCH_RE`. The one function that
# # moves anything is `reset_branch_to`, which checks its four conditions together like
# # `advance_detached`.


def _require_shas(*shas: str) -> None:
    for sha in shas:
        if not _SHA_RE.fullmatch(sha or ""):
            raise GitError(f"a full commit SHA is required, not {sha!r}")


async def merge_base_of(path: Path, a: str, b: str, timeout: float = BRANCH_TIMEOUT) -> str:
    """`git merge-base a b`, both full SHAs. `merge_base` above is fixed to HEAD and the trunk."""
    _require_repo(path)
    _require_shas(a, b)
    return await _run(["git", "-C", str(path), "merge-base", a, b], timeout)


async def commits_between(
    path: Path, base: str, head: str, timeout: float = BRANCH_TIMEOUT
) -> list[dict[str, str]]:
    """`base..head` as `{sha, subject}`."""
    return await log_range(path, base, head, timeout)


async def files_of_commit(path: Path, sha: str, timeout: float = BRANCH_TIMEOUT) -> list[str]:
    """The paths one commit touched, against its first parent."""
    _require_repo(path)
    _require_shas(sha)
    out = await _run(
        ["git", "-C", str(path), "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha],
        timeout,
    )
    return [line for line in out.splitlines() if line.strip()]


async def files_between(
    path: Path, base: str, head: str, timeout: float = BRANCH_TIMEOUT
) -> list[str]:
    """The paths `base..head` touched, as one diff."""
    _require_repo(path)
    _require_shas(base, head)
    out = await _run(["git", "-C", str(path), "diff", "--name-only", base, head], timeout)
    return [line for line in out.splitlines() if line.strip()]


async def has_commit(path: Path, sha: str, timeout: float = BRANCH_TIMEOUT) -> bool:
    """Whether this repository holds `sha` as a commit. A malformed SHA is simply no."""
    _require_repo(path)
    if not _SHA_RE.fullmatch(sha or ""):
        return False
    code, _ = await _run_code(
        ["git", "-C", str(path), "cat-file", "-e", f"{sha}^{{commit}}"], timeout
    )
    return code == 0


async def _git_dir(path: Path, timeout: float) -> Path:
    out = await _run(["git", "-C", str(path), "rev-parse", "--absolute-git-dir"], timeout)
    return Path(out.strip())


async def rebase_in_progress(path: Path, timeout: float = BRANCH_TIMEOUT) -> bool:
    """Whether a rebase stopped part-way in this tree (a linked worktree's own git dir)."""
    _require_repo(path)
    git_dir = await _git_dir(path, timeout)
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


async def abort_rebase(path: Path, timeout: float = BRANCH_TIMEOUT) -> str:
    """`git rebase --abort`: back to where the rebase began. Nothing else."""
    _require_repo(path)
    return await _run(["git", "-C", str(path), "rebase", "--abort"], timeout)


async def reset_branch_to(
    tree: Path, branch: str, expected_old: str, new_sha: str, timeout: float = FETCH_TIMEOUT
) -> str:
    """Move the unit's own branch in its own tree from `expected_old` to `new_sha`.

    Used after GitHub rebased the pull request. Refused, changing nothing, unless the tree is
    clean, on `branch`, at `expected_old`, and `new_sha` is present after `git fetch origin
    <branch>`. `reset --keep`, never `--hard`: it refuses rather than discard a local change.
    """
    _require_repo(tree)
    if branch == TRUNK or not _BRANCH_RE.fullmatch(branch or ""):
        raise GitError(f"not a branch name this app will move: {branch!r}")
    _require_shas(expected_old, new_sha)
    if not await is_clean(tree, timeout):
        raise GitError(f"{tree} has uncommitted changes, so its branch was not moved")
    on = await current_branch(tree, timeout)
    if on != branch:
        raise GitError(
            f"{tree} is on {on or 'a detached HEAD'}, not {branch}, so nothing was moved"
        )
    head = await _run(["git", "-C", str(tree), "rev-parse", "HEAD"], timeout)
    if head != expected_old:
        raise GitError(f"{tree}'s HEAD is {head[:7]}, not {expected_old[:7]}, so nothing was moved")
    # Not `fetch`: its name check takes one path part, and a unit branch has two.
    await _run(
        [
            "git",
            "-C",
            str(tree),
            "fetch",
            "--no-tags",
            "--",
            "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        ],
        timeout,
    )
    if not await has_commit(tree, new_sha, timeout):
        raise GitError(
            f"{new_sha[:7]} is not here even after fetching {branch}, so nothing was moved"
        )
    return await _run(["git", "-C", str(tree), "reset", "--keep", new_sha], timeout)


# # --- releasing ---------------------------------------------------------------
# #
# # The only functions that commit, push and tag. They take a release branch that passed
# # `_RELEASE_BRANCH_RE`, a tag that passed `_RELEASE_TAG_RE`, a full SHA, or a file in
# # `RELEASE_FILES`; the caller asked `coscc.loop check-branch`/`check-tag` first. Every writer
# # takes the tree twice: the tree it works in and `expected`, the path `worktrees.release_path`
# # names, recomputed by the caller from the workspace. Both must agree and the tree must be a
# # linked worktree named `RELEASE_TREE`, so a wrong path in both is still refused.

_RELEASE_BRANCH_RE = re.compile(r"^chore/release-\d+-\d+-\d+$")
_RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
RELEASE_FILES = ("pyproject.toml", "package.json", "package-lock.json", "uv.lock")
# # The release tree's own name. Not a `NNNN_slug`, so no unit's tree carries it.
RELEASE_TREE = "release"


def _release_branch(name: str) -> str:
    if not _RELEASE_BRANCH_RE.fullmatch(name or ""):
        raise GitError(f"not a release branch this app will write: {name!r}")
    return name


def _release_tag(tag: str) -> str:
    if not _RELEASE_TAG_RE.fullmatch(tag or ""):
        raise GitError(f"not a release tag this app will write: {tag!r}")
    return tag


def _is_release_path(path: Path, expected: Path) -> bool:
    where = Path(path).resolve()
    return where == Path(expected).resolve() and where.name == RELEASE_TREE


def _release_tree(tree: Path, expected: Path) -> None:
    """Refuses unless `tree` is `expected` and a linked worktree named `release` (a workspace's
    own checkout holds a `.git` directory, a linked tree a `.git` file).
    """
    _require_repo(tree)
    if not _is_release_path(tree, expected) or not (Path(tree) / ".git").is_file():
        raise GitError(f"{tree} is not the release worktree, so nothing was written")


async def fetch_with_tags(path: Path, timeout: float = FETCH_TIMEOUT) -> str:
    """`fetch --tags origin +refs/heads/main:refs/remotes/origin/main`. Plain `fetch` keeps
    `--no-tags`; only a release brings the tags.
    """
    _require_repo(path)
    return await _run(
        [
            "git",
            "-C",
            str(path),
            "fetch",
            "--tags",
            "--",
            "origin",
            f"+refs/heads/{TRUNK}:refs/remotes/origin/{TRUNK}",
        ],
        timeout,
    )


async def release_tags(path: Path, sha: str, timeout: float = BRANCH_TIMEOUT) -> list[str]:
    """Every local tag `v*` that `sha` contains."""
    _require_repo(path)
    _require_shas(sha)
    out = await _run(["git", "-C", str(path), "tag", "--list", "v*", "--merged", sha], timeout)
    return [t for t in out.splitlines() if t.strip()]


async def remote_has_tag(path: Path, tag: str, timeout: float = FETCH_TIMEOUT) -> bool:
    """Whether `origin` holds `refs/tags/<tag>`, asked of the remote, not the local refs."""
    _require_repo(path)
    _release_tag(tag)
    out = await _run(
        ["git", "-C", str(path), "ls-remote", "--tags", "origin", f"refs/tags/{tag}"], timeout
    )
    return bool(out.strip())


async def show_file(path: Path, sha: str, name: str, timeout: float = BRANCH_TIMEOUT) -> str:
    """`git show <sha>:<name>` for one of the four version files."""
    _require_repo(path)
    _require_shas(sha)
    if name not in RELEASE_FILES:
        raise GitError(f"not a version file: {name!r}")
    return await _run(["git", "-C", str(path), "show", f"{sha}:{name}"], timeout, strip=False)


async def diff_u0(tree: Path, expected: Path, timeout: float = BRANCH_TIMEOUT) -> str:
    """`git diff -U0 HEAD`: every change in the tree, staged or not, new files included after
    `add -N` (which writes the index, so only in the release tree).
    """
    _release_tree(tree, expected)
    await _run(["git", "-C", str(tree), "add", "--intent-to-add", "--all"], timeout)
    return await _run(
        ["git", "-C", str(tree), "diff", "-U0", "--no-color", "HEAD"], timeout, strip=False
    )


async def commit_files(
    tree: Path, expected: Path, message: str, timeout: float = BRANCH_TIMEOUT
) -> str:
    """Commit the four version files on the release branch the tree stands on; the new SHA."""
    _release_tree(tree, expected)
    _release_branch(await current_branch(tree, timeout))
    await _run(["git", "-C", str(tree), "add", "--", *RELEASE_FILES], timeout)
    await _run(
        ["git", "-C", str(tree), "commit", "-q", "-m", message, "--", *RELEASE_FILES], timeout
    )
    return await _run(["git", "-C", str(tree), "rev-parse", "HEAD"], timeout)


async def push_branch(
    tree: Path, expected: Path, branch: str, timeout: float = FETCH_TIMEOUT
) -> str:
    """`push origin refs/heads/<b>:refs/heads/<b>`. No `--force`, no `-u`."""
    _release_tree(tree, expected)
    _release_branch(branch)
    return await _run(
        [
            "git",
            "-C",
            str(tree),
            "push",
            "--",
            "origin",
            f"refs/heads/{branch}:refs/heads/{branch}",
        ],
        timeout,
    )


async def push_unit_branch(tree: Path, branch: str, timeout: float = FETCH_TIMEOUT) -> str:
    """Push a unit's branch to `origin`. No `--force`: a branch rewritten on GitHub is refused
    rather than overwritten.
    """
    if not _BRANCH_RE.match(branch or ""):
        raise GitError(f"not a unit branch name: {branch!r}")
    return await _run(
        [
            "git",
            "-C",
            str(tree),
            "push",
            "--",
            "origin",
            f"refs/heads/{branch}:refs/heads/{branch}",
        ],
        timeout,
    )


async def push_tag(
    tree: Path, expected: Path, tag: str, sha: str, timeout: float = FETCH_TIMEOUT
) -> str:
    """`push origin <sha>:refs/tags/<tag>`: a lightweight tag made on the remote; the push builds
    the release. No local tag first, so a refused push leaves none behind. No force.
    """
    _release_tree(tree, expected)
    _release_tag(tag)
    _require_shas(sha)
    return await _run(
        ["git", "-C", str(tree), "push", "--", "origin", f"{sha}:refs/tags/{tag}"], timeout
    )


async def detach_here(tree: Path, expected: Path, timeout: float = BRANCH_TIMEOUT) -> str:
    """`switch --detach` where the tree stands, so `gh pr merge --delete-branch` never has to
    delete a checked-out branch.
    """
    _release_tree(tree, expected)
    return await _run(["git", "-C", str(tree), "switch", "--detach"], timeout)


async def release_tree_remove(
    root: Path, path: Path, expected: Path, timeout: float = WORKTREE_TIMEOUT
) -> str:
    """`worktree remove --force`, the one forced removal: only of the release tree, whose changes
    are the app's own. Not `.git`-checked: a tree whose directory is gone is still listed and
    removed.
    """
    _require_repo(root)
    if not _is_release_path(path, expected) or Path(path).resolve() == Path(root).resolve():
        raise GitError(f"{path} is not the release worktree, so it was not removed")
    return await _run(
        ["git", "-C", str(root), "worktree", "remove", "--force", "--", str(path)], timeout
    )
