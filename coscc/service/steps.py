"""Integrating a branch with `main`, and running, driving and stopping one step of a unit."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator

from coscc.units import autopilot, backlog
from coscc.units import board as board_reader
from coscc.runlog import events
from coscc.git import drift, fetches, gitops
from coscc.agent import agents, harness, modeltrial
from coscc.hooks import Facts, Hooks, facts as facts_of
from coscc.units import submit as submit_mod
from coscc.github import integrate, prmachine
from coscc.units import planmap, retake
from coscc.units.board import Unavailable
from coscc.data import Data, now as _now
from coscc.git.gitops import GitError
from coscc.runlog.journal import BadRecord, Journal
from coscc.data import Busy
from coscc.agent.policy import grant_for
from coscc.runner.reply import RunError
from coscc.runner.step import Runner, check_started_by
from coscc.runner.prompt import answers_section
from coscc.runner.attempt import describe_attempt
from coscc.agent import steps as steps_mod
from coscc.agent.sessions import Suspended
from coscc import units
from coscc.units import worktrees
from coscc.units import BadUnit, CannotCreate
from coscc.service.update import refuse_while_updating, refuse_mechanical_while_updating
from coscc.service.common import (
    open_prs_once,
    BRANCH_REMOTE,
    BRANCH_TRUNK,
    CONSEQUENCE,
    Invalid,
    OWNER,
    Refused,
    _younger_than,
    describe_base,
    step_cwd,
)
from coscc.bus import Bus, Event
from coscc.config import Config
from coscc.service.workspaces import Workspaces
from coscc.service.common import Holds
from coscc.agent.sessions import Sessions
from coscc.update.updater import Updater
from coscc.service.agents import Agents
from coscc.service.models import Models
from coscc.service.ideas import Ideas
from coscc.service.answers import Answers
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


# The longest note a rerun takes, in characters. Chosen, not measured.
RERUN_NOTE_MAX = 4000

# What the page says when a retake of the screenshots refuses `review`: one
# sentence, no commit, path or log line. The rest is in the `screens` record.
RETAKE_REFUSED = "The screenshots could not be taken again after the branch was rewritten, so review did not start."


def _answers_kept(path: Path, before: bytes) -> bool:
    """Whether `path` still ends with the `## Answers` section it had, `before`,
    byte for byte. A `pr` step writes `pr.md` itself, and an `impl` step
    `impl.md`, so nothing else guards that section."""
    try:
        return path.read_bytes().endswith(before)
    except OSError:
        return False


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
            and rec.get("stage") == "review"
            and rec.get("outcome") == "done"
        ):
            found = None
    return found


def _gate_reasons(answer: board_reader.Gate) -> tuple[str, ...]:
    """The codes that go with the gate's words, so no reader downstream parses these."""
    return tuple(getattr(answer, "reasons", ()))


def _rounds_before(found: dict[str, Any], row: dict[str, Any]) -> set[Any] | None:
    """The rounds `review.md` held before this step, so that the ones it adds can be told apart
    afterwards. Taken from the board already read; None for any other artifact."""
    if row["file"] != "review.md":
        return None
    return {r.get("n") for r in found.get("rounds") or []}


def _round_kwargs(
    found: dict[str, Any], row: dict[str, Any], stage: str, rounds_before: set[Any] | None
) -> dict[str, Any]:
    """From the same board: a last round `cos.mjs` read as unfinished, and the ids it dropped,
    for the review that runs again. Whether it counts is not asked here."""
    last_round = (found.get("rounds") or [None])[-1] if row["file"] == "review.md" else None
    kw: dict[str, Any] = (
        {"unfinished_round": {"n": last_round["n"], "dropped": list(last_round["dropped"])}}
        if last_round and last_round.get("unfinished")
        else {}
    )
    # The findings the last round left open, which an `impl` may claim only a
    # person can close: guard `impl-claim` reads them when its object arrives.
    if rounds_before:
        kw["rounds_known"] = tuple(sorted(n for n in rounds_before if isinstance(n, int)))
    if stage in ("impl", "implement") and found.get("rounds"):
        last = found["rounds"][-1]
        kw.update(open_findings=tuple(last.get("open_ids") or ()), claims_round=last.get("n"))
    return kw


async def _plan_drift(
    journal: Journal,
    key: str,
    unit: str,
    stage: str,
    directory: Path,
    tree: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Which files the plan names `main` changed since the plan ran, for `impl`
    only. Unlike `failed_attempts`, nothing here may refuse the step: a busy run log, an
    unreadable `plan.md` or a bug in `drift.py` is "could not check"."""
    if stage not in ("impl", "implement"):
        return None
    try:
        return await drift.compute(
            journal.records(key, unit),
            (directory / "plan.md").read_text(encoding="utf-8"),
            tree["path"] if tree else None,
        )
    except Exception as e:
        # Recorded as the reason.
        log.exception("the plan drift of %s could not be read", unit)
        return {
            "plan_sha": None,
            "main_sha": None,
            "files": None,
            "checked": False,
            "reason": str(e) or type(e).__name__,
        }


def _shortlist(journal: Journal, key: str, unit: str) -> dict[str, Any]:
    """Where the unit stood in the shortlist in effect as it started, for the
    outcome's measurement. Like the plan drift, nothing here may refuse the step."""
    try:
        return backlog.stamp(journal.records(key, kind="shortlist"), unit)
    except Exception as e:
        # Recorded as the reason.
        log.exception("the shortlist rank of %s could not be read", unit)
        return {"rank": None, "of": None, "record": None, "error": str(e) or type(e).__name__}


def _answers_before(stage: str, directory: Path, row: dict[str, Any]) -> bytes | None:
    """The `## Answers` an `impl` step finds in its artifact. Read after the last refusal
    that reads nothing more, before any money is spent: every `impl` step writes `impl.md`
    itself, so only a comparison afterwards can tell whether its `## Answers` survived."""
    if stage != "impl":
        return None
    try:
        return answers_section((directory / row["file"]).read_bytes())
    except OSError:
        return None


# Seconds a held CI answer is trusted before a board read asks `gh` again, in the
# background. Chosen, not measured.
CI_REFRESH = 60.0


class Steps:
    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        holds: Holds,
        sessions: Sessions,
        updater: Updater,
        agents: Agents,
        models: Models,
        ideas: Ideas,
        answers: Answers,
        bus: Bus,
    ) -> None:
        self.config = config
        self.ws = ws
        self.holds = holds
        self.sessions = sessions
        self.updater = updater
        self.agents = agents
        self.models = models
        self.ideas = ideas
        self.answers = answers
        self.bus = bus
        self.hooks = Hooks()
        # Held across one retake of a unit's screenshots, app-wide: every capture binds
        # `127.0.0.1:18783`, so two at once fail. A capture a session runs does not take it.
        self._screens_lock = asyncio.Lock()
        # The retake running now, if any, `{workspace, unit, started}`; read only by `_update_waited`.
        self.retakes: dict[str, dict[str, Any]] = {}
        # One lock per workspace held across an integration's check-and-mark.
        self._integrate_locks: dict[str, asyncio.Lock] = {}
        # By `(journal key, unit)`: the last answer of `integrate.required_checks`,
        # `{head, checks | error, at}`, and the one background ask running for it. Never waited
        # on by a board read.
        self.ci: dict[tuple[str, str], dict[str, Any]] = {}
        self.ci_asks: dict[tuple[str, str], asyncio.Task] = {}
        # Board steps running now, each its own task, so a departing reader does not take the
        # step with it and a Stop has something to cancel.
        self.registry = steps_mod.Registry()
        # The recorder of every running board step, by `run`: what `events_page` reads and
        # `follow_events` subscribes to. A step leaves it when `drive` ends; then the tables answer.
        self.recorders: dict[str, events.Recorder] = {}

    # -- integration -------------------------------------------------

    async def attach_integration(
        self,
        cwd: str,
        units_: list[dict[str, Any]],
        journal: Journal | None,
        key: str,
        prs_once=None,
    ) -> list[tuple[tuple[str, str], str, int, str]]:
        """Give every unit `integration: {...}` when it sits in the window, else None.

        **Reads only.** One `gh pr list` for the workspace (up to `gh.TIMEOUT`),
        `git` counts against the `origin/main` the last fetch brought — no fetch here — and
        `gh pr checks` only for a unit whose head is the one its last integration pushed.
        Nothing here writes a record, calls `update-branch` or opens a session.

        Also gives each unit in the window `ci_held`, the held CI answer when it
        is for the head `gh pr list` just returned, and returns the CI asks `board` starts
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
            info = await self._integration_of(root, u, prs, last.get(u["name"]))
            if info is not None:
                u["integration"] = info
            number = (u.get("pr") or {}).get("number")
            row = (
                next((r for r in prs if r.get("number") == number), None)
                if isinstance(prs, list)
                else None
            )
            if row is None or number is None:
                continue
            slot, head = (key, u["name"]), str(row.get("headRefOid") or "")
            held = self._held_ci(key, u["name"], int(number), head)
            if held is not None and held.get("head") == head:
                u["ci_held"] = held
            if slot in self.ci_asks:
                continue
            if (
                held is None
                or held.get("head") != head
                or not _younger_than(held.get("at") or "", oldest)
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

    def ask_ci(self, asks: list[tuple[tuple[str, str], str, int, str]]) -> None:
        """One background `gh pr checks` per ask, none awaited. Its
        answer is recorded by `prmachine.record_ci`, through `ci-at-head`, on the unit's
        `pull_requests` row; `gh`'s error is held in `ci` with the time it was read, so it
        is not asked again before `CI_REFRESH` either."""
        for slot, tree, number, head in asks:
            if slot in self.ci_asks:
                continue

            async def ask(slot=slot, tree=tree, number=number, head=head) -> None:
                try:
                    checks = await integrate.required_checks(tree, number)
                except integrate.IntegrateError as e:
                    self.ci[slot] = {"head": head, "error": str(e), "at": _now()}
                    return
                u = prmachine.Unit(slot[0], slot[1], Path(tree), tree, "", "", None)
                try:
                    self.pr_machine().record_ci(
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

            task = asyncio.get_running_loop().create_task(ask())
            self.ci_asks[slot] = task
            # Removed however it ends — cancelled included — or the unit is never asked again.
            task.add_done_callback(
                lambda t, slot=slot: (
                    self.ci_asks.pop(slot, None) if self.ci_asks.get(slot) is t else None
                )
            )

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
        u: dict[str, Any],
        prs: list[dict[str, Any]] | str,
        last_record: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """One unit's state. None when its pull request is not among the open ones."""
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
        if isinstance(pr_row, dict):
            try:
                origin_sha = await gitops.rev_parse(root, "refs/remotes/origin/main")
                head = str(pr_row.get("headRefOid") or "")
                if not await gitops.has_commit(root, head):
                    missing = (
                        f"the pull request's head {head[:7]} is not here: fetch, then ask again"
                    )
                else:
                    missing = await gitops.count_missing(root, head, origin_sha)
            except GitError as e:
                missing = str(e)
        checks: list[dict[str, Any]] | str | None = None
        if number is not None and integrate.needs_checks(pr_row, last_record):
            try:
                checks = await integrate.required_checks(str(root), int(number))
            except integrate.IntegrateError as e:
                checks = str(e)
        verdict = integrate.classify(pr_row, missing, origin_sha, last_record, checks)
        state = verdict["state"]
        review_status = next(
            (r.get("status") or "" for r in u.get("stages") or [] if r.get("stage") == "review"), ""
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
                grant_for("integrate").warning,
                fallback=fallback,
                name=(self.agents.agent("integrate") or {}).get("name", ""),
            ),
            "consequence": CONSEQUENCE["integrate"],
        }

    async def integrate(  # noqa: C901, PLR0915 - still to split
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
        """
        try:
            check_started_by(started_by)
        except ValueError as e:
            raise Invalid(str(e)) from e
        self.ws.check(cwd)
        refuse_while_updating(self.updater)
        journal = self.ws.journal()
        if journal is None:
            raise Refused(
                "no working folder is set, so an integration cannot be recorded — set COS_WORKING_DIR",
                ("no-run-log",),
            )
        if not unit:
            raise Invalid("name a work unit")
        directory = self.ws.unit_dir(cwd, unit)
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
            try:
                seen["fetch"] = await fetches.fetch(root, BRANCH_REMOTE, BRANCH_TRUNK)
            except GitError as e:
                seen["fetch"] = {"outcome": "failed", "detail": str(e)}
            try:
                prs: list[dict[str, Any]] | str = await integrate.open_prs(str(root))
            except integrate.IntegrateError as e:
                prs = str(e)
            info = await self._integration_of(root, found, prs, last)
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

        lock = self._integrate_locks.setdefault(key, asyncio.Lock())
        async with lock:  # noqa: PLR1702 - still to split
            busy = self.holds.busy(key, unit)
            cut = None
            if not busy and tree is not None:
                try:
                    here = any(
                        e["workspace"] == key and e["unit"] == unit
                        for e in self.holds.running.values()
                    )
                    cut = integrate.cut_integration(journal.records(key, unit=unit), unit, here)
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
            reason = integrate.refusal(
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
                        **seen,
                    )
                )
                raise Invalid(reason)
            if state == "behind" and how not in integrate.COMPLETION:
                # An Apply waits for a mechanical integration, so none begins once
                # one is pressed.
                refuse_mechanical_while_updating(self.updater)
            mark = self.holds.take(key, unit, "integrate")
            # Commits never pushed go to Gebo whatever the state.
            completing = how in integrate.COMPLETION
            # Gebo shows as running under its agent name; a mechanical rebase has no agent and
            # shows as rebasing. The same condition as below.
            rid = self.holds.mark_running(
                key, unit, "integrate", "rebase" if state == "behind" and not completing else "gebo"
            )
        try:
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
                self.holds.running[rid]["kind"] = "gebo"
                # An update waits for a mechanical integration, and a Gebo session is
                # paused instead, so one waiting on this can go ahead.
                self.bus.publish(Event("integration.escalated", key, unit))
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
        finally:
            self.holds.release(key, unit, mark)
            self.holds.running.pop(rid, None)
            self.bus.publish(Event("integration.ended", key, unit))

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

    async def integrate_gebo(  # noqa: PLR0915 - still to split
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
        agent = self.agents.agent("integrate")
        grant = grant_for("integrate")
        name = agent["name"] if agent is not None else ""
        start_at = was.get("start_at")
        if resume is None:
            assert directory is not None
            assert info is not None
            # By path; Gebo reads what it needs of them (`integrate.read_paths`).
            own = {}
            for artifact in ("intent.md", "spec.md", "plan.md", "impl.md"):
                path = Path(directory).resolve() / artifact
                if path.exists():
                    own[artifact] = path
            try:
                skill = harness.read_skill("integrate")
            except harness.MissingRules as e:
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
            model, model_source = self.models.model_for("impl")
            app = self.app_identity()
            try:
                start_at = journal.started(
                    key,
                    unit,
                    "integrate",
                    "manual",
                    started_by=seen["started_by"],
                    prompt_chars=len(prompt),
                    granted=list(grant.tools),
                    max_turns=grant.max_turns,
                    head=head_before,
                    model=model,
                    model_source=model_source,
                    pointed=list(own),
                    app_version=app["version"],
                    app_commit=app["commit"],
                    # What opened this session, for *Integrate for a conflict*.
                    integrate_state=info["state"],
                    **({"agent": name} if name else {}),
                ).get("at")
            except BadRecord, Busy:
                pass
        else:
            prompt, model = str(resume.get("message") or ""), resume.get("model")
        # All `resume_integration` needs to take this session up again, no git read.
        owner = {
            "kind": "integrate",
            "workspace": key,
            "workspace_dir": cwd,
            "unit": unit,
            "stage": "integrate",
            "start_at": start_at,
            "max_turns": grant.max_turns,
            "max_budget_usd": grant.max_budget_usd,
            "pr": pr,
            "tree": str(tree),
            "branch": branch,
            "head_before": head_before,
            "origin_sha": origin_sha,
            "seen": seen,
            "refused_update": refused_update,
            "completion": completion,
            "rel": rel,
        }
        end: dict[str, Any] = {}
        failure = ""
        # What Gebo says needs a person is the object it hands back, not its words.
        collector = submit_mod.Collector("integrate")
        try:
            async for kind, payload in integrate.run_gebo(
                self.sessions,
                tree=str(tree),
                workspace=cwd,
                prompt=prompt,
                grant=grant,
                read_also=integrate.read_paths(units_root, unit, rel),
                lease=(branch, head_before),
                model=model,
                settings=agents.settings_json(agent) if agent is not None else None,
                owner=owner,
                resume=resume,
                channel=collector,
            ):
                if kind == "chunk":
                    yield ("chunk", payload)
                else:
                    end = payload
        except Suspended:
            # Paused by an update, with its `suspend` row. No `end` and no record.
            raise
        except Exception as e:
            # Recorded, never swallowed silently.
            log.exception("the integration session of %s failed", unit)
            failure = f"the session failed: {e}"
        details = [failure] if failure else []
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
        reply = str(end.get("reply") or "")
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
        try:
            journal.finished(
                key,
                unit,
                "integrate",
                "done" if outcome in ("pushed", "needs-person") else "failed",
                session_id=end.get("session_id", ""),
                detail="; ".join(details) or None,
                denials=end.get("denials", 0),
                denied=end.get("denied"),
                background=end.get("background", 0),
                models_used=end.get("models_used") or None,
                **(end.get("cost") or {}),
            )
        except BadRecord, Busy:
            pass
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
                report=reply,
                needs_person=needs_person,
                detail="; ".join(details),
                update_branch=refused_update,
                agent=name,
                **seen,
            )
        )
        yield ("done", {"integration": rec})

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

    async def after_end(self, cwd: str, unit: str, stage: str, key: str) -> None:
        """After a step's `done` and its `end`: a `questions` record when the unit is left with
        open questions, and after `ship` a `ship` record saying whether it merged. Never raises,
        like `cleanup`: a record that cannot be written changes nothing about the step.

        `why` is `decide`'s, read off the files by `cos.mjs status` as `board.read` copies it,
        without asking `gh` as `next` would, so `ship-refused` can also be a merge whose branch
        deletion failed."""
        try:
            journal = self.ws.journal()
            if journal is None:
                return
            data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                return
            asked = autopilot.open_questions(found)
            if asked:
                journal.append(
                    {
                        "kind": "questions",
                        "workspace": key,
                        "unit": unit,
                        "stage": stage,
                        "questions": [
                            {"artifact": q.get("artifact"), "n": q.get("n")} for q in asked
                        ],
                    }
                )
            result = {"finished": "shipped", "ship-refused": "refused"}.get(
                str(found.get("why") or "")
            )
            if stage == "ship" and result:
                journal.append(
                    {
                        "kind": "ship",
                        "workspace": key,
                        "unit": unit,
                        "stage": "ship",
                        "result": result,
                    }
                )
        except Exception:
            # `Unavailable`, `BadRecord`, `Busy` included.
            log.exception("what follows the %s of %s was not done", stage, unit)
            return

    async def next_step(self, cwd: str, unit: str) -> dict[str, Any]:
        """The one stage the run button may offer, and why -- `cos.mjs next`'s answer.

        Read with the same store and the same `repo=cwd` that `run_step` hands the gate, so
        the stage offered and the gate that will be asked read one checkout. Nothing here chooses a stage.
        """
        self.ws.check(cwd)
        if not unit:
            raise Invalid("name a work unit")
        self.ws.unit_dir(cwd, unit)
        # Asked first with no `--repo`, which reads files only: a held unit is
        # answered here, before `worktree` could reopen the tree a drop just removed.
        try:
            held = await board_reader.next_step(
                self.ws.units_root(cwd), unit, repo=None, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        if held.get("hold"):
            return {
                "cwd": cwd,
                "unit": unit,
                **{k: held[k] for k in ("stage", "action", "blocked")},
                "waiting": [],
                "dropped": [],
                "hold": held["hold"],
                "reasons": list(held.get("reasons") or []),
            }
        # The unit's worktree is the checkout its branch and pull request are read
        # from. None when there is none to open, and `cos.mjs` then keeps `review` and
        # `ship` closed rather than read the workspace's branch, which is not this unit's.
        # A workspace that is not a git repository has no worktrees, and is read as it
        # always was — the same fallback `run_step` takes, so the two read one checkout.
        if (Path(cwd).expanduser().resolve() / ".git").exists():
            tree = await self.answers.worktree(cwd, unit)
            repo = tree["path"] if tree else None
        else:
            repo = cwd
        try:
            found = await board_reader.next_step(
                self.ws.units_root(cwd), unit, repo=repo, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        return {
            "cwd": cwd,
            "unit": unit,
            **{k: found[k] for k in ("stage", "action", "blocked")},
            # The findings a person is awaited on, copied from `cos.mjs next`.
            "waiting": list(found.get("waiting") or []),
            # The ids the last review round left out, copied from `cos.mjs next`.
            "dropped": list(found.get("dropped") or []),
            # The stage a fully answered draft would run again; only the autopilot
            # reads it.
            "rerun": str(found.get("rerun") or ""),
            # The codes the autopilot branches on, copied from `cos.mjs next`.
            "reasons": list(found.get("reasons") or []),
        }

    async def rerun_offers(self, cwd: str, unit: str) -> dict[str, Any]:
        """The accepted stages `unit` may run again, each with the stages that
        then run again after it -- `cos.mjs rerun`'s answer, copied: `{unit, offers: [{stage,
        later}], why}`. Files only: no worktree is opened and no `gh` is asked. Nothing here
        chooses a stage."""
        self.ws.check(cwd)
        if not unit:
            raise Invalid("name a work unit")
        self.ws.unit_dir(cwd, unit)
        try:
            found = await board_reader.rerun(
                self.ws.units_root(cwd), unit, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if "error" in found:
            raise Invalid(str(found["error"]))
        return found

    async def set_mode(self, cwd: str, unit: str, stage: str, mode: str) -> dict[str, Any]:
        """Choose how one step runs. Validated against the board, not against a second list."""
        self.ws.check(cwd)
        journal = self.ws.journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a mode cannot be recorded — set COS_WORKING_DIR"
            )

        try:
            data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e

        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Invalid(f"no such work unit in this workspace: {unit}")
        if stage not in data["stages"]:
            raise Invalid(f"no such stage: {stage} (use one of {', '.join(data['stages'])})")

        try:
            journal.set_mode(self.ws.key(cwd), unit, stage, mode)
        except BadRecord as e:
            raise Invalid(str(e)) from e
        except Busy as e:
            raise Invalid(str(e)) from e
        return {"cwd": cwd, "unit": unit, "stage": stage, "mode": mode}

    async def run_step(
        self,
        cwd: str,
        unit: str,
        stage: str,
        started_by: str = "person",
        rerun: bool = False,
        note: str = "",
    ) -> AsyncIterator[tuple[str, Any]]:
        """Run one step of one unit, streaming the reply as it arrives.

        Everything this needs — the stage order, the artifact filename, the mode — comes
        from one board read, so a step cannot run against a different idea of the unit
        than the one the page is showing.

        `started_by` is `autopilot` only when the autopilot calls this; no route
        passes it, so a request cannot say it is the autopilot.

        `rerun` runs an accepted stage again, with a person's `note`. Whether the
        stage may, and the `### Rerun` block appended to `intent.md` before the session
        starts, are `cos.mjs rerun`'s. Refused for the autopilot and for a note over
        `RERUN_NOTE_MAX`; an empty note is not refused.
        """
        try:
            check_started_by(started_by)
        except ValueError as e:
            raise Invalid(str(e)) from e
        self.ws.check(cwd)
        refuse_while_updating(self.updater)
        journal = self.ws.journal()
        if journal is None:
            raise Refused(
                "no working folder is set, so a run cannot be recorded — set COS_WORKING_DIR",
                ("no-run-log",),
            )

        # The unit is held from here, before the first `await`: a second request
        # for any stage of it is refused before it reads the board, opens a worktree, runs
        # the gate or fetches. Until the step is handed to `drive` the mark is this frame's
        # to return, on every road out: a refusal, an exception, or a cancel when the client
        # goes away.
        key = self.ws.key(cwd)
        mark = self.holds.take(key, unit, "step", stage)
        handed = False
        running: steps_mod.Running | None = None
        rid: str | None = None
        try:
            # Refuse, or find what the step runs on: nothing is spent until the gate is open.
            data, found, row = await self._find_stage(cwd, unit, stage)
            note = str(note or "").strip()
            rerun_block = await self._ask_rerun(cwd, unit, stage, started_by, note) if rerun else ""
            tree, work = await self._open_tree(cwd, unit, stage)
            base = await self._tree_base(cwd, unit, tree)
            answer = await self._ask_gate(cwd, unit, stage, work)
            refusal = self.feature_refusal(
                facts_of(
                    workspace=cwd,
                    workspace_key=key,
                    unit=unit,
                    stage=stage,
                    run="",
                    cwd=work,
                    watch=None,
                    directory=self.ws.unit_dir(cwd, unit),
                    commands=(),
                    resumed=False,
                )
            )
            if refusal:
                raise Refused(refusal, ("feature-refused",))

            # `pr` and `ship` run no session: the PR machine pushes, opens or
            # merges, and records each move through its guard. The mark is this frame's, as for
            # any refusal above, and is given back by the `finally` below.
            if stage in prmachine.STAGES:
                yield (
                    "done",
                    await self._run_mechanical(
                        cwd, key, unit, stage, tree, started_by, rerun, rerun_block, answer
                    ),
                )
                return

            # What the step is handed.
            screens_note = await self._ready_tree(
                cwd, key, journal, unit, stage, tree, work, started_by
            )
            directory = self.ws.unit_dir(cwd, unit)
            rounds_before = _rounds_before(found, row)
            inputs, answers_before = await self._gather_inputs(
                cwd=cwd,
                key=key,
                journal=journal,
                unit=unit,
                stage=stage,
                stages=data["stages"],
                found=found,
                row=row,
                directory=directory,
                tree=tree,
                work=work,
                rounds_before=rounds_before,
            )
            if rerun:
                await self.answers.append_to_answers(
                    directory / "intent.md", "\n" + rerun_block, "a rerun"
                )
            inputs.update(await self._link_kwargs(cwd, unit, stage))
            # Emptied before the step, whatever an earlier one left, and removed
            # after it however it ends -- in `drive`, so a client that drops the stream does
            # not decide when.
            scratch = units.spike_dir(cwd, unit, self.config.data_dir) if stage == "spike" else None
            kwargs = self._step_kwargs(
                cwd=cwd,
                key=key,
                unit=unit,
                stage=stage,
                stages=data["stages"],
                artifact=row["file"],
                directory=directory,
                answer=answer,
                tree=tree,
                work=work,
                base=base,
                scratch=scratch,
                started_by=started_by,
                rerun=rerun,
                note=note,
                screens_note=screens_note,
                rounds_before=rounds_before,
                inputs=inputs,
            )

            # Launch.
            runner = Runner(self.sessions, journal, app=self.app_identity(), hooks=self.hooks)
            # The registry is what the page lists and what a Stop finds; the mark
            # taken above is what everything else asks. The same start time for both, and no
            # `await` between the listing and the phase.
            try:
                running = self.registry.claim(key, unit, stage, started_at=mark.started_at)
            except steps_mod.Busy as e:
                raise Refused(str(e), ("unit-busy",)) from e
            mark.phase = "running"
            rid = self.holds.mark_running(key, unit, stage, "step")
            queue = self._launch(
                running=running,
                mark=mark,
                rid=rid,
                runner=runner,
                cwd=cwd,
                key=key,
                journal=journal,
                unit=unit,
                stage=stage,
                artifact=row["file"],
                directory=directory,
                base=base,
                rounds_before=rounds_before,
                scratch=scratch,
                kwargs=kwargs,
                answers_before=answers_before,
            )
            handed = True
        finally:
            if not handed:
                self._give_back(key, unit, mark, rid, running)
        # Only the reader lives here. A reader that goes away -- a closed
        # tab, a dropped NDJSON client -- takes its queue with it and nothing else: the
        # step runs on to its own end in `drive`. Stopping it is `stop_step`, and only that.
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "raise":
                    raise payload
                yield (kind, payload)
                if kind == "done":
                    return
        finally:
            running.listeners.discard(queue)

    def _give_back(
        self,
        key: str,
        unit: str,
        mark: steps_mod.Mark,
        rid: str | None,
        running: steps_mod.Running | None,
    ) -> None:
        """Return what a step that was not handed to `drive` holds. Past `claim`, the listing
        and the `holds.running` entry are this frame's to return too, or `/api/board/steps`
        keeps a step that never started and the next request gets past the mark to `claim` again."""
        self.holds.release(key, unit, mark)
        if rid is not None:
            self.holds.running.pop(rid, None)
        if running is not None:
            self.registry.release(running)
            self.bus.publish(Event("step.released", key, unit))

    async def _find_stage(
        self, cwd: str, unit: str, stage: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """The board read, the unit in it and its row for `stage`. Refuses a unit or a stage
        that is not there, and a unit that is held."""
        try:
            data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e

        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Refused(f"no such work unit in this workspace: {unit}", ("no-unit",))
        row = next((r for r in found["stages"] if r["stage"] == stage), None)
        if row is None:
            raise Refused(
                f"no such stage: {stage} (use one of {', '.join(data['stages'])})", ("no-stage",)
            )
        # `cos.mjs`'s own field, read before any worktree is opened — the gate
        # below would refuse too, but only after `worktree` had reopened a dropped tree.
        held = found.get("hold")
        if held:
            raise Refused(
                f"{unit} is {held.get('state')}: {held.get('reason')} — nothing runs on it",
                ("held",),
            )
        return data, found, row

    async def _ask_rerun(self, cwd: str, unit: str, stage: str, started_by: str, note: str) -> str:
        """The `### Rerun` block for running `stage` again, asked before a worktree is opened or
        the gate asked. Whether `stage` may run again, and the block that says so, are
        `cos.mjs`'s; its refusal is passed on."""
        if started_by != "person":
            raise Refused(
                "a stage is run again only by a person, from the board, never by the autopilot",
                ("rerun-by-person",),
            )
        if len(note) > RERUN_NOTE_MAX:
            raise Invalid(
                f"the note is {len(note)} characters, over the {RERUN_NOTE_MAX} a rerun takes"
            )
        try:
            asked = await board_reader.rerun(
                self.ws.units_root(cwd), unit, stage, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        if "error" in asked:
            raise Invalid(str(asked["error"]))
        return str(asked.get("block") or "")

    async def _open_tree(
        self, cwd: str, unit: str, stage: str
    ) -> tuple[dict[str, Any] | None, str]:
        """The unit's worktree, and the directory the step runs in.

        Every step runs in the unit's own worktree. A workspace that is not a git
        repository has none, and its steps run where they always did — there is no
        branch there for another unit to take away.
        """
        is_repo = (Path(cwd).expanduser().resolve() / ".git").exists()
        tree = await self.answers.worktree(cwd, unit, strict=True) if is_repo else None
        if is_repo and tree is None:
            try:
                tree = {
                    "path": (await worktrees.ensure(cwd, unit, None, self.config.data_dir))["path"]
                }
            except (GitError, BadUnit) as e:
                raise Refused(
                    f"{unit} has no worktree and one could not be opened: {e}", ("no-worktree",)
                ) from e
        work = tree["path"] if tree else cwd
        # A spike is watched through the worktree's `HEAD` and `git status`;
        # with no git there is nothing to watch, so it does not run at all.
        if stage == "spike" and tree is None:
            raise Refused(
                "spike needs a git worktree to watch, and this workspace is not a git repository",
                ("no-git",),
            )
        return tree, work

    async def _tree_base(
        self, cwd: str, unit: str, tree: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """A tree already on its branch carries whatever `worktree` read when it was opened onto it (or nothing,
        when it was already there before this call); a tree still detached is refreshed
        now, on the spot, because a session about to run on it is about to read it."""
        if tree is None:
            return None
        if tree.get("branch"):
            return tree.get("base")
        return await worktrees.refresh_base(cwd, unit, self.config.data_dir)

    def feature_refusal(self, facts: Facts) -> str:
        """The words of the first feature guard that denies this run, or `""` when all abstain. A
        guard that raises denies: a run is never let through by a check that could not be made."""
        for guard in self.hooks.for_step(facts.stage, facts.workspace).guards:
            try:
                words = guard.check(facts)
            except Exception as e:
                log.exception("guard %s of a feature failed", guard.name)
                return f"{guard.name}: failed ({type(e).__name__})"
            if words is not None:
                return f"{guard.name}: {words}"
        return ""

    async def _ask_gate(self, cwd: str, unit: str, stage: str, work: str) -> board_reader.Gate:
        """`cos.mjs gate` is asked here, not left to the skill: a session often cannot run
        a command. Here rather than in `Runner` because a refusal must arrive before any
        money is spent, and `run_step` is the last place that is still true."""
        # `pr.md`'s title and body go up before the `ship` gate compares the
        # title, so one a person changed on GitHub, or a `pr` step left behind, does not
        # close it. Never raises; when it fails, the gate decides.
        if stage == "ship":
            await self.answers.sync_pr(cwd, unit, None, stage="ship")
        try:
            # `work` is the checkout the `review` and `ship` gates read git and the pull
            # request from. The store has no git to read.
            answer = await board_reader.gate(
                self.ws.units_root(cwd),
                unit,
                stage,
                repo=work,
                state=self.ws.snapshot(cwd, [unit]),
            )
        except Unavailable as e:
            raise Refused(str(e), ("unavailable",)) from e
        allowed, said = answer
        if not allowed:
            raise Refused(said, _gate_reasons(answer))
        return answer

    async def _run_mechanical(
        self,
        cwd: str,
        key: str,
        unit: str,
        stage: str,
        tree: dict[str, Any] | None,
        started_by: str,
        rerun: bool,
        rerun_block: str,
        answer: board_reader.Gate,
    ) -> dict[str, Any]:
        """One `pr` or `ship` through the PR machine, and the `done` item it ends with.
        A `pr` run again has its block appended as any rerun, and the note reaches
        no prompt: the app writes `pr.md` again from the unit's metadata."""
        if rerun:
            await self.answers.append_to_answers(
                self.ws.unit_dir(cwd, unit) / "intent.md", "\n" + rerun_block, "a rerun"
            )
        return await self.mechanical(
            cwd,
            key,
            unit,
            stage,
            tree,
            started_by,
            again=rerun,
            rebased=getattr(answer, "rebased", None),
        )

    async def _ready_tree(
        self,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        stage: str,
        tree: dict[str, Any] | None,
        work: str,
        started_by: str,
    ) -> str:
        """Refuse a tree the step cannot start on, before any money is spent. Returns the
        section the `review` prompt carries about its screenshots, `""` for any other step."""
        if tree is None:
            return ""
        if stage == "impl":
            # A tree that cannot run its tests turns every `impl` red from the start, so
            # the step is not started on one. Tried once more first: a network blip is the
            # ordinary reason, and the page has nothing better to offer than *try again*.
            prepared = worktrees.read_prepare(Path(work)) or {}
            if not prepared.get("ok"):
                prepared = await worktrees.prepare(Path(work), cwd, data_dir=self.config.data_dir)
            if not prepared.get("ok"):
                raise Refused(worktrees.describe_failure(prepared), ("no-worktree",))
        # A UI unit whose branch was rewritten since `impl` took its
        # screenshots has them taken again, here, before any money is spent; a retake that
        # fails refuses the step, and no round is spent on a stale manifest.
        if stage == "review":
            return await self.retake_screens(cwd, key, journal, unit, work, started_by)
        return ""

    async def _gather_inputs(
        self,
        *,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        stage: str,
        stages: list[str],
        found: dict[str, Any],
        row: dict[str, Any],
        directory: Path,
        tree: dict[str, Any] | None,
        work: str,
        rounds_before: set[Any] | None,
    ) -> tuple[dict[str, Any], bytes | None]:
        """What the stage is handed beyond who runs it and where: keyword arguments for
        `Runner.run`, and `## Answers` as the artifact had it. Only a busy run log refuses
        the step here; every other read that fails is recorded as the reason."""
        mode = journal.modes(key).get((unit, stage), "manual")
        round_kw = _round_kwargs(found, row, stage, rounds_before)
        config, failed = await self._stage_config(
            cwd, key, journal, unit, stage, stages, directory, work
        )
        integration_note = self._integration_note(journal, key, unit, stage)
        plan_drift = await _plan_drift(journal, key, unit, stage, directory, tree)
        # The files the plan names, as they stand in the tree the step runs
        # on, for `impl` only. The same again: nothing in `for_step` may refuse the step.
        plan_kw = (
            planmap.for_step(directory / "plan.md", work) if stage in ("impl", "implement") else {}
        )
        shortlist = _shortlist(journal, key, unit)
        answers_before = _answers_before(stage, directory, row)
        # `dict(...)`, not a literal: two sources naming one key is a `TypeError`, not an override.
        return dict(
            mode=mode,
            **round_kw,
            **config,
            last_attempt=describe_attempt(failed) if failed else "",
            integration_note=integration_note,
            plan_drift=plan_drift,
            drift_note=drift.describe(plan_drift) if plan_drift is not None else "",
            shortlist=shortlist,
            end_fields=self._end_fields(
                cwd, unit, rounds_before, answers_before, directory / row["file"]
            ),
            **plan_kw,
        ), answers_before

    async def _stage_config(
        self,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        stage: str,
        stages: list[str],
        directory: Path,
        work: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """The stage's configuration and the failed attempts before this run. Resolved after
        the gate, so a refused step reads nothing more: the plan's label, the effort and, for
        `impl`, which run this is, read before any money is spent. `Runner` does not read the
        run log itself; `build_prompt` only places what it is handed, the same as `base_note`."""
        try:
            config = self.models.stage_config(stage, list(stages), directory, journal, key, unit)
            failed = journal.failed_attempts(key, unit, stage)
        except Busy as e:
            raise Refused(str(e), ("unavailable",)) from e
        # A return to `impl` in the model trial asks `next` once whether CI sent it back;
        # `ci_red` never raises, so nothing here refuses the step.
        if (
            modeltrial.FIELD in (config.get("trial_record") or {})
            and (config.get("impl_run") or 0) > 1
        ):
            config.setdefault("trial_record", {})[modeltrial.CI_RED] = await self.models.ci_red(
                cwd, unit, work
            )
        return config, failed

    def _integration_note(self, journal: Journal, key: str, unit: str, stage: str) -> str:
        """The integration pushed since the last review round, for `review` only."""
        if stage != "review":
            return ""
        since = integration_since_review(journal, key, unit)
        if not since:
            return ""
        return integrate.describe_for_review(since, self.agents.agent_overrides()[0])

    def _end_fields(
        self,
        cwd: str,
        unit: str,
        rounds_before: set[Any] | None,
        answers_before: bytes | None,
        artifact: Path,
    ) -> Callable[[], Awaitable[dict[str, Any]]] | None:
        """What a step that ends `done` adds to its record, asked only then: whether `impl.md`
        kept its `## Answers`, else the findings a `review` added to `review.md`."""
        if answers_before is not None:

            async def answers_kept() -> dict[str, Any]:
                return {"answers_kept": _answers_kept(artifact, answers_before)}

            return answers_kept
        if rounds_before is not None:

            async def findings_added() -> dict[str, Any]:
                return await self.models.findings_added(cwd, unit, rounds_before)

            return findings_added
        return None

    async def _link_kwargs(self, cwd: str, unit: str, stage: str) -> dict[str, Any]:
        """The keyword arguments the database and the unit's idea add to the prompt."""
        # The answers and holds the prompt renders, from the database.
        link_kw: dict[str, Any] = {"meta": self.ws.meta_of(cwd, unit)}
        state_file = self._write_step_state(cwd, unit)
        if state_file:
            link_kw["state_file"] = state_file
        # Only for a unit an idea lists, and only for `intent` and `impl`;
        # every other step is handed no key.
        if stage == "intent":
            idea_note = self.ideas.idea_note(cwd, unit)
            if idea_note:
                link_kw["idea_note"] = idea_note
        if stage in ("impl", "implement"):
            sibling_paths, siblings_note = await self.ideas.siblings(cwd, unit)
            if siblings_note:
                link_kw.update(siblings_note=siblings_note, read_also=sibling_paths)
        return link_kw

    def _step_kwargs(
        self,
        *,
        cwd: str,
        key: str,
        unit: str,
        stage: str,
        stages: list[str],
        artifact: str,
        directory: Path,
        answer: board_reader.Gate,
        tree: dict[str, Any] | None,
        work: str,
        base: dict[str, Any] | None,
        scratch: Path | None,
        started_by: str,
        rerun: bool,
        note: str,
        screens_note: str,
        rounds_before: set[Any] | None,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """The keyword arguments `Runner.run` is called with: who runs what, where, what the
        gate said, and the `inputs` gathered."""
        return dict(
            workspace=cwd,
            directory=directory,
            journal_key=key,
            unit=unit,
            stage=stage,
            artifact=artifact,
            stages=list(stages),
            gate_said=answer[1],
            gate_reasons=_gate_reasons(answer),
            cwd=step_cwd(stage, work, directory, str(scratch) if scratch else None),
            base=base,
            base_note=describe_base(base),
            screens_note=screens_note,
            # The stage's row with today's overrides, read once
            # as the step starts: a rename later reaches the next step, not this one.
            agent=self.agents.agent(stage),
            **inputs,
            # Only named for a spike, so a stand-in `run` without it keeps working.
            **({"watch": work} if scratch is not None else {}),
            # The same: `Runner.run` writes `person` when it is not named.
            **({"started_by": started_by} if started_by != "person" else {}),
            # The same again: only a rerun names them.
            **({"rerun": True, "rerun_note": note} if rerun else {}),
            # What `resume_step` needs of this step, in its `suspend` row.
            owner_extra={
                "workspace_dir": cwd,
                "rounds_before": sorted(rounds_before) if rounds_before is not None else None,
                "tree": tree is not None,
                "watch": work if scratch is not None else None,
                "scratch": str(scratch) if scratch is not None else None,
                "read_also": list(inputs.get("read_also") or ()),
            },
        )

    def _launch(
        self,
        *,
        running: steps_mod.Running,
        mark: steps_mod.Mark,
        rid: str,
        runner: Runner,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        stage: str,
        artifact: str,
        directory: Path,
        base: dict[str, Any] | None,
        rounds_before: set[Any] | None,
        scratch: Path | None,
        kwargs: dict[str, Any],
        answers_before: bytes | None,
    ) -> asyncio.Queue:
        """Start `drive` as the step's own task, and return the queue its reader streams from."""
        queue: asyncio.Queue = asyncio.Queue()
        running.listeners.add(queue)
        # The step's `run` and recorder, from here to the task with no `await`
        # between, so every list that names the step names its `run` too.
        run = uuid.uuid4().hex
        recorder = events.Recorder(
            run,
            Data(self.config.data_dir),
            str(journal.working_dir),
            key,
            unit,
            stage,
        )
        running.run = run
        running.handle.recorder = recorder
        self.recorders[run] = recorder
        self.holds.running[rid]["run"] = run
        running.task = asyncio.create_task(
            self.drive(
                running,
                mark,
                runner,
                cwd,
                unit,
                stage,
                artifact,
                directory,
                base,
                rounds_before,
                rid,
                scratch,
                kwargs,
                answers_before=answers_before,
            )
        )
        running.task.add_done_callback(lambda _task: self.never_driven(running, mark, rid))
        return queue

    async def retake_screens(
        self,
        cwd: str,
        key: str,
        journal: Journal,
        unit: str,
        work: str,
        started_by: str,
    ) -> str:
        """Ask `cos.mjs screens`; when it says to, take the screenshots again under
        `_screens_lock`, judge the result and record it. Returns the section for the `review`
        prompt, `""` when nothing was taken. A retake that fails raises `Invalid` with
        `RETAKE_REFUSED`; what went wrong is only in its record. No tracked file is put back;
        `.screens/` is, by `retake.take`."""
        try:
            asked = await board_reader.screens(
                self.ws.units_root(cwd), unit, work, state=self.ws.snapshot(cwd, [unit])
            )
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if not asked.get("retake"):
            return ""
        old = asked.get("manifest") or {}
        addresses = [str(a) for a in old.get("addresses") or []]
        async with self._screens_lock:
            # Asked again past the lock, which another retake may have held
            # for minutes; from here to the end of `take` a pending update waits for it.
            # And none begins once Apply is pressed.
            refuse_mechanical_while_updating(self.updater)
            rid = uuid.uuid4().hex
            self.retakes[rid] = {"workspace": key, "unit": unit, "started": _now()}
            started = datetime.now().timestamp()
            try:
                result = await retake.take(Path(work), addresses, data_dir=self.config.data_dir)
            except asyncio.CancelledError:
                # A client that went away, or the app going down: the group is killed, and
                # the record still says a retake was begun and did not finish.
                gone = {"code": None, "seconds": round(datetime.now().timestamp() - started, 1)}
                try:
                    journal.append(
                        retake.record(
                            key, unit, old, gone, False, "cancelled before it finished", started_by
                        )
                    )
                except BadRecord, Busy:
                    pass
                raise
            finally:
                self.retakes.pop(rid, None)
                self.bus.publish(Event("retake.ended", key, unit))
        ok, detail = retake.judge(result)
        try:
            journal.append(retake.record(key, unit, old, result, ok, detail, started_by))
        except (BadRecord, Busy) as e:
            # Only a retake that was taken may say it was.
            if not ok:
                raise Invalid(RETAKE_REFUSED) from e
            raise Invalid(
                f"the screenshots were taken again, but the run log could not record it: {e}"
            ) from e
        if not ok:
            raise Invalid(RETAKE_REFUSED)
        return retake.describe_for_review(old, result.get("manifest_after") or {})

    def never_driven(self, running: steps_mod.Running, mark: steps_mod.Mark, rid: str) -> None:
        """A task cancelled before its first turn -- a Stop queued
        ahead of it, or an update's `shutdown` -- never enters `drive`, so its `finally` never runs.
        That `finally` is the only thing that frees the mark once the step is handed over, so
        a mark still held when the task is done means the body never ran: give back what it
        would have, and tell the reader instead of leaving it waiting."""
        if self.holds.marks.get((running.workspace, running.unit)) is not mark:
            return
        self.holds.release(running.workspace, running.unit, mark)
        self.holds.running.pop(rid, None)
        # Never started, so it wrote nothing and has nothing to say.
        self.recorders.pop(running.run, None)
        self.registry.release(running)
        self.bus.publish(Event("step.released", running.workspace, running.unit))
        for q in list(running.listeners):
            q.put_nowait(
                (
                    "raise",
                    Invalid(
                        f"{running.unit}'s {running.stage} step was cancelled before it began; nothing ran"
                    ),
                )
            )

    async def drive(  # noqa: C901, PLR0915 - still to split
        self,
        running: steps_mod.Running,
        mark: steps_mod.Mark,
        runner: Runner,
        cwd: str,
        unit: str,
        stage: str,
        artifact: str,
        directory: Path,
        base: dict[str, Any] | None,
        rounds_before: set[Any] | None,
        rid: str,
        scratch: Path | None,
        kwargs: dict[str, Any],
        answers_before: bytes | None = None,
        resumed: bool = False,
    ) -> None:
        """One board step, start to end, as its own task.

        Every item goes to the step's listeners with `put_nowait` -- this never waits on a
        reader -- and a `stopped` step records no transition, cleans nothing and posts nothing.

        `answers_before` is `pr.md`'s `## Answers` as a `pr` rerun found it, or `impl.md`'s
        as an `impl` step found it; a `done` that no longer ends with
        it says `answers_lost`.
        """

        def tell(item: tuple[str, Any]) -> None:
            for q in list(running.listeners):
                q.put_nowait(item)

        told_done = False
        # `after_end` runs last, only on this.
        ended_done = False
        recorder = running.handle.recorder
        # A cancel with no Stop behind it is the app going down.
        going_down = False
        # Paused by an update: the next start takes the step up in the directory it had.
        suspended = False
        try:
            if recorder is not None:
                recorder.start()
            if scratch is not None and not resumed:
                # A spike taken up again goes on in the directory it had.
                shutil.rmtree(scratch, ignore_errors=True)
                scratch.mkdir(parents=True)
            async for item in runner.run(**kwargs, running=running):
                if item[0] == "done":
                    item = ("done", {**item[1], "base": base})
                    if item[1].get("outcome") != "stopped":
                        item = (
                            "done",
                            {**item[1], **await self.answers.ingest(cwd, unit, item[1], artifact)},
                        )
                    if rounds_before is not None and item[1].get("outcome") == "done":
                        # After `Runner` has written `review.md`, never
                        # before: the artifact does not wait on GitHub.
                        item = (
                            "done",
                            {
                                **item[1],
                                "comments": await self.answers.post_new_rounds(
                                    cwd, unit, rounds_before
                                ),
                            },
                        )
                    if (
                        answers_before is not None
                        and item[1].get("outcome") == "done"
                        and not _answers_kept(directory / artifact, answers_before)
                    ):
                        item = ("done", {**item[1], "answers_lost": True})
                    told_done = True
                tell(item)
                if item[0] == "done" and item[1].get("outcome") == "done":
                    ended_done = True
        except RunError as e:
            tell(("raise", Invalid(str(e))))
            told_done = True
        except Suspended:
            # An update paused the step and wrote its `suspend` row; like the app
            # going down, nothing is ended, nudged or recorded as a transition here.
            going_down = suspended = True
        except asyncio.CancelledError:
            if not running.stop_requested:
                going_down = True
                raise
            # A Stop's cancel that arrived after the runner had already ended.
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
        except Exception as e:  # noqa: BLE001 - the reader raises it, as it always did
            tell(("raise", e))
            told_done = True
        finally:
            if not told_done:
                tell(
                    (
                        "raise",
                        Invalid(
                            f"{unit}'s {stage} step ended without an outcome; the app may be shutting down"
                        ),
                    )
                )
            self.holds.release(running.workspace, running.unit, mark)
            entry = self.holds.running.pop(rid, None)
            task = asyncio.current_task()
            if entry is not None and task is not None:
                # Off the board from here, and until this task
                # ends -- its recorder, `after_end` -- an Apply's settle still waits for it.
                self.holds.finishing[rid] = (entry, task)
            try:
                if scratch is not None and not suspended:
                    shutil.rmtree(scratch, ignore_errors=True)
                self.registry.release(running)
                # After the mark is gone, so the pass sees the unit free.
                self.bus.publish(
                    Event("step.ended", running.workspace, running.unit, going_down=going_down)
                )
                if recorder is not None and not recorder.closed:
                    # The runner closes it on every road that writes an `end`. Left open means the
                    # app is going down -- what can be written is, with no `end` -- or the
                    # runner raised before its own `finally`, which is an ending like any other.
                    if going_down:
                        await recorder.abandon()
                    else:
                        await recorder.close("failed", "the step ended without an outcome")
                if recorder is not None:
                    self.recorders.pop(recorder.run, None)
                if ended_done and not going_down and stage not in autopilot.NOT_STEPS:
                    # After the runner's `end`, which it writes before it yields
                    # `done`, and after the mark is given back: the board read it costs holds
                    # neither the reader's `done` nor the unit.
                    await self.after_end(cwd, unit, stage, running.workspace)
            finally:
                self.holds.finishing.pop(rid, None)

    def pr_machine(self) -> prmachine.Machine:
        """The PR machine over the same history and run log as every other transition."""
        meta = self.ws.unit_meta()
        return prmachine.Machine(
            meta.history, self.ws.journal() or Journal(meta.root, self.config.data_dir)
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
    ) -> dict[str, Any]:
        """One `pr` or `ship`, with no session, no `start` and no `end` row;
        what it did is its transitions and, for `ship`, the `ship` row notices read.
        Returns the `done` item a session's step would have ended with. `rebased` is the
        `ship` gate's clean-rebase read, which guard `ship-ready` takes."""
        if tree is None:
            raise Invalid(
                f"{stage} needs the unit's git worktree, and this workspace is not a git repository"
            )
        work = Path(tree["path"])
        try:
            expected = await asyncio.to_thread(
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
        if stage == "pr":
            out = await machine.open_pr(u, again=again)
        else:
            out = await machine.ship(
                u, authority="code" if started_by == "autopilot" else "person", rebased=rebased
            )
        artifact = prmachine.PR_FILE if stage == "pr" else prmachine.SHIP_FILE
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
        if stage == "pr" and out.ok:
            # The title and body the app wrote go onto a pull request it
            # found open rather than created, and the scope is read once, as after a session.
            done["pr_sync"] = await self.answers.sync_pr(
                cwd, unit, out.url if out.result in ("found", "already") else ""
            )
        refused = False
        if stage == "ship":
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
                        "kind": autopilot.PR_MACHINE,
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
        """The `ship` row notices read, and after a merge the cleanup. From `mechanical`, and from the PR reader and the start-up reconcile
        when they record a merge no `ship` step follows any more. Never raises; the cleanup's answer after a merge, else `None`."""
        journal = self.ws.journal()
        if journal is not None:
            try:
                journal.append(
                    {
                        "kind": "ship",
                        "workspace": key,
                        "unit": unit,
                        "stage": "ship",
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

    async def stop_step(self, cwd: str, unit: str, by: str) -> dict[str, Any]:
        """Stop one running board step. The route and the page's
        button both call this, and nothing else.

        `by` is a name the person typed, not an identity: the password names nobody. What it
        leaves is an `end` record with `outcome: stopped` and `stopped_by` -- or nothing at
        all when the cancel lands before the step's first turn (`never_driven`). It opens
        and closes no gate, and starts nothing.
        """
        self.ws.check(cwd)
        name = (by or "").strip() or OWNER
        return await self.stop_running(self.ws.key(cwd), unit, name)

    async def stop_running(self, key: str, unit: str, by: str) -> dict[str, Any]:
        """The Stop itself: an `end` record with `stopped` and `stopped_by`, or none for a
        step cancelled before its first turn."""
        # An integration is listed beside the steps but has no Stop; say what
        # holds the unit rather than that nothing runs.
        mark = self.holds.marks.get((key, unit))
        if mark is not None and mark.kind == "integrate":
            raise Invalid(steps_mod.describe(unit, mark))
        try:
            running = self.registry.request_stop(key, unit, by)
        except (steps_mod.NotRunning, steps_mod.Finishing) as e:
            raise Invalid(str(e)) from e
        await running.handle.close()
        if running.task is not None:
            running.task.cancel()
        return {"unit": running.unit, "stage": running.stage, "stopped_by": running.stopped_by}

    def running_steps(self, cwd: str) -> list[dict[str, Any]]:
        """The board steps running now in this workspace. This process only.

        Also every integration, from its `holds.running` entry to the `finally` that
        pops it, with `kind: "integration"` and no `run`; a step is `kind: "step"`. A restart
        that asks this sees an integration it would cut."""
        self.ws.check(cwd)
        key = self.ws.key(cwd)
        rows = [{**r, "kind": "step"} for r in self.registry.listing(key)]
        rows += [
            {
                "unit": e["unit"],
                "stage": "integrate",
                "started_at": e["started"],
                "stopping": False,
                "run": None,
                "kind": "integration",
            }
            for e in self.holds.running.values()
            if e["workspace"] == key and e["stage"] == "integrate"
        ]
        return sorted(rows, key=lambda r: r["started_at"])

    def app_identity(self) -> dict[str, str]:
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
