"""Watching a running step: the window of its events, paging and following them.

Split from `coscc/state.py` (`0095`). `StudioState` inherits it, so its vars and handlers
keep their names; a handler that needs `SERVICE` or `StudioState` imports them in its body,
because this module cannot import `coscc.state` at the top (`spike.md ## U1`).
"""

from __future__ import annotations

import reflex as rx

from coscc import events as events_mod
from coscc.service import Invalid
from coscc.state_views import (
    NO_RUN_NOTE,
    WATCH_GATHER,
    WATCH_WINDOW,
    WatchEvent,
    _watch_events,
    _watch_note,
)


class WatchMixin(rx.State, mixin=True):
    # -- `0073`: the watch pane. One `run` at a time; `_watch_token` changes whenever the pane
    # closes or opens another, and the loop following the old one stops at its next batch.
    watch_run: str = ""
    watch_unit: str = ""
    watch_title: str = ""
    watch_status: str = ""
    watch_note: str = ""
    watch_events: list[WatchEvent] = []
    watch_has_older: bool = False
    # Older pages pushed the newest rows out of the list; *Jump to latest* brings them back.
    watch_has_newer: bool = False
    # At the bottom and taking new events; off once older ones were loaded.
    watch_following: bool = False
    # New events not in the list because it was full while the person read older ones.
    watch_pending: int = 0
    watch_open_seq: int = 0
    watch_open_text: str = ""
    _watch_token: int = 0

    # -- `0073`: watching a step. Every read is `Service.events_page` or `.follow_events`; the
    # page only keeps the list under `WATCH_WINDOW`. Nothing here writes anything anywhere.

    def _watch_reset(self, run: str, title: str, unit: str) -> None:
        self._watch_token += 1
        self.watch_run, self.watch_title, self.watch_unit = run, title, unit
        self.watch_status, self.watch_note = "", ""
        self.watch_events, self.watch_has_older, self.watch_has_newer = [], False, False
        self.watch_following, self.watch_pending = False, 0
        self.watch_open_seq, self.watch_open_text = 0, ""

    def _watch_page(self, before: int | None = None) -> dict | None:
        from coscc.state import SERVICE
        try:
            return SERVICE.events_page(self.cwd, self.watch_unit, self.watch_run, before=before)
        except Invalid as e:
            self.watch_note = str(e)
            return None

    def _watch_take(self, fresh: list[WatchEvent]) -> None:
        """New events, by `plan.md` step 7's rule: at the bottom, appended and the oldest
        dropped past `WATCH_WINDOW`; reading older ones, appended while there is room and
        counted in `watch_pending` once there is none. An event already shown is dropped: a
        batch the follower yielded before *Jump to latest* read the last page again is also in that
        page (`review.md` F1)."""
        last = self.watch_events[-1].seq if self.watch_events else 0
        fresh = [e for e in fresh if e.seq > last]
        if not fresh:
            return
        if self.watch_following:
            kept = self.watch_events + fresh
            if len(kept) > WATCH_WINDOW:
                kept = kept[-WATCH_WINDOW:]
                self.watch_has_older = True
            self.watch_events = kept
            return
        # Rows past the last one shown were dropped: a new event appended would sit after a gap.
        room = 0 if self.watch_has_newer else WATCH_WINDOW - len(self.watch_events)
        if room > 0:
            self.watch_events = self.watch_events + fresh[:room]
        self.watch_pending += max(0, len(fresh) - max(room, 0))

    @rx.event
    def open_watch(self, run: str, title: str, unit: str = ""):
        """R10. Open the pane on one step's `run`. A timeline row with no `run` opens it on
        R13's line instead."""
        from coscc.state import StudioState
        self._watch_reset(run, title or run, unit or self.unit_id)
        if not run:
            self.watch_run = "-"
            self.watch_note = NO_RUN_NOTE
            return
        return StudioState.watch_follow

    @rx.event
    def close_watch(self):
        self._watch_reset("", "", "")

    @rx.event
    def toggle_watch(self, value: bool):
        if not value:
            self._watch_reset("", "", "")

    @rx.event(background=True)
    async def watch_follow(self):
        """R10, R11. The last page, then every new event of a running step, gathered up to
        `WATCH_GATHER` seconds. Reading the page first and following from its last `seq` is
        safe: a running step's recorder holds every event, so nothing falls in between."""
        from coscc.state import SERVICE, StudioState
        async with self:
            token, run = self._watch_token, self.watch_run
            page = self._watch_page()
            if page is None:
                return
            self.watch_events = _watch_events(page["events"])
            self.watch_has_older = bool(page["has_older"])
            self.watch_has_newer, self.watch_pending = False, 0
            self.watch_status = str(page["status"])
            self.watch_note = _watch_note(page)
            self.watch_following = True
            cwd, unit = self.cwd, self.watch_unit
            last = self.watch_events[-1].seq if self.watch_events else 0
        if page["status"] != "running":
            return
        try:
            async for kind, value in SERVICE.follow_events(cwd, unit, run, after=last, gather=WATCH_GATHER):
                async with self:
                    if self._watch_token != token:
                        return
                    if kind == "events":
                        self._watch_take(_watch_events(value))
                        if value and value[-1].get("kind") == "end":
                            self.watch_status = "ended"
                    elif kind == "cut":
                        # Fell `SUB_LIMIT` behind: start again from the last page.
                        return StudioState.watch_follow
                    else:
                        self.watch_status = str(value.get("status") or "")
                        self.watch_note = _watch_note(value)
        except Invalid as e:
            async with self:
                if self._watch_token == token:
                    self.watch_note = str(e)

    @rx.event
    def watch_older(self):
        """R7, R10. The page before the first event shown; the pane stops following. A full
        list drops its newest rows, and says so, for a step that has ended too
        (`review.md` F2)."""
        if not self.watch_events or not self.watch_has_older:
            return
        page = self._watch_page(before=self.watch_events[0].seq)
        if page is None:
            return
        older = _watch_events(page["events"])
        self.watch_following = False
        kept = older + self.watch_events
        if len(kept) > WATCH_WINDOW:
            kept = kept[:WATCH_WINDOW]
            self.watch_has_newer = True
        self.watch_events = kept
        self.watch_has_older = bool(page["has_older"])

    @rx.event
    def watch_live(self):
        """*Jump to latest*: the last page again, and following again."""
        page = self._watch_page()
        if page is None:
            return
        self.watch_events = _watch_events(page["events"])
        self.watch_has_older = bool(page["has_older"])
        self.watch_has_newer, self.watch_pending = False, 0
        self.watch_following = True

    @rx.event
    def watch_expand(self, seq: int):
        """R12 *Mở*: one event whole, as stored, in a var of its own."""
        from coscc.state import SERVICE
        try:
            page = SERVICE.events_page(self.cwd, self.watch_unit, self.watch_run, seq=int(seq))
        except Invalid as e:
            self.watch_note = str(e)
            return
        if page["events"]:
            self.watch_open_seq = int(seq)
            self.watch_open_text = events_mod.full_text(page["events"][0])

    @rx.event
    def watch_collapse(self):
        self.watch_open_seq, self.watch_open_text = 0, ""
