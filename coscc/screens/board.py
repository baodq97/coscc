"""The Board screen: its lanes and cards, starting a unit, what is running, the update panel
and the autopilot strip.
Split from `coscc/screens/__init__.py` (`0095`), which re-exports every name.
"""

from __future__ import annotations

import reflex as rx

from coscc.web import studio as s
from coscc.service import CONSEQUENCE
from coscc.state import AutopilotStop, Card
from coscc.screens.common import P, _MONO, _details
from coscc.screens.overview import _empty_board


def _unit_card(unit: rx.Var[Card], grouped: bool = False) -> rx.Component:
    """`0133` R3, R4. One line: the unit's number, its title cut to fit, and its state in words
    unless it is *Ready*. `grouped` for a card in a folded group, which names its stage
    (`0100` R8): in a lane, the lane does."""
    return rx.el.button(
        rx.hstack(
            s.text(unit.id.split("_")[0], size="1", font_family=_MONO, flex_shrink="0"),
            rx.cond(unit.mode == "autonomous",
                    rx.icon("sparkles", size=13, color=rx.color("iris", 10), flex_shrink="0")),
            rx.text(unit.title, size="2", weight="medium", color=s.INK, white_space="nowrap",
                    overflow="hidden", text_overflow="ellipsis", min_width="0", flex="1"),
            # `0100` R9. The state, as the service decided it; a paused or dropped hold is
            # this badge's word. `attention_reason` stays in the dialog (spec C3).
            rx.cond(unit.state != "ready", s.badge(unit.state_label, unit.state_color)),
            *([s.badge(unit.at, "gray")] if grouped else []),
            # `0133` R7: the badges `0016`, `0035`, `0047`, `0074`, `0082` and `0040` put here
            # live in the dialog's header (`_unit_badges`).
            width="100%", align="center", spacing="2",
        ),
        id="unit-" + unit.id, data_testid="work-card", data_state=unit.state, type="button",
        title=unit.id + " · " + unit.title, aria_label="Open " + unit.id + " " + unit.title,
        on_click=P.open_unit(unit.id),
        # `0133` Design: the density changes only the padding.
        padding=rx.cond(P.density == "compact", "4px 8px", "7px 10px"),
        background=s.CANVAS, border=f"1px solid {s.LINE}", border_radius="8px",
        border_left=rx.cond(unit.state == "needs-you", f"3px solid {rx.color('amber', 9)}",
                            f"1px solid {s.LINE}"),
        width="100%", min_width="0", cursor="pointer", text_align="left", font_family="inherit",
        transition="border-color 150ms ease",
        _hover={"border_color": rx.color("iris", 7)},
        _focus_visible={"outline": f"2px solid {s.ACCENT}", "outline_offset": "2px"},
    )


def _lane(stage: rx.Var[str]) -> rx.Component:
    """`0133` R1, R2. One stage's lane: its name and count, then its cards wrapping in the
    width left, so the board is never wider than the page. The stages are the board read's,
    never a list here (R8)."""
    count = P.stage_counts[stage]
    return rx.flex(
        rx.hstack(
            rx.text(stage, size="2", weight="medium"),
            s.text(count.to_string(), size="1"),
            width=rx.breakpoints(initial="100%", md="112px"), flex_shrink="0", align="center",
            padding_top="4px", data_testid="lane-label",
        ),
        rx.grid(
            # `0053` R8: every lane walks the one `cards` list and draws its own shown ones
            # (`spike.md ## U2`), so no card reaches the page twice.
            rx.foreach(P.cards, lambda c: rx.cond(
                (c.at == stage) & P.board_ids.contains(c.id), _unit_card(c), rx.fragment())),
            grid_template_columns=rx.breakpoints(initial="1fr", md="repeat(auto-fill, minmax(180px, 1fr))"),
            gap="6px", flex="1", min_width="0", width="100%",
        ),
        direction=rx.breakpoints(initial="column", md="row"), gap="10px", width="100%",
        min_width="0", padding="6px 0", border_bottom=f"1px solid {s.LINE}",
        # So a proof can ask which lane a card is in (`0001_product-describes-a-state-it-
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


def _start_idea() -> rx.Component:
    """`0040` R15 (1). An idea several units share, one per repository, and this workspace's
    ideas, each a link to its page. Units are opened from there."""
    return s.panel(
        rx.vstack(
            rx.hstack(
                rx.icon("lightbulb", size=16, color=rx.color("iris", 10)),
                rx.heading("Start an idea", size="4", weight="medium"),
                width="100%", align="center", spacing="2",
            ),
            s.text("One feature across repositories; open a unit per repository from its page.",
                   size="1", margin_top="2px"),
            rx.input(
                placeholder="the-feature-in-a-few-words",
                value=P.new_idea_slug, on_change=P.set_new_idea_slug,
                aria_label="Slug for the idea", id="new-idea-slug", width="100%", margin_top="8px",
            ),
            rx.text_area(
                placeholder="The feature, in your own words.",
                value=P.new_idea_brief, on_change=P.set_new_idea_brief,
                aria_label="What the feature is", id="new-idea-brief", width="100%", rows="3",
            ),
            rx.button(rx.icon("lightbulb", size=15), "Start the idea",
                      on_click=P.create_idea, id="new-idea-start", size="2"),
            rx.cond(
                P.ideas.length() > 0,
                rx.vstack(
                    s.text("Ideas", size="1", weight="medium", margin_top="8px"),
                    rx.foreach(P.ideas, lambda i: rx.hstack(
                        rx.link(i.id, href=i.href, size="2", data_testid="idea-link",
                                font_family="ui-monospace, monospace", overflow_wrap="anywhere"),
                        rx.spacer(),
                        s.badge(i.units.to_string() + " units", "gray"),
                        width="100%", align="center",
                    )),
                    width="100%", spacing="2",
                ),
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
    """`0043` R9. Shown while the autopilot is on: the cap line, and one row per stop behind
    their count."""
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
            # `0133` R11: the stops in a closed part, so the strip does not push the lanes down.
            rx.cond(
                P.autopilot_stops.length() > 0,
                rx.el.details(
                    rx.el.summary(s.text(P.autopilot_stops.length().to_string() + " stops",
                                         size="1", as_="span"), cursor="pointer"),
                    rx.vstack(rx.foreach(P.autopilot_stops, _autopilot_stop_row),
                              spacing="2", margin_top="8px", width="100%"),
                    id="autopilot-stops", width="100%",
                ),
            ),
            padding="12px 16px", background=rx.color("iris", 3), border_radius="10px",
            spacing="2", width="100%", role="status", id="autopilot-strip",
        ),
    )


def _release_panel() -> rx.Component:
    """`0046`. What `main` holds since the last release, and the one button its state has.
    Every word and whether the button may be pressed come from the board's `release` block."""
    row = dict(gap="8px", align="center", wrap="wrap", width="100%")
    return rx.cond(
        P.rel_state != "",
        s.panel(
            rx.hstack(
                s.eyebrow("RELEASE"),
                rx.cond(P.rel_last_tag != "", s.text("last " + P.rel_last_tag, size="1")),
                rx.spacer(),
                s.badge(P.rel_state, rx.cond(P.rel_button != "", "amber", "gray")),
                width="100%", align="center",
            ),
            rx.cond(P.rel_reason != "", s.text(P.rel_reason, size="1", margin_top="6px", id="release-reason")),
            rx.cond(
                P.rel_units.length() > 0,
                rx.vstack(
                    s.text(P.rel_units.length().to_string() + " units since " + P.rel_last_tag, size="2"),
                    rx.foreach(P.rel_units, lambda u: rx.flex(
                        s.text(u.sha, size="1", font_family=_MONO), s.badge(u.type, "gray"),
                        s.text(u.name, size="1"), s.text(u.pr, size="1"), **row,
                    )),
                    spacing="1", margin_top="8px", width="100%", id="release-units",
                ),
            ),
            rx.cond(
                P.rel_unmatched.length() > 0,
                rx.vstack(
                    s.text(P.rel_unmatched.length().to_string() + " commits with no unit", size="2"),
                    rx.foreach(P.rel_unmatched, lambda c: rx.flex(
                        s.text(c.sha, size="1", font_family=_MONO),
                        s.text(c.subject, size="1", overflow_wrap="anywhere"), **row,
                    )),
                    spacing="1", margin_top="8px", width="100%", id="release-unmatched",
                ),
            ),
            rx.cond(
                P.rel_workflow != "",
                rx.flex(
                    s.text("Release workflow: " + P.rel_workflow, size="1"),
                    rx.link("run", href=P.rel_workflow_url, is_external=True, size="1"),
                    rx.cond(P.rel_release_url != "",
                            rx.link("release", href=P.rel_release_url, is_external=True, size="1")),
                    margin_top="8px", **row,
                ),
            ),
            rx.cond(
                P.rel_button != "",
                rx.vstack(
                    s.text(CONSEQUENCE["release"], size="1", id="release-consequence"),
                    rx.flex(
                        rx.input(value=P.rel_version, on_change=P.set_rel_version, aria_label="Version",
                                 size="1", width="120px", id="release-version",
                                 disabled=P.rel_phase == "publish"),
                        rx.button(
                            rx.icon("tag", size=14), P.rel_button,
                            on_click=P.press_release, loading=P.releasing,
                            disabled=P.releasing | ~P.rel_enabled, size="1", id="release-button",
                        ),
                        gap="8px", align="center",
                    ),
                    rx.cond(~P.rel_enabled & (P.rel_disabled_reason != ""),
                            s.text(P.rel_disabled_reason, size="1", id="release-disabled")),
                    spacing="2", margin_top="10px", align="start",
                ),
            ),
            width="100%", id="release-panel",
        ),
    )


def _focus(target: str):
    """`0133` R10. Moves focus to a form's first field, below the board."""
    return rx.call_script(f"document.getElementById('{target}').focus()")


def _board() -> rx.Component:
    """`0133` Design: the toolbar, the autopilot strip, the lanes, the folded groups, what is
    running, the release panel (`0046`), and the two forms last, so nothing above the lanes
    pushes them down (R10)."""
    return rx.vstack(
        s.heading("Work board", "From an idea to something real. One clear step at a time."),
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
            rx.cond(P.has_workspace, rx.button(rx.icon("plus", size=14), "New unit", id="board-new-unit",
                                               on_click=_focus("new-unit-slug"), size="1", variant="soft")),
            rx.cond(P.has_workspace, rx.button(rx.icon("lightbulb", size=14), "New idea", id="board-new-idea",
                                               on_click=_focus("new-idea-slug"), size="1", variant="soft")),
            # `0133` R5. The done units are a number here, and a link to their group.
            rx.cond(P.group_counts["done"] > 0, rx.button(
                P.group_counts["done"].to_string() + " done", id="done-count",
                on_click=rx.call_script(
                    "var d=document.getElementById('done-group');d.open=true;d.scrollIntoView({block:'start'})"),
                size="1", variant="ghost")),
            width="100%", align="center", gap="12px", wrap="wrap",
        ),
        _autopilot_strip(),
        rx.box(rx.cond(
            P.cards.length() == 0,
            _empty_board(),
            rx.cond(
                P.shown_ids.length() == 0,
                s.panel(rx.heading("No matching work", size="4"),
                        s.text("Try a different search or choose All work.", margin_top="8px")),
                rx.cond(
                    P.board_view == "Board",
                    # `0133` R1: lanes wrap their cards, so the board is never wider than
                    # the page.
                    rx.vstack(
                        rx.foreach(P.stages, _lane),
                        spacing="0", width="100%", min_width="0", id="board-grid",
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
        ), width="100%", min_width="0", id="board-lanes-area"),
        _collapsed_groups(),
        _running_steps(),
        _release_panel(),
        rx.cond(P.has_workspace, _start_unit(), rx.fragment()),
        rx.cond(P.has_workspace, _start_idea(), rx.fragment()),
        spacing="5", width="100%",
    )


def _collapsed_groups() -> rx.Component:
    """`0100` R8 (`intent.md ## Answers, câu 4`). Done and dropped units, each in a closed
    group with its count at the foot of the board; a group of none is not drawn. A paused
    unit stays in its lane (`0133` spec C2). The search and the filter leave in a group what
    they leave in the List (review F2)."""
    return rx.vstack(
        *(_collapsed_group(state, label) for state, label in
          (("done", "Done"), ("dropped", "Dropped"))),
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
