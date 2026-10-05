"""The pure part of cutting a release: the states, the rules that refuse a press, the version
files' edits and the board block's shape. Nothing here calls `git`, `gh` or the loop."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, get_args

from coscc.kernel import OwnTree

TREE = "release"
STATES = ("nothing", "ready", "pr-open", "merged-untagged", "tagged", "published", "unknown")
Outcome = Literal["opened", "merged", "tagged", "refused", "failed"]
OUTCOMES: tuple[Outcome, ...] = get_args(Outcome)
PHASES = ("prepare", "publish")
# The button each state carries; a state absent here has none.
BUTTON = {"ready": "prepare", "pr-open": "publish", "merged-untagged": "publish"}

# The four files a release commit may change, and nothing else.
VERSION_FILES = ("pyproject.toml", "package.json", "package-lock.json", "uv.lock")
BRANCH_PREFIX = "chore/release-"

# Said beside the button, before it is pressed.
CONSEQUENCE = "Commits, pushes, merges and tags on main with this machine's gh login."
# The page shows `CONSEQUENCE` beside the button; this whole string is on the board.
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
# The `(#N)` GitHub's squash puts at the end of a subject.
_PR_NUMBER = re.compile(r"\(#(\d+)\)\s*$")
_GREEN = ("pass", "skipping")


class ReleaseError(Exception):
    """A `gh`, loop or `uv` call that failed, carrying the tool's own words."""


def own(path: Path) -> OwnTree:
    """The release tree at `path` as the kernel's writers want it: the release branch and tag
    shapes, and the version files, nothing else."""
    return OwnTree(path, _BRANCH, _TAG, VERSION_FILES)


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


def pr_number_of(subject: str) -> int | None:
    """The `(#N)` GitHub's squash puts at the end of a subject, or None."""
    m = _PR_NUMBER.search(subject or "")
    return int(m.group(1)) if m else None


def match_commits(commits: list[dict], units: list[dict]) -> dict[str, list[dict]]:
    """A commit belongs to a unit when the `(#N)` its subject ends in is the pull request the store
    names for it (`pr_number_of`). Any other commit is kept, in `unmatched`.
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
        n = pr_number_of(subject)
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


def release_prs(prs: list[dict]) -> list[dict]:
    """The open pull requests whose head branch is a release branch, from `open_prs`'s one call."""
    return [r for r in prs if str(r.get("headRefName") or "").startswith(BRANCH_PREFIX)]


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


def empty_block(state: str, reason: str) -> ReleaseView:
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
        "consequence": CONSEQUENCE,
        "release_url": "",
        "workflow": "",
        "workflow_url": "",
    }
