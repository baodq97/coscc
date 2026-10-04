"""Updating the app from the board, and refusing new work while an update waits."""

from __future__ import annotations

from typing import Any

from coscc.update import updater as updater_mod
from coscc.service.common import NotUpdatable, Updating
from coscc.kernel import Invalid


# How often `settle_after_suspend` looks again.
SETTLE_POLL = 0.1


def as_invalid(e: updater_mod.Refused) -> Invalid:
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
        return {
            "line": "Updates apply only to an install made by install.sh.",
            "local_line": "",
            "actions": [],
        }
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


def refuse_while_updating(updater: updater_mod.Updater) -> None:
    try:
        updater.refuse_while_updating()
    except updater_mod.Refused as e:
        raise as_invalid(e) from e


def refuse_mechanical_while_updating(updater: updater_mod.Updater) -> None:
    """A mechanical integration or a retake is refused once Apply is pressed."""
    try:
        updater.refuse_mechanical_while_updating()
    except updater_mod.Refused as e:
        raise as_invalid(e) from e
