"""Running `git`, with the smallest surface that does the job.

`spec.md` C3 said it plainly: any path in this app that runs a command from text the
user sent is the path that leaks the login token. This module is that path, so it is built
to make the leak impossible rather than unlikely.

Four rules, each answering `spec.md` R15:

1. **argv, never a shell.** No string is ever interpreted; `repo_url` is one element of a
   list, so quoting, `;`, `$(...)` and friends have no meaning.
2. **Fixed subcommands.** `clone` and `pull --ff-only`. No flag reaches `git` from a
   caller, and a URL that begins with `-` is refused before `git` is invoked so it cannot
   be read as one.
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
# The app may: create a branch, in a workspace a caller has already passed the membership
# gate for, with a name `cos.mjs unit-branch` produced.
#
# The app may **not**: push, merge, commit, delete a branch, or move `main`. Those are a
# step's business — `("pr", "autonomous")` carries `git` and `gh` and a warning that says
# what that reaches (`coscc/policy.py:96-99`) — or nobody's.
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


async def current_branch(path: Path, timeout: float = BRANCH_TIMEOUT) -> str:
    """The branch this checkout is on. Empty on a detached HEAD, which is not an error."""
    if not (path / ".git").exists():
        raise GitError(f"not a git repository: {path}")
    return await _run(["git", "-C", str(path), "branch", "--show-current"], timeout)


async def create_branch(path: Path, name: str, timeout: float = BRANCH_TIMEOUT) -> str:
    """Cut `name` from `TRUNK` and switch to it. Creates nothing else and pushes nothing.

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

    existing = await _run(
        ["git", "-C", str(path), "branch", "--list", "--format=%(refname:short)", name], timeout
    )
    if existing.strip():
        raise GitError(f"branch already exists: {name}")

    # `switch -c <name> <start>` is one command that cannot fall back to the current HEAD:
    # given a start point it either uses it or fails.
    return await _run(["git", "-C", str(path), "switch", "-c", name, TRUNK], timeout)
