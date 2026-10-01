"""A unit's dialog and its tabs: runs, questions, integration, outcome, run again, hold and review rounds."""

from __future__ import annotations

import reflex as rx

from coscc.units import hold as hold_rules
from coscc.state import present
from coscc.screens import studio as s
from coscc.service.common import CONSEQUENCE
from coscc.state import Cell, Move, Question, Run
from coscc.state.views import Round
from coscc.screens.common import P, _details
from coscc.screens.chrome import _banners
from coscc.screens.settings import _settings_row
from coscc.screens.sessions import _unit_cost
from coscc.screens.board import _update_warning


# --- the unit drawer ---------------------------------------------------------


def _cell_chip(cell: rx.Var[Cell]) -> rx.Component:
    # `cell.label` is `cell.status` except when the artifact is absent and the last run of this
    # stage failed, in which case it names that run instead of the bare word "not started".
    return rx.vstack(
        s.text(cell.stage, size="1"),
        s.badge(cell.label, cell.color),
        spacing="1",
        align="center",
        min_width="0",
    )


def _run_row(run: rx.Var[Run]) -> rx.Component:
    return rx.hstack(
        rx.center(
            rx.icon("play", size=15, color=rx.color(run.color, 11)),
            width="32px",
            height="32px",
            border_radius="50%",
            background=rx.color(run.color, 3),
            flex_shrink="0",
        ),
        rx.vstack(
            rx.hstack(
                rx.text(run.stage, size="2", weight="medium"),
                s.badge(run.mode, "gray"),
                s.badge(run.outcome, run.color),
                rx.spacer(),
                # A run from before the watch pane opens the pane on its note.
                rx.button(
                    rx.icon("eye", size=13),
                    "View",
                    on_click=P.open_watch(run.run, P.unit_id + " · " + run.stage, P.unit_id),
                    class_name="watch-run",
                    variant="soft",
                    size="1",
                ),
                spacing="2",
                wrap="wrap",
                align="center",
                width="100%",
            ),
            s.text(
                rx.cond(
                    run.ended != "", run.started + " → " + run.ended, run.started + " · running"
                ),
                size="1",
            ),
            s.text(run.tokens + " tokens / " + run.usd, size="1"),
            rx.cond(
                run.session_id != "—",
                _details(
                    "run-" + run.key,
                    "Details",
                    s.text(
                        "session " + run.session_id, size="1", font_family="ui-monospace, monospace"
                    ),
                ),
            ),
            rx.cond(
                run.detail != "",
                rx.box(
                    s.text(
                        run.detail,
                        size="1",
                        overflow_wrap="anywhere",
                        white_space="pre-wrap",
                        font_family="ui-monospace, monospace",
                    ),
                    padding="8px 10px",
                    border_radius="6px",
                    margin_top="4px",
                    background=rx.color("amber", 2),
                    border=f"1px solid {rx.color('amber', 5)}",
                    max_height="220px",
                    overflow_y="auto",
                    width="100%",
                ),
            ),
            spacing="1",
            min_width="0",
            width="100%",
        ),
        align="start",
        spacing="3",
        padding="14px 0",
        width="100%",
        border_bottom=f"1px solid {s.LINE}",
        data_testid="run-row",
    )


def _move_row(move: rx.Var[Move]) -> rx.Component:
    """One transition: what moved, the guard's label and whose decision it was.
    The guard's id, the full SHA and the run's id only behind *Details*."""
    return rx.vstack(
        rx.hstack(
            s.badge(move.artifact, "gray"),
            rx.text(move.change, size="2", weight="medium"),
            rx.spacer(),
            s.badge(move.authority, "gray"),
            spacing="2",
            wrap="wrap",
            align="center",
            width="100%",
        ),
        s.text(move.guard_label, size="1"),
        s.text(move.at, size="1"),
        rx.cond(
            move.guard != "unknown",
            _details(
                move.key,
                "Details",
                s.text("guard " + move.guard, size="1", font_family="ui-monospace, monospace"),
                rx.cond(
                    move.head != "",
                    s.text(
                        "SHA " + move.head,
                        size="1",
                        overflow_wrap="anywhere",
                        font_family="ui-monospace, monospace",
                    ),
                ),
                rx.cond(
                    (move.run != "") & (move.run != "unknown"),
                    s.text(
                        "run " + move.run,
                        size="1",
                        overflow_wrap="anywhere",
                        font_family="ui-monospace, monospace",
                    ),
                ),
            ),
        ),
        spacing="1",
        padding="12px 0",
        width="100%",
        min_width="0",
        align="start",
        border_bottom=f"1px solid {s.LINE}",
        data_testid="move-row",
    )


def _question_row(q: rx.Var[Question]) -> rx.Component:
    """One unanswered question and the box its answer goes in."""
    return s.panel(
        rx.hstack(
            s.badge(q.artifact, rx.cond(q.counted, "amber", "gray")),
            # A finding a person is awaited on reads as one, by its `F<n>`.
            s.text(rx.cond(q.number == 0, "finding ", "question ") + q.label, size="1"),
            rx.spacer(),
            width="100%",
            align="center",
        ),
        rx.box(rx.markdown(q.text), width="100%", margin_top="8px"),
        # A dropped unit is read, not answered, nor a finished or closed one: the service's `answerable` says which.
        rx.cond(~P.current_unit.answerable, s.badge("Not answered", "gray")),
        rx.cond(
            ~P.unit_dropped & P.current_unit.answerable,
            rx.fragment(
                rx.text_area(
                    placeholder="Your answer.",
                    value=rx.cond(P.answer_target == q.key, P.answer_text, ""),
                    on_change=lambda v: P.edit_answer(q.key, v),
                    aria_label="Answer to " + q.key,
                    width="100%",
                    rows="3",
                    margin_top="8px",
                ),
                rx.button(
                    rx.icon("send", size=14),
                    "Send this answer",
                    on_click=P.answer_question(q.key),
                    loading=P.answering_key == q.key,
                    size="1",
                    margin_top="8px",
                    id="answer-" + q.key,
                ),
            ),
        ),
        width="100%",
    )


def _questions_tab() -> rx.Component:
    """The page says one sentence about what an answer is, and asks no name."""
    return rx.vstack(
        s.text(
            rx.cond(
                P.current_unit.answerable,
                "The app keeps your answer, and the next step reads it.",
                "This unit is finished; its questions are shown to read.",
            ),
            size="1",
        ),
        rx.foreach(P.open_questions_here, _question_row),
        rx.cond(
            P.open_questions_here.length() == 0,
            s.text("No question in this unit is waiting for an answer."),
        ),
        spacing="3",
        padding="26px",
        width="100%",
        align="start",
        id="questions-body",
    )


def _integration_panel() -> rx.Component:
    """A separate component from the run button, which still offers only the stage `cos.mjs next` names. Hidden for a unit outside the window."""
    u = P.current_unit
    return rx.cond(
        u.integration_state != "",
        s.panel(
            rx.hstack(
                s.eyebrow("INTEGRATION WITH MAIN"),
                rx.spacer(),
                s.badge(
                    u.integration_state,
                    rx.cond(
                        u.integrate_button & (u.integration_state != "current"), "amber", "gray"
                    ),
                ),
                width="100%",
                align="center",
            ),
            # One state; the count only when there is one.
            rx.cond(
                (u.integration_behind != "") & (u.integration_behind != "0"),
                s.text(
                    u.integration_behind + " commits behind main, as of the last fetch",
                    size="1",
                    margin_top="8px",
                    id="integration-behind",
                ),
            ),
            rx.cond(
                u.integration_reason != "",
                s.text(u.integration_reason, size="1", overflow_wrap="anywhere"),
            ),
            rx.foreach(
                u.integration_needs_person,
                lambda n: s.text("[needs-person] " + n, size="1", color=rx.color("red", 11)),
            ),
            rx.cond(
                u.integrate_button,
                rx.vstack(
                    s.text(CONSEQUENCE["integrate"], size="1", id="integration-consequence"),
                    rx.button(
                        rx.icon("git-pull-request-arrow", size=14),
                        "Integrate",
                        on_click=P.integrate,
                        loading=P.integrating,
                        disabled=P.integrating,
                        size="1",
                        id="integrate-button",
                    ),
                    spacing="2",
                    margin_top="8px",
                    align="start",
                ),
            ),
            width="100%",
            id="integration-panel",
        ),
    )


def _outcome_panel() -> rx.Component:
    """The outcome of the open unit against its intent's deadline, and on a finished unit a form to record one. Hidden when the service gives no label."""
    u = P.current_unit
    return rx.cond(
        u.outcome_text != "",
        s.panel(
            rx.hstack(
                s.eyebrow("OUTCOME"),
                rx.spacer(),
                s.badge(u.outcome_text, u.outcome_color),
                width="100%",
                align="center",
            ),
            rx.cond(
                u.outcome_deadline != "",
                s.text(
                    "Deadline read from intent.md: " + u.outcome_deadline,
                    size="1",
                    margin_top="8px",
                ),
            ),
            rx.cond(
                u.outcome_detail != "", s.text(u.outcome_detail, size="1", overflow_wrap="anywhere")
            ),
            rx.cond(
                u.outcome_by != "",
                s.text(
                    "Recorded by "
                    + u.outcome_by
                    + " on "
                    + u.outcome_date
                    + ", measured by "
                    + u.outcome_measured_by,
                    size="1",
                ),
            ),
            rx.cond(
                u.outcome_hint != "", s.text(u.outcome_hint, size="1", color=rx.color("red", 11))
            ),
            rx.cond(
                u.outcome_invalid > 0,
                s.text(
                    u.outcome_invalid.to_string()
                    + " ### Outcome block(s) in intent.md could not be read and were skipped.",
                    size="1",
                    color=rx.color("amber", 11),
                ),
            ),
            rx.cond(
                u.outcome_form,
                rx.vstack(
                    s.text("Adds an outcome block to intent.md.", size="1"),
                    rx.select(
                        list(present.RESULT_LABEL.values()),
                        value=P.outcome_result,
                        on_change=P.set_outcome_result,
                        size="1",
                        id="outcome-result",
                    ),
                    rx.input(
                        placeholder="Source — where the figure came from",
                        value=P.outcome_source,
                        on_change=P.set_outcome_source,
                        width="100%",
                        id="outcome-source",
                    ),
                    rx.input(
                        placeholder="Reason — why it could not be measured",
                        value=P.outcome_reason,
                        on_change=P.set_outcome_reason,
                        width="100%",
                        id="outcome-reason",
                    ),
                    rx.hstack(
                        s.text("Measured by", size="1"),
                        rx.select(
                            list(present.MEASURER_LABEL.values()),
                            value=P.outcome_measured_by,
                            on_change=P.set_outcome_measured_by,
                            size="1",
                            aria_label="Measured by",
                            id="outcome-measured-by",
                        ),
                        spacing="2",
                        align="center",
                    ),
                    rx.text_area(
                        placeholder="Note (optional)",
                        value=P.outcome_note,
                        on_change=P.set_outcome_note,
                        width="100%",
                        id="outcome-note",
                    ),
                    rx.button(
                        rx.icon("flag", size=14),
                        "Record outcome",
                        on_click=P.record_outcome,
                        loading=P.recording_outcome,
                        disabled=P.recording_outcome,
                        size="1",
                        id="outcome-button",
                    ),
                    spacing="2",
                    margin_top="8px",
                    align="start",
                    width="100%",
                ),
            ),
            width="100%",
            id="outcome-panel",
        ),
    )


# The button each move gets, keyed by the value `cos.mjs` puts in `holdMoves`.
_HOLD_BUTTONS = (
    ("paused", "Pause", "pause"),
    ("dropped", "Drop", "circle-x"),
    ("active", "Resume", "play"),
)


def _rerun_panel() -> rx.Component:
    """Run an accepted stage again, apart from the main *Run*. The stages offered and what runs again after each are `cos.mjs rerun`'s. Nothing runs until the confirming button."""
    return rx.vstack(
        rx.heading("Run an earlier stage again", size="4", weight="medium"),
        rx.hstack(
            rx.select(
                P.rerun_stages,
                value=P.rerun_stage,
                on_change=P.set_rerun_stage,
                size="2",
                id="rerun-stage",
                aria_label="Stage to run again",
            ),
            rx.cond(
                ~P.rerun_confirming,
                rx.button(
                    rx.icon("rotate-ccw", size=14),
                    "Rerun…",
                    id="rerun-ask",
                    on_click=P.ask_rerun,
                    variant="soft",
                    size="2",
                    disabled=P.running_here,
                    loading=P.running_here,
                ),
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        rx.text_area(
            placeholder="What should change (optional)",
            value=P.rerun_note,
            on_change=P.set_rerun_note,
            aria_label="What should change",
            id="rerun-note",
            max_length=4000,
            width="100%",
            rows="3",
        ),
        rx.cond(
            P.rerun_confirming,
            rx.vstack(
                # The list as badges, never a run of prose.
                rx.hstack(
                    s.text("Rerunning " + P.rerun_stage + " means these run again:", size="2"),
                    rx.foreach(P.rerun_after, lambda stage: s.badge(stage, "amber")),
                    spacing="2",
                    align="center",
                    flex_wrap="wrap",
                    id="rerun-after",
                ),
                rx.hstack(
                    rx.button(
                        rx.icon("play", size=14),
                        "Rerun " + P.rerun_stage + " — spends quota",
                        id="rerun-confirm",
                        on_click=P.run_rerun,
                        size="2",
                    ),
                    rx.button(
                        "Cancel",
                        id="rerun-cancel",
                        on_click=P.cancel_rerun,
                        variant="soft",
                        color_scheme="gray",
                        size="2",
                    ),
                    spacing="2",
                    flex_wrap="wrap",
                ),
                spacing="2",
                width="100%",
            ),
        ),
        spacing="3",
        width="100%",
        align="start",
        id="rerun-panel",
    )


def _hold_panel() -> rx.Component:
    """The unit's hold as `cos.mjs` read it, and one button per move it allows.

    A button shows only when its value is in `hold_moves`. The *Drop* sentence is on screen before the button, because a drop closes a pull request with this machine's `gh` login. No name is asked.
    """
    u = P.current_unit
    return rx.cond(
        u.hold_moves.length() > 0,
        s.panel(
            rx.hstack(
                s.eyebrow("PAUSE OR DROP"),
                rx.spacer(),
                rx.cond(u.hold_state != "", s.badge(u.hold_state, "amber")),
                width="100%",
                align="center",
            ),
            rx.cond(
                u.hold_state != "",
                s.text(
                    u.hold_reason + " — " + u.hold_by + ", " + u.hold_date,
                    size="1",
                    margin_top="8px",
                    overflow_wrap="anywhere",
                ),
            ),
            rx.input(
                placeholder="Why, in one line",
                value=P.hold_reason,
                on_change=P.set_hold_reason,
                aria_label="Reason for this change",
                id="hold-reason",
                width="100%",
                margin_top="8px",
            ),
            rx.cond(
                u.hold_moves.contains("dropped"),
                s.text(hold_rules.DROP_WARNING, size="1", id="hold-drop-warning"),
            ),
            rx.hstack(
                *(
                    rx.cond(
                        u.hold_moves.contains(value),
                        rx.button(
                            rx.icon(icon, size=14),
                            label,
                            on_click=P.set_hold(value),
                            loading=P.holding,
                            disabled=P.holding,
                            size="1",
                            variant="soft",
                            id=f"hold-{value}",
                        ),
                    )
                    for value, label, icon in _HOLD_BUTTONS
                ),
                spacing="2",
                margin_top="4px",
            ),
            width="100%",
            id="hold-panel",
        ),
    )


def _rounds_panel() -> rx.Component:
    """Shown only when `cos.mjs` says the unit used its review rounds with findings still open. One sentence and one button."""
    return s.panel(
        s.eyebrow("REVIEW ROUNDS"),
        s.text(
            "Review used all its rounds and findings are still open.", size="2", margin_top="8px"
        ),
        rx.button(
            rx.icon("rotate-ccw", size=14),
            "Allow one more review round",
            id="rounds-allow",
            on_click=P.allow_more_rounds,
            loading=P.granting_round,
            disabled=P.granting_round,
            size="2",
            variant="soft",
            margin_top="8px",
        ),
        width="100%",
        id="rounds-panel",
    )


def _round_row(r: rx.Var[Round]) -> rx.Component:
    """One review round: on the PR with its link, or not and a button."""
    return s.panel(
        rx.hstack(
            s.text("Round " + r.number.to_string(), size="2"),
            s.badge(r.verdict, "gray"),
            rx.spacer(),
            rx.cond(
                r.posted, s.badge("on the PR", "grass"), s.badge("comment not on the PR", "amber")
            ),
            width="100%",
            align="center",
        ),
        rx.cond(
            r.posted,
            rx.cond(
                r.url != "",
                rx.link(r.url, href=r.url, is_external=True, size="1", margin_top="8px"),
            ),
            rx.vstack(
                rx.cond(
                    r.reason != "",
                    s.text("Last attempt: " + r.reason, size="1", overflow_wrap="anywhere"),
                ),
                rx.cond(
                    ~P.unit_dropped,
                    rx.button(
                        rx.icon("send", size=14),
                        "Post to PR",
                        on_click=P.post_review_comment(r.number),
                        loading=P.posting_round == r.number,
                        disabled=P.posting_round != 0,
                        size="1",
                        id="post-round-" + r.number.to_string(),
                    ),
                ),
                spacing="2",
                margin_top="8px",
                align="start",
            ),
        ),
        width="100%",
    )


def _comments_tab() -> rx.Component:
    """What a comment is not is `.claude/docs/not-built.md`'s."""
    return rx.vstack(
        s.text("Each review round is posted to the pull request as one comment.", size="1"),
        rx.cond(
            P.current_unit.pr_url != "",
            rx.link(P.current_unit.pr_url, href=P.current_unit.pr_url, is_external=True, size="1"),
            s.text("pr.md names no pull request yet.", size="1"),
        ),
        rx.foreach(P.current_unit.rounds, _round_row),
        rx.cond(P.current_unit.rounds.length() == 0, s.text("review.md holds no round yet.")),
        spacing="3",
        padding="26px",
        width="100%",
        align="start",
        id="comments-body",
    )


def _unit_not_found() -> rx.Component:
    """An address named a unit this workspace's board does not list: say so, rather than draw an empty unit as if it were one."""
    return rx.vstack(
        rx.hstack(
            rx.dialog.title("Not found", size="6", weight="medium"),
            rx.spacer(),
            rx.dialog.close(s.icon_button("x", "Close work detail")),
            width="100%",
            align="center",
        ),
        rx.dialog.description(P.unit_id + " is not a unit of " + P.ws_name + ".", size="2"),
        rx.link("Back to the board", href=P.board_href, size="2"),
        rx.cond(P.board_note != "", s.text(P.board_note, size="1", overflow_wrap="anywhere")),
        spacing="4",
        padding="28px",
        width="100%",
        align="start",
        id="unit-not-found",
    )


def _link_row(label: str, text: rx.Var[str], href: rx.Var[str], testid: str) -> rx.Component:
    return rx.hstack(
        s.text(label, size="1", width="72px", flex_shrink="0"),
        rx.cond(
            href != "",
            rx.link(text, href=href, size="2", data_testid=testid, overflow_wrap="anywhere"),
            rx.text(text, size="2", data_testid=testid),
        ),
        align="center",
        width="100%",
        spacing="2",
    )


def _links() -> rx.Component:
    """The idea a unit was opened from, its repository, and what it waits on."""
    u = P.current_unit
    return rx.cond(
        (u.idea_ref != "") | (u.repo != "") | (u.waits_for != ""),
        rx.vstack(
            rx.cond(u.idea_ref != "", _link_row("Idea", u.idea_ref, u.idea_href, "unit-idea")),
            rx.cond(u.repo != "", _link_row("Repo", u.repo, rx.Var.create(""), "unit-repo")),
            rx.cond(
                u.waits_for != "",
                _link_row("Waits for", u.waits_for, u.waits_for_href, "unit-waits-for"),
            ),
            spacing="1",
            width="100%",
            padding_bottom="6px",
            border_bottom=f"1px solid {s.LINE}",
        ),
    )


def _paused():
    return P.current_unit.hold_state == "paused"


def _answer_first() -> rx.Component:
    """The first action while questions are open: a run reads the answers, so they come before it."""
    n = P.open_questions_here.length()
    return rx.vstack(
        rx.button(
            rx.icon("message-square", size=15),
            "Answer " + n.to_string() + rx.cond(n == 1, " question", " questions"),
            id="answer-first",
            on_click=P.set_detail_tab("questions"),
            width="100%",
        ),
        s.text(
            rx.cond(
                P.next_stage != "",
                "Run " + P.next_stage + " reads your answers, so answer first.",
                "The next step waits for your answers.",
            ),
            size="1",
        ),
        spacing="2",
        width="100%",
    )


def _unit_badges() -> rx.Component:
    """The badges the card let go, where the unit is opened. `problems` and `waits_for` are not here: the overview tab already shows both."""
    u = P.current_unit
    return rx.fragment(
        rx.flex(
            # Only while the service says the unit can still be answered; it opens them.
            rx.cond(
                (u.open_questions > 0) & u.answerable,
                rx.button(
                    u.open_questions.to_string() + " waiting on you",
                    on_click=P.set_detail_tab("questions"),
                    color_scheme="amber",
                    variant="soft",
                    radius="full",
                    size="1",
                    id="unit-waiting",
                ),
            ),
            # `current` has a button too, and still reads gray.
            rx.cond(
                u.integration_state != "",
                s.badge(
                    "main: " + u.integration_state,
                    rx.cond(
                        u.integrate_button & (u.integration_state != "current"), "amber", "gray"
                    ),
                ),
            ),
            # The label is the service's; the page only shows it.
            rx.cond(u.outcome_text != "", s.badge("outcome: " + u.outcome_text, u.outcome_color)),
            # Its place in the shortlist.
            rx.cond(u.shortlist_rank > 0, s.badge("#" + u.shortlist_rank.to_string(), "iris")),
            rx.cond(u.mode == "autonomous", s.badge("Autonomous", "iris")),
            wrap="wrap",
            gap="8px",
            margin_top="14px",
            id="unit-badges",
        ),
        # Its relations.
        rx.cond(
            u.relations_text != "",
            s.text(u.relations_text, size="1", margin_top="8px", overflow_wrap="anywhere"),
        ),
    )


def _detail_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            # Sticky at the top of the content, which is what scrolls here (`overflow_y` below), so a message is in view however far down the tab is.
            rx.box(
                _banners("detail"),
                position="sticky",
                top="0",
                z_index="2",
                background=s.CANVAS,
                padding=rx.cond((P.error != "") | (P.notice != ""), "12px 28px 12px", "0"),
                id="detail-messages",
            ),
            rx.cond(
                P.unit_missing,
                _unit_not_found(),
                rx.fragment(
                    rx.box(
                        rx.hstack(
                            rx.flex(
                                s.text(
                                    P.current_unit.id,
                                    size="1",
                                    font_family="ui-monospace, monospace",
                                    overflow_wrap="anywhere",
                                ),
                                s.badge(P.current_unit.state_label, P.current_unit.state_color),
                                # What a unit waits on, beside its state, where the service finds the two agree. Text, not a pill: nothing here is pressed.
                                rx.cond(
                                    P.current_unit.state_reason != "",
                                    s.text(
                                        P.current_unit.state_reason, size="1", id="state-reason"
                                    ),
                                ),
                                gap="8px",
                                align="center",
                                wrap="wrap",
                                min_width="0",
                            ),
                            rx.spacer(),
                            # Never squeezed off a phone's edge by a long id.
                            rx.dialog.close(
                                s.icon_button("x", "Close work detail", flex_shrink="0")
                            ),
                            width="100%",
                            align="start",
                        ),
                        rx.dialog.title(
                            P.current_unit.title,
                            size="6",
                            weight="medium",
                            margin_top="24px",
                            line_height="1.3",
                        ),
                        rx.dialog.description(
                            "Next: " + P.current_unit.summary,
                            size="2",
                            color=s.MUTED,
                            margin_top="12px",
                        ),
                        _unit_badges(),
                        # Empty: a feature's line about the open unit renders here.
                        rx.box(id="slot-unit"),
                        rx.flex(
                            rx.foreach(P.current_unit.cells, _cell_chip),
                            gap="14px",
                            wrap="wrap",
                            margin_top="20px",
                        ),
                        rx.cond(
                            P.unit_dropped,
                            rx.callout(
                                "Dropped: "
                                + P.current_unit.hold_reason
                                + " — "
                                + P.current_unit.hold_by
                                + ", "
                                + P.current_unit.hold_date
                                + ".",
                                icon="circle-x",
                                color_scheme="gray",
                                variant="surface",
                                size="1",
                                margin_top="20px",
                                id="unit-dropped",
                            ),
                        ),
                        padding="28px",
                    ),
                    rx.tabs.root(
                        rx.tabs.list(
                            rx.tabs.trigger("Overview", value="overview"),
                            rx.tabs.trigger("Artifact", value="artifacts"),
                            rx.tabs.trigger("Questions", value="questions"),
                            rx.tabs.trigger("PR comments", value="comments"),
                            rx.tabs.trigger("Timeline", value="timeline"),
                            width="100%",
                            padding="0 24px",
                            # A phone scrolls the tabs rather than hiding the last ones.
                            overflow_x="auto",
                            overflow_y="hidden",
                            flex_wrap="nowrap",
                            white_space="nowrap",
                        ),
                        rx.tabs.content(
                            rx.vstack(
                                # First in the tab, so a phone sees them.
                                _links(),
                                rx.cond(
                                    P.current_unit.problems != "",
                                    rx.callout(
                                        P.current_unit.problems,
                                        icon="triangle_alert",
                                        color_scheme="red",
                                        variant="surface",
                                        size="1",
                                    ),
                                ),
                                rx.heading("The next step", size="4", weight="medium"),
                                # What `cos.mjs next` said about this unit, verbatim. The stage below is its answer; the page works nothing out itself.
                                rx.hstack(
                                    s.text(
                                        P.run_said,
                                        size="1",
                                        overflow_wrap="anywhere",
                                        id="next-said",
                                    ),
                                    rx.button(
                                        rx.icon("refresh-cw", size=13),
                                        "Ask again",
                                        id="ask-next",
                                        on_click=P.load_next,
                                        variant="soft",
                                        size="1",
                                        disabled=P.running_here,
                                    ),
                                    justify="between",
                                    align="center",
                                    width="100%",
                                    spacing="3",
                                ),
                                # A paused unit's one way on, beside the line that says it is paused.
                                rx.cond(_paused(), _hold_panel()),
                                # `cos.mjs next` named findings a person must act on, and offers no stage. Say which; the button below opens them.
                                rx.cond(
                                    (P.next_stage == "") & (P.run_waiting.length() > 0),
                                    # The findings as a list, not a comma run.
                                    rx.hstack(
                                        s.text("Needs a person", size="2"),
                                        rx.foreach(P.run_waiting, lambda f: s.badge(f, "amber")),
                                        spacing="2",
                                        align="center",
                                        flex_wrap="wrap",
                                        id="next-waiting",
                                    ),
                                ),
                                rx.cond(P.answer_first, _answer_first()),
                                # The findings the last review round left out, as a list beside `next`'s sentence rather than joined into it.
                                rx.cond(
                                    P.run_dropped.length() > 0,
                                    rx.hstack(
                                        s.text("Left out of the last round", size="2"),
                                        rx.foreach(P.run_dropped, lambda f: s.badge(f, "amber")),
                                        spacing="2",
                                        align="center",
                                        flex_wrap="wrap",
                                        id="next-dropped",
                                    ),
                                ),
                                # Nothing that writes, for a dropped unit.
                                rx.cond(
                                    ~P.unit_dropped & (P.next_stage != ""),
                                    rx.vstack(
                                        _settings_row(
                                            "Mode",
                                            "",
                                            rx.segmented_control.root(
                                                rx.segmented_control.item("Manual", value="manual"),
                                                rx.segmented_control.item(
                                                    "Auto", value="autonomous"
                                                ),
                                                value=P.next_cell.mode,
                                                on_change=P.set_mode,
                                                size="1",
                                            ),
                                        ),
                                        # One line of what the step may do; the grant's tools and its full warning only inside *Details*.
                                        rx.hstack(
                                            s.badge(P.next_stage, "iris"),
                                            s.text(
                                                rx.cond(
                                                    P.next_cell.opens_tools,
                                                    "with tools",
                                                    "no tools",
                                                ),
                                                size="1",
                                                id="next-grants",
                                            ),
                                            _details(
                                                "grant",
                                                "What it may use",
                                                s.text(
                                                    P.next_cell.grants,
                                                    size="1",
                                                    overflow_wrap="anywhere",
                                                ),
                                                rx.cond(
                                                    P.next_cell.warning != "",
                                                    s.text(
                                                        P.next_cell.warning,
                                                        size="1",
                                                        id="next-warning",
                                                        overflow_wrap="anywhere",
                                                    ),
                                                ),
                                            ),
                                            align="center",
                                            spacing="3",
                                            flex_wrap="wrap",
                                            width="100%",
                                        ),
                                        rx.button(
                                            rx.icon("play", size=15),
                                            "Run " + P.next_stage + " — spends quota",
                                            id="run-step",
                                            on_click=P.run_step,
                                            variant=rx.cond(P.answer_first, "soft", "solid"),
                                            disabled=P.running_here | ~P.recording,
                                            loading=P.running_here,
                                            width="100%",
                                        ),
                                        s.text(
                                            P.next_cell.consequence, size="1", id="run-consequence"
                                        ),
                                        _update_warning(),
                                        # The one control on this page that writes to the repository's git. It stays separate from Run: cutting a branch decides where the work lands, and Run decides to spend money.
                                        rx.button(
                                            rx.icon("git-branch", size=15),
                                            "Cut this unit's branch",
                                            id="cut-branch",
                                            on_click=P.start_branch,
                                            variant="soft",
                                            width="100%",
                                        ),
                                        s.text(
                                            "Cuts the unit's branch from a fresh main, in its own worktree.",
                                            size="1",
                                        ),
                                        rx.cond(
                                            P.unit_tree != "",
                                            _details(
                                                "tree",
                                                "Worktree",
                                                s.text(
                                                    P.unit_tree,
                                                    id="unit-tree",
                                                    size="1",
                                                    overflow_wrap="anywhere",
                                                ),
                                            ),
                                        ),
                                        rx.cond(
                                            ~P.recording,
                                            s.text(
                                                "No working folder is set, so a run cannot be "
                                                "recorded and will not start.",
                                                size="1",
                                            ),
                                        ),
                                        spacing="4",
                                        width="100%",
                                        align="start",
                                    ),
                                ),
                                # What the board last heard from CI, and when.
                                rx.cond(
                                    P.current_unit.ci_line != "",
                                    s.text(P.current_unit.ci_line, id="unit-ci-line", size="2"),
                                ),
                                # Under the CI line, so the button stays in view on a phone; the hold panel follows it here rather than below, so the page holds one `#hold-panel` at a time.
                                rx.cond(
                                    P.current_unit.more_rounds,
                                    rx.fragment(
                                        _rounds_panel(), rx.cond(~_paused(), _hold_panel())
                                    ),
                                ),
                                # Below the CI line, which belongs to the next step.
                                rx.cond(
                                    ~P.unit_dropped & P.recording & (P.rerun_stages.length() > 0),
                                    _rerun_panel(),
                                ),
                                rx.cond(~P.unit_dropped, _integration_panel()),
                                rx.cond(~P.unit_dropped, _outcome_panel()),
                                _unit_cost(),
                                # Kept for a dropped unit: its one move (`paused`, `cos.mjs` `HOLD_MOVES`) is the board's way back.
                                rx.cond(~P.current_unit.more_rounds & ~_paused(), _hold_panel()),
                                rx.cond(
                                    P.log_here,
                                    s.panel(
                                        s.eyebrow("OUTPUT / LATEST RUN"),
                                        rx.text(
                                            P.run_log,
                                            size="1",
                                            font_family="ui-monospace, monospace",
                                            white_space="pre-wrap",
                                            margin_top="12px",
                                            overflow_wrap="anywhere",
                                        ),
                                        role="log",
                                        aria_label="Run output",
                                        id="run-output",
                                    ),
                                ),
                                spacing="4",
                                padding="26px",
                                width="100%",
                                align="start",
                            ),
                            value="overview",
                        ),
                        rx.tabs.content(
                            rx.vstack(
                                rx.hstack(
                                    rx.icon("file-text", size=17, color=s.MUTED),
                                    rx.text(P.artifact_file, size="2", weight="medium"),
                                    rx.spacer(),
                                    rx.cond(
                                        P.artifact_missing,
                                        s.badge("not written", "gray"),
                                        s.badge("on disk", "grass"),
                                    ),
                                    width="100%",
                                ),
                                rx.box(rx.markdown(P.artifact), width="100%", id="artifact-body"),
                                spacing="5",
                                padding="26px",
                                width="100%",
                            ),
                            value="artifacts",
                        ),
                        rx.tabs.content(_questions_tab(), value="questions"),
                        rx.tabs.content(_comments_tab(), value="comments"),
                        rx.tabs.content(
                            rx.vstack(
                                s.text("Every run of this unit, oldest first.", size="1"),
                                rx.foreach(P.runs, _run_row),
                                rx.cond(
                                    P.runs.length() == 0,
                                    s.text("No step of this unit has been run from here."),
                                ),
                                # Every transition, with the guard that decided it.
                                rx.cond(
                                    P.moves.length() > 0,
                                    rx.vstack(
                                        s.eyebrow("TRANSITIONS"),
                                        s.text(
                                            "Each change of state, newest first, and the rule that allowed it.",
                                            size="1",
                                        ),
                                        rx.foreach(P.moves, _move_row),
                                        spacing="1",
                                        width="100%",
                                        align="start",
                                        margin_top="12px",
                                        id="timeline-moves",
                                    ),
                                ),
                                spacing="3",
                                padding="26px",
                                width="100%",
                                align="start",
                                id="timeline-body",
                            ),
                            value="timeline",
                        ),
                        value=P.detail_tab,
                        on_change=P.set_detail_tab,
                        width="100%",
                    ),
                ),
            ),
            position="fixed",
            right="0",
            top="0",
            left="auto",
            bottom="0",
            transform="none",
            width="min(620px, 100vw)",
            max_width="100vw",
            height="100dvh",
            max_height="100dvh",
            border_radius="0",
            padding="0",
            overflow_y="auto",
            background=s.CANVAS,
            aria_label="Work detail",
        ),
        open=P.unit_id != "",
        on_open_change=P.toggle_detail,
    )
