"""What more than one part of `Service` uses, and what code outside it imports: the errors a
request is refused with and the setting log."""

from __future__ import annotations

from typing import Any

from coscc.kernel import Invalid
from coscc.runner.queue import Refused
from coscc.store.journal import BadRecord, Journal
from coscc.units import states
from coscc.store.db import Busy


# The stage names, in stage order, from the state set the loop is checked against: a copy
# kept here by hand once left `spike` out, and the unit's detail could not open `spike.md`.
STAGE_FILES = states.default().stage_names


def log_setting(journal: Journal | None, key: str, old: Any, new: Any) -> None:
    """One `setting` record of a changed setting, its old and new value."""
    if journal is None:
        return
    try:
        journal.append(
            {
                "kind": "setting",
                "workspace": "",
                "unit": "",
                "stage": "",
                "name": key,
                "old": old,
                "new": new,
            }
        )
    except (BadRecord, Busy) as e:
        raise Invalid(f"the setting was saved but not logged: {e}") from e


class Updating(Refused):
    """Refused because the app is in the seconds before it restarts. A 503."""

    def __init__(self, said: str) -> None:
        super().__init__(said, ("updating",))


class NotUpdatable(Invalid):
    """This install is not the shape an update can be applied to. A 409."""


# The three results a `### Outcome` block may carry, as a person types them, and the word
# the loop's `parseOutcome` reads each one as.
OUTCOME_RESULTS = {"đạt": "met", "trượt": "missed", "không đo được": "unmeasurable"}
