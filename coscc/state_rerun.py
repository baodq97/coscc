"""Running a stage of a unit again: the stages offered, the one chosen and the confirmation.

Split from `coscc/state.py` (`0095`). `StudioState` inherits it, so its vars and handlers
keep their names; a handler that needs `SERVICE` or `StudioState` imports them in its body,
because this module cannot import `coscc.state` at the top (`spike.md ## U1`).
"""

from __future__ import annotations

import reflex as rx

from coscc.service import Invalid, describe_base


class RerunMixin(rx.State, mixin=True):
    # `0017`. Per unit: where its worktree is and what preparing it said, as one line.
    # Backend only since `0053`: the page reads the open unit's line, `unit_tree`.
    _trees: dict[str, str] = {}

    @rx.var
    def unit_tree(self) -> str:
        return self._trees.get(self.unit_id, "")
    # `0054` R9. The accepted stages `cos.mjs rerun` says may run again, and for each the
    # stages that then run again after it (R2). Set only by `load_next`, from
    # `SERVICE.rerun_offers`; nothing here works either out. The rest is what a person chose.
    rerun_stages: list[str] = []
    rerun_later: dict[str, list[str]] = {}
    rerun_stage: str = ""
    rerun_note: str = ""
    rerun_confirming: bool = False

    @rx.var
    def rerun_after(self) -> list[str]:
        """`0054` R2. The stages that run again after the chosen one, as `cos.mjs` listed them."""
        return self.rerun_later.get(self.rerun_stage, [])

    # -- running an accepted stage again (`0054` R9) -----------------------------

    @rx.event
    def set_rerun_stage(self, value: str):
        self.rerun_stage = str(value)
        self.rerun_confirming = False

    @rx.event
    def set_rerun_note(self, value: str):
        self.rerun_note = str(value)

    @rx.event
    def ask_rerun(self):
        """The confirmation line first; nothing runs until *Rerun … — spends quota*."""
        self.rerun_confirming = True

    @rx.event
    def cancel_rerun(self):
        self.rerun_confirming = False

    @rx.event(background=True)
    async def run_rerun(self):
        """Run the chosen stage again with the note, streaming what comes back, as `run_step`
        does. Whether it may run is the service's: every refusal is its `Invalid`."""
        from coscc.state import SERVICE, StudioState
        async with self:
            unit, stage, cwd, note = self.unit_id, self.rerun_stage, self.cwd, self.rerun_note
            self.rerun_confirming = False
            if not (unit and stage and cwd):
                self.notice = "Choose a stage to run again."
                return
            self.run_log = ""
            self.log_unit = unit
            self.error = ""

        listed = False
        try:
            async for kind, payload in SERVICE.run_step(cwd, unit, stage, rerun=True, note=note):
                async with self:
                    if not listed:
                        listed = True
                        self.rerun_note = ""
                        self._load_running()
                    if kind == "chunk" and self.log_unit == unit:
                        self.run_log += payload
                    elif kind == "done" and isinstance(payload, dict):
                        if payload.get("error"):
                            self.error = payload["error"]
                        outcome = payload.get("outcome") or ""
                        written = payload.get("artifact") or ""
                        self.notice = f"{stage} {outcome}" + (f" — wrote {written}" if written else "")
                        # `0054` R8: the service found `pr.md` without its `## Answers`.
                        if payload.get("answers_lost"):
                            self.notice += " The session removed the answers pr.md carried."
                        stale = describe_base(payload.get("base"))
                        if stale:
                            self.notice += " " + stale
        except Invalid as e:
            async with self:
                self._fail(e)
        finally:
            async with self:
                await self._load_board()
                self._load_timeline()
                self._load_artifact()
                self._load_activity()
        return StudioState.load_next
