"""A unit's pull request: integrating its branch with `main` (a mechanical rebase, or Gebo), the
CI answers the board shows beside it, and the `pr` and `ship` the app does itself through the
PR machine.

Its attempts run on `runner.steps.Steps`, which holds their tasks, readers and ends.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator
from collections.abc import Callable, Mapping, Sequence

from coscc import units
from coscc.agent import agents, pack, skills
from coscc.agent import steps as steps_mod
from coscc.agent.policy import row_for
from coscc.agent.sessions import Sessions
from coscc.bus import Bus
from coscc.config import Config
from coscc.git import fetches, gitops
from coscc.git.gitops import GitError
from coscc.github import integrate, prmachine
from coscc.github.integrate import open_prs_once
from coscc.kernel import Grant, Invalid, facts as facts_of
from coscc.runner.queue import Attempt, Holds, Refused
from coscc.kernel import Run
from coscc.runner import run as run_mod
from coscc.runner.run import NO_SUBMISSION
from coscc.runner.step import check_started_by, config_sources, with_ceilings
from coscc.runner.steps import Steps
from coscc.store.db import Busy, in_thread, now as _now
from coscc.store.journal import MERGE_RECORD, BadRecord, Journal
from coscc.units import states, submit as submit_mod, worktrees
from coscc.units import board as board_reader
from coscc.units import BadUnit, CannotCreate
from coscc.units.board import Unavailable
from coscc.units.read import younger_than
from coscc.units.workspaces import Workspaces
from coscc.units.worktrees import BRANCH_REMOTE, BRANCH_TRUNK

log = logging.getLogger(__name__)

# Seconds a held CI answer is trusted before a board read asks `gh` again, in the
# background. Chosen, not measured.
CI_REFRESH = 60.0


class _Stopped(Exception):
    """A Stop an integration read at one of its stop points."""


def integration_since_review(journal: Journal, key: str, unit: str) -> dict[str, Any] | None:
    """The latest `pushed` integration recorded after the last `review` step
    that ended `done`, or None. Read by id order, which is the order the rows were written."""
    try:
        rows = journal.records(key, unit)
    except Busy:
        return None
    found = None
    for rec in rows:
        if rec.get("kind") == "integration" and rec.get("outcome") == "pushed":
            found = rec
        elif (
            rec.get("kind") == "end"
            and states.is_review(rec.get("stage"))
            and rec.get("outcome") == "done"
        ):
            found = None
    return found


class Integration:
    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        holds: Holds,
        sessions: Sessions,
        steps: Steps,
        bus: Bus,
        *,
        config_for: Callable[[str], tuple[str | None, str, str | None, str]],
    ) -> None:
        self.config = config
        self.ws = ws
        self.holds = holds
        self.sessions = sessions
        self.steps = steps
        self.bus = bus
        self.config_for = config_for
        # One lock per workspace held across an integration's check-and-mark.
        self._integrate_locks: dict[str, asyncio.Lock] = {}
        # By `(journal key, unit)`: the last answer of `integrate.required_checks`,
        # `{head, checks | error, at}`, and the one background ask running for it. Never waited
        # on by a board read.
        self.ci: dict[tuple[str, str], dict[str, Any]] = {}
        self.ci_asks: dict[tuple[str, str], asyncio.Task] = {}
        holds.attempts.launchers["integration"] = lambda row: steps.launch(row, self._integration)
        steps.mechanical = self.mechanical
        steps.integration_note = self.review_note

    def review_note(self, journal: Journal, key: str, unit: str) -> str:
        """The integration pushed since the last review round, for `review`."""
        since = integration_since_review(journal, key, unit)
        if not since:
            return ""
        return integrate.describe_for_review(since)

    async def attach_integration(
        self,
        cwd: str,
        units_: list[dict[str, Any]],
        journal: Journal | None,
        key: str,
        prs_once=None,
        fresh: bool = False,
    ) -> list[tuple[tuple[str, str], str, int, str]]:
        """Give every unit `integration: {...}` when it sits in the window, else None.

        **Reads only.** The workspace's open pull requests from `prs_once` (the board passes
        the held list), `git` counts against the `origin/main` the last fetch brought — no
        fetch here — and, for a unit whose head is the one its last integration pushed, the
        held CI answer: no `gh pr checks` is waited on, unless `fresh`. Nothing here writes a
        record, calls `update-branch` or opens a session.

        Also gives each unit in the window `ci_held`, the held CI answer when it
        is for the head `gh pr list` returned, and returns the CI asks `board` starts
        once it has answered: `(slot, tree, pr number, head)` for each unit with no answer
        for that head, or one older than `CI_REFRESH`, and no ask already running.

        The answer is the `pull_requests` row's, which only a `ci-at-head`
        transition writes. `gh`'s error is no answer, so it stays in `ci`, held only so it
        is not asked again before `CI_REFRESH`.
        """
        for u in units_:
            u["integration"] = None
        window = [u for u in units_ if u.get("between_pr_and_ship") and u.get("pr")]
        if not window:
            return []
        root = Path(cwd).expanduser().resolve()
        last = self._last_integrations(journal, key)
        # The board's one `gh pr list`, shared with the release block.
        prs: list[dict[str, Any]] | str = await (prs_once or open_prs_once(cwd))()
        asks: list[tuple[tuple[str, str], str, int, str]] = []
        oldest = datetime.fromisoformat(_now()) - timedelta(seconds=CI_REFRESH)
        for u in window:
            number = (u.get("pr") or {}).get("number")
            row = (
                next((r for r in prs if r.get("number") == number), None)
                if isinstance(prs, list)
                else None
            )
            if row is None or number is None:
                info = await self._integration_of(root, key, u, prs, last.get(u["name"]), ask=fresh)
                if info is not None:
                    u["integration"] = info
                continue
            slot, head = (key, u["name"]), str(row.get("headRefOid") or "")
            held = self._held_ci(key, u["name"], int(number), head)
            if held is not None and held.get("head") != head:
                held = None
            info = await self._integration_of(
                root, key, u, prs, last.get(u["name"]), held, ask=fresh
            )
            if info is not None:
                u["integration"] = info
            if held is not None:
                u["ci_held"] = held
            if slot in self.ci_asks:
                continue
            if (
                held is None
                or held.get("head") != head
                or not younger_than(held.get("at") or "", oldest)
            ):
                asks.append((slot, str(root), int(number), head))
        return asks

    def _held_ci(self, key: str, unit: str, number: int, head: str) -> dict[str, Any] | None:
        """The row's answer at `head`, else `gh`'s last error there, else `None`."""
        try:
            held = prmachine.ci_held(self.ws.unit_meta().history, key, number, head)
        except Exception:
            # A board read never fails on this.
            log.exception("the CI hold of %s could not be read", unit)
            held = None
        if held is not None:
            return held
        error = self.ci.get((key, unit))
        return error if error is not None and error.get("head") == head else None

    def ask_ci(
        self,
        asks: list[tuple[tuple[str, str], str, int, str]],
        ended: Callable[[str], None] | None = None,
    ) -> None:
        """One background `gh pr checks` per ask, none awaited. Its
        answer is recorded by `prmachine.record_ci`, through `ci-at-head`, on the unit's
        `pull_requests` row; `gh`'s error is held in `ci` with the time it was read, so it
        is not asked again before `CI_REFRESH` either. `ended` is told the tree of each ask
        that brought an answer or an error, so the board can be read again."""
        for slot, tree, number, head in asks:
            if slot in self.ci_asks:
                continue

            async def ask(slot=slot, tree=tree, number=number, head=head) -> None:
                await self._answer_ci(slot, tree, number, head)
                if ended is not None:
                    ended(tree)

            task = asyncio.get_running_loop().create_task(ask())
            self.ci_asks[slot] = task
            # Removed however it ends — cancelled included — or the unit is never asked again.
            task.add_done_callback(
                lambda t, slot=slot: (
                    self.ci_asks.pop(slot, None) if self.ci_asks.get(slot) is t else None
                )
            )

    async def _answer_ci(self, slot: tuple[str, str], tree: str, number: int, head: str) -> None:
        try:
            checks = await integrate.required_checks(tree, number)
        except integrate.IntegrateError as e:
            self.ci[slot] = {"head": head, "error": str(e), "at": _now()}
            return
        u = prmachine.Unit(slot[0], slot[1], Path(tree), tree, "", "", None)
        try:
            await self.pr_machine().record_ci(
                u, number, head, [c for c in checks if isinstance(c, dict)]
            )
        except Exception as e:
            # A background ask never raises.
            log.exception("the CI answer of %s could not be recorded", u)
            self.ci[slot] = {
                "head": head,
                "error": f"the CI answer could not be recorded: {e}",
                "at": _now(),
            }
            return
        self.ci.pop(slot, None)

    @staticmethod
    def _last_integrations(journal: Journal | None, key: str) -> dict[str, dict[str, Any]]:
        if journal is None:
            return {}
        try:
            rows = journal.records(key, kind="integration")
        except Busy:
            return {}
        return {str(r.get("unit")): r for r in rows}

    async def _integration_of(
        self,
        root: Path,
        key: str,
        u: dict[str, Any],
        prs: list[dict[str, Any]] | str,
        last_record: dict[str, Any] | None,
        held_ci: dict[str, Any] | None = None,
        ask: bool = True,
    ) -> dict[str, Any] | None:
        """One unit's state. None when its pull request is not among the open ones. The
        required checks are asked of `gh` and recorded through `prmachine.record_ci`, or with
        `ask` off are `held_ci`'s: none held is none counted red yet, and neither is a red
        check while the `ci` recorded at the head is not `red`."""
        number = (u.get("pr") or {}).get("number")
        if isinstance(prs, str):
            pr_row: dict[str, Any] | str = prs
        else:
            match = next((r for r in prs if r.get("number") == number), None)
            if match is None or number is None:
                return None
            pr_row = match
        origin_sha = ""
        missing: int | str = 0
        head = str(pr_row.get("headRefOid") or "") if isinstance(pr_row, dict) else ""
        if isinstance(pr_row, dict):
            try:
                origin_sha = await gitops.rev_parse(root, "refs/remotes/origin/main")
                if not await gitops.has_commit(root, head):
                    missing = (
                        f"the pull request's head {head[:7]} is not here: fetch, then ask again"
                    )
                else:
                    missing = await gitops.count_missing(root, head, origin_sha)
            except GitError as e:
                missing = str(e)
        checks: list[dict[str, Any]] | str | None = None
        if not ask:
            if held_ci is not None:
                checks = held_ci.get("checks", str(held_ci.get("error") or ""))
        elif number is not None and integrate.needs_checks(pr_row, last_record):
            try:
                checks = await integrate.required_checks(str(root), int(number))
            except integrate.IntegrateError as e:
                checks = str(e)
            else:
                pu = prmachine.Unit(key, u["name"], root, str(root), "", "", None)
                try:
                    await self.pr_machine().record_ci(pu, int(number), head, checks)
                except Exception:
                    log.exception("the CI answer of %s could not be recorded", u["name"])
        # Red only once the PR machine recorded `red` at this head: it reruns a red head first.
        if isinstance(checks, list) and number is not None:
            held = self._held_ci(key, u["name"], int(number), head)
            if (held or {}).get("ci") != "red":
                checks = []
        verdict = integrate.classify(pr_row, missing, origin_sha, last_record, checks)
        state = verdict["state"]
        review_status = next(
            (
                r.get("status") or ""
                for r in u.get("stages") or []
                if states.is_review(r.get("stage"))
            ),
            "",
        )
        gebo = state in integrate.GEBO_STATES
        # A `current` unit also has the button, since the count may be against a
        # stale `origin/main` and only a press fetches. Both mechanical states may fall
        # to Gebo when GitHub refuses the rebase, and the page says so.
        fallback = state in ("current", "behind")
        return {
            "state": state,
            "reason": verdict.get("reason", ""),
            "behind": missing if isinstance(missing, int) else None,
            "origin_sha": origin_sha,
            "pr_head": pr_row.get("headRefOid", "") if isinstance(pr_row, dict) else "",
            "mode": "agent" if gebo else ("mechanical" if fallback else ""),
            "button": state in integrate.BUTTON_STATES or state == "current",
            "needs_person": list((last_record or {}).get("needs_person") or [])
            if (last_record or {}).get("outcome") == "needs-person"
            else [],
            "warnings": integrate.warnings(
                u.get("rounds") or [],
                review_status,
                gebo or fallback,
                row_for("integrate").warning,
                fallback=fallback,
                name=(self.steps.agent_of("integrate") or {}).get("name", ""),
            ),
            "consequence": str((pack.row("integrate") or {}).get("consequence") or ""),
        }

    async def integrate(
        self,
        cwd: str,
        unit: str,
        started_by: str = "person",
    ) -> AsyncIterator[tuple[str, Any]]:
        """Integrate one unit, on a person's request. Streams like `run_step`.

        Or on the autopilot's, which passes `started_by="autopilot"`; every record this writes
        carries it. No route passes it.

        Refuses before anything changes, and every refusal, push or failure leaves one
        `integration` record. `behind` goes the mechanical road; `conflicting` and
        `red-after-integration` open Gebo.

        A press inside the window fetches `origin/main` first, through the fetch
        coordinator and before the lock, so the count is against the trunk as it is now; a
        failed fetch goes on with the ref it has and says so. It also reads GitHub's
        `mergeStateStatus`, only to record it. A mechanical road whose `update-branch`
        exits non-zero opens Gebo with that code and gh's words.

        A rebase left in progress by an integration this run log shows cut is aborted first.
        A local head that is not the pull request's is read against it: `behind` follows it
        with no session; `ahead` or `diverged` opens Gebo to push what was never pushed, in
        every state.

        The press is an attempt in `queued`, run by `_integration` once the workspace's one
        slot for heavy work is free; this frame only reads, as `run_step`'s does.
        """
        try:
            check_started_by(started_by)
        except ValueError as e:
            raise Invalid(str(e)) from e
        self.ws.check(cwd)
        self.steps.refuse_updating()
        if self.ws.journal() is None:
            raise Refused(
                "no working folder is set, so an integration cannot be recorded — set COS_WORKING_DIR",
                ("no-run-log",),
            )
        if not unit:
            raise Invalid("name a work unit")
        self.ws.unit_dir(cwd, unit)
        async for item in self.steps.stream(
            "integration", cwd, unit, "integrate", started_by=started_by
        ):
            yield item

    def _stop_point(self, running: steps_mod.Running, last: bool = False) -> None:
        """Between two mechanical steps of an integration: a Stop recorded on its attempt ends
        it here, never inside a `git` or `gh`. The `last` one, before the push or Gebo, moves
        the attempt to `ending`: a Stop after it is recorded as `stop_late`."""
        row = self.holds.attempts.get(running.attempt)
        if row is not None and row["stop_asked_at"]:
            raise _Stopped(
                f"{running.unit}'s integration was stopped by {row['stop_asked_by']} before it pushed anything"
            )
        if last:
            self.holds.attempts.move(running.attempt, "ending")

    async def _integration(self, running: steps_mod.Running, asked: Attempt) -> None:
        """An integration from `running` to its end, its items told to its readers."""
        outcome = "done"
        try:
            async for item in self._integrate_body(
                running, running.cwd or asked["workspace"], asked["unit"], asked["started_by"]
            ):
                self.steps.tell(running, item)
        except _Stopped as e:
            self.steps.tell(running, ("raise", Invalid(str(e))))
            outcome = "stopped"
        except asyncio.CancelledError:
            # The app going down: the next start ends the attempt.
            if self.steps.tasks.get(running.attempt) is running:
                del self.steps.tasks[running.attempt]
            raise
        except Exception as e:  # noqa: BLE001 - the reader raises it, as it always did
            self.steps.close(running, e, "failed")
            return
        if self.steps.tasks.get(running.attempt) is running:
            del self.steps.tasks[running.attempt]
        self.steps.end_attempt(running.attempt, outcome)

    async def _integrate_body(  # noqa: C901, PLR0915 - still to split
        self,
        running: steps_mod.Running,
        cwd: str,
        unit: str,
        started_by: str,
    ) -> AsyncIterator[tuple[str, Any]]:
        """`integrate`'s work, in its attempt's task."""
        journal = self.ws.journal()
        if journal is None:
            raise Refused(
                "no working folder is set, so an integration cannot be recorded — set COS_WORKING_DIR",
                ("no-run-log",),
            )
        directory = self.ws.unit_dir(cwd, unit)
        self._stop_point(running)
        try:
            data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Refused(f"no such work unit in this workspace: {unit}", ("no-unit",))
        key = self.ws.key(cwd)
        root = Path(cwd).expanduser().resolve()
        last = self._last_integrations(journal, key).get(unit)
        info = None
        pr = (found.get("pr") or {}).get("number")
        # Spread into every record this press writes.
        seen: dict[str, Any] = {
            "fetch": None,
            "merge_state": "",
            "started_by": started_by,
            "completion": None,
        }
        if found.get("between_pr_and_ship") and found.get("pr"):
            self._stop_point(running)
            try:
                seen["fetch"] = await fetches.fetch(root, BRANCH_REMOTE, BRANCH_TRUNK)
            except GitError as e:
                seen["fetch"] = {"outcome": "failed", "detail": str(e)}
            try:
                prs: list[dict[str, Any]] | str = await integrate.open_prs(str(root))
            except integrate.IntegrateError as e:
                prs = str(e)
            info = await self._integration_of(root, key, found, prs, last)
            if info is not None and seen["fetch"]["outcome"] == "failed":
                note = integrate.origin_note(info["origin_sha"], seen["fetch"])
                info["reason"] = f"{info['reason']}; {note}" if info.get("reason") else note
            if pr is not None:
                seen["merge_state"] = await integrate.merge_state(str(root), int(pr))
        state = (info or {}).get("state", "")
        pr_head = (info or {}).get("pr_head", "")
        origin_sha = (info or {}).get("origin_sha", "")
        try:
            branch = units.branch_name(
                cwd, unit, self.config.data_dir, self.ws.snapshot(cwd, [unit])
            )
        except CannotCreate, BadUnit:
            branch = ""
        tree_found = None
        try:
            tree_found = await worktrees.find(cwd, unit, self.config.data_dir)
        except GitError, BadUnit:
            tree_found = None
        tree = Path(tree_found["path"]) if tree_found else None

        # What the app did to the tree before deciding, at the head of every
        # record this press writes.
        before: list[str] = []

        def write(rec: dict[str, Any]) -> dict[str, Any]:
            if before:
                rec = {
                    **rec,
                    "detail": "; ".join(before + ([rec["detail"]] if rec.get("detail") else [])),
                }
            try:
                return journal.append(rec)
            except BadRecord, Busy:
                return rec

        self._stop_point(running)
        lock = self._integrate_locks.setdefault(key, asyncio.Lock())
        async with lock:  # noqa: PLR1702 - still to split
            # The attempt is what holds the unit, and it was refused `unit-busy` if another did.
            busy = ""
            cut = None
            if tree is not None:
                try:
                    # Nothing else runs for the unit in this process: its attempt holds it.
                    cut = integrate.cut_integration(journal.records(key, unit=unit), unit, False)
                except Busy:
                    cut = None
                if cut is not None:
                    try:
                        if await gitops.rebase_in_progress(tree):
                            await gitops.abort_rebase(tree)
                            before.append(
                                f"an integration cut at {cut['at']} left a rebase in progress; the app aborted it"
                            )
                    except GitError as e:
                        before.append(
                            f"could not abort the rebase an integration cut at {cut['at']} left: {e}"
                        )
            clean = on_branch = None
            local_head = ""
            if tree is not None:
                try:
                    clean = await gitops.is_clean(tree)
                    on_branch = bool(branch) and (await gitops.current_branch(tree)) == branch
                    local_head, _ = await gitops.head_and_branch(tree)
                except GitError:
                    clean = on_branch = None
            how, how_said = "", ""
            if (
                tree is not None
                and clean is True
                and on_branch is True
                and pr_head
                and local_head
                and local_head != pr_head
            ):
                answers: list[bool | None] = []
                for ancestor, descendant in ((local_head, pr_head), (pr_head, local_head)):
                    try:
                        answers.append(await gitops.is_ancestor(tree, ancestor, descendant))
                    except GitError as e:
                        answers.append(None)
                        how_said = how_said or str(e)
                how = integrate.relation(local_head, pr_head, *answers)
                if how == "diverged":
                    # Only a local head on a newer `main` than the pull request's is
                    # a rebase that was never pushed; the other way round, the pull request was
                    # rebased elsewhere and pushing the tree would undo that.
                    newer = None
                    try:
                        if not origin_sha:
                            raise GitError(
                                "origin/main could not be read, so the two bases cannot be compared"
                            )
                        local_base = await gitops.merge_base_of(tree, local_head, origin_sha)
                        pr_base = await gitops.merge_base_of(tree, pr_head, origin_sha)
                        newer = integrate.newer_base(
                            local_base, pr_base, await gitops.is_ancestor(tree, pr_base, local_base)
                        )
                    except GitError as e:
                        how_said = str(e)
                    how = integrate.relation(local_head, pr_head, *answers, newer=newer)
                was = local_head
                if how == "behind":
                    try:
                        await gitops.reset_branch_to(tree, branch, local_head, pr_head)
                        local_head = pr_head
                        before.append(
                            f"the local branch followed the pull request's head from {was[:7]} to {pr_head[:7]}"
                        )
                    except GitError as e:
                        how, how_said = "", str(e)
                if how and how != "same":
                    seen["completion"] = {"relation": how, "local_head": was, "cut": cut}
            reason, why = integrate.refusal(
                in_window=info is not None,
                busy=busy,
                clean=clean,
                branch_ok=on_branch,
                local_head=local_head,
                pr_head=pr_head,
                state=state,
                origin=integrate.origin_note(origin_sha, seen["fetch"]),
                relation=how,
                relation_said=how_said,
            )
            if reason:
                write(
                    integrate.record(
                        workspace=key,
                        unit=unit,
                        pr=pr,
                        mode=(info or {}).get("mode") or "mechanical",
                        head_before=pr_head,
                        head_after="",
                        origin_sha=origin_sha,
                        outcome="refused",
                        detail=reason,
                        code=why,
                        **seen,
                    )
                )
                # The attempt's row carries the code, which the autopilot reads.
                raise Refused(reason, (why,)) if why else Invalid(reason)
            refusal = self.steps.feature_refusal(
                facts_of(
                    workspace=cwd,
                    workspace_key=key,
                    unit=unit,
                    agent="integrate",
                    run="",
                    cwd=str(tree or root),
                    watch=None,
                    directory=directory,
                    resumed=False,
                    # Gebo, and a mechanical rebase, push the unit's branch.
                    grant=Grant(branch=branch),
                )
            )
            if refusal:
                raise Refused(refusal, ("feature-refused",))
            if state == "behind" and how not in integrate.COMPLETION:
                # An Apply waits for a mechanical integration, so none begins once
                # one is pressed.
                self.steps.refuse_mechanical()
            # Commits never pushed go to Gebo whatever the state.
            completing = how in integrate.COMPLETION
            # Gebo shows as running under its agent name; a mechanical rebase has no agent and
            # shows as rebasing. The same condition as below.
            self.holds.attempts.set_road(
                running.attempt, "rebase" if state == "behind" and not completing else "gebo"
            )
            self._stop_point(running, last=True)
        assert tree is not None
        assert pr is not None
        refused_update = None
        if state == "behind" and not completing:
            rec, refused_update = await self._integrate_mechanical(
                key,
                unit,
                int(pr),
                tree,
                branch,
                pr_head,
                origin_sha,
                seen,
            )
            if rec is not None:
                rec = write(rec)
                yield ("done", {"integration": rec})
                return
            # GitHub refused the rebase, and the press agreed to Gebo for that. The board shows Gebo from here on, not a rebase.
            self.holds.attempts.set_road(running.attempt, "gebo")
            # An update waits for a mechanical integration, and a Gebo session is
            # paused instead, so one waiting on this can go ahead.
            self.bus.publish("integration.escalated", {"workspace": key, "unit": unit})
        async for item in self.integrate_gebo(
            cwd,
            key,
            unit,
            directory,
            data,
            info,
            int(pr),
            tree,
            branch,
            pr_head,
            origin_sha,
            journal,
            write,
            seen,
            refused_update,
            completion=seen["completion"] if completing else None,
        ):
            yield item

    async def _integrate_mechanical(
        self,
        key: str,
        unit: str,
        pr: int,
        tree: Path,
        branch: str,
        head_before: str,
        origin_sha: str,
        seen: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """GitHub rebases, the local branch follows. No session.

        `(record, None)`, or `(None, {code, said})` when `update-branch` exited non-zero and
        the pull request's head is still `head_before` — the caller then opens Gebo.
        A `gh` that could not run or did not answer in time, and a head that has not moved
        yet, stay `failed`: there is no exit code to go on, and GitHub may still be
        rebasing, which a Gebo session would race.
        """
        record = functools.partial(
            integrate.record,
            workspace=key,
            unit=unit,
            pr=pr,
            mode="mechanical",
            head_before=head_before,
            origin_sha=origin_sha,
            fetch=seen["fetch"],
            merge_state=seen["merge_state"],
            started_by=seen["started_by"],
            completion=seen["completion"],
        )
        try:
            code, said = await integrate.update_branch(str(tree), pr)
        except integrate.IntegrateError as e:
            return record(head_after="", outcome="failed", detail=str(e)), None
        if code != 0:
            refused = {"code": code, "said": said or "gh refused"}
            # A non-zero exit does not rule out that GitHub took the command.
            # Gebo's lease would then refuse its push while the head read afterwards counted as
            # Gebo's — so the head is read once, and a moved one opens no session.
            try:
                head_after = await integrate.pr_head(str(tree), pr)
                unread = "it came back empty"
            except integrate.IntegrateError as e:
                head_after, unread = "", str(e)
            if head_after == head_before:
                return None, refused
            if not head_after:
                return record(
                    head_after="",
                    outcome="failed",
                    update_branch=refused,
                    detail=f"gh pr update-branch exited {code}, and the pull request's head could not be "
                    f"read to rule out a rebase on GitHub's side, so no session was opened: {unread}",
                ), None
            record = functools.partial(record, update_branch=refused)
            said = f"gh pr update-branch exited {code}, but the pull request's head moved; no session was opened"
        else:
            head_after = head_before
            for attempt in range(integrate.POLL_TRIES):
                try:
                    head_after = await integrate.pr_head(str(tree), pr)
                except integrate.IntegrateError:
                    head_after = head_before
                if head_after and head_after != head_before:
                    break
                if attempt + 1 < integrate.POLL_TRIES:
                    await asyncio.sleep(integrate.POLL_DELAY)
        if not head_after or head_after == head_before:
            return record(
                head_after="",
                outcome="failed",
                detail="GitHub accepted the command but the head has not changed yet",
            ), None
        try:
            await gitops.reset_branch_to(tree, branch, head_before, head_after)
            detail = said
        except GitError as e:
            # The push happened on GitHub's side either way; the local tree is behind it.
            detail = f"pushed on GitHub, but the local branch was not moved: {e}"
        return record(head_after=head_after, outcome="pushed", detail=detail), None

    async def integrate_gebo(
        self,
        cwd: str,
        key: str,
        unit: str,
        directory: Path | None,
        data: dict[str, Any] | None,
        info: dict[str, Any] | None,
        pr: int,
        tree: Path,
        branch: str,
        head_before: str,
        origin_sha: str,
        journal: Journal,
        write: Any,
        seen: dict[str, Any],
        refused_update: dict[str, Any] | None = None,
        completion: dict[str, Any] | None = None,
        resume: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """One Gebo session; the outcome is read from GitHub afterwards.

        `refused_update`: the `update-branch` refusal that opened it, carried into
        the prompt and the press's one record.

        `completion`: the local head to push as it is. A pull request that
        ends on any other head is `failed`, whoever pushed it.

        `resume`: a `suspend` row of this session. It goes on from its safe point
        with the row's `message`, writes no `start`, and ends as it would have.
        """
        root = Path(cwd).expanduser().resolve()
        was = dict((resume or {}).get("owner") or {})
        rel = (
            was.get("rel") or {}
            if resume is not None
            else await self._related(root, unit, data or {}, head_before, origin_sha)
        )
        units_root = self.ws.units_root(cwd)
        # The `integrate` row, read once for the prompt, the records and
        # the session's commit attribution.
        agent = self.steps.agent_of("integrate")
        # Gebo runs no `Runner`, so its row takes its two ceilings here,
        # the ceilings by the function the runner asks: one taken up again keeps its owner's.
        row, ceilings = with_ceilings(row_for("integrate"), "integrate", None, was)
        name = agent["name"] if agent is not None else ""
        start: dict[str, Any] = {}
        if resume is None:
            assert directory is not None
            assert info is not None
            # By path; Gebo reads what it needs of them.
            own = {}
            for artifact in states.files_where(kind="artifact"):
                path = Path(directory).resolve() / artifact
                if path.exists():
                    own[artifact] = path
            try:
                skill = skills.text((pack.row("integrate") or {}).get("skills") or [])
            except LookupError as e:
                raise Invalid(f"the integrate skill could not be read: {e}") from e
            prompt = integrate.build_prompt(
                skill=skill,
                unit=unit,
                branch=branch,
                pr=pr,
                state=info["state"],
                reason=info.get("reason", ""),
                head_before=head_before,
                origin_sha=origin_sha,
                rel=rel,
                units_root=units_root,
                own_paths=own,
                refused_update=refused_update,
                completion=completion,
                agent=agent,
            )
            model, model_source, effort, effort_source = self.config_for("integrate")
            # What opened this session, for *Integrate for a conflict*.
            start = {"head": head_before, "pointed": list(own), "integrate_state": info["state"]}
        else:
            prompt, model = "", resume.get("model")
            effort = was.get("effort")
            # Where they came from is in the owner.
            model_source = effort_source = ""
        # What Gebo says needs a person is the object it hands back, not its words.
        collector = submit_mod.Collector("integrate")
        reply: list[str] = []
        # What the session's end reads of GitHub and the tree: the outcome and why.
        seen_after: dict[str, Any] = {"head_now": head_before, "outcome": "failed", "details": []}

        async def finish(got: Run) -> Mapping[str, Any]:
            seen_after.update(
                await self._after_gebo(got, tree, pr, branch, head_before, completion, collector)
            )
            if seen_after["outcome"] in ("pushed", "needs-person"):
                got.status = "done"
            elif got.status == "done":
                got.status = "failed"
            got.detail = "; ".join(seen_after["details"])
            return {}

        stream = run_mod.run(
            run_mod.Agent(
                "integrate",
                row,
                model=model,
                effort=effort,
                sources=config_sources(ceilings, model_source, effort_source, was),
                name=name,
                settings=agents.settings_json(agent) if agent is not None else None,
                preset=True,
                system=str((pack.row("integrate") or {}).get(pack.BODY) or ""),
            ),
            run_mod.Input(
                str(tree),
                prompt,
                key,
                workspace_dir=cwd,
                unit=unit,
                started_by=seen["started_by"],
                start=start,
                # All `resume_integration` needs to take this session up again, no git read.
                owner={
                    "pr": pr,
                    "tree": str(tree),
                    "branch": branch,
                    "head_before": head_before,
                    "origin_sha": origin_sha,
                    "seen": seen,
                    "refused_update": refused_update,
                    "completion": completion,
                    "rel": rel,
                },
                # Gebo's grant: it writes only its `tree` (its `cwd`) and pushes only `branch`,
                # with a lease bound to its head.
                branch=branch,
                lease=head_before,
                channel=collector,
                resume=resume,
            ),
            ctx=run_mod.Ctx(self.sessions, journal, self.config.data_dir, self.steps.identity()),
            finish=finish,
        )
        async for kind, payload in stream:
            if kind == "chunk":
                reply.append(payload)
                yield ("chunk", payload)
        head_now, outcome = seen_after["head_now"], seen_after["outcome"]
        needs_person = integrate.needs_person_of(collector.object())
        rec = write(
            integrate.record(
                workspace=key,
                unit=unit,
                pr=pr,
                mode="agent",
                head_before=head_before,
                head_after=head_now,
                origin_sha=origin_sha,
                outcome=outcome,
                related_=rel,
                report="".join(reply),
                needs_person=needs_person,
                detail="; ".join(seen_after["details"]),
                update_branch=refused_update,
                agent=name,
                **seen,
            )
        )
        yield ("done", {"integration": rec})

    async def _after_gebo(
        self,
        got: Run,
        tree: Path,
        pr: int,
        branch: str,
        head_before: str,
        completion: dict[str, Any] | None,
        collector: submit_mod.Collector,
    ) -> dict[str, Any]:
        """What a Gebo session left, read from GitHub and its tree before its `end`:
        `{head_now, outcome, details}`. A rebase it left open is aborted."""
        failed = got.status in ("failed", "refused") and not got.detail.startswith(NO_SUBMISSION)
        details = [got.detail] if failed and got.detail else []
        try:
            if await gitops.rebase_in_progress(tree):
                await gitops.abort_rebase(tree)
                details.append("the session left a rebase in progress; the app aborted it")
        except GitError as e:
            details.append(f"could not check for a stopped rebase: {e}")
        try:
            head_now = await integrate.pr_head(str(tree), pr)
        except integrate.IntegrateError as e:
            head_now = head_before
            details.append(f"could not read the pull request's head afterwards: {e}")
        needs_person = integrate.needs_person_of(collector.object())
        outcome = integrate.outcome_of_session(head_before, head_now, needs_person)
        if outcome == "failed" and not submit_mod.submitted(collector):
            details.append("no-submission: the session handed back no result through submit")
        if outcome == "pushed":
            # A head that moved is Gebo's push only if Gebo's tree
            # ends on it. A GitHub rebase finishing late, which the lease then refused Gebo's
            # push over, is not — the tree follows it as on the mechanical road.
            try:
                local_head, _ = await gitops.head_and_branch(tree)
            except GitError as e:
                local_head = ""
                details.append(f"could not read the tree's HEAD afterwards: {e}")
            if local_head != head_now:
                outcome = "failed"
                details.append(
                    f"the pull request's head moved to {head_now[:7]}, but this session's tree is at "
                    f"{local_head[:7] or 'an unread HEAD'}, so the push was not this session's"
                )
                try:
                    await gitops.reset_branch_to(tree, branch, local_head, head_now)
                    details.append(f"the local branch was moved to {head_now[:7]}")
                except GitError as e:
                    details.append(f"the local branch was not moved: {e}")
        # The completion road pushes the local head as it was, or nothing.
        if completion is not None and outcome == "pushed" and head_now != completion["local_head"]:
            outcome = "failed"
            details.append(
                f"the pull request's head moved to {head_now[:7]}, not to the local head "
                f"{str(completion['local_head'])[:7]} this completion was to push"
            )
        return {"head_now": head_now, "outcome": outcome, "details": details}

    async def _related(
        self, root: Path, unit: str, data: dict[str, Any], head: str, origin_sha: str
    ) -> dict[str, list[dict[str, Any]]]:
        """From git. A failure leaves a list empty rather than stopping the step."""
        try:
            base = await gitops.merge_base_of(root, head, origin_sha)
            mine = await gitops.files_between(root, base, head)
            commits = []
            for c in await gitops.commits_between(root, base, origin_sha):
                commits.append({**c, "files": await gitops.files_of_commit(root, c["sha"])})
        except GitError:
            return {"merged": [], "open": []}
        try:
            prs = await integrate.open_prs(str(root))
        except integrate.IntegrateError:
            prs = []
        heads = {r.get("number"): str(r.get("headRefOid") or "") for r in prs}
        others = []
        for u in data["units"]:
            if u["name"] == unit or not u.get("between_pr_and_ship") or not u.get("pr"):
                continue
            other_head = heads.get(u["pr"].get("number"))
            if other_head is None:
                continue
            files = None
            try:
                if await gitops.has_commit(root, other_head):
                    their_base = await gitops.merge_base_of(root, other_head, origin_sha)
                    files = await gitops.files_between(root, their_base, other_head)
            except GitError:
                files = None
            others.append({"unit": u["name"], "files": files})
        return integrate.related(commits, mine, data["units"], others, unit)

    async def cleanup(self, cwd: str, unit: str) -> dict[str, Any]:
        """After a `ship` step. Never raises; says what it did or why not."""
        try:
            data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
        except Unavailable as e:
            return {"removed": False, "reason": str(e)}
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            return {"removed": False, "reason": "unit not on the board"}
        return await worktrees.remove_if_finished(cwd, unit, found, self.config.data_dir)

    def enqueue_integration(self, cwd: str, unit: str) -> int:
        """The autopilot's integration: `integrate`'s first half with no reader, as
        `Steps.enqueue_step`."""
        if not unit:
            raise Invalid("name a work unit")
        self.ws.check(cwd)
        self.ws.unit_dir(cwd, unit)
        return self.steps.enqueue(cwd, unit, "integration", "integrate")

    def pr_machine(self) -> prmachine.Machine:
        """The PR machine over the same history and run log as every other transition."""
        meta = self.ws.unit_meta()
        return prmachine.Machine(
            meta.history,
            self.ws.journal() or Journal(meta.root, self.config.data_dir),
            bus=self.bus,
        )

    async def mechanical(
        self,
        cwd: str,
        key: str,
        unit: str,
        stage: str,
        tree: dict[str, Any] | None,
        started_by: str,
        again: bool = False,
        rebased: dict[str, str] | None = None,
        process: str | None = None,
        passed: Sequence[Mapping[str, str]] = (),
    ) -> dict[str, Any]:
        """One `open-pr` or `merge` state, with no session, no `start` and no `end` row;
        what it did is its transitions and, for `ship`, the `ship` row notices read.
        Returns the `done` item a session's step would have ended with. `rebased` is the
        `ship` gate's clean-rebase read, which guard `ship-ready` takes; `passed` its findings
        that did not block, which `ship` turns into proposals."""
        if tree is None:
            raise Invalid(
                f"{stage} needs the unit's git worktree, and this workspace is not a git repository"
            )
        work = Path(tree["path"])
        try:
            expected = await in_thread(
                units.branch_name, cwd, unit, self.config.data_dir, self.ws.snapshot(cwd, [unit])
            )
            branch = await gitops.current_branch(work)
        except (CannotCreate, BadUnit, GitError) as e:
            raise Invalid(str(e)) from e
        u = prmachine.Unit(
            key,
            unit,
            self.ws.unit_dir(cwd, unit),
            str(work),
            branch,
            expected,
            expected.partition("/")[0] or None,
        )
        machine = self.pr_machine()
        if states.action_of(process, stage) == "open-pr":
            out = await machine.open_pr(u, again=again)
        else:
            out = await machine.ship(
                u,
                authority="code" if started_by == "autopilot" else "person",
                rebased=rebased,
                passed=passed,
            )
        artifact = f"{stage}.md"
        done: dict[str, Any] = {
            "unit": unit,
            "stage": stage,
            "outcome": "done" if out.ok else "failed",
            "artifact": artifact if out.ok else None,
            "session_id": None,
            "included": [],
            "error": out.detail or ", ".join(out.reasons),
            "cost": {},
            "model": None,
            "model_source": None,
            "mechanical": out.as_dict(),
        }
        if states.action_of(process, stage) == "open-pr" and out.ok:
            # The title and body the app wrote go onto a pull request it
            # found open rather than created, and the scope is read once, as after a session.
            done["pr_sync"] = await self.steps.sync_pr(
                cwd, unit, out.url if out.result in ("found", "already") else ""
            )
        refused = False
        if states.action_of(process, stage) == "merge":
            merged = out.result in ("merged", "recorded", "already")
            refused = (
                not merged
                and prmachine.state(machine.history, key, unit)["state"] == "merge-requested"
            )
            if merged or refused:
                cleanup = await self.shipped(cwd, key, unit, "shipped" if merged else "refused")
                if merged:
                    done["cleanup"] = cleanup
        # With no `end`, this is what the autopilot's stop `e` reads as the unit's last word, so
        # a `pr` or `ship` that failed or was refused stops it for a person as a failed session
        # did, and one that did its work lifts that stop. `merge_refused` is a merge GitHub
        # refused after the machine requested it.
        journal = self.ws.journal()
        if journal is not None:
            try:
                journal.append(
                    {
                        "kind": prmachine.RECORD_KIND,
                        "workspace": key,
                        "unit": unit,
                        "stage": stage,
                        "outcome": done["outcome"],
                        "result": out.result,
                        "reasons": list(out.reasons),
                        "detail": out.detail,
                        "started_by": started_by,
                        "merge_refused": refused,
                    }
                )
            except BadRecord, Busy:
                pass
        return done

    async def shipped(self, cwd: str, key: str, unit: str, result: str) -> dict[str, Any] | None:
        """The `merge` record notices read, and after a merge the cleanup. From `mechanical`, and from the PR reader and the start-up reconcile
        when they record a merge no step follows. Never raises; the cleanup's answer after a merge, else `None`."""
        journal = self.ws.journal()
        if journal is not None:
            try:
                journal.append(
                    {
                        "kind": MERGE_RECORD,
                        "workspace": key,
                        "unit": unit,
                        "stage": states.states_where(action="merge")[0],
                        "result": result,
                    }
                )
            except BadRecord, Busy:
                pass
        if result != "shipped":
            return None
        cleanup = await self.cleanup(cwd, unit)
        return cleanup

    async def reconcile_prs(self) -> list[dict[str, Any]]:
        """Every unit left at `merge-requested` is read once from GitHub; a merged one is recorded and none is
        merged. Never raises: a start-up that cannot read goes on as it would have."""
        try:
            machine = self.pr_machine()
            with machine.history.data.connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT workspace, unit FROM transitions WHERE root = ? AND guard = 'ship-ready' "
                    "AND artifact = ?",
                    (str(machine.history.working_dir), prmachine.SHIP_FILE),
                ).fetchall()
            pending = []
            for r in rows:
                ws, unit = r["workspace"], r["unit"]
                if prmachine.state(machine.history, ws, unit)["state"] != "merge-requested":
                    continue
                tree = worktrees.path(ws, unit, self.config.data_dir)
                pending.append(
                    prmachine.Unit(
                        ws,
                        unit,
                        self.ws.unit_dir(ws, unit),
                        str(tree if tree.is_dir() else ws),
                        "",
                        "",
                        None,
                    )
                )
            done = []
            for u in pending:
                for o in await machine.reconcile([u]):
                    # The journal key is the resolved path, so it is the cwd.
                    if o.result == "recorded":
                        await self.shipped(u.workspace, u.workspace, u.name, "shipped")
                    done.append(o.as_dict())
            return done
        except Exception as e:
            # A start-up is never stopped by this.
            log.exception("the pull requests could not be reconciled")
            return [{"result": "failed", "detail": str(e) or type(e).__name__}]

    def resume(self, record: dict[str, Any]) -> Any:
        """Gebo, with the lease, the grant and the press's facts its owner kept. The unit is
        claimed now; the returned coroutine runs the session and what follows it."""
        owner = record["owner"]
        key, cwd, unit = str(owner["workspace"]), str(owner["workspace_dir"]), str(owner["unit"])
        journal = self.ws.journal()
        if journal is None:
            raise Invalid("no working folder is set, so nothing can be taken up")
        attempt = self.steps.adopt("integration", key, unit, "integrate")
        self.holds.attempts.set_road(attempt, "gebo")
        running = steps_mod.Running(
            workspace=key, unit=unit, stage="integrate", started_at=_now(), attempt=attempt, cwd=cwd
        )
        self.steps.tasks[attempt] = running

        def write(rec: dict[str, Any]) -> dict[str, Any]:
            try:
                return journal.append(rec)
            except BadRecord, Busy:
                return rec

        async def go() -> None:
            running.task = asyncio.current_task()
            outcome = "failed"
            try:
                async for _ in self.integrate_gebo(
                    cwd,
                    key,
                    unit,
                    None,
                    None,
                    None,
                    int(owner["pr"]),
                    Path(owner["tree"]),
                    str(owner["branch"]),
                    str(owner["head_before"]),
                    str(owner["origin_sha"]),
                    journal,
                    write,
                    dict(owner.get("seen") or {}),
                    owner.get("refused_update"),
                    completion=owner.get("completion"),
                    resume=record,
                ):
                    pass
                outcome = "done"
            except asyncio.CancelledError:
                # The app going down: the next start ends the attempt.
                self.steps.tasks.pop(attempt, None)
                raise
            finally:
                if self.steps.tasks.pop(attempt, None) is not None:
                    self.steps.end_attempt(attempt, outcome)

        return go()
