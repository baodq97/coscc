"""The backlog: estimates, relations, the shortlist, precedent, starting a unit's branch,
and a unit's history.

Split from `coscc/service.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

from coscc import backlog
from coscc import board as board_reader
from coscc import fetches, gitops
from coscc import hold as hold_rules
from coscc import precedent as precedent_mod
from coscc.board import Unavailable
from coscc.gitops import GitError
from coscc.history import History, settled_edits
from coscc.journal import BadRecord, Busy, Journal, timelines_of, totals_of
from coscc.policy import grant_for
from coscc import models
from coscc.runner import CEILING_MARKERS, Denials, permission_gate
from coscc.sessions import StepHandle
from coscc import steps as steps_mod
from coscc import units, worktrees
from coscc.units import BadUnit, CannotCreate
from coscc.service_common import BRANCH_REMOTE, BRANCH_TRUNK, Invalid, OWNER


class BacklogMixin:

    # -- backlog (`0074`) -----------------------------------------------------

    async def _backlog_context(self, cwd: str) -> tuple[Journal, str, dict[str, Any]]:
        """The run log, its key and one board read. The read is `node`, so it happens before
        any transaction is opened (`plan.md` Risk 5): only the run log's part of a check is
        read inside one."""
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so nothing can be recorded — set COS_WORKING_DIR")
        try:
            data = await board_reader.read(self._units_root(cwd))
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
        self, cwd: str, unit: str, value: Any, effort: Any, basis: Any, by: Any,
    ) -> dict[str, Any]:
        """R2, R6, R7. A person's estimate: always a new record, never an edit of an old one."""
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
            "kind": "estimate-value", "workspace": key, "unit": unit, "value": value, "effort": effort,
            "effort_source": "person", "similar": [], "basis": basis, "effort_basis": "", "by": by,
        }
        return {"recorded": self._append_checked(journal, record, lambda rows: "")}

    async def record_relation(
        self, cwd: str, unit: str, other: str, rtype: str, op: str, reason: str, by: str,
    ) -> dict[str, Any]:
        """R8. Add or remove one relation; checked against the ones in effect inside the write."""
        by = str(by or "").strip() or OWNER
        journal, key, data = await self._backlog_context(cwd)
        names = [u["name"] for u in data["units"]]
        record = {"kind": "relation", "workspace": key, "unit": unit, "other": other, "type": rtype,
                  "op": op, "reason": reason, "by": by}
        return {"recorded": self._append_checked(journal, record, lambda rows: backlog.check_relation(
            unit, other, rtype, op, reason, by, names, backlog.relations_of(rows),
        ))}

    async def record_shortlist(self, cwd: str, names: Any, reason: str, by: str) -> dict[str, Any]:
        """R10, R12, R13. The whole list, by a person, as one new record."""
        by = str(by or "").strip() or OWNER
        journal, key, data = await self._backlog_context(cwd)
        for what, value in (("reason", reason), ("name", by)):
            said = hold_rules._line_problem(what, value)
            if said:
                raise Invalid(said)
        if backlog.is_agent(by):
            raise Invalid(f"a person's name may not start with {backlog.AGENT_PREFIX!r}")
        waiting = [u["name"] for u in data["units"] if backlog.in_backlog(u)]
        record = {"kind": "shortlist", "workspace": key, "unit": "", "units": names, "reason": reason, "by": by}
        return {"recorded": self._append_checked(journal, record, lambda rows: backlog.check_shortlist(
            names, waiting, backlog.estimates_of(rows),
        ))}

    async def propose_estimates(self, cwd: str) -> AsyncIterator[tuple[str, Any]]:
        """R17, R18. One paid session proposes estimates and relations for the whole backlog.

        Refused before anything is spent while another proposal of this workspace runs: the
        unit `""` in `_active`, which no real unit is ever called. Streams like `integrate`.
        Writes `start`/`end` (stage `estimate`, unit `""`) so Activity counts the money, one
        `estimate` record for the run, and each valid part of the reply through
        `append_checked`. A reply over a ceiling or not JSON writes no estimate.
        """
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so a proposal cannot be recorded — set COS_WORKING_DIR")
        key = self._journal_key(cwd)
        held = self._active.get((key, ""))
        if held is not None:
            raise Invalid(f"a proposal for this workspace is already running since {held.started_at}; wait for it to end")
        mark = steps_mod.Mark("estimate", "estimate", "")
        self._active[(key, "")] = mark
        rid = self._mark_running(key, "", "estimate", "estimate")
        started = ended = False
        try:
            try:
                data = await board_reader.read(self._units_root(cwd))
                rows = journal.records(key)
            except Unavailable as e:
                raise Invalid(str(e)) from e
            except Busy as e:
                raise Invalid(str(e)) from e
            unit_timelines = timelines_of(rows)
            found = backlog.measured(unit_timelines, data["units"])
            # `0092` R11: how many finished units are left out for a cost nobody knows.
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
                {"unit": n, "idea": backlog.section(read(n, "idea.md"), "In their own words"),
                 "problem": backlog.section(read(n, "intent.md"), "Problem"),
                 "outcome": backlog.section(read(n, "intent.md"), "Proposed outcome")}
                for n in waiting
            ]
            finished = [
                {"unit": n, "title": backlog.title_of(read(n, "intent.md")), **f} for n, f in found.items()
            ]
            prompt = backlog.build_prompt(texts, finished, left_out)
            grant = grant_for("estimate")
            defaults, _ = models.load_defaults()
            model, model_source, effort, effort_source = models.resolve(
                models.ESTIMATE, None, self._model_overrides()[0], self._effort_overrides()[0], defaults,
                self.config.model,
            )
            try:
                journal.started(key, "", "estimate", "manual", started_by="person",
                                prompt_chars=len(prompt), granted=[],
                                max_turns=grant.max_turns, model=model, model_source=model_source,
                                effort=effort, effort_source=effort_source)
                started = True
            except (BadRecord, Busy):
                pass
            reply, end, failure = "", {}, ""
            try:
                async for kind, payload in self.sessions.stream(
                    cwd, prompt, None, max_turns=grant.max_turns, tools=[],
                    # `tools=[]` still lets MCP tools through (`sessions.py`); the gate refuses them.
                    can_use_tool=permission_gate(grant, cwd, Denials()),
                    max_budget_usd=grant.max_budget_usd, step=StepHandle(),
                    **({"model": model} if model is not None else {}),
                    **({"effort": effort} if effort is not None else {}),
                ):
                    if kind == "chunk":
                        reply += payload
                        yield ("chunk", payload)
                    elif kind == "session":
                        end["session_id"] = str(payload)
                    elif kind == "done":
                        end.update(session_id=payload.get("session_id", end.get("session_id", "")),
                                   cost=payload.get("cost") or {},
                                   terminal_reason=str(payload.get("terminal_reason") or ""))
            except Exception as e:  # noqa: BLE001 — recorded as the reason
                failure = f"the session failed: {e}"
            cost = end.get("cost") or {}
            terminal = end.get("terminal_reason", "")
            if not failure and any(m in terminal for m in CEILING_MARKERS):
                failure = f"the session stopped at a ceiling ({terminal}); nothing was recorded"
            session = end.get("session_id", "")
            names = [u["name"] for u in data["units"]]
            parsed = {"records": [], "rejected": [], "failed": failure or None}
            if not failure:
                parsed = backlog.parse_proposal(
                    reply, waiting, names, found, session, backlog.relations_of(rows), workspace=key,
                    undetermined=left_out,
                )
            written, rejected = 0, list(parsed["rejected"])
            for rec in parsed["records"]:
                if rec["kind"] == "relation":
                    check = (lambda rec: lambda live: backlog.check_relation(
                        rec["unit"], rec["other"], rec["type"], "add", rec["reason"], rec["by"], names,
                        backlog.relations_of(live), agent=True,
                    ))(rec)
                else:
                    check = lambda live: ""  # noqa: E731
                try:
                    self._append_checked(journal, rec, check)
                    written += 1
                except Invalid as e:
                    rejected.append({"unit": rec["unit"], "reason": str(e)})
            outcome = "failed" if parsed["failed"] else "done"
            try:
                journal.finished(key, "", "estimate", outcome, session_id=session,
                                 detail=parsed["failed"], **cost)
                ended = True
            except (BadRecord, Busy):
                pass
            summary = {
                "kind": "estimate", "workspace": key, "unit": "", "stage": "estimate", "session_id": session,
                "cost_usd": cost.get("cost_usd"), "turns": cost.get("turns"), "written": written,
                "rejected": rejected, "outcome": outcome, "detail": parsed["failed"],
            }
            try:
                summary = journal.append(summary)
            except (BadRecord, Busy):
                pass
            yield ("done", {"estimate": summary})
        finally:
            if started and not ended:
                try:
                    journal.finished(key, "", "estimate", "cancelled", detail="the proposal ended before its reply was read")
                except (BadRecord, Busy):
                    pass
            if self._active.get((key, "")) is mark:
                del self._active[(key, "")]
            self._running.pop(rid, None)
            self.updater.job_ended()

    async def precedent(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0044`. Jera answers this unit's open questions from precedent: one paid session.

        Started only by a person's press (`POST /api/units/precedent`, *Ask Jera*) — nothing
        else in the app calls this (R1). Holds the unit like a step (`_take`), so Jera, a
        step, an integration and a hold of one unit exclude each other in this process; a
        session at a terminal is not excluded (`spec.md` C6).

        Writes a `start` and an `end` (stage `precedent`, the unit's own), one `precedent`
        row per question (R9, R12), and each `answer` that survives `precedent.verdicts`
        through `_append_answers` as `Jera`, `Via: precedent`, `actor = agent:Jera` (R7). A
        `needs-person` verdict writes no byte of any artifact. A reply that cannot be read
        writes nothing either, and its tail is kept in the `end` row (R13).
        """
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so nothing Jera says can be recorded — set COS_WORKING_DIR")
        key = self._journal_key(cwd)
        mark = self._take(key, unit, "precedent", "precedent")
        rid = self._mark_running(key, unit, "precedent", "precedent")
        started = ended = False
        try:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            questions = precedent_mod.asked(found)
            if not questions:
                raise Invalid(f"{unit} has no open question Jera may answer")
            prefs = str(self.preferences().get("decision_preferences") or "")
            store = precedent_mod.entries(data["units"], prefs, {unit})
            prompt = precedent_mod.build_prompt(questions, store)
            grant = grant_for("precedent")
            defaults, _ = models.load_defaults()
            model, model_source, effort, effort_source = models.resolve(
                models.PRECEDENT, None, self._model_overrides()[0], self._effort_overrides()[0], defaults,
                self.config.model,
            )
            try:
                journal.started(key, unit, "precedent", "manual", prompt_chars=len(prompt), granted=[],
                                max_turns=grant.max_turns, model=model, model_source=model_source,
                                effort=effort, effort_source=effort_source, questions=len(questions),
                                entries=len(store))
                started = True
            except (BadRecord, Busy):
                pass
            reply, end, failure = await precedent_mod.ask(self.sessions, cwd, prompt, grant, model, effort)
            cost = end.get("cost") or {}
            session = end.get("session_id", "")
            found_v = {"failed": failure or None, "verdicts": [], "ignored": []}
            if not failure:
                found_v = precedent_mod.verdicts(reply, questions, {e["id"] for e in store})
            if found_v["failed"]:
                try:
                    # R13. The tail is chosen at 2000 characters, not measured.
                    journal.finished(key, unit, "precedent", "failed", session_id=session,
                                     detail=f"{found_v['failed']}; the reply ended: {reply[-2000:]}", **cost)
                    ended = True
                except (BadRecord, Busy):
                    pass
                return {"unit": unit, "outcome": "failed", "detail": found_v["failed"], "written": [],
                        "needs_person": [], "skipped": [], "ignored": [], "cost_usd": cost.get("cost_usd")}

            answers = [v for v in found_v["verdicts"] if v["verdict"] == precedent_mod.ANSWER]
            done = await self._append_answers(
                cwd, unit, [(v["artifact"], v["n"], precedent_mod.block_text(v)) for v in answers],
                precedent_mod.AGENT, precedent_mod.VIA, precedent_mod.ACTOR, "precedent",
            ) if answers else {"written": [], "skipped": []}
            skipped = {(s["artifact"], s["question"]): s["reason"] for s in done["skipped"]}
            for v in found_v["verdicts"]:
                at = (v["artifact"], v["n"])
                row = {"kind": "precedent", "workspace": key, "unit": unit, "artifact": v["artifact"],
                       "n": v["n"], "verdict": v["verdict"], "category": v["category"], "text": v["text"],
                       "reason": v["reason"], "cites": v["cites"], "session_id": session,
                       "written": v["verdict"] == precedent_mod.ANSWER and at not in skipped}
                if at in skipped:
                    row.update(verdict="skipped", reason=skipped[at])
                try:
                    journal.append(row)
                except (BadRecord, Busy):
                    pass
            try:
                journal.finished(key, unit, "precedent", "done", session_id=session, **cost)
                ended = True
            except (BadRecord, Busy):
                pass
            return {
                "unit": unit, "outcome": "done", "detail": None,
                "written": [{"artifact": w["artifact"], "n": w["question"]} for w in done["written"]],
                "needs_person": [{"artifact": v["artifact"], "n": v["n"]} for v in found_v["verdicts"]
                                 if v["verdict"] == precedent_mod.PERSON],
                "skipped": [{"artifact": a, "n": n, "reason": r} for (a, n), r in skipped.items()],
                "ignored": found_v["ignored"], "cost_usd": cost.get("cost_usd"),
            }
        finally:
            if started and not ended:
                try:
                    journal.finished(key, unit, "precedent", "cancelled", detail="Jera ended before its reply was read")
                except (BadRecord, Busy):
                    pass
            self._release(key, unit, mark)
            self._running.pop(rid, None)
            self.updater.job_ended()

    async def start_branch(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0014` R4. Cut this unit's branch in the workspace and switch to it.

        The name is not chosen here and is not the caller's: `cos.mjs unit-branch` reads
        the `Type:` the intent declared and prints `<type>/<slug>`. `coscc/gitops.py`
        carries the list of what the app may do with it, which is this and nothing else.

        Since `0001_product-describes-a-state-it-is-not-in` it is cut from the trunk **as the
        remote has it**, not from whatever the local `main` last saw: fetch, read the SHA
        that fetch brought, cut from that SHA (R1). If the fetch fails nothing is cut and
        the refusal says so (R2) — cutting from a stale `main` with a warning would still
        open the pull request on the wrong base. The result names the ref and the commit
        (R3). The remote and the trunk are constants here, never taken from a request.
        """
        self._workspace_or_refuse(cwd)
        try:
            name = units.branch_name(cwd, unit, self.config.data_dir)
        except (CannotCreate, BadUnit) as e:
            raise Invalid(str(e)) from e
        # `0017`. Cut in the unit's own worktree, never in the workspace: cutting there is
        # what took one unit's branch away from another. The workspace stays on `main`.
        try:
            tree = await worktrees.ensure(cwd, unit, None, self.config.data_dir)
        except (GitError, BadUnit) as e:
            raise Invalid(f"Could not open {unit}'s worktree, so no branch was cut. {e}") from e
        repo = Path(tree["path"])
        # `0048`: through the coordinator, so a step starting beside this does not race it
        # for `refs/remotes/origin/main` — and a fetch under 30s old is reused here too
        # (`spec.md ## Answers, câu 2`).
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
        # Prepared here rather than when the tree was made (`0017` plan): the lockfiles an
        # `impl` works with are the ones at the commit just cut from, not the local `main`.
        # A failure is returned, not raised — the branch is cut either way — and `run_step`
        # refuses `impl` until preparing succeeds (R6).
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
        """Which branch the workspace is on. A read, so the page can show it."""
        self._workspace_or_refuse(cwd)
        try:
            return {
                "cwd": cwd,
                "branch": await gitops.current_branch(Path(cwd).expanduser().resolve()),
            }
        except GitError as e:
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
