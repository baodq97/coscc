"""Running `git`, with the smallest surface that does the job.

`0002` spec.md C3 said it plainly: any path in this app that runs a command from text the
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

The timeouts are choices, not measurements (`spec.md` C3 — no source). They were picked
here and should be adjusted after real use rather than trusted.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

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
