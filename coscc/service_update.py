"""Updating the app from the board, and refusing new work while an update waits.

Split from `coscc/service.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

import asyncio
from typing import Any

from coscc import updater as updater_mod
from coscc.service_common import Invalid, NotUpdatable, OWNER, StaleCutList, Updating


def _as_invalid(e: updater_mod.Refused) -> Invalid:
    if isinstance(e, updater_mod.Stale):
        return StaleCutList(str(e), e.listing)
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
        line = "An update waits for the running work to finish."
        actions.append("cancel")
    else:
        for channel, ready in (("release", rs == "ready"), ("local", ls == "ready")):
            if ready:
                actions += [f"apply-{channel}", f"now-{channel}"]
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

    def _update_jobs(self) -> list[dict[str, Any]]:
        """R8. What this process is running now, besides the updater's own build."""
        jobs: list[dict[str, Any]] = []
        for r in self.steps.all():
            jobs.append({
                "kind": "step", "id": f"step:{r.workspace}:{r.unit}", "workspace": r.workspace,
                "unit": r.unit, "stage": r.stage, "started": r.started_at,
            })
        for entry in self._running.values():
            if entry["stage"] == "integrate":
                jobs.append({
                    "kind": "integration", "id": f"integration:{entry['workspace']}:{entry['unit']}",
                    "workspace": entry["workspace"], "unit": entry["unit"], "stage": "integrate",
                    "started": entry["started"],
                })
            elif entry["stage"] == "precedent":
                # `0044`. Waited for like an estimate, never cut: its money is spent either way.
                jobs.append({
                    "kind": "integration", "id": f"precedent:{entry['workspace']}:{entry['unit']}",
                    "workspace": entry["workspace"], "unit": entry["unit"], "stage": "precedent",
                    "started": entry["started"],
                })
            elif entry["stage"] == "estimate":
                # `0074`. Waited for like an integration, never cut: its money is spent either way.
                jobs.append({
                    "kind": "integration", "id": f"estimate:{entry['workspace']}",
                    "workspace": entry["workspace"], "unit": "", "stage": "estimate",
                    "started": entry["started"],
                })
        for entry in self._retakes.values():
            # `0111` review round 1, F3. Waited for like an integration, never cut: no Stop
            # reaches it, and `retake.take` puts `.screens/` back only if it gets to.
            jobs.append({
                "kind": "integration", "id": f"screens:{entry['workspace']}:{entry['unit']}",
                "workspace": entry["workspace"], "unit": entry["unit"], "stage": "screens",
                "started": entry["started"],
            })
        for turn in self.sessions.in_flight():
            jobs.append({"kind": "chat", "id": f"chat:{turn['id']}", "turn": turn["id"],
                         "session_id": turn["session_id"], "workspace": turn["workspace"],
                         "started": turn["started"]})
        return jobs

    async def _update_cut(self, job: dict[str, Any], by: str) -> bool:
        """R10. A step through Stop's own road; a chat turn closed. Never an integration."""
        if job["kind"] == "step":
            try:
                await self._stop_running(job["workspace"], job["unit"], by)
            except Invalid:
                return False  # already ended, or writing its artifact: it is waited for
            return True
        if job["kind"] == "chat":
            return await self.sessions.cut_turn(job["turn"])
        return False

    def update_status(self) -> dict[str, Any]:
        """`Updater.status`, unchanged, with `0082` R9's `line`, `local_line` and `actions`."""
        status = self.updater.status()
        return {**status, **update_words(status)}

    def update_cut_list(self) -> dict[str, Any]:
        try:
            return self.updater.cut_list()
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    async def update_apply(self, channel: str, mode: str, by: str, token: str) -> dict[str, Any]:
        try:
            return await self.updater.apply(channel, mode, (by or "").strip() or OWNER, token)
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
