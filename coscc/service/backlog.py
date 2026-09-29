"""The backlog: estimates, relations, the shortlist, starting a unit's branch,
and a unit's history. A mixin with no fields."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

from coscc.units import backlog, guards
from coscc.units import board as board_reader
from coscc.git import fetches, gitops
from coscc.units import hold as hold_rules
from coscc.units import submit as submit_mod
from coscc.units.submit import RUN_SUBMITTED, submitted
from coscc.units.board import Unavailable
from coscc.git.gitops import GitError
from coscc.units.history import History, settled_edits
from coscc.runlog.journal import BadRecord, Journal, timelines_of, totals_of
from coscc.data import Busy
from coscc.agent.policy import grant_for
from coscc.agent import models
from coscc.runner import Denials, permission_gate
from coscc.runner.reply import CEILING_MARKERS
from coscc.agent.sessions import StepHandle, Suspended
from coscc.agent import steps as steps_mod
from coscc.service.resume import nothing, resume_kwargs
from coscc import units
from coscc.units import worktrees
from coscc.units import BadUnit, CannotCreate
from coscc.service.common import BRANCH_REMOTE, BRANCH_TRUNK, Invalid, OWNER

log = logging.getLogger(__name__)


def _labelled(row: dict[str, Any]) -> dict[str, Any]:
    """One transition row for the timeline: its `inputs` as an object, the label of its guard
    (`""` for a guard `guards` does not know), and `head`, the SHA its guard read, if any."""
    try:
        inputs = json.loads(row.get("inputs") or "{}")
    except TypeError, ValueError:
        inputs = {}
    inputs = inputs if isinstance(inputs, dict) else {}
    known = guards.GUARDS.get(str(row.get("guard") or ""))
    return {
        **row,
        "inputs": inputs,
        "guard_label": known.label if known else "",
        "head": str(inputs.get("merge_commit") or inputs.get("head") or ""),
    }


class BacklogMixin:
    # -- backlog --------------------------------------------------------------

    async def _backlog_context(self, cwd: str) -> tuple[Journal, str, dict[str, Any]]:
        """The run log, its key and one board read. The read is `node`, so it happens before any
        transaction is opened; only the run log's part of a check is read inside one."""
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so nothing can be recorded — set COS_WORKING_DIR"
            )
        try:
            data = await board_reader.read(self._units_root(cwd), state=self._snapshot(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e
        return journal, self._journal_key(cwd), data

    @staticmethod
    def _append_checked(journal: Journal, record: dict[str, Any], check: Any) -> dict[str, Any]:
        """`check(rows)` returns a refusal or `""`, on the rows read inside the transaction."""

        def refuse(rows: list[dict[str, Any]]) -> None:
            said = check(rows)
            if said:
                raise BadRecord(said)

        try:
            return journal.append_checked(record, backlog.KINDS, refuse)
        except (BadRecord, Busy) as e:
            raise Invalid(str(e)) from e

    async def record_estimate(
        self,
        cwd: str,
        unit: str,
        value: Any,
        effort: Any,
        basis: Any,
        by: Any,
    ) -> dict[str, Any]:
        """A person's estimate: always a new record, never an edit of an old one."""
        by = str(by or "").strip() or OWNER
        journal, key, data = await self._backlog_context(cwd)
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if unit not in [u["name"] for u in data["units"] if backlog.in_backlog(u)]:
            raise Invalid(f"{unit or 'that unit'} is not in the backlog")
        said = backlog.check_estimate(value, effort, basis, by, agent=False)
        if said:
            raise Invalid(said)
        record = {
            "kind": "estimate-value",
            "workspace": key,
            "unit": unit,
            "value": value,
            "effort": effort,
            "effort_source": "person",
            "similar": [],
            "basis": basis,
            "effort_basis": "",
            "by": by,
        }
        return {"recorded": self._append_checked(journal, record, lambda rows: "")}

    async def record_relation(
        self,
        cwd: str,
        unit: str,
        other: str,
        rtype: str,
        op: str,
        reason: str,
        by: str,
    ) -> dict[str, Any]:
        """Add or remove one relation; checked against the ones in effect inside the write."""
        by = str(by or "").strip() or OWNER
        journal, key, data = await self._backlog_context(cwd)
        names = [u["name"] for u in data["units"]]
        record = {
            "kind": "relation",
            "workspace": key,
            "unit": unit,
            "other": other,
            "type": rtype,
            "op": op,
            "reason": reason,
            "by": by,
        }
        return {
            "recorded": self._append_checked(
                journal,
                record,
                lambda rows: backlog.check_relation(
                    unit,
                    other,
                    rtype,
                    op,
                    reason,
                    by,
                    names,
                    backlog.relations_of(rows),
                ),
            )
        }

    async def record_shortlist(self, cwd: str, names: Any, reason: str, by: str) -> dict[str, Any]:
        """The whole list, by a person, as one new record."""
        by = str(by or "").strip() or OWNER
        journal, key, data = await self._backlog_context(cwd)
        for what, value in (("reason", reason), ("name", by)):
            said = hold_rules._line_problem(what, value)
            if said:
                raise Invalid(said)
        if backlog.is_agent(by):
            raise Invalid(f"a person's name may not start with {backlog.AGENT_PREFIX!r}")
        waiting = [u["name"] for u in data["units"] if backlog.in_backlog(u)]
        record = {
            "kind": "shortlist",
            "workspace": key,
            "unit": "",
            "units": names,
            "reason": reason,
            "by": by,
        }
        return {
            "recorded": self._append_checked(
                journal,
                record,
                lambda rows: backlog.check_shortlist(
                    names,
                    waiting,
                    backlog.estimates_of(rows),
                ),
            )
        }

    async def propose_estimates(
        self,
        cwd: str,
        resume: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """One paid session proposes estimates and relations for the whole backlog.

        Refused before anything is spent while another proposal of this workspace runs: the
        unit `""` in `_active`, which no real unit is called. Streams like `integrate`.
        Writes `start`/`end` (stage `estimate`, unit `""`) so Activity counts the money, one
        `estimate` record, and each valid part of the reply through `append_checked`. A
        session over a ceiling, or one that handed back no object through `submit`, writes
        no estimate.
        """
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a proposal cannot be recorded — set COS_WORKING_DIR"
            )
        key = self._journal_key(cwd)
        held = self._active.get((key, ""))
        if held is not None:
            raise Invalid(
                f"a proposal for this workspace is already running since {held.started_at}; wait for it to end"
            )
        mark = steps_mod.Mark("estimate", "estimate", "")
        self._active[(key, "")] = mark
        rid = self._mark_running(key, "", "estimate", "estimate")
        started = ended = False
        try:
            try:
                data = await board_reader.read(self._units_root(cwd), state=self._snapshot(cwd))
                rows = journal.records(key)
            except Unavailable as e:
                raise Invalid(str(e)) from e
            except Busy as e:
                raise Invalid(str(e)) from e
            unit_timelines = timelines_of(rows)
            found = backlog.measured(unit_timelines, data["units"])
            # How many finished units are left out for a cost nobody knows.
            left_out = len(backlog.undetermined(unit_timelines, data["units"]))
            waiting = [u["name"] for u in data["units"] if backlog.in_backlog(u)]
            if not waiting:
                raise Invalid("the backlog is empty; there is nothing to estimate")
            root = self._units_root(cwd)

            def read(unit: str, name: str) -> str:
                try:
                    return (root / unit / name).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return ""

            texts = [
                {
                    "unit": n,
                    "idea": backlog.section(read(n, "idea.md"), "In their own words"),
                    "problem": backlog.section(read(n, "intent.md"), "Problem"),
                    "outcome": backlog.section(read(n, "intent.md"), "Proposed outcome"),
                }
                for n in waiting
            ]
            finished = [
                {"unit": n, "title": backlog.title_of(read(n, "intent.md")), **f}
                for n, f in found.items()
            ]
            prompt = backlog.build_prompt(texts, finished, left_out)
            grant = grant_for("estimate")
            defaults, _ = models.load_defaults()
            model, model_source, effort, effort_source = models.resolve(
                models.ESTIMATE,
                None,
                self._model_overrides()[0],
                self._effort_overrides()[0],
                defaults,
                self.config.model,
            )
            start_at = ((resume or {}).get("owner") or {}).get("start_at")
            if resume is None:
                try:
                    start_at = journal.started(
                        key,
                        "",
                        "estimate",
                        "manual",
                        started_by="person",
                        prompt_chars=len(prompt),
                        granted=[],
                        max_turns=grant.max_turns,
                        model=model,
                        model_source=model_source,
                        effort=effort,
                        effort_source=effort_source,
                    ).get("at")
                    started = True
                except BadRecord, Busy:
                    pass
            else:
                # The `start` was written before the update; this ends it.
                started = True
            ask = resume_kwargs(resume, grant, prompt)
            used_up = ask.pop("used_up", "")
            reply, end, failure = "", {}, ""
            # The estimate is the object handed back, never the reply's words.
            collector = submit_mod.Collector("estimate")
            try:
                async for kind, payload in (
                    nothing()
                    if used_up
                    else self.sessions.stream(
                        cwd,
                        ask.pop("text"),
                        ask.pop("session_id"),
                        tools=[],
                        # `tools=[]` still lets MCP tools through (`sessions.py`); the gate refuses
                        # every one but `submit`.
                        can_use_tool=permission_gate(grant, cwd, Denials()),
                        step=StepHandle(),
                        mcp_servers={submit_mod.SERVER: collector.server()},
                        owner={
                            "kind": "estimate",
                            "workspace": key,
                            "workspace_dir": cwd,
                            "unit": "",
                            "stage": "estimate",
                            "start_at": start_at,
                        },
                        **ask,
                        **({"model": model} if model is not None else {}),
                        **({"effort": effort} if effort is not None else {}),
                    )
                ):
                    if kind == "chunk":
                        reply += payload
                        yield ("chunk", payload)
                    elif kind == "session":
                        end["session_id"] = str(payload)
                    elif kind == "done":
                        end.update(
                            session_id=payload.get("session_id", end.get("session_id", "")),
                            cost=payload.get("cost") or {},
                            terminal_reason=str(payload.get("terminal_reason") or ""),
                        )
            except Suspended:
                # An update paused it and wrote its `suspend` row; no `end` here.
                ended = True
                raise
            except Exception as e:
                # Recorded as the reason.
                log.exception("the estimate session failed")
                failure = f"the session failed: {e}"
            if used_up and not failure:
                failure = f"the session stopped at a ceiling ({used_up}) before the update"
            cost = end.get("cost") or {}
            terminal = end.get("terminal_reason", "")
            if not failure and any(m in terminal for m in CEILING_MARKERS):
                failure = f"the session stopped at a ceiling ({terminal}); nothing was recorded"
            # No object, no estimate, whatever the reply says.
            if not failure and not submitted(collector):
                failure = backlog.NO_OBJECT
            session = end.get("session_id", "")
            names = [u["name"] for u in data["units"]]
            parsed: dict[str, Any] = {"records": [], "rejected": [], "failed": failure or None}
            if not failure:
                parsed = backlog.parse_proposal(
                    collector.object(),
                    waiting,
                    names,
                    found,
                    session,
                    backlog.relations_of(rows),
                    workspace=key,
                    undetermined=left_out,
                )
            written, rejected = 0, list(parsed["rejected"])
            for rec in parsed["records"]:
                if rec["kind"] == "relation":
                    check = (
                        lambda rec: (
                            lambda live: backlog.check_relation(
                                rec["unit"],
                                rec["other"],
                                rec["type"],
                                "add",
                                rec["reason"],
                                rec["by"],
                                names,
                                backlog.relations_of(live),
                                agent=True,
                            )
                        )
                    )(rec)
                else:
                    check = lambda live: ""
                try:
                    self._append_checked(journal, rec, check)
                    written += 1
                except Invalid as e:
                    rejected.append({"unit": rec["unit"], "reason": str(e)})
            outcome = "failed" if parsed["failed"] else "done"
            try:
                journal.finished(
                    key,
                    "",
                    "estimate",
                    outcome,
                    session_id=session,
                    detail=parsed["failed"],
                    guard=RUN_SUBMITTED,
                    **cost,
                )
                ended = True
            except BadRecord, Busy:
                pass
            summary = {
                "kind": "estimate",
                "workspace": key,
                "unit": "",
                "stage": "estimate",
                "session_id": session,
                "cost_usd": cost.get("cost_usd"),
                "turns": cost.get("turns"),
                "written": written,
                "rejected": rejected,
                "outcome": outcome,
                "detail": parsed["failed"],
            }
            try:
                summary = journal.append(summary)
            except BadRecord, Busy:
                pass
            yield ("done", {"estimate": summary})
        finally:
            if started and not ended:
                try:
                    journal.finished(
                        key,
                        "",
                        "estimate",
                        "cancelled",
                        detail="the proposal ended before its reply was read",
                    )
                except BadRecord, Busy:
                    pass
            if self._active.get((key, "")) is mark:
                del self._active[(key, "")]
            self._running.pop(rid, None)
            self.updater.job_ended()

    async def start_branch(self, cwd: str, unit: str) -> dict[str, Any]:
        """Cut this unit's branch in the workspace and switch to it.

        The name is not the caller's: `cos.mjs unit-branch` reads the `Type:` the intent
        declared and prints `<type>/<slug>`. `coscc/git/gitops.py` lists what the app may do
        with it.

        It is cut from the trunk **as the remote has it**: fetch, read the SHA that fetch
        brought, cut from that SHA. If the fetch fails nothing is cut and the refusal says so,
        since cutting from a stale `main` would open the pull request on the wrong base. The
        result names the ref and the commit. The remote and the trunk are constants here.
        """
        self._workspace_or_refuse(cwd)
        try:
            name = units.branch_name(cwd, unit, self.config.data_dir, self._snapshot(cwd, [unit]))
        except (CannotCreate, BadUnit) as e:
            raise Invalid(str(e)) from e
        # Cut in the unit's own worktree, never in the workspace: cutting there took one unit's
        # branch away from another. The workspace stays on `main`.
        try:
            tree = await worktrees.ensure(cwd, unit, None, self.config.data_dir)
        except (GitError, BadUnit) as e:
            raise Invalid(f"Could not open {unit}'s worktree, so no branch was cut. {e}") from e
        repo = Path(tree["path"])
        # Through the coordinator, so a step starting beside this does not race it for
        # `refs/remotes/origin/main`; a fetch under 30s old is reused here too.
        try:
            await fetches.fetch(repo, BRANCH_REMOTE, BRANCH_TRUNK)
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
        # Prepared here rather than when the tree was made: the lockfiles an `impl` works with are
        # the ones at the commit just cut from. A failure is returned, not raised (the branch is cut
        # either way), and `run_step` refuses `impl` until preparing succeeds.
        prepared = await worktrees.prepare(repo, cwd, data_dir=self.config.data_dir)
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
        """Which branch the workspace is on. A read."""
        self._workspace_or_refuse(cwd)
        try:
            return {
                "cwd": cwd,
                "branch": await gitops.current_branch(Path(cwd).expanduser().resolve()),
            }
        except GitError as e:
            raise Invalid(str(e)) from e

    def timeline(self, cwd: str, unit: str) -> dict[str, Any]:
        """What has happened to one unit, oldest first.

        `transitions` beside `runs`, each row of the log with the one-sentence label of the
        guard that decided it; `""` for a row that names none.
        """
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        history = self._history()
        if journal is None or history is None:
            return {"cwd": cwd, "unit": unit, "runs": [], "cost": {}, "transitions": []}
        key = self._journal_key(cwd)
        try:
            runs = journal.timeline(key, unit)
            rows = history.transitions(key, unit)
        except Busy as e:
            raise Invalid(str(e)) from e
        return {
            "cwd": cwd,
            "unit": unit,
            "runs": runs,
            "cost": totals_of(runs),
            "transitions": [_labelled(r) for r in rows],
        }

    def _history(self) -> History | None:
        """The transition log, or `None` when there is no working folder to keep it in.

        Same reasoning as `_journal`: with nothing set, the safe direction to fail in is read-only.
        """
        return (
            History(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )

    def unit_history(self, cwd: str, unit: str) -> dict[str, Any]:
        """Everything the log knows about one unit.

        **Beside the board, not instead of it.** `board()` still asks `cos.mjs` and reads state
        out of the `Status:` line on disk; this answers from the transition log. Two sources
        is deliberate and has a cost.

        `state` is a projection over `transitions`, computed and never stored. It is returned
        alongside the transitions so a caller can check one against the other.

        `settled_edits` is the number of times an artifact was rewritten after it had been settled.
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
        # Rows written under one state set and read under another compare words that never meant
        # the same thing, and nothing about that failure looks like a failure: every query still
        # returns rows. Said out loud in the payload rather than refused, because refusing a *read*
        # would hide the only evidence there is. A caller that compares these against another source
        # must stop here.
        foreign = [name for name in written_under if name != history.machine.name]
        return {
            "cwd": cwd,
            "unit": unit,
            "recording": True,
            "machine": history.machine.name,
            "written_under": written_under,
            "mixed_state_sets": (
                None
                if not foreign
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

        Not the same list as `board()`'s: a unit retired from the working tree still has a
        history, and this is the only place it can be seen.
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
