"""What is written into a unit from outside a step: review rounds posted to the pull
request, transitions, answers, outcomes, holds and review rounds allowed."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from coscc.units import autopilot, backlog
from coscc.units import board as board_reader
from coscc.git import gh, gitops
from coscc.units import hold as hold_rules
from coscc.units import more_rounds as more_rounds_rules
from coscc.agent import agents, policy
from coscc.github import prcomment, prscope, prsync
from coscc.units.board import Unavailable
from coscc.git.gitops import GitError
from coscc.units.history import UNKNOWN, BadTransition
from coscc.data import Data
from coscc.units.meta import DELEGATION, MetaError, UnitMeta
from coscc.runlog.journal import BadRecord, Journal
from coscc.data import Busy, Unusable
from coscc.units import submit
from coscc.units import transitions
from coscc.agent import steps as steps_mod
from coscc import units
from coscc.units import worktrees
from coscc.units import BadUnit, CannotCreate, ideas
from coscc.service.autopilot import autopilot_values
from coscc.service.common import Invalid, OUTCOME_RESULTS, OWNER
from coscc.config import Config
from coscc.service.workspaces import Workspaces
from coscc.service.common import Holds
from coscc.service.agents import Agents
from coscc.service.backlog import Backlog
from coscc.service.ideas import Ideas
from collections.abc import Callable

log = logging.getLogger(__name__)

# The kinds of a decision, the longest text one may carry (chosen, not measured), and the
# agent a delegation may name: Leif answers in the person's place from outside the app.
DECISION_KINDS = ("decision", "delegation")
DECISION_TEXT_MAX = 2000
DELEGATES = ("Leif",)


def decision_id(d: Any) -> str:
    return f"D{d.get('id')}"


def in_force(d: Any, day: str, workspace: str) -> bool:
    """Decision `d` holds on `day` (ISO) in `workspace` (a slot): from its first day,
    to its last if it has one, before the day it was withdrawn, and in its workspace or all."""
    day = str(day or "")
    start, until = str(d.get("from_day") or ""), str(d.get("until_day") or "")
    gone, where = str(d.get("withdrawn") or ""), str(d.get("workspace") or "")
    return (
        bool(day)
        and bool(start)
        and start <= day
        and (not until or day <= until)
        and (not gone or day < gone)
        and (not where or where == workspace)
    )


def opens_with(by: Any, names: Any) -> bool:
    """`by` opens with one of `names`, case aside, and the name ends there or at a character
    that is not a letter: `Leif (CoS)` does, `Leifson` does not."""
    b = str(by or "").strip().casefold()
    for n in names:
        n = str(n or "").strip().casefold()
        if n and b.startswith(n) and (len(b) == len(n) or not b[len(n)].isalpha()):
            return True
    return False


class Answers:
    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        holds: Holds,
        agents: Agents,
        backlog: Backlog,
        ideas: Ideas,
        nudge: Callable[..., None],
    ) -> None:
        self.config = config
        self.ws = ws
        self.holds = holds
        self.agents = agents
        self.backlog = backlog
        self.ideas = ideas
        self.nudge = nudge
        # Held across read-check-append so two answers arriving together cannot interleave.
        # The page and the API share this instance, so one lock covers both.
        self._answer_lock = asyncio.Lock()
        # Held across read-comments-then-post, so two presses of *Post to PR* for one round
        # run in turn and the second finds the first's marker. One process only.
        self._comment_lock = asyncio.Lock()
        # Per workspace, created on first use.
        self._create_locks: dict[str, asyncio.Lock] = {}

    async def post_new_rounds(self, cwd: str, unit: str, before: set[Any]) -> list[dict[str, Any]]:
        """Post every round the step just added. Never raises.

        What happened to each is in the run log; the board shows a failed one as *not on the PR*.
        """
        try:
            data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
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
        self.ws.check(cwd)
        try:
            n = int(round_n)
        except TypeError, ValueError:
            raise Invalid(f"a round is named by its number, got {round_n!r}") from None
        async with self._comment_lock:
            try:
                data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
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
        reviewer = self.agents.agent("review")
        result = await prcomment.post(
            unit,
            n,
            rnd.get("verdict"),
            rnd.get("text") or "",
            pr_url,
            str(Path(cwd).expanduser().resolve()),
            author=agents.label(reviewer) if reviewer is not None else "",
        )
        record: dict[str, Any] = {
            "kind": "pr-comment",
            "workspace": self.ws.key(cwd),
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
        journal = self.ws.journal()
        if journal is not None:
            try:
                journal.append(record)
            except Busy, BadRecord, OSError:
                pass
        return {"unit": unit, "round": n, "pr": pr_url, **result.as_dict()}

    async def sync_pr(
        self, cwd: str, unit: str, pr_before: str | None, stage: str = "pr"
    ) -> dict[str, Any]:
        """Put `pr.md`'s title and body onto its pull request. Never raises.

        Called by `drive` after a `pr` step that was not stopped, and by `run_step` before
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
            text = await board_reader.pr_text(
                self.ws.units_root(cwd), unit, state=self.ws.snapshot(cwd, [unit])
            )
            url = str(text.get("url") or "")
            if "error" in text:
                outcome, detail = "skipped", str(text["error"])
            elif text.get("status") != "accepted":
                outcome, detail = (
                    "skipped",
                    f"pr.md is {text.get('status') or 'without a status'}, not accepted",
                )
            elif not url or not gh.PR_URL_RE.match(url):
                outcome, detail = "skipped", f"pr.md names no pull request URL: {url!r}"
            else:
                where = str(Path(cwd).expanduser().resolve())
                result = await prsync.sync(
                    url, text.get("title"), str(text.get("body") or ""), where
                )
                outcome, detail = result.state, result.reason
                if stage == "pr":
                    scope = await prscope.read(url, text.get("scope"), where)
        except Unavailable as e:
            detail = str(e)
        except Exception as e:
            # The step is done whatever this does.
            log.exception("the %s of %s could not be read", stage, unit)
            detail = str(e) or type(e).__name__
        record: dict[str, Any] = {"unit": unit, "stage": stage, "pr": url}
        if stage == "pr":
            record["existed"] = None if pr_before is None else bool(pr_before)
        record["outcome"] = outcome
        if outcome in ("failed", "skipped"):
            record["detail"] = detail
        if scope is not None:
            record["scope"] = scope
        journal = self.ws.journal()
        if journal is not None:
            try:
                journal.append({"kind": "pr-sync", "workspace": self.ws.key(cwd), **record})
            except Busy, BadRecord, OSError:
                pass
        return record

    async def ingest(
        self, cwd: str, unit: str, done: dict[str, Any], wrote: str | None = None
    ) -> dict[str, Any]:
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
        meta = self.ws.unit_meta()
        workspace = self.ws.key(cwd)
        stage = str(done.get("stage") or "")
        submitted = done.get("submitted") if wrote else None
        try:
            await asyncio.to_thread(
                meta.ingest,
                workspace,
                self.ws.units_root(cwd),
                unit,
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
            log.warning("%s in %s could not be read after its step: %s", unit, workspace, e)
            if isinstance(e, BadTransition):
                reason = str(e) or "a status it read is not one the app records"
            elif isinstance(e, (Busy, sqlite3.Error)):
                reason = "the database could not be written"
            else:
                reason = "its files could not be read"
            try:
                meta.ingest_failed(workspace, unit, reason)
            except Busy, sqlite3.Error, OSError:
                pass
            return {"ingest_error": reason}

    def _apply_result(
        self,
        meta: UnitMeta,
        workspace: str,
        unit: str,
        stage: str,
        artifact: str,
        submitted: dict[str, Any],
        done: dict[str, Any],
    ) -> None:
        """The stage result a run submitted, as the status of `artifact`: one transition
        through guard `stage-result`, and the rows `UnitMeta.record_result` writes, in one
        transaction with its event."""
        journal = self.ws.journal() or Journal(meta.root, self.config.data_dir)
        obj = dict(submitted.get("object") or {})
        applied = transitions.apply(
            meta.history,
            journal,
            machine="unit",
            transition="result",
            workspace=workspace,
            unit=unit,
            artifact=artifact,
            to_state=submit.JUDGEMENTS[str(obj.get("judgement"))],
            inputs=submitted,
            authority="agent",
            run=str(submitted.get("run") or UNKNOWN),
            session=str(done.get("session_id") or "") or UNKNOWN,
            actor=f"stage:{stage}",
            source=f"run:{stage}",
            also=lambda conn: meta.record_result(conn, workspace, unit, stage, artifact, submitted),
        )
        if not applied.open:
            raise BadTransition(
                f"guard {applied.guard} refused {artifact}: {', '.join(applied.reasons)}"
            )

    def _apply_round(
        self,
        meta: UnitMeta,
        workspace: str,
        unit: str,
        stage: str,
        artifact: str,
        submitted: dict[str, Any],
        done: dict[str, Any],
    ) -> None:
        """The round a review run submitted, as the status of `review.md`: one transition
        through guard `review-round`, reading the head the app recorded when the run opened,
        and the round's rows, in one transaction with its event. The verdict reaches the
        history whole, `changes-requested` and all."""
        journal = self.ws.journal() or Journal(meta.root, self.config.data_dir)
        obj = dict(submitted.get("object") or {})
        applied = transitions.apply(
            meta.history,
            journal,
            machine="unit",
            transition="round",
            workspace=workspace,
            unit=unit,
            artifact=artifact,
            to_state=submit.ROUND_STATES[str(obj.get("verdict"))],
            inputs=submitted,
            authority="agent",
            run=str(submitted.get("run") or UNKNOWN),
            session=str(done.get("session_id") or "") or UNKNOWN,
            actor=f"stage:{stage}",
            source=f"run:{stage}",
            also=lambda conn: meta.record_round(conn, workspace, unit, submitted),
        )
        if not applied.open:
            raise BadTransition(
                f"guard {applied.guard} refused {artifact}: {', '.join(applied.reasons)}"
            )

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
        self.ws.check(cwd)
        if depends_on and not idea:
            raise Invalid(
                "depends_on needs an idea: it names a unit already under the idea's Units."
            )
        linked = self.ideas.idea_link(cwd, idea, brief, depends_on) if idea else None
        root = Path(cwd).expanduser().resolve()
        async with self._create_lock(cwd):
            reserve = [root]
            try:
                reserve += [
                    Path(t["path"])
                    for t in (await gitops.worktree_list(root))[1:]
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
                    raise Invalid(
                        f"{made['unit']} was made, but {idea} could not list it: {e}"
                    ) from e
                made["idea"] = idea
                self.ideas.refresh_ideas(linked["home"])
            # The new unit's row, and its `idea.md`'s status.
            made.update(
                await self.ingest(cwd, made["unit"], {"outcome": "done", "stage": "create"})
            )
            try:
                made["worktree"] = await worktrees.ensure(
                    cwd, made["unit"], None, self.config.data_dir
                )
            except (GitError, BadUnit) as e:
                made["worktree"] = {"path": "", "error": str(e)}
        return made

    async def worktree(self, cwd: str, unit: str, strict: bool = False) -> dict[str, Any] | None:
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
                branch = units.branch_name(
                    cwd, unit, self.config.data_dir, self.ws.snapshot(cwd, [unit])
                )
                await gitops.rev_parse(Path(cwd).expanduser().resolve(), f"refs/heads/{branch}")
            except CannotCreate, BadUnit, GitError:
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
        self.ws.check(cwd)
        name = str(answered_by or "").strip() or OWNER
        done = await self._append_answers(
            cwd,
            unit,
            [(artifact, question, answer)],
            name,
            "product",
            f"human:{name}",
            "answer",
            delegation=str(delegation or "").strip(),
        )
        written = done["written"][0]
        # The answer itself starts nothing; a pass may, if the switch is on.
        self.nudge(self.ws.key(cwd))
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
        """The one place that records an answer: `items` is `[(artifact, question, text)]`, all
        checked and written under one hold of `_answer_lock` and one board read. A refusal
        raises. Returns `{written: [{artifact, question}], date}`.

        Each row says whose answer it is by the road it came, never by the name typed: one
        under a delegation `delegated`, any other `person`.
        """
        authority = "delegated" if delegation else "person"
        name = answered_by
        today = date.today().isoformat()
        written: list[dict[str, Any]] = []
        async with self._answer_lock:
            try:
                data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            texts: list[str] = []
            for artifact, question, answer in items:
                number, finding, text = self._append_one(
                    cwd,
                    unit,
                    found,
                    artifact,
                    question,
                    str(answer or "").strip("\n"),
                    name,
                    today,
                    delegation,
                )
                written.append({"artifact": artifact, "question": finding or number})
                texts.append(text)
            self._record_answers(cwd, unit, found, written, texts, name, today, via, authority)

        # The store is not a git repository, so provenance is a row in `outputs`. Never
        # raises: the answer is recorded, and failing now would say it was not.
        history = self.backlog.history()
        if history is not None:
            for w in written:
                try:
                    history.add_output(
                        self.ws.key(cwd),
                        unit,
                        w["artifact"].removesuffix(".md"),
                        "deliverable",
                        w["artifact"],
                        actor=actor,
                        source=source,
                    )
                except OSError, BadTransition, Busy:
                    pass

        return {"written": written, "date": today}

    def _record_answers(
        self,
        cwd: str,
        unit: str,
        found: dict[str, Any],
        written: list[dict[str, Any]],
        texts: list[str],
        name: str,
        today: str,
        via: str,
        authority: str = "person",
    ) -> None:
        """Each answer's row in `unit_answers` and its `answer` record in the run log in one
        transaction: all are written or none is. Raises `Invalid` when none was.

        One `answer` record per answer lets the run log tell which answer finished a draft's
        questions (`completes`). With no run log there is only the row to write.
        """
        if not written:
            return
        key = self.ws.key(cwd)
        meta = self.ws.unit_meta()

        def rows(conn) -> None:
            for w, text in zip(written, texts):
                meta.add_answer(
                    key,
                    unit,
                    w["artifact"],
                    w["question"],
                    text,
                    name,
                    today,
                    via,
                    conn=conn,
                    authority=authority,
                )

        journal = self.ws.journal()
        try:
            if journal is None:
                with meta.data.write() as conn:
                    rows(conn)
                return
            listed, _ = backlog.shortlist_of(journal.records(workspace=key, kind="shortlist"))
            on = bool(autopilot_values(self.config, key)["autopilot"])
            stages = {s["file"]: s for s in found.get("stages") or []}
            given: dict[str, set[Any]] = {}
            records = []
            for w in written:
                artifact = w["artifact"]
                given.setdefault(artifact, set()).add(w["question"])
                row = stages.get(artifact) or {}
                records.append(
                    {
                        "kind": "answer",
                        "workspace": key,
                        "unit": unit,
                        "stage": row.get("stage", ""),
                        "artifact": artifact,
                        "question": w["question"],
                        "via": via,
                        "authority": authority,
                        "status": row.get("status", ""),
                        "completes": autopilot.answer_completes(found, artifact, given[artifact]),
                        "autopilot": on,
                        "shortlisted": unit in ((listed or {}).get("units") or []),
                        "held": bool(found.get("hold")),
                    }
                )
            journal.append_with(records, rows)
        except (BadRecord, Busy, sqlite3.Error, OSError) as e:
            # The error goes to the log, not the dialog: `Busy` names the database's path.
            said = ", ".join(f"{w['artifact']} {w['question']}" for w in written)
            log.warning("the answer was not recorded (%s): %s", said, e)
            raise Invalid(f"the answer was not recorded ({said})") from e

    def _append_one(
        self,
        cwd: str,
        unit: str,
        found: dict[str, Any],
        artifact: str,
        question: Any,
        text: str,
        name: str,
        today: str,
        delegation: str = "",
    ) -> tuple[int | str, str, str]:
        """Check one answer against the board read `found`. Raises `Invalid`; returns
        `(number, finding, text)`, the text as its row keeps it. `_record_answers` writes it."""
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
            except TypeError, ValueError:
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
        # before the write, like `Holds.take`.
        mark = self.holds.marks.get((self.ws.key(cwd), unit))
        if mark is not None and mark.kind == "step" and not policy.is_prose_stage(mark.stage):
            row = next((r for r in found.get("stages") or [] if r.get("stage") == mark.stage), None)
            if row is not None and row.get("file") == artifact:
                raise Invalid(
                    f"{artifact} cannot be answered while the {mark.stage} step that writes it "
                    "is running; answer it once the step ends"
                )
        if delegation:
            # Last of the refusals, before the row is written.
            text = (
                f"{text}\n\n{DELEGATION} {self._delegation_or_refuse(cwd, name, delegation, today)}"
            )
        return number, finding, text.strip()

    def _delegation_or_refuse(self, cwd: str, name: str, delegation: str, today: str) -> str:
        """The `D<n>` an answer under `name` may cite today in `cwd`, or `Invalid` naming why
        not. What the delegation `covers` is not checked."""
        cited = str(delegation or "").strip()
        if not re.fullmatch(r"D\d+", cited):
            raise Invalid(f"a delegation is named D<n>, got {delegation!r}")
        try:
            rows = Data(self.config.data_dir).decisions()
        except (Unusable, sqlite3.Error, OSError) as e:
            raise Invalid(f"the decisions could not be read, so nothing was written: {e}") from e
        d = next((d for d in rows if decision_id(d) == cited), None)
        if d is None:
            raise Invalid(f"there is no decision {cited}")
        if d["kind"] != "delegation":
            raise Invalid(f"{cited} is a decision, not a delegation")
        if d["workspace"] and d["workspace"] != units.slot(cwd):
            raise Invalid(f"{cited} does not cover this workspace")
        if not in_force(d, today, units.slot(cwd)):
            raise Invalid(f"{cited} is not in force today")
        if not opens_with(name, [d["agent"]]):
            raise Invalid(f"{cited} delegates to {d['agent']}, and this answer is under {name}")
        return cited

    # -- the person's decisions ------------------------
    #
    # Called only by the Settings screen's handlers (`coscc/state/answers.py`). No route
    # reaches these: over HTTP an agent could make "the person's decision" itself.

    def decisions_table(self) -> dict[str, Any]:
        """Every decision, withdrawn and expired included, each with its `state` and its
        workspace by name, and the workspace names the form offers."""
        today = date.today().isoformat()
        rows = self.ws.all()["workspaces"]
        names = {units.slot(r["path"]): str(r["name"]) for r in rows}
        try:
            found = Data(self.config.data_dir).decisions()
        except (Unusable, sqlite3.Error, OSError) as e:
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
            out.append(
                {
                    **d,
                    "id": decision_id(d),
                    "state": state,
                    "workspace_name": names.get(d["workspace"], "a removed workspace")
                    if d["workspace"]
                    else "All workspaces",
                }
            )
        return {"rows": out, "workspaces": sorted(dict.fromkeys(names.values()))}

    def add_decision(self, fields: dict[str, Any]) -> dict[str, Any]:
        """One new decision from the Settings form, or `Invalid` with one sentence. `from` is
        today, set here: a form that took it could date a decision before the blocks it relabels."""
        get = lambda k: str(fields.get(k) or "").strip()
        kind, text, source, until, where = (
            get("kind"),
            get("text"),
            get("source"),
            get("until"),
            get("workspace"),
        )
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
            slot = next(
                (units.slot(r["path"]) for r in self.ws.all()["workspaces"] if r["name"] == where),
                "",
            )
            if not slot:
                raise Invalid("Choose all workspaces or one of the app's workspaces.")
        if kind == "delegation":
            if agent not in DELEGATES:
                raise Invalid(f"Choose {' or '.join(DELEGATES)} for a delegation.")
            if not covers:
                raise Invalid("Say which questions the delegation covers.")
            if "\n" in covers or "\r" in covers:
                raise Invalid("What it covers must fit on one line.")
        else:
            agent = covers = ""
        try:
            n = Data(self.config.data_dir).decision_add(
                kind=kind,
                text=text,
                source=source,
                workspace=slot,
                agent=agent,
                covers=covers,
                from_day=today,
                until_day=until,
            )
        except (Unusable, sqlite3.Error, OSError) as e:
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
        except (Unusable, sqlite3.Error, OSError) as e:
            raise Invalid(f"{cited} could not be withdrawn: {e}") from e
        return {"withdrawn": cited, **self.decisions_table()}

    async def record_outcome(  # noqa: C901, PLR0915 - still to split
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
        self.ws.check(cwd)
        name = str(recorded_by or "").strip() or OWNER
        measurer = str(measured_by or "").strip()
        word = str(result or "").strip()
        src = str(source or "").strip()
        why = str(reason or "").strip()
        text = str(note or "").strip("\n")
        async with self._answer_lock:
            try:
                data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            nxt = str(found.get("next") or "")
            if found.get("why") != "finished":
                raise Invalid(
                    f"{unit} is {nxt or 'not finished'}; an outcome is recorded only on a finished unit"
                )
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

            path = self.ws.unit_dir(cwd, unit) / "intent.md"
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as e:
                raise Invalid(f"could not read intent.md: {e}") from e
            lines = existing.splitlines()
            heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
            if heading is not None and any(l.startswith("## ") for l in lines[heading + 1 :]):
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
        history = self.backlog.history()
        if history is not None:
            try:
                history.add_output(
                    self.ws.key(cwd),
                    unit,
                    "intent",
                    "deliverable",
                    "intent.md",
                    actor=f"human:{name}",
                    source="outcome",
                )
            except OSError, BadTransition, Busy:
                pass

        return {
            "unit": unit,
            "result": word,
            "measured_by": measurer,
            "recorded_by": name,
            "date": today,
        }

    async def append_to_answers(self, path: Path, block: str, what: str) -> None:
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
            if heading is not None and any(l.startswith("## ") for l in lines[heading + 1 :]):
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
        self.ws.check(cwd)
        journal = self.ws.journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a hold cannot be recorded — set COS_WORKING_DIR"
            )
        if not unit:
            raise Invalid("name a work unit")
        to = str(to or "").strip()
        reason = str(reason or "").strip()
        by = str(by or "").strip() or OWNER
        self.ws.unit_dir(cwd, unit)
        key = self.ws.key(cwd)
        # No `await` between the check and the take: the same mark `run_step` and
        # `integrate` take, so neither starts while this writes. When the unit is already
        # held, the board is still read, so a move refused for another reason says that one.
        held = self.holds.marks.get((key, unit))
        mark = self.holds.take(key, unit, "hold") if held is None else None
        try:
            try:
                data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            said = hold_rules.refusal(
                found, to, reason, by, steps_mod.describe(unit, held) if held else ""
            )
            if said:
                raise Invalid(said)
            assert found is not None
            from_ = (found.get("hold") or {}).get("state") or "active"
            today = date.today().isoformat()

            effects: list[dict[str, str]] = []
            if to == "dropped":
                try:
                    branch = units.branch_name(
                        cwd, unit, self.config.data_dir, self.ws.snapshot(cwd, [unit])
                    )
                except CannotCreate, BadUnit:
                    branch = ""
                root = str(Path(cwd).expanduser().resolve())
                effects.append(await hold_rules.close_pr(root, branch))
                effects.append(await hold_rules.remove_tree(cwd, unit, self.config.data_dir))
            # The hold's row and its run-log record in one transaction, after the effects
            # the record names.
            meta = self.ws.unit_meta()
            try:
                journal.append_with(
                    [
                        hold_rules.record(
                            workspace=key,
                            unit=unit,
                            from_=from_,
                            to=to,
                            reason=reason,
                            by=by,
                            effects=effects,
                        )
                    ],
                    lambda conn: meta.add_hold(
                        key, unit, to, reason, by, today, "product", conn=conn
                    ),
                )
            except (BadRecord, Busy, sqlite3.Error, OSError) as e:
                done = "; ".join(f"{x['effect']}: {x['result']}" for x in effects)
                log.warning("the hold of %s was not recorded: %s", unit, e)
                raise Invalid("the hold was not recorded" + (f" ({done})" if done else "")) from e
        finally:
            if mark is not None:
                self.holds.release(key, unit, mark)
        return {
            "unit": unit,
            "from": from_,
            "to": to,
            "reason": reason,
            "by": by,
            "date": today,
            "effects": effects,
        }

    async def more_rounds(self, cwd: str, unit: str, by: str) -> dict[str, Any]:
        """A person allows one more review round to a unit that used all of its.

        Appends one `### More rounds` block under `review.md ## Answers`, never rewriting a
        byte above it. Whether the unit is out of rounds is `cos.mjs`'s `moreRounds`. Not an
        approval; it starts nothing and does not wake the autopilot. `by` is `owner` when
        none. Refused while a step or an integration of this unit runs; it holds that same
        mark itself while it writes.
        """
        self.ws.check(cwd)
        if not unit:
            raise Invalid("name a work unit")
        by = str(by or "").strip() or OWNER
        directory = self.ws.unit_dir(cwd, unit)
        key = self.ws.key(cwd)
        # No `await` between the check and the take, as in `hold`.
        held = self.holds.marks.get((key, unit))
        mark = self.holds.take(key, unit, "more-rounds") if held is None else None
        try:
            try:
                data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            said = more_rounds_rules.refusal(
                found, by, steps_mod.describe(unit, held) if held else ""
            )
            if said:
                raise Invalid(said)
            today = date.today().isoformat()
            await self.append_to_answers(
                directory / "review.md", more_rounds_rules.block(by, today), "a round"
            )
        finally:
            if mark is not None:
                self.holds.release(key, unit, mark)
        return {"unit": unit, "by": by, "date": today, "rounds": 1}
