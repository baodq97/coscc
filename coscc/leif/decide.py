"""What the autopilot decides, with no I/O: every function here is pure.

The autopilot queues the stage the loop's `next` names, as an attempt the scheduler starts and
the gate is asked about, as a person's press is. What it decides is where it must stop for a
person, whether the day's money allows one more step, and which candidate steps may queue now.
Queueing a step is not a person's approval of anything.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from coscc.agent import models, pack, policy
from coscc.github import prmachine
from coscc.leif import spend
from coscc.store.journal import is_step
from coscc.units import states
from coscc.units.board import SHIP_UNRECORDED, open_questions
from coscc.units.guards import REASONS as GATE_REASONS

# Defaults.
DEFAULT_MAX_PARALLEL = 4
DEFAULT_DAILY_CAP_USD = 50.0
# Seconds. Chosen, not measured.
POLL_SECONDS = 300.0
# A `start` with no `end` counts as running for this long, then not. Chosen, not measured.
OPEN_FOR = timedelta(hours=24)

# The stages that write code, and so may not run beside another whose files overlap.


def is_coder(stage: object) -> bool:
    """Whether `stage` writes in the unit's branch: a state whose output is written `by: session`.
    `is_code` adds Gebo, which rewrites the same branch."""
    return stage in states.states_where(by="session", kind="artifact")


def is_code(stage: object) -> bool:
    return is_coder(stage) or stage == "integrate"


def is_merge(stage: object) -> bool:
    """Whether `stage` is a state the engine's `merge` action runs."""
    return stage in states.states_where(action="merge")


# The stop kinds `a`-`f`, plus an empty shortlist, a draft run again as often as it may, and
# one waiting for a free place.
STOP_KINDS = ("a", "b", "c", "d", "e", "f", "cap", "shortlist", "reruns", "full")

# Why a unit ranked higher on the shortlist was passed over, and nothing else.
REASONS = (
    "held",
    "finished",
    "closed",
    "stop",
    "ci",
    "running",
    "overlap",
    "ship-busy",
    "missing",
    "dependency",
    "overlap-pr",
)
# The stop `e` of a unit whose screenshots could not be taken again before `review`.
SCREENS_FAILED = "the screenshots could not be taken again before review"


def has_nothing_to_integrate(code: Any) -> bool:
    """An integration refused because the unit's state had nothing to integrate
    (`integrate.NOTHING`): no refusal a person must look at. A record with no code is a real one."""
    return code == "nothing-to-integrate"


# The workspace's stop line when there is no shortlist to follow.
NO_SHORTLIST = "Nothing is on the shortlist, so the autopilot starts nothing."

_NUMBER = re.compile(r"^(\d+)")


# What `next` or the gate said is read by code, from `reasons` (`guards.REASONS`), never from its words.
def said(answer: Any, reason: str) -> bool:
    """Whether `reason` is among the codes of `answer`: `next`'s dict, or anything with `reasons`."""
    if isinstance(answer, dict):
        codes = answer.get("reasons") or ()
    else:
        codes = getattr(answer, "reasons", None) or ()
    return reason in codes


def is_waiting_on_dependency(answer: Any) -> bool:
    """`next` holds `impl` back until a dependency merges. Not a stop: nothing a person does would move it."""
    return said(answer, "waiting-on")


def is_ci_pending(answer: Any) -> bool:
    """The gate or `next` is waiting on CI. Not a stop; the next pass asks again."""
    return said(answer, "ci-pending")


# The codes of a refusal that is a race: try again, nobody needs to act.
WAIT = ("unit-busy", "updating", "ci-pending")


def is_waiting(answer: Any) -> bool:
    """A refusal that is a race: the next pass asks again. Not a stop."""
    return any(said(answer, code) for code in WAIT)


def is_ci_red(answer: Any) -> bool:
    """`next` sends the unit back to `impl` because CI is red."""
    return said(answer, "ci-red")


def continues(answer: Any) -> bool:
    """`next` names no stage but says the unit's `impl` ended with its file still a draft."""
    return isinstance(answer, dict) and is_coder(answer.get("continue"))


def is_recording_ship(answer: Any) -> bool:
    """`next` or the gate names a `ship` that only records a merge already made."""
    return said(answer, "recording-ship")


def is_ship_merging(answer: Any) -> bool:
    """`next` reads `ship.md` as a merge asked for and not yet recorded."""
    return said(answer, "ship-merging")


def needs_a_person(answer: Any) -> bool:
    """`next` stops for a person: review used its rounds, a spike failed too often, a red check
    no impl can fix, a pass left closed twice on one head, or a spec or plan skipped by no person."""
    return (
        said(answer, "needs-person")
        or said(answer, "awaits-person")
        or said(answer, "agent-cannot-skip")
    )


def is_over(answer: Any) -> bool:
    """`next` offers nothing because the unit is finished or closed."""
    return said(answer, "finished") or said(answer, "closed")


def _stop(kind: str, reason: str) -> dict[str, str]:
    return {"kind": kind, "reason": reason}


def _listed(questions: Iterable[dict[str, Any]]) -> str:
    return ", ".join(f"{q.get('artifact')} question {q.get('n')}" for q in questions)


def stop_for(
    unit_row: dict[str, Any],
    nxt: Mapping[str, Any],
    last: dict[str, Any] | None,
    may_ship: bool,
    unopened: int = 0,
    shipping: bool = False,
) -> dict[str, str] | None:
    """The first stop that holds for one unit, as `{kind, reason}`, or `None`.

    `unit_row` is the unit as `Core.board` has it; `nxt` is `Steps.next_step`'s answer;
    `last` the unit's latest `end`, `integration`, `screens` or `prmachine.RECORD_KIND` record, or
    `None`. `None` back means no stop, which is not the same as something to run.
    `unopened` is how many steps of `last`'s stage ended `failed` for their reply's opening; at 0
    such a step stops at once. `shipping` is whether a `ship` attempt of the unit has not ended.
    """
    stage = str(nxt.get("stage") or "")
    action = str(nxt.get("action") or "")
    if nxt.get("hold"):
        return None
    if not stage and is_over(nxt):
        return None

    # a. Every unanswered question of the counted artifact.
    open_ = open_questions(unit_row)
    if open_:
        return _stop("a", f"open questions: {_listed(open_)}")

    # b. A person is awaited: a finding `next` names, or review has used every round.
    waiting = [str(x) for x in nxt.get("waiting") or []]
    if waiting:
        return _stop("b", f"{action} (awaiting a person on {', '.join(waiting)})")
    if not stage and needs_a_person(nxt):
        return _stop("b", action)

    # The last record, or nothing when there is none.
    seen = last or {}
    kind = seen.get("kind")
    outcome = str(seen.get("outcome") or "")
    # d. Gebo's integration ended `needs-person`.
    if kind == "integration" and outcome == "needs-person":
        said = "; ".join(str(x) for x in seen.get("needs_person") or []) or "no reason given"
        return _stop("d", f"the last integration needs a person: {said}")
    # e. The unit's last step did not end `done`. A step that paused at a ceiling stops with its
    # own code: only a person raises the ceiling or reruns it. The first time a prose stage ends
    # `failed` because its reply lacked its opening is no stop: it runs again once. Otherwise no
    # retry: `failed`, `cancelled`, `stopped`, an integration that failed or that the autopilot
    # started and was refused for anything but a state with nothing to integrate. An
    # integration refused with nothing to integrate is no last word: what follows is decided as
    # if it had not run.
    if kind == "end" and outcome == "paused-budget":
        return {
            **_stop("e", f"the last {seen.get('stage')} step paused at its ceiling"),
            "code": "budget-reached",
        }
    if kind == "end" and outcome != "done" and not (_unopened(last) and unopened == 1):
        return _stop(
            "e", f"the last {seen.get('stage')} step ended {outcome or 'without an outcome'}"
        )
    if kind == "integration" and (
        outcome == "failed"
        or (
            outcome == "refused"
            and seen.get("started_by") == "autopilot"
            and not has_nothing_to_integrate(seen.get("code"))
        )
    ):
        detail = str(seen.get("detail") or "")
        return _stop("e", f"the last integration was {outcome}" + (f": {detail}" if detail else ""))
    # A `pr` or `ship` the PR machine ran that failed, or whose guard refused it, stops as a
    # `failed` session does. A merge GitHub refused stops `f` on what `gh` said, so the pass
    # can still integrate a unit the refusal left behind `main`.
    if kind == prmachine.RECORD_KIND and outcome != "done":
        detail = str(seen.get("detail") or "") or ", ".join(
            str(r) for r in seen.get("reasons") or []
        )
        if seen.get("merge_refused"):
            return _stop("f", f"ship was refused: {detail or 'no reason given'}")
        return _stop(
            "e",
            f"the last {seen.get('stage')} was {seen.get('result') or outcome}"
            + (f": {detail}" if detail else ""),
        )
    # The screenshots could not be taken again before `review`, which did not start. No retry:
    # a person runs it again, and a retake that is taken lifts the stop.
    if kind == "screens" and outcome == "failed":
        return _stop("e", SCREENS_FAILED)
    # A merge asked for and not yet recorded: no stop while the `ship` that asked runs; with none
    # running, nothing will record it.
    if not stage and is_ship_merging(nxt):
        return None if shipping else _stop("e", SHIP_UNRECORDED)

    # c. `ship`, while the workspace has not allowed it.
    if is_merge(stage) and not may_ship:
        return _stop("c", "ship waits for a person: the autopilot may not ship in this workspace")

    # A draft whose questions are all answered, or an impl that left its file a draft, is a stage
    # to run again, not a stop.
    if not stage and (nxt.get("rerun") or continues(nxt)):
        return None
    if not stage and is_waiting_on_dependency(nxt):
        return None
    # f. Nothing to run, and not because CI is still running.
    if not stage and not is_ci_pending(nxt):
        return _stop("f", action or "the loop named no stage for next")
    return None


# How many `impl` steps the autopilot runs after its own integration turned CI red. Not
# `COS_REVIEW_ROUNDS`, which counts review rounds.
IMPL_PER_INTEGRATION = 1
# The stop `e` once that `impl` ran and CI is still red.
STILL_RED = "CI is still red after the impl that followed the autopilot's last integration"


def since_integration(
    records: Iterable[dict[str, Any]],
    workspace: str,
    unit: str,
) -> list[dict[str, Any]] | None:
    """The unit's records after its latest `integration`, up to the first `review` `start` after
    it, or `None` when it has no `integration` or that `start` has come."""
    after: list[dict[str, Any]] | None = None
    for r in records:
        if r.get("workspace") != workspace or r.get("unit") != unit or not is_step(r):
            continue
        if r.get("kind") == "integration":
            after = []
        elif after is not None and r.get("kind") == "start" and states.is_review(r.get("stage")):
            after = None
        elif after is not None:
            after.append(r)
    return after


def after_own_integration(
    integration: dict[str, Any] | None,
    last_integration: dict[str, Any] | None,
    after: list[dict[str, Any]] | None,
    nxt: Mapping[str, Any],
) -> tuple[str, dict[str, str] | None] | None:
    """What follows CI red on the autopilot's own pushed integration.

    `integration` is the board's integration block of the unit, `last_integration` its latest
    `integration` record, `after` what `since_integration` returns, `nxt` `next`'s answer.
    `(stage, None)` runs the stage `next` names, once; `("", stop)` is a stop `e`; `None`
    leaves the unit to the rest of the pass. Gebo is never started again.
    """
    last_integration = last_integration or {}
    if started_by(last_integration) != "autopilot" or last_integration.get("outcome") != "pushed":
        return None
    red_state = (integration or {}).get("state") == "red-after-integration"
    fixing = is_coder(nxt.get("stage"))
    red_next = fixing and is_ci_red(nxt)
    again = _stop("e", "CI is still red after the autopilot's last integration")
    if after is None:
        return ("", again) if red_state else None
    ran, by = 0, ""
    for r in after:
        if not is_coder(r.get("stage")):
            continue
        if r.get("kind") == "start":
            by = started_by(r)
            ran += 1 if by == "autopilot" else 0
    if ran >= IMPL_PER_INTEGRATION and (red_state or red_next):
        return ("", _stop("e", STILL_RED))
    if red_state and fixing:
        return (str(nxt.get("stage")), None)
    if red_state:
        return ("", again)
    return None


# --- the day's money ------------------------------------------------------


def _moment(at: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(at or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def today(now: datetime) -> str:
    """The machine's calendar day of `now`."""
    return spend.local_day(now.isoformat())


def spent_today(records: Iterable[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """`spent_on` the machine's day of `now`. An `end` with no `cost_usd` is counted at its estimate, not as the cap reached."""
    return spent_on(records, today(now))


def reservation(stage: str) -> float:
    """What a step of `stage` is counted at before it ends: the largest dollar ceiling any
    label can give it, as `models.ceilings` resolves it from its row, so a raised ceiling is
    held in full."""
    return float(
        max(
            models.ceilings(stage, label)["max_budget_usd"] or 0.0 for label in (None, policy.NOVEL)
        )
    )


def estimate(stage: str) -> float:
    """What an `end` of `stage` with no `cost_usd` is counted at: its reservation, or, for a
    stage no row gives a budget, the largest any row gives (read, never copied)."""
    own = reservation(stage)
    if own > 0:
        return own
    return max(reservation(k) for k in pack.rows())


def spent_on(records: Iterable[dict[str, Any]], day: str) -> dict[str, Any]:
    """`{known, estimated, estimated_count}`: every `end` of the machine's `day`, whoever
    started it, every workspace. An `end` with no `cost_usd` counts at `estimate` of its
    stage. An `integration` record is never added: a Gebo session's cost is on its own `end`."""
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


def open_starts(
    records: Iterable[dict[str, Any]], now: datetime
) -> dict[tuple[str, str], dict[str, Any]]:
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
    records: Iterable[dict[str, Any]],
    now: datetime,
    active: Iterable[tuple[str, str, str]] = (),
) -> float:
    """What the steps running now are counted at: every open `start`, and every step this
    process holds (`(workspace, unit, stage)`) that has not written its `start` yet."""
    opened = open_starts(records, now)
    total = sum(reservation(str(r.get("stage") or "")) for r in opened.values())
    for workspace, unit, stage in active:
        if (workspace, unit) not in opened:
            total += reservation(stage)
    return total


# --- which of the candidates run now --------------------------------------


def overlaps(a: set[str] | None, b: set[str] | None) -> bool:
    return a is None or b is None or bool(a & b)


def unit_number(name: str) -> int:
    m = _NUMBER.match(name or "")
    return int(m.group(1)) if m else 10**9


def pick(
    candidates: Iterable[dict[str, Any]],
    running: Iterable[dict[str, Any]],
    max_parallel: int,
    room: float,
    open_prs: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Which of the steps that could start may start now, highest on the shortlist first.

    Each candidate and each running entry is `{unit, stage, files}`, and a candidate also
    carries `rank`, its place on the shortlist, and `need`, its reservation. `room` is the
    money left under the cap. Returns `{"chosen": [...], "capped": [...], "held": {...}}`:
    what to start, what the cap alone held back, and `(reason, detail)` for each candidate
    another rule held back. What `max_parallel` held back is in none of them.

    `open_prs` is `{unit, number, files}` for each open, unmerged pull request of the
    workspace, `files` `None` when its diff could not be read. An `impl` of a unit with no
    pull request of its own waits while one of another unit's touches a file its plan names.
    """
    running = list(running)
    open_prs = list(open_prs)
    with_pr = {p["unit"] for p in open_prs}
    busy = {r["unit"] for r in running}
    taken = list(running)
    chosen: list[dict[str, Any]] = []
    capped: list[dict[str, Any]] = []
    held: dict[str, tuple[str, str]] = {}
    for c in sorted(candidates, key=lambda c: (c["rank"], c["unit"])):
        if c["unit"] in busy:
            held[c["unit"]] = (
                "running",
                next((t["stage"] for t in taken if t["unit"] == c["unit"]), ""),
            )
            continue
        if len(busy) >= max_parallel:
            break
        shipping = next((t for t in taken if is_merge(c["stage"]) and is_merge(t["stage"])), None)
        if shipping is not None:
            held[c["unit"]] = ("ship-busy", shipping["unit"])
            continue
        crossing = next(
            (
                t
                for t in taken
                if is_code(c["stage"])
                and is_code(t["stage"])
                and overlaps(c.get("files"), t.get("files"))
            ),
            None,
        )
        if crossing is not None:
            held[c["unit"]] = ("overlap", crossing["unit"])
            continue
        if is_coder(c["stage"]) and c["unit"] not in with_pr:
            blocking = next(
                (
                    p
                    for p in open_prs
                    if p["unit"] != c["unit"] and overlaps(c.get("files"), p.get("files"))
                ),
                None,
            )
            if blocking is not None:
                held[c["unit"]] = ("overlap-pr", f"#{blocking['number']}")
                continue
        need = float(c.get("need") or 0.0)
        if need > room:
            capped.append(c)
            continue
        room -= need
        chosen.append(c)
        taken.append(c)
        busy.add(c["unit"])
    return {"chosen": chosen, "capped": capped, "held": held}


# --- the shortlist's order --------------------------------------------


def stop_reason(stop: dict[str, str]) -> tuple[str, str]:
    """Why a unit with a stop is no candidate, `(reason, detail)`."""
    return ("stop", f"{stop['kind']}: {stop['reason']}")


def reason_for(
    nxt: Mapping[str, Any],
    stage: str,
    stop: dict[str, str] | None,
) -> tuple[str, str] | None:
    """Why a unit is no candidate, as `(reason, detail)`, or `None` when it is one.

    Read off what the pass already has, never made up: a case none of these explains raises.
    """
    if stage and stop is None:
        return None
    action = str(nxt.get("action") or "")
    if stop is not None:
        return stop_reason(stop)
    if nxt.get("hold"):
        return ("held", str((nxt["hold"] or {}).get("state") or "held"))
    if said(nxt, "finished"):
        return ("finished", action)
    if said(nxt, "closed"):
        return ("closed", action)
    if is_ci_pending(nxt):
        return ("ci", action)
    if is_waiting_on_dependency(nxt):
        return ("dependency", action)
    raise ValueError(f"no reason for a unit with no stage and no stop: {action or 'nothing said'}")


def passed_for(
    units: list[str],
    chosen: list[str],
    reasons: dict[str, tuple[str, str]],
) -> list[list[dict[str, str]]]:
    """For each of `chosen`, in order, every unit ranked above it on `units` that was not
    chosen before it this pass, each `{unit, reason, detail}`. A unit with no reason raises."""
    out: list[list[dict[str, str]]] = []
    for i, name in enumerate(chosen):
        before = set(chosen[:i])
        passed = []
        for above in units[: units.index(name)]:
            if above in before:
                continue
            if above not in reasons:
                raise ValueError(
                    f"{name} would be started ahead of {above}, which has no reason recorded"
                )
            reason, detail = reasons[above]
            passed.append({"unit": above, "reason": reason, "detail": detail})
        out.append(passed)
    return out


# --- a draft whose questions are all answered -------------------------

# How often one artifact runs again after its answers before a person decides the next run.
MAX_RERUNS = 2


def reruns_of(records: Iterable[dict[str, Any]], workspace: str, unit: str, stage: str) -> int:
    """How many `start`s of `stage` on `unit` followed an earlier one with at least one
    `answer` record of the same unit and stage between them, whoever started either."""
    count, started, answered = 0, False, False
    for r in records:
        if r.get("workspace") != workspace or r.get("unit") != unit or r.get("stage") != stage:
            continue
        if r.get("kind") == "answer":
            answered = True
        elif r.get("kind") == "start":
            if started and answered:
                count += 1
            started, answered = True, False
    return count


def _lacks_opening(record: dict[str, Any]) -> bool:
    """The `detail` `opening_reason` opens with (`coscc/runner/reply.py`), for the record's own
    stage; a repair turn that failed too keeps it on the first line."""
    return str(record.get("detail") or "").startswith(
        f"{record.get('stage')}.md lacks its opening:"
    )


def _unopened(last: dict[str, Any] | None) -> bool:
    """`last` is a prose stage's `end` that failed because its reply lacked its opening."""
    last = last or {}
    return (
        last.get("kind") == "end"
        and last.get("outcome") == "failed"
        and policy.row_for(str(last.get("stage") or "")).prose
        and _lacks_opening(last)
    )


def unopened_of(records: Iterable[dict[str, Any]], workspace: str, unit: str, stage: str) -> int:
    """How many steps of `stage` on `unit` ended `failed` because their reply lacked its
    opening, over the whole run log, whoever started them."""
    return sum(
        1
        for r in records
        if r.get("kind") == "end"
        and r.get("outcome") == "failed"
        and is_step(r)
        and r.get("workspace") == workspace
        and r.get("unit") == unit
        and r.get("stage") == stage
        and _lacks_opening(r)
    )


def answered_since_start(
    records: Iterable[dict[str, Any]], workspace: str, unit: str, stage: str
) -> bool:
    """Whether an `answer` record of `stage` on `unit` came after its last `start`. A run
    again that ends `draft` keeps its answered questions' numbers, so the loop's `next` can say
    `rerun` with no new answer behind it; without this the autopilot would rerun on every pass."""
    fresh = False
    for r in records:
        if r.get("workspace") != workspace or r.get("unit") != unit or r.get("stage") != stage:
            continue
        if r.get("kind") == "answer":
            fresh = True
        elif r.get("kind") == "start":
            fresh = False
    return fresh


def answer_completes(unit_row: dict[str, Any], artifact: str, answered: Iterable[Any]) -> bool:
    """Whether `artifact`, of a stage whose answered draft runs again, has no numbered question
    left unanswered: each is answered on the board read `unit_row` came from, or its number is
    in `answered`. Those stages are `unit_row["after_answers"]`; a row without the list completes nothing."""
    stage = next(
        (s.get("stage") for s in unit_row.get("stages") or [] if s.get("file") == artifact), ""
    )
    if not stage or stage not in (unit_row.get("after_answers") or ()):
        return False
    given = set(answered)
    asked = [q for q in unit_row.get("questions") or [] if q.get("artifact") == artifact]
    return bool(asked) and all(q.get("answered") or q.get("n") in given for q in asked)


def rerun_stop(artifact: str) -> dict[str, str]:
    """The draft has run again `MAX_RERUNS` times after its answers."""
    return _stop(
        "reruns",
        f"{artifact} was run again {MAX_RERUNS} times after its answers; a person decides the next run.",
    )


# --- a try that must change the head ---------------------------------

# How often `impl` is queued on one head with an app note (a draft continued, a red CI) before a
# person decides the next run.
MAX_TRIES = 2
# The note of a draft `impl.md` that runs again.
CONTINUE_NOTE = "impl.md is still a draft: go on with it"
# The buckets of `gh pr checks` that are red.
RED_BUCKETS = ("fail", "cancel")


def tries_on_head(
    records: Iterable[Mapping[str, Any]], workspace: str, unit: str, head: str = ""
) -> int:
    """How many steps the autopilot began on the unit's head, since that head last changed, after an
    `autopilot-pick` carrying `continued` or `ci_note`. A try is such a pick and the autopilot's
    `start` that follows it, on the head the step began on: picks refused before their step began
    are none, and a pick with no `start` yet is still queued or running, which no pass picks again.
    `head`: the head the PR machine reads now, when there is one; a different one than the last
    `start` saw means the step pushed, and nothing was tried on it yet."""
    count, current, pending = 0, "", False
    for r in records:
        if r.get("workspace") != workspace or r.get("unit") != unit:
            continue
        if r.get("kind") == "autopilot-pick" and (r.get("continued") or r.get("ci_note")):
            pending = True
        elif r.get("kind") == "start" and r.get("head"):
            if r["head"] != current:
                current, count = str(r["head"]), 0
            if pending and started_by(r) == "autopilot":
                count += 1
            pending = False
    if head and current and head != current:
        return 0
    return count


def tries_stop(stage: str, tries: int) -> dict[str, str]:
    """`stage` was queued with a note as often as it may on one head."""
    return _stop(
        "e", f"{stage} was tried {tries} times on the same head; a person decides the next run."
    )


def ci_note(head: str, checks: Iterable[Mapping[str, Any]]) -> str:
    """The app's note for an `impl` after CI went red: the red required checks and the head the PR
    machine read them at."""
    red = [str(c.get("name") or "?") for c in checks if str(c.get("bucket") or "") in RED_BUCKETS]
    at = f" at {head[:12]}" if head else ""
    names = ", ".join(red) if red else "the required checks"
    return f"CI is red{at}: {names} failed. Fix them and push."


# --- an attempt the gate refused --------------------------------------

# A refusal that is a race gets one try again after this long, unless a read of the pull requests
# woke the pass. Chosen, not measured.
REFUSAL_HOLD = timedelta(seconds=30)


def after_refusal(
    refusal: Mapping[str, Any] | None, stage: str, now: datetime, fresh: bool = False
) -> tuple[dict[str, str] | None, tuple[str, str] | None]:
    """What the autopilot's last attempt of a unit, refused by the gate, makes of a pass that would
    queue `stage` again: `(stop, why_not)`. `refusal` is `{stage, code, at}`.

    The same stage refused is the stop `f`, with the refusal's code, but for a race (`WAIT`). A race is
    asked again at once if `fresh` (a read of the pull requests moved something), else after
    `REFUSAL_HOLD`; before that the unit waits with a reason of its own. Another stage than the
    refused one is no part of it, and neither is an integration with nothing to integrate."""
    if refusal is None or refusal.get("stage") != stage:
        return None, None
    code = str(refusal.get("code") or "")
    if has_nothing_to_integrate(code):
        return None, None
    if code in WAIT:
        # `unit-busy` is no hold: the other attempt shows as running while it lasts.
        moment = _moment(refusal.get("at"))
        if code != "unit-busy" and not fresh and moment is not None and now - moment < REFUSAL_HOLD:
            return None, (("ci", "") if code == "ci-pending" else ("running", ""))
        return None, None
    stop = _stop("f", f"{stage} was refused: {code or 'no reason given'}")
    return ({**stop, "code": code} if code in GATE_REASONS else stop), None


def full_stop(stage: str, max_parallel: int) -> dict[str, str]:
    """A run again that `max_parallel` alone held back. Not a wait for a person."""
    running = "1 step is" if max_parallel == 1 else f"{max_parallel} steps are"
    return _stop(
        "full",
        f"{stage} waits to run again: {running} already running, the most this workspace allows.",
    )


# --- what the intent's outcome is measured by ----------------------------

# The stages before `intent` is accepted, and those that are not a unit's stage at all.
_BEFORE = (*states.opening_states(), "estimate")


def started_by(record: Mapping[str, Any]) -> str:
    """A record with no `started_by` reads as `person`."""
    return str(record.get("started_by") or "person")


def measure(
    records: Iterable[dict[str, Any]],
    workspace: str,
    since: str,
    until: str,
) -> dict[str, Any]:
    """Per unit of `workspace`, over the machine's days `since`..`until` inclusive: whether it
    reached the stop before `ship` (a `review` step ending `done` with a `pass` round), how
    many starts the autopilot made on the way, and each person's start that was not at a stop.

    A person's start is at a stop when the last `autopilot-stop` record of the unit before it
    names one, or when the unit's last `end` before it was not `done`. A mechanical
    integration writes no `start`; its `integration` record counts instead.
    """
    window = [
        r
        for r in records
        if r.get("workspace") == workspace and since <= spend.local_day(r.get("at")) <= until
    ]
    rows = [r for r in window if is_step(r)]
    per_unit: dict[str, dict[str, Any]] = {}
    stopped: dict[str, str] = {}
    last_end: dict[str, str] = {}

    def blank(unit: str) -> dict[str, Any]:
        return {"unit": unit, "reached": False, "autopilot": 0, "person": 0, "outside": []}

    for r in rows:
        unit = str(r.get("unit") or "")
        if not unit:
            continue
        u = per_unit.setdefault(unit, blank(unit))
        kind = r.get("kind")
        if kind == "autopilot-stop":
            stopped[unit] = str(r.get("stop") or "")
            continue
        if kind == "end":
            last_end[unit] = str(r.get("outcome") or "")
            if (
                states.is_review(r.get("stage"))
                and r.get("outcome") == "done"
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
