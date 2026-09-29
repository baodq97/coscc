"""The Board screen: lanes and cards, starting a unit, what is running, the update panel and the guide panel."""

from __future__ import annotations

import reflex as rx

from coscc.web import studio as s
from coscc.service import CONSEQUENCE
from coscc.state import Card, GuideItem
from coscc.screens.common import P, _MONO, _RUNIC, _details
from coscc.screens.overview import _empty_board


def _unit_card(unit: rx.Var[Card], grouped: bool = False) -> rx.Component:
    """One line: the unit's number, its title cut to fit, and its state in words unless it is *Ready*. `grouped` names the stage for a card in a folded group."""
    return rx.el.button(
        rx.hstack(
            s.text(unit.id.split("_")[0], size="1", font_family=_MONO, flex_shrink="0"),
            rx.cond(unit.mode == "autonomous",
                    rx.icon("sparkles", size=13, color=rx.color("iris", 10), flex_shrink="0")),
            rx.text(unit.title, size="2", weight="medium", color=s.INK, white_space="nowrap",
                    overflow="hidden", text_overflow="ellipsis", min_width="0", flex="1"),
            # The state as the service decided it; a paused or dropped hold is this badge's word.
            rx.cond(unit.state != "ready", s.badge(unit.state_label, unit.state_color)),
            # The code the autopilot's last pass held the unit back with.
            rx.cond(unit.held != "", s.badge(unit.held, "amber")),
            *([s.badge(unit.at, "gray")] if grouped else []),
            # The unit's other badges live in the dialog's header (`_unit_badges`).
            width="100%", align="center", spacing="2",
        ),
        id="unit-" + unit.id, data_testid="work-card", data_state=unit.state, type="button",
        title=unit.id + " · " + unit.title, aria_label="Open " + unit.id + " " + unit.title,
        on_click=P.open_unit(unit.id),
        padding=rx.cond(P.density == "compact", "3px 8px", "5px 10px"),
        background=s.CANVAS, border=f"1px solid {s.LINE}", border_radius="8px",
        border_left=rx.cond(unit.state == "needs-you", f"3px solid {rx.color('amber', 9)}",
                            f"1px solid {s.LINE}"),
        width="100%", min_width="0", cursor="pointer", text_align="left", font_family="inherit",
        # In a lane, a card with a state word takes two places, so its title is still read.
        **({} if grouped else {"grid_column": rx.breakpoints(
            initial="auto", md=rx.cond(unit.state != "ready", "span 2", "auto"))}),
        transition="border-color 150ms ease",
        _hover={"border_color": rx.color("iris", 7)},
        _focus_visible={"outline": f"2px solid {s.ACCENT}", "outline_offset": "2px"},
    )


def _stage_glyph(stage: rx.Var[str]) -> rx.Component:
    """The lane's agent: its glyph as a button, with its name on hover and a small panel on a press."""
    label = P.stage_labels[stage]
    return rx.popover.root(
        rx.popover.trigger(
            rx.el.button(
                P.stage_glyphs[stage], type="button", title=label, aria_label=label,
                font_family=_RUNIC, font_size="15px", line_height="1", color=s.MUTED,
                background="transparent", border="none", padding="2px", cursor="pointer",
                data_testid="stage-glyph",
            ),
        ),
        rx.popover.content(
            rx.text(label, size="2", weight="medium"),
            s.text(P.stage_notes[stage], size="1", white_space="pre-line", margin_top="4px"),
            max_width="260px", size="1",
        ),
    )


def _lane(stage: rx.Var[str]) -> rx.Component:
    """One stage's lane: its name and count, then its cards wrapping in the width left, so the board is never wider than the page."""
    count = P.stage_counts[stage]
    return rx.flex(
        rx.hstack(
            rx.cond(P.stage_glyphs.contains(stage), _stage_glyph(stage)),
            rx.text(stage, size="2", weight="medium"),
            s.text(count.to_string(), size="1"),
            width=rx.breakpoints(initial="100%", md="112px"), flex_shrink="0", align="center",
            padding_top="4px", data_testid="lane-label",
        ),
        rx.grid(
            # Every lane walks the one `cards` list and draws its own shown ones, so no card reaches the page twice.
            rx.foreach(P.cards, lambda c: rx.cond(
                (c.at == stage) & P.board_ids.contains(c.id), _unit_card(c), rx.fragment())),
            grid_template_columns=rx.breakpoints(initial="1fr", md="repeat(auto-fill, minmax(160px, 1fr))"),
            gap="6px", flex="1", min_width="0", width="100%",
        ),
        direction=rx.breakpoints(initial="column", md="row"), gap="10px", width="100%",
        min_width="0", padding="3px 0", border_bottom=f"1px solid {s.LINE}",
        # So a proof can ask which lane a card is in.
        data_testid="column-" + stage,
    )


def _start_unit() -> rx.Component:
    """The form that starts a unit. The brief is required: it becomes the unit's `idea.md`, the only input of the intent step."""
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
    """An idea several units share, one per repository, and this workspace's ideas, each a link to its page."""
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
    """Every step running in this workspace, one Stop each, and every integration, with none.

    The list is re-read on each board load and after each Stop, never on a timer.
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
                # Watching changes nothing; Stop, beside it, is what acts.
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
    """A log's tail holds paths and commands, so it opens only on request."""
    return rx.cond(
        text != "",
        _details(f"log-{key}", "Log",
                 rx.text(text, size="1", font_family=_MONO, white_space="pre-wrap",
                         background=rx.color("gray", 2), padding="8px", width="100%", margin_top="6px")),
    )


def _update_actions(channel: str, line) -> rx.Component:
    """One channel's line and only the buttons `Service.update_status` lists."""
    return rx.hstack(
        s.text(line, size="1"),
        rx.spacer(),
        rx.cond(P.upd_actions.contains(f"apply-{channel}"),
                rx.button("Apply", id=f"update-apply-{channel}", on_click=P.apply_update(channel),
                          size="1", variant="soft")),
        *([rx.cond(P.upd_actions.contains("build-local"),
                   rx.button("Build from origin/main", id="update-build-local", on_click=P.build_local,
                             size="1", variant="soft"))] if channel == "local" else []),
        width="100%", align="center", spacing="3", margin_top="10px", flex_wrap="wrap",
    )


def _update_panel() -> rx.Component:
    """What runs, one line of state, and only the buttons that can be used, all from `Service.update_status`."""
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
    """Starting work while an update waits is allowed, and pushes the update back."""
    return rx.cond(P.update_pending, s.text(P.update_warning, size="1", color=rx.color("amber", 11)))


# Asks `/api/update` every 5 s outside Reflex's socket, and reloads the page when the
# build it answers for is not the one the page was loaded under. The overlay says
# "Restarting…" while nothing answers, and after 120 s says it could not reconnect.
# A `401` is not a restart: the session ended, so the page goes to `/login`.
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


def _guide_row(item: rx.Var[GuideItem], testid: str) -> rx.Component:
    """One line of a guide list: the unit, what it is, and the line below it."""
    return rx.el.li(
        rx.flex(
            rx.cond(item.unit != "", s.badge(item.unit, "iris")),
            rx.cond(item.href != "",
                    rx.link(item.what, href=item.href, size="2", overflow_wrap="anywhere"),
                    s.text(item.what, size="2", overflow_wrap="anywhere")),
            gap="8px", align="center", wrap="wrap", width="100%",
        ),
        rx.cond(item.detail != "", s.text(item.detail, size="1", min_width="0", overflow_wrap="anywhere")),
        data_testid=testid, style={"listStyle": "none", "padding": "4px 0"},
    )


def _guide_list(title: str, items, empty: str, testid: str) -> rx.Component:
    return rx.vstack(
        s.eyebrow(title),
        rx.cond(
            items.length() > 0,
            rx.el.ul(rx.foreach(items, lambda i: _guide_row(i, testid)),
                     style={"margin": "0", "padding": "0", "width": "100%"}),
            s.text(empty, size="1"),
        ),
        spacing="1", width="100%", align="start",
    )


def _guide_panel() -> rx.Component:
    """What runs, what needs you and what Jera decided, under the day's cap line. Off, one sentence and the way to Settings."""
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
            # The lists sit in a closed part so the panel does not push the lanes down.
            rx.el.details(
                rx.el.summary(s.text(
                    P.guide_running.length().to_string() + " running · "
                    + P.guide_needs_you.length().to_string()
                    + rx.cond(P.guide_needs_you.length() == 1, " needs you · ", " need you · ")
                    + P.guide_decided.length().to_string() + " decided for you",
                    size="1", as_="span"), cursor="pointer"),
                rx.grid(
                    _guide_list("RUNNING", P.guide_running, "Nothing is running.", "guide-running"),
                    _guide_list("NEEDS YOU", P.guide_needs_you, "Nothing waits for you.", "guide-needs-you"),
                    _guide_list("DECIDED FOR YOU", P.guide_decided, "Jera decided nothing in the last seven days.", "guide-decided"),
                    columns=rx.breakpoints(initial="1", md="3"), gap="16px", width="100%", margin_top="8px",
                ),
                id="guide-lists", width="100%",
            ),
            padding="12px 16px", background=rx.color("iris", 3), border_radius="10px",
            spacing="3", width="100%", role="status", id="guide-panel",
        ),
        rx.flex(
            s.text("The autopilot is off, so nothing starts on its own.", size="1"),
            rx.link("Settings", href=P.settings_href, size="1"),
            gap="8px", align="center", wrap="wrap", width="100%", id="guide-panel",
        ),
    )


def _release_panel() -> rx.Component:
    """What `main` holds since the last release, and the one button its state has, from the board's `release` block."""
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
    """Moves focus to a form's first field, below the board."""
    return rx.call_script(f"document.getElementById('{target}').focus()")


def _board() -> rx.Component:
    """The toolbar, guide panel, lanes, folded groups, what is running, the release panel, and the two forms last so nothing above the lanes pushes them down."""
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
            # The done units are a number here, and a link to their group.
            rx.cond(P.group_counts["done"] > 0, rx.button(
                P.group_counts["done"].to_string() + " done", id="done-count",
                on_click=rx.call_script(
                    "var d=document.getElementById('done-group');d.open=true;d.scrollIntoView({block:'start'})"),
                size="1", variant="ghost")),
            width="100%", align="center", gap="12px", wrap="wrap",
        ),
        rx.cond(P.has_workspace, _guide_panel(), rx.fragment()),
        rx.box(rx.cond(
            P.cards.length() == 0,
            _empty_board(),
            rx.cond(
                P.shown_ids.length() == 0,
                s.panel(rx.heading("No matching work", size="4"),
                        s.text("Try a different search or choose All work.", margin_top="8px")),
                rx.cond(
                    P.board_view == "Board",
                    # lanes wrap their cards, so the board is never wider than the page.
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
        spacing="4", width="100%",
    )


def _collapsed_groups() -> rx.Component:
    """Done and dropped units, each in a closed group with its count at the foot of the board; a group of none is not drawn. A paused unit stays in its lane. The search and the filter leave in a group what they leave in the List."""
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
