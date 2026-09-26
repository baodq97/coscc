"""The dialogs outside a unit: watching a step, a workspace's form, removing one, the command
palette and the mobile menu.
Split from `coscc/screens.py` (`0095`), which re-exports every name.
"""

from __future__ import annotations

import reflex as rx

from coscc import studio as s
from coscc.state import NAVIGATION, WatchEvent
from coscc.screens_common import P, _MONO
from coscc.screens_chrome import _nav, _workspace_select


# --- watching a step (`0073`) -------------------------------------------------

# Two observers, and nothing else: the first row coming into view presses *older*, and a
# list that was at its bottom before a change is put back there. Every rule about what the
# list holds is `StudioState`'s; this only scrolls.
#
# Away from the bottom, the row being read stays where it was whatever changed the list:
# the first row in view is remembered by its `data-seq` and offset on every scroll, and
# brought back to that offset after every change. Rows are drawn by position, so a page
# prepended, a full list dropping its newest rows (`review.md` F5) and a live batch
# dropping its oldest while following (F6 a) all rewrite rows in place, and neither the
# scroll height nor the row nodes say where the row went; the seq does. The anchor is
# never spent on the first change, so a live batch landing between *older* and its page
# does not leave the page to arrive with none (F6 b). *Older* pressed at the bottom
# anchors too; *Jump to latest* drops the anchor and goes to the bottom. `data-seq` is watched as
# an attribute because a list that stays at `WATCH_WINDOW` rows changes no child at all.
# The browser's own scroll anchoring is off on `#watch-list`, so this is the one thing
# that moves it.
_WATCH_JS = """
(function () {
  if (window.__coscc_watch) return;
  window.__coscc_watch = true;
  var atBottom = true, anchor = null, observed = null;
  function rows(list) { return list.querySelectorAll(".watch-ev"); }
  function mark(list) {
    var all = rows(list), box = list.getBoundingClientRect();
    for (var i = 0; i < all.length; i++) {
      var r = all[i].getBoundingClientRect();
      if (r.bottom > box.top) { anchor = {seq: +all[i].dataset.seq, offset: r.top - box.top}; return; }
    }
    anchor = null;
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      var older = document.getElementById("watch-older");
      if (older && !older.disabled) older.click();
    });
  });
  document.addEventListener("click", function (ev) {
    var t = ev.target && ev.target.closest ? ev.target : null;
    var list = document.getElementById("watch-list");
    if (!t || !list) return;
    var older = t.closest("#watch-older");
    if (older && !older.disabled) { atBottom = false; mark(list); }
    else if (t.closest("#watch-live")) { atBottom = true; anchor = null; list.scrollTop = list.scrollHeight; }
  }, true);
  document.addEventListener("scroll", function (ev) {
    var list = ev.target;
    if (!list || list.id !== "watch-list") return;
    atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    if (atBottom) anchor = null; else mark(list);
  }, true);
  new MutationObserver(function () {
    var list = document.getElementById("watch-list");
    if (!list) { atBottom = true; anchor = null; observed = null; return; }
    var top = document.getElementById("watch-top");
    if (top && top !== observed) {
      if (observed) io.unobserve(observed);
      io.observe(top);
      observed = top;
    }
    if (anchor && !atBottom) {
      // The row itself, or the oldest one still after it when it has left from the top.
      var row = null, all = rows(list);
      for (var i = 0; i < all.length && !row; i++) if (+all[i].dataset.seq >= anchor.seq) row = all[i];
      if (row) {
        list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top - anchor.offset;
      }
    } else if (atBottom) {
      list.scrollTop = list.scrollHeight;
    }
  }).observe(document.documentElement,
             {subtree: true, childList: true, attributes: true, attributeFilter: ["data-seq"]});
})();
"""

_WATCH_COLOR = {"denied": "red", "result": "grass", "end": "iris", "turn": "amber", "tool_use": "blue"}


def _watch_row(e: rx.Var[WatchEvent]) -> rx.Component:
    """R10, R12. One event: when, kind, what it says, and its body collapsed unless opened."""
    opened = P.watch_open_seq == e.seq
    return rx.box(
        rx.hstack(
            s.text(e.when, size="1", font_family=_MONO),
            rx.match(e.kind, *[(k, s.badge(k, c)) for k, c in _WATCH_COLOR.items()], s.badge(e.kind, "gray")),
            rx.text(e.label, size="1", weight="medium", overflow_wrap="anywhere"),
            rx.spacer(),
            rx.cond(
                e.collapsed,
                rx.cond(
                    opened,
                    rx.button("Collapse", on_click=P.watch_collapse, size="1", variant="ghost",
                              class_name="watch-collapse"),
                    rx.button("Expand", on_click=P.watch_expand(e.seq), size="1", variant="ghost",
                              class_name="watch-expand"),
                ),
            ),
            width="100%", align="center", spacing="2", flex_wrap="wrap",
        ),
        rx.cond(
            e.body != "",
            rx.el.pre(
                rx.cond(opened, P.watch_open_text, e.body),
                class_name=rx.cond(opened, "watch-body watch-open", "watch-body"),
                style={"white_space": "pre-wrap", "overflow_wrap": "anywhere", "margin": "4px 0 0",
                       "font_size": "12px", "font_family": _MONO},
            ),
        ),
        rx.cond(e.collapsed & ~opened, s.text("… collapsed; press Expand to see all", size="1")),
        rx.cond(e.truncated,
                s.text("cut when stored: kept the first 64 000 of " + e.original_length.to_string() + " characters",
                       size="1", color=rx.color("amber", 11))),
        rx.cond(e.persisted != "", s.text(e.persisted, size="1", color=rx.color("amber", 11))),
        class_name="watch-ev", custom_attrs={"data-seq": e.seq, "data-at": e.at},
        padding="8px 0", border_bottom=f"1px solid {s.LINE}", width="100%",
    )


def _watch_dialog() -> rx.Component:
    """`0073` R10-R13. One step's events, oldest at the top; opens at the bottom."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.hstack(
                rx.dialog.title(P.watch_title, size="4", weight="medium"),
                s.badge(rx.cond(P.watch_status != "", P.watch_status, "—"), "iris"),
                rx.spacer(),
                rx.dialog.close(s.icon_button("x", "Close")),
                width="100%", align="center",
            ),
            rx.dialog.description(
                "This step's events, oldest first.",
                size="1", margin_top="6px",
            ),
            rx.cond(P.watch_note != "", rx.callout(P.watch_note, id="watch-note", size="1",
                                                   color_scheme="amber", margin_top="8px")),
            rx.button("Load older events", id="watch-older", on_click=P.watch_older,
                      disabled=~P.watch_has_older, variant="ghost", size="1", margin_top="8px"),
            rx.box(
                rx.box(id="watch-top", height="1px"),
                rx.foreach(P.watch_events, _watch_row),
                id="watch-list", max_height="62vh", overflow_y="auto", width="100%",
                style={"overflow_anchor": "none"},
            ),
            rx.hstack(
                rx.cond(P.watch_pending > 0,
                        s.text(P.watch_pending.to_string() + " new events", id="watch-pending", size="1")),
                rx.cond(P.watch_has_newer,
                        s.text("Newer events have left this view", id="watch-newer", size="1")),
                rx.cond(P.watch_has_newer | (~P.watch_following & (P.watch_status == "running")),
                        rx.button("Jump to latest", id="watch-live", on_click=P.watch_live, size="1")),
                spacing="3", align="center", margin_top="8px",
            ),
            id="watch-pane", max_width="min(960px, 96vw)", width="96vw",
        ),
        open=P.watch_run != "", on_open_change=P.toggle_watch,
    )


# --- the other dialogs -------------------------------------------------------


def _workspace_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(rx.cond(P.editing == "", "Add a workspace", "Rename the label")),
            rx.dialog.description(
                rx.cond(
                    P.editing == "",
                    "A name is one path segment under the working folder. Leave the URL "
                    "empty to adopt a folder that is already there, or give one to clone.",
                    "Only the label changes. The folder and its name stay as they are.",
                ),
                size="2", margin_top="8px",
            ),
            rx.vstack(
                rx.el.label("Name", html_for="workspace-name", font_size="13px"),
                rx.input(id="workspace-name", placeholder="e.g. my-project",
                         value=P.new_name, on_change=P.set_new_name, width="100%",
                         max_length=64, disabled=P.editing != ""),
                rx.el.label("Label", html_for="workspace-label", font_size="13px"),
                rx.input(id="workspace-label", placeholder="What is this for?",
                         value=P.new_label, on_change=P.set_new_label, width="100%",
                         max_length=200),
                rx.cond(
                    P.editing == "",
                    rx.fragment(
                        rx.el.label("Repository URL (optional)", html_for="workspace-url",
                                    font_size="13px"),
                        rx.input(id="workspace-url", placeholder="https://…",
                                 value=P.new_url, on_change=P.set_new_url, width="100%"),
                    ),
                ),
                rx.cond(P.form_error != "",
                        rx.callout(P.form_error, color_scheme="red", role="alert",
                                   id="workspace-form-error")),
                rx.hstack(
                    rx.dialog.close(rx.button("Cancel", variant="soft", color_scheme="gray")),
                    rx.button("Save", id="save-workspace", on_click=P.save_workspace,
                              loading=P.busy),
                    justify="end", width="100%", spacing="3",
                ),
                spacing="3", width="100%", margin_top="24px",
            ),
            max_width="480px",
        ),
        open=P.workspace_form, on_open_change=P.toggle_workspace_form,
    )


def _remove_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Remove this workspace from the list?"),
            rx.alert_dialog.description(
                "It disappears from this app. The folder on disk, and anything "
                "uncommitted in it, is left exactly where it is.",
            ),
            rx.hstack(
                rx.alert_dialog.cancel(rx.button("Keep it", variant="soft",
                                                 color_scheme="gray")),
                rx.button("Remove from list", id="confirm-remove", color_scheme="red",
                          on_click=P.remove_workspace),
                justify="end", spacing="3", margin_top="24px",
            ),
            max_width="460px",
        ),
        open=P.remove_name != "", on_open_change=P.toggle_remove,
    )


def _command_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Find your next step.", size="5"),
            rx.dialog.description("Search screens and work in this workspace.", size="2"),
            rx.input(rx.input.slot(rx.icon("search", size=17)),
                     id="command-query", placeholder="Where would you like to go?",
                     aria_label="Search screens and work", value=P.command_query,
                     on_change=P.search_commands, margin="20px 0", width="100%"),
            rx.vstack(
                *[
                    rx.cond(
                        rx.Var.create(label.lower()).contains(P.command_query.lower()),
                        rx.button(rx.icon(icon, size=16), label, rx.spacer(),
                                  rx.icon("arrow-up-right", size=14),
                                  aria_label="Go to " + label, on_click=P.navigate(key),
                                  variant="ghost", color_scheme="gray", width="100%",
                                  justify_content="flex-start"),
                    ) for key, label, icon in NAVIGATION
                ],
                rx.foreach(P.cards, lambda u: rx.cond(P.command_ids.contains(u.id), rx.button(
                    rx.icon("file-text", size=16), u.title, on_click=P.open_unit(u.id),
                    variant="ghost", color_scheme="gray", width="100%",
                    justify_content="flex-start", height="auto", padding="10px",
                    white_space="normal",
                ), rx.fragment())),
                spacing="2", width="100%",
            ),
            rx.dialog.close(rx.button("Close", variant="soft", color_scheme="gray",
                                      margin_top="20px")),
            max_width="560px",
        ),
        open=P.command_open, on_open_change=P.toggle_command,
    )


def _mobile_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.hstack(rx.dialog.title("CoS Studio", size="5"), rx.spacer(),
                      rx.dialog.close(s.icon_button("x", "Close navigation")), width="100%"),
            rx.dialog.description("Your workspace, your next step.", size="2"),
            _workspace_select(aria_label="Mobile active workspace", width="100%",
                              margin="24px 0"),
            _nav(mobile=True),
            rx.button(rx.icon("search", size=16), "Search the studio",
                      on_click=[P.toggle_mobile(False), P.toggle_command(True)],
                      variant="soft", width="100%", margin_top="20px"),
            max_width="360px",
        ),
        open=P.mobile_open, on_open_change=P.toggle_mobile,
    )
