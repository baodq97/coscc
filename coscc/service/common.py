"""What more than one part of `Service` uses, and what code outside it imports: the errors a
request is refused with."""

from __future__ import annotations

from coscc.kernel import Invalid
from coscc.runner.queue import Refused
from coscc.units import states


# The stage names, in stage order, from the state set the loop is checked against: a copy
# kept here by hand once left `spike` out, and the unit's detail could not open `spike.md`.
STAGE_FILES = states.default().stage_names


class Updating(Refused):
    """Refused because the app is in the seconds before it restarts. A 503."""

    def __init__(self, said: str) -> None:
        super().__init__(said, ("updating",))


class NotUpdatable(Invalid):
    """This install is not the shape an update can be applied to. A 409."""
