"""Updating the app from the board, and refusing new work while an update waits.

Split from `coscc/service/__init__.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

import asyncio
from typing import Any

from coscc.update import updater as updater_mod
from coscc.runlog.journal import BadRecord, Busy
from coscc.service.common import Invalid, NotUpdatable, OWNER, Updating


def _as_invalid(e: updater_mod.Refused) -> Invalid:
    if isinstance(e, updater_mod.Updating):
        return Updating(str(e))
    if isinstance(e, updater_mod.NotHere):
        return NotUpdatable(str(e))
    return Invalid(str(e))


# `0082` R9. The release channel's state in plain words; `{v}` is the offered version.
_RELEASE_LINE = {
    "ready": "Version {v} is ready to apply.",
    "up-to-date": "This is the latest release.",
    "off": "Release checks are off.",
    "unavailable": "Releases could not be checked.",
    "downloading": "Downloading version {v}.",
    "error": "The last download failed.",
    "blocked": "Updates are held after a failed update.",
}
_LOCAL_LINE = {
    "unconfigured": "Local builds are not set up.",
    "ready": "A local build ({v}) is ready to apply.",
    "building": "A local build is running.",
    "error": "The last local build failed.",
    "blocked": "Local builds are held after a failed update.",
}


def update_words(status: dict[str, Any]) -> dict[str, Any]:
    """`0082` R9. What the Updates section says and which buttons it shows, from
    `Updater.status`. A button that could not be used is not listed; nothing names an
    environment variable."""
    if status.get("shape") != "service":
        return {"line": "Updates apply only to an install made by install.sh.", "local_line": "", "actions": []}
    state = status.get("state") or ""
    if state == "applying":
        return {"line": "Updating now.", "local_line": "", "actions": []}
    release, local = status.get("release") or {}, status.get("local") or {}
    rs, ls = release.get("state") or "", local.get("state") or ""
    line = _RELEASE_LINE.get(rs, "").format(v=release.get("version") or "")
    local_line = _LOCAL_LINE.get(ls, "").format(v=local.get("version") or "")
    actions: list[str] = []
    if state == "pending":
        # `0138` R12: the only two things an update still waits for, in one sentence.
        line = updater_mod.WAITING_WARNING
        actions.append("cancel")
    else:
        for channel, ready in (("release", rs == "ready"), ("local", ls == "ready")):
            if ready:
                actions.append(f"apply-{channel}")
    if ls not in ("unconfigured", "building", "blocked", ""):
        actions.append("build-local")
    return {"line": line, "local_line": local_line, "actions": actions}


class UpdateMixin:

    # -- updating the app (`0068`) -------------------------------------------
    #
    # Every decision is `Updater`'s; these translate its refusals into `Invalid`, as the
    # rest of this file does, so a route maps one exception type.

    def _refuse_while_updating(self) -> None:
        try:
            self.updater.refuse_while_updating()
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def _refuse_mechanical_while_updating(self) -> None:
        """`0138` R3: a mechanical integration or a retake, refused once Apply is pressed."""
        try:
            self.updater.refuse_mechanical_while_updating()
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def _update_waited(self) -> list[dict[str, Any]]:
        """`0138` R2, R3. What an Apply waits for: a mechanical integration and a screenshot
        retake (spec.md ## Answers, câu 1, Jera's inference), and since `0131` a knowledge
        gather, whose sessions are not the app's. A Gebo session, a step, an
        estimate, Jera and chat are paused by `suspend_sessions` instead (C10)."""
        jobs: list[dict[str, Any]] = []
        for entry in self._running.values():
            if entry["stage"] == "integrate" and entry.get("kind") != "gebo":
                jobs.append({
                    "kind": "integration", "id": f"integration:{entry['workspace']}:{entry['unit']}",
                    "workspace": entry["workspace"], "unit": entry["unit"], "stage": "integrate",
                    "started": entry["started"],
                })
            elif entry["stage"] == "knowledge":
                # `0131` R1. A gather after a ship: waited for, never paused, because it runs on
                # a `Sessions` of its own that `suspend_sessions` does not reach (`0138`).
                jobs.append({
                    "kind": "integration", "id": f"knowledge:{entry['workspace']}:{entry['unit']}",
                    "workspace": entry["workspace"], "unit": entry["unit"], "stage": "knowledge",
                    "started": entry["started"],
                })
        for entry in self._retakes.values():
            # `0111` review round 1, F3. No Stop reaches it, and `retake.take` puts `.screens/`
            # back only if it gets to.
            jobs.append({
                "kind": "integration", "id": f"screens:{entry['workspace']}:{entry['unit']}",
                "workspace": entry["workspace"], "unit": entry["unit"], "stage": "screens",
                "started": entry["started"],
            })
        return jobs

    async def suspend_sessions(self, by: str) -> list[dict[str, Any]]:
        """`0138` R4-R6. Every session paused, and one `suspend` row written for each, before
        this process hands off. With no working folder there is nowhere to write one, and
        the sessions end as a restart ends them."""
        records = await self.sessions.suspend_all()
        journal = self._journal()
        written: list[dict[str, Any]] = []
        for record in records:
            owner = record.get("owner") or {}
            if journal is None:
                break
            try:
                written.append(journal.suspended(
                    str(owner.get("workspace") or ""), str(owner.get("unit") or ""),
                    str(owner.get("stage") or ""), by=by, **record,
                ))
            except (BadRecord, Busy):
                continue
        return written

    def update_status(self) -> dict[str, Any]:
        """`Updater.status`, unchanged, with `0082` R9's `line`, `local_line` and `actions`."""
        status = self.updater.status()
        return {**status, **update_words(status)}

    async def update_apply(self, channel: str, by: str) -> dict[str, Any]:
        try:
            return await self.updater.apply(channel, (by or "").strip() or OWNER)
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def update_cancel(self, by: str) -> dict[str, Any]:
        try:
            return self.updater.cancel((by or "").strip() or OWNER)
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def update_build_local(self, by: str) -> dict[str, Any]:
        try:
            return self.updater.build_local((by or "").strip() or OWNER)
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    async def shutdown(self) -> None:
        """Cancel every step still running, and wait for them, 10 seconds at most.

        No `end` is written for them (C6): a step with no `end` is what an app that went
        down in the middle of it looks like, and that is what happened.
        """
        # `0043`: the autopilot first, so no pass starts a step while the rest go down.
        for key in list(self._autopilot_tasks):
            self.autopilot_stop(key)
        for t in list(self._autopilot_pending):
            t.cancel()
        # `0100` R6. A CI ask holds nothing worth waiting for.
        for t in list(self._ci_asks.values()):
            t.cancel()
        tasks = [r.task for r in self.steps.all() if r.task is not None and not r.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=10)
