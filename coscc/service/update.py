"""Updating the app from the board, and refusing new work while an update waits. A mixin with no fields."""

from __future__ import annotations

import asyncio
from typing import Any

from coscc.update import updater as updater_mod
from coscc.runlog.journal import BadRecord, Busy
from coscc.service.common import Invalid, NotUpdatable, OWNER, Updating


# How often `settle_after_suspend` looks again.
SETTLE_POLL = 0.1


def _as_invalid(e: updater_mod.Refused) -> Invalid:
    if isinstance(e, updater_mod.Updating):
        return Updating(str(e))
    if isinstance(e, updater_mod.NotHere):
        return NotUpdatable(str(e))
    return Invalid(str(e))


# The release channel's state in plain words; `{v}` is the offered version.
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
    """What the Updates section says and which buttons it shows, from `Updater.status`.
    A button that could not be used is not listed; nothing names an environment variable."""
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
        # The only things an update still waits for, in one sentence.
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

# -- updating the app -----------------------------------------------------
#
# Every decision is `Updater`'s; these translate its refusals into `Invalid`, so a route
# maps one exception type.

    def _refuse_while_updating(self) -> None:
        try:
            self.updater.refuse_while_updating()
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def _refuse_mechanical_while_updating(self) -> None:
        """A mechanical integration or a retake is refused once Apply is pressed."""
        try:
            self.updater.refuse_mechanical_while_updating()
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def _update_waited(self) -> list[dict[str, Any]]:
        """What an Apply waits for: a mechanical integration and a screenshot retake.
        Gebo sessions, steps, estimates and chat are paused by `suspend_sessions`; what of
        them had no session open gets `settle_after_suspend`'s bounded wait."""
        jobs: list[dict[str, Any]] = []
        for entry in self._running.values():
            if entry["stage"] == "integrate" and entry.get("kind") != "gebo":
                jobs.append({
                    "kind": "integration", "id": f"integration:{entry['workspace']}:{entry['unit']}",
                    "workspace": entry["workspace"], "unit": entry["unit"], "stage": "integrate",
                    "started": entry["started"],
                })
        for entry in self._retakes.values():
            # No Stop reaches it, and `retake.take` puts `.screens/` back only if it gets to.
            jobs.append({
                "kind": "integration", "id": f"screens:{entry['workspace']}:{entry['unit']}",
                "workspace": entry["workspace"], "unit": entry["unit"], "stage": "screens",
                "started": entry["started"],
            })
        return jobs

    async def suspend_sessions(self, by: str) -> list[dict[str, Any]]:
        """Every session paused, and one `suspend` row written for each, before this process
        hands off. With no working folder there is nowhere to write one, and the sessions end
        as a restart ends them."""
        records = await self.sessions.suspend_all()
        journal = self._journal()
        written: list[dict[str, Any]] = []
        for record in records:
            owner = record.get("owner") or {}
            if journal is None:
                break
            if not owner.get("kind"):
                continue  # a caller that named no owner: nothing could take it up again
            try:
                written.append(journal.suspended(
                    str(owner.get("workspace") or ""), str(owner.get("unit") or ""),
                    str(owner.get("stage") or ""), by=by, **record,
                ))
            except (BadRecord, Busy):
                continue
        return written

    async def settle_after_suspend(self, within: float) -> list[dict[str, Any]]:
        """`suspend_sessions` pauses only what had a session open: a step writing its round to
        the PR or syncing `pr.md` after its `end`, or a Gebo reading the PR's head after its
        session, had none. Each gets `within` seconds to finish (no new session may open
        meanwhile) and what still runs is returned, for the updater to name in a `cut` row
        before `shutdown` cancels it. A step past its `_running` entry, writing its `questions`
        or `ship` record, counts as `after-end` until its task ends.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        while (self._running or self._finishing) and loop.time() < deadline:
            await asyncio.sleep(SETTLE_POLL)
        left = list(self._running.values())
        left += [{**entry, "kind": "after-end"} for entry, _task in self._finishing.values()]
        return [{k: entry.get(k) for k in ("kind", "workspace", "unit", "stage", "started")} for entry in left]

    def update_status(self) -> dict[str, Any]:
        """`Updater.status`, unchanged, with `line`, `local_line` and `actions`."""
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

        No `end` is written: a step with no `end` is what an app that went down mid-step looks like.
        """
        # The autopilot first, so no pass starts a step while the rest go down.
        for key in list(self._autopilot_tasks):
            self.autopilot_stop(key)
        for t in list(self._autopilot_pending):
            t.cancel()
        # A CI ask holds nothing worth waiting for.
        for t in list(self._ci_asks.values()):
            t.cancel()
        tasks = [r.task for r in self.steps.all() if r.task is not None and not r.task.done()]
        # A step's task past `steps.release`, still in its `_after_end`.
        tasks += [t for _entry, t in self._finishing.values() if not t.done() and t not in tasks]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=10)
