"""The look of the app: one theme, one type scale, one set of building blocks.

The theme is declared here and nowhere else. A component asks for a colour *role*
(`rx.color("gray", 6)`), never a hex value, so the same code is correct in light and dark.
Nothing that carries information is hidden at a small width; it reflows or scrolls.
Everything here is presentation: no handler, no service call.
"""

from __future__ import annotations

import reflex as rx

THEME = rx.theme(
    # `inherit` hands the decision to the colour mode, which Reflex keeps in the browser.
    appearance="inherit",
    has_background=True,
    accent_color="iris",
    gray_color="slate",
    panel_background="translucent",
    radius="large",
    scaling="100%",
)

# Applied once at the app rather than as props on every component.
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
