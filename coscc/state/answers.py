"""What a person writes into a unit from the page: answers, outcomes, review rounds
posted, integration, holds and review rounds allowed.

Handlers call `app.SERVICE`.
"""

from __future__ import annotations

import logging
import reflex as rx

from coscc.state import app, present
from coscc.kernel import Invalid
from coscc.state.views import DecisionRow, _key_label

log = logging.getLogger(__name__)

# The fields of the decision form, and what each starts as.
DECISION_FORM = {
    "kind": "decision",
    "text": "",
    "source": "",
    "workspace": "All workspaces",
    "until": "",
    "agent": "Leif",
    "covers": "",
}


class AnswersMixin(rx.State, mixin=True):
    # -- answering a question. One text box is live at a time: typing into a question's box
    # makes it the target, and the box of every other question reads empty. No name is typed;
    # the service records `owner`.
    answer_target: str = ""
    answer_text: str = ""
    # The key of the question being sent, `""` while none is: only that row's button shows it.
    answering_key: str = ""
    # The round being posted, 0 while none is.
    posting_round: int = 0
    # True while an integration runs; locks the *Integrate* button.
    integrating: bool = False
    # The outcome form. `outcome_measured_by` starts as `agent`, the case with no script to
    # run. Both hold the English label the select shows; `record_outcome` sends the stored word.
    outcome_result: str = "met"
    outcome_measured_by: str = "Agent"
    outcome_source: str = ""
    outcome_reason: str = ""
    outcome_note: str = ""
    recording_outcome: bool = False
    # The reason typed into the hold panel, and whether a move is in flight.
    hold_reason: str = ""
    holding: bool = False
    # True while one more review round is being allowed; locks its button.
    granting_round: bool = False
    # The Settings panel, read on arriving at Settings, and the form as typed;
    # `decision_workspaces` is what the form's workspace select offers.
    decision_rows: list[DecisionRow] = []
    decision_workspaces: list[str] = ["All workspaces"]
    decision_form: dict[str, str] = dict(DECISION_FORM)

    def _show_decisions(self, data: dict) -> None:
        self.decision_rows = [
            DecisionRow(
                id=str(r["id"]),
                kind=str(r["kind"]),
                text=str(r["text"]),
                source=str(r["source"]),
                workspace=str(r["workspace_name"]),
                agent=str(r.get("agent") or ""),
                covers=str(r.get("covers") or ""),
                from_day=present.day(r.get("from_day")),
                until=present.day(r.get("until_day")) or "until withdrawn",
                withdrawn=present.day(r.get("withdrawn")),
                state=str(r["state"]),
                in_force=r["state"] == "in force",
            )
            for r in data.get("rows") or []
        ]
        self.decision_workspaces = [
            "All workspaces",
            *[str(n) for n in data.get("workspaces") or []],
        ]

    def _load_decisions(self) -> None:
        try:
            self._show_decisions(app.SERVICE.answers.decisions_table())
        except Invalid as e:
            self._fail(e)

    @rx.event
    def edit_decision(self, field: str, value: str):
        if field in DECISION_FORM:
            self.decision_form = {**self.decision_form, field: str(value)}

    @rx.event
    def add_decision(self):
        """Whether the form may be saved is `Answers.add_decision`'s call."""
        form = dict(self.decision_form)
        if form.get("workspace") == DECISION_FORM["workspace"]:
            form["workspace"] = ""
        try:
            done = app.SERVICE.answers.add_decision(form)
        except Invalid as e:
            self.notice = str(e)
            return
        self._show_decisions(done)
        self.decision_form = {**DECISION_FORM, "kind": self.decision_form.get("kind", "decision")}
        self.notice = f"Added {done['added']}."

    @rx.event
    def withdraw_decision(self, decision_id: str):
        try:
            self._show_decisions(app.SERVICE.answers.withdraw_decision(decision_id))
        except Invalid as e:
            self.notice = str(e)
            return
        self.notice = f"Withdrew {decision_id}."

    @rx.event
    async def answer_question(self, key: str):
        """Send one answer; whether it may be written is `Answers.answer`'s.

        Every press ends in an answer written or a reason shown. The `yield` after raising
        `answering_key` sends it to the browser. A second press is queued behind the first,
        finds the box empty and gets a reason; that branch leaves `notice` alone so the
        first press's "Answered ..." survives.
        """
        self.error = ""
        if key != self.answer_target or not self.answer_text.strip():
            self.error = (
                f"Nothing was sent: you pressed Send on {_key_label(key)}, but its box is empty."
            )
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
            done = await app.SERVICE.answers.answer(
                self.cwd, self.unit_id, artifact, number, self.answer_text, ""
            )
        except Invalid as e:
            self._fail(e)
            return
        except Exception as e:
            # An unexpected failure is shown, not lost.
            log.exception("the answer failed")
            # Not "nothing was written": a failure may come after the answer's row, which the
            # artifact cannot show; the Questions tab, read again, can.
            self.error = (
                f"{type(e).__name__}: {e}. It is not known whether the answer was recorded; "
                "reload the page, and the Questions tab shows it if it was."
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
        except Exception as e:
            # The answer is written; say so regardless.
            log.exception("the board could not be read again after an answer")
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
        """Record the open unit's outcome. A label missing from the tables goes as it is, for
        the service to refuse."""
        result = {v: k for k, v in present.RESULT_LABEL.items()}.get(
            self.outcome_result, self.outcome_result
        )
        measurer = {v: k for k, v in present.MEASURER_LABEL.items()}.get(
            self.outcome_measured_by, self.outcome_measured_by
        )
        self.recording_outcome = True
        try:
            done = await app.SERVICE.answers.record_outcome(
                self.cwd,
                self.unit_id,
                result,
                measurer,
                self.outcome_source,
                self.outcome_reason,
                self.outcome_note,
                "",
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
        """Post one review round to the pull request; `Answers.post_review_comment` decides.

        The `yield` after raising `posting_round` sends it to the browser: without it the
        flag is set and cleared inside one delta and the button never locks for the up to
        60s `gh` may take."""
        if self.posting_round != 0:
            return
        self.posting_round = int(number)
        yield
        try:
            done = await app.SERVICE.answers.post_review_comment(self.cwd, self.unit_id, number)
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
        """Integrate the open unit; whether it may, and which road, is `Steps.integrate`'s.
        The `yield` sends `integrating` to the browser."""
        if self.integrating:
            return
        self.integrating = True
        yield
        done: dict = {}
        try:
            async for kind, payload in app.SERVICE.steps.integrate(self.cwd, self.unit_id):
                if kind == "done":
                    done = payload.get("integration") or {}
        except Invalid as e:
            self.notice = f"Not integrated: {e}"
            return
        finally:
            self.integrating = False
        outcome = done.get("outcome", "")
        # The name the service wrote into the record, never one put together here.
        name = str(done.get("agent") or "")
        who = (
            "the app"
            if done.get("mode") == "mechanical"
            else (f"an agent ({name})" if name else "an agent")
        )
        if outcome == "pushed":
            self.notice = (
                f"Integrated by {who}: the pull request is now at {str(done.get('head_after'))[:7]}. "
                "The ship gate is closed until a new review round reviews that head."
            )
        elif outcome == "needs-person":
            self.notice = f"{name or 'The agent'} stopped: a person is needed. The contradictions are listed on the unit."
        else:
            self.notice = (
                f"Integration {outcome or 'ended'}: {done.get('detail') or 'see Activity'}"
            )
        await self._load_board()

    @rx.event
    def set_hold_reason(self, value: str):
        self.hold_reason = value

    @rx.event
    @rx.event
    async def set_hold(self, to: str):
        """Pause, drop or resume the open unit; `Answers.hold` decides. Starts nothing: the
        run button still waits for a person."""
        if self.holding:
            return
        self.holding = True
        yield
        try:
            done = await app.SERVICE.answers.hold(self.cwd, self.unit_id, to, self.hold_reason, "")
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
        yield self.__class__.load_next

    @rx.event
    async def allow_more_rounds(self):
        """Allow the open unit one more review round; `Answers.more_rounds` decides. Starts
        nothing: the run button still waits for a person."""
        if self.granting_round:
            return
        self.granting_round = True
        yield
        try:
            done = await app.SERVICE.answers.more_rounds(self.cwd, self.unit_id, "")
        except Invalid as e:
            self.notice = f"Not changed: {e}"
            return
        finally:
            self.granting_round = False
        self.notice = f"{done['unit']}: one more review round allowed. Nothing was started."
        await self._load_board()
        yield self.__class__.load_next
