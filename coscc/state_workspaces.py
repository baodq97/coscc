"""The Workspaces screen: the list, the form that adds or edits one, removing and pulling.

Split from `coscc/state.py` (`0095`). `StudioState` inherits it, so its vars and handlers
keep their names; a handler that needs `SERVICE` or `StudioState` imports them in its body,
because this module cannot import `coscc.state` at the top (`spike.md ## U1`).
"""

from __future__ import annotations

import reflex as rx

from coscc.service import Invalid
from coscc.state_views import Workspace


class WorkspacesMixin(rx.State, mixin=True):

    # -- workspaces
    workspaces: list[Workspace] = []
    cwd: str = ""
    working_dir: str = ""
    workspace_query: str = ""

    new_name: str = ""
    new_label: str = ""
    new_url: str = ""
    form_error: str = ""
    workspace_form: bool = False
    editing: str = ""
    remove_name: str = ""

    # -- managing workspaces -------------------------------------------------

    @rx.event
    def edit_workspace(self, name: str):
        self.editing = name
        self.form_error = ""
        self.new_name = name
        self.new_url = ""
        self.new_label = next((w.label for w in self.workspaces if w.name == name), "")
        self.workspace_form = True

    @rx.event
    def toggle_workspace_form(self, value: bool):
        self.workspace_form = value
        if not value:
            self.form_error = ""

    @rx.event
    def set_new_name(self, value: str):
        self.new_name = value

    @rx.event
    def set_new_label(self, value: str):
        self.new_label = value

    @rx.event
    def set_new_url(self, value: str):
        self.new_url = value

    @rx.event
    async def save_workspace(self):
        """Adopt, clone, or relabel. Which one is `Service`'s decision, not this file's."""
        from coscc.state import SERVICE
        self.busy, self.form_error = True, ""
        yield
        try:
            if self.editing:
                SERVICE.set_label(self.editing, self.new_label)
            else:
                await SERVICE.add_workspace(
                    self.new_name, label=self.new_label, repo_url=self.new_url or None
                )
            self._load_workspaces()
            self.workspace_form = False
            self.new_name = self.new_url = self.new_label = ""
            self.editing = ""
        except Invalid as e:
            self.form_error = str(e)
        finally:
            self.busy = False
        yield
        await self._load_board()

    @rx.event
    def request_remove(self, name: str):
        self.remove_name = name

    @rx.event
    def toggle_remove(self, value: bool):
        if not value:
            self.remove_name = ""

    @rx.event
    async def remove_workspace(self):
        """De-lists only. The directory stays on disk — `spec.md` R18."""
        from coscc.state import SERVICE
        name, self.remove_name = self.remove_name, ""
        if not name:
            return
        try:
            SERVICE.remove_workspace(name)
            self._load_workspaces()
            self.notice = f"{name} is off the list. Its folder is untouched."
        except Invalid as e:
            self._fail(e)
        yield
        await self._load_board()

    @rx.event
    async def pull(self, name: str):
        from coscc.state import SERVICE
        self.busy, self.error = True, ""
        yield
        try:
            await SERVICE.pull_workspace(name)
            self.notice = f"{name} is up to date."
        except Invalid as e:
            # A failed fast-forward is normal and must be visible, not swallowed.
            self._fail(e)
        finally:
            self.busy = False
