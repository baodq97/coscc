"""Every step, integration, hold, review round and estimate, from the click to its end: one
row of `attempts` and its moves in `cos.db`, the one place that says what holds a unit.

What an attempt is now is its last move. It changes only through `move` (and `open`, which
writes the first), which refuses a move its machine's table lacks, writes the row in one
transaction and, once that committed, publishes exactly one `<machine>.<state>` on the bus.
A Stop is the `stop_asked_at` column, recorded through `ask_stop`, which publishes
`<machine>.stop-asked`; on the board an unfinished attempt with one is `stopping`.

The scheduler is here too: one per process, woken by `*.queued`, `*.ended` and `*.refused`,
it counts the slots held by reading the rows and moves the oldest queued attempt of each kind
of slot on, then calls the launcher its machine registered. Nothing else launches. While an
update is under way, and once the process goes down, it moves nothing on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypedDict, cast

from coscc.bus import NAMES, Bus, Event, Name
from coscc.kernel import Invalid
from coscc.store.db import Data, now
from coscc.store.journal import Intervention
from coscc.units.guards import REASONS

log = logging.getLogger(__name__)


class Refused(Invalid):
    """The gate refused a step. `reasons` are its codes (`guards.REASONS`), which the
    autopilot reads instead of the words."""

    def __init__(self, said: str, reasons: tuple[str, ...] = ()) -> None:
        super().__init__(said)
        for code in reasons:
            if code not in REASONS:
                raise ValueError(f"no reason code {code!r}")
        self.reasons = tuple(reasons)


class Updating(Refused):
    """Refused because the app is in the seconds before it restarts. A 503."""

    def __init__(self, said: str) -> None:
        super().__init__(said, ("updating",))


# Each machine's moves: from a state, the states it may go to. `ended` and `refused` are the
# ends; `ended` carries an outcome (`done`, `failed`, `stopped`, `stop_late`, `interrupted`, ...),
# `refused` a reason code.
_SHORT = {"queued": {"running", "ended", "refused"}, "running": {"ended"}}
MACHINES: dict[str, dict[str, set[str]]] = {
    "step": {
        "queued": {"preparing", "ended", "refused"},
        "preparing": {"running", "ended", "refused"},
        "running": {"ending", "ended"},
        "ending": {"ended"},
    },
    "integration": {
        "queued": {"running", "ended", "refused"},
        "running": {"ending", "ended", "refused"},
        "ending": {"ended"},
    },
    # Short holds: no slot, `queued` -> `running` -> `ended` at once.
    "hold": _SHORT,
    "rounds": _SHORT,
    "estimate": _SHORT,
}


def add_session(kind: str) -> None:
    """A feature's paid session (`kernel.Session`, run through `kernel.Ctx.session`): a short
    hold named by its grant, added when the app is built."""
    if MACHINES.get(kind, _SHORT) is not _SHORT:
        raise ValueError(f"the machine {kind!r} is taken")
    MACHINES[kind] = _SHORT


ENDS = ("ended", "refused")
# The kind of slot each machine waits for, and where the scheduler sends one that gets it.
SLOTS = {"step": "agent", "integration": "heavy"}
FIRST = {"step": "preparing", "integration": "running"}
# The states that hold a slot.
HOLDING = ("preparing", "running", "ending")
# What `open` may write first: `running` only for an attempt Resume takes up after an update.
ENTRIES = ("queued", "running")
# Machines a Stop is recorded on.
STOPPABLE = ("step", "integration")


class Attempt(TypedDict):
    """One attempt as read: its row, and what its last and first moves say."""

    id: int
    machine: str
    workspace: str
    unit: str
    stage: str
    slot: str
    started_by: str
    rerun: int
    note: str
    note_by: str
    stop_asked_at: str | None
    stop_asked_by: str | None
    run: str
    road: str
    state: str
    outcome: str
    at: str
    since: str


class Move(TypedDict):
    attempt: int
    seq: int
    moved_to: str
    outcome: str
    at: str


class Illegal(ValueError):
    """A move the machine's table does not have, or of an attempt that is not there."""


_ROW = """
SELECT a.*, m.moved_to AS state, m.outcome AS outcome, m.at AS at, f.at AS since
FROM attempts a
JOIN attempt_moves m ON m.attempt = a.id
    AND m.seq = (SELECT MAX(seq) FROM attempt_moves WHERE attempt = a.id)
JOIN attempt_moves f ON f.attempt = a.id AND f.seq = 1
"""


def describe(unit: str, row: Attempt) -> str:
    """The one sentence every refusal of a busy unit carries: what holds it, and since when."""
    t, machine, state, stage = row["since"], row["machine"], row["state"], row["stage"]
    stopping = "; a stop was asked" if row.get("stop_asked_at") else ""
    if machine == "integration":
        if state == "queued":
            return (
                f"{unit} is busy: an integration is queued since {t}{stopping}; wait for it to end"
            )
        return f"{unit} is busy: it is being integrated since {t}{stopping}; wait for the integration to end"
    if machine == "hold":
        return f"{unit} is busy: a hold is being recorded since {t}; try again in a moment"
    if machine == "rounds":
        return f"{unit} is busy: a review round is being allowed since {t}; try again in a moment"
    if machine == "estimate":
        return f"a proposal for this workspace is already running since {t}; wait for it to end"
    if machine != "step":
        return f"a {machine} of this workspace is already running since {t}; wait for it to end"
    if state == "queued":
        return (
            f"{unit} is busy: a {stage} step is queued since {t}, waiting for a free slot{stopping}; "
            "stop it with its Stop button on the Board, or wait for it to run"
        )
    if state == "preparing":
        return (
            f"{unit} is busy: a {stage} step is being prepared since {t}{stopping}; stop it "
            "with its Stop button on the Board, or wait for it to start"
        )
    if state == "ending":
        return (
            f"{unit} is busy: a {stage} step is writing its artifact since {t}{stopping}; "
            "wait for it to end"
        )
    return (
        f"{unit} is busy: a {stage} step is running since {t}{stopping}; stop it with its Stop "
        "button on the Board (0034), or wait for it to end"
    )


class Attempts:
    """The store, the one function that moves an attempt, and the scheduler."""

    def __init__(
        self,
        data_dir: Any,
        bus: Bus,
        capacity: Callable[[str, str], int] = lambda _workspace, _slot: 1,
    ) -> None:
        self.data = Data(data_dir)
        self.bus = bus
        # How many attempts of a workspace may hold a kind of slot at once.
        self.capacity = capacity
        # By machine: what the scheduler calls with the row it moved on. Set by the runner.
        self.launchers: dict[str, Callable[[Attempt], None]] = {}
        # Whether a queued attempt may be moved on now: not while an update is under way. Set
        # by the service; `closed` from the moment the process goes down until a start reopens
        # it. Either way a queued attempt stays in the queue, for this process or the next.
        self.admitting: Callable[[], bool] = lambda: True
        self.closed = False
        self._waking = False
        self._again: set[str] = set()
        # Every move of every machine: a slot is freed by an end, wanted by a `queued`, and a
        # wake is one read when neither.
        for name in NAMES:
            if name.split(".")[0] in MACHINES:
                self.bus.subscribe(name, self._woken)

    # -- reading ----------------------------------------------------------

    def _rows(self, where: str = "", args: tuple[Any, ...] = ()) -> list[Attempt]:
        with self.data.connect() as conn:
            return [
                cast(Attempt, dict(r))
                for r in conn.execute(f"{_ROW} {where} ORDER BY a.id", args).fetchall()
            ]

    def get(self, attempt: int) -> Attempt | None:
        rows = self._rows("WHERE a.id = ?", (int(attempt),))
        return rows[0] if rows else None  # one id, one row

    def moves(self, attempt: int) -> list[Move]:
        with self.data.connect() as conn:
            return [
                cast(Move, dict(r))
                for r in conn.execute(
                    "SELECT * FROM attempt_moves WHERE attempt = ? ORDER BY seq", (int(attempt),)
                )
            ]

    def interventions(self, workspace: str, after: str, limit: int) -> list[Intervention]:
        """Each attempt of `workspace` the gate refused (`refused`, its reason code as the
        detail) and each a person opened as a rerun (`rerun`, its note), whose move is past
        `after`. Oldest first, at most `limit` of each."""
        out: list[Intervention] = []
        with self.data.connect() as conn:
            for row in conn.execute(
                "SELECT m.rowid AS id, m.at, m.outcome, a.unit, a.stage FROM attempt_moves m "
                "JOIN attempts a ON a.id = m.attempt WHERE a.workspace = ? "
                "AND m.moved_to = 'refused' AND m.at > ? ORDER BY m.at, m.rowid LIMIT ?",
                (workspace, after, int(limit)),
            ).fetchall():
                out.append(
                    Intervention(
                        f"refused:attempt_moves:{row['id']}",
                        "refused",
                        row["at"],
                        row["unit"],
                        row["stage"],
                        f"the gate refused it: {row['outcome']}",
                    )
                )
            for row in conn.execute(
                "SELECT a.id, f.at, a.unit, a.stage, a.note FROM attempts a "
                "JOIN attempt_moves f ON f.attempt = a.id AND f.seq = 1 "
                "WHERE a.workspace = ? AND a.rerun = 1 AND f.at > ? ORDER BY f.at, a.id LIMIT ?",
                (workspace, after, int(limit)),
            ).fetchall():
                out.append(
                    Intervention(
                        f"rerun:attempts:{row['id']}",
                        "rerun",
                        row["at"],
                        row["unit"],
                        row["stage"],
                        row["note"],
                    )
                )
        return out

    def unfinished(self, workspace: str | None = None, unit: str | None = None) -> list[Attempt]:
        """Every attempt not yet `ended` or `refused`, oldest first; of one workspace, one unit."""
        where = "WHERE m.moved_to NOT IN ('ended', 'refused')"
        args: list[Any] = []
        if workspace is not None:
            where += " AND a.workspace = ?"
            args.append(workspace)
        if unit is not None:
            where += " AND a.unit = ?"
            args.append(unit)
        return self._rows(where, tuple(args))

    def latest(self, workspace: str) -> list[Attempt]:
        """The last attempt of each unit of `workspace`, ended or refused included, oldest first."""
        return self._rows(
            "WHERE a.workspace = ? AND a.unit != '' AND a.id = "
            "(SELECT MAX(id) FROM attempts WHERE workspace = a.workspace AND unit = a.unit)",
            (workspace,),
        )

    def holding(self, workspace: str, unit: str) -> Attempt | None:
        """The unit's unfinished attempt, or `None`."""
        rows = self.unfinished(workspace, unit)
        return rows[0] if rows else None

    def busy(self, workspace: str, unit: str) -> str:
        """What holds this unit, in the one sentence every refusal carries, or `""`."""
        row = self.holding(workspace, unit)
        return describe(unit, row) if row is not None else ""

    # -- the one way a state changes ----------------------------------------

    def open(
        self,
        machine: str,
        workspace: str,
        unit: str,
        stage: str = "",
        *,
        started_by: str = "person",
        rerun: bool = False,
        note: str = "",
        note_by: str = "person",
        state: str = "queued",
    ) -> Attempt:
        """A new attempt in `state`, unless the unit already has one: then `Refused` with
        `unit-busy`, the only refusal left, and nothing written. Checked and written in one
        transaction. `note_by` is who wrote `note`: `person`, or `app` for the autopilot's."""
        if machine not in MACHINES or state not in ENTRIES:
            raise Illegal(f"no attempt of {machine!r} begins {state!r}")
        with self.data.write() as conn:
            held = conn.execute(
                f"{_ROW} WHERE a.workspace = ? AND a.unit = ? "
                "AND m.moved_to NOT IN ('ended', 'refused') ORDER BY a.id",
                (workspace, unit),
            ).fetchone()
            if held is not None:
                raise Refused(describe(unit, cast(Attempt, dict(held))), ("unit-busy",))
            cur = conn.execute(
                "INSERT INTO attempts (machine, workspace, unit, stage, slot, started_by, rerun, "
                "note, note_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    machine,
                    workspace,
                    unit,
                    stage,
                    SLOTS.get(machine, ""),
                    started_by,
                    int(rerun),
                    note,
                    note_by,
                ),
            )
            attempt = int(cur.lastrowid or 0)
            conn.execute(
                "INSERT INTO attempt_moves (attempt, seq, moved_to, at) VALUES (?, 1, ?, ?)",
                (attempt, state, now()),
            )
        self.bus.publish(Event(cast(Name, f"{machine}.{state}"), workspace, unit))
        row = self.get(attempt)
        assert row is not None
        return row

    def move(
        self,
        attempt: int,
        to: str,
        outcome: str = "",
        *,
        run: str | None = None,
        going_down: bool = False,
    ) -> Attempt:
        """Move an attempt to `to`, or raise `Illegal` and write nothing. `outcome` is an
        `ended` move's outcome or a `refused` move's code; `run` the step's events, set with
        the move that launched it."""
        if to in ENDS and not outcome:
            raise Illegal(f"an attempt is {to} with an outcome")
        with self.data.write() as conn:
            row = conn.execute(f"{_ROW} WHERE a.id = ?", (int(attempt),)).fetchone()
            if row is None:
                raise Illegal(f"no attempt {attempt}")
            machine, state = row["machine"], row["state"]
            if to not in MACHINES[machine].get(state, set()):
                raise Illegal(f"a {machine} attempt does not go from {state} to {to}")
            seq = conn.execute(
                "SELECT MAX(seq) FROM attempt_moves WHERE attempt = ?", (int(attempt),)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO attempt_moves (attempt, seq, moved_to, outcome, at) VALUES (?, ?, ?, ?, ?)",
                (int(attempt), int(seq) + 1, to, outcome if to in ENDS else "", now()),
            )
            if run is not None:
                conn.execute("UPDATE attempts SET run = ? WHERE id = ?", (run, int(attempt)))
        self.bus.publish(
            Event(
                cast(Name, f"{machine}.{to}"), row["workspace"], row["unit"], going_down=going_down
            )
        )
        moved = self.get(attempt)
        assert moved is not None
        return moved

    def ask_stop(self, attempt: int, by: str) -> Attempt:
        """Record a Stop on an unfinished attempt: the first name stays, two presses are one
        stop. Publishes `<machine>.stop-asked` once, when it is recorded."""
        with self.data.write() as conn:
            row = conn.execute(f"{_ROW} WHERE a.id = ?", (int(attempt),)).fetchone()
            if row is None or row["state"] in ENDS or row["machine"] not in STOPPABLE:
                raise Illegal(f"attempt {attempt} has nothing a Stop could end")
            first = row["stop_asked_at"] is None
            if first:
                conn.execute(
                    "UPDATE attempts SET stop_asked_at = ?, stop_asked_by = ? WHERE id = ?",
                    (now(), by, int(attempt)),
                )
        if first:
            self.bus.publish(
                Event(cast(Name, f"{row['machine']}.stop-asked"), row["workspace"], row["unit"])
            )
        asked = self.get(attempt)
        assert asked is not None
        return asked

    def set_run(self, attempt: int, run: str) -> None:
        """The events of a step Resume took up again: a new `run`, not a move."""
        with self.data.write() as conn:
            conn.execute("UPDATE attempts SET run = ? WHERE id = ?", (run, int(attempt)))

    def set_road(self, attempt: int, road: str) -> None:
        """An integration's road, `rebase` or `gebo`: what the board names it by, not a state."""
        with self.data.write() as conn:
            conn.execute("UPDATE attempts SET road = ? WHERE id = ?", (road, int(attempt)))

    # -- the scheduler ------------------------------------------------------

    def _woken(self, event: Event) -> None:
        self.wake(event.workspace)

    def wake(self, workspace: str) -> None:
        """Move the oldest queued attempts of `workspace` on while a slot of their kind is free.
        Synchronous: a slot freed is taken before the publish that freed it returns. A wake
        that comes while one runs is done after it; with no event loop nothing is launched."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._again.add(workspace)
        if self._waking:
            return
        self._waking = True
        try:
            while self._again:
                self._admit(self._again.pop())
        finally:
            self._waking = False

    def wake_all(self) -> None:
        for workspace in {r["workspace"] for r in self.unfinished() if r["state"] == "queued"}:
            self.wake(workspace)

    def _admit(self, workspace: str) -> None:
        if self.closed or not self.admitting():
            return
        rows = self.unfinished(workspace)
        for slot in set(SLOTS.values()):
            held = sum(1 for r in rows if r["slot"] == slot and r["state"] in HOLDING)
            free = self.capacity(workspace, slot) - held
            for row in [r for r in rows if r["slot"] == slot and r["state"] == "queued"]:
                launch = self.launchers.get(row["machine"])
                if free <= 0 or launch is None:
                    break
                moved = self.move(row["id"], FIRST[row["machine"]])
                free -= 1
                try:
                    launch(moved)
                except Exception:
                    log.exception("attempt %s could not be launched", row["id"])
                    self.move(row["id"], "ended", "failed")


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
