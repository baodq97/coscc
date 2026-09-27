"""An idea several units share: starting one, its page, and opening a unit from it (`0040`).

Split the way `coscc/state/backlog.py` is (`0095`). `StudioState` inherits it; a handler that
needs `SERVICE` imports it in its body, because this module cannot import `coscc.state` at
the top. Every decision is `Service`'s; these only copy and ask.
"""

from __future__ import annotations

import reflex as rx

from coscc.service import Invalid
from coscc.state.views import ChildRow, IdeaRow, child_rows, idea_rows
from coscc.web import place


class IdeasMixin(rx.State, mixin=True):

    # The Board's list, and its *Start an idea* box.
    ideas: list[IdeaRow] = []
    new_idea_slug: str = ""
    new_idea_brief: str = ""
    # The `/idea` page.
    idea_id: str = ""
    idea_title: str = ""
    idea_brief: str = ""
    idea_ref: str = ""
    idea_note: str = ""
    idea_units: list[ChildRow] = []
    idea_workspaces: list[str] = []
    idea_depends: list[str] = []
    child_ws: str = ""
    child_slug: str = ""
    child_depends: str = ""

    def _show_ideas(self, data: dict) -> None:
        self.ideas = idea_rows(data, self._name_of(self.cwd))

    async def _load_idea(self, idea_id: str) -> None:
        from coscc.state import SERVICE

        self.idea_id, self.idea_note, self.idea_units = idea_id, "", []
        try:
            page = await SERVICE.idea(self.cwd, idea_id)
        except Invalid as e:
            self.idea_title, self.idea_brief, self.idea_ref = idea_id, "", ""
            self.idea_note = str(e)
            return
        self.idea_title, self.idea_brief, self.idea_ref = page["title"], page["brief"], page["ref"]
        self.idea_units = child_rows(page)
        self.idea_workspaces = list(page["workspaces"])
        self.idea_depends = [r.ref for r in self.idea_units if not r.missing]
        if self.child_ws not in self.idea_workspaces:
            self.child_ws = self.idea_workspaces[0] if self.idea_workspaces else ""

    @rx.event
    def set_new_idea_slug(self, value: str):
        self.new_idea_slug = value

    @rx.event
    def set_new_idea_brief(self, value: str):
        self.new_idea_brief = value

    @rx.event
    def set_child_ws(self, value: str):
        self.child_ws = value

    @rx.event
    def set_child_slug(self, value: str):
        self.child_slug = value

    @rx.event
    def set_child_depends(self, value: str):
        self.child_depends = "" if value == "none" else value

    @rx.event
    def create_idea(self):
        """`0040` R15 (1). Start an idea in this workspace and go to its page."""
        from coscc.state import SERVICE

        try:
            made = SERVICE.create_idea(self.cwd, self.new_idea_slug.strip(), self.new_idea_brief)
        except Invalid as e:
            self.notice = str(e)
            return
        self.new_idea_slug, self.new_idea_brief = "", ""
        return rx.redirect(place.href(place.Place("idea", self._name_of(self.cwd), idea=made["id"])))

    @rx.event
    async def open_child(self):
        """`0040` R15 (1). Open a unit from this idea in the workspace chosen, then read the
        page again. The unit is made in that workspace's store, not this one's."""
        from coscc.state import SERVICE

        where = next((w.id for w in self.workspaces if w.name == self.child_ws), "")
        if not where:
            self.notice = "Choose a workspace for the unit."
            return
        try:
            made = await SERVICE.create_unit(
                where, self.child_slug.strip(), idea=self.idea_ref, depends_on=self.child_depends,
            )
        except Invalid as e:
            self.notice = str(e)
            return
        self.child_slug, self.child_depends = "", ""
        self.notice = f"Opened {self.child_ws}/{made['unit']}."
        await self._load_idea(self.idea_id)
