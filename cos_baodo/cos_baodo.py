"""The app: one page, one ASGI app, one loopback port.

This file once held a second page — the one the earlier units built — and
a prototype sat beside it at `/prototype`. `spec.md` R19 replaced both with
one: the prototype's shape, on the real service. The old page's components and state are
gone rather than kept around, because two front ends are two things to fix every time and
only one of them ever gets fixed (`spec.md` C3 records what that costs).

What is left here is registration. The page is `cos_baodo/screens.py`, its state is
`cos_baodo/state.py`, and the business logic is where it always was, in
`cos_baodo/service.py`.

The FastAPI app mounted here is the *same object* `state.py` reads its service from. Two
instances would mean two `Sessions` registries, and knob 4 ("resume only what this app
created") would start answering differently depending on which door you came through.
"""

from __future__ import annotations

import reflex as rx

from cos_baodo import screens, ui
from cos_baodo.state import API

# The theme lives in `rxconfig.py` through `RadixThemesPlugin`, because 0.9.11 deprecates
# `App(theme=...)` and removes it at 1.0. The global style still belongs here.
app = rx.App(api_transformer=API, style=ui.GLOBAL_STYLE)
app.add_page(screens.index, title="COS Studio")
