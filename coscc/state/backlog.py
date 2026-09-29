"""The backlog, and starting a unit: estimates, relations, the shortlist, a new unit and its
branch.

Handlers import `SERVICE` in their bodies: this module cannot import `coscc.state` at the top.
"""

from __future__ import annotations

import reflex as rx

from coscc.state import present
from coscc.service.common import Invalid
from coscc.state.views import BacklogRow, tree_line


class BacklogMixin(rx.State, mixin=True):
    # -- starting a unit
    new_slug: str = ""
    new_brief: str = ""
    starting: bool = False
    branch: str = ""
    # The Backlog panel, copied from `Service.board`'s `backlog` by `backlog_view`, and what
    # a person types into it. The shortlist is edited row by row.
    backlog_rows: list[BacklogRow] = []
    backlog_rest: list[BacklogRow] = []
    backlog_unestimated: list[str] = []
    backlog_note: str = ""
    backlog_measured: str = ""
    backlog_recorded: str = ""
    backlog_warnings: list[str] = []
    backlog_suggested: list[str] = []
    propose_warning: str = ""
    _backlog_history: dict = {}
    history_unit: str = ""
    history_lines: list[str] = []
    backlog_names: list[str] = []
    shortlist_draft: list[str] = []
    # The unit whose row is open for editing, `""` for none.
    backlog_editing: str = ""
    shortlist_reason: str = ""
    est_unit: str = ""
    est_value: str = ""
    est_effort: str = ""
    est_basis: str = ""
    rel_unit: str = ""
    rel_other: str = ""
    rel_type: str = "liên quan"
    rel_op: str = "add"
    rel_reason: str = ""
    proposing: bool = False

    # -- backlog. Every handler calls `SERVICE` and copies; none decides.

    @rx.event
    def set_backlog_field(self, name: str, value: str):
        if name in (
            "shortlist_reason",
            "est_value",
            "est_effort",
            "est_basis",
            "rel_other",
            "rel_type",
            "rel_op",
            "rel_reason",
        ):
            setattr(self, name, value)

    @rx.event
    def edit_backlog_row(self, unit: str):
        """Open one row's estimate and relation forms, or close the open one."""
        if self.backlog_editing == unit:
            self.backlog_editing = ""
            return
        self.backlog_editing, self.est_unit, self.rel_unit = unit, unit, unit
        self.show_backlog_history(unit)

    @rx.event
    def shortlist_add(self, unit: str):
        if unit not in self.shortlist_draft:
            self.shortlist_draft = [*self.shortlist_draft, unit]

    @rx.event
    def shortlist_remove(self, unit: str):
        self.shortlist_draft = [u for u in self.shortlist_draft if u != unit]

    @rx.event
    def shortlist_move(self, unit: str, step: int):
        """Move one unit of the draft up (-1) or down (1). The page decides nothing else."""
        draft = list(self.shortlist_draft)
        if unit not in draft:
            return
        i = draft.index(unit)
        j = min(max(i + int(step), 0), len(draft) - 1)
        draft[i], draft[j] = draft[j], draft[i]
        self.shortlist_draft = draft

    @rx.event
    def fill_shortlist(self):
        """The first seven of the computed order, into the draft. A person still saves it."""
        self.shortlist_draft = list(self.backlog_suggested)

    @rx.event
    def show_backlog_history(self, unit: str):
        self.history_unit = unit
        self.history_lines = [
            (
                f"{present.when(h.get('at'))} · {h.get('by')} · value {h.get('value')}, effort {h.get('effort')} — {h.get('basis')}"
                if h.get("kind") == "estimate"
                else f"{present.when(h.get('at'))} · {h.get('by')} · {h.get('op')} {h.get('unit')} {present.RELATION_LABEL.get(h.get('type'), h.get('type'))} {h.get('other')} — {h.get('reason')}"
            )
            for h in self._backlog_history.get(unit) or []
        ]

    async def _backlog_write(self, call) -> None:
        try:
            await call
        except Invalid as e:
            self.notice = f"Not recorded: {e}"
            return
        self.notice = "Recorded in the run log. No gate reads it and nothing was started."
        await self._load_board()

    @rx.event
    async def save_shortlist(self):
        from coscc.state import SERVICE

        await self._backlog_write(
            SERVICE.backlog.record_shortlist(
                self.cwd, list(self.shortlist_draft), self.shortlist_reason, ""
            )
        )

    @rx.event
    async def save_estimate(self):
        from coscc.state import SERVICE

        await self._backlog_write(
            SERVICE.backlog.record_estimate(
                self.cwd,
                self.est_unit.strip(),
                self.est_value.strip(),
                self.est_effort.strip(),
                self.est_basis,
                "",
            )
        )

    @rx.event
    async def save_relation(self):
        from coscc.state import SERVICE

        await self._backlog_write(
            SERVICE.backlog.record_relation(
                self.cwd,
                self.rel_unit.strip(),
                self.rel_other.strip(),
                self.rel_type,
                self.rel_op,
                self.rel_reason,
                "",
            )
        )

    @rx.event
    async def propose_estimates(self):
        """Opens one paid session; the warning above the button says so."""
        from coscc.state import SERVICE

        if self.proposing:
            return
        self.proposing = True
        yield
        done: dict = {}
        try:
            async for kind, payload in SERVICE.backlog.propose_estimates(self.cwd):
                if kind == "done":
                    done = payload.get("estimate") or {}
        except Invalid as e:
            self.notice = f"Not started: {e}"
            return
        finally:
            self.proposing = False
        self.notice = (
            f"Proposal {done.get('outcome')}: {done.get('written', 0)} recorded, "
            f"{len(done.get('rejected') or [])} refused"
            + (f" — {done['detail']}" if done.get("detail") else "")
        )
        await self._load_board()

    @rx.event
    def set_new_slug(self, value: str):
        self.new_slug = value

    @rx.event
    def set_new_brief(self, value: str):
        self.new_brief = value

    @rx.event
    async def create_unit(self):
        """Start a work unit from the page. The slug grammar and number come back from `cos.mjs`."""
        from coscc.state import SERVICE

        slug = self.new_slug.strip()
        if not slug:
            self.notice = "Give the work a short name, like `board-cannot-say-what-happened`."
            return
        if not self.new_brief.strip():
            # A rule of this page: the brief becomes `idea.md`, all intent has to work from.
            self.notice = "Say what the problem is, in your own words. The intent step reads it."
            return
        self.starting = True
        try:
            made = await SERVICE.create_unit(self.cwd, slug, self.new_brief)
        except Invalid as e:
            self.notice = str(e)
            return
        finally:
            self.starting = False
        self.new_slug, self.new_brief = "", ""
        self.notice = f"Started {made['unit']}."
        await self._load_board()

    @rx.event
    async def start_branch(self):
        """Cut the open unit's branch in the workspace: the one control here that writes to
        somebody else's git (`coscc/git/gitops.py` lists what that may be)."""
        from coscc.state import SERVICE

        if not self.unit_id:
            return
        try:
            cut = await SERVICE.backlog.start_branch(self.cwd, self.unit_id)
        except Invalid as e:
            self.notice = str(e)
            return
        # Say where it was cut from: the fetched ref, not the local `main`. It is cut in the
        # unit's own worktree, so the workspace's branch does not change.
        said = f"{cut['branch']} cut from {cut['base']} at {cut['sha']}, in {cut['worktree']}."
        if cut.get("switched"):
            said += " The workspace was moved back to main to open it."
        prepared = cut.get("prepare") or {}
        if not prepared.get("ok", True):
            said += (
                f" Preparing it failed: `{prepared.get('command')}` exited "
                f"{prepared.get('exit_code')} — impl will not run until it succeeds."
            )
        self.notice = said
        self._trees = {
            **self._trees,
            self.unit_id: tree_line(
                {"path": cut["worktree"], "branch": cut["branch"], "prepare": prepared}
            ),
        }
