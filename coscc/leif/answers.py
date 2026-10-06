"""What is written into a unit from outside a step: review rounds posted to the pull
request, transitions, answers, outcomes, holds and review rounds allowed."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from datetime import date
from pathlib import Path
from collections.abc import Callable
from typing import Any

from coscc.units import backlog
from coscc.units import board as board_reader
from coscc.git import gh, gitops
from coscc.units import hold as hold_rules
from coscc.units import more_rounds as more_rounds_rules
from coscc.agent import agents, policy
from coscc.github import prcomment, prmachine, prsync
from coscc.units.board import Unavailable
from coscc.git.gitops import GitError
from coscc.units.history import UNKNOWN, BadTransition
from coscc.units.meta import By, UnitMeta
from coscc.store.journal import BadRecord, Journal
from coscc.store.db import Busy
from coscc.units import submit
from coscc.units import transitions
from coscc.runner.queue import Attempt, describe
from coscc import units
from coscc.units import scratch, worktrees
from coscc.units import BadUnit, CannotCreate
from coscc.leif import decide
from coscc.leif.autopilot import autopilot_values
from coscc.runner.queue import Refused
from coscc.kernel import OWNER
from coscc.kernel import Invalid
from coscc.config import Config
from coscc.units.workspaces import Workspaces
from coscc.runner.queue import Holds
from coscc.units.ideas import Ideas
from coscc.bus import Bus

log = logging.getLogger(__name__)

# The three results an outcome may carry, as a person types them, and the word each is kept as.
OUTCOME_RESULTS = {"đạt": "met", "trượt": "missed", "không đo được": "unmeasurable"}
# Whose decision an answer is: the two values `by` takes.
BY: tuple[By, ...] = ("person", "delegated")


def _to_send(found: dict[str, Any]) -> str:
    """What a refused `question` should have been: the open questions of the board read
    `found`, the first 60 characters of each, and the forms accepted."""
    open_ = [
        f"{q.get('artifact')} {q.get('n')} ({str(q.get('text') or '')[:60]})"
        for q in found.get("questions") or []
        if not q.get("answered")
    ]
    return (
        f"Open questions: {'; '.join(open_) or 'none'}. "
        'Send question as its number, 1 or "1", or F<n> for a finding in review.md.'
    )


def _named(unit: str, found: dict[str, Any], artifact: str, question: Any) -> tuple[str, int | str]:
    """`artifact` as its file name and `question` as an int or `F<n>`, whichever way they
    were sent, or `Invalid` saying what to send. The one place that reads either: `found` is
    the board read the check that follows uses."""
    stages = found.get("stages") or []
    wanted = str(artifact or "").strip().casefold()
    file = next(
        (s["file"] for s in stages if wanted in (s["file"].casefold(), s["stage"].casefold())), None
    )
    if file is None:
        names = ", ".join(s["file"] for s in stages)
        raise Invalid(f"{artifact!r} is not an artifact of {unit}; send one of {names}")
    if isinstance(question, int) and not isinstance(question, bool):
        return file, question
    if isinstance(question, str):
        text = question.strip()
        if re.fullmatch(r"F[0-9]+", text):
            return file, text
        if re.fullmatch(r"[0-9]+", text):
            return file, int(text)
    raise Invalid(f"{question!r} does not name a question. {_to_send(found)}")


class Answers:
    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        holds: Holds,
        agent: Callable[[str], dict[str, Any] | None],
        ideas: Ideas,
        bus: Bus,
    ) -> None:
        self.config = config
        self.ws = ws
        self.holds = holds
        self.agent = agent
        self.ideas = ideas
        self.bus = bus
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

        The body is built from the round as the loop read it; nothing a caller sends reaches
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
        if not isinstance(n, int):
            return {
                "round": None,
                "state": "failed",
                "url": "",
                "reason": "the round has no number",
            }
        pr_url = (found.get("pr") or {}).get("url") or ""
        # The `review` agent as its row names it now.
        reviewer = self.agent("review")
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
        """Put the unit's title and body onto its pull request. Never raises.

        Called by `drive` after a `pr` step that was not stopped, and by `run_step` before
        it asks the gate of a `ship` step; `stage` names which. The pull request is the PR
        machine's row, the title `pr_title`, the body `body_of`; `prsync` compares and writes.
        One with no pull request `open` or `merge-requested` is `skipped` with no `gh` call. One
        `pr-sync` row says how it went (`existed` is `None` when the lookup before a `pr` step
        could not answer). `pr.md` is never touched.
        """
        url, outcome, detail = "", "failed", ""
        try:
            key = self.ws.key(cwd)
            now = prmachine.state(self.ws.unit_meta().history, key, unit)
            url = str(now.get("url") or "")
            snap = self.ws.snapshot(cwd, [unit])
            type_ = ((snap.get("units") or {}).get(f"{snap.get('workspace')}/{unit}") or {}).get(
                "type"
            )
            if now["state"] not in prmachine.WATCHED:
                outcome, detail = "skipped", f"the pull request is {now['state']}, not open"
            elif not url or not gh.PR_URL_RE.match(url):
                outcome, detail = "skipped", f"no pull request URL is recorded: {url!r}"
            else:
                where = str(Path(cwd).expanduser().resolve())
                result = await prsync.sync(
                    url, units.pr_title(unit, type_), prmachine.body_of(unit), where
                )
                outcome, detail = result.state, result.reason
        except (Invalid, Unavailable) as e:
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
        """The record a finished step handed back, written to `cos.db`; no file is read.

        A run that ended `done` and submitted for `wrote` goes through `transitions.apply` and
        guard `stage-result` (or `review-round`); any other step writes nothing. The step still
        ends as it ended, but a failure is not dropped: the `done` item carries `ingest_error`,
        and a `unit_unknowns` row tells the snapshot. A guard that closes is a failure here.
        """
        submitted: dict[str, Any] = done.get("submitted") or {}
        if not (wrote and done.get("outcome") == "done" and submitted):
            return {}
        meta = self.ws.unit_meta()
        workspace = self.ws.key(cwd)
        stage = str(done.get("stage") or "")
        try:
            apply = self._apply_round if stage == submit.ROUND else self._apply_result
            await asyncio.to_thread(apply, meta, workspace, unit, stage, wrote, submitted, done)
            return {}
        except (BadTransition, Busy, sqlite3.Error) as e:
            # One fixed sentence on the card and the step, the error in the log: `Busy` carries
            # the database's path, a `BadTransition` names a status and nothing else.
            log.warning("%s in %s could not be recorded after its step: %s", unit, workspace, e)
            if isinstance(e, BadTransition):
                reason = str(e) or "a status it handed back is not one the app records"
            else:
                reason = "the database could not be written"
            try:
                meta.ingest_failed(workspace, unit, reason)
            except Busy, sqlite3.Error, OSError:
                pass
            return {"ingest_error": reason}

    async def _open_unit(
        self, cwd: str, unit: str, brief: bool, idea: str = "", depends_on: str = ""
    ) -> dict[str, Any]:
        """A new unit's `unit_meta` row, its `idea` and `depends` rows when opened from an idea,
        and, given a brief, its `idea.md` accepted through guard `unit-created`, in one
        transaction. A failure is told as `ingest` tells it."""
        meta = self.ws.unit_meta()
        workspace = self.ws.key(cwd)
        deps = [depends_on] if depends_on else []

        def opening(conn: sqlite3.Connection) -> None:
            meta.add_unit(conn, workspace, unit)
            if idea:
                meta.link(conn, workspace, unit, idea, deps)

        try:
            await asyncio.to_thread(self._write_opening, meta, workspace, unit, brief, opening)
            return {}
        except (BadTransition, Busy, sqlite3.Error, OSError) as e:
            log.warning("%s in %s could not be opened: %s", unit, workspace, e)
            if isinstance(e, BadTransition):
                reason = str(e) or "its first state was refused"
            else:
                reason = "the database could not be written"
            try:
                meta.ingest_failed(workspace, unit, reason)
            except Busy, sqlite3.Error, OSError:
                pass
            return {"ingest_error": reason}

    def _write_opening(
        self,
        meta: UnitMeta,
        workspace: str,
        unit: str,
        brief: bool,
        opening: Callable[[sqlite3.Connection], None],
    ) -> None:
        if not brief:
            with meta.data.write() as conn:
                opening(conn)
            return
        journal = self.ws.journal() or Journal(meta.root, self.config.data_dir)
        applied = transitions.apply(
            meta.history,
            journal,
            machine="unit",
            transition="create",
            workspace=workspace,
            unit=unit,
            artifact="idea.md",
            to_state="accepted",
            inputs={"brief": True},
            authority="code",
            actor="app:create",
            source="app:create",
            also=opening,
        )
        if not applied.open:
            raise BadTransition(
                f"guard {applied.guard} refused {unit}: {', '.join(applied.reasons)}"
            )

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

    async def create_unit(
        self, cwd: str, slug: str, brief: str = "", idea: str = "", depends_on: str = ""
    ) -> dict[str, Any]:
        """Start a work unit, in the product's store rather than the repository.

        The number and the slug grammar are the loop's. It also opens the unit's own
        worktree, detached at the workspace's `main`; a worktree that cannot be opened does
        not undo the unit: the result says why under `worktree.error`.

        With `idea`, the unit is one side of a shared idea. Everything is checked before a
        number is taken; the unit gets no `idea.md`, and its `idea` and `depends` rows in
        `unit_links` are what ties it to the idea (the idea file is never written).
        """
        self.ws.check(cwd)
        if depends_on and not idea:
            raise Invalid("depends_on needs an idea: it names another unit of the same idea.")
        if idea:
            self.ideas.idea_link(cwd, idea, brief, depends_on)
        root = Path(cwd).expanduser().resolve()
        async with self._create_locks.setdefault(units.key(cwd), asyncio.Lock()):
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
                made: dict[str, Any] = {
                    "cwd": cwd,
                    # The host repository's own `.cos/` and every worktree's count toward
                    # the number, so a unit cannot take one already used there.
                    **units.create(cwd, slug, brief, self.config.data_dir, reserve_from=reserve),
                }
            except (CannotCreate, BadUnit) as e:
                raise Invalid(str(e)) from e
            # The new unit's row, its idea's rows, and its `idea.md` accepted when it has a brief.
            made.update(
                await self._open_unit(cwd, made["unit"], bool(made.get("brief")), idea, depends_on)
            )
            if idea:
                if "ingest_error" in made:
                    raise Invalid(
                        f"{made['unit']} was made, but its idea link was not kept: "
                        f"{made['ingest_error']}"
                    )
                made["idea"] = idea
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
        by: Any,
        name: str = "",
    ) -> dict[str, Any]:
        """One answer to an open question of an artifact.

        The answer is a row in `cos.db`; the artifact is not touched. What counts as a
        question and whether it is answered is the loop's decision, read through one board
        read. Not an approval; it starts nothing itself, though with the autopilot on the
        pass it nudges may start the next stage. `by` is whose decision it is, `person` (a
        person's press) or `delegated` (decided for them), and is required: an answer without
        it is refused, never taken as a person's. `name` is whatever name the caller typed,
        `owner` when none: a claim, not an identity. No gate reads either.

        `question` may be `"F<n>"`, a finding the loop lists in the unit's `personFindings`;
        then `artifact` must be `review.md`. That row is read by `coscc.loop next` and the
        `ship` gate.
        """
        self.ws.check(cwd)
        if by not in BY:
            raise Invalid(f"say whose decision the answer is: by is {' or '.join(BY)}, got {by!r}")
        name = str(name or "").strip() or OWNER
        done = await self._append_answers(
            cwd,
            unit,
            [(artifact, question, answer)],
            by,
            name,
            "product",
        )
        written = done["written"][0]
        # The answer itself starts nothing; a pass may, if the switch is on.
        self.bus.publish("answer.written", {"workspace": self.ws.key(cwd), "unit": unit})
        return {
            "unit": unit,
            "artifact": written["artifact"],
            "question": written["question"],
            "by": by,
            "name": name,
            "date": done["date"],
        }

    async def _append_answers(
        self,
        cwd: str,
        unit: str,
        items: list[tuple[str, Any, str]],
        by: By,
        name: str,
        via: str,
    ) -> dict[str, Any]:
        """The one place that records an answer: `items` is `[(artifact, question, text)]`, all
        checked and written under one hold of `_answer_lock` and one board read. A refusal
        raises. Returns `{written: [{artifact, question}], date}`. Each row carries `by`.
        """
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
                artifact, question = _named(unit, found, artifact, question)
                number, finding, text = self._append_one(
                    cwd,
                    unit,
                    found,
                    artifact,
                    question,
                    str(answer or "").strip("\n"),
                    name,
                )
                written.append({"artifact": artifact, "question": finding or number})
                texts.append(text)
            self._record_answers(cwd, unit, found, written, texts, by, name, today, via)

        return {"written": written, "date": today}

    def _record_answers(
        self,
        cwd: str,
        unit: str,
        found: dict[str, Any],
        written: list[dict[str, Any]],
        texts: list[str],
        by: By,
        name: str,
        today: str,
        via: str,
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
                    by,
                    name,
                    today,
                    via,
                    conn=conn,
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
                        "by": by,
                        "status": row.get("status", ""),
                        "completes": decide.answer_completes(found, artifact, given[artifact]),
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
    ) -> tuple[int | str, str, str]:
        """Check one answer against the board read `found`. Raises `Invalid`; returns
        `(number, finding, text)`, the text as its row keeps it. `_record_answers` writes it."""
        # A finding the last review round confirmed needs a person is answered by its id,
        # `F<n>`, into `review.md`, and only while the loop lists it in `personFindings`.
        # `question` is what `_named` returned: an int or `F<n>`.
        finding = question if isinstance(question, str) else ""
        if finding:
            if artifact != "review.md":
                raise Invalid(
                    f"a finding is answered in review.md, not {artifact}. {_to_send(found)}"
                )
            awaited = [p["id"] for p in found.get("person_findings") or []]
            if finding not in awaited:
                raise Invalid(
                    f"{finding} is not a finding the last review round of {unit} "
                    "confirmed needs a person"
                    + (f" (those are {', '.join(awaited)})" if awaited else "")
                    + f". {_to_send(found)}"
                )
            number: int | str = finding
        else:
            asked = [q for q in found.get("questions") or [] if q.get("artifact") == artifact]
            if not asked:
                raise Invalid(
                    f"{artifact} in {unit} has no numbered item under ## Open questions. "
                    + _to_send(found)
                )
            number = question
            if number not in {q["n"] for q in asked}:
                raise Invalid(
                    f"{artifact} has no question {number} "
                    f"(it has {', '.join(str(q['n']) for q in asked)}). {_to_send(found)}"
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
        held = self.holds.attempts.holding(self.ws.key(cwd), unit)
        if (
            held is not None
            and held["machine"] == "step"
            and not policy.row_for(held["stage"]).prose
        ):
            row = next(
                (r for r in found.get("stages") or [] if r.get("stage") == held["stage"]), None
            )
            if row is not None and row.get("file") == artifact:
                raise Invalid(
                    f"{artifact} cannot be answered while the {held['stage']} step that writes it "
                    "is running; answer it once the step ends"
                )
        return number, finding, text.strip()

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
        """Record whether a finished unit met its intent's outcome: one `outcome` row in
        `unit_decisions`, under the same lock and board read as `answer()`. No file is touched.

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
            today = date.today().isoformat()
            fields = {
                "result": kind,
                "measured_by": measurer,
                "source": src,
                "reason": why,
                "note": text.strip(),
            }
            try:
                self.ws.unit_meta().add_decision(
                    self.ws.key(cwd), unit, "outcome", fields, name, today, "product"
                )
            except (Busy, sqlite3.Error, OSError) as e:
                log.warning("the outcome of %s was not recorded: %s", unit, e)
                raise Invalid("the outcome was not recorded") from e

        return {
            "unit": unit,
            "result": word,
            "measured_by": measurer,
            "recorded_by": name,
            "date": today,
        }

    async def hold(self, cwd: str, unit: str, to: str, reason: str, by: str) -> dict[str, Any]:
        """A person pauses, drops or resumes a unit (`to`: paused, dropped, active).

        Records one row in `unit_holds` and one `hold` record in the run log, in one
        transaction; `intent.md` is not touched. Which moves exist is the loop's
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
        # Checked and taken in one transaction: an attempt of its own, so no step or
        # integration starts while this writes. When the unit is already held, the board is
        # still read, so a move refused for another reason says that one.
        held, mark = self._short_attempt("hold", key, unit)
        outcome = "failed"
        try:
            try:
                data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            said = hold_rules.refusal(found, to, reason, by, describe(unit, held) if held else "")
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
                scratch.remove(cwd, unit, self.config.data_dir)
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
            outcome = "done"
        finally:
            if mark is not None:
                self.holds.attempts.move(mark, "ended", outcome)
        self.bus.publish("hold.moved", {"workspace": key, "unit": unit})
        return {
            "unit": unit,
            "from": from_,
            "to": to,
            "reason": reason,
            "by": by,
            "date": today,
            "effects": effects,
        }

    def _short_attempt(
        self, machine: str, key: str, unit: str
    ) -> tuple[Attempt | None, int | None]:
        """`(what holds the unit, None)`, or `(None, id)` of this hold's or round's own attempt,
        `running` from here: queued and moved on at once, with no slot."""
        try:
            attempt = self.holds.attempts.open(machine, key, unit)["id"]
        except Refused:
            return self.holds.attempts.holding(key, unit), None
        self.holds.attempts.move(attempt, "running")
        return None, attempt

    async def more_rounds(self, cwd: str, unit: str, by: str) -> dict[str, Any]:
        """A person allows one more review round to a unit that used all of its.

        Records one `more-rounds` row in `unit_decisions`; no file is touched. Whether the unit
        is out of rounds is the loop's `moreRounds`. Not an
        approval; it starts nothing and does not wake the autopilot. `by` is `owner` when
        none. Refused while a step or an integration of this unit runs; it holds that same
        mark itself while it writes.
        """
        self.ws.check(cwd)
        if not unit:
            raise Invalid("name a work unit")
        by = str(by or "").strip() or OWNER
        self.ws.unit_dir(cwd, unit)
        key = self.ws.key(cwd)
        # Checked and taken in one transaction, as in `hold`.
        held, mark = self._short_attempt("rounds", key, unit)
        outcome = "failed"
        try:
            try:
                data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            said = more_rounds_rules.refusal(found, by, describe(unit, held) if held else "")
            if said:
                raise Invalid(said)
            today = date.today().isoformat()
            try:
                self.ws.unit_meta().add_decision(
                    key, unit, "more-rounds", {"rounds": 1}, by, today, "product"
                )
            except (Busy, sqlite3.Error, OSError) as e:
                log.warning("the round for %s was not recorded: %s", unit, e)
                raise Invalid("the round was not recorded") from e
            outcome = "done"
        finally:
            if mark is not None:
                self.holds.attempts.move(mark, "ended", outcome)
        return {"unit": unit, "by": by, "date": today, "rounds": 1}
