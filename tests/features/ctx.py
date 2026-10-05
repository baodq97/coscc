"""A `Ctx` for a test: the handles it names, and for the rest an object that raises when touched."""

from __future__ import annotations

from typing import Any

from coscc.bus import Bus
from coscc.kernel import Ctx


class Unused:
    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, attr: str) -> Any:
        raise AssertionError(f"this test gave no `{self.name}` handle, and touched .{attr}")


def ctx_for(**given: Any) -> Ctx:
    """`ctx_for(store=data, runs=Runs(...))`: `units`, `runs`, `agents`, `store`, `bus`, `settings`."""
    handles = {n: given.pop(n, None) or Unused(n) for n in ("units", "runs", "agents", "store")}
    handles["bus"] = given.pop("bus", None) or Bus()
    handles["settings"] = given.pop("settings", None) or Unused("settings")
    if given:
        raise TypeError(f"not a handle of Ctx: {', '.join(given)}")
    return Ctx(**handles)
