"""The board's *Release* panel (`0046`): what it shows, copied from the board's `release`
block, and its two buttons.

`StudioState` inherits it. Nothing here decides: whether a press may run is
`Service.release_prepare`/`release_publish`'s answer, shown as it is.
"""

from __future__ import annotations

import dataclasses

import reflex as rx

from coscc.service import Invalid

# The label each button carries, by the block's `button`.
RELEASE_BUTTON = {"prepare": "Prepare release", "publish": "Merge and tag"}


@dataclasses.dataclass
class ReleaseUnit:
    """One unit merged since the last release: name, `Type`, pull request, short commit."""

    name: str = ""
    type: str = ""
    pr: str = ""
    sha: str = ""


@dataclasses.dataclass
class ReleaseCommit:
    """One commit since the last release that no unit claims."""

    sha: str = ""
    subject: str = ""


def release_fields(block: dict | None) -> dict:
    """The panel's fields from the board's `release` block; every field empty for none."""
    block = block or {}
    state = str(block.get("state") or "")
    button = str(block.get("button") or "")
    return {
        "rel_state": state,
        "rel_reason": str(block.get("reason") or ""),
        "rel_last_tag": str(block.get("last_tag") or ""),
        "rel_units": [
            ReleaseUnit(name=str(u.get("name") or ""), type=str(u.get("type") or "no type"),
                        pr=f"#{u.get('pr')}", sha=str(u.get("sha") or "")[:7])
            for u in block.get("units") or []
        ],
        "rel_unmatched": [
            ReleaseCommit(sha=str(c.get("sha") or "")[:7], subject=str(c.get("subject") or ""))
            for c in block.get("unmatched") or []
        ],
        "rel_version": str(block.get("version") or ""),
        "rel_button": RELEASE_BUTTON.get(button, ""),
        "rel_phase": button,
        "rel_enabled": bool(block.get("enabled")),
        "rel_disabled_reason": str(block.get("disabled_reason") or ""),
        "rel_pr": "" if block.get("pr") is None else f"#{block.get('pr')}",
        "rel_workflow": str(block.get("workflow") or ""),
        "rel_workflow_url": str(block.get("workflow_url") or ""),
        "rel_release_url": str(block.get("release_url") or ""),
    }


class ReleaseMixin(rx.State, mixin=True):

    rel_state: str = ""
    rel_reason: str = ""
    rel_last_tag: str = ""
    rel_units: list[ReleaseUnit] = []
    rel_unmatched: list[ReleaseCommit] = []
    # Filled with the proposed version on every board read; the person may change it.
    rel_version: str = ""
    rel_button: str = ""
    rel_phase: str = ""
    rel_enabled: bool = False
    rel_disabled_reason: str = ""
    rel_pr: str = ""
    rel_workflow: str = ""
    rel_workflow_url: str = ""
    rel_release_url: str = ""
    # True while a press runs; locks the button.
    releasing: bool = False
    # The version the last read proposed. Backend only: a read that proposes the same one
    # keeps what the person typed over it.
    _rel_read_version: str = ""

    def _show_release(self, block: dict | None) -> None:
        fields = release_fields(block)
        if fields["rel_version"] == self._rel_read_version:
            fields.pop("rel_version")
        else:
            self._rel_read_version = fields["rel_version"]
        for name, value in fields.items():
            setattr(self, name, value)

    @rx.event
    def set_rel_version(self, value: str):
        self.rel_version = value

    @rx.event(background=True)
    async def press_release(self):
        """Run the button the panel shows. In the background, holding the state only to
        read and to write it: a press can take minutes (`uv lock`, a push, a merge and its
        poll), and the rest of the page keeps answering meanwhile."""
        from coscc.state import SERVICE
        async with self:
            if self.releasing or not self.rel_phase:
                return
            self.releasing = True
            phase, cwd, version = self.rel_phase, self.cwd, self.rel_version
        run = SERVICE.release_prepare if phase == "prepare" else SERVICE.release_publish
        done: dict = {}
        notice = ""
        try:
            async for kind, payload in run(cwd, version):
                if kind == "done":
                    done = payload.get("release") or {}
        except Invalid as e:
            notice = f"Not released: {e}"
        else:
            outcome = str(done.get("outcome") or "")
            if outcome == "opened":
                notice = f"Opened release pull request #{done.get('pr')} for {done.get('version')}."
            elif outcome == "tagged":
                notice = f"Pushed v{done.get('version')}; the release workflow builds it on GitHub."
            else:
                notice = f"Release {outcome or 'ended'}: {done.get('detail') or 'see Activity'}"
        finally:
            async with self:
                self.releasing = False
                if notice:
                    self.notice = notice
                await self._load_board()
