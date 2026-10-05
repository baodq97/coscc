"""Every time a person had to step in on a workspace, from the tables that saw it.

Each table's owner reads its own rows (`Journal`, `UnitMeta`, `Attempts`); this only merges
them, oldest first, cuts each `detail`, and counts a rerun once. One rerun writes two rows:
`Steps.run_step` opens its attempt with `rerun`, and the `start` the step writes once it
launches carries `rerun` too. So a `start` rerun with an `attempts` rerun of the same unit and
stage opened at most `SAME_RERUN` before it is that attempt, and is dropped. A tool a session
was denied (`end.denials`) is no intervention.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from coscc.store.journal import Intervention, Journal
from coscc.service.attempts import Attempts
from coscc.units.meta import UnitMeta

# The kinds, as the scan's prompt and Backlog name them.
KINDS = ("refused", "ci-red", "rerun", "review-round", "impl-draft", "integrate")
DETAIL_MAX = 300
# Chosen, not measured: a rerun's attempt is opened at the click and its `start` written once
# the step launches, which is seconds later unless the step waited in the queue.
SAME_RERUN = timedelta(seconds=60)


def _time(at: str) -> datetime | None:
    try:
        return datetime.fromisoformat(at)
    except ValueError:
        return None


def _earlier(after: str, by: timedelta) -> str:
    """`after` moved back `by`, in the stamp every table writes; `""` stays `""`."""
    t = _time(after) if after else None
    return (t - by).isoformat(timespec="seconds") if t is not None else after


def _counted_twice(row: Intervention, attempts: list[Intervention]) -> bool:
    """Whether `row`, a `start` rerun, is one of `attempts`' reruns written again."""
    at = _time(row.at)
    if at is None:
        return False
    for a in attempts:
        opened = _time(a.at)
        if (a.unit, a.stage) == (row.unit, row.stage) and opened is not None:
            if timedelta(0) <= at - opened <= SAME_RERUN:
                return True
    return False


def interventions(
    journal: Journal | None, meta: UnitMeta, attempts: Attempts, key: str, after: str, limit: int
) -> list[Intervention]:
    """The interventions of the workspace `key` past `after` (`""`: from the first), oldest
    first, at most `limit`, each `detail` one line of at most `DETAIL_MAX` characters. With no
    run log there is none."""
    if journal is None:
        return []
    # Read from a little earlier, so a rerun whose attempt is just before `after` and whose
    # `start` is just after it is still counted once.
    held = attempts.interventions(key, _earlier(after, SAME_RERUN), limit + limit)
    reruns = [a for a in held if a.kind == "rerun"]
    starts = journal.interventions(key, after, limit)
    rows = [a for a in held if a.at > after]
    rows += [s for s in starts if s.kind != "rerun" or not _counted_twice(s, reruns)]
    rows += meta.interventions(key, after, limit)
    rows.sort(key=lambda r: (r.at, r.id))
    return [r._replace(detail=" ".join(r.detail.split())[:DETAIL_MAX]) for r in rows[:limit]]
