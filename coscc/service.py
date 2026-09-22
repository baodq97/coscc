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

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from coscc import board as board_reader
from coscc import gitops
from coscc import sessions as reader
from coscc.board import Unavailable
from coscc.config import Config
from coscc.data import Data
from coscc.gitops import GitError
from coscc.history import History, settled_edits
from coscc.journal import (
    COST_FIELDS,
    COST_USD,
    BadRecord,
    Busy,
    Journal,
    add_cost,
    totals_of,
    zero_cost,
)
from coscc.policy import GRANTS, PROSE_STAGES, grant_for
from coscc.runner import RunError, Runner, unit_dir
from coscc.sessions import Sessions
from coscc.store import BadName, Store, require_name

# The eight stage names, in stage order. Taken from the stage list the board reports rather
# than written again here would be better; the board read is async and this method is not,
# so the names are repeated and this comment is the warning.
STAGE_FILES = ("idea", "intent", "spec", "plan", "impl", "pr", "review", "ship")


class Invalid(Exception):
    """A request this layer refuses, carrying a reason a caller can show verbatim."""


@dataclass
class Service:
    config: Config
    sessions: Sessions
    store: Store | None = field(default=None, init=False)

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

    async def board(self, cwd: str) -> dict[str, Any]:
        """Every unit in this workspace, each with its eight stages, modes and cost.

        The status of a stage comes from the artifact and the mode comes from the journal,
        and they are joined here rather than stored together. Storing them together is how
        a board starts disagreeing with the files it claims to describe.
        """
        self._workspace_or_refuse(cwd)
        try:
            data = await board_reader.read(cwd)
        except Unavailable as e:
            raise Invalid(str(e)) from e

        journal = self._journal()
        key = self._journal_key(cwd)
        modes: dict[tuple[str, str], str] = {}
        timelines: dict[str, list[dict[str, Any]]] = {}
        if journal is not None:
            try:
                modes = journal.modes(key)
                # One read for every unit's cost. Asking `totals` per unit re-scanned the
                # working folder N times for the rows this already has.
                timelines = journal.timelines(key)
            except Busy as e:
                raise Invalid(str(e)) from e

        for unit in data["units"]:
            for row in unit["stages"]:
                # `manual` is the default because starting work is a decision someone has
                # to make, not one an unset value should make for them.
                row["mode"] = modes.get((unit["name"], row["stage"]), "manual")
                grant = grant_for(row["stage"], row["mode"])
                # Carried to the page so `spec.md` C4 can be met where the button is: what
                # a step will be allowed to do has to be readable before it is started.
                row["grants"] = list(grant.tools)
                row["warning"] = grant.warning
            unit["cost"] = (
                totals_of(timelines.get(unit["name"], [])) if journal is not None else {}
            )

        data["recording"] = journal is not None
        data["read_only_because"] = (
            None if journal is not None
            else "no working folder is set, so nothing can be recorded — set COS_WORKING_DIR"
        )
        return data

    async def set_mode(self, cwd: str, unit: str, stage: str, mode: str) -> dict[str, Any]:
        """Choose how one step runs. Validated against the board, not against a second list."""
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a mode cannot be recorded — set COS_WORKING_DIR"
            )

        try:
            data = await board_reader.read(cwd)
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
            data = await board_reader.read(cwd)
        except Unavailable as e:
            raise Invalid(str(e)) from e

        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Invalid(f"no such work unit in this workspace: {unit}")
        row = next((r for r in found["stages"] if r["stage"] == stage), None)
        if row is None:
            raise Invalid(f"no such stage: {stage} (use one of {', '.join(data['stages'])})")

        key = self._journal_key(cwd)
        mode = journal.modes(key).get((unit, stage), "manual")
        runner = Runner(self.sessions, journal)
        try:
            async for item in runner.run(
                workspace=cwd,
                journal_key=key,
                unit=unit,
                stage=stage,
                artifact=row["file"],
                stages=list(data["stages"]),
                mode=mode,
            ):
                yield item
        except RunError as e:
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
        async for item in self.sessions.stream(cwd, text, session_id):
            yield item

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
        """
        c = self.config
        return {
            "working_dir": c.working_dir,
            "data_dir": str(Data(c.data_dir).root),
            "host": c.host,
            "port": c.port,
            "model": c.model,
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
                    "mode": mode,
                    "tools": ", ".join(grant.tools) or "none",
                    "commands": ", ".join(grant.commands) or "none",
                    "max_turns": grant.max_turns,
                    "max_budget_usd": grant.max_budget_usd,
                    "app_writes_artifact": grant.app_writes_artifact,
                    "warning": grant.warning,
                }
                for (stage, mode), grant in sorted(GRANTS.items())
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
        try:
            path = unit_dir(cwd, unit) / filename
        except RunError as e:
            raise Invalid(str(e)) from e
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
