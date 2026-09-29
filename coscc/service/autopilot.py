"""The autopilot: the loop that asks `cos.mjs next` and starts the stages it names.

A mixin with no fields, inherited by `Service` (`coscc/service/__init__.py`).
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from coscc.units import autopilot, backlog, guide, states
from coscc.agent import precedent
from coscc.git import fetches
from coscc.github import integrate, prmachine
from coscc.git.gitops import GitError
from coscc.runlog.journal import BadRecord, Busy
from coscc.service.common import BRANCH_REMOTE, BRANCH_TRUNK, Invalid


class AutopilotMixin:

    # The autopilot holds no rule of the loop. Each pass reads the board, the run log, the settings
    # and the workspace's last shortlist, and asks `next` for every unit on it and no other; with no
    # shortlist it asks nothing. `coscc/units/autopilot.py` decides where to stop and what to start;
    # what it starts goes through `run_step` and `integrate`, which ask the gate themselves. A
    # refusal from them is a stop line, never a second way past the gate.

    def autopilot_start(self, cwd: str) -> None:
        """Run a pass now and every `POLL_SECONDS` after, for as long as the switch is on. A second
        call for a workspace already running does nothing.
        """
        key = self._journal_key(cwd)
        self._autopilot_cwd[key] = cwd
        task = self._autopilot_tasks.get(key)
        if task is not None and not task.done():
            return
        self._autopilot_tasks[key] = asyncio.get_running_loop().create_task(self._autopilot_loop(key))
        # The reader of the workspace's pull requests lives and dies with it.
        self._pr_readers[key] = asyncio.get_running_loop().create_task(self._pr_reader_loop(key))

    def autopilot_stop(self, key: str) -> None:
        """Turned off: no more passes and no more `gh` calls for it. A step it already started runs on
        to its end, as a person's would.
        """
        task = self._autopilot_tasks.pop(key, None)
        if task is not None:
            task.cancel()
        reader = self._pr_readers.pop(key, None)
        if reader is not None:
            reader.cancel()
        self._autopilot_stops.pop(key, None)
        self._autopilot_held.pop(key, None)

    def autopilot_resume(self) -> list[str]:
        """At start-up, every workspace whose switch is on starts again. Returns them."""
        started = []
        for row in self.workspaces()["workspaces"]:
            if row["missing"]:
                continue
            if self._autopilot_values(self._journal_key(row["path"]))["autopilot"]:
                self.autopilot_start(row["path"])
                started.append(row["path"])
        return started

    def _autopilot_on(self, key: str) -> bool:
        task = self._autopilot_tasks.get(key)
        return task is not None and not task.done()

    def _autopilot_nudge(self, key: str, woken_by: list[dict[str, Any]] | None = None) -> None:
        """A step or an integration ended, or an answer was written. One pass is scheduled and not
        waited for; nothing happens when the switch is off. `woken_by`: the transitions of the PR
        machine that scheduled it, which its picks record.
        """
        if not self._autopilot_on(key):
            return
        task = asyncio.get_running_loop().create_task(self._autopilot_guarded(key, woken_by))
        self._autopilot_pending.add(task)
        task.add_done_callback(self._autopilot_pending.discard)

    async def _autopilot_loop(self, key: str) -> None:
        while True:
            await self._autopilot_guarded(key)
            await asyncio.sleep(autopilot.POLL_SECONDS)

    async def _pr_reader_loop(self, key: str) -> None:
        """Every `ci_poll_seconds` of the lane config, one read of the workspace's pull requests. The
        300-second pass goes on beside it as the net.
        """
        poll = states.default_lanes().ci_poll_seconds
        while True:
            await asyncio.sleep(poll)
            try:
                await self._pr_read(key)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — the next read tries again; the pass is the net
                print(f"coscc: the pull request reader of {key} failed: {e}", file=sys.stderr)

    async def _pr_read(self, key: str) -> prmachine.Read:
        """One read of the workspace's pull requests. Whatever it recorded schedules one pass for this
        workspace, however many transitions that was; a merge also schedules one for every other
        workspace the autopilot is on in, where a unit may depend on it.
        """
        cwd = self._autopilot_cwd.get(key)
        if cwd is None or not self._autopilot_on(key):
            return prmachine.Read()
        root = Path(cwd).expanduser().resolve()

        def directory_of(unit: str) -> Path:
            try:
                return self._unit_dir(cwd, unit)
            except Invalid:
                return self._units_root(cwd) / unit

        got = await self._pr_machine().read(str(root), key, directory_of)
        if not got.moved:
            return got
        merged = [c for c in got.causes if c["transition"] == "merged"]
        # A merge made on GitHub is followed by no `ship` step, so its `ship` row and cleanup
        # are the reader's, before the pass it schedules.
        for c in merged:
            await self._shipped(cwd, key, c["unit"], "shipped")
        self._autopilot_nudge(key, got.causes)
        if merged:
            for other in list(self._autopilot_tasks):
                if other != key:
                    self._autopilot_nudge(other, merged)
        return got

    async def _autopilot_guarded(self, key: str, woken_by: list[dict[str, Any]] | None = None) -> None:
        """A pass that raises leaves a stop line saying so, not a dead loop."""
        try:
            await (self._autopilot_pass(key, woken_by) if woken_by else self._autopilot_pass(key))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — shown on the board, never swallowed
            self._autopilot_set_stops(key, {"": {"unit": "", "kind": "f", "reason": f"the autopilot's pass failed: {e}"}})

    def _autopilot_running(self, key: str) -> list[dict[str, Any]]:
        """What runs in this workspace now, by unit, a person's steps included."""
        out: dict[str, dict[str, Any]] = {}
        for (k, unit), mark in self._active.items():
            if k == key:
                out[unit] = {"unit": unit, "stage": "integrate" if mark.kind == "integrate" else mark.stage}
        for unit, (stage, task) in (self._autopilot_runs.get(key) or {}).items():
            if not task.done() and unit not in out:
                out[unit] = {"unit": unit, "stage": stage}
        return list(out.values())

    def _autopilot_files(self, cwd: str, unit: str) -> set[str] | None:
        try:
            return autopilot.files_of((self._unit_dir(cwd, unit) / "plan.md").read_text(encoding="utf-8"))
        except (Invalid, OSError):
            return None

    def _autopilot_cap(self, records: list[dict[str, Any]], limit: float) -> dict[str, Any]:
        """The figures for a pass and for the board: every workspace, every starter."""
        now = datetime.now().astimezone()
        spent = autopilot.spent_today(records, now)
        active = {
            (k, unit): "integrate" if mark.kind == "integrate" else mark.stage
            for (k, unit), mark in self._active.items()
        }
        # A launch holds no mark until `run_step` or `integrate` takes one (`integrate` only after its
        # fetch and `gh` reads) and is counted from the moment it was chosen.
        for k, runs in self._autopilot_runs.items():
            for unit, (stage, task) in runs.items():
                if not task.done():
                    active.setdefault((k, unit), stage)
        running = autopilot.reserved(records, now, [(k, unit, stage) for (k, unit), stage in active.items()])
        return {
            "limit": limit, "spent": round(spent["known"] + spent["estimated"], 2),
            "known": round(spent["known"], 2), "estimated": round(spent["estimated"], 2),
            "estimated_count": spent["estimated_count"], "running": round(running, 2),
            "day": autopilot.today(now),
        }

    def _autopilot_set_stops(
        self, key: str, found: dict[str, dict[str, str]], asked: set[str] | None = None,
    ) -> None:
        """Keep the stops a pass found, and log each unit's that changed.

        `asked`: the units this pass looked at, when it did not look at all of them; the others keep
        what they had, the workspace's own stop among them. The log is an `autopilot-stop` record per
        change, `stop` empty once it cleared, which tells a person's press at a stop from one outside
        them. The workspace's own stop, unit `""`, is logged the same way, so a notice can say it.
        """
        before = self._autopilot_stops.get(key, {})
        if asked is None:
            after = dict(found)
        else:
            after = {**{u: s for u, s in before.items() if u not in asked}, **found}
        self._autopilot_stops[key] = after
        journal = self._journal()
        if journal is None:
            return
        for unit in sorted(set(before) | set(after)):
            old, new = before.get(unit), after.get(unit)
            if (old or {}).get("kind") == (new or {}).get("kind"):
                continue
            try:
                journal.append({
                    "kind": "autopilot-stop", "workspace": key, "unit": unit, "stage": "",
                    "stop": (new or {}).get("kind", ""), "reason": (new or {}).get("reason", ""),
                })
            except (BadRecord, Busy):
                pass

    async def _autopilot_pass(self, key: str, woken_by: list[dict[str, Any]] | None = None) -> None:
        """One look at a workspace: follow its shortlist, find each listed unit's stop or why it waits,
        then start what may start, highest first, each after its record.
        """
        cwd = self._autopilot_cwd.get(key)
        if cwd is None or not self._autopilot_on(key):
            return
        lock = self._autopilot_locks.setdefault(key, asyncio.Lock())
        async with lock:
            settings = self._autopilot_values(key)
            if not settings["autopilot"]:
                return
            refused = self._off_loopback()
            if refused:
                # `COS_HOST` can change after the switch was turned on.
                self._autopilot_set_stops(key, {"": {"unit": "", "kind": "f", "reason": refused}})
                return
            journal = self._journal()
            if journal is None:
                return
            data = await self.board(cwd)
            try:
                records = journal.records(
                    kinds=("start", "end", "integration", "shortlist", "answer", "screens", "precedent",
                           autopilot.PR_MACHINE),
                )
            except Busy as e:
                self._autopilot_set_stops(key, {"": {"unit": "", "kind": "f", "reason": str(e)}})
                return
            # The last well-formed shortlist, read again on every pass.
            listed, n = backlog.shortlist_of(r for r in records if r.get("workspace") == key)
            if listed is None or not listed["units"]:
                # Nothing is asked and nothing starts.
                if not self._autopilot_on(key) or not self._autopilot_values(key)["autopilot"]:
                    return
                self._autopilot_set_stops(key, {"": {"unit": "", "kind": "shortlist", "reason": autopilot.NO_SHORTLIST}})
                return
            names = list(listed["units"])
            # A listed unit at `ship` is decided on the `origin/main` the remote has now: `behind` below
            # and the `ship` gate `next` asks both read that ref, and neither fetches. Through the
            # coordinator, which reuses a fetch under `REUSE_SECONDS`. A fetch that fails leaves the ref as
            # it was, and each such unit's stop says so.
            at_ship = {
                u["name"] for u in data["units"]
                if u["name"] in names and u.get("between_pr_and_ship") and u.get("at") == "ship"
            }
            unfetched: dict[str, Any] | None = None
            if at_ship:
                try:
                    await fetches.fetch(Path(cwd).expanduser().resolve(), BRANCH_REMOTE, BRANCH_TRUNK)
                except GitError as e:
                    unfetched = {"outcome": "failed", "detail": str(e)}
                else:
                    data = await self.board(cwd)
            last: dict[str, dict[str, Any]] = {}
            integrations: dict[str, dict[str, Any]] = {}
            # The `start` of the step each unit's last `end` closed, the latest of that unit and stage
            # before it, so a recording `ship` that ran out is told apart.
            starts: dict[tuple[str, str], dict[str, Any]] = {}
            began: dict[str, dict[str, Any] | None] = {}
            for r in records:
                if r.get("workspace") == key and r.get("kind") == "start" and autopilot.is_step(r):
                    starts[(str(r.get("unit") or ""), str(r.get("stage") or ""))] = r
                # A retake of the screenshots that failed is the unit's last word too, and so is a `pr` or
                # `ship` the PR machine ran.
                if r.get("workspace") == key and r.get("kind") in ("end", "integration", "screens", autopilot.PR_MACHINE) \
                        and autopilot.is_step(r):
                    last[str(r.get("unit") or "")] = r
                    if r.get("kind") == "integration":
                        integrations[str(r.get("unit") or "")] = r
                    if r.get("kind") == "end":
                        began[str(r.get("unit") or "")] = starts.get((str(r.get("unit") or ""), str(r.get("stage") or "")))

            running = self._autopilot_running(key)
            here = {r["unit"]: r["stage"] for r in running}
            board = {u["name"]: u for u in data["units"]}
            found: dict[str, dict[str, str]] = {}
            candidates: list[dict[str, Any]] = []
            # Why each unit that is no candidate waits, for the units ranked below it.
            reasons: dict[str, tuple[str, str]] = {}
            # Every unit on the shortlist is asked, and no other.
            for rank, name in enumerate(names, 1):
                u = board.get(name)
                if u is None:
                    reasons[name] = ("missing", "")
                    continue
                try:
                    nxt = await self.next_step(cwd, name)
                except Invalid as e:
                    found[name] = {"unit": name, "kind": "f", "reason": str(e)}
                    reasons[name] = ("running", here[name]) if name in here else autopilot.reason_for({}, "", found[name])
                    continue
                # A first `exhausted` step of a stage other than `ship` runs again once; so does a first prose
                # step whose reply lacked its opening.
                last_stage = str((last.get(name) or {}).get("stage") or "")
                ran_out = autopilot.exhausted_of(records, key, name, last_stage)
                unopened = autopilot.unopened_of(records, key, name, last_stage)
                # No `start` found reads as no recording `ship`.
                recorded = (began.get(name) or {}).get("ship_mode") == "record"
                stop = autopilot.stop_for(
                    u, nxt, last.get(name), settings["autopilot_may_ship"], ran_out, unopened, recorded,
                )
                stage = nxt.get("stage") or ""
                info = u.get("integration") or {}
                # Not while its step runs, whose `start` is already in the window.
                own = None if name in here else autopilot.after_own_integration(
                    info, integrations.get(name), autopilot.since_integration(records, key, name), nxt,
                    autopilot.exhausted_of(records, key, name, "impl"),
                )
                # A unit behind `main`, conflicting or red after integration is integrated first, also after a
                # `pass`, but only where the autopilot may ship (otherwise a person merges and the round is
                # theirs: GitHub would refuse the merge). A rebase that leaves the unit's patch unchanged opens
                # `ship` again once CI is green; one that changes it closes `ship` until a new round passes.
                # CI red after the autopilot's own integration runs `impl` once if `next` names it, and is not
                # integrated again, nor once `main` has moved on or the pull request conflicts, which would open
                # a new window.
                rounds = u.get("rounds") or []
                passed = bool(rounds) and rounds[-1].get("verdict") == "pass"
                if (
                    info.get("state") in integrate.BUTTON_STATES
                    and (not passed or settings["autopilot_may_ship"])
                    and (stop is None or stop["kind"] == "f")
                ):
                    if own is not None and (info.get("state") == "red-after-integration" or own[1] is not None):
                        stage, stop = own
                    else:
                        stop, stage = None, "integrate"
                # Once the `impl` pushed: the board no longer reads the head as the integrated one, and only
                # `next`'s words say CI is still red.
                if stop is None and stage == "impl" and own is not None and own[1] is not None:
                    stage, stop = own
                # A draft whose questions are all answered runs again, at most `MAX_RERUNS` times, and only on
                # an answer given since its last run; with none, it is a stop. Before `reason`, which raises on
                # no stage and no stop.
                rerun = False
                if stop is None and not stage and nxt.get("rerun"):
                    if autopilot.reruns_of(records, key, name, nxt["rerun"]) >= autopilot.MAX_RERUNS:
                        artifact = next((s["file"] for s in u.get("stages") or [] if s["stage"] == nxt["rerun"]), nxt["rerun"])
                        stop = autopilot.rerun_stop(artifact)
                    elif not autopilot.answered_since_start(records, key, name, nxt["rerun"]):
                        stop = autopilot.stop_for(
                            u, {**nxt, "rerun": ""}, last.get(name), settings["autopilot_may_ship"], ran_out,
                            unopened, recorded,
                        )
                    else:
                        stage, rerun = nxt["rerun"], True
                # Open questions Jera was not asked are its to try first, at the ceiling of the prompt it will
                # be given. After the integrate branch, which never takes that stop, and the rerun branch, which
                # never makes one.
                need = None
                if stop is not None and stop["kind"] == "a":
                    unasked = autopilot.unasked(u, records, key)
                    if here.get(name) == autopilot.JERA:
                        # Its `start` already counts the questions as asked; no stop yet.
                        stop, stage = None, ""
                    elif not unasked:
                        stop = autopilot.waiting_for_you(autopilot.open_questions(u))
                    else:
                        _, _, prompt = self._precedent_prompt(data["units"], u, name, unasked, cwd=cwd)
                        need = precedent.ceiling(len(prompt))
                        if need > precedent.PRECEDENT_MAX_USD:
                            stop = {"kind": "a", "reason": autopilot.STORE_PAST_CEILING}
                        else:
                            stop, stage = None, autopilot.JERA
                if stop is not None and unfetched is not None and name in at_ship:
                    note = integrate.origin_note(str(info.get("origin_sha") or ""), unfetched)
                    stop = {**stop, "reason": f"{stop['reason']}; {note}"}
                reason = ("running", here[name]) if name in here else autopilot.reason_for(nxt, stage, stop)
                if reason is not None:
                    reasons[name] = reason
                if stop is not None:
                    found[name] = {"unit": name, **stop}
                    continue
                if not stage:
                    continue
                files = self._autopilot_files(cwd, name) if stage in autopilot.CODE_STAGES else None
                # The exhausted `ship` this pick went past. Not once the stage became `integrate`, which
                # skipped nothing.
                skipped = stage == "ship" and autopilot.skips_exhausted(nxt, last.get(name), recorded)
                candidates.append({
                    "unit": name, "stage": stage, "files": files, "rank": rank,
                    "need": need if stage == autopilot.JERA else autopilot.reservation(stage),
                    "rerun": rerun,
                    "past_exhausted": {"at": last[name].get("at")} if skipped else None,
                })

            for r in running:
                if r["stage"] in autopilot.CODE_STAGES:
                    r["files"] = self._autopilot_files(cwd, r["unit"])
            now = datetime.now().astimezone()
            # A `start` with no `end`, from a process before this one, counts against N for 24 hours.
            elsewhere = sum(1 for (k, unit) in autopilot.open_starts(records, now) if k == key and unit not in here)
            cap = self._autopilot_cap(records, settings["daily_cap_usd"])
            room = cap["limit"] - cap["spent"] - cap["running"]
            # The pull requests the PR machine holds open, with the files it read.
            try:
                prs = prmachine.open_prs(self._pr_machine().history, key)
            except (sqlite3.Error, OSError, Busy):
                prs = []
            picked = autopilot.pick(candidates, running, settings["max_parallel"] - elsewhere, room, prs)
            reasons.update(picked["held"])
            est = f" ({cap['estimated']:.2f} estimated)" if cap["estimated_count"] else ""
            for c in picked["capped"]:
                found[c["unit"]] = {"unit": c["unit"], "kind": "cap", "reason": (
                    f"spent {cap['spent']:.2f}{est} + running {cap['running']:.2f} + {c['stage']} "
                    f"{c['need']:.2f} is over the cap of {cap['limit']:.2f} USD ({cap['day']})"
                )}
                reasons[c["unit"]] = autopilot.reason_for({}, c["stage"], found[c["unit"]])
            # A run again that `max_parallel` alone held back says so. Any other candidate held back that
            # way still says nothing.
            left = {c["unit"] for c in picked["chosen"] + picked["capped"]} | set(picked["held"])
            for c in candidates:
                if c["rerun"] and c["unit"] not in left:
                    found[c["unit"]] = {"unit": c["unit"], **autopilot.full_stop(c["stage"], settings["max_parallel"])}
                    reasons[c["unit"]] = autopilot.reason_for({}, c["stage"], found[c["unit"]])
            # Raises before anything is recorded or started when a unit above one chosen has no
            # reason; `_autopilot_guarded` shows it as a stop line.
            passed = autopilot.passed_for(names, [c["unit"] for c in picked["chosen"]], reasons)
            # The switch may have been turned off while this pass read the board and `next`. Nothing from
            # here on awaits, so nothing starts once it is off.
            if not self._autopilot_on(key) or not self._autopilot_values(key)["autopilot"]:
                return
            self._autopilot_set_stops(key, found)
            self._autopilot_held[key] = dict(picked["held"])
            run_id = uuid.uuid4().hex
            shortlist = {"n": n, "at": listed.get("at"), "units": names}
            for c, over in zip(picked["chosen"], passed):
                # No record, no start, and nothing ranked below it either, since starting one would pass over
                # a unit chosen with no record of it.
                try:
                    journal.append({
                        "kind": "autopilot-pick", "workspace": key, "unit": c["unit"], "stage": c["stage"],
                        "pass": run_id, "rank": c["rank"], "shortlist": shortlist, "passed": over,
                        **({"past_exhausted": c["past_exhausted"]} if c.get("past_exhausted") else {}),
                        # The transitions whose read scheduled this pass.
                        **({"woken_by": woken_by} if woken_by else {}),
                    })
                except (BadRecord, Busy) as e:
                    self._autopilot_set_stops(key, {**found, "": {
                        "unit": "", "kind": "f",
                        "reason": f"could not record the autopilot's choice, so nothing more was started: {e}",
                    }})
                    return
                task = asyncio.get_running_loop().create_task(self._autopilot_launch(key, cwd, c["unit"], c["stage"]))
                self._autopilot_runs.setdefault(key, {})[c["unit"]] = (c["stage"], task)

    async def _autopilot_launch(self, key: str, cwd: str, unit: str, stage: str) -> None:
        """Start one step, integration or Jera session and read it to its end, since no client will.

        A refusal is a stop line with the gate's words, unless it is CI still running, or the unit
        taken by someone else in the meantime, which are not stops.
        """
        try:
            # Turned off between the pass and this task's first turn.
            if not self._autopilot_on(key):
                return
            if stage == autopilot.JERA:
                await self.precedent(cwd, unit, started_by="autopilot")
                return
            stream = (
                self.integrate(cwd, unit, started_by="autopilot") if stage == "integrate"
                else self.run_step(cwd, unit, stage, started_by="autopilot")
            )
            async for _ in stream:
                pass
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — the reason is shown, not swallowed
            said = str(e)
            # A gate's refusal carries its codes (`Refused`); anything else has none.
            if not autopilot.is_ci_pending(e) and not self._busy(key, unit):
                self._autopilot_set_stops(key, {unit: {"unit": unit, "kind": "f", "reason": said}}, {unit})
        finally:
            runs = self._autopilot_runs.get(key) or {}
            if runs.get(unit, ("", None))[1] is asyncio.current_task():
                del runs[unit]

    def _autopilot_block(self, key: str) -> dict[str, Any]:
        """What the board shows of the autopilot. Display only; decides nothing."""
        values = self._autopilot_values(key)
        on = values["autopilot"]
        block: dict[str, Any] = {
            "on": on, "may_ship": values["autopilot_may_ship"], "max_parallel": values["max_parallel"],
            "cap": None, "stops": [], "refused_because": self._off_loopback() if on else "",
        }
        if not on:
            return block
        journal = self._journal()
        if journal is not None:
            try:
                block["cap"] = self._autopilot_cap(journal.records(kinds=("start", "end")), values["daily_cap_usd"])
            except Busy:
                block["cap"] = None
        block["stops"] = sorted(
            (self._autopilot_stops.get(key) or {}).values(),
            key=lambda s: (autopilot.unit_number(s["unit"]), s["unit"]),
        )
        return block

    def _guide_block(self, key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """The board's guide: `{on, running, needs_you, decided}`, or `{on: False}` alone while the
        autopilot is off. `rows` are the run-log rows the board already read; what runs is read from
        memory. Display only; decides nothing.
        """
        if not self._autopilot_values(key)["autopilot"]:
            return {"on": False}
        stops = sorted(
            (self._autopilot_stops.get(key) or {}).values(),
            key=lambda s: (autopilot.unit_number(s["unit"]), s["unit"]),
        )
        return {
            "on": True,
            "running": guide.running(self._running_here(key, self._agent_overrides()[0])),
            "needs_you": guide.needs_you(stops),
            "decided": guide.decided(rows, datetime.now().astimezone()),
        }
