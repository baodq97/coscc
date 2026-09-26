"""Taking a UI unit's screenshots again after its branch was rewritten (`0111`).

`0083` ties a unit's screenshots to the commit they were taken at: `.screens/manifest.json`
names it, and `review` writes it as `Taken at`. A rebase — by the app's integration, by Gebo,
or by a person at a terminal — gives the branch a new head, and a review round was spent
saying only that the manifest was stale. So before a `review` step, when `cos.mjs screens`
says so, the app runs the branch's own `scripts/capture_screens.py` in the unit's worktree
with the addresses `impl` chose, and checks what it wrote. Whether to is `cos.mjs`'s rule;
this module only runs the command, judges its result, and says what happened.

**It runs the branch's code with this process's environment**, less every `__REFLEX_*`
(set blank, as `sessions.child_env` does for a session) and with this app's `cos.db` named in
`config.PROTECTED_DB_VAR`. That is the environment `spike.md ## U1` measured exit 0 in (57.7 s
cold, 26.1 s warm); with the `__REFLEX_*` names left as `coscc/run.py` sets them the build
fails and overwrites `reflex.lock/package.json`, a tracked file. No session is involved, and
nothing narrows the environment further, since nothing narrower was measured.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from typing import Any, Iterable

from coscc import config
from coscc.data import Data

# Chosen, not measured: about five times the cold run `spike.md ## U1` measured (57.7 s).
RETAKE_TIMEOUT = 300.0
# The command, with the addresses after it. Relative to the worktree it runs in.
COMMAND = ("uv", "run", "python", "scripts/capture_screens.py")
MANIFEST = Path(".screens") / "manifest.json"
TAIL_LINES = 12
# What is kept of the output while it runs; only its last lines are ever recorded.
_KEEP_BYTES = 64 * 1024


def env(data_dir: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """This process's environment, every `__REFLEX_*` blank, and this app's `cos.db` protected.

    `capture_screens.py` deletes the blank ones itself (`scripts/capture_screens.py:345`).
    `data_dir` is the app's data root, `None` meaning the default `~/.cos`.
    """
    e = dict(os.environ)
    e.update({name: "" for name in e if name.startswith("__REFLEX_")})
    e[config.PROTECTED_DB_VAR] = config.protect(Data(data_dir).db_path)
    return e


async def _git(tree: Path, *args: str) -> str:
    """What git printed, or `""` when it failed: a failure is judged, not raised."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(tree),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return (out or b"").decode(errors="replace") if proc.returncode == 0 else ""


def _kill(proc: asyncio.subprocess.Process) -> None:
    """The whole group: `uv`, the app server and chromium `capture_screens.py` started. Only
    `uv` killed would leave port 18783 held, and every capture after it refused."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def read_manifest(tree: Path) -> dict[str, Any] | None:
    try:
        found = json.loads((Path(tree) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


async def take(
    tree: str | os.PathLike[str],
    addresses: Iterable[str],
    *,
    data_dir: str | os.PathLike[str] | None = None,
    timeout: float = RETAKE_TIMEOUT,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """Run the capture in `tree` and return what `judge` needs. Never raises for the command.

    `{code, seconds, tail, head_before_run, manifest_after, status_before, status_after}`:
    `code` is 124 when it ran past `timeout` and was killed. Cancelled, the group is killed
    and the cancel raised again. `argv` replaces the command, for tests only.
    """
    tree = Path(tree)
    head = (await _git(tree, "rev-parse", "HEAD")).strip()
    status_before = await _git(tree, "status", "--porcelain")
    command = list(argv) if argv is not None else [*COMMAND, *addresses]
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *command, cwd=str(tree), env=env(data_dir),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    kept = bytearray()

    async def pump() -> int:
        assert proc.stdout is not None
        while chunk := await proc.stdout.read(65536):
            kept.extend(chunk)
            del kept[:-_KEEP_BYTES]
        return await proc.wait()

    try:
        code = await asyncio.wait_for(pump(), timeout)
    except asyncio.TimeoutError:
        _kill(proc)
        await proc.wait()
        code = 124
        kept.extend(f"\ndid not finish in {timeout:.0f}s, and was killed".encode())
    except asyncio.CancelledError:
        _kill(proc)
        await proc.wait()
        raise
    lines = kept.decode(errors="replace").splitlines()
    return {
        "code": code,
        "seconds": round(time.monotonic() - started, 1),
        "tail": "\n".join(lines[-TAIL_LINES:]),
        "head_before_run": head,
        "manifest_after": read_manifest(tree),
        "status_before": status_before,
        "status_after": await _git(tree, "status", "--porcelain"),
    }


def _changed(before: str, after: str) -> list[str]:
    was, now = before.splitlines(), after.splitlines()
    return [line for line in now if line not in was] + [line for line in was if line not in now]


def judge(result: dict[str, Any]) -> tuple[bool, str]:
    """`0111` R4: taken only when the command exited 0, the manifest names the `HEAD` it
    started on and a clean tree, and `git status` is what it was. An exit other than 0 fails
    whatever the manifest on disk says: after one, it is the previous run's
    (`spike.md ## U1`, result 5). Returns `(ok, detail)`; `detail` is `""` when ok."""
    problems: list[str] = []
    code = result.get("code")
    if code != 0:
        problems.append(f"capture_screens.py exited {code}")
    head = str(result.get("head_before_run") or "")
    manifest = result.get("manifest_after")
    if not head:
        problems.append("the worktree's HEAD could not be read")
    if not isinstance(manifest, dict):
        problems.append("no .screens/manifest.json could be read after it")
    else:
        if manifest.get("head") != head:
            problems.append(f"the manifest names {str(manifest.get('head') or 'no head')[:12]}, not HEAD {head[:12]}")
        if manifest.get("dirty") is not False:
            problems.append("the manifest says the tree changed while it ran")
    changed = _changed(str(result.get("status_before") or ""), str(result.get("status_after") or ""))
    if changed:
        problems.append("git status --porcelain changed:\n" + "\n".join(changed))
    if not problems:
        return True, ""
    tail = str(result.get("tail") or "").strip()
    return False, "; ".join(problems) + (f"\n{tail}" if tail else "")


def record(
    workspace: str, unit: str, old: dict[str, Any], result: dict[str, Any], ok: bool, detail: str,
    started_by: str,
) -> dict[str, Any]:
    """`0111` R6: the one run-log line a retake leaves, taken or not. No screen shows it."""
    new = result.get("manifest_after") or {}
    return {
        "kind": "screens",
        "workspace": workspace,
        "unit": unit,
        "stage": "review",
        "head_before": str(old.get("head") or ""),
        "head_after": str(new.get("head") or "") if ok else "",
        "addresses": [str(a) for a in old.get("addresses") or []],
        "code": result.get("code"),
        "seconds": result.get("seconds"),
        "outcome": "taken" if ok else "failed",
        "detail": detail,
        "started_by": started_by,
    }


def _hits(manifest: dict[str, Any]) -> str:
    rows = [h for h in manifest.get("hits") or [] if isinstance(h, dict)]
    if not rows:
        return "- none"
    return "\n".join(
        f"- `{h.get('address')}` — {h.get('size')} — {h.get('kind')} — {h.get('snippet')}" for h in rows
    )


def describe_for_review(old: dict[str, Any], new: dict[str, Any]) -> str:
    """`0111` R7: the section the `review` prompt carries after a retake that was taken."""
    before, after = str(old.get("head") or ""), str(new.get("head") or "")
    return (
        "# The screenshots, taken again\n\n"
        f"The branch was rewritten after its screenshots were taken at `{before}`, which is no "
        "longer an ancestor of this checkout's `HEAD`. Before this step started, the app — not "
        "`impl`, and not a person — took the addresses `impl` chose again with "
        f"`scripts/capture_screens.py`, at `{after}`. `.screens/manifest.json` and the images "
        f"beside it are that run's, so `Taken at` is `{after}`.\n\n"
        "A hit in the new manifest counts as explained when `impl.md ## Screens` explains a hit "
        "with the same address, size and kind; the snippet is not compared, since the fixture's "
        "`/tmp` paths change on every run. The hits of both manifests:\n\n"
        f"Before, at `{before[:7]}`:\n\n{_hits(old)}\n\n"
        f"Now, at `{after[:7]}`:\n\n{_hits(new)}\n"
    )
