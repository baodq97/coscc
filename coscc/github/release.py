"""Cutting a release from the board (`0046`).

Not a stage. `.claude/scripts/cos.mjs` defines the loop and this module adds nothing to it;
it writes no artifact. A release is two presses a person makes: *Prepare* opens a pull
request that changes the five declared versions and nothing else (R6, R7), and *Merge and
tag* merges it once its required checks are green and pushes `vX.Y.Z` onto the merge commit
(R9, R10). Pushing the tag is what builds the release, on GitHub.

Every press leaves one `release` record in the run log (R15). The state is read again from
git and `gh` on every board read, never from this process's memory (R12).

Grammar is `cos.mjs`'s, run from the workspace's own checkout (`check-tag`, `check-branch`)
and from the release worktree (`check-version`). The regular expressions here only sort
versions and keep a string from being read as a flag.

The pure functions come first; the `gh`, `node` and `uv` calls after them.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from coscc.agent.harness import child_env
from coscc.github import integrate

STATES = ("nothing", "ready", "pr-open", "merged-untagged", "tagged", "published", "unknown")
OUTCOMES = ("opened", "merged", "tagged", "refused", "failed")
PHASES = ("prepare", "publish")
# The button each state carries (R12); a state absent here has none.
BUTTON = {"ready": "prepare", "pr-open": "publish", "merged-untagged": "publish"}

# R7: the four files a release commit may change, and nothing else.
VERSION_FILES = ("pyproject.toml", "package.json", "package-lock.json", "uv.lock")
BRANCH_PREFIX = "chore/release-"
SCRIPT = Path(".claude") / "scripts" / "cos.mjs"

# Seconds. `uv lock` resolves again and may reach the network. Chosen, not measured.
LOCK_TIMEOUT = 300.0
# Seconds. `cos.mjs` reads a few files and prints one line. Chosen, not measured.
COS_TIMEOUT = 30.0

# R16, with `spec.md ## Answers, câu 4`. Since `0070` every route sits behind the master
# password, so "no login" is no longer true; what is true is said instead. The page shows
# `CONSEQUENCE["release"]` beside the button (S2); this whole string is in `/api/board`.
WARNING = (
    "Whoever holds the password or a live session can open a pull request, merge it into main "
    "and push a tag — publishing a release — under this machine's gh login. The default bind "
    "is 0.0.0.0; COS_HOST=127.0.0.1 keeps the port on loopback. No ruleset protects v* tags."
)

_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_BRANCH = re.compile(r"^chore/release-(\d+)-(\d+)-(\d+)$")
# Conventional Commits' `feat`, with or without a scope and `!` (`spec.md ## Answers, câu 3`).
_FEAT = re.compile(r"^feat(?:\([^)]*\))?!?:")
_URL_NUMBER = re.compile(r"/pull/(\d+)\s*$")
_GREEN = ("pass", "skipping")


class ReleaseError(Exception):
    """A `gh`, `node` or `uv` call that failed, carrying the tool's own words."""


# --- pure --------------------------------------------------------------------


def version_of(text: str) -> tuple[int, int, int] | None:
    """`X.Y.Z` or `vX.Y.Z` as three integers, for ordering only; None otherwise."""
    m = _TAG.fullmatch(text or "") or _VERSION.fullmatch(text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def candidates(tags: list[str]) -> list[str]:
    """Every `vX.Y.Z` among `tags`, highest first. `check-tag` still decides which is a
    release (R1 a); the caller asks it in this order and keeps the first."""
    shaped = [t for t in tags if _TAG.fullmatch(t or "")]
    return sorted(shaped, key=lambda t: version_of(t) or (0, 0, 0), reverse=True)


def branch_name(version: str) -> str:
    """`chore/release-X-Y-Z`. `cos.mjs check-branch` is asked about it before it is cut (R6.3)."""
    return BRANCH_PREFIX + version.replace(".", "-")


def version_of_branch(branch: str) -> str:
    """`X.Y.Z` from `chore/release-X-Y-Z`, or `""`."""
    m = _BRANCH.fullmatch(branch or "")
    return ".".join(m.groups()) if m else ""


def is_feat(subject: str) -> bool:
    return bool(_FEAT.match(subject or ""))


def match_commits(commits: list[dict], units: list[dict]) -> dict[str, list[dict]]:
    """R1 b, d; R2. A commit belongs to a unit when the `(#N)` its subject ends in is the
    pull request the store names for it (`integrate.pr_number_of`). Any other commit is
    kept, in `unmatched`."""
    by_pr: dict[int, dict] = {}
    for u in units:
        number = (u.get("pr") or {}).get("number") if isinstance(u.get("pr"), dict) else None
        if number is not None:
            by_pr[int(number)] = u
    matched: list[dict] = []
    unmatched: list[dict] = []
    for c in commits:
        subject = str(c.get("subject") or "")
        sha = str(c.get("sha") or "")
        n = integrate.pr_number_of(subject)
        u = by_pr.get(n) if n is not None else None
        if u is None:
            unmatched.append({"sha": sha, "subject": subject})
        else:
            matched.append({"name": str(u.get("name") or ""), "type": str(u.get("type") or ""),
                            "pr": n, "sha": sha, "subject": subject})
    return {"units": matched, "unmatched": unmatched}


def propose(last_tag: str, units: list[dict], unmatched: list[dict]) -> tuple[str, str]:
    """R4, widened by `spec.md ## Answers, câu 3`: `(proposed, reason)`.

    Minor when a unit is `Type: feat` or an unmatched subject opens with `feat`; patch when
    anything else is new; nothing when nothing is. Never major: no answer asked for one."""
    v = version_of(last_tag)
    if v is None:
        return "", "no release yet"
    if not units and not unmatched:
        return "", f"nothing new since {last_tag}"
    x, y, z = v
    if any(u.get("type") == "feat" for u in units) or any(is_feat(c.get("subject", "")) for c in unmatched):
        return f"{x}.{y + 1}.0", ""
    return f"{x}.{y}.{z + 1}", ""


def version_problem(version: str, check_tag_code: int, check_tag_out: str, last_tag: str, on_remote: bool) -> str:
    """R5, in its order: `""` when `version` may be released."""
    if check_tag_code != 0:
        return f"v{version} is not a release tag: {check_tag_out.strip() or 'check-tag refused it'}"
    if check_tag_out.strip() == "prerelease":
        return f"v{version} is a prerelease, and only a plain release is cut here"
    have, last = version_of(version), version_of(last_tag)
    if have is None or (last is not None and have <= last):
        return f"{version} is not greater than {last_tag}"
    if on_remote:
        return f"the tag v{version} is already on the remote"
    return ""


def refusal(
    *,
    active: bool,
    phase: str,
    open_release_pr: dict | None,
    has_script: bool,
    check_version: tuple[int, str],
    version_problem_: str,
    state: str,
) -> str:
    """R13, in its order: the first condition that does not hold, or `""`."""
    if active:
        return "a release is already running for this workspace"
    if phase == "prepare" and open_release_pr is not None:
        return f"a release pull request is already open: #{open_release_pr.get('number')}"
    if not has_script:
        return f"this workspace has no {SCRIPT.as_posix()}"
    code, said = check_version
    if code != 0:
        return f"check-version on origin/main failed: {said.strip() or f'exit {code}'}"
    if version_problem_:
        return version_problem_
    if BUTTON.get(state) != phase:
        return f"the release is {state}, which has no {'Prepare' if phase == 'prepare' else 'Merge and tag'} button"
    return ""


def checks_problem(checks: list[dict] | str | None) -> str:
    """R9 a: `""` when every required check is green, else why not."""
    if isinstance(checks, str):
        return checks
    if not checks:
        return "the pull request has no required checks"
    red = [str(c.get("name") or "?") for c in checks if str(c.get("bucket") or "") in ("fail", "cancel")]
    if red:
        return "required checks failed: " + ", ".join(red)
    waiting = [str(c.get("name") or "?") for c in checks if str(c.get("bucket") or "") not in _GREEN]
    if waiting:
        return "required checks are still running: " + ", ".join(waiting)
    return ""


def publish_problem(checks: list[dict] | str | None, pr_head: str, opened_head: str, tag_known: bool, version: str) -> str:
    """R9: `""` when *Merge and tag* may run on an open release pull request."""
    if not opened_head:
        return "this pull request was not opened by the app"
    if pr_head != opened_head:
        return f"the pull request's head {pr_head[:7]} is not the commit the app pushed, {opened_head[:7]}"
    if tag_known:
        return f"the tag v{version} already exists"
    return checks_problem(checks)


def classify(
    *,
    last_tag: str | None,
    units: list[dict],
    unmatched: list[dict],
    open_pr: dict | None,
    main_version: str | None,
    tags: list[str],
    last_record: dict | None,
) -> dict[str, Any]:
    """R12: the state, its reason and the version it is about, from what git and `gh` said.

    Order: an open release pull request; a version on `origin/main` above the last tag that
    has no tag; new commits; the last tag this app pushed; nothing."""
    if open_pr is not None:
        version = version_of_branch(str(open_pr.get("headRefName") or ""))
        return {"state": "pr-open", "reason": f"release pull request #{open_pr.get('number')} is open",
                "version": version}
    if not last_tag:
        return {"state": "nothing", "reason": "no release yet", "version": ""}
    main, last = version_of(main_version or ""), version_of(last_tag)
    if main is not None and last is not None and main > last and f"v{main_version}" not in tags:
        return {"state": "merged-untagged", "reason": f"{main_version} is on main and v{main_version} is not tagged",
                "version": str(main_version)}
    proposed, why = propose(last_tag, units, unmatched)
    if proposed:
        return {"state": "ready", "reason": "", "version": proposed}
    rec = last_record or {}
    if rec.get("outcome") == "tagged" and f"v{rec.get('version')}" == last_tag:
        return {"state": "tagged", "reason": f"{last_tag} was pushed", "version": str(rec.get("version") or "")}
    return {"state": "nothing", "reason": why, "version": ""}


_PROJECT_VERSION = re.compile(r'^\s*"?version"?\s*[:=]\s*"([^"]*)",?\s*$')


def extra_diff(diff_text: str, old: str, new: str) -> list[str]:
    """R7: every file or line in `git diff -U0` that is not `old` turning into `new` on a
    version line of one of `VERSION_FILES`. Empty when the commit may be made."""
    extra: list[str] = []
    current = ""
    removed: list[str] = []
    added: list[str] = []

    def close() -> None:
        if not current or current not in VERSION_FILES:
            return
        if len(removed) != len(added):
            extra.append(f"{current}: {len(removed)} line(s) removed, {len(added)} added")
        for line in removed:
            m = _PROJECT_VERSION.match(line)
            if not m or m.group(1) != old:
                extra.append(f"{current}: -{line}")
        for line in added:
            m = _PROJECT_VERSION.match(line)
            if not m or m.group(1) != new:
                extra.append(f"{current}: +{line}")

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            close()
            removed, added = [], []
            parts = line.split(" b/", 1)
            current = parts[1] if len(parts) == 2 else line
            if current not in VERSION_FILES:
                extra.append(f"{current}: not one of the version files")
        elif line.startswith(("--- ", "+++ ", "@@", "index ", "new file", "deleted file", "similarity", "rename ", "old mode", "new mode")):
            continue
        elif line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    close()
    return extra


def pr_body(version: str, units: list[dict], unmatched: list[dict]) -> str:
    """R6.9: the pull request's body lists what the release carries (R1 b, d)."""
    lines = [f"Release {version}, prepared from the coscc board.", "", f"## Units ({len(units)})", ""]
    lines += [f"- {u['name']} ({u.get('type') or 'no type'}) #{u['pr']} {u['sha'][:7]}" for u in units] or ["- none"]
    lines += ["", f"## Commits with no unit ({len(unmatched)})", ""]
    lines += [f"- {c['sha'][:7]} {c['subject']}" for c in unmatched] or ["- none"]
    return "\n".join(lines) + "\n"


def record(
    *,
    workspace: str,
    phase: str,
    version: str,
    outcome: str,
    proposed: str = "",
    last_tag: str = "",
    units: list[dict] | None = None,
    commits: list[dict] | None = None,
    pr: int | None = None,
    head: str = "",
    merge_sha: str = "",
    detail: str = "",
) -> dict[str, Any]:
    """R15: the one record every press leaves, whatever happened. `unit` is empty and
    `stage` is `release`, so the run log's columns are filled; the time is `Journal.append`'s."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}, got {outcome!r}")
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {', '.join(PHASES)}, got {phase!r}")
    return {
        "kind": "release",
        "workspace": workspace,
        "unit": "",
        "stage": "release",
        "phase": phase,
        "version": version,
        "proposed": proposed,
        "last_tag": last_tag,
        "units": list(units or []),
        "commits": list(commits or []),
        "pr": pr,
        "head": head,
        "merge_sha": merge_sha,
        "outcome": outcome,
        "detail": detail,
    }


def opened_head(records: list[dict], version: str) -> tuple[str, int | None]:
    """The head and pull request of the last `opened` record for `version`, oldest first."""
    found: tuple[str, int | None] = ("", None)
    for r in records:
        if r.get("kind") == "release" and r.get("outcome") == "opened" and r.get("version") == version:
            found = (str(r.get("head") or ""), r.get("pr"))
    return found


def set_version_text(name: str, text: str, old: str, new: str) -> str:
    """One version file with `old` turned into `new` on its project's own version line(s),
    as text, so nothing else in it moves. Raises `ReleaseError` when a line is not found."""
    if name == "pyproject.toml":
        lines = text.split("\n")
        in_project = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("["):
                in_project = stripped == "[project]"
            elif in_project and re.fullmatch(rf'version\s*=\s*"{re.escape(old)}"', stripped):
                lines[i] = line.replace(f'"{old}"', f'"{new}"', 1)
                return "\n".join(lines)
        raise ReleaseError(f'pyproject.toml has no version = "{old}" in [project]')
    if name in ("package.json", "package-lock.json"):
        want = 1 if name == "package.json" else 2
        pattern = re.compile(rf'("version":\s*"){re.escape(old)}(")')
        out, n = pattern.subn(rf"\g<1>{new}\g<2>", text, count=want)
        try:
            data = json.loads(out)
        except ValueError as e:
            raise ReleaseError(f"{name} is not JSON after the change: {e}") from e
        found = [data.get("version")]
        if name == "package-lock.json":
            found.append(((data.get("packages") or {}).get("") or {}).get("version"))
        if n != want or any(v != new for v in found):
            raise ReleaseError(f'{name} does not carry "version": "{old}" where cos.mjs reads it')
        return out
    raise ReleaseError(f"not a version file: {name}")


# --- gh, node, uv ------------------------------------------------------------


async def _gh(argv: list[str], cwd: str) -> tuple[int, str, str]:
    """One `gh` call, through `integrate._gh`: the same environment and `GH_TIMEOUT`."""
    try:
        return await integrate._gh(argv, cwd)
    except integrate.IntegrateError as e:
        raise ReleaseError(str(e)) from e


def _said(out: str, err: str) -> str:
    return integrate._said(out, err)


def release_prs(prs: list[dict]) -> list[dict]:
    """The open pull requests whose head branch is a release branch, from `open_prs`'s one call."""
    return [r for r in prs if str(r.get("headRefName") or "").startswith(BRANCH_PREFIX)]


async def merged_release_pr(root: str, branch: str) -> dict:
    """R12: the merged pull request of one release branch, `{number, merge_sha, head}`."""
    code, out, err = await _gh(
        ["pr", "list", "--state", "merged", "--head", branch,
         "--json", "number,mergeCommit,headRefOid", "--limit", "5"], root)
    if code != 0:
        raise ReleaseError(_said(out, err))
    try:
        rows = json.loads(out or "[]")
    except ValueError as e:
        raise ReleaseError(f"gh pr list did not return JSON: {e}") from e
    for r in rows if isinstance(rows, list) else []:
        sha = str((r.get("mergeCommit") or {}).get("oid") or "")
        if sha:
            return {"number": r.get("number"), "merge_sha": sha, "head": str(r.get("headRefOid") or "")}
    raise ReleaseError(f"no merged pull request was found for {branch}")


async def create_pr(tree: str, branch: str, title: str, body: str) -> int:
    """R6.9. The pull request's number, read off the URL `gh` prints."""
    code, out, err = await _gh(
        ["pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body], tree)
    if code != 0:
        raise ReleaseError(_said(out, err))
    m = _URL_NUMBER.search(out.strip().splitlines()[-1] if out.strip() else "")
    if not m:
        raise ReleaseError(f"gh pr create printed no pull request URL: {out.strip()[:200]}")
    return int(m.group(1))


async def merge_pr(tree: str, n: int, head: str) -> None:
    """R10.1. Squash, delete the branch, and only if the head is still the one the app pushed."""
    code, out, err = await _gh(
        ["pr", "merge", str(int(n)), "--squash", "--delete-branch", "--match-head-commit", head], tree)
    if code != 0:
        raise ReleaseError(_said(out, err))


async def merge_commit(tree: str, n: int) -> str:
    """R10.2. The merge commit, asked up to `integrate.POLL_TRIES` times."""
    said = ""
    for attempt in range(integrate.POLL_TRIES):
        code, out, err = await _gh(["pr", "view", str(int(n)), "--json", "state,mergeCommit"], tree)
        if code == 0:
            try:
                data = json.loads(out)
            except ValueError:
                data = {}
            sha = str((data.get("mergeCommit") or {}).get("oid") or "")
            if data.get("state") == "MERGED" and sha:
                return sha
            said = f"the pull request is {data.get('state') or 'unread'}"
        else:
            said = _said(out, err)
        if attempt + 1 < integrate.POLL_TRIES:
            await asyncio.sleep(integrate.POLL_DELAY)
    raise ReleaseError(f"no merge commit for #{n}: {said}")


async def release_status(root: str, tag: str) -> dict[str, str]:
    """R11. `{release, release_url, workflow, workflow_url}`, each `""` when unread."""
    out_: dict[str, str] = {"release": "", "release_url": "", "workflow": "", "workflow_url": ""}
    try:
        code, out, _ = await _gh(["release", "view", tag, "--json", "url,isDraft"], root)
        if code == 0:
            data = json.loads(out or "{}")
            out_["release"] = "draft" if data.get("isDraft") else "published"
            out_["release_url"] = str(data.get("url") or "")
        code, out, _ = await _gh(
            ["run", "list", "--workflow", "release.yml", "--branch", tag,
             "--json", "status,conclusion,url", "--limit", "1"], root)
        rows = json.loads(out or "[]") if code == 0 else []
        if isinstance(rows, list) and rows:
            row = rows[0]
            out_["workflow"] = str(row.get("conclusion") or row.get("status") or "")
            out_["workflow_url"] = str(row.get("url") or "")
    except (ReleaseError, ValueError, AttributeError):
        pass
    return out_


async def cos(where: Path, *args: str, timeout: float = COS_TIMEOUT) -> tuple[int, str]:
    """`node <where>/.claude/scripts/cos.mjs <args>`, never the packaged copy: the packaged
    one has no version files beside it (spec, Design). `(exit code, stdout or stderr)`."""
    script = Path(where) / SCRIPT
    if not script.is_file():
        return 2, f"no {SCRIPT.as_posix()} in this checkout"
    return await _run(["node", str(script), *args], Path(where), timeout)


async def _run(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env=child_env(), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return 127, f"{argv[0]} could not be started: {e}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"{argv[0]} did not finish within {timeout:.0f}s"
    code = proc.returncode or 0
    text = out.decode(errors="replace").strip() if code == 0 else (
        err.decode(errors="replace").strip() or out.decode(errors="replace").strip())
    return code, text


async def set_versions(tree: Path, old: str, new: str) -> None:
    """R6.5, R8: the three hand-edited files, then `uv lock`. No `uv sync`, `npm ci` or build."""
    for name in ("pyproject.toml", "package.json", "package-lock.json"):
        path = Path(tree) / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ReleaseError(f"could not read {name}: {e}") from e
        path.write_text(set_version_text(name, text, old, new), encoding="utf-8")
    code, said = await _run(["uv", "lock"], Path(tree), LOCK_TIMEOUT)
    if code != 0:
        raise ReleaseError(f"uv lock failed: {said}")
