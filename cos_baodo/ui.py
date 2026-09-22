"""The look of the app: one theme, one type scale, one set of building blocks.

`spec.md` R22–R25 are the floor this file exists to hold. Before it, `rx.App` was called
with no `theme=` and no `style=` at all, so every screen inherited whatever the component
library defaulted to — light only, no accent, nothing responsive. That is not a style
choice anybody made; it is the absence of one, and it is most of why the page read as
unfinished.

Three rules, each answering a requirement:

- **R22.** The theme is declared here and nowhere else. A component that wants a colour
  asks for a *role* (`rx.color("gray", 6)`), never a hex value, so the same code is
  correct in both appearances.
- **R23/R24.** Light and dark are the same layout, and the three widths in `BREAKPOINTS`
  are the same layout too. Nothing is hidden at a small width that carries information —
  it reflows or it scrolls.
- **R25.** Body text sits on `gray 12` over `gray 1`, the two ends of the scale, which is
  the pairing the palette is built to keep legible in both appearances. The *measurement*
  is `scripts/verify_0004.py`; this file only makes it likely.

Everything here is presentation. No handler, no service call, no decision.
"""

from __future__ import annotations

import reflex as rx

# The three widths `spec.md` R24 names: phone, tablet, laptop. Chosen, not measured — they
# exist to turn "responsive" into something a browser can fail.
BREAKPOINTS = {"phone": "390px", "tablet": "768px", "laptop": "1280px"}

# One scale, used everywhere. Reflex's spacing props take these as strings.
SPACE = {"tight": "2", "normal": "4", "loose": "6"}

THEME = rx.theme(
    # `inherit` hands the decision to the colour mode, which Reflex keeps in the browser —
    # that is what makes R23's "survives a reload" true without this file storing anything.
    appearance="inherit",
    has_background=True,
    accent_color="iris",
    gray_color="slate",
    panel_background="translucent",
    radius="large",
    scaling="100%",
)

# Applied once at the app, rather than repeated as props on every component. The second
# form is how a page ends up with six slightly different body sizes.
GLOBAL_STYLE = {
    "font_family": (
        "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif"
    ),
    "font_size": "15px",
    "line_height": "1.55",
    "color": rx.color("gray", 12),
    "background": rx.color("gray", 1),
    "::selection": {"background": rx.color("iris", 5)},
    rx.code: {
        "font_family": "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        "font_size": "0.85em",
    },
    rx.heading: {"letter_spacing": "-0.01em"},
}
