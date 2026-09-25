"""Where the page is, as an address, and back (`0056`).

A *place* is the screen, the workspace's name, the unit and the unit's tab. `read` turns
the path and query the browser is on into one, word for word; `href` writes one back. Both
only translate: neither knows which workspaces or units exist, and neither corrects
anything — `StudioState.arrive` decides what a place that names nothing becomes.

`ws` carries a workspace's *name*, never its path (`spec.md` R8): the resolved path is not
something an address may leak. `id` and `tab` belong to `/unit` alone and are ignored on
every other route.

Reflex answers a direct GET of `/board` with a 307 to `/board/`, while `rx.redirect`
keeps `/board` (`spike.md ## U1`), so `read` takes both spellings and `href` writes the
one without the slash (`spec.md` R2, C2).
"""

from __future__ import annotations

import dataclasses
from urllib.parse import parse_qs, urlencode

# The screens with a route of their own, in `NAVIGATION`'s order; `state_test.py` pins the
# two together. `unit` is not one of them: it is the Board with a unit's dialog open.
SCREENS = ("overview", "workspaces", "board", "backlog", "sessions", "activity", "cost", "settings")

# What `set_detail_tab` accepts (`spec.md` R3).
TABS = ("overview", "artifacts", "questions", "comments", "timeline")


@dataclasses.dataclass(frozen=True)
class Place:
    screen: str
    ws: str = ""
    unit: str = ""
    tab: str = "overview"


def read(path: str, query: str) -> Place:
    """The place an address names, exactly as written. A `tab` that is not one of `TABS`
    stays as it is, so the caller can tell it was asked for."""
    path = path.rstrip("/") or "/"
    screen = "overview" if path == "/" else path.removeprefix("/")
    params = parse_qs(query or "", keep_blank_values=True)

    def one(key: str) -> str:
        return (params.get(key) or [""])[0]

    if screen != "unit":
        return Place(screen, one("ws"))
    return Place("unit", one("ws"), one("id"), one("tab") or "overview")


def href(place: Place) -> str:
    """The address of a place: no trailing slash, and `ws`, `id`, `tab` in that order, so
    one place has one address and `arrive` can compare two by their text."""
    path = "/" if place.screen == "overview" else f"/{place.screen}"
    pairs = [("ws", place.ws)] if place.ws else []
    if place.screen == "unit":
        if place.unit:
            pairs.append(("id", place.unit))
        if place.tab != "overview":
            pairs.append(("tab", place.tab))
    return f"{path}?{urlencode(pairs)}" if pairs else path
