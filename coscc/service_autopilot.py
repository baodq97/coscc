"""The autopilot: the loop that asks `cos.mjs next` and starts the stages it names.

Split from `coscc/service.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from coscc import autopilot, backlog
from coscc import fetches
from coscc import integrate
from coscc.gitops import GitError
from coscc.journal import BadRecord, Busy
from coscc.service_common import BRANCH_REMOTE, BRANCH_TRUNK, Invalid


class AutopilotMixin:

    # -- the autopilot (`0043` R4–R10) ----------------------------------------
    #
    # It holds no rule of the loop. Each pass reads the board, the run log, the settings and
    # the workspace's last shortlist, and `next` for every unit on it and no other (`0104`);
    # with no shortlist it asks nothing. `coscc/autopilot.py` decides where to stop and what
    # to start; what it starts goes through `run_step` and `integrate`, which ask the gate
    # themselves. A refusal from them is a stop line, never a second way past the gate.

    def autopilot_start(self, cwd: str) -> None:
        """Run a pass now and every `POLL_SECONDS` after, for as long as the switch is on
        (R5 c, d). A second call for a workspace already running does nothing."""
        key = self._journal_key(cwd)
        self._autopilot_cwd[key] = cwd
        task = self._autopilot_tasks.get(key)
        if task is not None and not task.done():
            return
        self._autopilot_tasks[key] = asyncio.get_running_loop().create_task(self._autopilot_loop(key))

    def autopilot_stop(self, key: str) -> None:
        """Turned off: no more passes and no more `gh` calls for it (R1). A step it already
        started runs on to its end, as a person's would."""
        task = self._autopilot_tasks.pop(key, None)
        if task is not None:
            task.cancel()
        self._autopilot_stops.pop(key, None)

    def autopilot_resume(self) -> list[str]:
        """R5 c: at start-up, every workspace whose switch is on starts again. Returns them."""
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

    def _autopilot_nudge(self, key: str) -> None:
        """R5 a, b: a step or an integration ended, or an answer was written. One pass is
        scheduled and not waited for; nothing happens when the switch is off."""
        if not self._autopilot_on(key):
            return
        task = asyncio.get_running_loop().create_task(self._autopilot_guarded(key))
        self._autopilot_pending.add(task)
        task.add_done_callback(self._autopilot_pending.discard)

    async def _autopilot_loop(self, key: str) -> None:
        while True:
            await self._autopilot_guarded(key)
            await asyncio.sleep(autopilot.POLL_SECONDS)

    async def _autopilot_guarded(self, key: str) -> None:
        """A pass that raises leaves a stop line saying so, not a dead loop."""
        try:
            await self._autopilot_pass(key)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — shown on the board, never swallowed
            self._autopilot_set_stops(key, {"": {"unit": "", "kind": "f", "reason": f"the autopilot's pass failed: {e}"}})

    def _autopilot_running(self, key: str) -> list[dict[str, Any]]:
        """What runs in this workspace now, by unit, a person's steps included (R8 a)."""
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
        """R7's figures, for a pass and for the board: every workspace, every starter."""
        now = datetime.now().astimezone()
        spent = autopilot.spent_today(records, now)
        active = {
            (k, unit): "integrate" if mark.kind == "integrate" else mark.stage
            for (k, unit), mark in self._active.items()
        }
        # A launch holds no mark until `run_step` or `integrate` takes one — `integrate` only
        # after its fetch and `gh` reads — and is counted from the moment it was chosen.
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
        """Keep the stops a pass found, and log each unit's that changed (R9, R11).

        `asked`: the units this pass looked at, when it did not look at all of them; the
        others keep what they had. The log is an `autopilot-stop` record per change, `stop`
        empty once it cleared — what `verify_0043` reads to tell a person's press at a stop
        from one outside them.
        """
        before = self._autopilot_stops.get(key, {})
        if asked is None:
            after = dict(found)
        else:
            after = {**{u: s for u, s in before.items() if u and u not in asked}, **found}
        self._autopilot_stops[key] = after
        journal = self._journal()
        if journal is None:
            return
        for unit in sorted(set(before) | set(after)):
            if not unit:
                continue
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

    async def _autopilot_pass(self, key: str) -> None:
        """One look at a workspace: follow its shortlist (`0104`), find each listed unit's stop
        or why it waits, then start what may start, highest first, each after its record."""
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
                records = journal.records(kinds=("start", "end", "integration", "shortlist", "answer", "screens"))
            except Busy as e:
                self._autopilot_set_stops(key, {"": {"unit": "", "kind": "f", "reason": str(e)}})
                return
            # `0104` R1: the last well-formed shortlist, read again on every pass.
            listed, n = backlog.shortlist_of(r for r in records if r.get("workspace") == key)
            if listed is None or not listed["units"]:
                # R3: nothing is asked and nothing starts. R1 of `0043` as below.
                if not self._autopilot_on(key) or not self._autopilot_values(key)["autopilot"]:
                    return
                self._autopilot_set_stops(key, {"": {"unit": "", "kind": "shortlist", "reason": autopilot.NO_SHORTLIST}})
                return
            names = list(listed["units"])
            # `0112` R4: a listed unit at `ship` is decided on the `origin/main` the remote has
            # now — `behind` below and the `ship` gate `next` asks both read that ref, and
            # neither fetches. Through the coordinator, which reuses a fetch under
            # `REUSE_SECONDS`. A fetch that fails leaves the ref as it was, and each such
            # unit's stop says so.
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
            for r in records:
                # `0111`: a retake of the screenshots that failed is the unit's last word too.
                if r.get("workspace") == key and r.get("kind") in ("end", "integration", "screens") and autopilot.is_step(r):
                    last[str(r.get("unit") or "")] = r
                    if r.get("kind") == "integration":
                        integrations[str(r.get("unit") or "")] = r

            running = self._autopilot_running(key)
            here = {r["unit"]: r["stage"] for r in running}
            board = {u["name"]: u for u in data["units"]}
            found: dict[str, dict[str, str]] = {}
            candidates: list[dict[str, Any]] = []
            # R6: why each unit that is no candidate waits, for the units ranked below it.
            reasons: dict[str, tuple[str, str]] = {}
            # R2, R5: every unit on the shortlist is asked, and no other.
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
                stop = autopilot.stop_for(u, nxt, last.get(name), settings["autopilot_may_ship"])
                stage = nxt.get("stage") or ""
                # R10: a unit behind `main`, conflicting or red after integration is integrated
                # first, and not again when CI is red on what the autopilot's own integration
                # pushed. Since `0112` R1 also after a `pass`: GitHub would refuse the merge,
                # the rebase closes `ship`, and a new round opens it again — but only where the
                # autopilot may ship, since otherwise a person merges and the round is theirs.
                info = u.get("integration") or {}
                rounds = u.get("rounds") or []
                passed = bool(rounds) and rounds[-1].get("verdict") == "pass"
                if (
                    info.get("state") in integrate.BUTTON_STATES
                    and (not passed or settings["autopilot_may_ship"])
                    and (stop is None or stop["kind"] == "f")
                ):
                    stop, stage = autopilot.red_again(info, integrations.get(name)), "integrate"
                # `0106` R2, R3: a draft whose questions are all answered runs again, at most
                # `MAX_RERUNS` times, and only on an answer given since its last run; with none,
                # it is the stop `f` it was before `0106`. Before `reason`, which raises on no
                # stage and no stop.
                rerun = False
                if stop is None and not stage and nxt.get("rerun"):
                    if autopilot.reruns_of(records, key, name, nxt["rerun"]) >= autopilot.MAX_RERUNS:
                        artifact = next((s["file"] for s in u.get("stages") or [] if s["stage"] == nxt["rerun"]), nxt["rerun"])
                        stop = autopilot.rerun_stop(artifact)
                    elif not autopilot.answered_since_start(records, key, name, nxt["rerun"]):
                        stop = autopilot.stop_for(u, {**nxt, "rerun": ""}, last.get(name), settings["autopilot_may_ship"])
                    else:
                        stage, rerun = nxt["rerun"], True
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
                candidates.append({
                    "unit": name, "stage": stage, "files": files, "need": autopilot.reservation(stage), "rank": rank,
                    "rerun": rerun,
                })

            for r in running:
                if r["stage"] in autopilot.CODE_STAGES:
                    r["files"] = self._autopilot_files(cwd, r["unit"])
            now = datetime.now().astimezone()
            # The spec's `## Design`: a `start` with no `end`, from a process before this one,
            # counts against N for 24 hours.
            elsewhere = sum(1 for (k, unit) in autopilot.open_starts(records, now) if k == key and unit not in here)
            cap = self._autopilot_cap(records, settings["daily_cap_usd"])
            room = cap["limit"] - cap["spent"] - cap["running"]
            picked = autopilot.pick(candidates, running, settings["max_parallel"] - elsewhere, room)
            reasons.update(picked["held"])
            est = f" ({cap['estimated']:.2f} estimated)" if cap["estimated_count"] else ""
            for c in picked["capped"]:
                found[c["unit"]] = {"unit": c["unit"], "kind": "cap", "reason": (
                    f"spent {cap['spent']:.2f}{est} + running {cap['running']:.2f} + {c['stage']} "
                    f"{c['need']:.2f} is over the cap of {cap['limit']:.2f} USD ({cap['day']})"
                )}
                reasons[c["unit"]] = autopilot.reason_for({}, c["stage"], found[c["unit"]])
            # `0106` R5: a run again that `max_parallel` alone held back says so. Any other
            # candidate held back that way still says nothing, as before.
            left = {c["unit"] for c in picked["chosen"] + picked["capped"]} | set(picked["held"])
            for c in candidates:
                if c["rerun"] and c["unit"] not in left:
                    found[c["unit"]] = {"unit": c["unit"], **autopilot.full_stop(c["stage"], settings["max_parallel"])}
                    reasons[c["unit"]] = autopilot.reason_for({}, c["stage"], found[c["unit"]])
            # Raises before anything is recorded or started when a unit above one chosen has no
            # reason; `_autopilot_guarded` shows it as a stop line.
            passed = autopilot.passed_for(names, [c["unit"] for c in picked["chosen"]], reasons)
            # R1: the switch may have been turned off while this pass read the board and
            # `next`. Nothing from here on awaits, so nothing starts once it is off.
            if not self._autopilot_on(key) or not self._autopilot_values(key)["autopilot"]:
                return
            self._autopilot_set_stops(key, found)
            run_id = uuid.uuid4().hex
            shortlist = {"n": n, "at": listed.get("at"), "units": names}
            for c, over in zip(picked["chosen"], passed):
                # R6: no record, no start — and nothing ranked below it either, since starting
                # one would pass over a unit chosen with no record of it.
                try:
                    journal.append({
                        "kind": "autopilot-pick", "workspace": key, "unit": c["unit"], "stage": c["stage"],
                        "pass": run_id, "rank": c["rank"], "shortlist": shortlist, "passed": over,
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
        """Start one step or integration and read it to its end, since no client will.

        A refusal is a stop line with the gate's words (R6 f) — unless it is CI still
        running, or the unit taken by someone else in the meantime, which are not stops.
        """
        stream = (
            self.integrate(cwd, unit, started_by="autopilot") if stage == "integrate"
            else self.run_step(cwd, unit, stage, started_by="autopilot")
        )
        try:
            # R1: turned off between the pass and this task's first turn.
            if not self._autopilot_on(key):
                return
            async for _ in stream:
                pass
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — the reason is shown, not swallowed
            said = str(e)
            if not autopilot.is_ci_pending(said) and not self._busy(key, unit):
                self._autopilot_set_stops(key, {unit: {"unit": unit, "kind": "f", "reason": said}}, {unit})
        finally:
            runs = self._autopilot_runs.get(key) or {}
            if runs.get(unit, ("", None))[1] is asyncio.current_task():
                del runs[unit]

    def _autopilot_block(self, key: str) -> dict[str, Any]:
        """What the board shows of the autopilot (R9). Display only; decides nothing."""
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
