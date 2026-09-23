"""Running `git`, with the smallest surface that does the job.

`spec.md` C3 said it plainly: any path in this app that runs a command from text the
user sent is the path that leaks the login token. This module is that path, so it is built
to make the leak impossible rather than unlikely.

Four rules, each answering `spec.md` R15:

1. **argv, never a shell.** No string is ever interpreted; `repo_url` is one element of a
   list, so quoting, `;`, `$(...)` and friends have no meaning.
2. **Fixed subcommands.** `clone`, `pull --ff-only`, and for branches `fetch` of the
   trunk, `rev-parse` and `switch -c`. No flag reaches `git` from a caller, and a URL that
   begins with `-` is refused before `git` is invoked so it cannot be read as one.
3. **A constructed environment.** The child gets `PATH`, `HOME`, and the two variables that
   make it non-interactive. It does **not** inherit ours, so `CLAUDE_CODE_OAUTH_TOKEN`
   cannot reach a process that talks to the network.
4. **A deadline.** A silent host must not hold a request forever.

The timeouts were invented when this was written (`spec.md` C3 recorded them as
having no source). They now have one — see `CLONE_TIMEOUT`.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

# Measured 2026-09-21 on this machine and network, cloning over https:
#
#     Hello-World      1.14s    ~0 MB      pull 0.83s
#     Spoon-Knife      1.18s    ~0 MB      pull 0.88s
#     click            4.19s    7.8 MB     pull 0.84s
#     requests         4.80s   19.2 MB     pull 0.92s
#
# That is roughly 4 MB/s, and a pull with nothing to fetch costs about a second
# regardless of size. **Unverifiable beyond this machine:** one network, one day, four
# public repositories — it bounds the ordinary case and says nothing about a slow link.
#
# So these are not the measurement, they are derived from it: at the measured throughput
# 120s covers about 480 MB of clone and 60s about 240 MB of fetch. Both are far larger
# than anything a person would reasonably open in a chat tool, which is the point — the
# deadline exists to stop a silent host holding a request forever, not to police size.
# Lower them and an ordinary clone starts failing; the measurement above is what says
# how much room there is before that happens.
CLONE_TIMEOUT = 120.0
PULL_TIMEOUT = 60.0

# Only this scheme. `git@`, `ssh://` and `file://` all reach credentials or the local disk
# by routes this unit does not want, and `spec.md` puts private repos out of scope.
ALLOWED_PREFIX = "https://"

# Everything a caller is not allowed to smuggle into the child environment. Matched by
# prefix because the point is the whole family, not four names that could grow to five.
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
        raise GitError(
            f"only {ALLOWED_PREFIX} URLs are supported in this version, got: {url!r}"
        )
    return url


def child_env() -> dict[str, str]:
    """The environment `git` runs in. Built up, never filtered down.

    Filtering an inherited environment is the version of this that goes wrong: it works
    until someone adds a variable nobody thought to exclude. Starting empty means a new
    secret is excluded by default.
    """
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
    # Belt and braces: if any of the above ever picks up a value from elsewhere, this is
    # the assertion that catches it. Cheap, and it turns a leak into a crash.
    for key in list(env):
        if any(key.startswith(p) for p in SCRUB_PREFIXES):
            del env[key]
    return env


async def _run(argv: list[str], timeout: float, cwd: str | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=child_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise GitError(f"git timed out after {timeout:.0f}s: {' '.join(argv[:2])}")
    text = (out or b"").decode(errors="replace").strip()
    if proc.returncode != 0:
        raise GitError(text or f"git exited {proc.returncode}")
    return text


async def clone(repo_url: str, dest: Path, timeout: float = CLONE_TIMEOUT) -> str:
    """Clone into a directory that must not already exist."""
    url = check_url(repo_url)
    if dest.exists():
        raise GitError(f"destination already exists: {dest}")
    return await _run(["git", "clone", "--", url, str(dest)], timeout)


async def pull(path: Path, timeout: float = PULL_TIMEOUT) -> str:
    """Fast-forward only.

    Failing on a dirty tree or a diverged branch is correct behaviour, not a defect
    (`spec.md` C7). What would be a defect is failing quietly, so the output comes back to
    the caller either way.
    """
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    return await _run(["git", "-C", str(path), "pull", "--ff-only"], timeout)


# --- branches ----------------------------------------------------------------
#
# `0014` R5. This is the first thing in this app that **writes** to somebody else's git,
# and it is not covered by `coscc/policy.py`: that table says what a *session* may do, and
# these run with the app process's own authority. So the limit has to live here, and it is
# a short list on purpose.
#
# The app may: fetch the trunk from `origin` into `refs/remotes/origin/main`, and create a
# branch from the commit that fetch brought, in a workspace a caller has already passed the
# membership gate for, with a name `cos.mjs unit-branch` produced. The fetch writes one
# remote-tracking ref and moves nothing a person works on.
#
# Since `0017` it may also, and only in the ways the functions below spell out:
#
# - `worktree add` a unit's own working tree outside the workspace, detached at a full SHA
#   or on a branch that already exists; `worktree list --porcelain` to find it again;
#   `worktree remove` it, **never** with `--force`, so a tree with changes stays.
# - `status --porcelain`, to know whether a tree is clean.
# - `switch main` in the workspace itself, and only when that tree is clean
#   (`0017` `spec.md ## Answers, câu 2`). This is the one exception to "move nothing a
#   person works on", and the page is told whenever it happens.
# - `branch -D` a unit's branch after its pull request merged, and only when the local
#   branch still points at the head GitHub merged. `-d` always refuses after a squash, so
#   the head comparison is the only thing standing between this and a lost commit.
#
# The app may **not**: push, merge, commit, move `main` to another commit, or delete any
# branch but that one. Those are a step's business — `("pr", "autonomous")` carries `git`
# and `gh` and a warning that says what that reaches (`coscc/policy.py:96-99`) — or nobody's.
#
# `plan.md` Risk 1 names the weakness honestly: this is a hand-written list, not a
# mechanism, in the same way `policy.check_command` is. What makes it narrow is that the
# only argument that reaches git from a request is a branch name, and that name is not the
# caller's: it comes back from `cos.mjs`.

BRANCH_TIMEOUT = 30.0

# The branch a unit is cut from. Named rather than taken from the current checkout: cutting
# from wherever somebody happened to be standing is how a unit's branch quietly contains
# another unit's work.
TRUNK = "main"

# What a branch name may look like. `cos.mjs check-branch` owns the grammar and this is the
# guard that stops a name reaching `git` at all — `-` or `--` at the front would be read as
# an option, and that is the one shape that turns a name into a flag.
_BRANCH_RE = re.compile(r"^[a-z]+/[a-z0-9]+(?:-[a-z0-9]+)*$")

# A remote name or a trunk name handed to `fetch`. No leading `-`, no `/`, no `:` — so
# neither can become an option or reshape the refspec it is spliced into.
_REF_PART_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# The only start point `create_branch` takes.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Seconds. **Not measured**: the figure is `0001_product-describes-a-state-it-is-not-in`
# `spec.md` R2's, chosen there and marked unverifiable. The nearest measurement with a
# source is a `pull` with nothing to fetch, 0.83-0.92s on this machine (the table at the
# top of this file). So it bounds the ordinary case and says nothing about a slow link —
# and while it runs, the page's request waits (`spec.md` C7).
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

    `0001_product-describes-a-state-it-is-not-in` R1. The refspec is spelled out so that
    ref is updated even when the remote's config carries no default refspec, and no tags
    come along with it. A fetch moves no local branch and does not look at the working
    tree, which is why a dirty checkout does not stop it.

    Raises `GitError` on failure or on the deadline, like everything else here: `_run`
    already carries git's own words back, so a second return shape would say less.
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


async def create_branch(
    path: Path, name: str, base: str, timeout: float = BRANCH_TIMEOUT
) -> str:
    """Cut `name` from the commit `base` and switch to it. Creates nothing else, pushes nothing.

    `base` is a full SHA and nothing else. Since `0001_product-describes-a-state-it-is-not-in`
    the caller decides the start point — fetch, then read the SHA — so what this reports as
    cut is exactly what was cut, even if another fetch lands in between. A SHA also carries
    no upstream, and `--no-track` says so explicitly: cut from `origin/main` by name, git
    would set the new branch to track `main`, and a later `git pull` on it would pull the
    trunk in without anybody asking.

    Refuses rather than reuses when the branch already exists: switching to a branch that
    somebody else's work is already on is a different act from starting one, and the two
    should not share a button.
    """
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    # Before the grammar check, not after. `_BRANCH_RE` requires a `<type>/` prefix and so
    # already excludes `main` today, which would make this line unreachable — found by
    # writing the test for it. Ordered this way it stays alive: it keeps holding if the
    # grammar is ever loosened, and it gives the reason rather than the shape.
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

    # `switch -c <name> <start>` is one command that cannot fall back to the current HEAD:
    # given a start point it either uses it or fails. It does fail when a file modified in
    # the working tree also differs between HEAD and `base` — git refuses rather than
    # overwrite, and creates no branch; that output reaches the page as it is.
    return await _run(
        ["git", "-C", str(path), "switch", "--no-track", "-c", name, base], timeout
    )


# --- worktrees (`0017`) --------------------------------------------------------
#
# One working tree per unit, so cutting one unit's branch cannot take another's away
# (`0017` intent). Everything here takes paths the caller computed (`coscc/worktrees.py`)
# and refs that passed `_SHA_RE` or `_BRANCH_RE`, so no request text reaches `git`.

WORKTREE_TIMEOUT = 60.0  # seconds. Chosen, not measured: `worktree add` checks a tree out.


def _require_repo(path: Path) -> None:
    # A linked worktree has a `.git` *file*, not a directory; `exists` covers both.
    if not (Path(path) / ".git").exists():
        raise GitError(f"not a git repository: {path}")


async def is_clean(path: Path, timeout: float = BRANCH_TIMEOUT) -> bool:
    """True when `status --porcelain` prints nothing: no change, staged or not, no new file."""
    _require_repo(path)
    out = await _run(["git", "-C", str(path), "status", "--porcelain"], timeout)
    return not out.strip()


async def switch_trunk(root: Path, timeout: float = BRANCH_TIMEOUT) -> str:
    """Put the workspace back on `main`. Refused unless its tree is clean.

    Plain `switch main`: no `-c`, no `--force`, no start point, so it moves no branch and
    discards nothing. A dirty tree is refused here rather than left to git, because git
    would carry the change across when it can and that is still moving somebody's work.
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


async def worktree_add(
    root: Path, path: Path, start: str, timeout: float = WORKTREE_TIMEOUT
) -> str:
    """Add a working tree at `path`: detached at a full SHA, or on an existing branch.

    Never `-b`/`-B`: creating the branch stays `create_branch`'s job, with its checks.
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
    """Every working tree of `root`'s repository, the main one first: path, branch, head.

    `branch` is the short name, or empty on a detached HEAD.
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
            current = {"path": rest, "branch": "", "head": ""}
        elif word == "HEAD":
            current["head"] = rest
        elif word == "branch":
            current["branch"] = rest.removeprefix("refs/heads/")
    return trees


async def worktree_remove(root: Path, path: Path, timeout: float = WORKTREE_TIMEOUT) -> str:
    """Remove a linked working tree. Never forced: git refuses a tree with changes."""
    _require_repo(root)
    if Path(path).resolve() == Path(root).resolve():
        raise GitError("the workspace's own working tree is never removed")
    return await _run(["git", "-C", str(root), "worktree", "remove", "--", str(path)], timeout)


async def delete_merged_branch(
    root: Path, name: str, expected_head: str, timeout: float = BRANCH_TIMEOUT
) -> bool:
    """`branch -D <name>`, only when the local branch is exactly `expected_head`.

    Returns False, deleting nothing, when the branch is already gone or points anywhere
    else — a local commit after the merged head is somebody's unpushed work.
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
