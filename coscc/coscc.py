"""The app: one shell, a route per screen, one ASGI app, one port, behind the login guard."""

from __future__ import annotations

import reflex as rx

from coscc import screens
from coscc.web import place, ui
from coscc.state import API, StudioState

# The theme lives in `rxconfig.py` (`App(theme=...)` is deprecated).
app = rx.App(api_transformer=API, style=ui.GLOBAL_STYLE)
# Static routes, not `/unit/[unit]`: Reflex builds no page for a dynamic route and serves
# it through the SPA fallback with a 404.
for route in ("/", *(f"/{s}" for s in place.SCREENS[1:]), "/unit", "/idea"):
    app.add_page(screens.index, route=route, title="CoS Studio", on_load=StudioState.arrive)


async def resume_after_update() -> None:
    """Take up every session an update paused, then start each autopilot-on workspace again.

    A Reflex lifespan task, because `api.py`'s lifespan is never run by the real stack.
    """
    await API.state.service.resume_after_update()


app.register_lifespan_task(resume_after_update)


def served():
    """What uvicorn serves: the composed app behind the login guard.

    It wraps `app()` because that position sees every scope, CORS preflight included.
    """
    from coscc.web.auth import Guard
    from coscc.data import Data

    return Guard(app(), Data(API.state.config.data_dir))
