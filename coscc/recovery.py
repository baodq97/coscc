"""The steps the app went down under, ended at the next start (`0092` R5).

A board step's runner writes its `end` record when the step ends. When the app itself ends
first -- a restart, an update, SIGTERM, SIGKILL -- nothing does: the runner deliberately
writes no `end` for an app going down (`0073` C6, C9), the recorder only `abandon()`s, and
the `start` stays "ended, unknown" with the turns it ran nowhere in the run log.

So at the next start, before the server serves and before `0073`'s purge, every `run` that
meets all four of R5's conditions is given the `end` it never got:

- its `start` carries a `pid` (a `start` written before `0092` never does, and is left
  exactly as it is -- R9);
- no `end` names its `run`;
- its `step_runs` row has no `ended_at`;
- that `pid` is not a live process.

The `end` says `failed`, "the app went down while the step ran", `recovered: true`, no
`cost_usd` and `cost_unknown: true`, and the turns the recorder stored. The row in
`step_runs` is then closed at its last stored event, so the watch pane stops reading
`ended-unknown` for a step the run log calls `failed`.

**A live `pid` is never touched**, this process's own included: that is a step still
running, or a second copy of the app on the same data root (`.claude/rules/coscc-events.md`).
A `pid` the system has since given to some other process reads as alive too, and the step
keeps its "ended, unknown" -- the safe way to be wrong. The check is one machine's: a copy
of the app on another machine sharing the data root over a network drive would have its
running steps ended here (`spec.md` C4). On anything but POSIX, `os.kill` ends a process
rather than asking after it, so there nothing is recovered at all.
"""

from __future__ import annotations

import os
from typing import Any

from coscc.data import Data
from coscc.journal import Journal

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
    records = journal.records(row["workspace"], row["unit"], kinds=("start", "end"))
    starts = [r for r in records if r.get("kind") == "start" and r.get("run") == run]
    if any(r.get("kind") == "end" and r.get("run") == run for r in records):
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
        row["workspace"], row["unit"], str(start.get("stage") or row["stage"]), "failed",
        detail=DETAIL, run=run, recovered=True, cost_unknown=True,
        **({"turns": n} if n > 0 else {}),
    )
    data.step_run_close(run, row["last_at"] or row["started_at"], row["lost"])
    return True


def recover(data: Data) -> int:
    """R5. End every run the app went down under; how many were. One run's error does not
    stop the others."""
    recovered = 0
    for row in data.step_runs_open():
        try:
            recovered += int(_recover_one(data, row))
        except Exception:  # noqa: BLE001 - that run keeps "ended, unknown"; the next start tries again
            continue
    return recovered


def recover_on_start(config: Any) -> int:
    """What `coscc/run.py` calls before the purge: a `Data` built from `config`, and nothing
    else of the app."""
    return recover(Data(config.data_dir))
