"""The board: every unit of a workspace with its stage, what is running on it and its worktree.

A mixin with no fields, which `Service` inherits.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from coscc.units import backlog, prose_import
from coscc.agent import agents
from coscc.units import board as board_reader
from coscc.git import gitops
from coscc.github import integrate
from coscc.units.board import Unavailable
from coscc.data import Data, now as _now
from coscc.git.gitops import GitError
from coscc.runlog.journal import BadRecord, Journal, last_runs, timelines_of, totals_of
from coscc.data import Busy
from coscc.units.history import BadTransition
from coscc.units.meta import MetaError, UnitMeta
from coscc.agent.policy import grant_for
from coscc.agent import steps as steps_mod
from coscc import units
from coscc.git import worktrees
from coscc.units import BadUnit
from coscc.service.store import valid_name
from coscc.service.common import (
    CONSEQUENCE,
    Invalid,
    _younger_than,
    attention_reason,
    consequence,
    outcome_label,
    unit_state,
)


# An `ended, unknown` row stops being shown this long after it began, unless a later `start`
# of the same unit retired it first.
UNKNOWN_END_FOR = timedelta(hours=24)


def waits_for(unit: dict[str, Any]) -> list[str]:
    """The units `impl` waits on, when `cos.mjs` said it waits; else none."""
    if unit.get("why") != "dependency":
        return []
    return [d["ref"] for d in unit.get("depends_on") or [] if d.get("merged") is not True]


def _attach_comment_state(units_: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    """Give every review round a `comment`: on the pull request, or not and why.

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


def answerable(unit: dict[str, Any]) -> bool:
    """Whether the board invites an answer on this unit: not once it is finished, closed or
    dropped."""
    # `next`'s code, never its words.
    why = str(unit.get("why") or "")
    dropped = (unit.get("hold") or {}).get("state") == "dropped"
    return not (why in ("finished", "rejected") or dropped)


class BoardMixin:
    # -- board --------------------------------------------------------------

    def _journal(self) -> Journal | None:
        """The run log, or `None` when there is no working folder to keep it in.

        Unset `COS_WORKING_DIR` means the board is read-only: there is nowhere to record a mode,
        so every step reads `manual` and nothing can be started. That is the safe way to fail.
        """
        return (
            Journal(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )

    # Check-and-mark with no `await` in any of these, so nothing on the event loop
    # can come between the look and the write.
    def _busy(self, key: str, unit: str) -> str:
        """What holds this unit, in the one sentence every refusal carries, or `""`."""
        mark = self._active.get((key, unit))
        return steps_mod.describe(unit, mark) if mark is not None else ""

    def _take(self, key: str, unit: str, kind: str, stage: str = "") -> steps_mod.Mark:
        said = self._busy(key, unit)
        if said:
            raise Invalid(said)
        mark = steps_mod.Mark(kind, stage, "preparing" if kind == "step" else "")
        self._active[(key, unit)] = mark
        return mark

    def _release(self, key: str, unit: str, mark: steps_mod.Mark) -> None:
        """Only this mark: a refused or late caller never frees a unit someone else holds."""
        if self._active.get((key, unit)) is mark:
            del self._active[(key, unit)]

    @staticmethod
    def _journal_key(cwd: str) -> str:
        """How a workspace is named in the journal.

        The resolved path, not a store name: an env-declared workspace has no name at all
        (`config.is_workspace`), and a path is the one identifier both kinds have. The
        cost is that moving a workspace detaches its history from it.
        """
        return str(Path(cwd).expanduser().resolve())

    def _app_identity(self) -> dict[str, str]:
        """The running build's version and commit, for a step's `start` row.

        `Updater.me` is `update.identity`, computed once and kept. Anything failing is two
        empty strings; it never stops a step.
        """
        try:
            me = self.updater.me()
            return {"version": str(me.get("version") or ""), "commit": str(me.get("commit") or "")}
        except Exception:  # noqa: BLE001 — a record field, never a reason to refuse a step
            return {"version": "", "commit": ""}

    def _units_root(self, cwd: str) -> Path:
        """Where this workspace's units live. One question, asked of one module.

        `coscc/units/__init__.py` owns the answer; this is the only place in the service that asks.
        """
        return units.root(cwd, self.config.data_dir)

    def _peer_table(self) -> tuple[list[tuple[str, str]], list[str]]:
        """Every workspace as `(name, path)`, and what was left out and why.

        A name two workspaces share is given for neither: a reference to it could mean
        either store. A name `valid_name` refuses (an env workspace's basename can be one)
        could not be a reference at all.
        """
        rows = self.workspaces()["workspaces"]
        count = Counter(str(r["name"]) for r in rows)
        peers: list[tuple[str, str]] = []
        problems: list[str] = []
        for name, n in count.items():
            if n > 1:
                problems.append(
                    f"Two workspaces are named {name}, so neither is linked by that name."
                )
        for r in rows:
            name = str(r["name"])
            if count[name] == 1 and valid_name(name):
                peers.append((name, str(r["path"])))
        return peers, problems

    def _peers(self) -> list[tuple[str, Path]]:
        """Every named workspace as `(name, store root)`, the stores an `Idea:` may name."""
        return [(name, self._units_root(path)) for name, path in self._peer_table()[0]]

    def _snapshot(
        self,
        cwd: str,
        units_: Iterable[str] | None = None,
        peers: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """What `cos.mjs --state` decides on: `cwd`'s units and those of every workspace a link
        may name, from `cos.db`; `units_` narrows it as `UnitMeta.snapshot` says. A store not
        imported yet is imported first.

        Raises `Invalid` when an import cannot run: a board read on metadata nobody could
        read would show every unit as not started. So also when `cos.db` cannot be read, in one
        sentence that names the workspace; the error, which may name the database's path, goes
        to the log. `peers` is `_peer_table`'s, when the caller read it already.
        """
        meta = self._unit_meta()
        own = self._journal_key(cwd)
        names = {
            name: self._journal_key(path)
            for name, path in (self._peer_table()[0] if peers is None else peers)
        }
        try:
            for key in {own, *names.values()} - self._imported:
                # A workspace with no units yet has nothing to import: one `stat`, not a query.
                if not (units.root(key, self.config.data_dir) / units.COS_DIR).is_dir():
                    continue
                if meta.imported(key):
                    # The answers imported before a row said whose each was.
                    meta.classify_answers(key)
                    self._imported.add(key)
                else:
                    self._import(meta, key)
            return meta.snapshot(own, names, units_)
        except (Busy, sqlite3.Error, OSError) as e:
            print(f"coscc: the units of {own} could not be read: {e}", file=sys.stderr)
            raise Invalid(
                f"the units of {self._workspace_name(own) or 'a workspace'} could not be read"
            ) from e

    def _meta_of(self, cwd: str, unit: str) -> dict[str, Any]:
        """`unit`'s entry in the snapshot, `{}` when the app has none."""
        snap = self._snapshot(cwd, [unit])
        return snap["units"].get(f"{snap['workspace']}/{unit}") or {}

    def _write_step_state(self, cwd: str, unit: str) -> str:
        """The snapshot a step that runs `cos.mjs` itself hands `--state` — the `pr`
        step's `pr-text`, the `ship` step's gate — which refuse to decide without one. Written
        as the step begins, under the data root beside `spikes/`, never in a store, and
        replaced by the next step of the unit. `""` when it could not be written: the step
        still runs, and the command it runs says what is missing."""
        path = Data(self.config.data_dir).root / "state" / units.slot(cwd) / f"{unit}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._snapshot(cwd, [unit]), ensure_ascii=False), encoding="utf-8"
            )
        except OSError, Invalid:
            return ""
        return str(path)

    def _import(self, meta: UnitMeta, key: str) -> None:
        """One store into `cos.db`, once; what it could not read, if anything, goes to
        the log and to one `import` row of the run log. A store with no `.cos/` yet is left
        for later."""
        store = units.root(key, self.config.data_dir)
        if not (store / units.COS_DIR).is_dir():
            return
        try:
            unknowns = meta.import_store(key, store)
        except (MetaError, BadTransition, Busy, sqlite3.Error, OSError) as e:
            # The workspace by name and the error in the log: `key` is a path, and `Busy` and
            # `MetaError` carry the database's path or `cos.mjs`'s stderr.
            print(f"coscc: the units of {key} could not be imported: {e}", file=sys.stderr)
            name = self._workspace_name(key) or "a workspace"
            raise Invalid(f"the units of {name} could not be imported") from e
        # Only when there is something to report, so a store read cleanly adds no row.
        if not unknowns:
            return
        for u in unknowns:
            print(
                f"coscc: import {key}: {u['unit']} {u['artifact']} {u['field']}: {u['reason']}",
                file=sys.stderr,
            )
        journal = self._journal()
        if journal is not None:
            try:
                journal.append({"kind": "import", "workspace": key, "unknowns": unknowns})
            except BadRecord, Busy:
                pass

    async def _import_rounds(self, cwd: str, units_: list[dict[str, Any]]) -> None:
        """The review rounds only the prose of a store holds, into `cos.db`, on the
        first board read that finds the store unimported (`coscc/units/prose_import.py`). The
        board this read shows is the same either way. One that cannot write goes to the log and
        is tried on the next read."""
        meta = self._unit_meta()
        key = self._journal_key(cwd)
        try:
            if meta.data.has_run(prose_import.key(meta.root, key)):
                return
            root = Path(cwd).expanduser().resolve()
            heads: dict[str, str] = {}
            for sha in {
                str(r.get("reviewed"))
                for u in units_
                for r in u.get("rounds") or []
                if r.get("reviewed")
            }:
                try:
                    heads[sha] = await gitops.rev_parse(root, sha)
                except GitError:
                    pass
            prose_import.import_rounds(meta, key, units_, heads)
        except (Busy, sqlite3.Error, OSError) as e:
            print(f"coscc: the review rounds of {key} could not be imported: {e}", file=sys.stderr)

    def _workspace_name(self, cwd: str) -> str:
        """The name the app shows for `cwd`, or "" when it is not one of the workspaces."""
        here = Path(cwd).expanduser().resolve()
        for r in self.workspaces()["workspaces"]:
            if Path(r["path"]).expanduser().resolve() == here:
                return str(r["name"])
        return ""

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
        peers, peer_problems = self._peer_table()
        try:
            data = await board_reader.read(
                self._units_root(cwd), state=self._snapshot(cwd, peers=peers)
            )
        except Unavailable as e:
            raise Invalid(str(e)) from e
        await self._import_rounds(cwd, data["units"])
        # Only when there is something to say.
        if peer_problems:
            data["peer_problems"] = peer_problems
        # Each stage column's agent, by the one lookup, for the page to show only.
        # A stage the table has no row for is left out, and its column has no glyph.
        overrides = self._agent_overrides()[0]
        data["stage_agents"] = {}
        for stage in data["stages"]:
            row = agents.agent_for(stage, overrides)
            if row is not None:
                data["stage_agents"][stage] = {
                    "glyph": row["glyph"],
                    "label": agents.label({**row, "key": stage}),
                    "meaning": row["meaning"],
                    "role": row["role"],
                }
        name = self._workspace_name(cwd)
        for unit in data["units"]:
            if unit.get("repo") and name and unit["repo"] != name:
                unit["problems"] = [
                    *unit["problems"],
                    f"Repo: {unit['repo']} is not this workspace, {name}.",
                ]
            unit["waits_for"] = waits_for(unit)

        journal = self._journal()
        key = self._journal_key(cwd)
        modes: dict[tuple[str, str], str] = {}
        timelines: dict[str, list[dict[str, Any]]] = {}
        comments: list[dict[str, Any]] = []
        ranking: list[dict[str, Any]] = []
        if journal is not None:
            try:
                modes = journal.modes(key)
                # One read for every unit's cost, comment attempts and the backlog's
                # records. Asking `totals` per unit re-scanned the
                # working folder N times for the rows this already has.
                rows = journal.records(key)
            except Busy as e:
                raise Invalid(str(e)) from e
            timelines = timelines_of(rows)
            comments = [r for r in rows if r.get("kind") == "pr-comment"]
            ranking = [r for r in rows if r.get("kind") in backlog.KINDS]
        _attach_comment_state(data["units"], comments)
        # Display only: nothing below reads it, and `next`/`blocked` are untouched.
        data["backlog"] = {
            **backlog.fold(
                data["units"],
                ranking,
                backlog.measured(timelines, data["units"]),
                backlog.undetermined(timelines, data["units"]),
            ),
            "propose_warning": grant_for("estimate").warning,
            "propose_consequence": CONSEQUENCE["estimate"],
        }
        per_unit = data["backlog"].pop("per_unit")
        for unit in data["units"]:
            unit["backlog"] = per_unit.get(unit["name"]) or {
                "rank": None,
                "value": None,
                "effort": None,
                "effort_source": None,
                "relations": [],
            }

        for unit in data["units"]:
            unit_last_runs = last_runs(timelines.get(unit["name"], []))
            for row in unit["stages"]:
                # `manual` is the default because starting work is a decision someone has
                # to make, not one an unset value should make for them.
                row["mode"] = modes.get((unit["name"], row["stage"]), "manual")
                # The mode is a label; the grant follows the stage alone.
                grant = grant_for(row["stage"])
                # Carried to the page: what a step will be allowed to do has to be readable
                # before it is started.
                row["grants"] = list(grant.tools)
                row["warning"] = grant.warning
                row["consequence"] = consequence(row["stage"])
                # From the same `timelines` read above, no second scan of the run log.
                # `status` stays read from the artifact alone; this is a separate field.
                row["last_run"] = unit_last_runs.get(row["stage"])
            unit["cost"] = totals_of(timelines.get(unit["name"], [])) if journal is not None else {}
            # A label and nothing else: a deadline passing writes no row and
            # starts no step.
            unit["outcome_label"] = outcome_label(
                unit.get("outcome"), date.today(), finished=unit.get("why") == "finished"
            )
            # Decided here so the page only shows them.
            unit["answerable"] = answerable(unit)
            unit["attention_reason"] = attention_reason(unit)
            # The code the last autopilot pass held the unit back with, and its
            # detail (`overlap-pr #7`); display only, and nothing while the autopilot is off.
            held = (self._autopilot_held.get(key) or {}).get(unit["name"])
            unit["held"] = " ".join(p for p in held if p) if held else ""

        await self._attach_worktrees(cwd, data["units"])
        # One `gh pr list` for the whole read, asked only by whichever block needs it.
        prs = self._prs_once(cwd)
        asks = await self._attach_integration(cwd, data["units"], journal, key, prs)
        data["release"] = await self._attach_release(cwd, data["units"], journal, key, prs)
        for unit in data["units"]:
            # From the timelines read above: no second scan of the run log.
            ended = [r for r in timelines.get(unit["name"], []) if r.get("ended") is not None]
            unit["state"] = unit_state(
                unit, ended[-1] if ended else None, unit.pop("ci_held", None)
            )

        data["recording"] = journal is not None
        # Display only: the page shows it and decides nothing from it.
        data["autopilot"] = self._autopilot_block(key)
        # Display only.
        data["guide"] = self._guide_block(key)
        data["read_only_because"] = (
            None
            if journal is not None
            else "no working folder is set, so nothing can be recorded — set COS_WORKING_DIR"
        )
        if not data["units"]:
            # A host repository can have a `.cos/` full of units the store never heard of, so
            # the page can say which directory it read and how many units sit in the other.
            # Counted on every call, never cached.
            data["empty"] = {
                "store": str(self._units_root(cwd)),
                "host": units.key(cwd),
                "host_units": units.host_unit_count(cwd),
            }
        # Started last and never awaited: their answers count from the next read.
        self._ask_ci(asks)
        return data

    @staticmethod
    def _prs_once(cwd: str):
        """`integrate.open_prs` for `cwd`, asked at most once however often it is awaited;
        `gh`'s error as a string."""
        held: list[Any] = []

        async def prs() -> list[dict[str, Any]] | str:
            if not held:
                try:
                    held.append(await integrate.open_prs(str(Path(cwd).expanduser().resolve())))
                except integrate.IntegrateError as e:
                    held.append(str(e))
            return held[0]

        return prs

    def _mark_running(self, key: str, unit: str, stage: str, kind: str) -> str:
        """Put one entry in `_running` and return its id, for the `finally` to pop.

        `started` is stamped by the same clock `Journal.append` uses, so it reads like an
        `at`. `turns` and `cost_usd` stay `None` while the session runs.
        """
        rid = uuid.uuid4().hex
        self._running[rid] = {
            "workspace": key,
            "unit": unit,
            "stage": stage,
            "started": _now(),
            "kind": kind,
            "turns": None,
            "cost_usd": None,
        }
        return rid

    def _running_here(self, key: str, overrides: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """`running`'s `running`, from memory alone: what `_guide_block` reads too."""
        running: dict[str, list[dict[str, Any]]] = {}
        for entry in self._running.values():
            if entry["workspace"] != key:
                continue
            kind = entry["kind"]
            row = None if kind == "rebase" else agents.agent_for(entry["stage"], overrides)
            agent = {"glyph": row["glyph"], "name": row["name"]} if row else None
            running.setdefault(entry["unit"], []).append(
                {
                    "kind": kind,
                    "stage": entry["stage"],
                    "agent": agent,
                    "started": entry["started"],
                    "turns": entry["turns"],
                    "cost_usd": entry["cost_usd"],
                    # A board step's events; `""` for an integration or an estimate.
                    "run": entry.get("run", ""),
                }
            )
        return running

    def running(self, cwd: str) -> dict[str, Any]:
        """What has an agent working in this workspace now, and what ended unseen.

        `running` is `_running` for this workspace, one element per entry, by unit.
        `unknown_end` is every `start` the run log holds without an `end` that no entry
        accounts for: the unit has nothing running here, no later `start` of the unit
        retired it, and it is younger than `UNKNOWN_END_FOR`. Matched by
        unit, not by session: `_active` allows one per unit per process, so a unit with an
        entry has no other `start` open in this process — only one another process wrote,
        and that one is shown as ended.

        Reads memory and the run log, nothing else: no `git`, no `gh`, no `cos.mjs`, and
        writes nothing. A busy run log is a `note`, not a refusal — the board asks this
        every few seconds, and a lock someone else holds must not break the board.
        """
        self._workspace_or_refuse(cwd)
        key = self._journal_key(cwd)
        # Through the one lookup, so an override shows here too. Read once per call.
        overrides = self._agent_overrides()[0]
        running = self._running_here(key, overrides)
        out: dict[str, Any] = {"running": running, "unknown_end": {}}
        journal = self._journal()
        if journal is None:
            return out
        try:
            opened = journal.open_starts(key)
        except Busy as e:
            out["note"] = str(e)
            return out
        oldest = datetime.now(timezone.utc) - UNKNOWN_END_FOR
        for unit, found in opened.items():
            if unit in running:
                continue
            rows = [
                # The name the `start` carries, or its stage's for an older one.
                {
                    "stage": r["stage"],
                    "started": r["started"],
                    "agent": agents.of_record(r, overrides),
                }
                for r in found["open"]
                if r.get("started")
                and r["started"] == found["last_start"]
                and _younger_than(r["started"], oldest)
            ]
            if rows:
                out["unknown_end"][unit] = rows
        return out

    async def _attach_worktrees(self, cwd: str, units_: list[dict[str, Any]]) -> None:
        """Give every unit `worktree: {path, branch, prepare}`, or `None`.

        One `git worktree list` for the whole board. A `finished` unit that still has a tree
        is cleaned up here, so a unit shipped at a terminal is cleaned up too — at the
        cost of a `gh pr view` (up to 30s) on **every** board read for as long as the tree
        stays: once, when the removal succeeds; on each read after, when it does not
        (`gh` failing, the pull request not merged, the local branch off the merged head).
        Nothing remembers a refusal, so a transient `gh` error is retried rather than
        believed. A dirty tree is refused before `gh` is asked.
        """
        root = Path(cwd).expanduser().resolve()
        try:
            listed = (
                {str(Path(t["path"]).resolve()): t for t in await gitops.worktree_list(root)}
                if (root / ".git").exists()
                else {}
            )
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
            if u.get("why") == "finished":
                done = await worktrees.remove_if_finished(cwd, u["name"], u, self.config.data_dir)
                if done.get("removed"):
                    continue
            u["worktree"] = {
                "path": str(where),
                "branch": found.get("branch") or "",
                "prepare": worktrees.read_prepare(where),
            }
