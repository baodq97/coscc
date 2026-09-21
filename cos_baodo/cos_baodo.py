"""The Reflex app. Spike scope only: one page, and the JSON surface mounted beside it.

`plan.md` step 1 asks four questions of this file and nothing more. The real page arrives
at step 9; the service layer it will call arrives at step 4.
"""

from __future__ import annotations

import reflex as rx

from cos_baodo.api import build


def index() -> rx.Component:
    return rx.text("cos-baodo — spike")


app = rx.App(api_transformer=build())
app.add_page(index)
