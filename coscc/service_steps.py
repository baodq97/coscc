"""Integrating a branch with `main`, and running, driving and stopping one step of a unit.

Split from `coscc/service.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator

from coscc import backlog
from coscc import board as board_reader
from coscc import drift, events, fetches, gitops
from coscc import harness, integrate, knowledge
from coscc import planmap, priorfindings, retake
from coscc.board import Unavailable
from coscc.data import Data, now as _now
from coscc.gitops import GitError
from coscc.journal import BadRecord, Busy, Journal
from coscc.policy import grant_for
from coscc.runner import RunError, Runner, answers_section, describe_attempt
from coscc import steps as steps_mod
from coscc import units, worktrees
from coscc.units import BadUnit, CannotCreate
from coscc.service_common import (
    BRANCH_REMOTE,
    BRANCH_TRUNK,
    CONSEQUENCE,
    Invalid,
    OWNER,
    _younger_than,
    describe_base,
    step_cwd,
)


# `0054` R6. The longest note a rerun takes, in characters. Chosen by the spec, not measured.
RERUN_NOTE_MAX = 4000

# `0111` R5. What the page says when a retake of the screenshots refuses `review`: one
# sentence, no commit, path or log line (S1, S3, S6). The rest is in the `screens` record.
RETAKE_REFUSED = "The screenshots could not be taken again after the branch was rewritten, so review did not start."


def _answers_kept(path: Path, before: bytes) -> bool:
    """`0054` R8. Whether `path` still ends with the `## Answers` section it had, `before`,
    byte for byte. A `pr` step writes `pr.md` itself, so nothing else guards that section."""
    try:
        return path.read_bytes().endswith(before)
    except OSError:
        return False


def integration_since_review(journal: Journal, key: str, unit: str) -> dict[str, Any] | None:
    """`0035` R10: the latest `pushed` integration recorded after the last `review` step
    that ended `done`, or None. Read by id order, which is the order the rows were written."""
    try:
        rows = journal.records(key, unit)
    except Busy:
        return None
    found = None
    for rec in rows:
        if rec.get("kind") == "integration" and rec.get("outcome") == "pushed":
            found = rec
        elif rec.get("kind") == "end" and rec.get("stage") == "review" and rec.get("outcome") == "done":
            found = None
    return found

# `0100` R6. Seconds a held CI answer is trusted before a board read asks `gh` again, in the
# background. Chosen, not measured.
CI_REFRESH = 60.0


class StepsMixin:

    # -- integration (`0035`) -------------------------------------------------

    async def _attach_integration(
        self, cwd: str, units_: list[dict[str, Any]], journal: Journal | None, key: str
    ) -> list[tuple[tuple[str, str], str, int, str]]:
        """R1/R2. Give every unit `integration: {...}` when it sits in the window, else None.

        **Reads only.** One `gh pr list` for the workspace (up to `integrate.GH_TIMEOUT`),
        `git` counts against the `origin/main` the last fetch brought — no fetch here — and
        `gh pr checks` only for a unit whose head is the one its last integration pushed.
        Nothing here writes a record, calls `update-branch` or opens a session.

        `0100` R6. Also gives each unit in the window `ci_held`, the held CI answer when it
        is for the head `gh pr list` just returned, and returns the CI asks `board` starts
        once it has answered: `(slot, tree, pr number, head)` for each unit with no answer
        for that head, or one older than `CI_REFRESH`, and no ask already running.
        """
        for u in units_:
            u["integration"] = None
        window = [u for u in units_ if u.get("between_pr_and_ship") and u.get("pr")]
        if not window:
            return []
        root = Path(cwd).expanduser().resolve()
        last = self._last_integrations(journal, key)
        try:
            prs: list[dict[str, Any]] | str = await integrate.open_prs(str(root))
        except integrate.IntegrateError as e:
            prs = str(e)
        asks: list[tuple[tuple[str, str], str, int, str]] = []
        oldest = datetime.fromisoformat(_now()) - timedelta(seconds=CI_REFRESH)
        for u in window:
            info = await self._integration_of(root, u, prs, last.get(u["name"]))
            if info is not None:
                u["integration"] = info
            number = (u.get("pr") or {}).get("number")
            row = next((r for r in prs if r.get("number") == number), None) if isinstance(prs, list) else None
            if row is None:
                continue
            slot, head = (key, u["name"]), str(row.get("headRefOid") or "")
            held = self._ci.get(slot)
            if held is not None and held.get("head") == head:
                u["ci_held"] = held
            if slot in self._ci_asks:
                continue
            if held is None or held.get("head") != head or not _younger_than(held.get("at") or "", oldest):
                asks.append((slot, str(root), int(number), head))
        return asks

    def _ask_ci(self, asks: list[tuple[tuple[str, str], str, int, str]]) -> None:
        """`0100` R6. One background `gh pr checks` per ask, none awaited. Its answer, or
        `gh`'s error, is held with the time it was read, so an error is not asked again
        before `CI_REFRESH` either. Writes no run-log record."""
        for slot, tree, number, head in asks:
            if slot in self._ci_asks:
                continue

            async def ask(slot=slot, tree=tree, number=number, head=head) -> None:
                try:
                    answer: dict[str, Any] = {"checks": await integrate.required_checks(tree, number)}
                except integrate.IntegrateError as e:
                    answer = {"error": str(e)}
                self._ci[slot] = {"head": head, **answer, "at": _now()}

            task = asyncio.get_running_loop().create_task(ask())
            self._ci_asks[slot] = task
            # Removed however it ends — cancelled included — or the unit is never asked again.
            task.add_done_callback(lambda t, slot=slot: self._ci_asks.pop(slot, None) if self._ci_asks.get(slot) is t else None)

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
        self, root: Path, u: dict[str, Any], prs: list[dict[str, Any]] | str,
        last_record: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """One unit's state. None when its pull request is not among the open ones."""
        number = (u.get("pr") or {}).get("number")
        if isinstance(prs, str):
            pr_row: dict[str, Any] | str = prs
        else:
            match = next((r for r in prs if r.get("number") == number), None)
            if match is None:
                return None
            pr_row = match
        origin_sha = ""
        missing: int | str = 0
        if isinstance(pr_row, dict):
            try:
                origin_sha = await gitops.rev_parse(root, "refs/remotes/origin/main")
                head = str(pr_row.get("headRefOid") or "")
                if not await gitops.has_commit(root, head):
                    missing = f"the pull request's head {head[:7]} is not here: fetch, then ask again"
                else:
                    missing = await gitops.count_missing(root, head, origin_sha)
            except GitError as e:
                missing = str(e)
        checks: list[dict[str, Any]] | str | None = None
        if integrate.needs_checks(pr_row, last_record):
            try:
                checks = await integrate.required_checks(str(root), int(number))
            except integrate.IntegrateError as e:
                checks = str(e)
        verdict = integrate.classify(pr_row, missing, origin_sha, last_record, checks)
        state = verdict["state"]
        review_status = next((r.get("status") or "" for r in u.get("stages") or [] if r.get("stage") == "review"), "")
        gebo = state in integrate.GEBO_STATES
        # `0052`: a `current` unit also has the button, since the count may be against a
        # stale `origin/main` and only a press fetches (R3). Both mechanical states may fall
        # to Gebo when GitHub refuses the rebase (spec, answer 1), and the page says so.
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
            if (last_record or {}).get("outcome") == "needs-person" else [],
            "warnings": integrate.warnings(
                u.get("rounds") or [], review_status, gebo or fallback, grant_for("integrate").warning,
                fallback=fallback,
            ),
            "consequence": CONSEQUENCE["integrate"],
        }

    async def integrate(
        self, cwd: str, unit: str, started_by: str = "person",
    ) -> AsyncIterator[tuple[str, Any]]:
        """`0035`. Integrate one unit, on a person's request. Streams like `run_step`.

        `0043`: or on the autopilot's, which passes `started_by="autopilot"`; every record
        this writes carries it (R3). No route passes it.

        Refuses before anything changes (R12), and every refusal, push or failure leaves one
        `integration` record (R9). `behind` goes the mechanical road (R4); `conflicting`
        and `red-after-integration` open Gebo (R5).

        `0052`: a press inside the window fetches `origin/main` first, through the `0048`
        coordinator and before the lock, so the count is against the trunk as it is now; a
        failed fetch goes on with the ref it has and says so. It also reads GitHub's
        `mergeStateStatus`, only to record it. A mechanical road whose `update-branch`
        exits non-zero opens Gebo with that code and gh's words.

        `0114`: a rebase left in progress by an integration this run log shows cut is
        aborted first (R4). A local head that is not the pull request's is read against it
        (R5): `behind` follows it with no session; `ahead` or `diverged` opens Gebo to push
        what was never pushed, in every state (R6).
        """
        try:
            integrate.check_started_by(started_by)
        except ValueError as e:
            raise Invalid(str(e)) from e
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so an integration cannot be recorded — set COS_WORKING_DIR")
        if not unit:
            raise Invalid("name a work unit")
        directory = self._unit_dir(cwd, unit)
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Invalid(f"no such work unit in this workspace: {unit}")
        key = self._journal_key(cwd)
        root = Path(cwd).expanduser().resolve()
        last = self._last_integrations(journal, key).get(unit)
        info = None
        pr = (found.get("pr") or {}).get("number")
        # Spread into every record this press writes (`0052` R5; `started_by`, `0043` R3).
        seen: dict[str, Any] = {"fetch": None, "merge_state": "", "started_by": started_by, "completion": None}
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
            branch = units.branch_name(cwd, unit, self.config.data_dir)
        except (CannotCreate, BadUnit):
            branch = ""
        tree_found = None
        try:
            tree_found = await worktrees.find(cwd, unit, self.config.data_dir)
        except (GitError, BadUnit):
            tree_found = None
        tree = Path(tree_found["path"]) if tree_found else None

        # `0114` R4: what the app did to the tree before deciding, at the head of every
        # record this press writes.
        before: list[str] = []

        def write(rec: dict[str, Any]) -> dict[str, Any]:
            if before:
                rec = {**rec, "detail": "; ".join(before + ([rec["detail"]] if rec.get("detail") else []))}
            try:
                return journal.append(rec)
            except (BadRecord, Busy):
                return rec

        lock = self._integrate_locks.setdefault(key, asyncio.Lock())
        async with lock:
            busy = self._busy(key, unit)
            cut = None
            if not busy and tree is not None:
                try:
                    here = any(e["workspace"] == key and e["unit"] == unit for e in self._running.values())
                    cut = integrate.cut_integration(journal.records(key, unit=unit), unit, here)
                except Busy:
                    cut = None
                try:
                    if cut is not None and await gitops.rebase_in_progress(tree):
                        await gitops.abort_rebase(tree)
                        before.append(f"an integration cut at {cut['at']} left a rebase in progress; the app aborted it")
                except GitError as e:
                    before.append(f"could not abort the rebase an integration cut at {cut['at']} left: {e}")
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
            if clean is True and on_branch is True and pr_head and local_head and local_head != pr_head:
                answers: list[bool | None] = []
                for ancestor, descendant in ((local_head, pr_head), (pr_head, local_head)):
                    try:
                        answers.append(await gitops.is_ancestor(tree, ancestor, descendant))
                    except GitError as e:
                        answers.append(None)
                        how_said = how_said or str(e)
                how = integrate.relation(local_head, pr_head, *answers)
                if how == "diverged":
                    # Review F1: only a local head on a newer `main` than the pull request's is
                    # a rebase that was never pushed; the other way round, the pull request was
                    # rebased elsewhere and pushing the tree would undo that.
                    newer = None
                    try:
                        if not origin_sha:
                            raise GitError("origin/main could not be read, so the two bases cannot be compared")
                        local_base = await gitops.merge_base_of(tree, local_head, origin_sha)
                        pr_base = await gitops.merge_base_of(tree, pr_head, origin_sha)
                        newer = integrate.newer_base(
                            local_base, pr_base, await gitops.is_ancestor(tree, pr_base, local_base))
                    except GitError as e:
                        how_said = str(e)
                    how = integrate.relation(local_head, pr_head, *answers, newer=newer)
                was = local_head
                if how == "behind":
                    try:
                        await gitops.reset_branch_to(tree, branch, local_head, pr_head)
                        local_head = pr_head
                        before.append(f"the local branch followed the pull request's head from {was[:7]} to {pr_head[:7]}")
                    except GitError as e:
                        how, how_said = "", str(e)
                if how and how != "same":
                    seen["completion"] = {"relation": how, "local_head": was, "cut": cut}
            reason = integrate.refusal(
                in_window=info is not None, busy=busy,
                clean=clean, branch_ok=on_branch, local_head=local_head, pr_head=pr_head, state=state,
                origin=integrate.origin_note(origin_sha, seen["fetch"]),
                relation=how, relation_said=how_said,
            )
            if reason:
                write(integrate.record(
                    workspace=key, unit=unit, pr=pr, mode=(info or {}).get("mode") or "mechanical",
                    head_before=pr_head, head_after="", origin_sha=origin_sha, outcome="refused",
                    detail=reason, **seen,
                ))
                raise Invalid(reason)
            mark = self._take(key, unit, "integrate")
            # `0114` R6: commits never pushed go to Gebo whatever the state.
            completing = how in integrate.COMPLETION
            # `0051` spec, answer 1: Gebo shows as running under its agent name; a mechanical
            # rebase has no agent and shows as rebasing. The same condition as below.
            rid = self._mark_running(
                key, unit, "integrate", "rebase" if state == "behind" and not completing else "gebo")
        try:
            assert tree is not None
            refused_update = None
            if state == "behind" and not completing:
                rec, refused_update = await self._integrate_mechanical(
                    key, unit, int(pr), tree, branch, pr_head, origin_sha, seen,
                )
                if rec is not None:
                    rec = write(rec)
                    yield ("done", {"integration": rec})
                    return
                # `0052`, spec answer 1: GitHub refused the rebase, and the press agreed to
                # Gebo for that. The board shows Gebo from here on, not a rebase.
                self._running[rid]["kind"] = "gebo"
            async for item in self._integrate_gebo(
                cwd, key, unit, directory, found, data, info, int(pr), tree, branch, pr_head, origin_sha,
                journal, write, seen, refused_update,
                completion=seen["completion"] if completing else None,
            ):
                yield item
        finally:
            self._release(key, unit, mark)
            self._running.pop(rid, None)
            self.updater.job_ended()
            # `0043` R5 a.
            self._autopilot_nudge(key)

    async def _integrate_mechanical(
        self, key: str, unit: str, pr: int, tree: Path, branch: str, head_before: str, origin_sha: str,
        seen: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """R4. GitHub rebases, the local branch follows. No session.

        `(record, None)`, or `(None, {code, said})` when `update-branch` exited non-zero and
        the pull request's head is still `head_before` — the caller then opens Gebo (`0052`).
        A `gh` that could not run or did not answer in time, and a head that has not moved
        yet, stay `failed`: there is no exit code to go on, and GitHub may still be
        rebasing, which a Gebo session would race.
        """
        base = dict(workspace=key, unit=unit, pr=pr, mode="mechanical", head_before=head_before,
                    origin_sha=origin_sha, **seen)
        try:
            code, said = await integrate.update_branch(str(tree), pr)
        except integrate.IntegrateError as e:
            return integrate.record(**base, head_after="", outcome="failed", detail=str(e)), None
        if code != 0:
            refused = {"code": code, "said": said or "gh refused"}
            # `0052` review F1: a non-zero exit does not rule out that GitHub took the command.
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
                return integrate.record(
                    **base, head_after="", outcome="failed", update_branch=refused,
                    detail=f"gh pr update-branch exited {code}, and the pull request's head could not be "
                           f"read to rule out a rebase on GitHub's side, so no session was opened: {unread}",
                ), None
            base["update_branch"] = refused
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
            return integrate.record(
                **base, head_after="", outcome="failed",
                detail="GitHub accepted the command but the head has not changed yet",
            ), None
        try:
            await gitops.reset_branch_to(tree, branch, head_before, head_after)
            detail = said
        except GitError as e:
            # The push happened on GitHub's side either way; the local tree is behind it.
            detail = f"pushed on GitHub, but the local branch was not moved: {e}"
        return integrate.record(**base, head_after=head_after, outcome="pushed", detail=detail), None

    async def _integrate_gebo(
        self, cwd: str, key: str, unit: str, directory: Path, found: dict[str, Any],
        data: dict[str, Any], info: dict[str, Any], pr: int, tree: Path, branch: str,
        head_before: str, origin_sha: str, journal: Journal, write: Any,
        seen: dict[str, Any], refused_update: dict[str, Any] | None = None,
        completion: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """R5–R8. One Gebo session; the outcome is read from GitHub afterwards.

        `refused_update`: the `update-branch` refusal that opened it (`0052`), carried into
        the prompt and the press's one record.

        `completion` (`0114` R6, R7): the local head to push as it is. A pull request that
        ends on any other head is `failed`, whoever pushed it.
        """
        root = Path(cwd).expanduser().resolve()
        rel = await self._related(root, unit, data, head_before, origin_sha)
        units_root = self._units_root(cwd)
        # `0094` R14: by path; Gebo reads what it needs of them (`integrate.read_paths`).
        own = {}
        for name in ("intent.md", "spec.md", "plan.md", "impl.md"):
            path = Path(directory).resolve() / name
            if path.exists():
                own[name] = path
        try:
            skill = harness.read_skill("integrate")
        except harness.MissingRules as e:
            raise Invalid(f"the integrate skill could not be read: {e}") from e
        prompt = integrate.build_prompt(
            skill=skill, unit=unit, branch=branch, pr=pr, state=info["state"], reason=info.get("reason", ""),
            head_before=head_before, origin_sha=origin_sha, rel=rel, units_root=units_root, own_paths=own,
            refused_update=refused_update, completion=completion,
        )
        grant = grant_for("integrate")
        model, model_source = self._model_for("impl")
        app = self._app_identity()
        try:
            journal.started(key, unit, "integrate", "manual", started_by=seen["started_by"],
                            prompt_chars=len(prompt), granted=list(grant.tools),
                            max_turns=grant.max_turns, head=head_before, model=model, model_source=model_source,
                            pointed=list(own), app_version=app["version"], app_commit=app["commit"],
                            # `0093` R8: what opened this session, for *Integrate for a conflict*.
                            integrate_state=info["state"])
        except (BadRecord, Busy):
            pass
        end: dict[str, Any] = {}
        failure = ""
        try:
            async for kind, payload in integrate.run_gebo(
                self.sessions, tree=str(tree), workspace=cwd, prompt=prompt, grant=grant,
                read_also=integrate.read_paths(units_root, unit, rel), lease=(branch, head_before), model=model,
            ):
                if kind == "chunk":
                    yield ("chunk", payload)
                else:
                    end = payload
        except Exception as e:  # noqa: BLE001 — recorded, never swallowed silently
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
        outcome = integrate.outcome_of_session(head_before, head_now, reply)
        if outcome == "pushed":
            # `0052` review round 2, F1: a head that moved is Gebo's push only if Gebo's tree
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
        # `0114` R7: the completion road pushes the local head as it was, or nothing.
        if completion is not None and outcome == "pushed" and head_now != completion["local_head"]:
            outcome = "failed"
            details.append(
                f"the pull request's head moved to {head_now[:7]}, not to the local head "
                f"{str(completion['local_head'])[:7]} this completion was to push"
            )
        try:
            journal.finished(
                key, unit, "integrate", "done" if outcome in ("pushed", "needs-person") else "failed",
                session_id=end.get("session_id", ""), detail="; ".join(details) or None,
                denials=end.get("denials", 0), denied=end.get("denied"),
                models_used=end.get("models_used") or None, **(end.get("cost") or {}),
            )
        except (BadRecord, Busy):
            pass
        rec = write(integrate.record(
            workspace=key, unit=unit, pr=pr, mode="agent", head_before=head_before, head_after=head_now,
            origin_sha=origin_sha, outcome=outcome, related_=rel, report=reply,
            needs_person=integrate.parse_needs_person(reply), detail="; ".join(details),
            update_branch=refused_update, **seen,
        ))
        yield ("done", {"integration": rec})

    async def _related(
        self, root: Path, unit: str, data: dict[str, Any], head: str, origin_sha: str
    ) -> dict[str, list[dict[str, Any]]]:
        """R7, from git. A failure leaves a list empty rather than stopping the step."""
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
        # `0045` R15. Asked first with no `--repo`, which reads files only: a held unit is
        # answered here, before `_worktree` could reopen the tree a drop just removed.
        try:
            held = await board_reader.next_step(self._units_root(cwd), unit, repo=None)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if held.get("hold"):
            return {
                "cwd": cwd, "unit": unit, **{k: held[k] for k in ("stage", "action", "blocked")},
                "waiting": [], "hold": held["hold"],
            }
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
        return {
            "cwd": cwd,
            "unit": unit,
            **{k: found[k] for k in ("stage", "action", "blocked")},
            # `0028`. The findings a person is awaited on, copied from `cos.mjs next`.
            "waiting": list(found.get("waiting") or []),
            # `0106`. The stage a fully answered draft would run again; only the autopilot
            # reads it.
            "rerun": str(found.get("rerun") or ""),
        }

    async def rerun_offers(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0054` R1, R2. The accepted stages `unit` may run again, each with the stages that
        then run again after it -- `cos.mjs rerun`'s answer, copied: `{unit, offers: [{stage,
        later}], why}`. Files only: no worktree is opened and no `gh` is asked. Nothing here
        chooses a stage."""
        self._workspace_or_refuse(cwd)
        if not unit:
            raise Invalid("name a work unit")
        self._unit_dir(cwd, unit)
        try:
            found = await board_reader.rerun(self._units_root(cwd), unit)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if "error" in found:
            raise Invalid(str(found["error"]))
        return found

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

    async def run_step(
        self, cwd: str, unit: str, stage: str, started_by: str = "person",
        rerun: bool = False, note: str = "",
    ) -> AsyncIterator[tuple[str, Any]]:
        """Run one step of one unit, streaming the reply as it arrives.

        Everything this needs — the stage order, the artifact filename, the mode — comes
        from one board read, so a step cannot run against a different idea of the unit
        than the one the page is showing.

        `started_by` (`0043` R3) is `autopilot` only when the autopilot calls this; no route
        passes it, so a request cannot say it is the autopilot.

        `rerun` (`0054` R6) runs an accepted stage again, with a person's `note`. Whether the
        stage may, and the `### Rerun` block appended to `intent.md` before the session
        starts, are `cos.mjs rerun`'s. Refused for the autopilot and for a note over
        `RERUN_NOTE_MAX`; an empty note is not refused (`spec.md ## Answers, câu 2`).
        """
        try:
            integrate.check_started_by(started_by)
        except ValueError as e:
            raise Invalid(str(e)) from e
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a run cannot be recorded — set COS_WORKING_DIR"
            )

        # `0050` R4. The unit is held from here, before the first `await`: a second request
        # for any stage of it is refused before it reads the board, opens a worktree, runs
        # the gate or fetches -- not after all of that, as it was (`spike.md ## U1`). Until
        # the step is handed to `_drive` the mark is this frame's to return, on every road
        # out: a refusal, an exception, or a cancel when the client goes away (R5).
        key = self._journal_key(cwd)
        mark = self._take(key, unit, "step", stage)
        handed = False
        running: steps_mod.Running | None = None
        rid: str | None = None
        try:
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
            # `0045` R4/R15. `cos.mjs`'s own field, read before any worktree is opened — the gate
            # below would refuse too, but only after `_worktree` had reopened a dropped tree.
            held = found.get("hold")
            if held:
                raise Invalid(f"{unit} is {held.get('state')}: {held.get('reason')} — nothing runs on it")

            # `0054` R6. Before a worktree is opened or the gate asked. Whether `stage` may run
            # again, and the block that says so, are `cos.mjs`'s; its refusal is passed on.
            note = str(note or "").strip()
            rerun_block = ""
            if rerun:
                if started_by != "person":
                    raise Invalid("a stage is run again only by a person, from the board, never by the autopilot")
                if len(note) > RERUN_NOTE_MAX:
                    raise Invalid(f"the note is {len(note)} characters, over the {RERUN_NOTE_MAX} a rerun takes")
                try:
                    asked = await board_reader.rerun(self._units_root(cwd), unit, stage)
                except Unavailable as e:
                    raise Invalid(str(e)) from e
                if "error" in asked:
                    raise Invalid(str(asked["error"]))
                rerun_block = str(asked.get("block") or "")

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
            # `0039` R13. A spike is watched through the worktree's `HEAD` and `git status`;
            # with no git there is nothing to watch, so it does not run at all.
            if stage == "spike" and tree is None:
                raise Invalid("spike needs a git worktree to watch, and this workspace is not a git repository")
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
                    prepared = await worktrees.prepare(Path(work), cwd, data_dir=self.config.data_dir)
                if not prepared.get("ok"):
                    raise Invalid(worktrees.describe_failure(prepared))

            # `0111` R1-R7. A UI unit whose branch was rewritten since `impl` took its
            # screenshots has them taken again, here, before any money is spent; a retake that
            # fails refuses the step, and no round is spent on a stale manifest.
            screens_note = ""
            if stage == "review" and tree is not None:
                screens_note = await self._retake_screens(cwd, key, journal, unit, work, started_by)

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
            # `0033`: with the plan's label, the effort and, for `impl`, which run this is.
            # `0019` plan step 6 / `spec.md` R6. Read after the gate, before any money is
            # spent — the same place `model` is resolved. `Runner` does not read the run log
            # itself; `build_prompt` only places what it is handed, the same as `base_note`.
            try:
                config = self._stage_config(stage, list(data["stages"]), directory, journal, key, unit)
                failed = journal.failed_attempts(key, unit, stage)
            except Busy as e:
                raise Invalid(str(e)) from e
            end_fields = None
            if rounds_before is not None:
                async def end_fields() -> dict[str, Any]:
                    return await self._findings_added(cwd, unit, rounds_before)
            # `0035` R10. The integration pushed since the last review round, for `review` only.
            integration_note = ""
            if stage == "review":
                since = integration_since_review(journal, key, unit)
                integration_note = integrate.describe_for_review(since) if since else ""
            # `0042`. Which files the plan names `main` changed since the plan ran, for `impl`
            # only. Unlike `failed_attempts` above, nothing here may refuse the step (R8): a
            # busy run log, an unreadable `plan.md` or a bug in `drift.py` is "could not check".
            plan_drift: dict[str, Any] | None = None
            if stage in ("impl", "implement"):
                try:
                    plan_drift = await drift.compute(
                        journal.records(key, unit),
                        (directory / "plan.md").read_text(encoding="utf-8"),
                        tree["path"] if tree else None,
                    )
                except Exception as e:  # noqa: BLE001 — R8, recorded as the reason
                    plan_drift = {
                        "plan_sha": None, "main_sha": None, "files": None,
                        "checked": False, "reason": str(e) or type(e).__name__,
                    }
            # `0110` R6/R7. What the reviews of the units the board above read as finished said
            # about the files the plan names, for `impl` only; every other stage is handed no
            # key. Like `plan_drift`, nothing in `for_step` may refuse the step.
            prior_kw: dict[str, Any] = {}
            if stage in ("impl", "implement"):
                prior_kw = priorfindings.for_step(
                    units.cos_dir(cwd, self.config.data_dir),
                    [u["name"] for u in data["units"] if u.get("next") == "finished"],
                    directory / "plan.md",
                    unit,
                )
            # `0096` R9, R11. The files the plan names, as they stand in the tree the step runs
            # on, for `impl` only. The same again: nothing in `for_step` may refuse the step.
            plan_kw: dict[str, Any] = {}
            if stage in ("impl", "implement"):
                plan_kw = planmap.for_step(directory / "plan.md", work)
            # `0074` R14. Where the unit stood in the shortlist in effect as it started, for the
            # outcome's measurement. Like `plan_drift`, nothing here may refuse the step.
            try:
                shortlist = backlog.stamp(journal.records(key, kind="shortlist"), unit)
            except Exception as e:  # noqa: BLE001 — recorded as the reason
                shortlist = {"rank": None, "of": None, "record": None, "error": str(e) or type(e).__name__}
            # `0041` R2. The unit's open pull request, for `pr` only, after the gate and before
            # any money is spent. One `gh pr list`, up to `integrate.GH_TIMEOUT`; a lookup that
            # fails still starts the step, and its prompt says so.
            pr_note, pr_before = "", None
            if stage == "pr":
                if tree is not None:
                    lookup = await integrate.pr_for_branch(work, tree.get("branch") or "")
                else:
                    lookup = {"state": "unknown", "reason": "this workspace is not a git checkout"}
                # `None` when the lookup could not answer, so `_sync_pr` does not read that as
                # "no pull request" (`0055` review F2); the `start` record still gets `""`.
                pr_note = integrate.describe_pr_lookup(lookup)
                pr_before = None if lookup.get("state") == "unknown" else lookup.get("url", "")
            # `0090` R1-R4. The store, read once, only with the flag on and only for the stages
            # that receive it; off, nothing is read and `Runner.run` is handed no key at all, so
            # its prompt and its `start` record are what they were (R2). A store that cannot be
            # read never refuses the step (`knowledge.for_step`).
            knowledge_kw: dict[str, Any] = {}
            if self.config.knowledge and stage in knowledge.STAGES:
                knowledge_kw = knowledge.for_step(self.config.data_dir, units.slot(cwd))
            # `0054` R3, R8. After the last refusal that reads nothing more, before any money
            # is spent. `pr.md`'s `## Answers` is read first: the `pr` session writes that file
            # itself, so only a comparison afterwards can tell whether the section survived.
            answers_before: bytes | None = None
            if rerun:
                if stage == "pr":
                    try:
                        answers_before = answers_section((directory / "pr.md").read_bytes())
                    except OSError:
                        answers_before = None
                await self._append_to_answers(directory / "intent.md", "\n" + rerun_block, "a rerun")
            if answers_before is not None:
                kept_from = answers_before

                async def end_fields() -> dict[str, Any]:
                    return {"answers_kept": _answers_kept(directory / "pr.md", kept_from)}
            runner = Runner(self.sessions, journal, app=self._app_identity())
            # `0034` R11. The registry is what the page lists and what a Stop finds; the mark
            # taken above is what everything else asks. The same start time for both, and no
            # `await` between the listing and the phase (`0050` R3).
            try:
                running = self.steps.claim(key, unit, stage, started_at=mark.started_at)
            except steps_mod.Busy as e:
                raise Invalid(str(e)) from e
            mark.phase = "running"
            # `0039` R12: emptied before the step, whatever an earlier one left, and removed
            # after it however it ends -- in `_drive`, so a client that drops the stream no
            # longer decides when (`0034`).
            scratch = units.spike_dir(cwd, unit, self.config.data_dir) if stage == "spike" else None
            rid = self._mark_running(key, unit, stage, "step")
            queue: asyncio.Queue = asyncio.Queue()
            running.listeners.add(queue)
            # `0073` R1. The step's `run` and recorder, from here to the task with no `await`
            # between, so every list that names the step names its `run` too.
            run = uuid.uuid4().hex
            recorder = events.Recorder(
                run, Data(self.config.data_dir), str(journal.working_dir), key, unit, stage,
            )
            running.run = run
            running.handle.recorder = recorder
            self._recorders[run] = recorder
            self._running[rid]["run"] = run
            running.task = asyncio.create_task(self._drive(
                running, mark, runner, cwd, unit, stage, row["file"], directory, tree, base, rounds_before,
                rid, scratch,
                dict(
                    workspace=cwd,
                    directory=directory,
                    journal_key=key,
                    unit=unit,
                    stage=stage,
                    artifact=row["file"],
                    stages=list(data["stages"]),
                    mode=mode,
                    gate_said=said,
                    cwd=step_cwd(stage, work, directory, str(scratch) if scratch else None),
                    base=base,
                    base_note=describe_base(base),
                    last_attempt=describe_attempt(failed) if failed else "",
                    integration_note=integration_note,
                    screens_note=screens_note,
                    plan_drift=plan_drift,
                    drift_note=drift.describe(plan_drift) if plan_drift is not None else "",
                    shortlist=shortlist,
                    end_fields=end_fields,
                    pr_note=pr_note,
                    pr_before=pr_before,
                    **knowledge_kw,
                    **prior_kw,
                    **plan_kw,
                    **config,
                    # Only named for a spike, so a stand-in `run` without it keeps working.
                    **({"watch": work} if scratch is not None else {}),
                    # The same: `Runner.run` writes `person` when it is not named.
                    **({"started_by": started_by} if started_by != "person" else {}),
                    # `0054`. The same again: only a rerun names them.
                    **({"rerun": True, "rerun_note": note} if rerun else {}),
                ),
                answers_before=answers_before,
            ))
            running.task.add_done_callback(
                lambda _task: self._never_driven(running, mark, rid)
            )
            handed = True
        finally:
            if not handed:
                # `0050` review round 1, F1. Past `claim`, the listing and the `0051` entry
                # are this frame's to return too, or `/api/board/steps` keeps a step that
                # never started and the next request gets past the mark to `claim` again.
                self._release(key, unit, mark)
                if rid is not None:
                    self._running.pop(rid, None)
                if running is not None:
                    self.steps.release(running)
                    self.updater.job_ended()
        # `0034` R3/R4. Only the reader lives here. A reader that goes away -- a closed
        # tab, a dropped NDJSON client -- takes its queue with it and nothing else: the
        # step runs on to its own end in `_drive`. Stopping it is `stop_step`, and only that.
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

    async def _retake_screens(
        self, cwd: str, key: str, journal: Journal, unit: str, work: str, started_by: str,
    ) -> str:
        """`0111`. Ask `cos.mjs screens`; when it says to, take the screenshots again under
        `_screens_lock`, judge the result (R4) and record it (R6). Returns the section for the
        `review` prompt (R7), `""` when nothing was taken. A retake that fails raises
        `Invalid` with `RETAKE_REFUSED`; what went wrong is only in its record (R5). No tracked
        file is put back; `.screens/` is, by `retake.take` (review round 1, F1)."""
        try:
            asked = await board_reader.screens(self._units_root(cwd), unit, work)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if not asked.get("retake"):
            return ""
        old = asked.get("manifest") or {}
        addresses = [str(a) for a in old.get("addresses") or []]
        async with self._screens_lock:
            # Review round 1, F3. Asked again past the lock, which another retake may have held
            # for minutes; from here to the end of `take` a pending update waits for it.
            self._refuse_while_updating()
            rid = uuid.uuid4().hex
            self._retakes[rid] = {"workspace": key, "unit": unit, "started": _now()}
            started = datetime.now().timestamp()
            try:
                result = await retake.take(Path(work), addresses, data_dir=self.config.data_dir)
            except asyncio.CancelledError:
                # A client that went away, or the app going down: the group is killed, and
                # the record still says a retake was begun and did not finish.
                gone = {"code": None, "seconds": round(datetime.now().timestamp() - started, 1)}
                try:
                    journal.append(retake.record(key, unit, old, gone, False, "cancelled before it finished", started_by))
                except (BadRecord, Busy):
                    pass
                raise
            finally:
                self._retakes.pop(rid, None)
                self.updater.job_ended()
        ok, detail = retake.judge(result)
        try:
            journal.append(retake.record(key, unit, old, result, ok, detail, started_by))
        except (BadRecord, Busy) as e:
            # Review round 1, F2: only a retake that was taken may say it was.
            if not ok:
                raise Invalid(RETAKE_REFUSED) from e
            raise Invalid(f"the screenshots were taken again, but the run log could not record it: {e}") from e
        if not ok:
            raise Invalid(RETAKE_REFUSED)
        return retake.describe_for_review(old, result.get("manifest_after") or {})

    def _never_driven(self, running: steps_mod.Running, mark: steps_mod.Mark, rid: str) -> None:
        """`0050` review round 2, F2. A task cancelled before its first turn -- a Stop queued
        ahead of it, or "apply now" -- never enters `_drive`, so its `finally` never runs.
        That `finally` is the only thing that frees the mark once the step is handed over, so
        a mark still held when the task is done means the body never ran: give back what it
        would have, and tell the reader instead of leaving it waiting."""
        if self._active.get((running.workspace, running.unit)) is not mark:
            return
        self._release(running.workspace, running.unit, mark)
        self._running.pop(rid, None)
        # `0073`. Never started, so it wrote nothing and has nothing to say.
        self._recorders.pop(running.run, None)
        self.steps.release(running)
        self.updater.job_ended()
        for q in list(running.listeners):
            q.put_nowait(("raise", Invalid(
                f"{running.unit}'s {running.stage} step was cancelled before it began; nothing ran"
            )))

    async def _drive(
        self, running: steps_mod.Running, mark: steps_mod.Mark, runner: Runner, cwd: str, unit: str, stage: str,
        artifact: str, directory: Path, tree: dict[str, Any] | None, base: dict[str, Any] | None,
        rounds_before: set[Any] | None, rid: str, scratch: Path | None, kwargs: dict[str, Any],
        answers_before: bytes | None = None,
    ) -> None:
        """One board step, start to end, as its own task (`0034`).

        What `run_step` used to do inline, unchanged, except that every item goes to the
        step's listeners with `put_nowait` -- this never waits on a reader -- and that a
        `stopped` step records no transition, cleans nothing and posts nothing (R9).

        `answers_before` (`0054` R8) is `pr.md`'s `## Answers` as a `pr` rerun found it; a
        `done` that no longer ends with it says `answers_lost`.
        """

        def tell(item: tuple[str, Any]) -> None:
            for q in list(running.listeners):
                q.put_nowait(item)

        told_done = False
        recorder = running.handle.recorder
        # `0073`. A cancel with no Stop behind it is the app going down (spec C9).
        going_down = False
        try:
            if recorder is not None:
                recorder.start()
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
                scratch.mkdir(parents=True)
            async for item in runner.run(**kwargs, running=running):
                if item[0] == "done":
                    item = ("done", {**item[1], "base": base})
                    if item[1].get("outcome") != "stopped":
                        self._record_transition(cwd, unit, artifact, directory, item[1])
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
                    if stage == "pr" and item[1].get("outcome") != "stopped":
                        # `0055` R3. After `pr.md` is on disk, like the rounds above; a
                        # stopped step posts nothing (`0034` R9, `spec.md ## Answers, câu 2`).
                        item = (
                            "done",
                            {**item[1], "pr_sync": await self._sync_pr(cwd, unit, kwargs.get("pr_before"))},
                        )
                    if (
                        answers_before is not None and item[1].get("outcome") == "done"
                        and not _answers_kept(directory / artifact, answers_before)
                    ):
                        item = ("done", {**item[1], "answers_lost": True})
                    told_done = True
                tell(item)
        except RunError as e:
            tell(("raise", Invalid(str(e))))
            told_done = True
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
                tell(("raise", Invalid(f"{unit}'s {stage} step ended without an outcome; the app may be shutting down")))
            self._release(running.workspace, running.unit, mark)
            self._running.pop(rid, None)
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
            self.steps.release(running)
            self.updater.job_ended()
            # `0043` R5 a: after the mark is gone, so the pass sees the unit free.
            self._autopilot_nudge(running.workspace)
            if recorder is not None and not recorder.closed:
                # The runner closes it on every road that writes an `end`. Left open means the
                # app is going down -- what can be written is, with no `end` (C9) -- or the
                # runner raised before its own `finally`, which is an ending like any other.
                if going_down:
                    await recorder.abandon()
                else:
                    await recorder.close("failed", "the step ended without an outcome")
            if recorder is not None:
                self._recorders.pop(recorder.run, None)

    async def stop_step(self, cwd: str, unit: str, by: str) -> dict[str, Any]:
        """Stop one running board step (`0034` R2, R5, R6). The route and the page's
        button both call this, and nothing else.

        `by` is a name the person typed, not an identity: the password names nobody. What it
        leaves is an `end` record with `outcome: stopped` and `stopped_by` -- or nothing at
        all when the cancel lands before the step's first turn (`_never_driven`). It opens
        and closes no gate, and starts nothing.
        """
        self._workspace_or_refuse(cwd)
        name = (by or "").strip() or OWNER
        return await self._stop_running(self._journal_key(cwd), unit, name)

    async def _stop_running(self, key: str, unit: str, by: str) -> dict[str, Any]:
        """The Stop itself, shared with `0068`'s "apply now" so a step it cuts ends the
        same way: an `end` record with `stopped` and `stopped_by`, or none for a step
        cancelled before its first turn."""
        # `0114` R2: an integration is listed beside the steps but has no Stop; say what
        # holds the unit rather than that nothing runs.
        mark = self._active.get((key, unit))
        if mark is not None and mark.kind == "integrate":
            raise Invalid(steps_mod.describe(unit, mark))
        try:
            running = self.steps.request_stop(key, unit, by)
        except (steps_mod.NotRunning, steps_mod.Finishing) as e:
            raise Invalid(str(e)) from e
        await running.handle.close()
        if running.task is not None:
            running.task.cancel()
        return {"unit": running.unit, "stage": running.stage, "stopped_by": running.stopped_by}

    def running_steps(self, cwd: str) -> list[dict[str, Any]]:
        """The board steps running now in this workspace (`0034` R13). This process only.

        `0114` R1: and every integration, from its `_running` entry to the `finally` that
        pops it, with `kind: "integration"` and no `run`; a step is `kind: "step"`. A restart
        that asks this sees an integration it would cut."""
        self._workspace_or_refuse(cwd)
        key = self._journal_key(cwd)
        rows = [{**r, "kind": "step"} for r in self.steps.listing(key)]
        rows += [
            {"unit": e["unit"], "stage": "integrate", "started_at": e["started"], "stopping": False,
             "run": None, "kind": "integration"}
            for e in self._running.values()
            if e["workspace"] == key and e["stage"] == "integrate"
        ]
        return sorted(rows, key=lambda r: r["started_at"])
