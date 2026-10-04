"""The app: one shell, a route per screen, one ASGI app, one port, behind the login guard."""

from __future__ import annotations

import asyncio

import reflex as rx

from coscc import plugin, screens, ui
from coscc.auth import Guard
from coscc.data import Data
from coscc.state import StudioState, place
from coscc.state.app import API

# The theme lives in `rxconfig.py` (`App(theme=...)` is deprecated).
app = rx.App(api_transformer=API, style=ui.GLOBAL_STYLE)
# Static routes, not `/unit/[unit]`: Reflex builds no page for a dynamic route and serves
# it through the SPA fallback with a 404.
for route in ("/", *(f"/{s}" for s in place.SCREENS[1:]), "/unit", "/idea", "/feature"):
    app.add_page(screens.index, route=route, title="CoS Studio", on_load=StudioState.arrive)


async def resume_after_update() -> None:
    """Take up every session an update paused, then start each autopilot-on workspace again.

    A Reflex lifespan task, because `api.py`'s lifespan is never run by the real stack.
    """
    await API.state.service.resume.resume_after_update()


app.register_lifespan_task(resume_after_update)


async def warm_boards() -> None:
    """Read every listed workspace's board once, so the first board opened after the start
    finds one held instead of waiting on `git` and `gh`."""
    await API.state.service.warm_boards()


app.register_lifespan_task(warm_boards)


async def create_feature_tables() -> None:
    """Every feature's tables, made when the app starts and never when the page is built."""
    plugin.create_tables(plugin.ctx_of(API.state.service), API.state.tables)


app.register_lifespan_task(create_feature_tables)


async def run_schedules() -> None:
    """Every scheduled feature, asked every `plugin.TICK_SECONDS` while the app runs; the first
    round waits one period, so a start spends nothing at once."""
    while True:
        await asyncio.sleep(plugin.TICK_SECONDS)
        await plugin.tick(API.state.service, API.state.ctx, API.state.plugins)


app.register_lifespan_task(run_schedules)


def served():
    """What uvicorn serves: the composed app behind the login guard.

    It wraps `app()` because that position sees every scope, CORS preflight included.
    """
    return Guard(app(), Data(API.state.config.data_dir))
