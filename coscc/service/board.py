"""The board: every unit of a workspace with its stage, what is running on it and its worktree.

A mixin with no fields, which `Service` inherits.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from coscc.units import backlog, prose_import
from coscc.agent import agents
from coscc.units import board as board_reader
from coscc.git import gitops
from coscc.units.board import Unavailable
from coscc.data import Data
from coscc.git.gitops import GitError
from coscc.runlog.journal import last_runs, timelines_of, totals_of
from coscc.data import Busy
from coscc.agent.policy import grant_for
from coscc import units
from coscc.units import worktrees
from coscc.units import BadUnit
from coscc.service.common import (
    open_prs_once,
    CONSEQUENCE,
    Invalid,
    _younger_than,
    attention_reason,
    consequence,
    outcome_label,
    unit_state,
)

log = logging.getLogger(__name__)


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

    def _app_identity(self) -> dict[str, str]:
        """The running build's version and commit, for a step's `start` row.

        `Updater.me` is `update.identity`, computed once and kept. Anything failing is two
        empty strings; it never stops a step.
        """
        try:
            me = self.updater.me()
            return {"version": str(me.get("version") or ""), "commit": str(me.get("commit") or "")}
        except Exception:
            # A record field, never a reason to refuse a step.
            log.exception("the version of the app could not be read")
            return {"version": "", "commit": ""}

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
                json.dumps(self.ws.snapshot(cwd, [unit]), ensure_ascii=False), encoding="utf-8"
            )
        except OSError, Invalid:
            return ""
        return str(path)

    async def _import_rounds(self, cwd: str, units_: list[dict[str, Any]]) -> None:
        """The review rounds only the prose of a store holds, into `cos.db`, on the
        first board read that finds the store unimported (`coscc/units/prose_import.py`). The
        board this read shows is the same either way. One that cannot write goes to the log and
        is tried on the next read."""
        meta = self.ws.unit_meta()
        key = self.ws.key(cwd)
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
            log.warning("the review rounds of %s could not be imported: %s", key, e)

    async def board(self, cwd: str) -> dict[str, Any]:
        """Every unit in this workspace, each with its eight stages, modes and cost.

        The status of a stage comes from the artifact and the mode comes from the journal,
        and they are joined here rather than stored together. Storing them together is how
        a board starts disagreeing with the files it claims to describe.
        """
        self.ws.check(cwd)
        peers, peer_problems = self.ws.peer_table()
        try:
            data = await board_reader.read(
                self.ws.units_root(cwd), state=self.ws.snapshot(cwd, peers=peers)
            )
        except Unavailable as e:
            raise Invalid(str(e)) from e
        await self._import_rounds(cwd, data["units"])
        # Only when there is something to say.
        if peer_problems:
            data["peer_problems"] = peer_problems
        # Each stage column's agent, by the one lookup, for the page to show only.
        # A stage the table has no row for is left out, and its column has no glyph.
        overrides = self.agents.agent_overrides()[0]
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
        name = self.ws.name(cwd)
        for unit in data["units"]:
            if unit.get("repo") and name and unit["repo"] != name:
                unit["problems"] = [
                    *unit["problems"],
                    f"Repo: {unit['repo']} is not this workspace, {name}.",
                ]
            unit["waits_for"] = waits_for(unit)

        journal = self.ws.journal()
        key = self.ws.key(cwd)
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
        prs = open_prs_once(cwd)
        asks = await self._attach_integration(cwd, data["units"], journal, key, prs)
        data["release"] = await self.release.attach_release(cwd, data["units"], journal, key, prs)
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
                "store": str(self.ws.units_root(cwd)),
                "host": units.key(cwd),
                "host_units": units.host_unit_count(cwd),
            }
        # Started last and never awaited: their answers count from the next read.
        self._ask_ci(asks)
        return data

    def _running_here(self, key: str, overrides: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """`running`'s `running`, from memory alone: what `_guide_block` reads too."""
        running: dict[str, list[dict[str, Any]]] = {}
        for entry in self.holds.running.values():
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

        `running` is `holds.running` for this workspace, one element per entry, by unit.
        `unknown_end` is every `start` the run log holds without an `end` that no entry
        accounts for: the unit has nothing running here, no later `start` of the unit
        retired it, and it is younger than `UNKNOWN_END_FOR`. Matched by
        unit, not by session: `holds.marks` allows one per unit per process, so a unit with an
        entry has no other `start` open in this process — only one another process wrote,
        and that one is shown as ended.

        Reads memory and the run log, nothing else: no `git`, no `gh`, no `cos.mjs`, and
        writes nothing. A busy run log is a `note`, not a refusal — the board asks this
        every few seconds, and a lock someone else holds must not break the board.
        """
        self.ws.check(cwd)
        key = self.ws.key(cwd)
        # Through the one lookup, so an override shows here too. Read once per call.
        overrides = self.agents.agent_overrides()[0]
        running = self._running_here(key, overrides)
        out: dict[str, Any] = {"running": running, "unknown_end": {}}
        journal = self.ws.journal()
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
