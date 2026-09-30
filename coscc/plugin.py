"""The core's door for a feature: what it gets of the running app, and what it hands back.

A feature (`coscc/features/<name>.py`) ends in one `PLUGIN` and reaches the app only through a
`Ctx`. It is turned off per workspace by the pref `features.off`, `{feature: [workspace keys]}`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from starlette.routing import BaseRoute

from coscc.bus import Bus
from coscc.data import Data
from coscc.runlog.journal import Journal
from coscc.service import Service
from coscc.service.common import Invalid
from coscc.service.workspaces import Workspaces

OFF_PREF = "features.off"


@dataclass(frozen=True)
class Ctx:
    journal: Callable[[], Journal | None]
    # Checks the workspace, raising `Invalid`, and returns how the run log names it.
    workspace_key: Callable[[str], str]
    # `(feature, workspace path)`; a workspace the app does not know counts as on.
    enabled: Callable[[str, str], bool]
    bus: Bus


@dataclass(frozen=True)
class Plugin:
    name: str
    routes: Callable[[Ctx], Sequence[BaseRoute]]
    scripts: tuple[str, ...] = ()


def _off(data: Data) -> dict[str, list[str]]:
    stored = data.pref(OFF_PREF, {})
    return stored if isinstance(stored, dict) else {}


def ctx_of(service: Service) -> Ctx:
    data = Data(service.config.data_dir)

    def workspace_key(cwd: str) -> str:
        service.ws.check(cwd)
        return Workspaces.key(cwd)

    def enabled(feature: str, workspace: str) -> bool:
        return Workspaces.key(workspace) not in _off(data).get(feature, [])

    return Ctx(service.ws.journal, workspace_key, enabled, service.bus)


def set_enabled(service: Service, known: Sequence[str], feature: str, cwd: str, on: bool) -> None:
    """Turn `feature` on or off for the workspace `cwd`; a feature or workspace not known is `Invalid`."""
    if feature not in known:
        raise Invalid(f"not a feature: {feature}")
    key = ctx_of(service).workspace_key(cwd)
    data = Data(service.config.data_dir)
    off = _off(data)
    keys = [k for k in off.get(feature, []) if k != key]
    if not on:
        keys.append(key)
    off[feature] = keys
    data.set_pref(OFF_PREF, off)
