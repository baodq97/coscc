"""Which agent name a stage's session is shown under on the board.

`0051_the-board-does-not-show-which-units-have-an-agent-working`, spec R4. The names were
chosen in `0051 spec.md ## Answers, câu 2`; they belong to `0036`, which will replace this
table with the one in Settings. Until then this is the only copy, and `agent_for` is the
only way to read it, so `0036` has exactly one place to change.

A name, not an identity: nothing opens, closes or grants anything because of it.
"""

from __future__ import annotations

# stage -> (glyph, ASCII name).
_AGENTS: dict[str, tuple[str, str]] = {
    "idea": ("ᛜ", "Ingwaz"),
    "intent": ("ᚾ", "Nauthiz"),
    "spec": ("ᚲ", "Kenaz"),
    "plan": ("ᚱ", "Raidho"),
    "impl": ("ᚢ", "Uruz"),
    "pr": ("ᚨ", "Ansuz"),
    "review": ("ᛏ", "Tiwaz"),
    "ship": ("ᛟ", "Othala"),
    "integrate": ("ᚷ", "Gebo"),
}

# `Service.run_step` takes both names for the one stage.
_ALIASES = {"implement": "impl"}


def agent_for(stage: str) -> dict[str, str] | None:
    """`{"glyph", "name"}` for `stage`, or `None` for a stage the table does not know."""
    found = _AGENTS.get(_ALIASES.get(stage, stage))
    if found is None:
        return None
    return {"glyph": found[0], "name": found[1]}
