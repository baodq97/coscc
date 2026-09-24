"""The only place business logic lives.

`spec.md` R10: the page and the JSON API are two entry points to one capability, and two
implementations of one capability is the surest way to have one of them fixed and the other
not. So neither an HTTP route nor a Reflex event handler may decide anything — they
translate a request into a call here, and a result back into their own shape.

The rule that makes this checkable: nothing in this module imports a web framework, and
nothing above it branches on business state. A conditional in a route is a bug in this
file, not in the route.

`Invalid` is how this layer refuses. Callers map it to their own vocabulary — 400 for
HTTP, an error banner for the page — and neither gets to invent a different reason.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, AsyncIterator

from coscc import board as board_reader
from coscc import gitops
from coscc import prcomment
from coscc import sessions as reader
from coscc.board import Unavailable
from coscc.config import Config
from coscc.data import Data
from coscc.gitops import GitError
from coscc.history import UNKNOWN, BadTransition, History, settled_edits
from coscc.journal import (
    COST_FIELDS,
    COST_USD,
    BadRecord,
    Busy,
    Journal,
    add_cost,
    last_runs,
    totals_of,
    zero_cost,
)
from coscc.policy import GRANTS, PROSE_STAGES, grant_for
from coscc import models
from coscc.runner import SESSIONS_PER_STEP, STATUS_RE, RunError, Runner, describe_attempt
from coscc.sessions import Sessions
from coscc.store import BadName, Store, require_name
from coscc import units, worktrees
from coscc.units import BadUnit, CannotCreate

# The eight stage names, in stage order. Taken from the stage list the board reports rather
# than written again here would be better; the board read is async and this method is not,
# so the names are repeated and this comment is the warning.
STAGE_FILES = ("idea", "intent", "spec", "plan", "impl", "pr", "review", "ship")

# Where a unit's branch is cut from: the trunk as this remote has it. Constants, not
# request fields — a caller cannot point the fetch at another remote or another branch.
BRANCH_REMOTE = "origin"
BRANCH_TRUNK = gitops.TRUNK


class Invalid(Exception):
    """A request this layer refuses, carrying a reason a caller can show verbatim."""


def _attach_comment_state(units_: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    """`0021` D4. Give every review round a `comment`: on the pull request, or not and why.

    Read off the run log, never stored beside the round: a `posted` or `already` row for
    the round means it is there. Anything else -- including a round written at a terminal,
    which has no row at all -- is *not on the PR*, with the latest failure's reason if any.
    """
    posted: dict[tuple[str, Any], str] = {}
    failed: dict[tuple[str, Any], str] = {}
    for r in records:
        k = (str(r.get("unit") or ""), r.get("round"))
        if r.get("outcome") in ("posted", "already"):
            posted[k] = str(r.get("comment_url") or "")
        elif r.get("outcome") == "failed":
            failed[k] = str(r.get("detail") or "")
    for u in units_:
        for rnd in u.get("rounds") or []:
            k = (u["name"], rnd.get("n"))
            rnd["comment"] = (
                {"posted": True, "url": posted[k], "reason": None}
                if k in posted
                else {"posted": False, "url": "", "reason": failed.get(k)}
            )


def step_cwd(stage: str, work: str, directory: Path) -> str:
    """Where a step's session runs. The unit's worktree, except for `ship`.

    `ship` runs `gh pr merge --squash --delete-branch`, and inside a worktree that command
    fails after it has already merged. Measured 2026-09-23 on `baodq97/coscc-proof` with gh
    2.93.0, `main` checked out at the root and the branch in a worktree: the pull request
    went to `MERGED`, then gh tried to switch the worktree to `main`, git answered
    `fatal: 'main' is already used by worktree`, and gh exited 1 -- with the remote branch
    and the local branch both left behind. A step reading that exit code reports a failed
    merge for a pull request that merged.

    The same command with the pull request's URL, run from a directory that is not a git
    checkout, exited 0, merged, and deleted the remote branch. The unit's directory in the
    store is such a directory -- the store has no git (`coscc/units.py`) -- and `ship` writes
    `ship.md` there anyway. The worktree and the local branch are then removed by
    `worktrees.remove_if_finished`, which already waits for GitHub to say `MERGED`.

    The gates still read `work`: only the session moves.
    """
    return str(directory) if stage == "ship" else work


def describe_base(base: dict[str, Any] | None) -> str:
    """The one sentence saying a step's base may be stale, or `""` when it is fresh.

    `0030_a-unit-branch-starts-from-a-stale-main`. `state.py` and this module's own prompt
    (`runner.build_prompt`, *The base this step runs on*) both call this rather than each
    writing the sentence its own way — the same reason `.claude/CLAUDE.md` gives for
    `cos.mjs` being the one place the loop is defined, at a much smaller scale.
    """
    if not base or base.get("fresh", True):
        return ""
    sha = base.get("sha") or "?"
    ref = base.get("ref") or f"{BRANCH_REMOTE}/{BRANCH_TRUNK}"
    reason = base.get("reason") or ""
    return f"This step ran on {ref} at {sha}, which may be stale: {reason}"


@dataclass
class Service:
    config: Config
    sessions: Sessions
    store: Store | None = field(default=None, init=False)
    # `0016`. Held across read-check-append so two answers arriving together cannot
    # interleave their blocks. The page and the API share this instance (`state.py`
    # takes `API.state.service`), so one lock covers both.
    _answer_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # `0021`. Held across read-comments-then-post, so two presses of *Post to PR* for one
    # round run one after the other and the second finds the first's marker. One process
    # only, like `pull` (`.claude/rules/coscc-app.md`).
    _comment_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # `0017` R8. Per workspace, created on first use.
    _create_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        # No working folder means no store, and the app behaves as it did before one existed.
        # That is what keeps `scripts/verify_0001.py` running unchanged (`spec.md` R6).
        self.store = (
            Store(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )
        # One question, asked in two places. See `Sessions.membership`.
        self.sessions.membership = self._is_member

    # -- workspaces ---------------------------------------------------------

    def workspaces(self) -> dict[str, Any]:
        """Both sources, with the count the app could not answer before the store existed.

        `source` is carried per entry rather than merged away: an env workspace cannot be
        renamed or removed from here, and a caller has to be able to tell.
        """
        rows: list[dict[str, Any]] = []
        for path in self.config.workspaces:
            rows.append(
                {
                    "name": Path(path).name,
                    "path": path,
                    "label": "",
                    "source": "env",
                    "missing": not Path(path).expanduser().is_dir(),
                }
            )
        if self.store is not None:
            for entry in self.store.entries():
                target = self.store.path_of(entry.name)
                rows.append(
                    {
                        "name": entry.name,
                        "path": str(target),
                        "label": entry.label,
                        "source": "store",
                        "missing": not target.is_dir(),
                    }
                )
        return {
            "working_dir": self.config.working_dir,
            "count": len(rows),
            "workspaces": rows,
            # Kept so the original shape still reads: it only ever asked for paths.
            "paths": [r["path"] for r in rows],
        }

    # -- changing the list --------------------------------------------------

    def _store_or_refuse(self) -> Store:
        if self.store is None:
            raise Invalid(
                "no working folder configured; set COS_WORKING_DIR and restart "
                "(it is deliberately not settable over HTTP)"
            )
        return self.store

    def _name_or_refuse(self, name: str) -> str:
        try:
            return require_name(name)
        except BadName as e:
            raise Invalid(str(e)) from e

    def _row(self, name: str, label: str) -> dict[str, Any]:
        store = self._store_or_refuse()
        target = store.path_of(name)
        return {
            "name": name,
            "path": str(target),
            "label": label,
            "source": "store",
            "missing": not target.is_dir(),
        }

    async def add_workspace(
        self, name: str, label: str = "", repo_url: str | None = None
    ) -> dict[str, Any]:
        """Add by adopting a directory already under the root, or by cloning into it.

        Both are `intent.md`'s scope line, where "thêm" and "clone" are separate entries.

        Order matters and is the whole of `spec.md` R16: clone into a temp directory,
        rename into place, and only then write the store. The worst state a failure can
        leave is a temp directory nobody cleaned — never a listed workspace that does not
        work.
        """
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        if any(e.name == name for e in store.entries()):
            raise Invalid(f"workspace already exists: {name}")

        target = store.path_of(name)
        if repo_url:
            if target.exists():
                raise Invalid(f"directory already exists: {target}")
            store.working_dir.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=store.working_dir, prefix=".cos-clone-"))
            try:
                await gitops.clone(repo_url, staging / name)
                os.replace(staging / name, target)
            except GitError as e:
                raise Invalid(str(e)) from e
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        elif not target.is_dir():
            raise Invalid(f"no such directory under the working folder: {target}")

        entry = store.add(name, label)
        return self._row(entry.name, entry.label)

    def set_label(self, name: str, label: str) -> dict[str, Any]:
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        try:
            entry = store.set_label(name, label)
        except KeyError as e:
            raise Invalid(f"no such workspace: {name}") from e
        return self._row(entry.name, entry.label)

    def remove_workspace(self, name: str) -> dict[str, Any]:
        """Drops the entry only. The directory stays — `spec.md` R18 and C6."""
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        try:
            store.remove(name)
        except KeyError as e:
            raise Invalid(f"no such workspace: {name}") from e
        return {"removed": name, "count": len(self.workspaces()["workspaces"])}

    async def pull_workspace(self, name: str) -> dict[str, Any]:
        """Fast-forward only. A failure comes back with its output — `spec.md` R20.

        Refused outright while a session is live here (`spec.md` R6). The refusal is an
        `Invalid` like every other reason a pull fails, so it reaches the page through the
        path R20 already built rather than through one of its own — R8.
        """
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        if not any(e.name == name for e in store.entries()):
            raise Invalid(f"no such workspace: {name}")
        target = store.path_of(name)
        if not target.is_dir():
            raise Invalid(f"workspace directory is missing: {target}")
        # R6, and this has to come before `gitops`: a fast-forward rewrites files
        # under a turn that is already reading them, and the turn cannot be told. The
        # answer covers this process only (`spec.md` C2) — a second app holding a session
        # here is not seen, and the pull will go ahead.
        live = self.sessions.live_in(str(target))
        if live:
            raise Invalid(
                f"workspace {name} has {len(live)} live session(s) — "
                "pull would change files under them. Finish or reload, then try again."
            )
        try:
            output = await gitops.pull(target)
        except GitError as e:
            raise Invalid(str(e)) from e
        return {"name": name, "output": output}

    # -- board --------------------------------------------------------------

    def _journal(self) -> Journal | None:
        """The run log, or `None` when there is no working folder to keep it in.

        Unset `COS_WORKING_DIR` and the app behaves as it did before the store — which now also means
        the board is read-only: there is nowhere to record a mode, so every step reads
        `manual` and nothing can be started. That is the safe direction to fail in.
        """
        return (
            Journal(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )

    @staticmethod
    def _journal_key(cwd: str) -> str:
        """How a workspace is named in the journal.

        The resolved path, not a store name: an env-declared workspace has no name at all
        (`config.is_workspace`), and a path is the one identifier both kinds have. The
        cost is that moving a workspace detaches its history from it.
        """
        return str(Path(cwd).expanduser().resolve())

    def _units_root(self, cwd: str) -> Path:
        """Where this workspace's units live. One question, asked of one module.

        `coscc/units.py` owns the answer; this is the only place in the service that asks.
        """
        return units.root(cwd, self.config.data_dir)

    def _unit_dir(self, cwd: str, unit: str) -> Path:
        try:
            return units.unit_dir(cwd, unit, self.config.data_dir)
        except BadUnit as e:
            raise Invalid(str(e)) from e

    async def board(self, cwd: str) -> dict[str, Any]:
        """Every unit in this workspace, each with its eight stages, modes and cost.

        The status of a stage comes from the artifact and the mode comes from the journal,
        and they are joined here rather than stored together. Storing them together is how
        a board starts disagreeing with the files it claims to describe.
        """
        self._workspace_or_refuse(cwd)
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e

        journal = self._journal()
        key = self._journal_key(cwd)
        modes: dict[tuple[str, str], str] = {}
        timelines: dict[str, list[dict[str, Any]]] = {}
        comments: list[dict[str, Any]] = []
        if journal is not None:
            try:
                modes = journal.modes(key)
                # One read for every unit's cost. Asking `totals` per unit re-scanned the
                # working folder N times for the rows this already has.
                timelines = journal.timelines(key)
                # `0021` D4. One read for every unit's comment attempts, too.
                comments = journal.records(key, kind="pr-comment")
            except Busy as e:
                raise Invalid(str(e)) from e
        _attach_comment_state(data["units"], comments)

        for unit in data["units"]:
            unit_last_runs = last_runs(timelines.get(unit["name"], []))
            for row in unit["stages"]:
                # `manual` is the default because starting work is a decision someone has
                # to make, not one an unset value should make for them.
                row["mode"] = modes.get((unit["name"], row["stage"]), "manual")
                # The mode is a label since `0020`; the grant follows the stage alone.
                grant = grant_for(row["stage"])
                # Carried to the page so `spec.md` C4 can be met where the button is: what
                # a step will be allowed to do has to be readable before it is started.
                row["grants"] = list(grant.tools)
                row["warning"] = grant.warning
                # `0019` plan step 6 / `spec.md` R5. From the same `timelines` read above —
                # no second scan of the run log. `status` (and the lanes) stays read from
                # the artifact alone (C6); this is a second, separate field.
                row["last_run"] = unit_last_runs.get(row["stage"])
            unit["cost"] = (
                totals_of(timelines.get(unit["name"], [])) if journal is not None else {}
            )

        await self._attach_worktrees(cwd, data["units"])

        data["recording"] = journal is not None
        data["read_only_because"] = (
            None if journal is not None
            else "no working folder is set, so nothing can be recorded — set COS_WORKING_DIR"
        )
        if not data["units"]:
            # `0001_product-describes-a-state-it-is-not-in` R6, R7, R8. The board reads the
            # store, and a host repository can have a `.cos/` full of units the store never
            # heard of. The page has to be able to say which directory it read and how many
            # units sit in the one it did not. Counted on every call: R8 forbids a cache.
            data["empty"] = {
                "store": str(self._units_root(cwd)),
                "host": units.key(cwd),
                "host_units": units.host_unit_count(cwd),
            }
        return data

    async def _attach_worktrees(self, cwd: str, units_: list[dict[str, Any]]) -> None:
        """`0017`. Give every unit `worktree: {path, branch, prepare}`, or `None`.

        One `git worktree list` for the whole board. A `finished` unit that still has a tree
        is cleaned up here (R10), so a unit shipped at a terminal is cleaned up too — at the
        cost of a `gh pr view` (up to 30s) on **every** board read for as long as the tree
        stays: once, when the removal succeeds; on each read after, when it does not
        (`gh` failing, the pull request not merged, the local branch off the merged head).
        Nothing remembers a refusal, so a transient `gh` error is retried rather than
        believed. A dirty tree is refused before `gh` is asked. (Plan Risk 7; `0017`
        review F4 — this docstring said "the first time" until then.)
        """
        root = Path(cwd).expanduser().resolve()
        try:
            listed = {
                str(Path(t["path"]).resolve()): t
                for t in await gitops.worktree_list(root)
            } if (root / ".git").exists() else {}
        except GitError:
            listed = {}
        for u in units_:
            u["worktree"] = None
            try:
                where = worktrees.path(cwd, u["name"], self.config.data_dir)
            except BadUnit:
                continue
            found = listed.get(str(where))
            if found is None:
                continue
            if u.get("next") == "finished":
                done = await worktrees.remove_if_finished(cwd, u["name"], u, self.config.data_dir)
                if done.get("removed"):
                    continue
            u["worktree"] = {
                "path": str(where),
                "branch": found.get("branch") or "",
                "prepare": worktrees.read_prepare(where),
            }

    async def _cleanup(self, cwd: str, unit: str) -> dict[str, Any]:
        """R10 after a `ship` step. Never raises; says what it did or why not."""
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            return {"removed": False, "reason": str(e)}
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            return {"removed": False, "reason": "unit not on the board"}
        return await worktrees.remove_if_finished(cwd, unit, found, self.config.data_dir)

    async def next_step(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0024`. The one stage the run button may offer, and why -- `cos.mjs next`'s answer.

        Read with the same store and the same `repo=cwd` that `run_step` hands the gate, so
        the stage offered and the gate that will be asked read one checkout (`0024` spec,
        *Repo mà `cos.mjs` đọc*). Nothing here chooses a stage.
        """
        self._workspace_or_refuse(cwd)
        if not unit:
            raise Invalid("name a work unit")
        self._unit_dir(cwd, unit)
        # `0017`. The unit's worktree is the checkout its branch and pull request are read
        # from. None when there is none to open, and `cos.mjs` then keeps `review` and
        # `ship` closed rather than read the workspace's branch, which is not this unit's.
        # A workspace that is not a git repository has no worktrees, and is read as it
        # always was — the same fallback `run_step` takes, so the two read one checkout.
        if (Path(cwd).expanduser().resolve() / ".git").exists():
            tree = await self._worktree(cwd, unit)
            repo = tree["path"] if tree else None
        else:
            repo = cwd
        try:
            found = await board_reader.next_step(self._units_root(cwd), unit, repo=repo)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        return {"cwd": cwd, "unit": unit, **{k: found[k] for k in ("stage", "action", "blocked")}}

    async def set_mode(self, cwd: str, unit: str, stage: str, mode: str) -> dict[str, Any]:
        """Choose how one step runs. Validated against the board, not against a second list."""
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a mode cannot be recorded — set COS_WORKING_DIR"
            )

        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e

        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Invalid(f"no such work unit in this workspace: {unit}")
        if stage not in data["stages"]:
            raise Invalid(f"no such stage: {stage} (use one of {', '.join(data['stages'])})")

        try:
            journal.set_mode(self._journal_key(cwd), unit, stage, mode)
        except BadRecord as e:
            raise Invalid(str(e)) from e
        except Busy as e:
            raise Invalid(str(e)) from e
        return {"cwd": cwd, "unit": unit, "stage": stage, "mode": mode}

    async def run_step(self, cwd: str, unit: str, stage: str) -> AsyncIterator[tuple[str, Any]]:
        """Run one step of one unit, streaming the reply as it arrives.

        Everything this needs — the stage order, the artifact filename, the mode — comes
        from one board read, so a step cannot run against a different idea of the unit
        than the one the page is showing.
        """
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a run cannot be recorded — set COS_WORKING_DIR"
            )

        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e

        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Invalid(f"no such work unit in this workspace: {unit}")
        row = next((r for r in found["stages"] if r["stage"] == stage), None)
        if row is None:
            raise Invalid(f"no such stage: {stage} (use one of {', '.join(data['stages'])})")

        # `.claude/CLAUDE.md` invariant 2: *"Ask `cos.mjs gate` before a stage and stop
        # when it exits non-zero."* Until 2026-09-23 this app did neither. It read the
        # board, found the row, and started the session -- so the board would run `ship`
        # on a unit whose `intent.md` was still a draft, and the only thing standing
        # between it and that was a sentence in a skill file addressed to a session that
        # often has no way to run a command.
        #
        # Asked here rather than in `Runner` because a refusal must arrive before any
        # money is spent, and `run_step` is the last place that is still true.
        # `0017`. Every step runs in the unit's own worktree. A workspace that is not a git
        # repository has none, and its steps run where they always did — there is no
        # branch there for another unit to take away.
        is_repo = (Path(cwd).expanduser().resolve() / ".git").exists()
        tree = await self._worktree(cwd, unit, strict=True) if is_repo else None
        if is_repo and tree is None:
            try:
                tree = {"path": (await worktrees.ensure(cwd, unit, None, self.config.data_dir))["path"]}
            except (GitError, BadUnit) as e:
                raise Invalid(f"{unit} has no worktree and one could not be opened: {e}") from e
        work = tree["path"] if tree else cwd
        # `0030_a-unit-branch-starts-from-a-stale-main` R1/R4/R5. A tree already on its
        # branch carries whatever `_worktree` read when it was opened onto it (or nothing,
        # when it was already there before this call); a tree still detached is refreshed
        # now, on the spot, because a session about to run on it is about to read it.
        base: dict[str, Any] | None = None
        if tree is not None:
            if tree.get("branch"):
                base = tree.get("base")
            else:
                base = await worktrees.refresh_base(cwd, unit, self.config.data_dir)
        try:
            # `work` is the checkout the `review` and `ship` gates read git and the pull
            # request from (`0015`). The store has no git to read.
            allowed, said = await board_reader.gate(
                self._units_root(cwd), unit, stage, repo=work
            )
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if not allowed:
            raise Invalid(said)

        if stage == "impl" and tree is not None:
            # R6. A tree that cannot run its tests turns every `impl` red from the start, so
            # the step is not started on one. Tried once more first: a network blip is the
            # ordinary reason, and the page has nothing better to offer than *try again*.
            prepared = worktrees.read_prepare(Path(work))
            if not (prepared or {}).get("ok"):
                prepared = await worktrees.prepare(Path(work), cwd)
            if not prepared.get("ok"):
                raise Invalid(worktrees.describe_failure(prepared))

        key = self._journal_key(cwd)
        directory = self._unit_dir(cwd, unit)
        mode = journal.modes(key).get((unit, stage), "manual")
        # `0021` D3. The rounds `review.md` held before this step, so that the ones it adds
        # can be told apart afterwards. Taken from the board already read above.
        rounds_before = (
            {r.get("n") for r in found.get("rounds") or []}
            if row["file"] == "review.md" else None
        )
        # `0004_no-setting-says-which-model-runs-a-stage`. Resolved after the gate, so a
        # refused step reads nothing more. `stage` was checked against the board above.
        model, model_source = self._model_for(stage)
        # `0019` plan step 6 / `spec.md` R6. Read after the gate, before any money is
        # spent — the same place `model` is resolved. `Runner` does not read the run log
        # itself; `build_prompt` only places what it is handed, the same as `base_note`.
        try:
            found = journal.failed_attempts(key, unit, stage)
        except Busy as e:
            raise Invalid(str(e)) from e
        runner = Runner(self.sessions, journal)
        try:
            async for item in runner.run(
                workspace=cwd,
                directory=directory,
                journal_key=key,
                unit=unit,
                stage=stage,
                artifact=row["file"],
                stages=list(data["stages"]),
                mode=mode,
                gate_said=said,
                cwd=step_cwd(stage, work, directory),
                model=model,
                model_source=model_source,
                base=base,
                base_note=describe_base(base),
                last_attempt=describe_attempt(found) if found else "",
            ):
                if item[0] == "done":
                    item = ("done", {**item[1], "base": base})
                    self._record_transition(cwd, unit, row["file"], directory, item[1])
                    if stage == "ship" and tree is not None and item[1].get("outcome") == "done":
                        # R10. Only if `cos.mjs` now says `finished` and GitHub says merged;
                        # otherwise nothing is touched and the board tries again later.
                        item = ("done", {**item[1], "cleanup": await self._cleanup(cwd, unit)})
                    if rounds_before is not None and item[1].get("outcome") == "done":
                        # After `Runner` has written `review.md` (`runner.py:442`), never
                        # before: the artifact does not wait on GitHub (`0021` R6).
                        item = (
                            "done",
                            {**item[1], "comments": await self._post_new_rounds(cwd, unit, rounds_before)},
                        )
                yield item
        except RunError as e:
            raise Invalid(str(e)) from e

    async def _post_new_rounds(
        self, cwd: str, unit: str, before: set[Any]
    ) -> list[dict[str, Any]]:
        """`0021` R2. Post every round the step just added. Never raises.

        What happened to each is in the run log whatever it was, and the board shows a
        round that did not make it as *not on the PR* with the reason.
        """
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            return [{"round": None, "state": "failed", "url": "", "reason": str(e)}]
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            return []
        out = []
        for rnd in found.get("rounds") or []:
            if rnd.get("n") in before:
                continue
            async with self._comment_lock:
                out.append(await self._post_round(cwd, found, rnd))
        return out

    async def post_review_comment(self, cwd: str, unit: str, round_n: Any) -> dict[str, Any]:
        """`0021` R8, R9. Post one review round to the unit's pull request, or say it is there.

        The body is built from the round as `cos.mjs` read it out of `review.md`; nothing a
        caller sends reaches GitHub but the unit's name and the round's number. Not an
        approval, and it opens no gate: neither gate reads comments (R10).
        """
        self._workspace_or_refuse(cwd)
        try:
            n = int(round_n)
        except (TypeError, ValueError):
            raise Invalid(f"a round is named by its number, got {round_n!r}") from None
        async with self._comment_lock:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            rnd = next((r for r in found.get("rounds") or [] if r.get("n") == n), None)
            if rnd is None:
                have = ", ".join(str(r.get("n")) for r in found.get("rounds") or []) or "none"
                raise Invalid(f"review.md of {unit} has no round {n} (it has {have})")
            return await self._post_round(cwd, found, rnd)

    async def _post_round(
        self, cwd: str, found: dict[str, Any], rnd: dict[str, Any]
    ) -> dict[str, Any]:
        """Post one round and write one `pr-comment` row saying how it went (R13).

        The caller holds `_comment_lock`. A row that cannot be written is dropped rather
        than turned into a failure: the comment is on GitHub or it is not, and that is what
        the person asked about. The next board read then shows the round as not posted, and
        a second press finds the marker and says `already`.
        """
        unit = found["name"]
        n = rnd.get("n")
        pr_url = (found.get("pr") or {}).get("url") or ""
        result = await prcomment.post(
            unit, n, rnd.get("verdict"), rnd.get("text") or "", pr_url,
            str(Path(cwd).expanduser().resolve()),
        )
        record: dict[str, Any] = {
            "kind": "pr-comment",
            "workspace": self._journal_key(cwd),
            "unit": unit,
            "stage": "review",
            "round": n,
            "pr": pr_url,
            "outcome": result.state,
        }
        if result.state == "failed":
            record["detail"] = result.reason
        else:
            record["comment_url"] = result.url
        journal = self._journal()
        if journal is not None:
            try:
                journal.append(record)
            except (Busy, BadRecord, OSError):
                pass
        return {"unit": unit, "round": n, "pr": pr_url, **result.as_dict()}

    def _record_transition(
        self, cwd: str, unit: str, artifact: str, directory: Path, done: dict[str, Any]
    ) -> None:
        """`0014` R6. One transition per step that finished, written as it happens.

        This is the first writer into `0013`'s log that is not the git import.
        `.cos/0013_.../ship.md` said the loop would come back here: history imported from
        git carries no actor and no session, because git knows neither, so the provenance
        that unit built is only ever true of work done **after** it. This is that work.

        Never raises into the run. A step that did its job and then failed to be recorded
        has still done its job, and turning a bookkeeping failure into a failed step would
        cost real money for nothing. The failure is dropped rather than shown, and that is
        a cost `0014` `impl.md` states rather than hides.
        """
        if done.get("outcome") != "done":
            return
        history = self._history()
        if history is None:
            return
        try:
            text = (directory / artifact).read_text(encoding="utf-8", errors="replace")
            found = STATUS_RE.search(text)
            if not found:
                return
            history.record(
                self._journal_key(cwd),
                unit,
                artifact,
                found.group(1).lower(),
                actor=f"stage:{done.get('stage') or ''}",
                session=str(done.get("session_id") or "") or UNKNOWN,
                source=f"run:{done.get('stage') or ''}",
            )
        except (OSError, BadTransition, Busy):
            return

    def _create_lock(self, cwd: str) -> asyncio.Lock:
        """`0017` R8. One lock per workspace, held across numbering and making the tree."""
        return self._create_locks.setdefault(units.key(cwd), asyncio.Lock())

    async def create_unit(self, cwd: str, slug: str, brief: str = "") -> dict[str, Any]:
        """`0014` R1. Start a work unit, in the product's store rather than the repository.

        The number and the slug grammar are `cos.mjs`'s, through `coscc/units.py`. Nothing
        here is a second opinion about either — `.claude/CLAUDE.md` says that script is the
        one place the loop is defined.

        Since `0017` it also opens the unit's own worktree, detached at the workspace's
        `main`. A worktree that cannot be opened does not undo the unit: the result says
        why under `worktree.error`, and the next step that needs the tree tries again.
        """
        self._workspace_or_refuse(cwd)
        root = Path(cwd).expanduser().resolve()
        async with self._create_lock(cwd):
            reserve = [root]
            try:
                reserve += [
                    Path(t["path"]) for t in (await gitops.worktree_list(root))[1:]
                    if (Path(t["path"]) / units.COS_DIR).is_dir()
                ]
            except GitError:
                pass
            try:
                made = {
                    "cwd": cwd,
                    # The host repository's own `.cos/` counts toward the number, so a unit
                    # started here cannot take a number already used there
                    # (`0001_product-describes-a-state-it-is-not-in` R10), and since `0017`
                    # so does every worktree's. Counting is `cos.mjs`'s.
                    **units.create(cwd, slug, brief, self.config.data_dir, reserve_from=reserve),
                }
            except (CannotCreate, BadUnit) as e:
                raise Invalid(str(e)) from e
            try:
                made["worktree"] = await worktrees.ensure(
                    cwd, made["unit"], None, self.config.data_dir
                )
            except (GitError, BadUnit) as e:
                made["worktree"] = {"path": "", "error": str(e)}
        return made

    async def _worktree(self, cwd: str, unit: str, strict: bool = False) -> dict[str, Any] | None:
        """The unit's worktree, opened on its branch if the branch exists and it is not.

        None when there is none and none can be opened — the workspace is dirty on the
        unit's branch, or is not a git repository at all. `strict` turns the first of those
        into `Invalid`: a step must not run on a tree that is not on its unit's branch.
        """
        try:
            found = await worktrees.find(cwd, unit, self.config.data_dir)
            if found is not None and found["branch"]:
                return {"path": found["path"], "branch": found["branch"]}
            try:
                branch = units.branch_name(cwd, unit, self.config.data_dir)
                await gitops.rev_parse(Path(cwd).expanduser().resolve(), f"refs/heads/{branch}")
            except (CannotCreate, BadUnit, GitError):
                branch = None
            if branch is None:
                return {"path": found["path"], "branch": ""} if found else None
            # The branch exists and the tree is not on it: open it there (`worktrees.ensure`).
            made = await worktrees.ensure(cwd, unit, branch, self.config.data_dir)
            return {"path": made["path"], "branch": made["branch"], "base": made.get("base")}
        except (GitError, BadUnit) as e:
            if strict:
                raise Invalid(f"{unit}'s worktree could not be opened on its branch: {e}") from e
            return None

    async def answer(
        self,
        cwd: str,
        unit: str,
        artifact: str,
        question: Any,
        answer: str,
        answered_by: str,
    ) -> dict[str, Any]:
        """`0016` R2–R4. A person answers one item under an artifact's `## Open questions`.

        The only route in this app that writes into an artifact a stage wrote, and it only
        ever **appends**: the file is opened `"a"`, never `"w"`, so every byte above the
        `## Answers` block is the byte the stage left there (R4). What counts as a question
        and whether it is answered is `cos.mjs`'s decision, read through one board read;
        nothing here parses `## Open questions` a second time (R7).

        Not an approval, and it starts nothing. `answered_by` is whatever name the caller
        typed: no route in this app has a login, so it is a claim, not an identity.
        """
        self._workspace_or_refuse(cwd)
        name = str(answered_by or "").strip()
        text = str(answer or "").strip("\n")
        async with self._answer_lock:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            asked = [q for q in found.get("questions") or [] if q.get("artifact") == artifact]
            if not asked:
                raise Invalid(f"{artifact} in {unit} has no numbered item under ## Open questions")
            try:
                number = int(question)
            except (TypeError, ValueError):
                raise Invalid(f"a question is named by its number, got {question!r}") from None
            if number not in {q["n"] for q in asked}:
                raise Invalid(
                    f"{artifact} has no question {number} "
                    f"(it has {', '.join(str(q['n']) for q in asked)})"
                )
            if not text.strip():
                raise Invalid("the answer is empty")
            if not name or "\n" in name or "\r" in name:
                raise Invalid("say who is answering, on one line")
            nxt = str(found.get("next") or "")
            if nxt == "finished" or nxt.startswith("closed"):
                raise Invalid(f"{unit} is {nxt}; its questions can no longer be answered")
            # A line that reads as a heading would end this block early or open another,
            # and `cos.mjs` would then read the answer wrongly. Refusing is cheaper and more
            # honest than escaping somebody's words.
            if any(line.lstrip().startswith("#") for line in text.splitlines()):
                raise Invalid("no line of an answer may start with #")

            path = self._unit_dir(cwd, unit) / artifact
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as e:
                raise Invalid(f"could not read {artifact}: {e}") from e
            lines = existing.splitlines()
            heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
            if heading is not None and any(l.startswith("## ") for l in lines[heading + 1:]):
                raise Invalid(
                    f"{artifact} has a section after its ## Answers, so a block appended at "
                    "the end would not be read as an answer"
                )

            today = date.today().isoformat()
            block = ""
            if existing and not existing.endswith("\n"):
                block += "\n"
            if heading is None:
                block += "\n## Answers\n"
            block += (
                f"\n### Câu {number}\n"
                f"Answered by: {name}. Date: {today}. Via: product.\n\n"
                f"{text}\n"
            )
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(block)
            except OSError as e:
                raise Invalid(f"could not write {artifact}: {e}") from e

        # `0016` plan, in place of spec R9: the store is not a git repository, so there is
        # no commit to make. The provenance this app already keeps is a row in `outputs`.
        # Never raises: the answer is on disk, and failing the request now would tell the
        # person it was not.
        history = self._history()
        if history is not None:
            try:
                history.add_output(
                    self._journal_key(cwd),
                    unit,
                    artifact.removesuffix(".md"),
                    "deliverable",
                    artifact,
                    actor=f"human:{name}",
                    source="answer",
                )
            except (OSError, BadTransition, Busy):
                pass

        return {
            "unit": unit,
            "artifact": artifact,
            "question": number,
            "answered_by": name,
            "date": today,
        }

    async def start_branch(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0014` R4. Cut this unit's branch in the workspace and switch to it.

        The name is not chosen here and is not the caller's: `cos.mjs unit-branch` reads
        the `Type:` the intent declared and prints `<type>/<slug>`. `coscc/gitops.py`
        carries the list of what the app may do with it, which is this and nothing else.

        Since `0001_product-describes-a-state-it-is-not-in` it is cut from the trunk **as the
        remote has it**, not from whatever the local `main` last saw: fetch, read the SHA
        that fetch brought, cut from that SHA (R1). If the fetch fails nothing is cut and
        the refusal says so (R2) — cutting from a stale `main` with a warning would still
        open the pull request on the wrong base. The result names the ref and the commit
        (R3). The remote and the trunk are constants here, never taken from a request.
        """
        self._workspace_or_refuse(cwd)
        try:
            name = units.branch_name(cwd, unit, self.config.data_dir)
        except (CannotCreate, BadUnit) as e:
            raise Invalid(str(e)) from e
        # `0017`. Cut in the unit's own worktree, never in the workspace: cutting there is
        # what took one unit's branch away from another. The workspace stays on `main`.
        try:
            tree = await worktrees.ensure(cwd, unit, None, self.config.data_dir)
        except (GitError, BadUnit) as e:
            raise Invalid(f"Could not open {unit}'s worktree, so no branch was cut. {e}") from e
        repo = Path(tree["path"])
        try:
            await gitops.fetch(repo, BRANCH_REMOTE, BRANCH_TRUNK)
        except GitError as e:
            raise Invalid(
                f"Could not update {BRANCH_TRUNK} from {BRANCH_REMOTE}, so no branch was cut. "
                f"Nothing in the repository changed. git said: {e}"
            ) from e
        try:
            sha = await gitops.rev_parse(repo, f"refs/remotes/{BRANCH_REMOTE}/{BRANCH_TRUNK}")
            output = await gitops.create_branch(repo, name, sha)
        except GitError as e:
            raise Invalid(str(e)) from e
        # Prepared here rather than when the tree was made (`0017` plan): the lockfiles an
        # `impl` works with are the ones at the commit just cut from, not the local `main`.
        # A failure is returned, not raised — the branch is cut either way — and `run_step`
        # refuses `impl` until preparing succeeds (R6).
        prepared = await worktrees.prepare(repo, cwd)
        return {
            "cwd": cwd,
            "unit": unit,
            "branch": name,
            "base": f"{BRANCH_REMOTE}/{BRANCH_TRUNK}",
            "sha": sha[:7],
            "output": output,
            "worktree": str(repo),
            "switched": bool(tree.get("switched")),
            "prepare": prepared,
        }

    async def branch_here(self, cwd: str) -> dict[str, Any]:
        """Which branch the workspace is on. A read, so the page can show it."""
        self._workspace_or_refuse(cwd)
        try:
            return {
                "cwd": cwd,
                "branch": await gitops.current_branch(Path(cwd).expanduser().resolve()),
            }
        except GitError as e:
            raise Invalid(str(e)) from e

    def timeline(self, cwd: str, unit: str) -> dict[str, Any]:
        """What has happened to one unit, oldest first (`spec.md` R15)."""
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            return {"cwd": cwd, "unit": unit, "runs": [], "cost": {}}
        key = self._journal_key(cwd)
        try:
            runs = journal.timeline(key, unit)
        except Busy as e:
            raise Invalid(str(e)) from e
        return {"cwd": cwd, "unit": unit, "runs": runs, "cost": totals_of(runs)}

    def _history(self) -> History | None:
        """The transition log, or `None` when there is no working folder to keep it in.

        Same shape and same reasoning as `_journal`: with nothing set, the app behaves as
        it did before, and the safe direction to fail in is read-only.
        """
        return (
            History(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )

    def unit_history(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0013` R8. Everything the log knows about one unit.

        **Beside the board, not instead of it.** `board()` still asks `cos.mjs` and still
        reads state out of the `Status:` line on disk (`intent.md` constraint 3); this
        answers from the transition log. Two sources during a transition is deliberate and
        has a cost, and `spec.md` C2 and C5 are where that cost is written down.

        `state` here is a projection over `transitions` and is computed, never stored —
        R1. It is returned alongside the transitions rather than instead of them precisely
        so a caller can check one against the other.

        `settled_edits` is carried because it is the unit of measure `intent.md` named:
        the number of times an artifact was rewritten after it had been settled, which
        was 0 before this and 43 in this repository on 2026-09-22.
        """
        self._workspace_or_refuse(cwd)
        history = self._history()
        key = self._journal_key(cwd)
        if history is None:
            return {
                "cwd": cwd,
                "unit": unit,
                "recording": False,
                "machine": "",
                "written_under": [],
                "mixed_state_sets": None,
                "transitions": [],
                "state": {},
                "settled_edits": 0,
                "sessions": [],
                "unknown_transitions": 0,
                "outputs": [],
                "output_counts": {},
            }
        try:
            rows = history.transitions(key, unit)
            state = history.state(key, unit)
            sessions = history.sessions_of(key, unit)
            outputs = history.outputs(key, unit)
            counts = history.output_counts(key, unit)
            written_under = history.machines_in(key, unit)
        except Busy as e:
            raise Invalid(str(e)) from e
        # `spec.md` C5. Rows written under one state set and read under another compare
        # words that never meant the same thing, and nothing about that failure looks like
        # a failure -- every query still returns rows. Said out loud in the payload rather
        # than refused, because refusing a *read* would hide the only evidence there is.
        # A caller that goes on to compare these against another source must stop here.
        foreign = [name for name in written_under if name != history.machine.name]
        return {
            "cwd": cwd,
            "unit": unit,
            "recording": True,
            "machine": history.machine.name,
            "written_under": written_under,
            "mixed_state_sets": (
                None if not foreign
                else f"this unit holds transitions written under {', '.join(foreign)}, "
                     f"but is being read under {history.machine.name} — the states in "
                     "those rows do not mean what they appear to mean here"
            ),
            "transitions": rows,
            "state": state,
            "settled_edits": len(settled_edits(rows, history.machine)),
            "sessions": sessions["sessions"],
            "unknown_transitions": sessions["unknown_transitions"],
            "outputs": outputs,
            "output_counts": counts,
        }

    def units_with_history(self, cwd: str) -> dict[str, Any]:
        """Every unit the log has a transition for, in the order they first appear.

        Not the same list as `board()`'s, and the difference is the point: a unit retired
        from the working tree still has a history, and this is the only place it can be
        seen. `.cos/` in this repository lost five units that way (`f506aae`).
        """
        self._workspace_or_refuse(cwd)
        history = self._history()
        if history is None:
            return {"cwd": cwd, "recording": False, "units": []}
        try:
            return {
                "cwd": cwd,
                "recording": True,
                "units": history.units(self._journal_key(cwd)),
            }
        except Busy as e:
            raise Invalid(str(e)) from e

    # -- sessions -----------------------------------------------------------

    def _is_member(self, cwd: str) -> bool:
        """The single membership question: env list, or a store entry under the root."""
        if self.config.is_workspace(cwd):
            return True
        return self.store is not None and self.store.resolves_to_entry(cwd)

    def _workspace_or_refuse(self, cwd: str) -> str:
        """The single gate. Every capability below goes through it.

        `spec.md` R21 wants this asked on every read rather than cached, because after
        the workspace list is no longer fixed for the life of the process.
        """
        # Recomputed from the working folder every time, so editing the store by hand
        # cannot widen what this accepts — the entry has to name a segment, and the
        # segment has to resolve back under the root.
        if self._is_member(cwd):
            return cwd
        raise Invalid(f"not a configured workspace: {cwd}")

    def sessions_for(self, cwd: str, limit: int | None = None) -> dict[str, Any]:
        self._workspace_or_refuse(cwd)
        rows = reader.list_for_directory(cwd, limit=limit)
        for row in rows:
            # Terminal sessions show up here too — the read layer sees them. This flag is
            # what tells a caller which of them it may write to (`spec.md` C1).
            row["resumable"] = self.config.may_resume(
                self.sessions.created_here(row["session_id"])
            )
        return {"cwd": cwd, "sessions": rows}

    def history(self, cwd: str, session_id: str) -> dict[str, Any]:
        self._workspace_or_refuse(cwd)
        if not session_id:
            raise Invalid("session_id is required")
        return {
            "session_id": session_id,
            "messages": reader.history(session_id, cwd),
        }

    def check_send(self, cwd: str, text: str) -> None:
        """Everything a caller can reject with a status code, decided before any output.

        Split out from `stream` on purpose. The design draws a hard line between two kinds of
        failure: an invalid request is a status code, while a refusal that surfaces once
        the reply is already streaming has to arrive as data, because the status line is
        long gone (`web.py` docstring on `post_send`). Validating inside an async
        generator would collapse that distinction, since the first item is only pulled
        after a caller has committed to streaming. A test in `web_test.py` holds the line.
        """
        self._workspace_or_refuse(cwd)
        if not text.strip():
            raise Invalid("text is required")

    async def stream(
        self, cwd: str, text: str, session_id: str | None = None
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield `(kind, payload)` exactly as the session layer does.

        Re-runs `check_send` so the generator is safe on its own; the checks are pure, so
        doing them twice costs nothing and leaves no caller able to skip them.
        """
        self.check_send(cwd, text)
        # `0004_no-setting-says-which-model-runs-a-stage`. Chat is a row of the same table
        # as the stages.
        model, model_source = self._model_for(models.CHAT)
        async for item in self.sessions.stream(
            cwd, text, session_id, **({"model": model} if model is not None else {})
        ):
            if item[0] == "session":
                # `0019` plan step 2, risk 2. `api.py` treats every kind but `chunk` as
                # the terminal `done` row; forwarding this to chat would turn it into a
                # spurious one, mid-reply.
                continue
            if item[0] == "done":
                # Chat wrote nothing to the run log before this. Now one record per turn
                # says which model it asked for — the model a *new* client is created with.
                # A client already live keeps the model it was made with (plan Risk 7).
                journal = self._journal()
                if journal is not None:
                    try:
                        journal.append({
                            "kind": "chat",
                            "workspace": self._journal_key(cwd),
                            "unit": "",
                            "stage": "",
                            "model": model,
                            "model_source": model_source,
                            "session_id": (item[1] or {}).get("session_id", ""),
                        })
                    except (BadRecord, Busy):
                        pass  # a busy log must not cost the reply that was already paid for
            yield item

    # -- which model each stage runs on --------------------------------------
    #
    # `0004_no-setting-says-which-model-runs-a-stage`. The resolving is `coscc/models.py`;
    # this is where its three inputs are gathered: the stage list from `cos.mjs`, the
    # overrides from `prefs`, `COS_MODEL` from `Config`.

    def _model_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(Data(self.config.data_dir).pref_rows(models.PREFIX))

    def _model_for(self, name: str) -> tuple[str | None, str]:
        """`(model, source)` for one stage, or for `chat`. Never raises on bad data.

        Takes no stage list: the caller has already checked `name` against `cos.mjs`
        (`run_step` found the row), and resolving one row does not need the others.
        """
        overrides, _ = self._model_overrides()
        defaults, _ = models.load_defaults()
        return models.resolve(name, overrides, defaults, self.config.model)

    async def stage_models(self) -> dict[str, Any]:
        """Every row Settings shows: stage, agents, model, where the model came from.

        When `node` cannot run there is no stage list, and inventing one here would be the
        second copy of the loop. So the table is empty and `problems` says why.
        """
        try:
            stages = await board_reader.stages()
        except Unavailable as e:
            return {"rows": [], "problems": [str(e)], "cos_model": self.config.model}
        overrides, bad_rows = self._model_overrides()
        defaults, bad_defaults = models.load_defaults()
        table = models.table(stages, overrides, defaults, self.config.model, SESSIONS_PER_STEP)
        table["problems"] = bad_defaults + bad_rows + table["problems"]
        table["cos_model"] = self.config.model
        return table

    async def set_stage_model(self, name: Any, model: Any = None) -> dict[str, Any]:
        """Set one row's model, or remove the override when `model` is None.

        **No login, like every route here.** Whoever reaches the port can move `review` to
        a weaker model, or every stage to a dearer one. The one trace is the `setting`
        record appended below, with the old and new value. It chooses a model and nothing
        else: no gate reads it, and no stage starts because of it.

        The model name is not checked against the API — an unknown one fails at the next
        step of that stage, with the CLI's own error (spec Out of scope).
        """
        if not isinstance(name, str) or not name:
            raise Invalid("name is required")
        try:
            stages = await board_reader.stages()
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if name not in stages and name != models.CHAT:
            raise Invalid(
                f"no such stage: {name} (use one of {', '.join(stages + [models.CHAT])})"
            )
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise Invalid("model is required")
            model = model.strip()

        data = Data(self.config.data_dir)
        key = models.PREFIX + name
        old = self._model_overrides()[0].get(name)
        if model is None:
            data.delete_pref(key)
        else:
            data.set_pref(key, model)

        journal = self._journal()
        if journal is not None:
            try:
                journal.append({
                    "kind": "setting", "workspace": "", "unit": "", "stage": "",
                    "name": key, "old": old, "new": model,
                })
            except (BadRecord, Busy) as e:
                raise Invalid(f"the setting was saved but not logged: {e}") from e
        return await self.stage_models()

    # -- activity, usage and settings ---------------------------------------
    #
    # Three read-only methods. `spec.md` said `Service` would not change,
    # and this is the one place it does — recorded as a departure in `plan.md`. The
    # alternative was to let the new page read `Journal` and `policy` directly, and that
    # would break the rule this module exists for (see the module docstring), which is a
    # far worse trade than three methods that only read.

    def _records_or_none(self, cwd: str) -> list[dict[str, Any]] | None:
        """Every record for this workspace, or `None` when nothing is being recorded.

        `activity` and `usage` both want the same rows and are always called together by
        the Activity screen. Shared so the scan is written once — see `activity_and_usage`
        for why it is also *read* once.
        """
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            return None
        try:
            return journal.records(self._journal_key(cwd))
        except Busy as e:
            raise Invalid(str(e)) from e

    def _events_of(self, cwd: str, rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
        events = [
            {
                "at": r.get("at") or "",
                "kind": r.get("kind") or "",
                "unit": r.get("unit") or "",
                "stage": r.get("stage") or "",
                "mode": r.get("mode") or "",
                "outcome": r.get("outcome") or "",
                "session_id": r.get("session_id") or "",
                "artifact": r.get("artifact") or "",
                "denials": int(r.get("denials") or 0),
                "cost": {f: r.get(f) for f in COST_FIELDS + (COST_USD,) if r.get(f)},
            }
            for r in rows
        ]
        events.reverse()
        return {"cwd": cwd, "events": events[:limit], "recording": True}

    def _usage_of(self, cwd: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        per_unit: dict[str, dict[str, Any]] = {}
        for record in rows:
            if record.get("kind") != "end":
                continue
            bucket = per_unit.setdefault(str(record.get("unit") or ""), zero_cost())
            add_cost(bucket, record)
        total = zero_cost()
        for bucket in per_unit.values():
            add_cost(total, bucket)
        return {"cwd": cwd, "total": total, "per_unit": per_unit, "recording": True}

    def activity(self, cwd: str, limit: int = 40) -> dict[str, Any]:
        """What has happened across the whole workspace, newest first.

        `timeline` answers the same question for one unit. This one exists because the
        Activity screen is workspace-wide, and building it by calling `timeline` once per
        unit would spawn one board read per unit to find out what the units are.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "events": [], "recording": False}
        return self._events_of(cwd, rows, limit)

    def usage(self, cwd: str) -> dict[str, Any]:
        """What this workspace has cost, added up from its records.

        Added rather than stored, for the reason `journal.totals` gives: a stored total is
        a second number that can disagree with the first.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "total": {}, "per_unit": {}, "recording": False}
        return self._usage_of(cwd, rows)

    def activity_and_usage(self, cwd: str, limit: int = 40) -> dict[str, Any]:
        """Both of the above, from one read.

        The Activity screen wants both at once. Calling the two public methods meant two
        connections and two full parses of the identical rows; they stay for the JSON API,
        and this is what the page calls.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {
                "cwd": cwd, "events": [], "total": {}, "per_unit": {}, "recording": False,
            }
        return {**self._events_of(cwd, rows, limit), **self._usage_of(cwd, rows)}

    def settings(self) -> dict[str, Any]:
        """The safety posture, as something a screen can render. Read only.

        `spec.md` R18: this screen shows the four knobs and the grant table and can
        change neither. There is no setter here for the same reason there is none in
        `config.from_env` — a request that could turn a knob is a request that could turn
        it on.

        **The model per stage is the one thing Settings can change**, and it is not here:
        `stage_models` and `set_stage_model` are. `0004_no-setting-says-which-model-runs-
        a-stage` made it changeable on purpose — a model is a choice of cost, not of
        capability, and the originator asked for it without a release. The price is a
        route with no login that decides what every step spends. `cos_model` below is only
        the fallback for a row nothing else answers.
        """
        c = self.config
        return {
            "working_dir": c.working_dir,
            "data_dir": str(Data(c.data_dir).root),
            "host": c.host,
            "port": c.port,
            "cos_model": c.model,
            "knobs": [
                {
                    "name": "tools",
                    "value": ", ".join(c.effective_tools()) or "none",
                    "on": bool(c.effective_tools()),
                    "detail": "Chat sessions are created with this tool list. Empty means "
                              "chat only — a session with no tools cannot write a file.",
                },
                {
                    "name": "allow_write_and_exec",
                    "value": "on" if c.allow_write_and_exec else "off",
                    "on": c.allow_write_and_exec,
                    "detail": "While off, no write or exec tool survives into a session, "
                              "whatever the tool list says.",
                },
                {
                    "name": "bypass_permissions",
                    "value": "on" if c.bypass_permissions else "off",
                    "on": c.bypass_permissions,
                    "detail": "Off, and not settable over HTTP. The only way in is the "
                              "environment this process was started with.",
                },
                {
                    "name": "resume_foreign_sessions",
                    "value": "on" if c.resume_foreign_sessions else "off",
                    "on": c.resume_foreign_sessions,
                    "detail": "Off because it is untested, not because it is dangerous. "
                              "The app resumes only what it created.",
                },
            ],
            # The board's own grants, from `policy.py` rather than from the config. They
            # are separate on purpose, and the screen has to show that they are.
            "grants": [
                {
                    "stage": stage,
                    "tools": ", ".join(grant.tools) or "none",
                    "commands": ", ".join(grant.commands) or "none",
                    "max_turns": grant.max_turns,
                    "max_budget_usd": grant.max_budget_usd,
                    "app_writes_artifact": grant.app_writes_artifact,
                    "warning": grant.warning,
                }
                for stage, grant in sorted(GRANTS.items())
            ],
            "prose_stages": list(PROSE_STAGES),
        }

    # -- artifacts and preferences ------------------------------------------

    def artifact(self, cwd: str, unit: str, stage: str) -> dict[str, Any]:
        """The text of one stage's artifact, or why there is none.

        The path is built by `runner.unit_dir`, which validates the unit name against the
        `NNNN_slug` shape. That is the same function the runner uses, so a name this
        refuses is a name no step could run against either — one rule, not two.
        """
        self._workspace_or_refuse(cwd)
        if stage not in STAGE_FILES:
            raise Invalid(f"no such stage: {stage}")
        filename = f"{stage}.md"
        path = self._unit_dir(cwd, unit) / filename
        if not path.is_file():
            return {"unit": unit, "stage": stage, "file": filename, "text": "", "exists": False}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise Invalid(f"could not read {filename}: {e}") from e
        return {"unit": unit, "stage": stage, "file": filename, "text": text, "exists": True}

    # Which preferences the page may keep. An open key/value store reachable from a
    # request is a place to put anything; this is the list of things the Settings screen
    # actually remembers, and nothing else is writable.
    PREFERENCES = {"density": "comfortable", "screen": "overview", "board_view": "Board"}

    def preferences(self) -> dict[str, Any]:
        data = Data(self.config.data_dir)
        stored = data.prefs()
        return {k: stored.get(k, default) for k, default in self.PREFERENCES.items()}

    def set_preference(self, key: str, value: Any) -> dict[str, Any]:
        if key not in self.PREFERENCES:
            raise Invalid(f"not a stored preference: {key}")
        if not isinstance(value, (str, int, float, bool)):
            raise Invalid("a preference must be a simple value")
        Data(self.config.data_dir).set_pref(key, value)
        return {"key": key, "value": value}
