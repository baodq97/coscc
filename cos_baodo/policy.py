"""What a step is allowed to do, keyed on the stage and the mode it runs in.

This is not a fifth knob. `Config` keeps meaning one thing — the app's default, which
`cos_baodo/config.py:44` states as *chat only, no tools at all* — and this table says what
a **board step** may do instead. `0007` exists because that sentence in `config.py` was not
true; making `Config` answer for two different things as well is how it would stop being
true again.

Three properties, each deliberate:

- **Deny by default.** A pair this table does not name gets `Grant()`, which is no tools,
  one turn and no budget. A stage invented tomorrow is therefore locked, not open.
- **Pure.** Nothing here reads the environment, the store, or a request. There is no path
  from HTTP to these values, the same way there is none to `COS_WORKING_DIR`.
- **Mode matters.** `manual` never carries tools for any stage. Choosing `autonomous` is
  the act that grants them, and it is recorded in the journal when it happens.

`0007`'s measurement is why `Grant.tools` is not the whole enforcement. A list handed to
the SDK covers the built-in set and nothing else — eleven MCP tools walked past `tools=[]`
on this machine. So the grant also carries what `Runner` must refuse at the moment of use,
and `can_use_tool` is where that happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Stages whose artifact is prose. The app writes these from the text the session returns,
# so the session itself needs no ability to write at all — see `plan.md` Risk 1 for why the
# spec's design section is wrong about this, and why it is recorded there rather than
# quietly fixed here.
PROSE_STAGES = ("idea", "intent", "spec", "plan", "review", "ship")


@dataclass(frozen=True)
class Grant:
    """What one step may do. The default is the locked position."""

    tools: tuple[str, ...] = ()
    # Commands the step may run, matched on the first word of the command line. Empty
    # means none, which is the only safe default for a field like this.
    commands: tuple[str, ...] = ()
    # `0008` R11. Chosen, not measured: they exist to turn a loop that will not end into a
    # named failure, not to describe what a step ought to cost.
    max_turns: int = 1
    max_budget_usd: float = 0.0
    # Whether the app writes the artifact from the reply (prose stages) or the session
    # writes it itself (stages that touch code).
    app_writes_artifact: bool = True

    @property
    def opens_anything(self) -> bool:
        return bool(self.tools or self.commands)


# Only pairs that appear here get anything. `impl` and `pr` are filled in by steps 9 and 10
# of `plan.md`; until then every stage in every mode is chat with no tools, which is what
# `0007` promises and what `scripts/verify_0008.py` will check.
GRANTS: dict[tuple[str, str], Grant] = {}


def grant_for(stage: str, mode: str) -> Grant:
    """The grant for one step. Unknown pairs are locked, not open."""
    return GRANTS.get((stage, mode), Grant())


def is_prose_stage(stage: str) -> bool:
    return stage in PROSE_STAGES
