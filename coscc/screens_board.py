"""The Board screen: its columns and cards, starting a unit, what is running, the update panel
and the autopilot strip.
Split from `coscc/screens.py` (`0095`), which re-exports every name.
"""

from __future__ import annotations

import reflex as rx

from coscc import studio as s
from coscc.service import CONSEQUENCE
from coscc.state import AutopilotStop, Card
from coscc.screens_common import P, _MONO, _details
from coscc.screens_overview import _activity_line, _empty_board


def _unit_card(unit: rx.Var[Card], grouped: bool = False) -> rx.Component:
    """One card. `grouped` for a card in a collapsed group, which names its stage (`0100` R8):
    in a column, the column does."""
    return rx.el.button(
        rx.hstack(
            s.text(unit.id, size="1", font_family="ui-monospace, monospace"),
            rx.spacer(),
            rx.cond(unit.mode == "autonomous",
                    rx.icon("sparkles", size=14, color=rx.color("iris", 10))),
            width="100%",
        ),
        rx.text(unit.title, size="2", weight="medium", line_height="1.6",
                color=s.INK, margin_top="13px"),
        s.text("Next: " + unit.summary, size="1", line_height="1.7", margin_top="7px",
               display=rx.cond(P.density == "compact", "none", "block")),
        rx.hstack(
            # `0100` R9. The one state badge, as the service decided it. `attention_reason`
            # stays in the dialog (spec C3); a paused or dropped hold is this badge's word.
            s.badge(unit.state_label, unit.state_color),
            *([s.badge(unit.at, "gray")] if grouped else []),
            rx.cond(unit.has_problem, s.badge("problem", "red")),
            # `0016` R8. How many, beside the state they put the unit in.
            # `0082` R11: only while the service says the unit can still be answered.
            rx.cond((unit.open_questions > 0) & unit.answerable,
                    s.badge(unit.open_questions.to_string() + " waiting on you", "amber")),
            # `0035` R1. Only for a unit in the window; the button lives on the unit screen.
            # `0052`: `current` has a button too, and still reads gray on the card.
            rx.cond(unit.integration_state != "",
                    s.badge("main: " + unit.integration_state,
                            rx.cond(unit.integrate_button & (unit.integration_state != "current"),
                                    "amber", "gray"))),
            # `0047` R8. The label is the service's; the page only shows it.
            rx.cond(unit.outcome_text != "",
                    s.badge("outcome: " + unit.outcome_text, unit.outcome_color)),
            # `0074`. Its place in the shortlist; changes nothing about the run button.
            rx.cond(unit.shortlist_rank > 0, s.badge("#" + unit.shortlist_rank.to_string(), "iris")),
            rx.spacer(),
            rx.center(rx.text(unit.owner, size="1", weight="medium"),
                      width="27px", height="27px", border_radius="50%",
                      background=rx.color("gray", 4), color=s.MUTED, flex_shrink="0"),
            # `0082` D57, R17: the badges wrap inside the card instead of running past it.
            width="100%", align="center", margin_top="18px", flex_wrap="wrap", row_gap="6px",
        ),
        # `0074` R9. Its relations, on both units' cards.
        rx.cond(unit.relations_text != "",
                s.text(unit.relations_text, size="1", margin_top="7px", overflow_wrap="anywhere")),
        # `0051`. One line per session on this unit, from `Service.running` alone (R8).
        rx.foreach(unit.live, lambda line: _activity_line(line, unit.id)),
        id="unit-" + unit.id, data_testid="work-card", type="button",
        aria_label="Open " + unit.title, on_click=P.open_unit(unit.id),
        padding=rx.cond(P.density == "compact", "12px", "17px"),
        background=s.CANVAS, border=f"1px solid {s.LINE}", border_radius="11px",
        width="100%", cursor="pointer", text_align="left", font_family="inherit",
        box_shadow=f"0 2px 3px {rx.color('gray', 3)}",
        transition="border-color 150ms ease, transform 150ms ease",
        _hover={"border_color": rx.color("iris", 7), "transform": "translateY(-2px)"},
        _focus_visible={"outline": f"2px solid {s.ACCENT}", "outline_offset": "3px"},
    )


def _column(stage: rx.Var[str]) -> rx.Component:
    """`0100` R1. One stage's column. The stages are the board read's, never a list here."""
    count = P.stage_counts[stage]
    return rx.vstack(
        rx.hstack(
            rx.text(stage, size="2", weight="medium"),
            s.text(count.to_string(), size="1"),
            rx.spacer(), width="100%", align="center", padding="2px 4px 8px",
        ),
        # `0053` R8: every column walks the one `cards` list and draws its own shown ones
        # (`spike.md ## U2`), so no card reaches the page twice.
        rx.foreach(P.cards, lambda c: rx.cond(
            (c.at == stage) & P.board_ids.contains(c.id), _unit_card(c), rx.fragment())),
        rx.cond(count == 0,
                rx.center(s.text("Nothing here", size="1", text_align="center"),
                          padding="26px 10px", border=f"1px dashed {s.LINE}",
                          border_radius="10px", width="100%")),
        spacing="3", align="stretch", width="264px", min_width="264px", flex_shrink="0",
        padding="12px", border_radius="13px", background=s.SURFACE,
        # So a proof can ask which column a card is in (`0001_product-describes-a-state-it-
        # is-not-in` R4).
        data_testid="column-" + stage,
    )


def _start_unit() -> rx.Component:
    """`0014` R8. The control that was missing entirely.

    Until `0014` a work unit could only be made by typing `cos.mjs new-path` in a terminal
    and creating the directory by hand, so the Board could list work but never start any —
    every unit it had ever shown was made outside the app.

    The brief is not optional here and `state.create_unit` says why: it becomes the unit's
    `idea.md`, which is the only thing the intent step has to work from. A unit started
    without one spends a paid step on an empty prompt.
    """
    return s.panel(
        rx.vstack(
            rx.hstack(
                rx.icon("plus", size=16, color=rx.color("iris", 10)),
                rx.heading("Start a work unit", size="4", weight="medium"),
                rx.spacer(),
                rx.cond(
                    P.branch != "",
                    rx.hstack(
                        rx.icon("git-branch", size=14),
                        s.text(P.branch, size="1"),
                        spacing="2", align="center",
                    ),
                    rx.fragment(),
                ),
                width="100%", align="center", spacing="2",
            ),
            s.text("Say the problem in your own words; it becomes the unit's idea.md.",
                   size="1", margin_top="2px"),
            rx.input(
                placeholder="short-name-for-the-problem",
                value=P.new_slug, on_change=P.set_new_slug,
                aria_label="Name for the work unit", id="new-unit-slug",
                width="100%", margin_top="8px",
            ),
            rx.text_area(
                placeholder="What is wrong, in your own words.",
                value=P.new_brief, on_change=P.set_new_brief,
                aria_label="What the problem is", id="new-unit-brief",
                width="100%", rows="3",
            ),
            rx.button(
                rx.icon("sprout", size=15), "Start it",
                on_click=P.create_unit, loading=P.starting,
                id="new-unit-start", size="2",
            ),
            width="100%", align="start", spacing="2",
        ),
        width="100%",
    )


def _running_steps() -> rx.Component:
    """`0034` R6, R13. Every step running in this workspace, one Stop each, and every
    integration, with none (`0114` R1).

    The list is the service's, re-read on each board load and after each Stop; nothing
    refreshes it on a timer. No name is asked (`0082` R3): `stopped_by` records `owner`.
    """
    return rx.cond(
        P.running_steps.length() > 0,
        s.panel(
            s.eyebrow("RUNNING STEPS"),
            rx.foreach(P.running_steps, lambda r: rx.hstack(
                s.text(r.unit, size="1", font_family="ui-monospace, monospace"),
                s.badge(r.stage, "iris"),
                s.text(r.started_at, size="1"),
                rx.spacer(),
                # `0073` R10. Watching changes nothing; Stop, beside it, is what acts.
                # `0114` R1: an integration is listed with neither.
                rx.cond(
                    r.kind == "step",
                    rx.hstack(
                        rx.button(
                            rx.icon("eye", size=13), "Watch",
                            on_click=P.open_watch(r.run, r.unit + " · " + r.stage, r.unit),
                            class_name="watch-step", variant="soft", size="1",
                        ),
                        rx.button(
                            rx.icon("square", size=13),
                            rx.cond(r.stopping, "Stopping", "Stop"),
                            id="stop-step", on_click=P.stop_step(r.unit), disabled=r.stopping,
                            color_scheme="red", variant="soft", size="1",
                        ),
                        spacing="3", align="center",
                    ),
                ),
                width="100%", align="center", spacing="3", margin_top="10px", flex_wrap="wrap",
            )),
            id="running-steps", padding="16px",
        ),
    )


def _log_tail(key: str, text) -> rx.Component:
    """`0082` D67: a log's tail holds paths and commands, so it opens only on request."""
    return rx.cond(
        text != "",
        _details(f"log-{key}", "Log",
                 rx.text(text, size="1", font_family=_MONO, white_space="pre-wrap",
                         background=rx.color("gray", 2), padding="8px", width="100%", margin_top="6px")),
    )


def _update_actions(channel: str, line) -> rx.Component:
    """One channel's line and only the buttons `Service.update_status` lists (`0082` R9)."""
    return rx.hstack(
        s.text(line, size="1"),
        rx.spacer(),
        rx.cond(P.upd_actions.contains(f"apply-{channel}"),
                rx.button("Apply", id=f"update-apply-{channel}", on_click=P.apply_update(channel),
                          size="1", variant="soft")),
        rx.cond(P.upd_actions.contains(f"now-{channel}"),
                rx.button("Apply now…", id=f"update-now-{channel}", on_click=P.show_cut_list(channel),
                          size="1", variant="soft", color_scheme="red")),
        *([rx.cond(P.upd_actions.contains("build-local"),
                   rx.button("Build from origin/main", id="update-build-local", on_click=P.build_local,
                             size="1", variant="soft"))] if channel == "local" else []),
        width="100%", align="center", spacing="3", margin_top="10px", flex_wrap="wrap",
    )


def _update_panel() -> rx.Component:
    """`0068`, on Settings since `0082` R9. What runs, one line of state, and only the
    buttons that can be used. Every word and every button comes from `Service.update_status`.
    """
    return s.panel(
        s.section_head("Updates", rx.icon("download", size=18, color=s.MUTED)),
        rx.hstack(
            s.text("Running " + P.upd_version, size="2"),
            s.text(P.upd_commit, size="1", font_family=_MONO),
            rx.cond(P.upd_checked_at != "", s.text("checked " + P.upd_checked_at, size="1")),
            spacing="3", align="center", flex_wrap="wrap", id="update-running",
        ),
        rx.cond(
            ~P.upd_available,
            s.text(P.upd_line, size="1", margin_top="6px", id="update-unavailable"),
            rx.vstack(
                _update_actions("release", P.upd_line),
                _update_actions("local", P.upd_local_line),
                _log_tail("local", P.upd_local_tail),
                rx.cond(
                    P.update_pending,
                    rx.box(
                        rx.foreach(P.upd_waiting, lambda w: s.text("waiting for " + w, size="1")),
                        rx.cond(P.upd_actions.contains("cancel"),
                                rx.button("Stop waiting", id="update-cancel", on_click=P.cancel_update,
                                          size="1", variant="soft", margin_top="6px")),
                        id="update-pending", margin_top="10px",
                    ),
                ),
                rx.cond(
                    P.cut_open,
                    rx.box(
                        s.text("Applying now:", size="2"),
                        rx.cond(P.cut_items.length() == 0, s.text("stops nothing", size="1")),
                        rx.foreach(P.cut_items, lambda i: s.text(i, size="1")),
                        s.text(CONSEQUENCE["apply-now"], size="1", id="update-now-consequence"),
                        rx.hstack(
                            rx.button("Apply now", id="update-confirm-now",
                                      on_click=P.confirm_apply_now, size="1", color_scheme="red"),
                            rx.button("Cancel", on_click=P.close_cut_list, size="1", variant="soft"),
                            spacing="2", margin_top="6px",
                        ),
                        id="update-cut-list", margin_top="10px",
                    ),
                ),
                rx.cond(P.upd_error != "", rx.box(
                    s.text("The last update stopped: " + P.upd_error, size="1"),
                    _log_tail("error", P.upd_error_tail), id="update-error", width="100%",
                )),
                rx.cond(P.upd_last != "", rx.box(
                    s.text("Last update: " + P.upd_last, size="1"),
                    _log_tail("last", P.upd_last_tail), id="update-last", width="100%",
                )),
                width="100%", spacing="1", margin_top="6px", align="start",
            ),
        ),
        _details("update", "Details",
                 s.text("commit " + P.upd_commit_full, size="1", font_family=_MONO, overflow_wrap="anywhere"),
                 margin_top="8px"),
        id="update-panel",
    )


def _update_warning() -> rx.Component:
    """R9: starting work while an update waits is allowed, and pushes the update back."""
    return rx.cond(P.update_pending, s.text(P.update_warning, size="1", color=rx.color("amber", 11)))


# R14. Asks `/api/update` every 5 s outside Reflex's socket, and reloads the page when the
# build it answers for is not the one the page was loaded under. The overlay says
# "Restarting…" while nothing answers, and after 120 s says it could not reconnect.
# A `401` is not a restart: the session ended (expiry, logout elsewhere, `reset-password`),
# so the page goes to `/login` rather than point at a rollback (`0070` review F3).
_RECONNECT_JS = """
(function () {
  if (window.__coscc_update_watch) return;
  window.__coscc_update_watch = true;
  var first = null, failing = null, log = "";
  var box = document.createElement("div");
  box.id = "update-reconnect";
  box.setAttribute("role", "status");
  box.style.cssText = "display:none;position:fixed;top:0;left:0;right:0;z-index:9999;" +
    "padding:10px 16px;background:#7a4a00;color:#fff;font:14px system-ui,sans-serif";
  function show(text) {
    if (!box.parentNode && document.body) document.body.appendChild(box);
    box.textContent = text;
    box.style.display = "block";
  }
  function tick() {
    fetch("/api/update", {cache: "no-store"}).then(function (r) {
      if (r.status === 401) { window.location.replace("/login"); return null; }
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    }).then(function (u) {
      if (u === null) return;
      if (u.log) log = u.log;
      if (first === null) { first = u.build_id; }
      else if (u.build_id !== first) { window.location.reload(); return; }
      failing = null;
      box.style.display = "none";
    }).catch(function () {
      if (failing === null) failing = Date.now();
      if (Date.now() - failing > 120000) {
        show("Could not reconnect. See docs/install.md, Update, to roll back.");
      } else {
        show("Restarting…");
      }
    });
  }
  tick();
  setInterval(tick, 5000);
})();
"""


def _autopilot_stop_row(stop: rx.Var[AutopilotStop]) -> rx.Component:
    return rx.flex(
        s.badge(stop.unit, "iris"),
        s.badge(stop.kind, "amber"),
        s.text(stop.reason, size="1", min_width="0", overflow_wrap="anywhere"),
        gap="8px", align="center", wrap="wrap", width="100%", data_testid="autopilot-stop",
    )


def _autopilot_strip() -> rx.Component:
    """`0043` R9. Shown while the autopilot is on: the cap line, and one row per stop."""
    return rx.cond(
        P.autopilot_on,
        rx.vstack(
            rx.hstack(
                rx.icon("bot", size=16, color=rx.color("iris", 11)),
                rx.text("Autopilot is on", size="2", weight="medium"),
                rx.spacer(),
                s.text(P.autopilot_cap, size="1"),
                width="100%", align="center", wrap="wrap",
            ),
            rx.cond(P.autopilot_refused != "", s.text(P.autopilot_refused, size="1")),
            rx.foreach(P.autopilot_stops, _autopilot_stop_row),
            padding="12px 16px", background=rx.color("iris", 3), border_radius="10px",
            spacing="2", width="100%", role="status", id="autopilot-strip",
        ),
    )


def _board() -> rx.Component:
    return rx.vstack(
        s.heading("Work board", "From an idea to something real. One clear step at a time."),
        rx.cond(P.has_workspace, _start_unit(), rx.fragment()),
        _autopilot_strip(),
        _running_steps(),
        rx.flex(
            rx.input(rx.input.slot(rx.icon("search", size=15)), id="work-search",
                     placeholder="Search work...", value=P.query, on_change=P.search_work,
                     aria_label="Search work",
                     width=rx.breakpoints(initial="100%", sm="240px")),
            rx.segmented_control.root(
                *[rx.segmented_control.item(label, value=label)
                  for label in ("All work", "Autonomous", "Needs you")],
                value=P.focus, on_change=P.filter_work, size="1",
            ),
            rx.spacer(),
            rx.segmented_control.root(
                rx.segmented_control.item(
                    rx.hstack(rx.icon("columns-3", size=14), rx.text("Board"),
                              spacing="2", align="center"),
                    value="Board",
                ),
                rx.segmented_control.item(
                    rx.hstack(rx.icon("list", size=14), rx.text("List"),
                              spacing="2", align="center"),
                    value="List",
                ),
                value=P.board_view, on_change=P.set_board_view, size="1",
            ),
            width="100%", align="center", gap="12px", wrap="wrap",
        ),
        rx.cond(
            P.cards.length() == 0,
            _empty_board(),
            rx.cond(
                P.shown_ids.length() == 0,
                s.panel(rx.heading("No matching work", size="4"),
                        s.text("Try a different search or choose All work.", margin_top="8px")),
                rx.cond(
                    P.board_view == "Board",
                    # `0100` Design 6. One column per stage; wider than the screen, it
                    # scrolls sideways, as GitHub Projects' board does.
                    rx.hstack(
                        rx.foreach(P.stages, _column),
                        spacing="3", width="100%", align="start", overflow_x="auto",
                        padding_bottom="8px", id="board-grid",
                    ),
                    s.panel(
                        rx.foreach(P.cards, lambda u: rx.cond(P.shown_ids.contains(u.id), rx.button(
                            s.text(u.id, size="1", min_width="110px",
                                   font_family="ui-monospace, monospace"),
                            rx.text(u.title, size="2", weight="medium", text_align="left"),
                            rx.spacer(), s.badge(u.at, "gray"), s.badge(u.state_label, u.state_color),
                            on_click=P.open_unit(u.id), variant="ghost", color_scheme="gray",
                            width="100%", height="auto", padding="15px 8px", flex_wrap="wrap",
                            justify_content="flex-start", border_bottom=f"1px solid {s.LINE}",
                        ), rx.fragment())),
                        id="board-list",
                    ),
                ),
            ),
        ),
        _collapsed_groups(),
        spacing="5", width="100%",
    )


def _collapsed_groups() -> rx.Component:
    """`0100` R8 (`intent.md ## Answers, câu 4`). Done, paused and dropped units, each in a
    closed group with its count at the foot of the board; a group of none is not drawn. The
    search and the filter leave in a group what they leave in the List (review F2)."""
    return rx.vstack(
        *(_collapsed_group(state, label) for state, label in
          (("done", "Done"), ("paused", "Paused"), ("dropped", "Dropped"))),
        spacing="3", width="100%",
    )


def _collapsed_group(state: str, label: str) -> rx.Component:
    count = P.group_counts[state]
    return rx.cond(
        count > 0,
        rx.el.details(
            rx.el.summary(s.text(label + " (" + count.to_string() + ")", size="2", as_="span"),
                          cursor="pointer"),
            rx.grid(
                rx.foreach(P.cards, lambda c: rx.cond(
                    (c.state == state) & P.shown_ids.contains(c.id),
                    _unit_card(c, grouped=True), rx.fragment())),
                columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                gap="12px", width="100%", margin_top="12px",
            ),
            width="100%", id=f"{state}-group",
        ),
    )
