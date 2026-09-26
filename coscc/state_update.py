"""Updating the app from the board: the channel's state, the cut list and the buttons.

Split from `coscc/state.py` (`0095`). `StudioState` inherits it, so its vars and handlers
keep their names; a handler that needs `SERVICE` or `StudioState` imports them in its body,
because this module cannot import `coscc.state` at the top (`spike.md ## U1`).
"""

from __future__ import annotations

import reflex as rx

from coscc import present
from coscc.service import Invalid, StaleCutList
from coscc.state_views import _channel_line, _job_line


class UpdateMixin(rx.State, mixin=True):

    # -- `0068`: the *Update* panel. Every field is copied from `Service.update_status`,
    # re-read on load, on every screen change and on every `poll_running` ask; the page
    # decides nothing about an update. `update_pending` is what Run and Send warn on (R9).
    upd_version: str = ""
    upd_commit: str = ""
    upd_available: bool = False
    upd_state: str = ""
    upd_waiting: list[str] = []
    upd_pending_reason: str = ""
    upd_release: str = ""
    upd_release_ready: bool = False
    upd_local: str = ""
    upd_local_ready: bool = False
    upd_local_configured: bool = False
    upd_local_tail: str = ""
    upd_checked_at: str = ""
    upd_error: str = ""
    upd_error_tail: str = ""
    upd_last: str = ""
    upd_last_tail: str = ""
    update_pending: bool = False
    update_warning: str = ""
    # `0082` R9: the service's line and the buttons it lists; the full sha for *Details*.
    upd_line: str = ""
    upd_local_line: str = ""
    upd_actions: list[str] = []
    upd_commit_full: str = ""
    # R10's confirmation: what "áp dụng ngay" would cut, and the token of that list.
    cut_open: bool = False
    cut_channel: str = ""
    cut_items: list[str] = []
    cut_token: str = ""

    # -- `0068`: updating the app. Every rule is `Updater`'s, behind `Service`; a refusal
    # arrives here as its words.

    def _load_update(self) -> None:
        from coscc.state import SERVICE
        u = SERVICE.update_status()
        self.upd_version = str(u.get("version") or "")
        self.upd_commit = present.short_sha(u.get("commit"))
        self.upd_commit_full = str(u.get("commit_label") or u.get("commit") or "")
        self.upd_line = str(u.get("line") or "")
        self.upd_local_line = str(u.get("local_line") or "")
        self.upd_actions = [str(a) for a in u.get("actions") or []]
        self.upd_available = u.get("shape") == "service"
        self.upd_state = str(u.get("state") or "")
        pending = u.get("pending") or {}
        self.upd_waiting = [_job_line(j) for j in pending.get("waiting") or []]
        self.upd_pending_reason = str(pending.get("reason") or "")
        self.update_pending = self.upd_state == "pending"
        self.update_warning = str(u.get("warning") or "")
        release, local = u.get("release") or {}, u.get("local") or {}
        self.upd_release = _channel_line(release)
        self.upd_release_ready = release.get("state") == "ready"
        self.upd_local = _channel_line(local)
        self.upd_local_ready = local.get("state") == "ready"
        self.upd_local_configured = bool(local) and local.get("state") != "unconfigured"
        self.upd_local_tail = str(local.get("log_tail") or "")
        self.upd_checked_at = present.when(u.get("checked_at"))
        error = u.get("error") or {}
        self.upd_error = str(error.get("message") or "")
        self.upd_error_tail = str(error.get("log_tail") or "")
        last = u.get("last") or {}
        self.upd_last = (
            f"{last.get('result')}: {last.get('from')} → {last.get('to')}"
            if last else ""
        )
        self.upd_last_tail = str(last.get("log_tail") or "")

    @rx.event
    async def apply_update(self, channel: str):
        """R7: apply, or wait for what is running (R9)."""
        from coscc.state import SERVICE
        try:
            await SERVICE.update_apply(channel, "wait", "", "")
        except Invalid as e:
            self._fail(e)
        self._load_update()

    @rx.event
    def show_cut_list(self, channel: str):
        """R10: what "apply now" would cut, shown before anything is cut."""
        from coscc.state import SERVICE
        try:
            listing = SERVICE.update_cut_list()
        except Invalid as e:
            self._fail(e)
            return
        self._show_cut(channel, listing)

    def _show_cut(self, channel: str, listing: dict) -> None:
        self.cut_channel = channel
        self.cut_items = [f"{i['action']}: {_job_line(i)}"
                          for i in listing.get("items") or []]
        self.cut_token = str(listing.get("token") or "")
        self.cut_open = True

    @rx.event
    def close_cut_list(self):
        self.cut_open = False

    @rx.event
    async def confirm_apply_now(self):
        from coscc.state import SERVICE
        try:
            await SERVICE.update_apply(self.cut_channel, "now", "", self.cut_token)
            self.cut_open = False
        except StaleCutList as e:
            # The list changed since it was shown: show the new one, cut nothing.
            self.notice = str(e)
            self._show_cut(self.cut_channel, e.listing)
        except Invalid as e:
            self._fail(e)
        self._load_update()

    @rx.event
    def cancel_update(self):
        from coscc.state import SERVICE
        try:
            SERVICE.update_cancel("")
        except Invalid as e:
            self._fail(e)
        self._load_update()

    @rx.event
    def build_local(self):
        from coscc.state import SERVICE
        try:
            SERVICE.update_build_local("")
        except Invalid as e:
            self._fail(e)
        self._load_update()
