"""Presentation primitives for the page. Python styles, no service dependencies.

Written for `fragmented-product-experience`'s prototype and kept unchanged through `0006`, which is the point:
the look the originator approved is this file, and swapping the data underneath it did
not require touching it.
"""

import reflex as rx

INK = rx.color("gray", 12)
MUTED = rx.color("gray", 11)
LINE = rx.color("gray", 5)
SURFACE = rx.color("gray", 2)
CANVAS = rx.color("gray", 1)
ACCENT = rx.color("iris", 9)


def text(value, **props) -> rx.Component:
    props.setdefault("size", "2")
    props.setdefault("color", MUTED)
    return rx.text(value, **props)


def eyebrow(value) -> rx.Component:
    return rx.text(value, size="1", weight="medium", color=MUTED, letter_spacing="0.12em")


def panel(*children, **props) -> rx.Component:
    props.setdefault("background", SURFACE)
    props.setdefault("border", f"1px solid {LINE}")
    props.setdefault("border_radius", "14px")
    props.setdefault("padding", "22px")
    props.setdefault("width", "100%")
    props.setdefault("min_width", "0")
    return rx.box(*children, **props)


def mark(initials, color="iris", size="40px") -> rx.Component:
    return rx.center(
        rx.text(initials, size="2", weight="bold", letter_spacing="-0.03em"),
        width=size, height=size, flex_shrink="0", border_radius="12px",
        background=rx.color(color, 3), color=rx.color(color, 11),
        border=f"1px solid {rx.color(color, 5)}",
    )


def icon_button(icon: str, label, **props) -> rx.Component:
    props.setdefault("variant", "ghost")
    props.setdefault("color_scheme", "gray")
    return rx.button(rx.icon(icon, size=17), aria_label=label, title=label, **props)


def badge(value, color="gray") -> rx.Component:
    return rx.badge(value, color_scheme=color, variant="soft", radius="full", size="1")


def heading(title, description, *actions) -> rx.Component:
    return rx.flex(
        rx.vstack(
            rx.heading(title, size="7", weight="medium", letter_spacing="-0.045em"),
            text(description, max_width="580px"),
            spacing="2", min_width="0",
        ),
        rx.spacer(),
        rx.flex(*actions, gap="8px", align="center", wrap="wrap"),
        width="100%", gap="16px", align="center", wrap="wrap",
    )


def stat(label, value, hint, icon: str, color="iris") -> rx.Component:
    return panel(
        rx.hstack(
            text(label, weight="medium"),
            rx.spacer(),
            rx.icon(icon, size=16, color=rx.color(color, 10)),
            width="100%",
        ),
        rx.heading(value, size="7", weight="medium", margin_top="14px",
                   letter_spacing="-0.055em"),
        text(hint, size="1", margin_top="5px"),
        padding="20px",
    )


def section_head(title, *actions) -> rx.Component:
    return rx.hstack(
        rx.heading(title, size="3", weight="medium"),
        rx.spacer(), *actions, width="100%", align="center", margin_bottom="18px",
    )


def native_select(*children, **props) -> rx.Component:
    return rx.el.select(
        *children, **props,
        background=SURFACE, color=INK, border=f"1px solid {LINE}",
        border_radius="8px", padding="7px 10px", font_size="12px",
        max_width="100%", cursor="pointer",
    )
