"""What is written into a unit from outside a step: review rounds posted to the pull
request, transitions, answers, outcomes, holds and review rounds allowed."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

from coscc.units import autopilot, backlog
from coscc.units import board as board_reader
from coscc.git import gitops
from coscc.units import hold as hold_rules
from coscc.units import more_rounds as more_rounds_rules
from coscc.agent import agents, policy
from coscc.github import prcomment, prscope, prsync
from coscc.agent import precedent as precedent_mod
from coscc.units.board import Unavailable
from coscc.git.gitops import GitError
from coscc.units.history import UNKNOWN, BadTransition
from coscc.data import Data
from coscc.units.meta import MetaError, UnitMeta
from coscc.runlog.journal import BadRecord, Busy, Journal
from coscc.agent import submit
from coscc.units import transitions
from coscc.agent import steps as steps_mod
from coscc import units
from coscc.git import worktrees
from coscc.units import BadUnit, CannotCreate, ideas
from coscc.data import Data
from coscc.service.common import Invalid, OUTCOME_RESULTS, OWNER

# The kinds of a decision, the longest text one may carry (chosen, not measured), the
# preference holding the names marked "This was me", and the artifacts whose answers those
# names are gathered from.
DECISION_KINDS = ("decision", "delegation")
DECISION_TEXT_MAX = 2000
NAMES_MINE = "answer_names_mine"
NAMES_FROM = ("intent.md", "spec.md")


class AnswersMixin:

    async def _post_new_rounds(
        self, cwd: str, unit: str, before: set[Any]
    ) -> list[dict[str, Any]]:
        """Post every round the step just added. Never raises.

        What happened to each is in the run log; the board shows a failed one as *not on the PR*.
        """
        try:
            data = await board_reader.read(self._units_root(cwd), state=self._snapshot(cwd))
        except Unavailable as e:
            return [{"round": None, "state": "failed", "url": "", "reason": str(e)}]
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            return []
        out = []
        for rnd in found.get("rounds") or []:
            if rnd.get("n") in before:
                continue
            async with self._comment_lock:
                out.append(await self._post_round(cwd, found, rnd))
        return out

    async def post_review_comment(self, cwd: str, unit: str, round_n: Any) -> dict[str, Any]:
        """Post one review round to the unit's pull request, or say it is there.

        The body is built from the round as `cos.mjs` read it; nothing a caller sends reaches
        GitHub but the unit's name and the round's number. Not an approval; no gate reads it.
        """
        self._workspace_or_refuse(cwd)
        try:
            n = int(round_n)
        except (TypeError, ValueError):
            raise Invalid(f"a round is named by its number, got {round_n!r}") from None
        async with self._comment_lock:
            try:
                data = await board_reader.read(self._units_root(cwd), state=self._snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            rnd = next((r for r in found.get("rounds") or [] if r.get("n") == n), None)
            if rnd is None:
                have = ", ".join(str(r.get("n")) for r in found.get("rounds") or []) or "none"
                raise Invalid(f"review.md of {unit} has no round {n} (it has {have})")
            return await self._post_round(cwd, found, rnd)

    async def _post_round(
        self, cwd: str, found: dict[str, Any], rnd: dict[str, Any]
    ) -> dict[str, Any]:
        """Post one round and write one `pr-comment` row saying how it went.

        The caller holds `_comment_lock`. A row that cannot be written is dropped, not turned
        into a failure: the comment is on GitHub or it is not.
        """
        unit = found["name"]
        n = rnd.get("n")
        pr_url = (found.get("pr") or {}).get("url") or ""
        # The `review` agent as the table names it now, overrides included.
        reviewer = self._agent("review")
        result = await prcomment.post(
            unit, n, rnd.get("verdict"), rnd.get("text") or "", pr_url,
            str(Path(cwd).expanduser().resolve()),
            author=agents.label(reviewer) if reviewer is not None else "",
        )
        record: dict[str, Any] = {
            "kind": "pr-comment",
            "workspace": self._journal_key(cwd),
            "unit": unit,
            "stage": "review",
            "round": n,
            "pr": pr_url,
            "outcome": result.state,
        }
        if result.state == "failed":
            record["detail"] = result.reason
        else:
            record["comment_url"] = result.url
        journal = self._journal()
        if journal is not None:
            try:
                journal.append(record)
            except (Busy, BadRecord, OSError):
                pass
        return {"unit": unit, "round": n, "pr": pr_url, **result.as_dict()}

    async def _sync_pr(
        self, cwd: str, unit: str, pr_before: str | None, stage: str = "pr"
    ) -> dict[str, Any]:
        """Put `pr.md`'s title and body onto its pull request. Never raises.

        Called by `_drive` after a `pr` step that was not stopped, and by `run_step` before
        it asks the gate of a `ship` step; `stage` names which. The words are `cos.mjs
        pr-text`'s; `prsync` compares and writes. A `pr.md` that is not accepted or names no
        pull request is `skipped` with no `gh` call. One `pr-sync` row says how it went
        (`existed` is `None` when the lookup before a `pr` step could not answer). After a
        `pr` step `prscope` reads the pull request's own counts into the row as `scope`;
        no gate reads it. `pr.md` is never touched.
        """
        url, outcome, detail = "", "failed", ""
        scope: dict[str, Any] | None = None
        try:
            text = await board_reader.pr_text(self._units_root(cwd), unit, state=self._snapshot(cwd, [unit]))
            url = str(text.get("url") or "")
            if "error" in text:
                outcome, detail = "skipped", str(text["error"])
            elif text.get("status") != "accepted":
                outcome, detail = "skipped", f"pr.md is {text.get('status') or 'without a status'}, not accepted"
            elif not url or not prcomment.PR_URL_RE.match(url):
                outcome, detail = "skipped", f"pr.md names no pull request URL: {url!r}"
            else:
                where = str(Path(cwd).expanduser().resolve())
                result = await prsync.sync(url, text.get("title"), str(text.get("body") or ""), where)
                outcome, detail = result.state, result.reason
                if stage == "pr":
                    scope = await prscope.read(url, text.get("scope"), where)
        except Unavailable as e:
            detail = str(e)
        except Exception as e:  # noqa: BLE001 — the step is done whatever this does
            detail = str(e) or type(e).__name__
        record: dict[str, Any] = {"unit": unit, "stage": stage, "pr": url}
        if stage == "pr":
            record["existed"] = None if pr_before is None else bool(pr_before)
        record["outcome"] = outcome
        if outcome in ("failed", "skipped"):
            record["detail"] = detail
        if scope is not None:
            record["scope"] = scope
        journal = self._journal()
        if journal is not None:
            try:
                journal.append({"kind": "pr-sync", "workspace": self._journal_key(cwd), **record})
            except (Busy, BadRecord, OSError):
                pass
        return record

    def _unit_meta(self) -> UnitMeta:
        """The unit metadata store. Unlike `_history` there is one with no working folder too,
        keyed by the data directory: every board read needs a snapshot."""
        data = Data(self.config.data_dir)
        return UnitMeta(self.config.working_dir or data.root, data)

    async def _ingest(self, cwd: str, unit: str, done: dict[str, Any], wrote: str | None = None) -> dict[str, Any]:
        """The one read of a unit's files after a step that finished, prose or not: what
        changed goes into `cos.db` through `cos.mjs meta`.

        The step still ends as it ended, but a failure is not dropped: the `done` item
        carries `ingest_error`, and a `unit_unknowns` row tells the snapshot.

        A step whose run submitted a stage result takes its artifact's status and questions
        from that object, through `transitions.apply` and guard `stage-result`, never from
        the file. A guard that closes is a failure like any other here.
        """
        if done.get("outcome") != "done":
            return {}
        meta = self._unit_meta()
        workspace = self._journal_key(cwd)
        stage = str(done.get("stage") or "")
        submitted = done.get("submitted") if wrote else None
        try:
            await asyncio.to_thread(
                meta.ingest, workspace, self._units_root(cwd), unit,
                actor=f"stage:{stage}",
                session=str(done.get("session_id") or "") or UNKNOWN,
                source=f"run:{stage}",
                wrote=None if submitted else wrote,
                decided=(wrote,) if submitted else (),
            )
            if submitted:
                apply = self._apply_round if stage == submit.ROUND else self._apply_result
                await asyncio.to_thread(apply, meta, workspace, unit, stage, wrote, submitted, done)
            return {}
        except (MetaError, BadTransition, Busy, sqlite3.Error, OSError) as e:
            # One fixed sentence on the card and the step, the error in the log: `MetaError`
            # carries `cos.mjs`'s stderr or its argv, `Busy` the database's path.
            # A `BadTransition` names a status and nothing else.
            print(f"coscc: {unit} in {workspace} could not be read after its step: {e}", file=sys.stderr)
            if isinstance(e, BadTransition):
                reason = str(e) or "a status it read is not one the app records"
            elif isinstance(e, (Busy, sqlite3.Error)):
                reason = "the database could not be written"
            else:
                reason = "its files could not be read"
            try:
                meta.ingest_failed(workspace, unit, reason)
            except (Busy, sqlite3.Error, OSError):
                pass
            return {"ingest_error": reason}

    def _apply_result(
        self, meta: UnitMeta, workspace: str, unit: str, stage: str, artifact: str,
        submitted: dict[str, Any], done: dict[str, Any],
    ) -> None:
        """The stage result a run submitted, as the status of `artifact`: one transition
        through guard `stage-result`, and the rows `UnitMeta.record_result` writes, in one
        transaction with its event."""
        journal = self._journal() or Journal(meta.root, self.config.data_dir)
        obj = dict(submitted.get("object") or {})
        applied = transitions.apply(
            meta.history, journal,
            machine="unit", transition="result",
            workspace=workspace, unit=unit, artifact=artifact,
            to_state=submit.JUDGEMENTS[str(obj.get("judgement"))],
            inputs=submitted, authority="agent",
            run=str(submitted.get("run") or UNKNOWN),
            session=str(done.get("session_id") or "") or UNKNOWN,
            actor=f"stage:{stage}", source=f"run:{stage}",
            also=lambda conn: meta.record_result(conn, workspace, unit, stage, artifact, submitted),
        )
        if not applied.open:
            raise BadTransition(f"guard {applied.guard} refused {artifact}: {', '.join(applied.reasons)}")

    def _apply_round(
        self, meta: UnitMeta, workspace: str, unit: str, stage: str, artifact: str,
        submitted: dict[str, Any], done: dict[str, Any],
    ) -> None:
        """The round a review run submitted, as the status of `review.md`: one transition
        through guard `review-round`, reading the head the app recorded when the run opened,
        and the round's rows, in one transaction with its event. The verdict reaches the
        history whole, `changes-requested` and all."""
        journal = self._journal() or Journal(meta.root, self.config.data_dir)
        obj = dict(submitted.get("object") or {})
        applied = transitions.apply(
            meta.history, journal,
            machine="unit", transition="round",
            workspace=workspace, unit=unit, artifact=artifact,
            to_state=submit.ROUND_STATES[str(obj.get("verdict"))],
            inputs=submitted, authority="agent",
            run=str(submitted.get("run") or UNKNOWN),
            session=str(done.get("session_id") or "") or UNKNOWN,
            actor=f"stage:{stage}", source=f"run:{stage}",
            also=lambda conn: meta.record_round(conn, workspace, unit, submitted),
        )
        if not applied.open:
            raise BadTransition(f"guard {applied.guard} refused {artifact}: {', '.join(applied.reasons)}")

    def _refresh_ideas(self, cwd: str) -> None:
        """`cwd`'s ideas into `cos.db` again, after the app wrote one. A failure is left to
        the board: `cos.mjs` then reports the idea link it cannot find."""
        try:
            self._unit_meta().refresh_ideas(self._journal_key(cwd), self._units_root(cwd))
        except (MetaError, Busy, sqlite3.Error, OSError):
            pass

    def _create_lock(self, cwd: str) -> asyncio.Lock:
        """One lock per workspace, held across numbering and making the tree."""
        return self._create_locks.setdefault(units.key(cwd), asyncio.Lock())

    async def create_unit(
        self, cwd: str, slug: str, brief: str = "", idea: str = "", depends_on: str = ""
    ) -> dict[str, Any]:
        """Start a work unit, in the product's store rather than the repository.

        The number and the slug grammar are `cos.mjs`'s. It also opens the unit's own
        worktree, detached at the workspace's `main`; a worktree that cannot be opened does
        not undo the unit: the result says why under `worktree.error`.

        With `idea`, the unit is one side of a shared idea. Everything is checked before a
        number is taken; the unit gets no `idea.md`, and the idea gets one line under
        `## Units`. A failed append leaves a unit the idea does not list, which `cos.mjs`
        reports and whose `impl` it keeps shut.
        """
        self._workspace_or_refuse(cwd)
        if depends_on and not idea:
            raise Invalid("depends_on needs an idea: it names a unit already under the idea's Units.")
        linked = self._idea_link(cwd, idea, brief, depends_on) if idea else None
        root = Path(cwd).expanduser().resolve()
        async with self._create_lock(cwd):
            reserve = [root]
            try:
                reserve += [
                    Path(t["path"]) for t in (await gitops.worktree_list(root))[1:]
                    if (Path(t["path"]) / units.COS_DIR).is_dir()
                ]
            except GitError:
                pass
            try:
                made = {
                    "cwd": cwd,
                    # The host repository's own `.cos/` and every worktree's count toward
                    # the number, so a unit cannot take one already used there.
                    **units.create(cwd, slug, brief, self.config.data_dir, reserve_from=reserve),
                }
            except (CannotCreate, BadUnit) as e:
                raise Invalid(str(e)) from e
            if linked is not None:
                try:
                    ideas.append_unit(linked["path"], linked["ws"], made["unit"], depends_on)
                except OSError as e:
                    raise Invalid(f"{made['unit']} was made, but {idea} could not list it: {e}") from e
                made["idea"] = idea
                self._refresh_ideas(linked["home"])
            # The new unit's row, and its `idea.md`'s status.
            made.update(await self._ingest(cwd, made["unit"], {"outcome": "done", "stage": "create"}))
            try:
                made["worktree"] = await worktrees.ensure(
                    cwd, made["unit"], None, self.config.data_dir
                )
            except (GitError, BadUnit) as e:
                made["worktree"] = {"path": "", "error": str(e)}
        return made

    async def _worktree(self, cwd: str, unit: str, strict: bool = False) -> dict[str, Any] | None:
        """The unit's worktree, opened on its branch if the branch exists and it is not.

        None when there is none and none can be opened — the workspace is dirty on the
        unit's branch, or is not a git repository at all. `strict` turns the first of those
        into `Invalid`: a step must not run on a tree that is not on its unit's branch.
        """
        try:
            found = await worktrees.find(cwd, unit, self.config.data_dir)
            if found is not None and found["branch"]:
                return {"path": found["path"], "branch": found["branch"]}
            try:
                branch = units.branch_name(cwd, unit, self.config.data_dir, self._snapshot(cwd, [unit]))
                await gitops.rev_parse(Path(cwd).expanduser().resolve(), f"refs/heads/{branch}")
            except (CannotCreate, BadUnit, GitError):
                branch = None
            if branch is None:
                return {"path": found["path"], "branch": ""} if found else None
            # The branch exists and the tree is not on it: open it there.
            made = await worktrees.ensure(cwd, unit, branch, self.config.data_dir)
            return {"path": made["path"], "branch": made["branch"], "base": made.get("base")}
        except (GitError, BadUnit) as e:
            if strict:
                raise Invalid(f"{unit}'s worktree could not be opened on its branch: {e}") from e
            return None

    async def answer(
        self,
        cwd: str,
        unit: str,
        artifact: str,
        question: Any,
        answer: str,
        answered_by: str,
        delegation: str = "",
    ) -> dict[str, Any]:
        """A person answers one item under an artifact's `## Open questions`.

        The answer is a row in `cos.db`; the artifact is not touched. What counts as a
        question and whether it is answered is `cos.mjs`'s decision, read through one board
        read. Not an approval; it starts nothing itself, though with the autopilot on the
        pass it nudges may start the next stage. `answered_by` is whatever name the caller
        typed: a claim, not an identity.

        `question` may be `"F<n>"`, a finding `cos.mjs` lists in the unit's `personFindings`;
        then `artifact` must be `review.md`. That row is read by `cos.mjs next` and the
        `ship` gate.

        With `delegation` `D<n>`, the answer is an agent's under a delegation the person
        entered on Settings; `_append_one` checks it and ends the row's text with
        `Theo ủy quyền: D<n>`, which is what reads it back as `delegated`.
        """
        self._workspace_or_refuse(cwd)
        name = str(answered_by or "").strip() or OWNER
        # Jera's name marks the road a block came by, not who typed it, so a person may not take it.
        if precedent_mod.is_jera(name):
            raise Invalid(f"{precedent_mod.AGENT} is the agent that answers from precedent; answer under another name")
        done = await self._append_answers(
            cwd, unit, [(artifact, question, answer)], name, "product", f"human:{name}", "answer",
            delegation=str(delegation or "").strip(),
        )
        written = done["written"][0]
        # The answer itself starts nothing; a pass may, if the switch is on.
        self._autopilot_nudge(self._journal_key(cwd))
        return {
            "unit": unit,
            "artifact": written["artifact"],
            "question": written["question"],
            "answered_by": name,
            "date": done["date"],
        }

    async def _append_answers(
        self,
        cwd: str,
        unit: str,
        items: list[tuple[str, Any, str]],
        answered_by: str,
        via: str,
        actor: str,
        source: str,
        delegation: str = "",
    ) -> dict[str, Any]:
        """The one place that records an answer: a person's through `answer`, Jera's through
        `precedent`. `items` is `[(artifact, question, text)]`, all checked and written under
        one hold of `_answer_lock` and one board read.

        A person's refusal raises. With `via == "precedent"` every item is judged alone and
        a refused one is `skipped` with its reason: `review.md` and any `F<n>`, and a
        question already answered because a person got there while Jera ran.
        Returns `{written: [{artifact, question}], skipped: [{artifact, question, reason}],
        date}`.

        Each row says whose answer it is by the road it came, never by the name typed:
        Jera's `agent`, one under a delegation `delegated`, any other `person`.
        """
        jera = via == precedent_mod.VIA
        authority = "agent" if jera else "delegated" if delegation else "person"
        name = answered_by
        today = date.today().isoformat()
        written: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        async with self._answer_lock:
            try:
                data = await board_reader.read(self._units_root(cwd), state=self._snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            texts: list[str] = []
            for artifact, question, answer in items:
                try:
                    number, finding, text = self._append_one(
                        cwd, unit, found, artifact, question, str(answer or "").strip("\n"), name, via,
                        today, jera, delegation,
                    )
                except Invalid as e:
                    if not jera:
                        raise
                    skipped.append({"artifact": artifact, "question": question, "reason": str(e)})
                    continue
                written.append({"artifact": artifact, "question": finding or number})
                texts.append(text)
            try:
                self._record_answers(cwd, unit, found, written, texts, name, today, via, authority)
            except Invalid as e:
                if not jera:
                    raise
                skipped += [{**w, "reason": str(e)} for w in written]
                written = []

        # The store is not a git repository, so provenance is a row in `outputs`. Never
        # raises: the answer is recorded, and failing now would say it was not.
        history = self._history()
        if history is not None:
            for w in written:
                try:
                    history.add_output(
                        self._journal_key(cwd),
                        unit,
                        w["artifact"].removesuffix(".md"),
                        "deliverable",
                        w["artifact"],
                        actor=actor,
                        source=source,
                    )
                except (OSError, BadTransition, Busy):
                    pass

        return {"written": written, "skipped": skipped, "date": today}

    def _record_answers(
        self, cwd: str, unit: str, found: dict[str, Any], written: list[dict[str, Any]], texts: list[str],
        name: str, today: str, via: str, authority: str = "person",
    ) -> None:
        """Each answer's row in `unit_answers` and its `answer` record in the run log in one
        transaction: all are written or none is. Raises `Invalid` when none was.

        One `answer` record per answer lets the run log tell which answer finished a draft's
        questions (`completes`). With no run log there is only the row to write.
        """
        if not written:
            return
        key = self._journal_key(cwd)
        meta = self._unit_meta()

        def rows(conn) -> None:
            for w, text in zip(written, texts):
                meta.add_answer(key, unit, w["artifact"], w["question"], text, name, today, via, conn=conn,
                                authority=authority)

        journal = self._journal()
        try:
            if journal is None:
                with meta.data.write() as conn:
                    rows(conn)
                return
            listed, _ = backlog.shortlist_of(journal.records(workspace=key, kind="shortlist"))
            on = bool(self._autopilot_values(key)["autopilot"])
            stages = {s["file"]: s for s in found.get("stages") or []}
            given: dict[str, set[Any]] = {}
            records = []
            for w in written:
                artifact = w["artifact"]
                given.setdefault(artifact, set()).add(w["question"])
                row = stages.get(artifact) or {}
                records.append({
                    "kind": "answer", "workspace": key, "unit": unit, "stage": row.get("stage", ""),
                    "artifact": artifact, "question": w["question"], "via": via, "authority": authority,
                    "status": row.get("status", ""),
                    "completes": autopilot.answer_completes(found, artifact, given[artifact]),
                    "autopilot": on, "shortlisted": unit in ((listed or {}).get("units") or []),
                    "held": bool(found.get("hold")),
                })
            journal.append_with(records, rows)
        except (BadRecord, Busy, sqlite3.Error, OSError) as e:
            # The error goes to the log, not the dialog: `Busy` names the database's path.
            said = ", ".join(f"{w['artifact']} {w['question']}" for w in written)
            print(f"coscc: the answer was not recorded ({said}): {e}", file=sys.stderr)
            raise Invalid(f"the answer was not recorded ({said})") from e

    def _append_one(
        self, cwd: str, unit: str, found: dict[str, Any], artifact: str, question: Any, text: str,
        name: str, via: str, today: str, jera: bool, delegation: str = "",
    ) -> tuple[int | str, str, str]:
        """Check one answer against the board read `found`. Raises `Invalid`; returns
        `(number, finding, text)`, the text as its row keeps it. `_record_answers` writes it."""
        if jera:
            # Decided by the name of the file and the shape of the heading, never by what the session said.
            if artifact == "review.md" or re.fullmatch(r"F\d+", str(question).strip()):
                raise Invalid(f"{precedent_mod.AGENT} never answers in review.md or a finding")
            if any(q.get("artifact") == artifact and q.get("n") == question and q.get("answered")
                   for q in found.get("questions") or []):
                raise Invalid(f"{artifact} question {question} was answered while {precedent_mod.AGENT} ran")
        # A finding the last review round confirmed needs a person is answered by its id,
        # `F<n>`, into `review.md`, and only while `cos.mjs` lists it in `personFindings`.
        finding = str(question).strip() if isinstance(question, str) else ""
        finding = finding if re.fullmatch(r"F\d+", finding) else ""
        if finding:
            if artifact != "review.md":
                raise Invalid(f"a finding is answered in review.md, not {artifact}")
            awaited = [p["id"] for p in found.get("person_findings") or []]
            if finding not in awaited:
                raise Invalid(
                    f"{finding} is not a finding the last review round of {unit} "
                    "confirmed needs a person"
                    + (f" (those are {', '.join(awaited)})" if awaited else "")
                )
            number: int | str = finding
        else:
            asked = [q for q in found.get("questions") or [] if q.get("artifact") == artifact]
            if not asked:
                raise Invalid(f"{artifact} in {unit} has no numbered item under ## Open questions")
            try:
                number = int(question)
            except (TypeError, ValueError):
                raise Invalid(f"a question is named by its number, got {question!r}") from None
            if number not in {q["n"] for q in asked}:
                raise Invalid(
                    f"{artifact} has no question {number} "
                    f"(it has {', '.join(str(q['n']) for q in asked)})"
                )
        if not text.strip():
            raise Invalid("the answer is empty")
        if not name or "\n" in name or "\r" in name:
            raise Invalid("say who is answering, on one line")
        nxt = str(found.get("next") or "")
        # The code decides; the words are only what the refusal says.
        if found.get("why") in ("finished", "rejected"):
            raise Invalid(f"{unit} is {nxt}; its questions can no longer be answered")
        # A line that reads as a heading would end this block early or open another in the
        # prompt that renders it. Refusing is cheaper than escaping somebody's words.
        if any(line.lstrip().startswith("#") for line in text.splitlines()):
            raise Invalid("no line of an answer may start with #")
        # A stage outside the prose ones writes its artifact with its own tools whenever it
        # likes, so the questions may be renumbered while it runs. Checked with no `await`
        # before the write, like `_take`.
        mark = self._active.get((self._journal_key(cwd), unit))
        if mark is not None and mark.kind == "step" and not policy.is_prose_stage(mark.stage):
            row = next((r for r in found.get("stages") or [] if r.get("stage") == mark.stage), None)
            if row is not None and row.get("file") == artifact:
                raise Invalid(
                    f"{artifact} cannot be answered while the {mark.stage} step that writes it "
                    "is running; answer it once the step ends"
                )
        if delegation:
            # Last of the refusals, before the row is written.
            text = f"{text}\n\n{precedent_mod.DELEGATION} {self._delegation_or_refuse(cwd, name, delegation, today)}"
        return number, finding, text.strip()

    def _delegation_or_refuse(self, cwd: str, name: str, delegation: str, today: str) -> str:
        """The `D<n>` an answer under `name` may cite today in `cwd`, or `Invalid` naming why
        not. What the delegation `covers` is not checked."""
        cited = str(delegation or "").strip()
        if not re.fullmatch(r"D\d+", cited):
            raise Invalid(f"a delegation is named D<n>, got {delegation!r}")
        try:
            rows = Data(self.config.data_dir).decisions()
        except Exception as e:  # noqa: BLE001 — `Busy`, `Protected`, `Incompatible` alike
            raise Invalid(f"the decisions could not be read, so nothing was written: {e}") from e
        d = next((d for d in rows if precedent_mod.decision_id(d) == cited), None)
        if d is None:
            raise Invalid(f"there is no decision {cited}")
        if d["kind"] != "delegation":
            raise Invalid(f"{cited} is a decision, not a delegation")
        if d["workspace"] and d["workspace"] != units.slot(cwd):
            raise Invalid(f"{cited} does not cover this workspace")
        if not precedent_mod.in_force(d, today, units.slot(cwd)):
            raise Invalid(f"{cited} is not in force today")
        if not precedent_mod.opens_with(name, [d["agent"]]):
            raise Invalid(f"{cited} delegates to {d['agent']}, and this answer is under {name}")
        return cited

    # -- the person's decisions and names ------------------------
    #
    # Called only by the Settings screen's handlers (`coscc/state/answers.py`). No route
    # reaches these: over HTTP an agent could make "the person's decision" itself.

    def _who_context(self, cwd: str) -> dict[str, Any]:
        """What `precedent.entries` needs to say who decided each entry."""
        data = Data(self.config.data_dir)
        return {"workspace": units.slot(cwd) if cwd else "", "decisions": data.decisions(),
                "mine": sorted(self._names_mine(data)), "agents": self.agent_names(),
                "today": date.today().isoformat()}

    @staticmethod
    def _names_mine(data: Data) -> set[str]:
        """The names marked "This was me", casefolded; a hand-broken value is none."""
        stored = data.pref(NAMES_MINE, [])
        return {str(n).casefold() for n in stored} if isinstance(stored, list) else set()

    def decisions_table(self) -> dict[str, Any]:
        """Every decision, withdrawn and expired included, each with its `state` and its
        workspace by name, and the workspace names the form offers."""
        today = date.today().isoformat()
        rows = self.workspaces()["workspaces"]
        names = {units.slot(r["path"]): str(r["name"]) for r in rows}
        try:
            found = Data(self.config.data_dir).decisions()
        except Exception as e:  # noqa: BLE001 — `Busy`, `Protected`, `Incompatible` alike
            raise Invalid(f"The decisions could not be read: {e}") from e
        out = []
        for d in found:
            if d["withdrawn"]:
                state = "withdrawn"
            elif d["until_day"] and d["until_day"] < today:
                state = "expired"
            elif d["from_day"] > today:
                state = "not yet"
            else:
                state = "in force"
            out.append({**d, "id": precedent_mod.decision_id(d), "state": state,
                        "workspace_name": names.get(d["workspace"], "a removed workspace")
                        if d["workspace"] else "All workspaces"})
        return {"rows": out, "workspaces": sorted(dict.fromkeys(names.values()))}

    def add_decision(self, fields: dict[str, Any]) -> dict[str, Any]:
        """One new decision from the Settings form, or `Invalid` with one sentence. `from` is
        today, set here: a form that took it could date a decision before the blocks it relabels."""
        get = lambda k: str(fields.get(k) or "").strip()  # noqa: E731
        kind, text, source, until, where = get("kind"), get("text"), get("source"), get("until"), get("workspace")
        agent, covers = get("agent"), get("covers")
        today = date.today().isoformat()
        if kind not in DECISION_KINDS:
            raise Invalid("Choose decision or delegation.")
        if not text:
            raise Invalid("Write what was decided.")
        if len(text) > DECISION_TEXT_MAX:
            raise Invalid(f"The text is longer than {DECISION_TEXT_MAX} characters.")
        if not source:
            raise Invalid("Say where it was decided.")
        if "\n" in source or "\r" in source:
            raise Invalid("The source must fit on one line.")
        if until:
            try:
                valid = len(until) == 10 and date.fromisoformat(until).isoformat() == until
            except ValueError:
                valid = False
            if not valid:
                raise Invalid("The end date must be a date such as 2026-12-31.")
            if until < today:
                raise Invalid("The end date is before today.")
        slot = ""
        if where and where.casefold() not in ("all", "all workspaces"):
            slot = next((units.slot(r["path"]) for r in self.workspaces()["workspaces"] if r["name"] == where), "")
            if not slot:
                raise Invalid("Choose all workspaces or one of the app's workspaces.")
        if kind == "delegation":
            if agent not in precedent_mod.AGENTS_ALWAYS:
                raise Invalid(f"Choose {' or '.join(precedent_mod.AGENTS_ALWAYS)} for a delegation.")
            if not covers:
                raise Invalid("Say which questions the delegation covers.")
            if "\n" in covers or "\r" in covers:
                raise Invalid("What it covers must fit on one line.")
        else:
            agent = covers = ""
        try:
            n = Data(self.config.data_dir).decision_add(
                kind=kind, text=text, source=source, workspace=slot, agent=agent, covers=covers,
                from_day=today, until_day=until,
            )
        except Exception as e:  # noqa: BLE001 — `Busy`, `Protected`, `Incompatible` alike
            raise Invalid(f"The decision could not be saved: {e}") from e
        return {"added": f"D{n}", **self.decisions_table()}

    def withdraw_decision(self, decision_id: Any) -> dict[str, Any]:
        """Withdraw one decision in force: its row stays, with today's date."""
        cited = str(decision_id or "").strip()
        today = date.today().isoformat()
        table = self.decisions_table()
        row = next((r for r in table["rows"] if r["id"] == cited), None)
        if row is None:
            raise Invalid(f"There is no decision {cited or '(none)'}.")
        if row["state"] in ("withdrawn", "expired"):
            raise Invalid(f"{cited} is {row['state']} already.")
        try:
            Data(self.config.data_dir).decision_withdraw(int(cited[1:]), today)
        except Exception as e:  # noqa: BLE001 — `Busy`, `Protected`, `Incompatible` alike
            raise Invalid(f"{cited} could not be withdrawn: {e}") from e
        return {"withdrawn": cited, **self.decisions_table()}

    async def answer_names(self) -> dict[str, Any]:
        """Every name in `Answered by:` of an answer in force in `intent.md` or `spec.md`, in
        every workspace, less `owner` and every agent's name; each with how many answers carry
        it and whether it is marked "This was me". An unreadable workspace is a `problems` line."""
        agent_names = self.agent_names()
        mine = self._names_mine(Data(self.config.data_dir))
        found: dict[str, dict[str, Any]] = {}
        problems: list[str] = []
        peers = self._peer_table()[0]
        for w in self.workspaces()["workspaces"]:
            if w.get("missing"):
                continue
            try:
                data = await board_reader.read(
                    self._units_root(w["path"]), state=self._snapshot(w["path"], peers=peers)
                )
            except (Unavailable, Invalid) as e:
                problems.append(f"{w['name']} could not be read: {e}")
                continue
            for u in data["units"]:
                for a in u.get("answers") or []:
                    by = str(a.get("by") or "").strip()
                    if a.get("artifact") not in NAMES_FROM or not by:
                        continue
                    key = by.casefold()
                    if key == OWNER or precedent_mod.is_agent_name(by, agent_names):
                        continue
                    row = found.setdefault(key, {"name": by, "count": 0, "mine": key in mine})
                    row["count"] += 1
        rows = sorted(found.values(), key=lambda r: (-r["count"], r["name"].casefold()))
        return {"rows": rows, "problems": problems}

    def set_name_mine(self, name: Any, on: bool) -> dict[str, Any]:
        """Mark one name as the person's, or unmark it. Stored casefolded under `NAMES_MINE`,
        which `PREFERENCES` does not list, so `set_preference` cannot write it."""
        shown = str(name or "").strip()
        if not shown or "\n" in shown or "\r" in shown:
            raise Invalid("Name one name, on one line.")
        if shown.casefold() == OWNER:
            raise Invalid("owner already counts as you.")
        if precedent_mod.is_agent_name(shown, self.agent_names()):
            raise Invalid(f"{shown} is an agent's name, so it cannot be yours.")
        key = shown.casefold()

        def change(current: Any) -> list[str]:
            have = {str(n) for n in current} if isinstance(current, list) else set()
            return sorted(have | {key}) if on else sorted(have - {key})

        try:
            Data(self.config.data_dir).update_pref(NAMES_MINE, change, [])
        except Exception as e:  # noqa: BLE001 — `Busy`, `Protected`, `Incompatible` alike
            raise Invalid(f"The name could not be saved: {e}") from e
        return {"name": shown, "mine": bool(on)}

    async def record_outcome(
        self,
        cwd: str,
        unit: str,
        result: str,
        measured_by: str,
        source: str = "",
        reason: str = "",
        note: str = "",
        recorded_by: str = "",
    ) -> dict[str, Any]:
        """Record whether a finished unit met its intent's outcome.

        Same lock, board read and refusal when a section follows `## Answers` as `answer()`,
        and appended, so every byte above the block stays as the stage left it. The block is
        `### Outcome` under `intent.md`'s `## Answers`; `cos.mjs` reads whether it is valid.

        Not an approval; no gate reads it. `recorded_by` and `measured_by` are names somebody
        typed, so both are claims. `source` is not checked against anything.
        """
        self._workspace_or_refuse(cwd)
        name = str(recorded_by or "").strip() or OWNER
        measurer = str(measured_by or "").strip()
        word = str(result or "").strip()
        src = str(source or "").strip()
        why = str(reason or "").strip()
        text = str(note or "").strip("\n")
        async with self._answer_lock:
            try:
                data = await board_reader.read(self._units_root(cwd), state=self._snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            nxt = str(found.get("next") or "")
            if found.get("why") != "finished":
                raise Invalid(f"{unit} is {nxt or 'not finished'}; an outcome is recorded only on a finished unit")
            if word not in OUTCOME_RESULTS:
                raise Invalid(f"the result is one of {', '.join(OUTCOME_RESULTS)}, got {word!r}")
            kind = OUTCOME_RESULTS[word]
            if not name or "\n" in name or "\r" in name:
                raise Invalid("say who is recording, on one line")
            if not measurer or "\n" in measurer or "\r" in measurer:
                raise Invalid("say who measured it — agent, or a person's name — on one line")
            if "\n" in src or "\r" in src:
                raise Invalid("the source is one line")
            if "\n" in why or "\r" in why:
                raise Invalid("the reason is one line")
            if kind != "unmeasurable" and not src:
                raise Invalid("the result needs a source: where the figure it rests on came from")
            if kind == "unmeasurable" and not why:
                raise Invalid("the result needs a reason: why it could not be measured")
            # As in `answer()`: a heading would end this block early or open another.
            if any(line.lstrip().startswith("#") for line in [src, why, *text.splitlines()]):
                raise Invalid("no line of an outcome may start with #")

            path = self._unit_dir(cwd, unit) / "intent.md"
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as e:
                raise Invalid(f"could not read intent.md: {e}") from e
            lines = existing.splitlines()
            heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
            if heading is not None and any(l.startswith("## ") for l in lines[heading + 1:]):
                raise Invalid(
                    "intent.md has a section after its ## Answers, so a block appended at "
                    "the end would not be read as an outcome"
                )

            today = date.today().isoformat()
            block = ""
            if existing and not existing.endswith("\n"):
                block += "\n"
            if heading is None:
                block += "\n## Answers\n"
            block += (
                "\n### Outcome\n"
                f"Answered by: {name}. Date: {today}. Via: product.\n\n"
                f"Result: {word}\n"
                f"Measured by: {measurer}\n"
            )
            if src:
                block += f"Source: {src}\n"
            if why:
                block += f"Reason: {why}\n"
            if text.strip():
                block += f"\n{text}\n"
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(block)
            except OSError as e:
                raise Invalid(f"could not write intent.md: {e}") from e

        # As above: provenance is a row in `outputs`; a failure to write it never fails a
        # block already on disk.
        history = self._history()
        if history is not None:
            try:
                history.add_output(
                    self._journal_key(cwd),
                    unit,
                    "intent",
                    "deliverable",
                    "intent.md",
                    actor=f"human:{name}",
                    source="outcome",
                )
            except (OSError, BadTransition, Busy):
                pass

        return {
            "unit": unit,
            "result": word,
            "measured_by": measurer,
            "recorded_by": name,
            "date": today,
        }

    async def _append_to_answers(self, path: Path, block: str, what: str) -> None:
        """Append `block` to the end of `path`, under its `## Answers`, opening that section
        when the file has none; never rewrites a byte above it. Under `_answer_lock`.
        `what` names the block in the refusal when a section follows `## Answers`, where an
        appended block would not be read."""
        name = path.name
        async with self._answer_lock:
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as e:
                raise Invalid(f"could not read {name}: {e}") from e
            lines = existing.splitlines()
            heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
            if heading is not None and any(l.startswith("## ") for l in lines[heading + 1:]):
                raise Invalid(
                    f"{name} has a section after its ## Answers, so a block appended at "
                    f"the end would not be read as {what}"
                )
            text = ""
            if existing and not existing.endswith("\n"):
                text += "\n"
            if heading is None:
                text += "\n## Answers\n"
            text += block
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(text)
            except OSError as e:
                raise Invalid(f"could not write {name}: {e}") from e

    async def hold(self, cwd: str, unit: str, to: str, reason: str, by: str) -> dict[str, Any]:
        """A person pauses, drops or resumes a unit (`to`: paused, dropped, active).

        Records one row in `unit_holds` and one `hold` record in the run log, in one
        transaction; `intent.md` is not touched. Which moves exist is `cos.mjs`'s
        `holdMoves`. Dropping also closes the unit's open pull request with this machine's
        `gh` login and removes its worktree; a failure there is reported, never raised.

        Not an approval; it starts nothing, even on a resume. `by` is whatever name the
        caller typed. Refused while a step or an integration of this unit runs; it holds
        that same mark itself while it writes, so no step can begin halfway through.
        """
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so a hold cannot be recorded — set COS_WORKING_DIR")
        if not unit:
            raise Invalid("name a work unit")
        to = str(to or "").strip()
        reason = str(reason or "").strip()
        by = str(by or "").strip() or OWNER
        self._unit_dir(cwd, unit)
        key = self._journal_key(cwd)
        # No `await` between the check and the take: the same mark `run_step` and
        # `integrate` take, so neither starts while this writes. When the unit is already
        # held, the board is still read, so a move refused for another reason says that one.
        held = self._active.get((key, unit))
        mark = self._take(key, unit, "hold") if held is None else None
        try:
            try:
                data = await board_reader.read(self._units_root(cwd), state=self._snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            said = hold_rules.refusal(found, to, reason, by, steps_mod.describe(unit, held) if held else "")
            if said:
                raise Invalid(said)
            assert found is not None
            from_ = (found.get("hold") or {}).get("state") or "active"
            today = date.today().isoformat()

            effects: list[dict[str, str]] = []
            if to == "dropped":
                try:
                    branch = units.branch_name(cwd, unit, self.config.data_dir, self._snapshot(cwd, [unit]))
                except (CannotCreate, BadUnit):
                    branch = ""
                root = str(Path(cwd).expanduser().resolve())
                effects.append(await hold_rules.close_pr(root, branch))
                effects.append(await hold_rules.remove_tree(cwd, unit, self.config.data_dir))
            # The hold's row and its run-log record in one transaction, after the effects
            # the record names.
            meta = self._unit_meta()
            try:
                journal.append_with(
                    [hold_rules.record(
                        workspace=key, unit=unit, from_=from_, to=to, reason=reason, by=by, effects=effects,
                    )],
                    lambda conn: meta.add_hold(key, unit, to, reason, by, today, "product", conn=conn),
                )
            except (BadRecord, Busy, sqlite3.Error, OSError) as e:
                done = "; ".join(f"{x['effect']}: {x['result']}" for x in effects)
                print(f"coscc: the hold of {unit} was not recorded: {e}", file=sys.stderr)
                raise Invalid("the hold was not recorded" + (f" ({done})" if done else "")) from e
        finally:
            if mark is not None:
                self._release(key, unit, mark)
        return {"unit": unit, "from": from_, "to": to, "reason": reason, "by": by, "date": today, "effects": effects}

    async def more_rounds(self, cwd: str, unit: str, by: str) -> dict[str, Any]:
        """A person allows one more review round to a unit that used all of its.

        Appends one `### More rounds` block under `review.md ## Answers`, never rewriting a
        byte above it. Whether the unit is out of rounds is `cos.mjs`'s `moreRounds`. Not an
        approval; it starts nothing and does not wake the autopilot. `by` is `owner` when
        none. Refused while a step or an integration of this unit runs; it holds that same
        mark itself while it writes.
        """
        self._workspace_or_refuse(cwd)
        if not unit:
            raise Invalid("name a work unit")
        by = str(by or "").strip() or OWNER
        directory = self._unit_dir(cwd, unit)
        key = self._journal_key(cwd)
        # No `await` between the check and the take, as in `hold`.
        held = self._active.get((key, unit))
        mark = self._take(key, unit, "more-rounds") if held is None else None
        try:
            try:
                data = await board_reader.read(self._units_root(cwd), state=self._snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            said = more_rounds_rules.refusal(found, by, steps_mod.describe(unit, held) if held else "")
            if said:
                raise Invalid(said)
            today = date.today().isoformat()
            await self._append_to_answers(
                directory / "review.md", more_rounds_rules.block(by, today), "a round"
            )
        finally:
            if mark is not None:
                self._release(key, unit, mark)
        return {"unit": unit, "by": by, "date": today, "rounds": 1}
