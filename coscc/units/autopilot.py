"""What the autopilot decides, with no I/O: every function here is pure.

The autopilot starts the stage `cos.mjs next` names through the same `Service.run_step` a
person's press goes through, which still asks the gate. What it decides is where it must
stop for a person, whether the day's money allows one more step, and which candidate steps
may start now. Starting a step is not a person's approval of anything.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from coscc.agent import labels
from coscc.runlog import spend
from coscc.agent.policy import GRANTS, NOVEL_CEILINGS, grant_for, grant_for_step, is_prose_stage

# Defaults.
DEFAULT_MAX_PARALLEL = 4
DEFAULT_DAILY_CAP_USD = 50.0
# Seconds. Chosen, not measured.
POLL_SECONDS = 300.0
# A `start` with no `end` counts as running for this long, then not. Chosen, not measured.
OPEN_FOR = timedelta(hours=24)

# The stages that write code, and so may not run beside another whose files overlap.
CODE_STAGES = ("impl", "implement", "integrate")

# Run-log lines under a stage that is none of the loop's (an earlier version wrote its answering
# session here); none of them is the unit's last step, a start, or a failed step.
NOT_STEPS = ("precedent",)

# The stop kinds `a`-`f`, plus an empty shortlist, a draft run again as often as it may, and
# one waiting for a free place.
STOP_KINDS = ("a", "b", "c", "d", "e", "f", "cap", "shortlist", "reruns", "full")

# Why a unit ranked higher on the shortlist was passed over, and nothing else.
REASONS = ("held", "finished", "closed", "stop", "ci", "running", "overlap", "ship-busy", "missing", "dependency", "overlap-pr")
# The stop `e` of a unit whose screenshots could not be taken again before `review`.
SCREENS_FAILED = "the screenshots could not be taken again before review"
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


def is_ci_red(answer: Any) -> bool:
    """`next` sends the unit back to `impl` because CI is red."""
    return said(answer, "ci-red")


def is_recording_ship(answer: Any) -> bool:
    """`next` or the gate names a `ship` that only records a merge already made."""
    return said(answer, "recording-ship")


def needs_a_person(answer: Any) -> bool:
    """`next` stops for a person: review used its rounds, a spike failed too often, a red check
    no impl can fix, a pass left closed twice on one head, or a spec or plan skipped by no person."""
    return said(answer, "needs-person") or said(answer, "awaits-person") or said(answer, "agent-cannot-skip")


def is_over(answer: Any) -> bool:
    """`next` offers nothing because the unit is finished or closed."""
    return said(answer, "finished") or said(answer, "closed")


# The record a `pr` or `ship` the PR machine ran leaves in place of an `end`: `outcome`
# `done` or `failed`, and the machine's `result`, `reasons` and `detail`.
PR_MACHINE = "prmachine"


def is_step(record: dict[str, Any]) -> bool:
    """False for a line of a session that is no stage of `cos.mjs`'s loop (`NOT_STEPS`)."""
    return record.get("stage") not in NOT_STEPS


def _stop(kind: str, reason: str) -> dict[str, str]:
    return {"kind": kind, "reason": reason}


def open_questions(unit_row: dict[str, Any]) -> list[dict[str, Any]]:
    """Every unanswered question of the counted artifact (`cos.mjs` `unitQuestions`): the stop `a`,
    and the `questions` record a step that ends `done` leaves."""
    return [q for q in unit_row.get("questions") or [] if q.get("counted") and not q.get("answered")]


def _listed(questions: Iterable[dict[str, Any]]) -> str:
    return ", ".join(f"{q.get('artifact')} question {q.get('n')}" for q in questions)


def skips_exhausted(nxt: dict[str, Any], last: dict[str, Any] | None, recorded: bool) -> bool:
    """A `ship` that ran out of turns is no stop when `next` names a `ship` that only records
    the merge, unless the step that ran out was itself one (`recorded`)."""
    last = last or {}
    return (
        last.get("kind") == "end" and last.get("stage") == "ship" and last.get("outcome") == "exhausted"
        and nxt.get("stage") == "ship" and is_recording_ship(nxt)
        and not recorded
    )


def stop_for(
    unit_row: dict[str, Any],
    nxt: dict[str, Any],
    last: dict[str, Any] | None,
    may_ship: bool,
    exhausted: int = 0,
    unopened: int = 0,
    recorded: bool = False,
) -> dict[str, str] | None:
    """The first stop that holds for one unit, as `{kind, reason}`, or `None`.

    `unit_row` is the unit as `Service.board` has it; `nxt` is `Service.next_step`'s answer;
    `last` the unit's latest `end`, `integration`, `screens` or `PR_MACHINE` record, or
    `None`. `None` back means no stop, which is not the same as something to run.
    `exhausted` and `unopened` are how many steps of `last`'s stage ended `exhausted` or
    `failed` for their reply's opening; at 0 such a step stops at once. `recorded` is whether
    the step that wrote `last` was a `ship` that only records.
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

    kind = (last or {}).get("kind")
    outcome = str((last or {}).get("outcome") or "")
    # d. Gebo's integration ended `needs-person`.
    if kind == "integration" and outcome == "needs-person":
        said = "; ".join(str(x) for x in (last or {}).get("needs_person") or []) or "no reason given"
        return _stop("d", f"the last integration needs a person: {said}")
    # e. The unit's last step did not end `done`. The first time a stage other than `ship` ends
    # `exhausted` is no stop: it runs again once, and the second time stops; `ship` stops the
    # first time. The same holds, on its own count, for a prose stage that ended `failed`
    # because its reply lacked its opening. Otherwise no retry: `failed`, `cancelled`,
    # `stopped`, an integration that failed or that the autopilot started and was refused,
    # and a `ship` that ran out before `next` names one that only records the merge.
    ran_out_once = outcome == "exhausted" and (last or {}).get("stage") != "ship" and exhausted == 1
    unopened_once = _unopened(last) and unopened == 1
    skipped = skips_exhausted(nxt, last, recorded)
    if kind == "end" and outcome != "done" and not ran_out_once and not unopened_once and not skipped:
        return _stop("e", f"the last {last.get('stage')} step ended {outcome or 'without an outcome'}")
    if kind == "integration" and (
        outcome == "failed" or (outcome == "refused" and last.get("started_by") == "autopilot")
    ):
        detail = str(last.get("detail") or "")
        return _stop("e", f"the last integration was {outcome}" + (f": {detail}" if detail else ""))
    # A `pr` or `ship` the PR machine ran that failed, or whose guard refused it, stops as a
    # `failed` session does. A merge GitHub refused stops `f` on what `gh` said, so the pass
    # can still integrate a unit the refusal left behind `main`.
    if kind == PR_MACHINE and outcome != "done":
        detail = str(last.get("detail") or "") or ", ".join(str(r) for r in last.get("reasons") or [])
        if last.get("merge_refused"):
            return _stop("f", f"ship was refused: {detail or 'no reason given'}")
        return _stop("e",f"the last {last.get('stage')} was {last.get('result') or outcome}" + (f": {detail}" if detail else ""))
    # The screenshots could not be taken again before `review`, which did not start. No retry:
    # a person runs it again, and a retake that is taken lifts the stop.
    if kind == "screens" and outcome == "failed":
        return _stop("e", SCREENS_FAILED)

    # c. `ship`, while the workspace has not allowed it.
    if stage == "ship" and not may_ship:
        return _stop("c", "ship waits for a person: the autopilot may not ship in this workspace")

    # A draft whose questions are all answered is a stage to run again, not a stop.
    if not stage and nxt.get("rerun"):
        return None
    if not stage and is_waiting_on_dependency(nxt):
        return None
    # f. Nothing to run, and not because CI is still running.
    if not stage and not is_ci_pending(nxt):
        return _stop("f", action or "cos.mjs next named no stage")
    return None


# How many `impl` steps the autopilot runs after its own integration turned CI red. Not
# `COS_REVIEW_ROUNDS`, which counts review rounds.
IMPL_PER_INTEGRATION = 1
# The stop `e` once that `impl` ran and CI is still red.
STILL_RED = "CI is still red after the impl that followed the autopilot's last integration"


def since_integration(
    records: Iterable[dict[str, Any]], workspace: str, unit: str,
) -> list[dict[str, Any]] | None:
    """The unit's records after its latest `integration`, up to the first `review` `start` after
    it, or `None` when it has no `integration` or that `start` has come."""
    after: list[dict[str, Any]] | None = None
    for r in records:
        if r.get("workspace") != workspace or r.get("unit") != unit or not is_step(r):
            continue
        if r.get("kind") == "integration":
            after = []
        elif after is not None and r.get("kind") == "start" and r.get("stage") == "review":
            after = None
        elif after is not None:
            after.append(r)
    return after


def after_own_integration(
    integration: dict[str, Any] | None,
    last_integration: dict[str, Any] | None,
    after: list[dict[str, Any]] | None,
    nxt: dict[str, Any],
    exhausted: int = 0,
) -> tuple[str, dict[str, str] | None] | None:
    """What follows CI red on the autopilot's own pushed integration.

    `integration` is the board's integration block of the unit, `last_integration` its latest
    `integration` record, `after` what `since_integration` returns, `nxt` `next`'s answer.
    `("impl", None)` runs the `impl` `next` names, once; `("", stop)` is a stop `e`; `None`
    leaves the unit to the rest of the pass. Gebo is never started again. `exhausted` is how
    many `impl` steps of the unit ended `exhausted`: while it is 1, the one that ran out is
    not the one `impl`, which runs once more.
    """
    last_integration = last_integration or {}
    if started_by(last_integration) != "autopilot" or last_integration.get("outcome") != "pushed":
        return None
    red_state = (integration or {}).get("state") == "red-after-integration"
    fixing = nxt.get("stage") == "impl"
    red_next = fixing and is_ci_red(nxt)
    again = _stop("e", "CI is still red after the autopilot's last integration")
    if after is None:
        return ("", again) if red_state else None
    ran, short, by = 0, 0, ""
    for r in after:
        if r.get("stage") != "impl":
            continue
        if r.get("kind") == "start":
            by = started_by(r)
            ran += 1 if by == "autopilot" else 0
        elif r.get("kind") == "end" and r.get("outcome") == "exhausted" and by == "autopilot":
            short += 1
    if exhausted == 1:
        ran -= min(short, 1)
    if ran >= IMPL_PER_INTEGRATION and (red_state or red_next):
        return ("", _stop("e", STILL_RED))
    if red_state and fixing:
        return ("impl", None)
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
    """What a step of `stage` is counted at before it ends: the largest `max_budget_usd` any
    label can give it (`coscc/agent/policy.py` `grant_for_step`)."""
    if stage == "integrate":
        return float(grant_for("integrate").max_budget_usd or 0.0)
    return float(max(
        grant_for_step(stage, None).max_budget_usd or 0.0,
        grant_for_step(stage, labels.NOVEL).max_budget_usd or 0.0,
    ))


def estimate(stage: str) -> float:
    """What an `end` of `stage` with no `cost_usd` is counted at: its reservation, or, for a
    stage no grant gives a budget, the largest budget in the grant table (read, never copied)."""
    own = reservation(stage)
    if own > 0:
        return own
    return float(max(
        [float(g.max_budget_usd or 0.0) for g in GRANTS.values()]
        + [float(budget) for _, budget in NOVEL_CEILINGS.values()]
    ))


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


def cap_allows(spent: float, running: float, need: float, limit: float) -> bool:
    """Spent (known and estimated) + running + this step's reservation must fit under the limit."""
    return spent + running + need <= limit


# --- which of the candidates run now --------------------------------------


def files_of(plan_text: str | None) -> set[str] | None:
    """The paths under `plan.md ## Files that change`, or `None` when there are none to read.

    `None` overlaps with everything. Only tokens that look like a path count:
    `labels.listed_paths` keeps every word of the section.
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
            held[c["unit"]] = ("running", next((t["stage"] for t in taken if t["unit"] == c["unit"]), ""))
            continue
        if len(busy) >= max_parallel:
            break
        shipping = next((t for t in taken if c["stage"] == "ship" and t["stage"] == "ship"), None)
        if shipping is not None:
            held[c["unit"]] = ("ship-busy", shipping["unit"])
            continue
        crossing = next((
            t for t in taken
            if c["stage"] in CODE_STAGES and t["stage"] in CODE_STAGES and overlaps(c.get("files"), t.get("files"))
        ), None)
        if crossing is not None:
            held[c["unit"]] = ("overlap", crossing["unit"])
            continue
        if c["stage"] in ("impl", "implement") and c["unit"] not in with_pr:
            blocking = next((
                p for p in open_prs if p["unit"] != c["unit"] and overlaps(c.get("files"), p.get("files"))
            ), None)
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


def reason_for(
    nxt: dict[str, Any], stage: str, stop: dict[str, str] | None,
) -> tuple[str, str] | None:
    """Why a unit is no candidate, as `(reason, detail)`, or `None` when it is one.

    Read off what the pass already has, never made up: a case none of these explains raises.
    """
    if stage and stop is None:
        return None
    action = str(nxt.get("action") or "")
    if stop is not None:
        return ("stop", f"{stop['kind']}: {stop['reason']}")
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
    units: list[str], chosen: list[str], reasons: dict[str, tuple[str, str]],
) -> list[list[dict[str, str]]]:
    """For each of `chosen`, in order, every unit ranked above it on `units` that was not
    chosen before it this pass, each `{unit, reason, detail}`. A unit with no reason raises."""
    out: list[list[dict[str, str]]] = []
    for i, name in enumerate(chosen):
        before = set(chosen[:i])
        passed = []
        for above in units[:units.index(name)]:
            if above in before:
                continue
            if above not in reasons:
                raise ValueError(f"{name} would be started ahead of {above}, which has no reason recorded")
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


def exhausted_of(records: Iterable[dict[str, Any]], workspace: str, unit: str, stage: str) -> int:
    """How many steps of `stage` on `unit` ended `exhausted`, whoever started them and over the whole run log."""
    return sum(
        1 for r in records
        if r.get("kind") == "end" and r.get("outcome") == "exhausted" and is_step(r)
        and r.get("workspace") == workspace and r.get("unit") == unit and r.get("stage") == stage
    )


def _lacks_opening(record: dict[str, Any]) -> bool:
    """The `detail` `opening_reason` opens with (`coscc/runner/reply.py`), for the record's own
    stage; a repair turn that failed too keeps it on the first line."""
    return str(record.get("detail") or "").startswith(f"{record.get('stage')}.md lacks its opening:")


def _unopened(last: dict[str, Any] | None) -> bool:
    """`last` is a prose stage's `end` that failed because its reply lacked its opening."""
    last = last or {}
    return (
        last.get("kind") == "end" and last.get("outcome") == "failed"
        and is_prose_stage(str(last.get("stage") or "")) and _lacks_opening(last)
    )


def unopened_of(records: Iterable[dict[str, Any]], workspace: str, unit: str, stage: str) -> int:
    """How many steps of `stage` on `unit` ended `failed` because their reply lacked its
    opening, counted as `exhausted_of` counts; the two counts are apart."""
    return sum(
        1 for r in records
        if r.get("kind") == "end" and r.get("outcome") == "failed" and is_step(r)
        and r.get("workspace") == workspace and r.get("unit") == unit and r.get("stage") == stage
        and _lacks_opening(r)
    )


def answered_since_start(records: Iterable[dict[str, Any]], workspace: str, unit: str, stage: str) -> bool:
    """Whether an `answer` record of `stage` on `unit` came after its last `start`. A run
    again that ends `draft` keeps its answered questions' numbers, so `cos.mjs next` can say
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
    stage = next((s.get("stage") for s in unit_row.get("stages") or [] if s.get("file") == artifact), "")
    if not stage or stage not in (unit_row.get("after_answers") or ()):
        return False
    given = set(answered)
    asked = [q for q in unit_row.get("questions") or [] if q.get("artifact") == artifact]
    return bool(asked) and all(q.get("answered") or q.get("n") in given for q in asked)


def rerun_stop(artifact: str) -> dict[str, str]:
    """The draft has run again `MAX_RERUNS` times after its answers."""
    return _stop("reruns", f"{artifact} was run again {MAX_RERUNS} times after its answers; a person decides the next run.")


def full_stop(stage: str, max_parallel: int) -> dict[str, str]:
    """A run again that `max_parallel` alone held back. Not a wait for a person."""
    running = "1 step is" if max_parallel == 1 else f"{max_parallel} steps are"
    return _stop("full", f"{stage} waits to run again: {running} already running, the most this workspace allows.")


# --- what the intent's outcome is measured by ----------------------------

# The stages before `intent` is accepted, and those that are not a unit's stage at all.
_BEFORE = ("idea", "intent", "estimate")


def started_by(record: dict[str, Any]) -> str:
    """A record with no `started_by` reads as `person`."""
    return str(record.get("started_by") or "person")


def measure(
    records: Iterable[dict[str, Any]], workspace: str, since: str, until: str,
) -> dict[str, Any]:
    """Per unit of `workspace`, over the machine's days `since`..`until` inclusive: whether it
    reached the stop before `ship` (a `review` step ending `done` with a `pass` round), how
    many starts the autopilot made on the way, and each person's start that was not at a stop.

    A person's start is at a stop when the last `autopilot-stop` record of the unit before it
    names one, or when the unit's last `end` before it was not `done`. A mechanical
    integration writes no `start`; its `integration` record counts instead.
    """
    window = [
        r for r in records
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


def _autopilot_step(r: dict[str, Any]) -> str | None:
    """The stage of a step the autopilot started, counted as `measure` counts one, or `None`.
    An agent integration writes a `start` and an `integration`: only the `start` counts."""
    if started_by(r) != "autopilot":
        return None
    if r.get("kind") == "start":
        return str(r.get("stage") or "")
    if r.get("kind") == "integration" and r.get("mode") == "mechanical":
        return "integrate"
    return None


def measure_order(
    records: Iterable[dict[str, Any]], workspace: str, until: str,
) -> dict[str, Any]:
    """From `workspace`'s first `autopilot-pick` to the end of the machine's day `until`: how
    many steps the autopilot started, and every violation of the shortlist's order, each
    `{v, unit, stage, at, why}`.

    V1 a pick of a unit not on its own shortlist. V2 a unit's first pick in the whole log
    passing over one ranked above it with no reason, or with one outside `REASONS` (one
    picked earlier in the same `pass` is not passed over). V3 a step with no pick of its unit
    and stage since that unit's previous step.
    """
    rows = [r for r in records if r.get("workspace") == workspace]
    first = next((i for i, r in enumerate(rows) if r.get("kind") == "autopilot-pick"), None)
    if first is None:
        return {"steps": 0, "violations": [], "since": None, "until": until}
    since = rows[first].get("at")
    violations: list[dict[str, Any]] = []

    def bad(v: str, r: dict[str, Any], stage: str, why: str) -> None:
        violations.append({"v": v, "unit": str(r.get("unit") or ""), "stage": stage, "at": r.get("at"), "why": why})

    steps = 0
    seen: set[str] = set()
    in_pass: dict[str, list[str]] = {}
    picked: dict[str, set[str]] = {}
    for r in rows[first:]:
        if spend.local_day(r.get("at")) > until:
            break
        unit = str(r.get("unit") or "")
        if r.get("kind") == "autopilot-pick":
            stage = str(r.get("stage") or "")
            names = list((r.get("shortlist") or {}).get("units") or [])
            same = in_pass.setdefault(str(r.get("pass") or ""), [])
            if unit not in names:
                bad("V1", r, stage, "not on the shortlist this pick recorded")
            elif unit not in seen:
                passed = {str(p.get("unit") or ""): p for p in r.get("passed") or []}
                for above in names[:names.index(unit)]:
                    if above in passed:
                        if passed[above].get("reason") not in REASONS:
                            bad("V2", r, stage, f"passed over {above} for {passed[above].get('reason')!r}, not a known reason")
                    elif above not in same:
                        bad("V2", r, stage, f"passed over {above} with no reason")
            seen.add(unit)
            same.append(unit)
            picked.setdefault(unit, set()).add(stage)
            continue
        stage = _autopilot_step(r)
        if stage is None:
            continue
        steps += 1
        if stage not in picked.get(unit, set()):
            bad("V3", r, stage, "started with no pick since this unit's previous step")
        picked[unit] = set()
    return {"steps": steps, "violations": violations, "since": since, "until": until}


# The four clauses of the per-day outcome.
ENOUGH_INTEGRATIONS = 5


def _local_midnight_utc(day: date) -> str:
    return datetime(day.year, day.month, day.day).astimezone().astimezone(timezone.utc).isoformat()


def measure_days(
    records: Iterable[dict[str, Any]], workspace: str, since: str, until: str, cap: float,
) -> list[dict[str, Any]]:
    """Per machine's day `since`..`until` inclusive: how many integrations and failed steps
    `workspace` had, how many starts the autopilot made there, and the day's spend over every
    workspace (`spent_on`'s, no second sum). `utc_from`/`utc_to` are that day's local
    midnights in UTC.

    An `integration` record carries no cost field, so "at least one integration with no
    `cost_usd`" holds whenever `enough_integrations` does.
    """
    rows = list(records)
    out: list[dict[str, Any]] = []
    d, last = date.fromisoformat(since), date.fromisoformat(until)
    while d <= last:
        day = d.isoformat()
        here = [
            r for r in rows
            if r.get("workspace") == workspace and spend.local_day(r.get("at")) == day
        ]
        integrations = sum(1 for r in here if r.get("kind") == "integration")
        failed = sum(
            1 for r in here
            if r.get("kind") == "end" and r.get("outcome") in ("failed", "exhausted") and is_step(r)
        )
        starts = sum(1 for r in here if r.get("kind") == "start" and started_by(r) == "autopilot")
        money = spent_on(rows, day)
        spent = round(money["known"] + money["estimated"], 6)
        clauses = {
            "enough_integrations": integrations >= ENOUGH_INTEGRATIONS,
            "a_failure": failed >= 1,
            "ran": starts >= 1,
            "within": spent <= cap,
        }
        out.append({
            "day": day,
            "utc_from": _local_midnight_utc(d),
            "utc_to": _local_midnight_utc(d + timedelta(days=1)),
            "integrations": integrations, "failed": failed, "autopilot_starts": starts,
            "known": money["known"], "estimated": money["estimated"], "spent": spent, "cap": cap,
            **clauses, "met": all(clauses.values()),
        })
        d += timedelta(days=1)
    return out


# Deadlines in seconds: the next pass, which `POLL_SECONDS` stands in for, and ten minutes when
# `max_parallel` held the run back.
ON_TIME = 300.0
ON_TIME_FULL = 600.0
# What `next` says of a draft; a stop `f` in other words is the gate's refusal.
FINISH = "finish and accept"
# The classes that count against the outcome.
_MISSED = ("late", "cap", "none")


def _rerun_class(answer: dict[str, Any], later: list[dict[str, Any]]) -> tuple[str, float | None]:
    """One case of `measure_reruns`: its class and, when the stage was reached, the seconds from the answer to the first pick or start of it."""
    stage = answer.get("stage")
    at = _moment(answer.get("at"))
    full = False
    for i, r in enumerate(later):
        kind = r.get("kind")
        if kind == "autopilot-stop":
            stop = str(r.get("stop") or "")
            if stop in ("", "full"):
                full = full or stop == "full"
                continue
            if stop == "reruns":
                return "reruns", None
            if stop == "f" and not str(r.get("reason") or "").startswith(FINISH):
                return "gate", None
            if stop == "cap":
                return "cap", None
            return f"stop:{stop}", None
        if kind not in ("autopilot-pick", "start") or r.get("stage") != stage:
            continue
        moment = _moment(r.get("at"))
        after = (moment - at).total_seconds() if moment and at else None
        if after is not None and (after <= ON_TIME or (full and after <= ON_TIME_FULL)):
            return "on-time", after
        if kind == "autopilot-pick":
            for s in later[i + 1:]:
                if s.get("kind") == "start" and s.get("stage") == stage:
                    break
                if s.get("kind") == "autopilot-stop" and s.get("stop") == "f":
                    return "gate", after
        return "late", after
    return "none", None


def measure_reruns(
    records: Iterable[dict[str, Any]], workspace: str, since: str, until: str,
) -> dict[str, Any]:
    """Every `answer` record of `workspace` over the machine's days `since`..`until` that
    finished the questions of a `draft` of a shortlisted unit while the autopilot was on,
    each classed by what the unit's records after it show first.

    `on-time`, `held`, `reruns` and `gate` meet the deadline; `late`, `cap`, `stop:<kind>` and
    `none` miss it. `met` is `None` when there is no case, which is not met.
    """
    rows = [r for r in records if r.get("workspace") == workspace]
    cases: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        if r.get("kind") != "answer" or not since <= spend.local_day(r.get("at")) <= until:
            continue
        if not (r.get("completes") and r.get("autopilot") and r.get("shortlisted")) or r.get("status") != "draft":
            continue
        unit = r.get("unit")
        if r.get("held"):
            found, after = "held", None
        else:
            found, after = _rerun_class(r, [x for x in rows[i + 1:] if x.get("unit") == unit])
        cases.append({
            "unit": unit, "stage": r.get("stage"), "artifact": r.get("artifact"),
            "question": r.get("question"), "at": r.get("at"), "class": found, "after": after,
        })
    met = None if not cases else not any(c["class"] in _MISSED or c["class"].startswith("stop:") for c in cases)
    return {"workspace": workspace, "since": since, "until": until, "cases": cases, "met": met}


# `stop_for`'s e on an `exhausted` step; an `autopilot-stop` records `stage: ""`, so the stage is
# read from here, and `; <origin note>` may follow.
_RAN_OUT = re.compile(r"^the last (\S+) step ended exhausted(?:;|$)")


def measure_exhausted(
    records: Iterable[dict[str, Any]], workspace: str, since: str, until: str,
) -> dict[str, Any]:
    """Every stop of `workspace` over the machine's days `since`..`until` on an `exhausted`
    step of a stage other than `ship`, with no other `exhausted` end of that unit and stage
    before it in the window: a stop at the first time it ran out.

    `exhausted` counts the window's `exhausted` ends of a stage other than `ship`; `met` is
    `None` when there is none, which is not met.
    """
    ran_out: dict[tuple[Any, str], int] = {}
    violations: list[dict[str, Any]] = []
    exhausted = 0
    for r in records:
        if r.get("workspace") != workspace or not is_step(r) or r.get("kind") not in ("end", "autopilot-stop"):
            continue
        if not since <= spend.local_day(r.get("at")) <= until:
            continue
        if r.get("kind") == "end":
            stage = str(r.get("stage") or "")
            if r.get("outcome") == "exhausted":
                ran_out[(r.get("unit"), stage)] = ran_out.get((r.get("unit"), stage), 0) + 1
                exhausted += stage != "ship"
            continue
        found = _RAN_OUT.match(str(r.get("reason") or ""))
        if r.get("stop") != "e" or not found or found.group(1) == "ship":
            continue
        if ran_out.get((r.get("unit"), found.group(1)), 0) < 2:
            violations.append({"unit": r.get("unit"), "stage": found.group(1), "at": r.get("at")})
    met = None if not exhausted else not violations
    return {
        "workspace": workspace, "since": since, "until": until,
        "violations": violations, "exhausted": exhausted, "met": met,
    }


# `stop_for`'s e on a `failed` step, read the way `_RAN_OUT` reads an `exhausted` one.
_UNOPENED_STOP = re.compile(r"^the last (\S+) step ended failed(?:;|$)")


def measure_opening(
    records: Iterable[dict[str, Any]], workspace: str, since: str, until: str,
) -> dict[str, Any]:
    """What became of the prose steps of `workspace` over the machine's days `since`..`until`
    whose reply lacked its opening.

    `failed` is every such `end`; `repaired` every `done` one a repair turn wrote, with that
    turn's cost; `reruns` every `start` of the same unit and stage after one of `failed`;
    `stops` every stop `e` on a unit and stage whose last `end` is one of `failed`, with
    `attempt`, how many of them there were by then. `met` is `None` with neither `failed`
    nor `repaired`, else `not stops`.
    """
    failed: list[dict[str, Any]] = []
    repaired: list[dict[str, Any]] = []
    stops: list[dict[str, Any]] = []
    reruns: list[dict[str, Any]] = []
    count: dict[tuple[Any, str], int] = {}
    last_failed: dict[tuple[Any, str], bool] = {}
    waiting: set[tuple[Any, str]] = set()
    for r in records:
        if r.get("workspace") != workspace or not is_step(r):
            continue
        if not since <= spend.local_day(r.get("at")) <= until:
            continue
        kind = r.get("kind")
        key = (r.get("unit"), str(r.get("stage") or ""))
        if kind == "end":
            last_failed[key] = r.get("outcome") == "failed" and is_prose_stage(key[1]) and _lacks_opening(r)
            if last_failed[key]:
                failed.append({"unit": key[0], "stage": key[1], "at": r.get("at")})
                count[key] = count.get(key, 0) + 1
                waiting.add(key)
            elif r.get("outcome") == "done" and r.get("opening") == "repaired":
                repaired.append({"unit": key[0], "stage": key[1], "at": r.get("at"),
                                 "cost_usd": (r.get("closing") or {}).get("cost_usd")})
        elif kind == "start" and key in waiting:
            waiting.discard(key)
            reruns.append({"unit": key[0], "stage": key[1], "at": r.get("at"), "started_by": started_by(r)})
        elif kind == "autopilot-stop" and r.get("stop") == "e":
            found = _UNOPENED_STOP.match(str(r.get("reason") or ""))
            stopped = (r.get("unit"), found.group(1)) if found else None
            if stopped is not None and last_failed.get(stopped):
                stops.append({"unit": stopped[0], "stage": stopped[1], "at": r.get("at"), "attempt": count[stopped]})
    met = None if not failed and not repaired else not stops
    return {
        "workspace": workspace, "since": since, "until": until,
        "failed": failed, "repaired": repaired, "stops": stops, "reruns": reruns, "met": met,
    }
