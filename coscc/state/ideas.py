"""An idea several units share: starting one, its page, and opening a unit from it.

`StudioState` inherits it. Every decision is `Service`'s.
"""

from __future__ import annotations

import reflex as rx

from coscc.kernel import Invalid
from coscc.state.views import ChildRow, IdeaRow, child_rows, idea_rows
from coscc.state import app, place

# The first item of *Depends on*: a select cannot hold an empty value, so a chosen
# dependency could not otherwise be taken back.
NO_DEPENDENCY = "No dependency"


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
        self.idea_id, self.idea_note, self.idea_units, self.idea_depends = idea_id, "", [], []
        try:
            page = await app.SERVICE.ideas.idea(self.cwd, idea_id)
        except Invalid as e:
            self.idea_title, self.idea_brief, self.idea_ref = idea_id, "", ""
            self.idea_note = str(e)
            return
        self.idea_title, self.idea_brief, self.idea_ref = page["title"], page["brief"], page["ref"]
        self.idea_units = child_rows(page)
        self.idea_workspaces = list(page["workspaces"])
        refs = [r.ref for r in self.idea_units if not r.missing]
        self.idea_depends = [NO_DEPENDENCY, *refs] if refs else []
        if self.child_depends not in refs:
            self.child_depends = ""
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
        self.child_depends = "" if value == NO_DEPENDENCY else value

    @rx.event
    def create_idea(self):
        """Start an idea in this workspace and go to its page."""
        try:
            made = app.SERVICE.ideas.create_idea(
                self.cwd, self.new_idea_slug.strip(), self.new_idea_brief
            )
        except Invalid as e:
            self.notice = str(e)
            return
        self.new_idea_slug, self.new_idea_brief = "", ""
        return rx.redirect(
            place.href(place.Place("idea", self._name_of(self.cwd), idea=made["id"]))
        )

    @rx.event
    async def open_child(self):
        """Open a unit from this idea in the workspace chosen; the unit is made in that
        workspace's store, not this one's."""
        where = next((w.id for w in self.workspaces if w.name == self.child_ws), "")
        if not where:
            self.notice = "Choose a workspace for the unit."
            return
        try:
            made = await app.SERVICE.answers.create_unit(
                where,
                self.child_slug.strip(),
                idea=self.idea_ref,
                depends_on=self.child_depends,
            )
        except Invalid as e:
            self.notice = str(e)
            return
        self.child_slug, self.child_depends = "", ""
        self.notice = f"Opened {self.child_ws}/{made['unit']}."
        await self._load_idea(self.idea_id)
