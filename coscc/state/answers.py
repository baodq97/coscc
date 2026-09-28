"""What a person writes into a unit from the page: answers, Jera, outcomes, review rounds
posted, integration, holds and review rounds allowed.

Split from `coscc/state/__init__.py` (`0095`). `StudioState` inherits it, so its vars and handlers
keep their names; a handler that needs `SERVICE` or `StudioState` imports them in its body,
because this module cannot import `coscc.state` at the top (`spike.md ## U1`).
"""

from __future__ import annotations

import reflex as rx

from coscc.web import present
from coscc.service import Invalid
from coscc.state.views import DecisionRow, NameRow, _key_label

# `0137` R5. The fields of the decision form, and what each starts as. No name is asked (S7).
DECISION_FORM = {"kind": "decision", "text": "", "source": "", "workspace": "All workspaces", "until": "",
                 "agent": "Leif", "covers": ""}


class AnswersMixin(rx.State, mixin=True):

    # -- answering a question (`0016`). One text box is live at a time: typing into a
    # question's box makes it the target, and the box of every other question reads empty.
    # `0082` R3: no name is typed; the service records `owner`.
    answer_target: str = ""
    answer_text: str = ""
    # `0071` R6. The key of the question being sent, `""` while none is: only that row's
    # button shows it is sending.
    answering_key: str = ""
    # `0021`. The round being posted, 0 while none is.
    posting_round: int = 0
    # `0035`. True while an integration runs; locks the *Integrate* button.
    integrating: bool = False
    # `0044`. True while Jera runs on the open unit; locks *Ask Jera*.
    asking_jera: bool = False
    # `0044` R8a. The Settings text Jera reads as precedent, as typed and as last saved.
    decision_preferences: str = ""
    # `0101` R8. What Jera's prompt says about deciding, as typed and as last saved; empty is
    # the default rules.
    decision_rules: str = ""
    # `0047`. The outcome form. Nobody types who records it (`0082` R3); `outcome_measured_by`
    # starts as `agent`, the case with no script to run.
    # Both hold the English label the select shows (`0089` R1, R4); `record_outcome` sends
    # the stored word.
    outcome_result: str = "met"
    outcome_measured_by: str = "Agent"
    outcome_source: str = ""
    outcome_reason: str = ""
    outcome_note: str = ""
    recording_outcome: bool = False
    # `0045`. The reason typed into the hold panel, and whether a move is in flight.
    hold_reason: str = ""
    holding: bool = False
    # `0081`. True while one more review round is being allowed; locks its button.
    granting_round: bool = False
    # `0137` R5, R6. The two Settings panels, read on arriving at Settings, and the form as
    # typed; `decision_workspaces` is what the form's workspace select offers.
    decision_rows: list[DecisionRow] = []
    decision_workspaces: list[str] = ["All workspaces"]
    decision_form: dict[str, str] = dict(DECISION_FORM)
    name_rows: list[NameRow] = []
    name_problems: list[str] = []

    def _show_decisions(self, data: dict) -> None:
        self.decision_rows = [
            DecisionRow(
                id=str(r["id"]), kind=str(r["kind"]), text=str(r["text"]), source=str(r["source"]),
                workspace=str(r["workspace_name"]), agent=str(r.get("agent") or ""),
                covers=str(r.get("covers") or ""), from_day=present.day(r.get("from_day")),
                until=present.day(r.get("until_day")) or "until withdrawn",
                withdrawn=present.day(r.get("withdrawn")), state=str(r["state"]),
                in_force=r["state"] == "in force",
            )
            for r in data.get("rows") or []
        ]
        self.decision_workspaces = ["All workspaces", *[str(n) for n in data.get("workspaces") or []]]

    async def _load_decisions(self) -> None:
        """`0137`. Both panels: one read of `cos.db` and one board read per workspace."""
        from coscc.state import SERVICE
        try:
            self._show_decisions(SERVICE.decisions_table())
            names = await SERVICE.answer_names()
        except Invalid as e:
            self._fail(e)
            return
        self.name_rows = [NameRow(name=str(r["name"]), count=int(r["count"]), mine=bool(r["mine"]))
                          for r in names.get("rows") or []]
        self.name_problems = [str(p) for p in names.get("problems") or []]

    @rx.event
    def edit_decision(self, field: str, value: str):
        if field in DECISION_FORM:
            self.decision_form = {**self.decision_form, field: str(value)}

    @rx.event
    def add_decision(self):
        """`0137` R5. Whether the form may be saved is `Service.add_decision`'s call; a refusal
        is its one sentence, shown as it is."""
        from coscc.state import SERVICE
        form = dict(self.decision_form)
        if form.get("workspace") == DECISION_FORM["workspace"]:
            form["workspace"] = ""
        try:
            done = SERVICE.add_decision(form)
        except Invalid as e:
            self.notice = str(e)
            return
        self._show_decisions(done)
        self.decision_form = {**DECISION_FORM, "kind": self.decision_form.get("kind", "decision")}
        self.notice = f"Added {done['added']}."

    @rx.event
    def withdraw_decision(self, decision_id: str):
        from coscc.state import SERVICE
        try:
            self._show_decisions(SERVICE.withdraw_decision(decision_id))
        except Invalid as e:
            self.notice = str(e)
            return
        self.notice = f"Withdrew {decision_id}."

    @rx.event
    def set_name_mine(self, name: str, on: bool):
        """`0137` R6. Whether the name may be marked is `Service.set_name_mine`'s call."""
        from coscc.state import SERVICE
        try:
            done = SERVICE.set_name_mine(name, bool(on))
        except Invalid as e:
            self.notice = str(e)
            return
        self.name_rows = [NameRow(name=r.name, count=r.count, mine=done["mine"] if r.name == name else r.mine)
                          for r in self.name_rows]
        self.notice = f"{name} {'counts' if done['mine'] else 'no longer counts'} as you."

    @rx.event
    async def answer_question(self, key: str):
        """`0016` R2. Send one answer. Every rule about whether it may be written is
        `Service.answer`'s; a refusal arrives here as its words and is shown as they are.

        `0071` R1: every press ends in a block written or a reason shown, never in nothing.
        The `yield` after raising `answering_key` is what sends it to the browser, as in
        `post_review_comment`. There is no guard against a second press: Reflex queues it
        behind the first, by then the box is empty, and the first branch gives it a reason.
        That branch leaves `notice` alone: the press it follows may be the one that wrote the
        block, and its "Answered …" must survive the second press (review round 1, F1).
        """
        from coscc.state import SERVICE
        self.error = ""
        if key != self.answer_target or not self.answer_text.strip():
            self.error = f"Nothing was sent: you pressed Send on {_key_label(key)}, but its box is empty."
            if self.answer_target and self.answer_text.strip():
                self.error += (
                    f" Your text is in the box of {_key_label(self.answer_target)}, and it is "
                    "still there."
                )
            return
        artifact, _, number = key.rpartition("#")
        self.notice = ""
        self.answering_key = key
        yield
        try:
            done = await SERVICE.answer(
                self.cwd, self.unit_id, artifact, number, self.answer_text, ""
            )
        except Invalid as e:
            self._fail(e)
            return
        except Exception as e:  # noqa: BLE001 - R5: an unexpected failure is shown, not lost
            # Not "nothing was written": `Service.answer` writes the block before it touches
            # the history, so a failure after that leaves it on disk (`spec.md` R5).
            self.error = (
                f"{type(e).__name__}: {e}. It is not known whether the answer was written; "
                f"open {artifact} to check."
            )
            return
        finally:
            self.answering_key = ""
        self.answer_target, self.answer_text = "", ""
        kind = "finding" if str(done["question"]).startswith("F") else "question"
        self.notice = (
            f"Answered {kind} {done['question']} of {done['artifact']} as "
            f"{done['answered_by']}. Nothing was started; the next step reads it when it runs."
        )
        try:
            await self._load_board()
            self._load_artifact()
        except Exception as e:  # noqa: BLE001 - R5: the answer is written; say so regardless
            self.notice += f" The board could not be read again: {type(e).__name__}: {e}"

    @rx.event
    def use_proposal(self, key: str, text: str):
        """`0101` R9. Put Jera's proposal in that question's box. Nothing is sent: the person
        still presses *Send this answer*, and the answer is theirs (S7)."""
        self.answer_target, self.answer_text = key, text

    @rx.event
    async def ask_jera(self):
        """`0044`. Ask Jera to answer the open unit's questions from precedent. Whether it may,
        and what is written, is `Service.precedent`'s; this locks the button and says what
        happened. The `yield` sends `asking_jera` to the browser, as `integrate` does."""
        from coscc.state import SERVICE
        if self.asking_jera:
            return
        self.error, self.notice = "", ""
        self.asking_jera = True
        yield
        try:
            done = await SERVICE.precedent(self.cwd, self.unit_id)
        except Invalid as e:
            self._fail(e)
            return
        finally:
            self.asking_jera = False
        if done.get("outcome") == "failed":
            self.error = f"Jera's reply could not be read, so nothing was written: {done.get('detail')}"
        else:
            self.notice = (
                f"Jera answered {len(done['written'])}; {len(done['needs_person'])} need a person"
                + (f"; {len(done['skipped'])} were answered meanwhile." if done["skipped"] else ".")
            )
        try:
            await self._load_board()
            self._load_artifact()
        except Exception as e:  # noqa: BLE001 - what Jera wrote is written; say so regardless
            self.notice += f" The board could not be read again: {type(e).__name__}: {e}"

    @rx.event
    def set_outcome_result(self, value: str):
        self.outcome_result = value

    @rx.event
    def set_outcome_measured_by(self, value: str):
        self.outcome_measured_by = value

    @rx.event
    def set_outcome_source(self, value: str):
        self.outcome_source = value

    @rx.event
    def set_outcome_reason(self, value: str):
        self.outcome_reason = value

    @rx.event
    def set_outcome_note(self, value: str):
        self.outcome_note = value

    @rx.event
    async def record_outcome(self):
        """`0047`. Record the open unit's outcome. Every rule about whether it may be written
        is `Service.record_outcome`'s; a refusal arrives here as its words, shown as they are.
        A label missing from the tables goes as it is, for the service to refuse."""
        from coscc.state import SERVICE
        result = {v: k for k, v in present.RESULT_LABEL.items()}.get(self.outcome_result, self.outcome_result)
        measurer = {v: k for k, v in present.MEASURER_LABEL.items()}.get(
            self.outcome_measured_by, self.outcome_measured_by
        )
        self.recording_outcome = True
        try:
            done = await SERVICE.record_outcome(
                self.cwd, self.unit_id, result, measurer,
                self.outcome_source, self.outcome_reason, self.outcome_note, "",
            )
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.recording_outcome = False
        self.outcome_source, self.outcome_reason, self.outcome_note = "", "", ""
        self.notice = (
            f"Recorded {present.RESULT_LABEL.get(done['result'], done['result'])} for {done['unit']}, "
            f"measured by {present.MEASURER_LABEL.get(done['measured_by'], done['measured_by'])}."
        )
        await self._load_board()
        self._load_artifact()

    @rx.event
    async def post_review_comment(self, number: int):
        """`0021` R8. Post one review round to the pull request. Whether it may, and whether
        it is already there, is `Service.post_review_comment`'s decision.

        The `yield` after raising `posting_round` is what sends it to the browser: without
        it the flag is set and cleared inside one delta, and the button never locks for the
        up to 60s `gh` may take (`0021` review, F1)."""
        from coscc.state import SERVICE
        if self.posting_round != 0:
            return
        self.posting_round = int(number)
        yield
        try:
            done = await SERVICE.post_review_comment(self.cwd, self.unit_id, number)
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.posting_round = 0
        if done["state"] == "failed":
            self.notice = f"Round {done['round']} is not on the PR: {done['reason']}"
        elif done["state"] == "already":
            self.notice = f"Round {done['round']} was already on the PR. Nothing was posted."
        else:
            self.notice = (
                f"Posted round {done['round']} to the PR as a comment. It is not an approval "
                "and no gate reads it."
            )
        await self._load_board()

    @rx.event
    async def integrate(self):
        """`0035`. Integrate the open unit. Whether it may, and which road, is
        `Service.integrate`'s decision; this only locks the button and says what happened.
        The `yield` sends `integrating` to the browser, as `post_review_comment` does."""
        from coscc.state import SERVICE
        if self.integrating:
            return
        self.integrating = True
        yield
        done: dict = {}
        try:
            async for kind, payload in SERVICE.integrate(self.cwd, self.unit_id):
                if kind == "done":
                    done = payload.get("integration") or {}
        except Invalid as e:
            self.notice = f"Not integrated: {e}"
            return
        finally:
            self.integrating = False
        outcome = done.get("outcome", "")
        # `0036` R7: the name the service wrote into the record, never one put together here.
        name = str(done.get("agent") or "")
        who = "the app" if done.get("mode") == "mechanical" else (f"an agent ({name})" if name else "an agent")
        if outcome == "pushed":
            self.notice = (
                f"Integrated by {who}: the pull request is now at {str(done.get('head_after'))[:7]}. "
                "The ship gate is closed until a new review round reviews that head."
            )
        elif outcome == "needs-person":
            self.notice = f"{name or 'The agent'} stopped: a person is needed. The contradictions are listed on the unit."
        else:
            self.notice = f"Integration {outcome or 'ended'}: {done.get('detail') or 'see Activity'}"
        await self._load_board()

    @rx.event
    def set_hold_reason(self, value: str):
        self.hold_reason = value

    @rx.event

    @rx.event
    async def set_hold(self, to: str):
        """`0045`. Pause, drop or resume the open unit. Whether the move exists and whether
        the words will do is `Service.hold`'s decision; a refusal is shown as it is. Reads
        the board again afterwards and starts nothing (R16) — not `run_step`, not `load_next`'s
        stage: the button still waits for a person to press it."""
        from coscc.state import SERVICE, StudioState
        if self.holding:
            return
        self.holding = True
        yield
        try:
            done = await SERVICE.hold(self.cwd, self.unit_id, to, self.hold_reason, "")
        except Invalid as e:
            self.notice = f"Not changed: {e}"
            return
        finally:
            self.holding = False
        self.hold_reason = ""
        effects = "; ".join(
            f"{e['effect']}: {e['result']}" + (f" ({e['detail']})" if e["result"] != "done" else "")
            for e in done["effects"]
        )
        self.notice = (
            f"{done['unit']}: {done['from']} → {done['to']}, by {done['by']}."
            + (f" {effects}." if effects else "")
            + " Nothing was started."
        )
        await self._load_board()
        self._load_activity()
        yield StudioState.load_next

    @rx.event
    async def allow_more_rounds(self):
        """`0081` R8. Allow the open unit one more review round. Whether it is out of rounds
        is `Service.more_rounds`'s decision, read off `cos.mjs`; a refusal is shown as it is.
        Reads the board and `next` again afterwards and starts nothing: the run button still
        waits for a person to press it."""
        from coscc.state import SERVICE, StudioState
        if self.granting_round:
            return
        self.granting_round = True
        yield
        try:
            done = await SERVICE.more_rounds(self.cwd, self.unit_id, "")
        except Invalid as e:
            self.notice = f"Not changed: {e}"
            return
        finally:
            self.granting_round = False
        self.notice = f"{done['unit']}: one more review round allowed. Nothing was started."
        await self._load_board()
        yield StudioState.load_next
