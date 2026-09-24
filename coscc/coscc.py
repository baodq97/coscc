"""The app: one shell, seven routes, one ASGI app, one port.

It was "one loopback port" until `0011` made `0.0.0.0` the default. Since `0070` what
uvicorn serves is `served()` below: this app behind `coscc/auth.py`, so every request —
the page, its socket and `/api` alike — needs the master password or a live session
first.

This file once held a second page — the one the earlier units built — and
a prototype sat beside it at `/prototype`. `spec.md` R19 replaced both with
one: the prototype's shape, on the real service. The old page's components and state are
gone rather than kept around, because two front ends are two things to fix every time and
only one of them ever gets fixed (`spec.md` C3 records what that costs).

What is left here is registration. The page is `coscc/screens.py`, its state is
`coscc/state.py`, and the business logic is where it always was, in
`coscc/service.py`.

The FastAPI app mounted here is the *same object* `state.py` reads its service from. Two
instances would mean two `Sessions` registries, and knob 4 ("resume only what this app
created") would start answering differently depending on which door you came through.
"""

from __future__ import annotations

import reflex as rx

from coscc import place, screens, ui
from coscc.state import API, StudioState

# The theme lives in `rxconfig.py` through `RadixThemesPlugin`, because 0.9.11 deprecates
# `App(theme=...)` and removes it at 1.0. The global style still belongs here.
app = rx.App(api_transformer=API, style=ui.GLOBAL_STYLE)
# `0056`: one shell under seven static routes, each arriving through `StudioState.arrive`.
# Static, not `/unit/[unit]`: 0.9.12 builds no page for a dynamic route and serves it
# through the SPA fallback with a 404 (`.cos/0056_*/spike.md ## U1`).
for route in ("/", *(f"/{s}" for s in place.SCREENS[1:]), "/unit"):
    app.add_page(screens.index, route=route, title="CoS Studio", on_load=StudioState.arrive)


def served():
    """What uvicorn serves: the composed app, behind the login guard (`0070`).

    It wraps what `app()` returns rather than joining `api_transformer`, because that is
    the one position `.cos/0070_*/spike.md ## U1` measured to see every scope — CORS
    preflight included, which Reflex's own middleware answers before any transformer.
    """
    from coscc.auth import Guard
    from coscc.data import Data

    return Guard(app(), Data(API.state.config.data_dir))
