"""`pr.md ## Scope of the diff` against the pull request it describes (`0122`).

**What this module may do on GitHub, and nothing more.** It reads one pull request's counts
and file list (`gh pr view <url> --json changedFiles,additions,deletions,files`). It writes
nothing, on GitHub or on disk, and no gate reads what it returns: the verdict goes into the
step's `pr-sync` row and nowhere else.

**Why GitHub's numbers.** GitHub counts from the merge-base of the head with the remote
base, which is what the reader of the pull request sees; a local `main` may be stale
(`intent.md ## Answers, câu 2`). Paths are compared only against the `files` `gh` returned,
which may be fewer than `changedFiles` (spec R5).

`read` never raises. Anything that keeps the comparison from being made is `unread`, with
the reason.
"""

from __future__ import annotations

import json
from typing import Any

from coscc.github import prcomment

FIELDS = "changedFiles,additions,deletions,files"
COUNTS = ("files", "additions", "deletions")


def argv(url: str) -> list[str]:
    """The one `gh` call this module makes."""
    return ["pr", "view", url, "--json", FIELDS]


def _count(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def compare(scope: Any, got: Any) -> dict[str, Any]:
    """`pr.md`'s scope, as `cos.mjs pr-text` read it, against `gh`'s JSON. Pure.

    `github` is kept whenever `gh` gave the three counts, even when `pr.md` cannot be read:
    the outcome is measured on those numbers.
    """
    if not isinstance(got, dict) or not all(_count(got.get(k)) for k in ("changedFiles", "additions", "deletions")):
        return {"verdict": "unread", "detail": "gh pr view did not return changedFiles, additions and deletions as numbers"}
    files = got.get("files")
    if not isinstance(files, list) or not all(isinstance(f, dict) and isinstance(f.get("path"), str) for f in files):
        return {"verdict": "unread", "detail": "gh pr view did not return files as a list of paths"}
    github = {"files": got["changedFiles"], "additions": got["additions"], "deletions": got["deletions"]}
    if (
        not isinstance(scope, dict)
        or not all(_count(scope.get(k)) for k in COUNTS)
        or not isinstance(scope.get("paths"), list)
        or not all(isinstance(p, str) for p in scope["paths"])
    ):
        return {"github": github, "verdict": "unread", "detail": "pr.md ## Scope of the diff does not follow write-pr's grammar"}
    differ = [k for k in COUNTS if scope[k] != github[k]]
    mine, theirs = set(scope["paths"]), {f["path"] for f in files}
    only_in, only_on = sorted(mine - theirs), sorted(theirs - mine)
    if not (differ or only_in or only_on):
        return {"github": github, "verdict": "match"}
    return {
        "github": github,
        "verdict": "mismatch",
        "differ": differ,
        "only_in_pr_md": only_in,
        "only_on_github": only_on,
    }


async def read(url: str, scope: Any, cwd: str, run: prcomment.Run | None = None) -> dict[str, Any]:
    """Ask `gh` for the pull request's counts and compare. Never raises.

    `run` defaults to `prcomment._gh`, looked up at call time as `prsync.sync` does, so the
    timeout is `prcomment.TIMEOUT`.
    """
    try:
        run = run or prcomment._gh
        if not url or not prcomment.PR_URL_RE.match(url):
            return {"verdict": "unread", "detail": f"not a pull request URL: {url!r}"}
        got = await prcomment._call(run, argv(url), cwd, None)
        if isinstance(got, str):
            return {"verdict": "unread", "detail": got}
        code, out, err = got
        if code != 0:
            return {"verdict": "unread", "detail": prcomment._said(code, out, err)}
        try:
            data = json.loads(out)
        except (json.JSONDecodeError, ValueError) as e:
            return {"verdict": "unread", "detail": f"gh pr view did not return JSON: {e}"}
        return compare(scope, data)
    except Exception as e:  # noqa: BLE001 — the step is done whatever this does
        return {"verdict": "unread", "detail": str(e) or type(e).__name__}
