"""The board: every unit of a workspace with its stage, what is running on it and its worktree.

Split from `coscc/service.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from coscc import agents, backlog
from coscc import board as board_reader
from coscc import gitops
from coscc import precedent as precedent_mod
from coscc.board import Unavailable
from coscc.data import now as _now
from coscc.gitops import GitError
from coscc.journal import Busy, Journal, last_runs, timelines_of, totals_of
from coscc.policy import grant_for
from coscc import steps as steps_mod
from coscc import units, worktrees
from coscc.units import BadUnit
from coscc.service_common import (
    CONSEQUENCE,
    Invalid,
    _younger_than,
    attention_reason,
    consequence,
    outcome_label,
    unit_state,
)


# `0051` spec, answer 4: an `ended, unknown` row stops being shown this long after it began,
# unless a later `start` of the same unit retired it first.
UNKNOWN_END_FOR = timedelta(hours=24)


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


def _attach_precedent(units_: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    """`0044` R10. On each question: `by_jera`, its `cites` and the words it `said` when the
    answer in force is Jera's, and — while it is still unanswered — `needs_person`,
    `proposal` and `reason` from the last `precedent` row for it. Display only: `cos.mjs` never sees any of this (R9)."""
    last: dict[tuple[str, str, Any], dict[str, Any]] = {}
    for r in rows:
        last[(str(r.get("unit") or ""), str(r.get("artifact") or ""), r.get("n"))] = r
    for unit in units_:
        answers = {(a["artifact"], a["n"]): a for a in unit.get("answers") or []}
        for q in unit.get("questions") or []:
            jera = bool(q.get("answered")) and precedent_mod.is_jera(q.get("by"))
            said = answers.get((q.get("artifact"), q.get("n"))) or {}
            row = last.get((unit["name"], str(q.get("artifact") or ""), q.get("n"))) or {}
            waiting = not q.get("answered") and row.get("verdict") == precedent_mod.PERSON
            q["by_jera"] = jera
            q["cites"] = precedent_mod.cites_of(str(said.get("text") or "")) if jera else []
            q["said"] = precedent_mod.words_of(str(said.get("text") or "")) if jera else ""
            q["needs_person"] = waiting
            q["proposal"] = str(row.get("text") or "") if waiting else ""
            q["reason"] = str(row.get("reason") or "") if waiting else ""


def answerable(unit: dict[str, Any]) -> bool:
    """`0082` R11. Whether the board invites an answer on this unit: not once it is finished,
    closed or dropped. The answer route itself is unchanged."""
    action = str(unit.get("next") or "")
    dropped = (unit.get("hold") or {}).get("state") == "dropped"
    return not (action == "finished" or action.startswith("closed") or dropped)


class BoardMixin:

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

    # `0050`. Check-and-mark with no `await` in any of these, so nothing on the event loop
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
        """`0094` R13: the running build's version and commit, for a step's `start` row.

        `Updater.me` is `update.identity`, computed once and kept. Anything failing is two
        empty strings, which `verify_0094 --measure` counts apart; it never stops a step.
        """
        try:
            me = self.updater.me()
            return {"version": str(me.get("version") or ""), "commit": str(me.get("commit") or "")}
        except Exception:  # noqa: BLE001 — a record field, never a reason to refuse a step
            return {"version": "", "commit": ""}

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
        ranking: list[dict[str, Any]] = []
        if journal is not None:
            try:
                modes = journal.modes(key)
                # One read for every unit's cost, comment attempts (`0021` D4) and, since
                # `0074`, the backlog's records. Asking `totals` per unit re-scanned the
                # working folder N times for the rows this already has.
                rows = journal.records(key)
            except Busy as e:
                raise Invalid(str(e)) from e
            timelines = timelines_of(rows)
            comments = [r for r in rows if r.get("kind") == "pr-comment"]
            ranking = [r for r in rows if r.get("kind") in backlog.KINDS]
            verdicts = [r for r in rows if r.get("kind") == "precedent"]
        else:
            verdicts = []
        _attach_comment_state(data["units"], comments)
        _attach_precedent(data["units"], verdicts)
        # `0074`. Display only: nothing below reads it, and `next`/`blocked` are untouched.
        data["backlog"] = {
            **backlog.fold(
                data["units"], ranking, backlog.measured(timelines, data["units"]),
                backlog.undetermined(timelines, data["units"]),
            ),
            "propose_warning": grant_for("estimate").warning,
            "propose_consequence": CONSEQUENCE["estimate"],
        }
        per_unit = data["backlog"].pop("per_unit")
        for unit in data["units"]:
            unit["backlog"] = per_unit.get(unit["name"]) or {
                "rank": None, "value": None, "effort": None, "effort_source": None, "relations": [],
            }

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
                row["consequence"] = consequence(row["stage"])
                # `0019` plan step 6 / `spec.md` R5. From the same `timelines` read above —
                # no second scan of the run log. `status` (and the lanes) stays read from
                # the artifact alone (C6); this is a second, separate field.
                row["last_run"] = unit_last_runs.get(row["stage"])
            unit["cost"] = (
                totals_of(timelines.get(unit["name"], [])) if journal is not None else {}
            )
            # `0047` R8, R9. A label and nothing else: a deadline passing writes no row and
            # starts no step.
            unit["outcome_label"] = outcome_label(
                unit.get("outcome"), date.today(), finished=unit.get("next") == "finished"
            )
            # `0082` R11, R12. Decided here so the page only shows them.
            unit["answerable"] = answerable(unit)
            unit["attention_reason"] = attention_reason(unit)

        await self._attach_worktrees(cwd, data["units"])
        asks = await self._attach_integration(cwd, data["units"], journal, key)
        for unit in data["units"]:
            # `0100` R3. From the timelines read above: no second scan of the run log.
            ended = [r for r in timelines.get(unit["name"], []) if r.get("ended") is not None]
            unit["state"] = unit_state(unit, ended[-1] if ended else None, unit.pop("ci_held", None))

        data["recording"] = journal is not None
        # `0043` R9. Display only: the page shows it and decides nothing from it.
        data["autopilot"] = self._autopilot_block(key)
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
        # `0100` R6. Started last and never awaited: their answers count from the next read.
        self._ask_ci(asks)
        return data

    def _mark_running(self, key: str, unit: str, stage: str, kind: str) -> str:
        """`0051` R1. Put one entry in `_running` and return its id, for the `finally` to pop.

        `started` is stamped by the same clock `Journal.append` uses, so it reads like an
        `at`. `turns` and `cost_usd` stay `None` while the session runs (R5).
        """
        rid = uuid.uuid4().hex
        self._running[rid] = {
            "workspace": key, "unit": unit, "stage": stage, "started": _now(),
            "kind": kind, "turns": None, "cost_usd": None,
        }
        return rid

    def running(self, cwd: str) -> dict[str, Any]:
        """`0051` R2. What has an agent working in this workspace now, and what ended unseen.

        `running` is `_running` for this workspace, one element per entry, by unit.
        `unknown_end` is every `start` the run log holds without an `end` that no entry
        accounts for (R6): the unit has nothing running here, no later `start` of the unit
        retired it, and it is younger than `UNKNOWN_END_FOR` (spec, answer 4). Matched by
        unit, not by session: `_active` allows one per unit per process, so a unit with an
        entry has no other `start` open in this process — only one another process wrote,
        and that one is shown as ended (spec C2, answer 3).

        Reads memory and the run log, nothing else: no `git`, no `gh`, no `cos.mjs`, and
        writes nothing. A busy run log is a `note`, not a refusal — the board asks this
        every few seconds, and a lock someone else holds must not break the board.
        """
        self._workspace_or_refuse(cwd)
        key = self._journal_key(cwd)
        running: dict[str, list[dict[str, Any]]] = {}
        for entry in self._running.values():
            if entry["workspace"] != key:
                continue
            kind = entry["kind"]
            agent = None if kind == "rebase" else agents.agent_for(entry["stage"])
            running.setdefault(entry["unit"], []).append({
                "kind": kind, "stage": entry["stage"], "agent": agent,
                "started": entry["started"], "turns": entry["turns"], "cost_usd": entry["cost_usd"],
                # `0073` R1. A board step's events; `""` for an integration or an estimate.
                "run": entry.get("run", ""),
            })
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
                {"stage": r["stage"], "started": r["started"]}
                for r in found["open"]
                if r.get("started") and r["started"] == found["last_start"]
                and _younger_than(r["started"], oldest)
            ]
            if rows:
                out["unknown_end"][unit] = rows
        return out

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
