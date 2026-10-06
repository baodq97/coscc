"""A `Ctx` for a test: the handles it names, and for the rest an object that raises when touched."""

from __future__ import annotations

from typing import Any

from coscc.bus import Bus
from coscc.kernel import Asked, Ctx


class Unused:
    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, attr: str) -> Any:
        raise AssertionError(f"this test gave no `{self.name}` handle, and touched .{attr}")

    def __call__(self, *args: Any) -> Any:
        raise AssertionError(f"this test gave no `{self.name}` handle, and called it")


def ctx_for(**given: Any) -> Ctx:
    """`ctx_for(store=data, runs=Runs(...))`: `units`, `runs`, `agents`, `store`, `bus`, `settings`,
    `refuse_updating`, `asks`, `required_checks`."""
    handles = {n: given.pop(n, None) or Unused(n) for n in ("units", "runs", "agents", "store")}
    handles["bus"] = given.pop("bus", None) or Bus()
    handles["settings"] = given.pop("settings", None) or Unused("settings")
    handles["refuse_updating"] = given.pop("refuse_updating", None) or Unused("refuse_updating")
    handles["asks"] = given.pop("asks", None) or Asked()
    handles["required_checks"] = given.pop("required_checks", None) or Unused("required_checks")
    if given:
        raise TypeError(f"not a handle of Ctx: {', '.join(given)}")
    return Ctx(**handles)


def rows_without_feature_tools() -> Any:
    """`pack.rows` with every feature's catalog name taken out of the built-in rows, as a patch:
    an app built with a list of features that leaves one out would stop, since a row naming a tool
    no catalog holds stops the build."""
    from unittest import mock

    from coscc.agent import pack
    from coscc.kernel import BUILTINS

    names = {t.name for t in BUILTINS}
    rows = pack.rows()
    trimmed = {
        k: {
            **r,
            "builtin": {
                **r["builtin"],
                "tools": {t: p for t, p in r["builtin"].get("tools", {}).items() if t in names},
            },
        }
        for k, r in rows.items()
    }
    return mock.patch.object(pack, "rows", lambda: trimmed)
