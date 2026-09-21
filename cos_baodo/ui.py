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


def mono(text, **props) -> rx.Component:
    """A path, an id, a number — anything where the characters matter one by one."""
    return rx.code(text, variant="soft", color_scheme="gray", **props)


def muted(text, **props) -> rx.Component:
    """Secondary text. `gray 11` is the lowest step that still clears AA on `gray 1`."""
    props.setdefault("size", "1")
    props.setdefault("color", rx.color("gray", 11))
    return rx.text(text, **props)


def card(*children, **props) -> rx.Component:
    """A bordered surface. Border rather than shadow: it reads the same in both modes.

    Defaults are `setdefault`, not fixed, so a caller can nest one card inside another and
    change the surface without the two definitions fighting.
    """
    props.setdefault("background", rx.color("gray", 2))
    props.setdefault("border", f"1px solid {rx.color('gray', 6)}")
    props.setdefault("border_radius", "12px")
    props.setdefault("padding", rx.breakpoints(initial="12px", sm="18px"))
    props.setdefault("width", "100%")
    return rx.box(*children, **props)


def section(title: str, *children, actions: rx.Component | None = None, **props) -> rx.Component:
    """A titled block. The title and its actions stay on one line until they cannot."""
    head = rx.flex(
        rx.heading(title, size="3", weight="bold"),
        rx.spacer(),
        actions if actions is not None else rx.fragment(),
        width="100%",
        align="center",
        gap="3",
        wrap="wrap",
    )
    return rx.vstack(head, *children, width="100%", spacing=SPACE["normal"], **props)


def top_bar(*right: rx.Component) -> rx.Component:
    """Sticky, quiet, and the one place the colour mode can be changed.

    `position="sticky"` rather than `fixed` so it never covers content on a short screen.
    """
    return rx.box(
        rx.flex(
            rx.hstack(
                rx.box(
                    width="10px",
                    height="10px",
                    border_radius="3px",
                    background=rx.color("iris", 9),
                ),
                rx.heading("cos-baodo", size="4", weight="bold"),
                spacing="3",
                align="center",
            ),
            rx.spacer(),
            rx.hstack(
                *right,
                # `id` is a hook for `scripts/verify_0004.py`, which has to find this exact
                # control. Without it the proof would have to click buttons until the mode
                # changed, and the buttons it would try on the way include `remove`.
                rx.box(rx.color_mode.button(size="2"), id="color-mode"),
                spacing="3",
                align="center",
            ),
            width="100%",
            align="center",
            gap="3",
        ),
        position="sticky",
        top="0",
        z_index="10",
        width="100%",
        padding=rx.breakpoints(initial="10px 14px", sm="12px 22px"),
        background=rx.color("gray", 2),
        border_bottom=f"1px solid {rx.color('gray', 6)}",
        backdrop_filter="saturate(180%) blur(8px)",
    )


def page(*children, bar_right: list[rx.Component] | None = None, **props) -> rx.Component:
    """The frame every screen sits in.

    One column, capped width, padding that shrinks on a phone. Nothing here is hidden at a
    narrow width — `spec.md` R24 is about reflowing, not about a second mobile design.
    """
    return rx.box(
        top_bar(*(bar_right or [])),
        rx.box(
            rx.vstack(*children, spacing=SPACE["loose"], width="100%", **props),
            width="100%",
            max_width="1180px",
            margin="0 auto",
            padding=rx.breakpoints(initial="16px 14px 56px", sm="24px 22px 64px"),
        ),
        min_height="100vh",
        width="100%",
        background=rx.color("gray", 1),
    )


def scroll_x(*children) -> rx.Component:
    """Horizontal overflow belongs to the thing that is too wide, never to the page.

    `spec.md` R24 fails on a page that scrolls sideways. A table that cannot shrink is a
    real case, so it gets its own scroll container and the document keeps its width.
    """
    return rx.box(*children, width="100%", overflow_x="auto")
