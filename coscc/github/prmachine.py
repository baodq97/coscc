"""The pull request machine: `pr` and `ship` as the app's own actions, with no session.

`.cos/0136_transitions-are-decided-by-parsing-prose` R10, R12, R13, spec Design "Máy PR/CI":
`none → open(n, head) → merge-requested(head) → merged(commit) | closed`. Each move is a
transition through `transitions.apply`, on the artifact the loop already reads it from, so
`cos.mjs` needs no second vocabulary:

- `open` is `pr.md: accepted`, guard `branch-named`;
- `merge-requested` is `ship.md: draft`, guard `ship-ready`;
- `merged` is `ship.md: accepted`, guard `merge-read`.

Where the machine stands is a fold over those rows (`state`), never a column. The files
`pr.md` and `ship.md` are written by the app from the same values after the transition, so a
reader has prose and `cos.mjs` finds the files it checks exist; nothing reads them back to
decide (R18).

**What this module does on GitHub**: `git push` of the unit's own branch (no `--force`),
`gh pr list`/`view`/`checks` to read, `gh pr create`, and `gh pr merge --squash
--delete-branch --match-head-commit <head>` with the head this module's own read gave the
guard (R10). It merges only when guard `ship-ready` is open, and only after the
`merge-requested` row is committed (R13 step 1), so a restart can tell a merge it may have
made from one it never asked for.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coscc.data import now as _now
from coscc.git import gitops
from coscc.github import integrate
from coscc.runlog.journal import Journal
from coscc.units import transitions
from coscc.units.history import History

PR_FILE = "pr.md"
SHIP_FILE = "ship.md"
MACHINE = "pr"
# The stages the board runs through this module rather than a session (R12, R13).
STAGES = ("pr", "ship")
# What `state` answers. `none` is a unit whose pull request the app never opened, or one
# opened by a `pr` session before `0136`: `ship` then finds it by its branch.
STATES = ("none", "open", "merge-requested", "merged", "closed")

Gh = Callable[[list[str], str], Awaitable[tuple[int, str, str]]]
Push = Callable[[str, str], Awaitable[Any]]
Head = Callable[[str], Awaitable[str]]


class PrError(RuntimeError):
    """A read or a call that did not answer. Nothing was recorded after it."""


@dataclass(frozen=True)
class Unit:
    """One unit as the machine needs it. `workspace` is the key `transitions` uses; `tree`
    the checkout git and `gh` run in; `branch` the one it is on and `expected` the one
    `cos.mjs unit-branch` names for it."""

    workspace: str
    name: str
    directory: Path
    tree: str
    branch: str
    expected: str
    type: str | None


@dataclass
class Outcome:
    """What one `pr` or `ship` did. `result` is `opened`, `found`, `already`, `merged`,
    `recorded`, `refused` or `failed`; `reasons` the guard's codes when it closed."""

    result: str
    number: int | None = None
    url: str = ""
    head: str = ""
    merge_commit: str = ""
    reasons: tuple[str, ...] = ()
    detail: str = ""
    guard: str = ""
    calls: list[list[str]] = field(default_factory=list)
    # `0136` R23: the id of the transition it recorded, where it recorded one.
    transition: int | None = None

    @property
    def ok(self) -> bool:
        return self.result in ("opened", "found", "already", "merged", "recorded")

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result, "number": self.number, "url": self.url, "head": self.head,
            "merge_commit": self.merge_commit, "reasons": list(self.reasons),
            "detail": self.detail, "guard": self.guard,
        }


def title_of(name: str, type_: str | None) -> str:
    """R12: `<type>(<NNNN>): <slug, hyphens as spaces>`, the grammar `cos.mjs titleProblem`
    holds a title to (`0049`). The slug is English by `cos.mjs new-path`'s grammar."""
    number, _, slug = name.partition("_")
    return f"{type_ or 'chore'}({number}): {slug.replace('-', ' ')}"


def body_of(name: str) -> str:
    """R12: from the unit's metadata, never from a commit (`0049`)."""
    return (
        f"Work unit `{name}`.\n\n"
        "Opened by coscc, not by a session: the `pr` step is mechanical (`0136` R12). What the "
        "unit set out to do and what it measured are in its artifacts, in the app's store.\n"
    )


def render_pr(u: Unit, title: str, url: str, head: str, at: str = "") -> str:
    """`at` is when the app wrote it: a `pr` run again (`0054`) writes other bytes, so the
    artifact it made stale is not stale any more (`cos.mjs` compares hashes)."""
    return (
        f"# PR: {title}\n"
        f"Author: coscc (code, pr). Status: accepted. PR: {url}\n\n"
        f"{body_of(u.name)}\n"
        f"Opened from `{u.branch}` at `{head}`, the head the app pushed. Written {at or _now()}.\n"
    )


def _write_keeping_answers(path: Path, text: str) -> None:
    """The app's `## Answers` section of the file stays, byte for byte, below what is written
    (`.claude/CLAUDE.md`: the app never rewrites a byte above it, nor it)."""
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        old = ""
    at = old.find("\n## Answers\n")
    path.write_text(text + (old[at:] if at != -1 else ""), encoding="utf-8")


def render_ship(u: Unit, *, status: str, round_n: int | None, number: int, head: str,
                merge_commit: str = "", refused: str = "") -> str:
    """`Round:` on the header line and `Refused:` under `## What went out` are what
    `cos.mjs parseShip` reads (`0112`)."""
    lines = [
        f"# Ship: {u.name}",
        f"Author: coscc (code, ship). Status: {status}."
        + (f" Round: {round_n}" if round_n is not None else ""),
        "",
        "## What went out",
        "",
    ]
    if merge_commit:
        lines.append(f"#{number} was merged as `{merge_commit}`, pinned to head `{head}`.")
    elif refused:
        lines.append(f"Refused: {refused}")
    else:
        lines.append(f"The merge of #{number} at head `{head}` was requested.")
    return "\n".join(lines) + "\n"


def ci_of(rows: list[dict]) -> str:
    """`pending`, `green` or `red`, from `gh pr checks --required`'s buckets."""
    buckets = [str(r.get("bucket") or "") for r in rows]
    if any(b in ("fail", "cancel") for b in buckets):
        return "red"
    if any(b not in ("pass", "skipping") for b in buckets):
        return "pending"
    return "green"


def state(history: History, workspace: str, unit: str) -> dict[str, Any]:
    """Where the unit's pull request stands: a fold over the rows the machine's guards wrote.
    `ci` is the one the reader last recorded, at `head`; `None` until it has read one."""
    now: dict[str, Any] = {"state": "none"}
    for row in history.transitions(workspace, unit):
        g, artifact = row.get("guard"), row.get("artifact")
        try:
            inputs = json.loads(row.get("inputs") or "{}")
        except ValueError:
            inputs = {}
        if g == "branch-named" and artifact == PR_FILE:
            now = {"state": "open", "ci": None, **{k: inputs.get(k) for k in ("number", "url", "head")}}
        elif g == "ci-at-head" and artifact == PR_FILE:
            now = {**now, "head": inputs.get("head"), "ci": inputs.get("ci")}
        elif g == "ship-ready" and artifact == SHIP_FILE:
            now = {**now, "state": "merge-requested", "number": inputs.get("number"), "head": inputs.get("head")}
        elif g == "merge-read" and artifact == SHIP_FILE:
            now = {**now, "state": "merged", "merge_commit": inputs.get("merge_commit")}
        elif g == "close-read" and artifact == PR_FILE:
            now = {**now, "state": "closed"}
    return now


# R23: the states whose pull request the reader watches.
WATCHED = ("open", "merge-requested")
# A `ci` the reader does not read again at the same head.
SETTLED_CI = ("green", "red", "unfixable")


def watched(history: History, workspace: str) -> list[tuple[str, dict[str, Any]]]:
    """`(unit, state)` for each unit of `workspace` whose pull request is `open` or
    `merge-requested`: what the reader reads, and what R22 holds an `impl` behind."""
    with history.data.connect() as conn:
        names = [r["unit"] for r in conn.execute(
            "SELECT DISTINCT unit FROM transitions WHERE root = ? AND workspace = ? AND guard = 'branch-named'",
            (str(history.working_dir), workspace),
        ).fetchall()]
    out = []
    for name in sorted(names):
        now = state(history, workspace, name)
        if now["state"] in WATCHED and now.get("number"):
            out.append((name, now))
    return out


def files_held(history: History, workspace: str, number: int, head: str) -> list[str] | None:
    """R22: the files the reader recorded for this pull request at this head; `None` when it
    has not read them, or could not."""
    with history.data.connect() as conn:
        row = conn.execute(
            "SELECT files FROM pull_requests WHERE root = ? AND workspace = ? AND number = ? AND head = ?",
            (str(history.working_dir), workspace, int(number), str(head or "")),
        ).fetchone()
    if row is None or row["files"] is None:
        return None
    try:
        files = json.loads(row["files"])
    except ValueError:
        return None
    return [str(f) for f in files] if isinstance(files, list) else None


def ci_held(history: History, workspace: str, number: int, head: str) -> dict[str, Any] | None:
    """`0139` R8: the CI answer the row holds for this pull request at this head, as
    `{head, ci, checks, at}`; `None` until one was read there."""
    with history.data.connect() as conn:
        row = conn.execute(
            "SELECT ci, ci_head, ci_checks, ci_at FROM pull_requests "
            "WHERE root = ? AND workspace = ? AND number = ? AND head = ?",
            (str(history.working_dir), workspace, int(number), str(head or "")),
        ).fetchone()
    if row is None or not row["ci_at"]:
        return None
    try:
        checks = json.loads(row["ci_checks"] or "[]")
    except ValueError:
        checks = []
    return {"head": row["ci_head"], "ci": row["ci"], "checks": checks if isinstance(checks, list) else [],
            "at": row["ci_at"]}


def open_prs(history: History, workspace: str) -> list[dict[str, Any]]:
    """R22: `{unit, number, files}` for `autopilot.pick`, from the machine's own rows; `files`
    a set, as `autopilot.files_of` gives a plan's."""
    out = []
    for name, now in watched(history, workspace):
        files = files_held(history, workspace, now["number"], str(now.get("head") or ""))
        out.append({"unit": name, "number": now["number"], "files": None if files is None else set(files)})
    return out


def last_round(history: History, workspace: str, unit: str) -> dict[str, Any] | None:
    """The last review round the app holds: `n`, the head it reviewed (R3 c) and its verdict."""
    with history.data.connect() as conn:
        row = conn.execute(
            "SELECT n, head, verdict FROM review_rounds WHERE root = ? AND workspace = ? AND unit = ? "
            "ORDER BY n DESC LIMIT 1",
            (str(history.working_dir), workspace, unit),
        ).fetchone()
    return None if row is None else {"n": row["n"], "head": row["head"], "verdict": row["verdict"]}


async def _head(tree: str) -> str:
    return await gitops.rev_parse(Path(tree), "HEAD")


async def _push(tree: str, branch: str) -> Any:
    return await gitops.push_unit_branch(Path(tree), branch)


async def _files(tree: str, head: str) -> list[str] | None:
    """R22: the paths the diff names from the merge-base with `origin/main` to `head`, in the
    local repository, with no fetch; `None` when that cannot be read (C12)."""
    try:
        base = await gitops.rev_parse(Path(tree), "refs/remotes/origin/main")
        if not await gitops.has_commit(Path(tree), head):
            return None
        return await gitops.files_between(Path(tree), await gitops.merge_base_of(Path(tree), base, head), head)
    except gitops.GitError:
        return None


@dataclass
class Read:
    """What one read of a workspace's pull requests did (R23). `moved` holds `(unit,
    transition)` for each transition recorded; `error` is `gh`'s words when the list could
    not be read, and nothing was recorded after it."""

    moved: list[tuple[str, str]] = field(default_factory=list)
    error: str = ""
    calls: int = 0
    # `{unit, transition, id}` for each of `moved`, which the pass it schedules records.
    causes: list[dict[str, Any]] = field(default_factory=list)

    def move(self, unit: str, transition: str, row_id: int | None) -> None:
        self.moved.append((unit, transition))
        self.causes.append({"unit": unit, "transition": transition, "id": row_id})


class Machine:
    """`pr`, `ship` and the reconcile after a restart, for one working folder.

    `gh`, `push` and `head` default to the real calls and are looked up at call time, so a
    test hands in fakes; `notify` goes to `transitions.apply` (R23)."""

    def __init__(
        self, history: History, journal: Journal, *, gh: Gh | None = None,
        push: Push | None = None, head: Head | None = None,
        notify: Callable[[transitions.Applied], None] | None = None,
        files: Callable[[str, str], Awaitable[list[str] | None]] | None = None,
    ):
        self.history = history
        self.journal = journal
        self._gh = gh
        self._push = push
        self._head_of = head
        self._files = files
        self.notify = notify

    async def gh(self, argv: list[str], cwd: str) -> tuple[int, str, str]:
        run = self._gh or integrate._gh
        try:
            return await run(argv, cwd)
        except integrate.IntegrateError as e:
            raise PrError(str(e)) from e

    async def _json(self, argv: list[str], cwd: str) -> Any:
        code, out, err = await self.gh(argv, cwd)
        if code != 0:
            raise PrError(integrate._said(out, err))
        try:
            return json.loads(out or "null")
        except ValueError as e:
            raise PrError(f"gh {' '.join(argv[:2])} did not return JSON: {e}") from e

    async def view(self, tree: str, n: int) -> dict[str, Any]:
        got = await self._json(["pr", "view", str(int(n)), "--json", "state,mergeCommit,headRefOid,url"], tree)
        return got if isinstance(got, dict) else {}

    async def open_of(self, tree: str, branch: str) -> dict[str, Any] | None:
        rows = await self._json(
            ["pr", "list", "--head", branch, "--state", "open", "--json", "number,url,headRefOid", "--limit", "5"], tree)
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        return rows[0] if rows else None

    def _apply(self, u: Unit, transition: str, artifact: str, to_state: str, inputs: dict[str, Any],
               authority: str, also: Callable[[Any], None] | None = None) -> transitions.Applied:
        return transitions.apply(
            self.history, self.journal, machine=MACHINE, transition=transition,
            workspace=u.workspace, unit=u.name, artifact=artifact, to_state=to_state,
            inputs=inputs, authority=authority, actor=f"code:{transition}", source=f"prmachine:{transition}",
            notify=self.notify, also=also,
        )

    def _pull_request_row(self, u: Unit, number: int, head: str, merge_commit: str = "",
                          files: list[str] | None = None, checks: list[dict] | None = None) -> Callable[[Any], None]:
        """A read of the files keeps the ones an earlier read at the same head found: they
        are the same diff. `checks` (`0139` R8) is handed in only by a `ci` transition, the
        one write of the row's `ci`."""
        def write(conn: Any) -> None:
            conn.execute(
                "INSERT INTO pull_requests (root, workspace, unit, number, head, files, merge_commit, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT (root, workspace, number, head) DO UPDATE SET merge_commit = excluded.merge_commit, "
                "files = COALESCE(excluded.files, pull_requests.files)",
                (str(self.history.working_dir), u.workspace, u.name, int(number), head,
                 None if files is None else json.dumps(files), merge_commit),
            )
            if checks is not None:
                conn.execute(
                    "UPDATE pull_requests SET ci = ?, ci_head = head, ci_checks = ?, ci_at = ? "
                    "WHERE root = ? AND workspace = ? AND number = ? AND head = ?",
                    (ci_of(checks), json.dumps(checks), _now(), str(self.history.working_dir),
                     u.workspace, int(number), head),
                )
        return write

    def record_ci(self, u: Unit, number: int, head: str, checks: list[dict]) -> bool:
        """`0139` R8. The board's read of the required checks at `head`, as the reader's own:
        a `ci` transition through `ci-at-head` when the head or the answer moved, and the
        row's `ci` written with it. An answer that did not move only says when it was read
        again. Whether it was taken is returned."""
        now = state(self.history, u.workspace, u.name)
        ci = ci_of(checks)
        if head == now.get("head") and ci == now.get("ci") and ci_held(self.history, u.workspace, number, head):
            with self.history.data.write() as conn:
                conn.execute(
                    "UPDATE pull_requests SET ci_checks = ?, ci_at = ? "
                    "WHERE root = ? AND workspace = ? AND number = ? AND head = ?",
                    (json.dumps(checks), _now(), str(self.history.working_dir), u.workspace, int(number), head),
                )
            return True
        inputs = {"number": int(number), "head": head, "read_head": head, "ci": ci,
                  "was_head": now.get("head"), "was_ci": now.get("ci")}
        applied = self._apply(u, "ci", PR_FILE, "accepted", inputs, "code",
                              also=self._pull_request_row(u, number, head, checks=checks))
        return applied.open

    # -- pr -------------------------------------------------------------------

    async def open_pr(self, u: Unit, again: bool = False) -> Outcome:
        """R12. Push the branch, take the open pull request it already has or create one, and
        record `open`. A second press finds the first one's row and creates nothing; one run
        `again` from the board (`0054`) writes `pr.md` again from the same row."""
        now = state(self.history, u.workspace, u.name)
        if now["state"] in ("open", "merge-requested", "merged"):
            out = Outcome("already", now.get("number"), str(now.get("url") or ""), str(now.get("head") or ""))
            if again:
                _write_keeping_answers(u.directory / PR_FILE, render_pr(u, title_of(u.name, u.type), out.url, out.head))
            return out
        branch_ok = bool(u.branch) and u.branch == u.expected
        if not branch_ok:
            # Asked before anything is pushed: the guard would refuse the row anyway.
            return Outcome("refused", reasons=("bad-branch",), guard="branch-named",
                           detail=f"the tree is on {u.branch or 'no branch'}, not {u.expected or 'a branch cos.mjs names'}")
        try:
            await (self._push or _push)(u.tree, u.branch)
            head = await (self._head_of or _head)(u.tree)
            found = await self.open_of(u.tree, u.branch)
            if found is not None:
                number, url, result = int(found["number"]), str(found.get("url") or ""), "found"
            else:
                title = title_of(u.name, u.type)
                code, out, err = await self.gh(
                    ["pr", "create", "--base", gitops.TRUNK, "--head", u.branch, "--title", title,
                     "--body", body_of(u.name)], u.tree)
                if code != 0:
                    raise PrError(integrate._said(out, err))
                url = (out.strip().splitlines() or [""])[-1].strip()
                number = int(url.rstrip("/").rsplit("/", 1)[-1]) if "/pull/" in url else 0
                if not number:
                    raise PrError(f"gh pr create printed no pull request URL: {out.strip()[:200]}")
                result = "opened"
        except (PrError, gitops.GitError, ValueError, KeyError) as e:
            return Outcome("failed", detail=str(e) or type(e).__name__)
        inputs = {"branch": u.branch, "expected": u.expected, "branch_ok": branch_ok,
                  "number": number, "url": url, "head": head}
        applied = self._apply(u, "open", PR_FILE, "accepted", inputs, "code",
                              also=self._pull_request_row(u, number, head))
        if not applied.open:
            return Outcome("refused", number, url, head, reasons=applied.reasons, guard=applied.guard)
        _write_keeping_answers(u.directory / PR_FILE, render_pr(u, title_of(u.name, u.type), url, head))
        return Outcome(result, number, url, head, guard=applied.guard)

    # -- ship -----------------------------------------------------------------

    async def _number(self, u: Unit, now: dict[str, Any]) -> int | None:
        if now.get("number"):
            return int(now["number"])
        # A pull request a `pr` session opened before `0136`: found by its branch, on GitHub.
        found = await self.open_of(u.tree, u.branch) if u.branch else None
        return int(found["number"]) if found else None

    async def _record_merged(self, u: Unit, number: int, view: dict[str, Any], round_n: int | None,
                             result: str) -> Outcome:
        commit = str((view.get("mergeCommit") or {}).get("oid") or "")
        head = str(view.get("headRefOid") or "")
        inputs = {"number": number, "merge_commit": commit, "head": head, "state": view.get("state")}
        applied = self._apply(u, "merged", SHIP_FILE, "accepted", inputs, "code",
                              also=self._pull_request_row(u, number, head, commit))
        if not applied.open:
            return Outcome("failed", number, head=head, reasons=applied.reasons, guard=applied.guard,
                           detail="the merge commit could not be read")
        (u.directory / SHIP_FILE).write_text(
            render_ship(u, status="accepted", round_n=round_n, number=number, head=head, merge_commit=commit),
            encoding="utf-8")
        return Outcome(result, number, str(view.get("url") or ""), head, commit, guard=applied.guard,
                       transition=(applied.row or {}).get("id"))

    async def ship(self, u: Unit, authority: str = "person", rebased: dict[str, str] | None = None) -> Outcome:
        """R13. Reconcile first; a merge made anywhere else is only recorded. Otherwise guard
        `ship-ready` reads CI at the head this read found, and the last round the app holds;
        open, it records `merge-requested`, merges pinned to that head, and records `merged`.
        `rebased` is the `ship` gate's read that the head is a clean rebase of a reviewed
        commit (`0067`); the guard takes it only when both commits match its own inputs."""
        now = state(self.history, u.workspace, u.name)
        if now["state"] == "merged":
            return Outcome("already", now.get("number"), merge_commit=str(now.get("merge_commit") or ""))
        round_ = last_round(self.history, u.workspace, u.name)
        round_n = round_["n"] if round_ else None
        try:
            number = await self._number(u, now)
            if number is None:
                return Outcome("failed", detail="the unit has no open pull request to merge")
            view = await self.view(u.tree, number)
            if view.get("state") == "MERGED":
                return await self._record_merged(u, number, view, round_n, "recorded")
            if view.get("state") != "OPEN":
                return Outcome("failed", number, detail=f"#{number} is {view.get('state') or 'unread'}, not open")
            head = str(view.get("headRefOid") or "")
            ci = ci_of(await self._checks(u.tree, number))
        except (PrError, ValueError) as e:
            return Outcome("failed", detail=str(e) or type(e).__name__)
        inputs = {
            "number": number, "head": head, "ci": ci,
            "verdict": round_["verdict"] if round_ else None,
            "reviewed_head": round_["head"] if round_ else "",
            "round": round_n,
        }
        if rebased:
            inputs["rebased"] = dict(rebased)
        applied = self._apply(u, "merge-requested", SHIP_FILE, "draft", inputs, authority)
        if not applied.open:
            return Outcome("refused", number, head=head, reasons=applied.reasons, guard=applied.guard)
        (u.directory / SHIP_FILE).write_text(
            render_ship(u, status="draft", round_n=round_n, number=number, head=head), encoding="utf-8")
        return await self._merge(u, number, head, round_n)

    async def _checks(self, tree: str, n: int) -> list[dict]:
        code, out, err = await self.gh(["pr", "checks", str(int(n)), "--required", "--json", "name,bucket"], tree)
        try:
            rows = json.loads(out or "null")
        except ValueError:
            rows = None
        if not isinstance(rows, list):
            raise PrError(integrate._said(out, err) if code else "gh pr checks returned no list")
        return [r for r in rows if isinstance(r, dict)]

    async def _merge(self, u: Unit, number: int, head: str, round_n: int | None) -> Outcome:
        """R13 steps 2 and 3. A non-zero exit is read again before it counts: `--delete-branch`
        in a worktree merges and then fails (`0116`)."""
        try:
            code, out, err = await self.gh(
                ["pr", "merge", str(int(number)), "--squash", "--delete-branch", "--match-head-commit", head], u.tree)
            said = "" if code == 0 else integrate._said(out, err)
            view = await self.view(u.tree, number)
        except PrError as e:
            return Outcome("failed", number, head=head, detail=str(e))
        if view.get("state") == "MERGED":
            return await self._record_merged(u, number, view, round_n, "merged")
        refused = said or f"#{number} is {view.get('state') or 'unread'} after the merge"
        (u.directory / SHIP_FILE).write_text(
            render_ship(u, status="draft", round_n=round_n, number=number, head=head, refused=refused),
            encoding="utf-8")
        return Outcome("failed", number, head=head, detail=refused)

    # -- after a restart --------------------------------------------------------

    async def reconcile(self, units: list[Unit]) -> list[Outcome]:
        """Spec Design "Đối soát sau khởi động lại": a unit left at `merge-requested` whose pull
        request GitHub says is merged is recorded, and nothing is merged. One still open is
        left as it is; the next `ship` asks the guard again from the start."""
        done: list[Outcome] = []
        for u in units:
            now = state(self.history, u.workspace, u.name)
            if now["state"] != "merge-requested" or not now.get("number"):
                continue
            try:
                view = await self.view(u.tree, int(now["number"]))
            except PrError as e:
                done.append(Outcome("failed", int(now["number"]), detail=str(e)))
                continue
            if view.get("state") == "MERGED":
                round_ = last_round(self.history, u.workspace, u.name)
                done.append(await self._record_merged(u, int(now["number"]), view, round_["n"] if round_ else None, "recorded"))
        return done

    # -- the reader (R23) -------------------------------------------------------

    async def read(self, root: str, workspace: str, directory_of: Callable[[str], Path]) -> Read:
        """Spec Design "Người đọc PR": one read of `workspace`'s pull requests.

        No call at all when no unit's pull request is `open` or `merge-requested`. Otherwise
        one `gh pr list`; `gh pr checks --required` only for a pull request whose `ci` at the
        head the list gave is not settled; `gh pr view` only for one gone from the list. Each
        change is a transition -- `ci` (a new head or a new answer at the same head),
        `merged`, `closed` -- and nothing is written when nothing changed. A call that fails is
        not asked again before the next read (C11)."""
        out = Read()
        watching = watched(self.history, workspace)
        if not watching:
            return out
        try:
            out.calls += 1
            rows = await self._json(
                ["pr", "list", "--state", "open", "--json", "number,headRefOid", "--limit", "200"], root)
        except PrError as e:
            out.error = str(e)
            return out
        listed = {
            r["number"]: r for r in (rows if isinstance(rows, list) else [])
            if isinstance(r, dict) and isinstance(r.get("number"), int)
        }
        for name, now in watching:
            u = Unit(workspace, name, directory_of(name), root, "", "", None)
            number = int(now["number"])
            try:
                row = listed.get(number)
                if row is None:
                    out.calls += 1
                    view = await self.view(root, number)
                    if view.get("state") == "MERGED":
                        round_ = last_round(self.history, workspace, name)
                        done = await self._record_merged(u, number, view, round_["n"] if round_ else None, "recorded")
                        if done.ok:
                            out.move(name, "merged", done.transition)
                    elif view.get("state") == "CLOSED":
                        # Closed without a merge: `pr` is to run again, and opens a new one.
                        applied = self._apply(u, "closed", PR_FILE, "draft",
                                              {"number": number, "state": "CLOSED"}, "code")
                        if applied.open:
                            out.move(name, "closed", (applied.row or {}).get("id"))
                    continue
                head = str(row.get("headRefOid") or "")
                if head == now.get("head") and now.get("ci") in SETTLED_CI:
                    continue
                out.calls += 1
                checks = await self._checks(root, number)
                ci = ci_of(checks)
            except PrError as e:
                out.error = out.error or str(e)
                continue
            if head == now.get("head") and ci == now.get("ci"):
                continue
            files = None
            if head != now.get("head") or files_held(self.history, workspace, number, head) is None:
                files = await (self._files or _files)(root, head)
            inputs = {"number": number, "head": head, "read_head": head, "ci": ci,
                      "was_head": now.get("head"), "was_ci": now.get("ci")}
            applied = self._apply(u, "ci", PR_FILE, "accepted", inputs, "code",
                                  also=self._pull_request_row(u, number, head, files=files, checks=checks))
            if applied.open:
                out.move(name, "ci", (applied.row or {}).get("id"))
        return out
