"""Reflex configuration. Flat layout: the package sits beside this file.

`spec.md` R1 fixes the name; Reflex resolves `app_name` to a directory of the same name at
the repo root, which is why there is no `src/`.

Two settings here are not preferences, they are defects found by running the thing:

`backend_host` — Reflex 0.9.11 defaults it to `0.0.0.0`. Verified on 2026-09-21 by reading
`Config.__dataclass_fields__["backend_host"].default`, and by `ss -ltn` showing
`0.0.0.0:8000` before this line existed. `0001` defaulted the other way
(`cos_baodo/config.py:66`), so adopting Reflex silently reverses that posture.

`api_url` — the compiled frontend **bakes in** the address it will open its `/_event`
WebSocket against, and Reflex defaults it to `http://localhost:8000`. Serving the app on
any other port therefore produced a page that rendered and then sat there showing
"Connection Error", with a working API behind it. Found on 2026-09-21 by driving the page
with a browser; no HTTP-level proof could see it, which is `spec.md` C11 exactly.

A relative `api_url` would be the right answer and Reflex rejects it: `api_url="/"` fails
the production build with `TypeError: Invalid URL`. So the address is read from the same
config the app serves itself on, and `run.py` refuses to start if the build it finds was
made for a different port.

The theme lives here rather than on `rx.App`. `0005` R22 requires it to be declared
explicitly, and 0.9.11 answers `App(theme=...)` with a deprecation warning pointing at
`RadixThemesPlugin` and saying it goes away at 1.0 — measured on 2026-09-21 by building
with it. The published guides still show the `rx.App` form, so the installed package is
what this follows.
"""

import reflex as rx

from cos_baodo.config import from_env
from cos_baodo.ui import THEME

_c = from_env()

config = rx.Config(
    app_name="cos_baodo",
    backend_host=_c.host,
    api_url=f"http://{_c.host}:{_c.port}",
    plugins=[rx.plugins.RadixThemesPlugin(theme=THEME)],
    # The framework's badge sits fixed in the corner of every screen and overlaps content
    # on a short viewport. This app is one person's loopback tool, not a showcase.
    show_built_with_reflex=False,
)
