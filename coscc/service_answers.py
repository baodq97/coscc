"""What is written into a unit from outside a step: review rounds posted to the pull
request, transitions, answers, outcomes and holds.

Split from `coscc/service.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path
from typing import Any

from coscc import autopilot, backlog
from coscc import board as board_reader
from coscc import gitops
from coscc import hold as hold_rules
from coscc import prcomment, prsync
from coscc import precedent as precedent_mod
from coscc.board import Unavailable
from coscc.gitops import GitError
from coscc.history import UNKNOWN, BadTransition
from coscc.journal import BadRecord, Busy
from coscc.runner import STATUS_RE
from coscc import steps as steps_mod
from coscc import units, worktrees
from coscc.units import BadUnit, CannotCreate
from coscc.service_common import Invalid, OUTCOME_RESULTS, OWNER


class AnswersMixin:

    async def _post_new_rounds(
        self, cwd: str, unit: str, before: set[Any]
    ) -> list[dict[str, Any]]:
        """`0021` R2. Post every round the step just added. Never raises.

        What happened to each is in the run log whatever it was, and the board shows a
        round that did not make it as *not on the PR* with the reason.
        """
        try:
            data = await board_reader.read(self._units_root(cwd))
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
        """`0021` R8, R9. Post one review round to the unit's pull request, or say it is there.

        The body is built from the round as `cos.mjs` read it out of `review.md`; nothing a
        caller sends reaches GitHub but the unit's name and the round's number. Not an
        approval, and it opens no gate: neither gate reads comments (R10).
        """
        self._workspace_or_refuse(cwd)
        try:
            n = int(round_n)
        except (TypeError, ValueError):
            raise Invalid(f"a round is named by its number, got {round_n!r}") from None
        async with self._comment_lock:
            try:
                data = await board_reader.read(self._units_root(cwd))
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
        """Post one round and write one `pr-comment` row saying how it went (R13).

        The caller holds `_comment_lock`. A row that cannot be written is dropped rather
        than turned into a failure: the comment is on GitHub or it is not, and that is what
        the person asked about. The next board read then shows the round as not posted, and
        a second press finds the marker and says `already`.
        """
        unit = found["name"]
        n = rnd.get("n")
        pr_url = (found.get("pr") or {}).get("url") or ""
        result = await prcomment.post(
            unit, n, rnd.get("verdict"), rnd.get("text") or "", pr_url,
            str(Path(cwd).expanduser().resolve()),
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

    async def _sync_pr(self, cwd: str, unit: str, pr_before: str | None) -> dict[str, Any]:
        """`0055` R3, R5. Put `pr.md`'s title and body onto its pull request. Never raises.

        Called from `_drive` after a `pr` step that was not stopped, and from nowhere else
        (R4). The words are `cos.mjs pr-text`'s; `prsync` compares and writes. A `pr.md` that
        is not accepted or names no pull request is `skipped` with no `gh` call. One
        `pr-sync` row says how it went, `existed` from the lookup before the step -- `None`
        when that lookup could not answer, never a guess; a row
        that cannot be written is dropped, as `_post_round` drops one. `pr.md` is never
        touched.
        """
        url, outcome, detail = "", "failed", ""
        try:
            text = await board_reader.pr_text(self._units_root(cwd), unit)
            url = str(text.get("url") or "")
            if "error" in text:
                outcome, detail = "skipped", str(text["error"])
            elif text.get("status") != "accepted":
                outcome, detail = "skipped", f"pr.md is {text.get('status') or 'without a status'}, not accepted"
            elif not url or not prcomment.PR_URL_RE.match(url):
                outcome, detail = "skipped", f"pr.md names no pull request URL: {url!r}"
            else:
                result = await prsync.sync(
                    url, text.get("title"), str(text.get("body") or ""),
                    str(Path(cwd).expanduser().resolve()),
                )
                outcome, detail = result.state, result.reason
        except Unavailable as e:
            detail = str(e)
        except Exception as e:  # noqa: BLE001 — R5: the step is done whatever this does
            detail = str(e) or type(e).__name__
        record: dict[str, Any] = {
            "unit": unit,
            "stage": "pr",
            "pr": url,
            "existed": None if pr_before is None else bool(pr_before),
            "outcome": outcome,
        }
        if outcome in ("failed", "skipped"):
            record["detail"] = detail
        journal = self._journal()
        if journal is not None:
            try:
                journal.append({"kind": "pr-sync", "workspace": self._journal_key(cwd), **record})
            except (Busy, BadRecord, OSError):
                pass
        return record

    def _record_transition(
        self, cwd: str, unit: str, artifact: str, directory: Path, done: dict[str, Any]
    ) -> None:
        """`0014` R6. One transition per step that finished, written as it happens.

        This is the first writer into `0013`'s log that is not the git import.
        `.cos/0013_.../ship.md` said the loop would come back here: history imported from
        git carries no actor and no session, because git knows neither, so the provenance
        that unit built is only ever true of work done **after** it. This is that work.

        Never raises into the run. A step that did its job and then failed to be recorded
        has still done its job, and turning a bookkeeping failure into a failed step would
        cost real money for nothing. The failure is dropped rather than shown, and that is
        a cost `0014` `impl.md` states rather than hides.
        """
        if done.get("outcome") != "done":
            return
        history = self._history()
        if history is None:
            return
        try:
            text = (directory / artifact).read_text(encoding="utf-8", errors="replace")
            found = STATUS_RE.search(text)
            if not found:
                return
            history.record(
                self._journal_key(cwd),
                unit,
                artifact,
                found.group(1).lower(),
                actor=f"stage:{done.get('stage') or ''}",
                session=str(done.get("session_id") or "") or UNKNOWN,
                source=f"run:{done.get('stage') or ''}",
            )
        except (OSError, BadTransition, Busy):
            return

    def _create_lock(self, cwd: str) -> asyncio.Lock:
        """`0017` R8. One lock per workspace, held across numbering and making the tree."""
        return self._create_locks.setdefault(units.key(cwd), asyncio.Lock())

    async def create_unit(self, cwd: str, slug: str, brief: str = "") -> dict[str, Any]:
        """`0014` R1. Start a work unit, in the product's store rather than the repository.

        The number and the slug grammar are `cos.mjs`'s, through `coscc/units.py`. Nothing
        here is a second opinion about either — `.claude/CLAUDE.md` says that script is the
        one place the loop is defined.

        Since `0017` it also opens the unit's own worktree, detached at the workspace's
        `main`. A worktree that cannot be opened does not undo the unit: the result says
        why under `worktree.error`, and the next step that needs the tree tries again.
        """
        self._workspace_or_refuse(cwd)
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
                    # The host repository's own `.cos/` counts toward the number, so a unit
                    # started here cannot take a number already used there
                    # (`0001_product-describes-a-state-it-is-not-in` R10), and since `0017`
                    # so does every worktree's. Counting is `cos.mjs`'s.
                    **units.create(cwd, slug, brief, self.config.data_dir, reserve_from=reserve),
                }
            except (CannotCreate, BadUnit) as e:
                raise Invalid(str(e)) from e
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
                branch = units.branch_name(cwd, unit, self.config.data_dir)
                await gitops.rev_parse(Path(cwd).expanduser().resolve(), f"refs/heads/{branch}")
            except (CannotCreate, BadUnit, GitError):
                branch = None
            if branch is None:
                return {"path": found["path"], "branch": ""} if found else None
            # The branch exists and the tree is not on it: open it there (`worktrees.ensure`).
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
    ) -> dict[str, Any]:
        """`0016` R2–R4. A person answers one item under an artifact's `## Open questions`.

        The only route in this app that writes into an artifact a stage wrote, and it only
        ever **appends**: the file is opened `"a"`, never `"w"`, so every byte above the
        `## Answers` block is the byte the stage left there (R4). What counts as a question
        and whether it is answered is `cos.mjs`'s decision, read through one board read;
        nothing here parses `## Open questions` a second time (R7).

        Not an approval, and it starts nothing itself; with the autopilot on, the pass it
        nudges may start the next stage (`0043`), or run again a draft this answer finished
        (`0106`). `answered_by` is whatever name the caller
        typed: no route in this app has a login, so it is a claim, not an identity.

        `0028`: `question` may be `"F<n>"`, a finding `cos.mjs` lists in the unit's
        `personFindings`; then `artifact` must be `review.md` and the block is `### F<n>`.
        Unlike a numbered answer, that block is read by `cos.mjs next` and the `ship` gate.
        """
        self._workspace_or_refuse(cwd)
        name = str(answered_by or "").strip() or OWNER
        # `0044` R11. Jera's name marks the road a block came by, not who typed it, so a
        # person may not take it.
        if precedent_mod.is_jera(name):
            raise Invalid(f"{precedent_mod.AGENT} is the agent that answers from precedent; answer under another name")
        done = await self._append_answers(
            cwd, unit, [(artifact, question, answer)], name, "product", f"human:{name}", "answer",
        )
        written = done["written"][0]
        # `0043` R5 b. The answer itself still starts nothing; a pass may, if the switch is on.
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
    ) -> dict[str, Any]:
        """The one place that appends a block under `## Answers` (`0044` Design 3): a person's
        through `answer`, Jera's through `precedent`. `items` is `[(artifact, question, text)]`,
        all checked and written under one hold of `_answer_lock` and one board read.

        A person's refusal raises, as `answer` always has. With `via == "precedent"` every
        item is judged alone and a refused one is `skipped` with its reason, never raised
        (R7): `review.md` and any `F<n>` before a file is opened (R3), and a question the
        board read already shows answered — a person got there while Jera ran.
        Returns `{written: [{artifact, question}], skipped: [{artifact, question, reason}],
        date}`.
        """
        jera = via == precedent_mod.VIA
        name = answered_by
        today = date.today().isoformat()
        written: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        async with self._answer_lock:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            for artifact, question, answer in items:
                try:
                    number, finding = self._append_one(
                        cwd, unit, found, artifact, question, str(answer or "").strip("\n"), name, via,
                        today, jera,
                    )
                except Invalid as e:
                    if not jera:
                        raise
                    skipped.append({"artifact": artifact, "question": question, "reason": str(e)})
                    continue
                written.append({"artifact": artifact, "question": finding or number})

        # `0016` plan, in place of spec R9: the store is not a git repository, so there is
        # no commit to make. The provenance this app already keeps is a row in `outputs`.
        # Never raises: the answer is on disk, and failing the request now would tell the
        # person it was not.
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

        self._journal_answers(cwd, unit, found, written, via)
        return {"written": written, "skipped": skipped, "date": today}

    def _journal_answers(
        self, cwd: str, unit: str, found: dict[str, Any], written: list[dict[str, Any]], via: str,
    ) -> None:
        """`0106` R4. One `answer` record per block written, so the run log can tell which
        answer finished a draft's questions (`completes`) and what the autopilot then did.
        It starts nothing. Never raises, like the `outputs` row above it."""
        journal = self._journal()
        if journal is None or not written:
            return
        key = self._journal_key(cwd)
        try:
            listed, _ = backlog.shortlist_of(journal.records(workspace=key, kind="shortlist"))
            on = bool(self._autopilot_values(key)["autopilot"])
        except (Busy, OSError):
            return
        stages = {s["file"]: s for s in found.get("stages") or []}
        given: dict[str, set[Any]] = {}
        for w in written:
            artifact = w["artifact"]
            given.setdefault(artifact, set()).add(w["question"])
            row = stages.get(artifact) or {}
            try:
                journal.append({
                    "kind": "answer", "workspace": key, "unit": unit, "stage": row.get("stage", ""),
                    "artifact": artifact, "question": w["question"], "via": via,
                    "status": row.get("status", ""),
                    "completes": autopilot.answer_completes(found, artifact, given[artifact]),
                    "autopilot": on, "shortlisted": unit in ((listed or {}).get("units") or []),
                    "held": bool(found.get("hold")),
                })
            except (BadRecord, Busy):
                pass

    def _append_one(
        self, cwd: str, unit: str, found: dict[str, Any], artifact: str, question: Any, text: str,
        name: str, via: str, today: str, jera: bool,
    ) -> tuple[int | str, str]:
        """Check one answer against the board read `found` and append its block. Raises
        `Invalid` before a byte is written; returns `(number, finding)`."""
        if jera:
            # `0044` R3. Decided by the name of the file and the shape of the heading, never
            # by what the session said.
            if artifact == "review.md" or re.fullmatch(r"F\d+", str(question).strip()):
                raise Invalid(f"{precedent_mod.AGENT} never answers in review.md or a finding")
            if any(q.get("artifact") == artifact and q.get("n") == question and q.get("answered")
                   for q in found.get("questions") or []):
                raise Invalid(f"{artifact} question {question} was answered while {precedent_mod.AGENT} ran")
        # `0028`. A finding the last review round confirmed needs a person is answered
        # by its id, `F<n>`, into `review.md` -- and only while `cos.mjs` lists it in
        # `personFindings`, so what may be answered is its decision, not this route's.
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
        if nxt == "finished" or nxt.startswith("closed"):
            raise Invalid(f"{unit} is {nxt}; its questions can no longer be answered")
        # A line that reads as a heading would end this block early or open another,
        # and `cos.mjs` would then read the answer wrongly. Refusing is cheaper and more
        # honest than escaping somebody's words.
        if any(line.lstrip().startswith("#") for line in text.splitlines()):
            raise Invalid("no line of an answer may start with #")

        path = self._unit_dir(cwd, unit) / artifact
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as e:
            raise Invalid(f"could not read {artifact}: {e}") from e
        lines = existing.splitlines()
        heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
        if heading is not None and any(l.startswith("## ") for l in lines[heading + 1:]):
            raise Invalid(
                f"{artifact} has a section after its ## Answers, so a block appended at "
                "the end would not be read as an answer"
            )

        block = ""
        if existing and not existing.endswith("\n"):
            block += "\n"
        if heading is None:
            block += "\n## Answers\n"
        block += f"\n### {finding}\n" if finding else f"\n### Câu {number}\n"
        block += (
            f"Answered by: {name}. Date: {today}. Via: {via}.\n\n"
            f"{text}\n"
        )
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(block)
        except OSError as e:
            raise Invalid(f"could not write {artifact}: {e}") from e
        return number, finding

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
        """`0047` R1–R4, R7. Record whether a finished unit met its intent's outcome.

        Built on `answer()`: the same lock, the same one board read, the same refusal when a
        section follows `## Answers`, and the same `"a"` open, so every byte above the block
        stays the byte the stage left there. The block is `### Outcome` under `intent.md`'s
        `## Answers`; whether it is valid and which one is in force is `cos.mjs`'s reading.

        Not an approval, and it starts nothing: no gate reads the block, and a finished unit
        stays finished. `recorded_by` and `measured_by` are names somebody typed; no route
        has a login, so both are claims. `source` is not checked against anything.
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
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            nxt = str(found.get("next") or "")
            if nxt != "finished":
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
            # The same refusal as `answer()`, and for the same reason: a heading would end
            # this block early or open another, and `cos.mjs` would read it wrongly.
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

        # As in `answer()`: the store has no git, so the provenance is a row in `outputs`,
        # and a failure to write it never fails a block already on disk.
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
        when the file has none -- the one way the app writes into an artifact a stage wrote,
        never rewriting a byte above it. `0045`'s hold wrote this way first; `0054`'s
        `### Rerun` block does too. Under `_answer_lock`, so two appends never interleave.
        `what` names the block in the refusal when a section follows `## Answers`, where an
        appended block would not be read at all."""
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
        """`0045`. A person pauses, drops or resumes a unit (`to`: paused, dropped, active).

        Appends one `### Paused|Dropped|Resumed` block under `intent.md ## Answers` — the
        way `answer` appends, never rewriting a byte above it (R8) — and one `hold` row to
        the run log (R10). Which moves exist is `cos.mjs`'s `holdMoves`, read off the board;
        nothing here decides it (R6). Dropping also closes the unit's open pull request with
        this machine's `gh` login and removes its worktree (R12); a failure there is
        reported, never raised, and undoes nothing.

        Not an approval, and it starts nothing (R16): no step runs, no session opens, even on
        a resume. `by` is whatever name the caller typed. Refused while a step or an
        integration of this unit runs in this process (R13); it holds that same mark itself
        while it writes, so no step can begin halfway through.
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
        directory = self._unit_dir(cwd, unit)
        key = self._journal_key(cwd)
        # No `await` between the check and the take: the same mark `run_step` and
        # `integrate` take, so neither starts while this writes. When the unit is already
        # held, the board is still read, so a move refused for another reason says that one.
        held = self._active.get((key, unit))
        mark = self._take(key, unit, "hold") if held is None else None
        try:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            said = hold_rules.refusal(found, to, reason, by, steps_mod.describe(unit, held) if held else "")
            if said:
                raise Invalid(said)
            assert found is not None
            from_ = (found.get("hold") or {}).get("state") or "active"
            today = date.today().isoformat()
            await self._append_to_answers(
                directory / "intent.md", hold_rules.block(to, by, today, reason), "a hold"
            )

            effects: list[dict[str, str]] = []
            if to == "dropped":
                try:
                    branch = units.branch_name(cwd, unit, self.config.data_dir)
                except (CannotCreate, BadUnit):
                    branch = ""
                root = str(Path(cwd).expanduser().resolve())
                effects.append(await hold_rules.close_pr(root, branch))
                effects.append(await hold_rules.remove_tree(cwd, unit, self.config.data_dir))
            try:
                journal.append(hold_rules.record(
                    workspace=key, unit=unit, from_=from_, to=to, reason=reason, by=by, effects=effects,
                ))
            except (BadRecord, Busy):
                # The block is on disk and `cos.mjs` reads it; failing now would tell the
                # person their decision was not recorded when it was.
                pass
        finally:
            if mark is not None:
                self._release(key, unit, mark)
        return {"unit": unit, "from": from_, "to": to, "reason": reason, "by": by, "date": today, "effects": effects}
