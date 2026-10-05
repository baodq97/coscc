"""Cutting a release from the board.

Not a stage; writes no artifact. A release is two presses: *Prepare* opens a pull request that
changes the five declared versions and nothing else, and *Merge and tag* merges it once its
required checks are green and pushes `vX.Y.Z` onto the merge commit (the tag builds the release).

Every press leaves one `release` record in the run log. State is read again from git and `gh`
on every board read, never from memory.

Grammar is the loop's (`check-tag`, `check-branch`, `check-version` of `python -m coscc.loop`). The regular expressions
here only sort versions and keep a string from being read as a flag.

Pure functions come first; the `gh`, loop and `uv` calls after them.
"""

from __future__ import annotations

import asyncio
import json
import re
import tomllib
from pathlib import Path
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Literal,
    NotRequired,
    TypedDict,
    get_args,
)

from coscc.agent.harness import child_env
from coscc.config import Config
from coscc.git import gh, gitops
from coscc.git.gitops import GitError
from coscc.github import integrate
from coscc.github.integrate import open_prs_once
from coscc.kernel import Invalid
from coscc.loop import run
from coscc.store.db import Busy
from coscc.store.journal import BadRecord, Journal
from coscc.units import BadUnit, worktrees
from coscc.units import board as board_reader
from coscc.units.board import CONSEQUENCE, Unavailable
from coscc.units.read import Asked
from coscc.units.workspaces import Workspaces

STATES = ("nothing", "ready", "pr-open", "merged-untagged", "tagged", "published", "unknown")
Outcome = Literal["opened", "merged", "tagged", "refused", "failed"]
OUTCOMES: tuple[Outcome, ...] = get_args(Outcome)
PHASES = ("prepare", "publish")
# The button each state carries; a state absent here has none.
BUTTON = {"ready": "prepare", "pr-open": "publish", "merged-untagged": "publish"}

# The four files a release commit may change, and nothing else.
VERSION_FILES = ("pyproject.toml", "package.json", "package-lock.json", "uv.lock")
BRANCH_PREFIX = "chore/release-"

# Seconds. `uv lock` resolves again and may reach the network. Chosen, not measured.
LOCK_TIMEOUT = 300.0
# Seconds. The loop child reads a few files and prints one line. Chosen, not measured.
COS_TIMEOUT = 30.0

# The page shows `CONSEQUENCE["release"]` beside the button; this whole string is on the board.
WARNING = (
    "Whoever holds the password or a live session can open a pull request, merge it into main "
    "and push a tag — publishing a release — under this machine's gh login. The default bind "
    "is 0.0.0.0; COS_HOST=127.0.0.1 keeps the port on loopback. No ruleset protects v* tags."
)

_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_BRANCH = re.compile(r"^chore/release-(\d+)-(\d+)-(\d+)$")
# Conventional Commits' `feat`, with or without a scope and `!`.
_FEAT = re.compile(r"^feat(?:\([^)]*\))?!?:")
_URL_NUMBER = re.compile(r"/pull/(\d+)\s*$")
_GREEN = ("pass", "skipping")


class ReleaseError(Exception):
    """A `gh`, loop or `uv` call that failed, carrying the tool's own words."""


def version_of(text: str) -> tuple[int, int, int] | None:
    """`X.Y.Z` or `vX.Y.Z` as three integers, for ordering only; None otherwise."""
    m = _TAG.fullmatch(text or "") or _VERSION.fullmatch(text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def candidates(tags: list[str]) -> list[str]:
    """Every `vX.Y.Z` among `tags`, highest first. `check-tag` still decides which is a release;
    the caller asks it in this order and keeps the first.
    """
    shaped = [t for t in tags if _TAG.fullmatch(t or "")]
    return sorted(shaped, key=lambda t: version_of(t) or (0, 0, 0), reverse=True)


def branch_name(version: str) -> str:
    """`chore/release-X-Y-Z`. `check-branch` is asked about it before it is cut."""
    return BRANCH_PREFIX + version.replace(".", "-")


def version_of_branch(branch: str) -> str:
    """`X.Y.Z` from `chore/release-X-Y-Z`, or `""`."""
    m = _BRANCH.fullmatch(branch or "")
    return ".".join(m.groups()) if m else ""


def is_feat(subject: str) -> bool:
    return bool(_FEAT.match(subject or ""))


def match_commits(commits: list[dict], units: list[dict]) -> dict[str, list[dict]]:
    """A commit belongs to a unit when the `(#N)` its subject ends in is the pull request the store
    names for it (`integrate.pr_number_of`). Any other commit is kept, in `unmatched`.
    """
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
            matched.append(
                {
                    "name": str(u.get("name") or ""),
                    "type": str(u.get("type") or ""),
                    "pr": n,
                    "sha": sha,
                    "subject": subject,
                }
            )
    return {"units": matched, "unmatched": unmatched}


def propose(last_tag: str, units: list[dict], unmatched: list[dict]) -> tuple[str, str]:
    """`(proposed, reason)`.

    Minor when a unit is `Type: feat` or an unmatched subject opens with `feat`; patch when
    anything else is new; nothing when nothing is. Never major.
    """
    v = version_of(last_tag)
    if v is None:
        return "", "no release yet"
    if not units and not unmatched:
        return "", f"nothing new since {last_tag}"
    x, y, z = v
    if any(u.get("type") == "feat" for u in units) or any(
        is_feat(c.get("subject", "")) for c in unmatched
    ):
        return f"{x}.{y + 1}.0", ""
    return f"{x}.{y}.{z + 1}", ""


def version_problem(
    version: str, check_tag_code: int, check_tag_out: str, last_tag: str, on_remote: bool
) -> str:
    """In its order: `""` when `version` may be released."""
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
    check_version: tuple[int, str],
    version_problem_: str,
    state: str,
    unreadable: str = "",
) -> str:
    """In its order: the first condition that does not hold, or `""`.

    `unreadable` is why the facts could not be read (`gh`, git or `pyproject.toml`, the tool's
    own words), so check-version is never named for it.
    """
    if active:
        return "a release is already running for this workspace"
    if phase == "prepare" and open_release_pr is not None:
        return f"a release pull request is already open: #{open_release_pr.get('number')}"
    if unreadable:
        return f"the release could not be read: {unreadable}"
    code, said = check_version
    if code != 0:
        return f"check-version on origin/main failed: {said.strip() or f'exit {code}'}"
    if version_problem_:
        return version_problem_
    if BUTTON.get(state) != phase:
        return f"the release is {state}, which has no {'Prepare' if phase == 'prepare' else 'Merge and tag'} button"
    return ""


def checks_problem(checks: list[dict] | str | None) -> str:
    """`""` when every required check is green, else why not."""
    if isinstance(checks, str):
        return checks
    if not checks:
        return "the pull request has no required checks"
    red = [
        str(c.get("name") or "?")
        for c in checks
        if str(c.get("bucket") or "") in ("fail", "cancel")
    ]
    if red:
        return "required checks failed: " + ", ".join(red)
    waiting = [
        str(c.get("name") or "?") for c in checks if str(c.get("bucket") or "") not in _GREEN
    ]
    if waiting:
        return "required checks are still running: " + ", ".join(waiting)
    return ""


def publish_problem(
    checks: list[dict] | str | None, pr_head: str, opened_head: str, tag_known: bool, version: str
) -> str:
    """`""` when *Merge and tag* may run on an open release pull request."""
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
    """The state, its reason and the version it is about, from what git and `gh` said.

    Order: an open release pull request; a version on `origin/main` above the last tag that
    has no tag; new commits; the last tag this app pushed; nothing.
    """
    if open_pr is not None:
        version = version_of_branch(str(open_pr.get("headRefName") or ""))
        return {
            "state": "pr-open",
            "reason": f"release pull request #{open_pr.get('number')} is open",
            "version": version,
        }
    if not last_tag:
        return {"state": "nothing", "reason": "no release yet", "version": ""}
    main, last = version_of(main_version or ""), version_of(last_tag)
    if main is not None and last is not None and main > last and f"v{main_version}" not in tags:
        return {
            "state": "merged-untagged",
            "reason": f"{main_version} is on main and v{main_version} is not tagged",
            "version": str(main_version),
        }
    proposed, why = propose(last_tag, units, unmatched)
    if proposed:
        return {"state": "ready", "reason": "", "version": proposed}
    rec = last_record or {}
    if rec.get("outcome") == "tagged" and f"v{rec.get('version')}" == last_tag:
        return {
            "state": "tagged",
            "reason": f"{last_tag} was pushed",
            "version": str(rec.get("version") or ""),
        }
    return {"state": "nothing", "reason": why, "version": ""}


_PROJECT_VERSION = re.compile(r'^\s*"?version"?\s*[:=]\s*"([^"]*)",?\s*$')


def extra_diff(diff_text: str, old: str, new: str) -> list[str]:
    """Every file or line in `git diff -U0` that is not `old` turning into `new` on a version line
    of one of `VERSION_FILES`. Empty when the commit may be made.
    """
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
        elif line.startswith(
            (
                "--- ",
                "+++ ",
                "@@",
                "index ",
                "new file",
                "deleted file",
                "similarity",
                "rename ",
                "old mode",
                "new mode",
            )
        ):
            continue
        elif line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    close()
    return extra


def pr_body(version: str, units: list[dict], unmatched: list[dict]) -> str:
    """The pull request's body lists what the release carries."""
    lines = [
        f"Release {version}, prepared from the coscc board.",
        "",
        f"## Units ({len(units)})",
        "",
    ]
    lines += [
        f"- {u['name']} ({u.get('type') or 'no type'}) #{u['pr']} {u['sha'][:7]}" for u in units
    ] or ["- none"]
    lines += ["", f"## Commits with no unit ({len(unmatched)})", ""]
    lines += [f"- {c['sha'][:7]} {c['subject']}" for c in unmatched] or ["- none"]
    return "\n".join(lines) + "\n"


def record(
    *,
    workspace: str,
    phase: str,
    version: str,
    outcome: Outcome,
    proposed: str = "",
    last_tag: str = "",
    units: list[dict] | None = None,
    commits: list[dict] | None = None,
    pr: int | None = None,
    head: str = "",
    merge_sha: str = "",
    detail: str = "",
) -> dict[str, Any]:
    """The one record every press leaves, whatever happened. `unit` is empty and `stage` is
    `release`, so the run log's columns are filled; the time is `Journal.append`'s.
    """
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
        if (
            r.get("kind") == "release"
            and r.get("outcome") == "opened"
            and r.get("version") == version
        ):
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
            raise ReleaseError(f'{name} does not carry "version": "{old}" where the loop reads it')
        return out
    raise ReleaseError(f"not a version file: {name}")


async def _gh(argv: list[str], cwd: str) -> tuple[int, str, str]:
    """One `gh` call; one that could not be made is a `ReleaseError`."""
    got = await gh.call(gh.run, argv, cwd)
    if isinstance(got, str):
        raise ReleaseError(got)
    return got


def release_prs(prs: list[dict]) -> list[dict]:
    """The open pull requests whose head branch is a release branch, from `open_prs`'s one call."""
    return [r for r in prs if str(r.get("headRefName") or "").startswith(BRANCH_PREFIX)]


async def merged_release_pr(root: str, branch: str) -> dict:
    """The merged pull request of one release branch, `{number, merge_sha, head}`."""
    code, out, err = await _gh(
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--head",
            branch,
            "--json",
            "number,mergeCommit,headRefOid",
            "--limit",
            "5",
        ],
        root,
    )
    if code != 0:
        raise ReleaseError(gh.said(code, out, err))
    try:
        rows = json.loads(out or "[]")
    except ValueError as e:
        raise ReleaseError(f"gh pr list did not return JSON: {e}") from e
    for r in rows if isinstance(rows, list) else []:
        sha = str((r.get("mergeCommit") or {}).get("oid") or "")
        if sha:
            return {
                "number": r.get("number"),
                "merge_sha": sha,
                "head": str(r.get("headRefOid") or ""),
            }
    raise ReleaseError(f"no merged pull request was found for {branch}")


async def create_pr(tree: str, branch: str, title: str, body: str) -> int:
    """The pull request's number, read off the URL `gh` prints."""
    code, out, err = await _gh(
        ["pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body], tree
    )
    if code != 0:
        raise ReleaseError(gh.said(code, out, err))
    m = _URL_NUMBER.search(out.strip().splitlines()[-1] if out.strip() else "")
    if not m:
        raise ReleaseError(f"gh pr create printed no pull request URL: {out.strip()[:200]}")
    return int(m.group(1))


async def merge_pr(tree: str, n: int, head: str) -> None:
    """Squash, delete the branch, and only if the head is still the one the app pushed."""
    code, out, err = await _gh(
        ["pr", "merge", str(int(n)), "--squash", "--delete-branch", "--match-head-commit", head],
        tree,
    )
    if code != 0:
        raise ReleaseError(gh.said(code, out, err))


async def merge_commit(tree: str, n: int) -> str:
    """The merge commit, asked up to `integrate.POLL_TRIES` times."""
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
            said = gh.said(code, out, err)
        if attempt + 1 < integrate.POLL_TRIES:
            await asyncio.sleep(integrate.POLL_DELAY)
    raise ReleaseError(f"no merge commit for #{n}: {said}")


async def release_status(root: str, tag: str) -> dict[str, str]:
    """`{release, release_url, workflow, workflow_url}`, each `""` when unread."""
    out_: dict[str, str] = {"release": "", "release_url": "", "workflow": "", "workflow_url": ""}
    try:
        code, out, _ = await _gh(["release", "view", tag, "--json", "url,isDraft"], root)
        if code == 0:
            data = json.loads(out or "{}")
            out_["release"] = "draft" if data.get("isDraft") else "published"
            out_["release_url"] = str(data.get("url") or "")
        code, out, _ = await _gh(
            [
                "run",
                "list",
                "--workflow",
                "release.yml",
                "--branch",
                tag,
                "--json",
                "status,conclusion,url",
                "--limit",
                "1",
            ],
            root,
        )
        rows = json.loads(out or "[]") if code == 0 else []
        if isinstance(rows, list) and rows:
            row = rows[0]
            out_["workflow"] = str(row.get("conclusion") or row.get("status") or "")
            out_["workflow_url"] = str(row.get("url") or "")
    except ReleaseError, ValueError, AttributeError:
        pass
    return out_


async def cos(where: Path, *args: str, timeout: float = COS_TIMEOUT) -> tuple[int, str]:
    """`python -m coscc.loop <args>` of this app with `where` as its cwd: the checkout's version
    files are read, its code is never run. `(exit code, stdout or stderr)`.
    """
    try:
        got = await run.ask(list(args), cwd=Path(where), timeout=timeout)
    except OSError as e:
        return 127, f"python -m coscc.loop could not be started: {e}"
    except TimeoutError:
        return 124, f"python -m coscc.loop did not finish within {timeout:.0f}s"
    text = got.out.strip() if got.code == 0 else (got.err.strip() or got.out.strip())
    return got.code, text


async def _run(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=child_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
    text = (
        out.decode(errors="replace").strip()
        if code == 0
        else (err.decode(errors="replace").strip() or out.decode(errors="replace").strip())
    )
    return code, text


async def set_versions(tree: Path, old: str, new: str) -> None:
    """The three hand-edited files, then `uv lock`. No `uv sync`, `npm ci` or build."""
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


# -- the board's *Release* block and its two presses ---------------------------

PrsOnce = Callable[[], Awaitable["list[dict[str, Any]] | str"]]


class ReleaseUnit(TypedDict):
    name: str
    type: str
    pr: int | None
    sha: str
    subject: str


class Commit(TypedDict):
    sha: str
    subject: str


class Check(TypedDict):
    name: str
    bucket: str


class ReleaseView(TypedDict):
    """The board's `release` block: what a release would gather since `last_tag`, the one
    button the state offers (`prepare` or `publish`) and why it may not be pressed."""

    state: str
    reason: str
    last_tag: str
    units: list[ReleaseUnit]
    unmatched: list[Commit]
    count: int
    proposed: str
    version: str
    pr: int | None
    checks: list[Check]
    head: str
    button: str
    enabled: bool
    disabled_reason: str
    warning: str
    consequence: str
    release_url: str
    workflow: str
    workflow_url: str
    main_version: NotRequired[str]


def _empty_block(state: str, reason: str) -> dict[str, Any]:
    return {
        "state": state,
        "reason": reason,
        "last_tag": "",
        "units": [],
        "unmatched": [],
        "count": 0,
        "proposed": "",
        "version": "",
        "pr": None,
        "checks": [],
        "head": "",
        "button": "",
        "enabled": False,
        "disabled_reason": "",
        "warning": WARNING,
        "consequence": CONSEQUENCE["release"],
        "release_url": "",
        "workflow": "",
        "workflow_url": "",
    }


class Release:
    def __init__(self, config: Config, ws: Workspaces, refuse_updating: Callable[[], None]) -> None:
        self.config = config
        self.ws = ws
        self.refuse_updating = refuse_updating
        # Journal keys with a release press running now, checked and marked with no `await` between.
        self._releasing: set[str] = set()
        # The block's `gh pr checks` and `gh release view` answers, for a board read.
        self.details = Asked()

    def _release_records(self, journal: Journal | None, key: str) -> list[dict[str, Any]]:
        if journal is None:
            return []
        try:
            return journal.records(key, kind="release")
        except Busy:
            return []

    async def _release_facts(
        self,
        root: Path,
        units_: list[dict[str, Any]],
        prs: PrsOnce,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """What git and `gh` say now, and the state `release.classify` makes of it.

        Reads only, against the `origin/main` and tags the last fetch brought. A block whose `state`
        is `unknown` or `nothing` carries only its reason. `prs` is awaited only once a release tag
        is found, so a workspace never released asks `gh` nothing here.
        """
        try:
            origin = await gitops.rev_parse(root, "refs/remotes/origin/main")
            tags = await gitops.release_tags(root, origin)
        except GitError as e:
            return _empty_block("unknown", str(e))
        last_tag = ""
        for tag in candidates(tags):
            code, said = await cos(root, "check-tag", tag)
            if code == 0 and said.strip() == "release":
                last_tag = tag
                break
        if not last_tag:
            return _empty_block("nothing", "no release yet")
        got = await prs()
        if isinstance(got, str):
            return {**_empty_block("unknown", got), "last_tag": last_tag}
        open_prs = release_prs(got)
        try:
            tag_sha = await gitops.rev_parse(root, f"refs/tags/{last_tag}")
            commits = await gitops.commits_between(root, tag_sha, origin)
            main_version = str(
                tomllib.loads(await gitops.show_file(root, origin, "pyproject.toml"))
                .get("project", {})
                .get("version")
                or ""
            )
        except (GitError, tomllib.TOMLDecodeError) as e:
            return {**_empty_block("unknown", str(e)), "last_tag": last_tag}
        matched = match_commits(commits, units_)
        verdict = classify(
            last_tag=last_tag,
            units=matched["units"],
            unmatched=matched["unmatched"],
            open_pr=open_prs[0] if open_prs else None,
            main_version=main_version,
            tags=tags,
            last_record=records[-1] if records else None,
        )
        block = {
            **_empty_block(verdict["state"], verdict["reason"]),
            "last_tag": last_tag,
            "units": matched["units"],
            "unmatched": matched["unmatched"],
            "count": len(matched["units"]),
            "version": verdict["version"],
            "proposed": verdict["version"] if verdict["state"] == "ready" else "",
            "main_version": main_version,
            "origin_sha": origin,
            "tags": tags,
            "_prs": got,
        }
        return block

    async def _release_detail(
        self, root: Path, block: dict[str, Any], records: list[dict[str, Any]], fresh: bool
    ) -> None:
        """The button, whether it may be pressed, and the workflow. Only the state that needs one asks
        `gh`, through `details`: waited on the first time a pull request's head or a tag is asked,
        or when `fresh`; else held and asked again in the background.
        """
        state = block["state"]
        block["button"] = BUTTON.get(state, "")
        block["enabled"] = bool(block["button"])
        if state == "pr-open":
            open_pr = release_prs(block.pop("_prs", []) or [])
            row = open_pr[0] if open_pr else {}
            number = row.get("number")
            block["pr"] = number
            block["head"] = str(row.get("headRefOid") or "")

            async def _ask_checks(number: Any) -> list[dict[str, Any]] | str:
                try:
                    return await integrate.required_checks(str(root), int(number))
                except (integrate.IntegrateError, TypeError, ValueError) as e:
                    return str(e)

            checks: list[dict[str, Any]] | str = (
                "no open release pull request"
                if number is None
                else await self.details.get(
                    (str(root), "checks", str(number), block["head"]),
                    lambda: _ask_checks(number),
                    fresh,
                )
            )
            block["checks"] = checks if isinstance(checks, list) else []
            opened, _ = opened_head(records, block["version"])
            why = publish_problem(
                checks,
                block["head"],
                opened,
                f"v{block['version']}" in block.get("tags", []),
                block["version"],
            )
            block["enabled"], block["disabled_reason"] = not why, why
        elif state == "tagged":
            tag = block["last_tag"]
            status = await self.details.get(
                (str(root), "status", tag), lambda: release_status(str(root), tag), fresh
            )
            block.update(status)
            if status["release"] == "published":
                block["state"], block["reason"] = "published", f"{block['last_tag']} is published"

    async def attach_release(
        self,
        cwd: str,
        units_: list[dict[str, Any]],
        journal: Journal | None,
        key: str,
        prs: PrsOnce,
        fresh: bool = False,
    ) -> dict[str, Any] | None:
        """The board's `release` block, or None for a workspace that is not a git checkout.

        Costs, at most: `git` reads, one `coscc.loop check-tag` per candidate tag, and once a
        release tag is found the board's shared `gh pr list`, then `gh pr checks` on an open release
        pull request or `gh release view` and `gh run list` after a tag this app pushed; each `gh`
        answer is held, and waited on only the first time or when `fresh`. No fetch.
        """
        root = Path(cwd).expanduser().resolve()
        if not (root / ".git").exists():
            return None
        records = self._release_records(journal, key)
        block = await self._release_facts(root, units_, prs, records)
        await self._release_detail(root, block, records, fresh)
        for k in ("_prs", "tags", "origin_sha"):
            block.pop(k, None)
        return block

    def _release_start(self, cwd: str) -> tuple[Journal, str, Path]:
        self.ws.check(cwd)
        self.refuse_updating()
        journal = self.ws.journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a release cannot be recorded — set COS_WORKING_DIR"
            )
        return journal, self.ws.key(cwd), Path(cwd).expanduser().resolve()

    def release_tree_path(self, cwd: str) -> Path:
        """`worktrees.release_path` for `cwd`, worked out again on every call: it is the `expected`
        each writing `gitops` function checks the tree it was handed against.
        """
        try:
            return worktrees.release_path(cwd, self.config.data_dir)
        except BadUnit as e:
            raise GitError(str(e)) from e

    async def _release_tree_fresh(self, cwd: str, root: Path, sha: str) -> Path:
        """The release worktree, detached at `sha`; a tree left over is removed first."""
        tree = self.release_tree_path(cwd)
        await self._release_tree_gone(cwd, root, tree)
        tree.parent.mkdir(parents=True, exist_ok=True)
        await gitops.worktree_add(root, tree, sha)
        return tree

    async def _release_tree_gone(self, cwd: str, root: Path, tree: Path) -> None:
        listed = {str(Path(t["path"]).resolve()) for t in await gitops.worktree_list(root)}
        if str(tree.resolve()) in listed:
            await gitops.release_tree_remove(root, tree, self.release_tree_path(cwd))

    async def _release_press(  # noqa: PLR0915 - still to split
        self,
        cwd: str,
        phase: str,
        version: str,
    ) -> tuple[Journal, str, Path, dict[str, Any], Path, str, Callable[..., dict[str, Any]]]:
        """Everything asked before a press changes anything: the fetch, the facts, the release tree at
        `origin/main` and the loop there. Refuses with one `refused` record.
        """
        journal, key, root = self._release_start(cwd)
        version = str(version or "").strip()
        ctx: dict[str, Any] = {}

        def write(outcome: Outcome, **fields: Any) -> dict[str, Any]:
            rec = record(
                workspace=key, phase=phase, version=version, outcome=outcome, **{**ctx, **fields}
            )
            try:
                return journal.append(rec)
            except BadRecord, Busy:
                return rec

        # Check-and-mark with no `await` between, as `Holds.take` does.
        if key in self._releasing:
            reason = refusal(
                active=True,
                phase=phase,
                open_release_pr=None,
                check_version=(0, ""),
                version_problem_="",
                state="",
            )
            write("refused", detail=reason)
            raise Invalid(reason)
        self._releasing.add(key)
        try:
            try:
                await gitops.fetch_with_tags(root)
                data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
            except (GitError, Unavailable) as e:
                write("failed", detail=f"could not read the workspace: {e}")
                raise Invalid(f"could not read the workspace: {e}") from e
            # Asked before the facts, tag or no tag: an open release pull request is a refusal reason.
            # `_release_facts` gets the same answer, not a second call.
            prs_once = open_prs_once(str(root))
            prs = await prs_once()
            records = self._release_records(journal, key)
            facts = await self._release_facts(root, data["units"], prs_once, records)
            ctx.update(
                proposed=facts["proposed"],
                last_tag=facts["last_tag"],
                units=facts["units"],
                commits=facts["unmatched"],
            )
            open_rel = release_prs(prs) if isinstance(prs, list) else []
            tree: Path | None = None
            # Asked in the tree below; `unreadable` refuses first when there is none.
            check_version = (1, "")
            problem = ""
            if facts["state"] != "unknown":
                try:
                    tree = await self._release_tree_fresh(cwd, root, facts["origin_sha"])
                except GitError as e:
                    write("failed", detail=f"could not open the release worktree: {e}")
                    raise Invalid(f"could not open the release worktree: {e}") from e
                check_version = await cos(tree, "check-version")
                code, said = await cos(tree, "check-tag", f"v{version}")
                on_remote = False
                if code == 0 and said.strip() == "release":
                    try:
                        on_remote = await gitops.remote_has_tag(tree, f"v{version}")
                    except GitError as e:
                        code, said = 1, f"the remote's tags could not be read: {e}"
                problem = version_problem(version, code, said, facts["last_tag"], on_remote)
                if not problem and phase == "publish" and version != facts["version"]:
                    problem = f"the release waiting to be tagged is {facts['version'] or 'none'}, not {version}"
            reason = refusal(
                active=False,
                phase=phase,
                open_release_pr=open_rel[0] if open_rel else None,
                check_version=check_version,
                version_problem_=problem,
                state=facts["state"],
                unreadable=facts["reason"] if facts["state"] == "unknown" else "",
            )
            checked: dict[str, Any] = {}
            if not reason and facts["state"] == "pr-open":
                row = open_rel[0]
                try:
                    checks: list[dict[str, Any]] | str = await integrate.required_checks(
                        str(root), int(row["number"])
                    )
                except integrate.IntegrateError as e:
                    checks = str(e)
                opened, _ = opened_head(records, version)
                reason = publish_problem(
                    checks,
                    str(row.get("headRefOid") or ""),
                    opened,
                    f"v{version}" in facts.get("tags", []),
                    version,
                )
                ctx.update(pr=row.get("number"), head=str(row.get("headRefOid") or ""))
                # The merge is pinned to the head just checked, which is the one the app pushed; the pull
                # request is not read a second time.
                checked = {"checked_pr": int(row["number"]), "checked_head": opened}
            if reason:
                if tree is not None:
                    try:
                        await self._release_tree_gone(cwd, root, tree)
                    except GitError:
                        pass
                write("refused", detail=reason)
                raise Invalid(reason)
            assert tree is not None
            old = check_version[1].split()[0] if check_version[1].split() else ""
            return journal, key, root, {**facts, **checked, "old": old}, tree, version, write
        except BaseException:
            self._releasing.discard(key)
            raise

    async def release_prepare(self, cwd: str, version: str) -> AsyncIterator[tuple[str, Any]]:
        """The first press: a `chore/release-X-Y-Z` pull request that changes the four version files
        and nothing else. One `release` record whatever happens.
        """
        journal, key, root, facts, tree, version, write = await self._release_press(
            cwd, "prepare", version
        )
        branch = branch_name(version)
        cut = False
        head = ""
        try:
            code, said = await cos(tree, "check-branch", branch)
            if code != 0:
                # Still before anything changed, so a 400 like every other refusal.
                write("refused", detail=f"check-branch refused {branch}: {said}")
                try:
                    await self._release_tree_gone(cwd, root, tree)
                except GitError:
                    pass
                raise Invalid(f"check-branch refused {branch}: {said}")
            await gitops.create_branch(tree, branch, facts["origin_sha"])
            cut = True
            await set_versions(tree, facts["old"], version)
            code, said = await cos(tree, "check-version")
            if code != 0 or (said.split() or [""])[0] != version:
                raise ReleaseError(f"check-version printed {said!r}, not {version}")
            extra = extra_diff(
                await gitops.diff_u0(tree, self.release_tree_path(cwd)), facts["old"], version
            )
            if extra:
                raise ReleaseError("the change is more than the version lines: " + "; ".join(extra))
            head = await gitops.commit_files(
                tree, self.release_tree_path(cwd), f"chore(release): {version}"
            )
            await gitops.push_branch(tree, self.release_tree_path(cwd), branch)
            number = await create_pr(
                str(tree),
                branch,
                f"chore(release): {version}",
                pr_body(version, facts["units"], facts["unmatched"]),
            )
            await gitops.detach_here(tree, self.release_tree_path(cwd))
            rec = write("opened", pr=number, head=head)
            yield ("done", {"release": rec})
        except (GitError, ReleaseError) as e:
            rec = write("failed", detail=str(e))
            try:
                await self._release_tree_gone(cwd, root, tree)
            except GitError:
                pass
            if cut:
                # Only where the app left it: at `origin/main`, or at its own commit that changes the version
                # and nothing else. Left behind, the next Prepare of this version stops at `create_branch`.
                try:
                    await gitops.delete_merged_branch(root, branch, head or facts["origin_sha"])
                except GitError:
                    pass
            yield ("done", {"release": rec})
        finally:
            self._releasing.discard(key)

    async def release_publish(self, cwd: str, version: str) -> AsyncIterator[tuple[str, Any]]:
        """The second press: merge the release pull request, then tag its merge commit. From
        `merged-untagged` it starts at the tag. Never started by anything but a request.
        """
        journal, key, root, facts, tree, version, write = await self._release_press(
            cwd, "publish", version
        )
        tag = f"v{version}"
        merged: dict[str, Any] = {}
        try:
            if facts["state"] == "pr-open":
                number, head = facts["checked_pr"], facts["checked_head"]
                await merge_pr(str(tree), number, head)
                merged = {
                    "pr": number,
                    "head": head,
                    "merge_sha": await merge_commit(str(tree), number),
                }
            else:
                found = await merged_release_pr(str(root), branch_name(version))
                merged = {
                    "pr": found["number"],
                    "head": found["head"],
                    "merge_sha": found["merge_sha"],
                }
            sha = merged["merge_sha"]
            await gitops.fetch_with_tags(root)
            origin = await gitops.rev_parse(root, "refs/remotes/origin/main")
            if not await gitops.is_ancestor(root, sha, origin):
                raise ReleaseError(f"the merge commit {sha[:7]} is not on origin/main")
            tree = await self._release_tree_fresh(cwd, root, sha)
            code, said = await cos(tree, "check-version")
            if code != 0 or (said.split() or [""])[0] != version:
                raise ReleaseError(f"check-version at {sha[:7]} printed {said!r}, not {version}")
            await gitops.push_tag(tree, self.release_tree_path(cwd), tag, sha)
            rec = write("tagged", **merged)
            try:
                await self._release_tree_gone(cwd, root, tree)
                # The tag was made on the remote only; the board reads local tags.
                await gitops.fetch_with_tags(root)
            except GitError:
                pass
            yield ("done", {"release": rec})
        except (
            GitError,
            ReleaseError,
            integrate.IntegrateError,
            IndexError,
            KeyError,
        ) as e:
            rec = write("merged" if merged else "failed", detail=str(e), **merged)
            try:
                await self._release_tree_gone(cwd, root, tree)
            except GitError:
                pass
            yield ("done", {"release": rec})
        finally:
            self._releasing.discard(key)
