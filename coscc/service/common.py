"""What more than one part of `Service` uses, and what code outside it imports: the errors a
request is refused with, what holds a unit, and the step's working directory."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coscc.kernel import Invalid
from coscc.github import integrate
from coscc.store.journal import BadRecord, Journal
from coscc.units import states
from coscc.units.guards import REASONS
from coscc.store.db import Busy

if TYPE_CHECKING:
    from coscc.service.attempts import Attempts


# The stage names, in stage order, from the state set the loop is checked against: a copy
# kept here by hand once left `spike` out, and the unit's detail could not open `spike.md`.
STAGE_FILES = states.default().stage_names


class Refused(Invalid):
    """The gate refused a step. `reasons` are its codes (`guards.REASONS`), which the
    autopilot reads instead of the words."""

    def __init__(self, said: str, reasons: tuple[str, ...] = ()) -> None:
        super().__init__(said)
        for code in reasons:
            if code not in REASONS:
                raise ValueError(f"no reason code {code!r}")
        self.reasons = tuple(reasons)


class Holds:
    """What holds each unit now: its unfinished attempt, in `cos.db` (`attempts`), the only
    thing a refusal of a busy unit reads. `finishing`: a step's `after_end`, run in its
    attempt's `ending`, by attempt id; an Apply's settle and `shutdown` wait for it.
    """

    def __init__(self, attempts: Attempts) -> None:
        self.attempts = attempts
        self.finishing: dict[int, tuple[dict[str, Any], asyncio.Task]] = {}

    def busy(self, key: str, unit: str) -> str:
        """What holds this unit, in the one sentence every refusal carries, or `""`."""
        return self.attempts.busy(key, unit)


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


async def _open_prs(cwd: str) -> list[dict[str, Any]] | str:
    try:
        return await integrate.open_prs(str(Path(cwd).expanduser().resolve()))
    except integrate.IntegrateError as e:
        return str(e)


def open_prs_once(cwd: str):
    """`integrate.open_prs` for `cwd`, asked at most once however often it is awaited;
    `gh`'s error as a string."""
    held: list[Any] = []

    async def prs() -> list[dict[str, Any]] | str:
        if not held:
            held.append(await _open_prs(cwd))
        return held[0]

    return prs


class Updating(Refused):
    """Refused because the app is in the seconds before it restarts. A 503."""

    def __init__(self, said: str) -> None:
        super().__init__(said, ("updating",))


class NotUpdatable(Invalid):
    """This install is not the shape an update can be applied to. A 409."""


def step_cwd(stage: str, work: str, directory: Path, spike_dir: str | None = None) -> str:
    """Where a step's session runs: the unit's worktree, except for `ship` and `spike`.

    `spike` runs in `spike_dir`, a throwaway directory under the data root, so its probe code
    never lands in the worktree whose branch it would ride.

    `ship` runs `gh pr merge --squash --delete-branch`, which inside a worktree merges and then
    fails (gh tries to switch the worktree to `main`, git refuses, exit 1, branches left
    behind). Run from a non-git directory with the PR URL it merges and deletes the remote
    branch; the unit's store directory is such a directory. `worktrees.remove_if_finished`
    removes the worktree and local branch once GitHub says `MERGED`. The gates still read `work`.
    """
    if stage == "spike" and spike_dir:
        return spike_dir
    return str(directory) if stage == "ship" else work


# The three results a `### Outcome` block may carry, as a person types them, and the word
# the loop's `parseOutcome` reads each one as.
OUTCOME_RESULTS = {"đạt": "met", "trượt": "missed", "không đo được": "unmeasurable"}
