"""What the autopilot decides (`0043`), with no I/O: every function here is pure.

The autopilot starts the stage `cos.mjs next` names, through the same `Service.run_step` a
person's press goes through, which still asks the gate. Nothing here chooses a stage or
holds a copy of the loop. What it decides is the rest: where it must stop and wait for a
person (R6), whether the day's money allows one more step (R7), and which of the steps it
could start it may start now (R8). `Service` reads the board, `next`, the run log and the
settings, hands them here, and starts what comes back.

It starting a step is not a person's approval of anything, and it makes no gate more than
advice (`.claude/docs/not-built.md`).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from coscc import labels, spend
from coscc.policy import GRANTS, NOVEL_CEILINGS, grant_for, grant_for_step

# R2. Defaults, `intent.md ## Answers`, câu 2 and 3.
DEFAULT_MAX_PARALLEL = 4
DEFAULT_DAILY_CAP_USD = 50.0
# R5 d. Seconds. Chosen, not measured.
POLL_SECONDS = 300.0
# The spec's `## Design`: a `start` with no `end` counts as running for this long, then not.
# Chosen, not measured.
OPEN_FOR = timedelta(hours=24)

# R8 b. The stages that write code, and so may not run beside another whose files overlap.
CODE_STAGES = ("impl", "implement", "integrate")

# R6, and the one stop the spec adds beside them.
STOP_KINDS = ("a", "b", "c", "d", "e", "f", "cap")

# `cos.mjs`'s own words, read here because `next` hands out no `why`. `autopilot_test.py`
# reads each one back out of `.claude/scripts/cos.mjs`, so a change there turns it red.
CI_PENDING = "CI has not finished on #"
NEEDS_A_PERSON = "needs a person"
FINISHED = "finished"
CLOSED = "closed — "

_NUMBER = re.compile(r"^(\d+)")


def is_ci_pending(said: str) -> bool:
    """R6: the gate or `next` is waiting on CI. Not a stop; R5 d asks again."""
    return CI_PENDING in (said or "")


def _stop(kind: str, reason: str) -> dict[str, str]:
    return {"kind": kind, "reason": reason}


def stop_for(
    unit_row: dict[str, Any],
    nxt: dict[str, Any],
    last: dict[str, Any] | None,
    may_ship: bool,
) -> dict[str, str] | None:
    """The first of R6's stops that holds for one unit, as `{kind, reason}`, or `None`.

    `unit_row` is the unit as `Service.board` has it; `nxt` is `Service.next_step`'s answer;
    `last` the unit's latest `end` or `integration` record, or `None`. `None` back means no
    stop, which is not the same as something to run: a finished, rejected or held unit, and
    one waiting on CI, have neither.
    """
    stage = str(nxt.get("stage") or "")
    action = str(nxt.get("action") or "")
    if nxt.get("hold"):
        return None
    if not stage and (action == FINISHED or action.startswith(CLOSED)):
        return None

    # a. Every unanswered question of the counted artifact (`cos.mjs` `unitQuestions`).
    open_ = [
        q for q in unit_row.get("questions") or []
        if q.get("counted") and not q.get("answered")
    ]
    if open_:
        listed = ", ".join(f"{q.get('artifact')} question {q.get('n')}" for q in open_)
        return _stop("a", f"open questions: {listed}")

    # b. A person is awaited: a finding `next` names, or review has used every round.
    waiting = [str(x) for x in nxt.get("waiting") or []]
    if waiting:
        return _stop("b", f"{action} (awaiting a person on {', '.join(waiting)})")
    if not stage and action.startswith(NEEDS_A_PERSON):
        return _stop("b", action)

    kind = (last or {}).get("kind")
    outcome = str((last or {}).get("outcome") or "")
    # d. Gebo ended on `[needs-person]`.
    if kind == "integration" and outcome == "needs-person":
        said = "; ".join(str(x) for x in (last or {}).get("needs_person") or []) or "no reason given"
        return _stop("d", f"the last integration needs a person: {said}")
    # e. The unit's last step did not end `done`; no retry (`spec.md ## Answers`, câu 1). An
    # integration that failed, or that the autopilot started and was refused, is the same.
    if kind == "end" and outcome != "done":
        return _stop("e", f"the last {last.get('stage')} step ended {outcome or 'without an outcome'}")
    if kind == "integration" and (
        outcome == "failed" or (outcome == "refused" and last.get("started_by") == "autopilot")
    ):
        detail = str(last.get("detail") or "")
        return _stop("e", f"the last integration was {outcome}" + (f": {detail}" if detail else ""))

    # c. `ship`, while the workspace has not allowed it.
    if stage == "ship" and not may_ship:
        return _stop("c", "ship waits for a person: the autopilot may not ship in this workspace")

    # f. Nothing to run, and not because CI is still running.
    if not stage and not is_ci_pending(action):
        return _stop("f", action or "cos.mjs next named no stage")
    return None


def red_again(integration: dict[str, Any] | None, last_integration: dict[str, Any] | None) -> dict[str, str] | None:
    """R6 e for R10: CI is red on the head the autopilot's own last integration pushed.

    `integration` is the board's integration block of the unit, `last_integration` its
    latest `integration` record — the one `integrate.classify` compared the head with. A
    Gebo started again would only fix the last one's result, and no retry is the rule
    (`spec.md ## Answers`, câu 1). A person's integration that left CI red is not stopped.
    """
    if (integration or {}).get("state") != "red-after-integration":
        return None
    if started_by(last_integration or {}) != "autopilot":
        return None
    return _stop("e", "CI is still red after the autopilot's last integration")


# --- R7, the day's money ------------------------------------------------------


def _moment(at: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(at or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def today(now: datetime) -> str:
    """The machine's calendar day of `now` (`intent.md ## Answers`, câu 9)."""
    return spend.local_day(now.isoformat())


def spent_today(records: Iterable[dict[str, Any]], now: datetime) -> tuple[float, bool]:
    """`(usd, unknown)`: every `end` of the machine's day, whoever started it, every
    workspace. One `end` with no `cost_usd` makes `unknown` true, and `unknown` is the cap
    reached (`intent.md ## Answers`, câu 10)."""
    day = today(now)
    usd, unknown = 0.0, False
    for r in records:
        if r.get("kind") != "end" or spend.local_day(r.get("at")) != day:
            continue
        if r.get("cost_usd") is None:
            unknown = True
        else:
            usd += float(r["cost_usd"])
    return round(usd, 6), unknown


def reservation(stage: str) -> float:
    """What a step of `stage` is counted at before it ends: the largest `max_budget_usd` any
    label can give it (`coscc/policy.py` `grant_for_step`)."""
    if stage == "integrate":
        return float(grant_for("integrate").max_budget_usd or 0.0)
    return float(max(
        grant_for_step(stage, None).max_budget_usd or 0.0,
        grant_for_step(stage, labels.NOVEL).max_budget_usd or 0.0,
    ))


def estimate(stage: str) -> float:
    """What an `end` of `stage` with no `cost_usd` is counted at (`0105`): its reservation,
    or, for a stage no grant gives a budget, the largest budget in the grant table — read
    here, never copied, so a dearer grant added later raises it too."""
    own = reservation(stage)
    if own > 0:
        return own
    return float(max(
        [float(g.max_budget_usd or 0.0) for g in GRANTS.values()]
        + [float(budget) for _, budget in NOVEL_CEILINGS.values()]
    ))


def spent_on(records: Iterable[dict[str, Any]], day: str) -> dict[str, Any]:
    """`{known, estimated, estimated_count}`: every `end` of the machine's `day`, whoever
    started it, every workspace. An `end` with no `cost_usd`, whatever its outcome, counts
    at `estimate` of its stage (`0105`). An `integration` record is never added: a Gebo
    session's cost is on its own `end`."""
    known, estimated, count = 0.0, 0.0, 0
    for r in records:
        if r.get("kind") != "end" or spend.local_day(r.get("at")) != day:
            continue
        if r.get("cost_usd") is None:
            estimated += estimate(str(r.get("stage") or ""))
            count += 1
        else:
            known += float(r["cost_usd"])
    return {"known": round(known, 6), "estimated": round(estimated, 6), "estimated_count": count}


def open_starts(records: Iterable[dict[str, Any]], now: datetime) -> dict[tuple[str, str], dict[str, Any]]:
    """Every `(workspace, unit)` whose last `start` has no `end` yet and began within
    `OPEN_FOR` of `now`, with that `start`."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for r in records:
        kind = r.get("kind")
        key = (str(r.get("workspace") or ""), str(r.get("unit") or ""))
        if kind == "start":
            found[key] = r
        elif kind == "end":
            found.pop(key, None)
    oldest = now - OPEN_FOR
    return {k: r for k, r in found.items() if (_moment(r.get("at")) or oldest) > oldest}


def reserved(
    records: Iterable[dict[str, Any]], now: datetime, active: Iterable[tuple[str, str, str]] = (),
) -> float:
    """What the steps running now are counted at: every open `start`, and every step this
    process holds (`(workspace, unit, stage)`) that has not written its `start` yet."""
    opened = open_starts(records, now)
    total = sum(reservation(str(r.get("stage") or "")) for r in opened.values())
    for workspace, unit, stage in active:
        if (workspace, unit) not in opened:
            total += reservation(stage)
    return total


def cap_allows(spent: float, unknown: bool, running: float, need: float, limit: float) -> bool:
    """R7: spent + running + this step's reservation must fit under the limit."""
    return not unknown and spent + running + need <= limit


# --- R8, which of the candidates run now --------------------------------------


def files_of(plan_text: str | None) -> set[str] | None:
    """The paths under `plan.md ## Files that change`, or `None` when there are none to
    read — and `None` overlaps with everything (R8 b).

    Only tokens that look like a path: `labels.listed_paths` keeps every word of the
    section, and two plans sharing the word `new` do not share a file.
    """
    found = {p for p in labels.listed_paths(plan_text) if "/" in p or re.search(r"\.\w+$", p)}
    return found or None


def overlaps(a: set[str] | None, b: set[str] | None) -> bool:
    return a is None or b is None or bool(a & b)


def unit_number(name: str) -> int:
    m = _NUMBER.match(name or "")
    return int(m.group(1)) if m else 10 ** 9


def pick(
    candidates: Iterable[dict[str, Any]],
    running: Iterable[dict[str, Any]],
    max_parallel: int,
    room: float | None,
) -> dict[str, list[dict[str, Any]]]:
    """R8 a–f and R7 over the steps that could start, lowest unit number first.

    Each candidate and each running entry is `{unit, stage, files}`, and a candidate also
    carries `need`, its reservation. `room` is the money left under the cap, `None` when it
    is unknown. Returns `{"chosen": [...], "capped": [...]}`: what to start, and what the
    cap alone held back. What another rule held back is in neither; it waits its turn.
    """
    running = list(running)
    busy = {r["unit"] for r in running}
    taken = list(running)
    chosen: list[dict[str, Any]] = []
    capped: list[dict[str, Any]] = []
    for c in sorted(candidates, key=lambda c: (unit_number(c["unit"]), c["unit"])):
        if c["unit"] in busy:
            continue
        if len(busy) >= max_parallel:
            break
        if c["stage"] == "ship" and any(t["stage"] == "ship" for t in taken):
            continue
        if c["stage"] in CODE_STAGES and any(
            t["stage"] in CODE_STAGES and overlaps(c.get("files"), t.get("files")) for t in taken
        ):
            continue
        need = float(c.get("need") or 0.0)
        if room is None or need > room:
            capped.append(c)
            continue
        room -= need
        chosen.append(c)
        taken.append(c)
        busy.add(c["unit"])
    return {"chosen": chosen, "capped": capped}


# --- R11, what the intent's outcome is measured by ----------------------------

# The stages before `intent` is accepted, and those that are not a unit's stage at all.
_BEFORE = ("idea", "intent", "estimate")


def started_by(record: dict[str, Any]) -> str:
    """A record written before `0043` has no `started_by`, and reads as `person` (R3)."""
    return str(record.get("started_by") or "person")


def measure(
    records: Iterable[dict[str, Any]], workspace: str, since: str, until: str,
) -> dict[str, Any]:
    """R11. Per unit of `workspace`, over the machine's days `since`..`until` inclusive:
    whether it reached the stop before `ship` (a `review` step ending `done` with a `pass`
    round), how many starts the autopilot made on the way, and each person's start that
    was not at a stop.

    A person's start is at a stop when the last `autopilot-stop` record of the unit before
    it names one, or when the unit's last `end` before it was not `done` (R6 e). A
    mechanical integration writes no `start`; its `integration` record counts instead.
    """
    rows = [
        r for r in records
        if r.get("workspace") == workspace and since <= spend.local_day(r.get("at")) <= until
    ]
    per_unit: dict[str, dict[str, Any]] = {}
    stopped: dict[str, str] = {}
    last_end: dict[str, str] = {}
    for r in rows:
        unit = str(r.get("unit") or "")
        if not unit:
            continue
        u = per_unit.setdefault(unit, {
            "unit": unit, "reached": False, "autopilot": 0, "person": 0, "outside": [],
        })
        kind = r.get("kind")
        if kind == "autopilot-stop":
            stopped[unit] = str(r.get("stop") or "")
            continue
        if kind == "end":
            last_end[unit] = str(r.get("outcome") or "")
            if (
                r.get("stage") == "review" and r.get("outcome") == "done"
                and "pass" in (r.get("verdicts") or [])
            ):
                u["reached"] = True
            continue
        press = (kind == "start" and r.get("stage") not in _BEFORE) or (
            kind == "integration" and r.get("mode") == "mechanical"
        )
        if not press or u["reached"]:
            continue
        if started_by(r) == "autopilot":
            u["autopilot"] += 1
            continue
        u["person"] += 1
        at_stop = bool(stopped.get(unit)) or last_end.get(unit, "done") != "done"
        if not at_stop:
            u["outside"].append({"at": r.get("at"), "stage": r.get("stage"), "kind": kind})
    units_ = sorted(per_unit.values(), key=lambda u: (unit_number(u["unit"]), u["unit"]))
    met = [u["unit"] for u in units_ if u["reached"] and u["autopilot"] and not u["outside"]]
    return {"workspace": workspace, "since": since, "until": until, "units": units_, "met": met}
