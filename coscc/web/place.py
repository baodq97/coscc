"""Where the page is, as an address, and back.

A *place* is the screen, the workspace's name, the unit and the unit's tab. `read` and `href`
only translate: neither knows which workspaces or units exist, and `StudioState.arrive`
decides what a place that names nothing becomes.

`ws` carries a workspace's *name*, never its path. `id` and `tab` belong to `/unit` alone.
Reflex answers a direct GET of `/board` with a 307 to `/board/`, so `read` takes both
spellings and `href` writes the one without the slash.
"""

from __future__ import annotations

import dataclasses
from urllib.parse import parse_qs, urlencode

# The screens with a route of their own, in `NAVIGATION`'s order. `unit` is the Board with a
# unit's dialog open.
SCREENS = ("overview", "workspaces", "board", "backlog", "sessions", "activity", "cost", "settings")

TABS = ("overview", "artifacts", "questions", "comments", "timeline")


@dataclasses.dataclass(frozen=True)
class Place:
    screen: str
    ws: str = ""
    unit: str = ""
    tab: str = "overview"
    # `/idea?ws=<home>&id=NNNN_<slug>`: one idea, like `unit` not in `SCREENS`.
    idea: str = ""


def read(path: str, query: str) -> Place:
    """The place an address names, exactly as written; an unknown `tab` stays as it is."""
    path = path.rstrip("/") or "/"
    screen = "overview" if path == "/" else path.removeprefix("/")
    params = parse_qs(query or "", keep_blank_values=True)

    def one(key: str) -> str:
        return (params.get(key) or [""])[0]

    if screen == "idea":
        return Place("idea", one("ws"), idea=one("id"))
    if screen != "unit":
        return Place(screen, one("ws"))
    return Place("unit", one("ws"), one("id"), one("tab") or "overview")


def href(place: Place) -> str:
    """The address of a place: one place has one address, so `arrive` can compare two by text."""
    path = "/" if place.screen == "overview" else f"/{place.screen}"
    pairs = [("ws", place.ws)] if place.ws else []
    if place.screen == "idea" and place.idea:
        pairs.append(("id", place.idea))
    if place.screen == "unit":
        if place.unit:
            pairs.append(("id", place.unit))
        if place.tab != "overview":
            pairs.append(("tab", place.tab))
    return f"{path}?{urlencode(pairs)}" if pairs else path
