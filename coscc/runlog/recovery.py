"""Ends the steps the app went down under, at the next start.

When the app ends first (restart, update, SIGTERM, SIGKILL) no `end` is written for a running
step, so its `start` stays "ended, unknown". At the next start, before the server serves and
before the purge, every `run` that meets all of these is given the `end` it never got:

- its `start` carries a `pid`;
- no `end` names its `run`;
- its `step_runs` row has no `ended_at`;
- that `pid` is not a live process.

The `end` says `failed`, `recovered: true`, `cost_unknown: true` and the stored turns. The
`step_runs` row is closed at its last stored event.

A live `pid` is never touched, this process's own included: it is a running step or a second
copy of the app on the same data root. A `pid` reused by another process reads as alive too
(the safe way to be wrong). The check is one machine's. Off POSIX, `os.kill` ends a process
rather than asking, so nothing is recovered.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from coscc.data import Data
from coscc.runlog.journal import Journal

log = logging.getLogger(__name__)

DETAIL = "the app went down while the step ran"


def _alive(pid: int) -> bool:
    """True unless the system says no such process exists. Anything uncertain is alive."""
    if pid == os.getpid() or os.name != "posix":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # `PermissionError` included: someone's process, so a live one
        return True
    return True


def _recover_one(data: Data, row: dict[str, Any]) -> bool:
    run = str(row["run"])
    journal = Journal(row["root"], data)
    records = journal.records(row["workspace"], row["unit"], kinds=("start", "end", "suspend"))
    starts = [r for r in records if r.get("kind") == "start" and r.get("run") == run]
    if any(r.get("kind") == "end" and r.get("run") == run for r in records):
        return False
    if any(
        r.get("kind") == "suspend" and (r.get("owner") or {}).get("run") == run for r in records
    ):
        # An update paused it, and its step goes on under a new `run`: that one writes the `end`.
        # Only this run's own row is closed.
        data.step_run_close(run, row["last_at"] or row["started_at"], row["lost"])
        return False
    if not starts or not isinstance(starts[-1].get("pid"), int):
        return False
    start = starts[-1]
    if _alive(start["pid"]):
        return False
    n = data.step_turns(run)
    # The `end` first: if closing the row fails, the run log is still right and only the
    # watch pane keeps saying `ended-unknown`.
    journal.finished(
        row["workspace"],
        row["unit"],
        str(start.get("stage") or row["stage"]),
        "failed",
        detail=DETAIL,
        run=run,
        recovered=True,
        cost_unknown=True,
        **({"turns": n} if n > 0 else {}),
    )
    data.step_run_close(run, row["last_at"] or row["started_at"], row["lost"])
    return True


def recover(data: Data) -> int:
    """End every run the app went down under; returns how many. One run's error does not stop the others."""
    recovered = 0
    for row in data.step_runs_open():
        try:
            recovered += int(_recover_one(data, row))
        except Exception:
            # That run keeps "ended, unknown"; the next start tries again.
            log.exception("a step the app went down under was not ended")
            continue
    return recovered


def recover_on_start(config: Any) -> int:
    """What `coscc/run.py` calls before the purge: a `Data` built from `config`, nothing else."""
    return recover(Data(config.data_dir))
