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
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from coscc.agent import labels
from coscc.agent import precedent
from coscc.runlog import spend
from coscc.agent.policy import GRANTS, NOVEL_CEILINGS, grant_for, grant_for_step, is_prose_stage

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

# `0044`: Jera writes `start` and `end` on the unit it answers for, under a stage that is none
# of the loop's. Its lines are not the unit's last step (R6 e), a start, or a failed step.
# Since `0101` R1 the autopilot starts Jera too, as a candidate like a stage, and still none
# of its lines is a step.
NOT_STEPS = ("precedent",)
JERA = "precedent"

# R6, the one stop the spec adds beside them, `0104` R3's empty shortlist, and `0106` R3's
# and R5's: a draft run again as often as it may, and one waiting for a free place.
STOP_KINDS = ("a", "b", "c", "d", "e", "f", "cap", "shortlist", "reruns", "full")

# `0104` R6. Why a unit ranked higher on the shortlist was passed over, and nothing else.
REASONS = ("held", "finished", "closed", "stop", "ci", "running", "overlap", "ship-busy", "missing", "dependency", "overlap-pr")
# `0111`. The stop `e` of a unit whose screenshots could not be taken again before `review`.
SCREENS_FAILED = "the screenshots could not be taken again before review"
# `0104` R3. The workspace's stop line when there is no shortlist to follow.
NO_SHORTLIST = "Nothing is on the shortlist, so the autopilot starts nothing."

_NUMBER = re.compile(r"^(\d+)")


# `0136` R11: what `next` or the gate said is read by code, from `reasons` (`guards.REASONS`),
# never from its words, which are a person's to read and `cos.mjs`'s to change.
def said(answer: Any, reason: str) -> bool:
    """Whether `reason` is among the codes of `answer`: `next`'s dict, or anything with
    `reasons` — a `board.Gate`, or the exception a refused step raised with it."""
    if isinstance(answer, dict):
        codes = answer.get("reasons") or ()
    else:
        codes = getattr(answer, "reasons", None) or ()
    return reason in codes


def is_waiting_on_dependency(answer: Any) -> bool:
    """`0040` R7: `next` holds `impl` back until a dependency merges. Not a stop, like CI
    pending: nothing a person does here would move it, and the next pass asks again."""
    return said(answer, "waiting-on")


def is_ci_pending(answer: Any) -> bool:
    """R6: the gate or `next` is waiting on CI. Not a stop; R5 d asks again."""
    return said(answer, "ci-pending")


def is_ci_red(answer: Any) -> bool:
    """`0124` R2: `next` sends the unit back to `impl` because CI is red."""
    return said(answer, "ci-red")


def is_recording_ship(answer: Any) -> bool:
    """`0126` R4: `next` or the gate names a `ship` that only records a merge already made."""
    return said(answer, "recording-ship")


def needs_a_person(answer: Any) -> bool:
    """R6 b: `next` stops for a person — review used its rounds, a spike failed too often, a
    red check no impl can fix, a pass left closed twice on one head, and (`0136` R14) a spec
    or plan skipped by no person or delegate of theirs."""
    return said(answer, "needs-person") or said(answer, "awaits-person") or said(answer, "agent-cannot-skip")


def is_over(answer: Any) -> bool:
    """`next` offers nothing because the unit is finished or closed."""
    return said(answer, "finished") or said(answer, "closed")


# `0136` review round 1, F2. The record a `pr` or `ship` the PR machine ran leaves in place of
# an `end`: `outcome` `done` or `failed`, and the machine's `result`, `reasons` and `detail`.
PR_MACHINE = "prmachine"


def is_step(record: dict[str, Any]) -> bool:
    """False for a line of a session that is no stage of `cos.mjs`'s loop (`NOT_STEPS`)."""
    return record.get("stage") not in NOT_STEPS


def _stop(kind: str, reason: str) -> dict[str, str]:
    return {"kind": kind, "reason": reason}


def open_questions(unit_row: dict[str, Any]) -> list[dict[str, Any]]:
    """Every unanswered question of the counted artifact (`cos.mjs` `unitQuestions`): the stop
    `a`, and since `0113` R4 the `questions` record a step that ends `done` leaves."""
    return [q for q in unit_row.get("questions") or [] if q.get("counted") and not q.get("answered")]


def _n(value: Any) -> Any:
    """A question's number as the board has it: an int where it reads as one."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return value


def unasked(
    unit_row: dict[str, Any], records: Iterable[dict[str, Any]], workspace: str,
) -> list[dict[str, Any]]:
    """`0101` R2. The open questions of the stop `a` Jera has not been asked, `[{artifact, n}]`.

    Only those `open_questions` counts that Jera may be given (`precedent.asked`: never
    `review.md`). A question was asked when, after the latest `end` of the stage that writes
    its artifact, the run log holds (a) a `precedent` row for it, whatever its verdict, or (b)
    a `start` of Jera whose `asked` names it. (b) is wider than R2's words on purpose: a
    session that failed, was cancelled or ran out writes no `precedent` row, and without it
    every pass would open that session again (`plan.md` step 4).
    """
    name = str(unit_row.get("name") or "")
    may = {(q["artifact"], q["n"]) for q in precedent.asked(unit_row)}
    open_ = [
        (str(q.get("artifact") or ""), _n(q.get("n"))) for q in open_questions(unit_row)
        if (str(q.get("artifact") or ""), _n(q.get("n"))) in may
    ]
    writes = {str(s.get("file") or ""): str(s.get("stage") or "") for s in unit_row.get("stages") or []}
    asked: set[tuple[str, Any]] = set()
    for r in records:
        if r.get("workspace") != workspace or r.get("unit") != name:
            continue
        kind = r.get("kind")
        if kind == "end" and is_step(r):
            stage = str(r.get("stage") or "")
            asked = {(a, n) for (a, n) in asked if writes.get(a) != stage}
        elif kind == "precedent":
            asked.add((str(r.get("artifact") or ""), _n(r.get("n"))))
        elif kind == "start" and r.get("stage") == JERA:
            for item in r.get("asked") or []:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    asked.add((str(item[0]), _n(item[1])))
    return [{"artifact": a, "n": n} for (a, n) in open_ if (a, n) not in asked]


def _listed(questions: Iterable[dict[str, Any]]) -> str:
    return ", ".join(f"{q.get('artifact')} question {q.get('n')}" for q in questions)


def waiting_for_you(open_: list[dict[str, Any]]) -> dict[str, str]:
    """`0101` R2: the stop `a` once Jera has been asked every open question it may be. `open_`
    is every open question, `review.md`'s included, which Jera is never asked."""
    count = "1 question waits" if len(open_) == 1 else f"{len(open_)} questions wait"
    return _stop("a", f"{count} for you: {_listed(open_)}")


# `0101` R5 point 3. The stop `a` when the prompt would cost more than one session may.
STORE_PAST_CEILING = "The precedent store is past what one Jera session may cost, so its questions wait for you."


def skips_exhausted(nxt: dict[str, Any], last: dict[str, Any] | None, recorded: bool) -> bool:
    """`0126` R1–R3: a `ship` that ran out of turns is no stop when `next` names a `ship` that
    only records the merge, unless the step that ran out was itself one (`recorded`)."""
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
    """The first of R6's stops that holds for one unit, as `{kind, reason}`, or `None`.

    `unit_row` is the unit as `Service.board` has it; `nxt` is `Service.next_step`'s answer;
    `last` the unit's latest `end`, `integration`, `screens` (`0111`) or `PR_MACHINE` (`0136`)
    record, or `None`. `None` back means no
    stop, which is not the same as something to run: a finished, rejected or held unit, and
    one waiting on CI, have neither. `exhausted` is how many steps of `last`'s stage ended
    `exhausted` (`exhausted_of`); left at 0, an exhausted step stops as before `0120`.
    `unopened` is how many ended `failed` for their reply's opening (`unopened_of`); left at
    0, such a step stops as before `0127`.
    `recorded` is whether the step that wrote `last` was a `ship` that only records (`0126`
    R3); left `False`, an exhausted `ship` before a recording one is no stop (`skips_exhausted`).
    """
    stage = str(nxt.get("stage") or "")
    action = str(nxt.get("action") or "")
    if nxt.get("hold"):
        return None
    if not stage and is_over(nxt):
        return None

    # a. Every unanswered question of the counted artifact (`cos.mjs` `unitQuestions`).
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
    # d. Gebo's integration ended `needs-person`: the object it handed back said so (`0136` R7).
    if kind == "integration" and outcome == "needs-person":
        said = "; ".join(str(x) for x in (last or {}).get("needs_person") or []) or "no reason given"
        return _stop("d", f"the last integration needs a person: {said}")
    # e. The unit's last step did not end `done`. The first time a stage other than `ship` ends
    # `exhausted` is no stop: it runs again once, and the second time stops; `ship` stops the
    # first time (`0120 intent.md ## Answers`, câu 3, 4). Since `0127` R8 the same holds, on a
    # count of its own, for a prose stage that ended `failed` because its reply lacked its
    # opening; a `failed` for any other reason still stops, even after one of those.
    # Otherwise no retry (`spec.md ## Answers`, câu 1): `failed`, `cancelled`, `stopped`, and
    # an integration that failed or that the autopilot started and was refused. `0126`: nor a
    # `ship` that ran out before `next` names one that only records the merge, however many times.
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
    # `0136` review round 1, F2: a `pr` or `ship` the PR machine ran and that failed, or whose
    # guard refused it, stops as a session that ended `failed` did; no retry, as above.
    # Review round 2, F6: a merge GitHub refused once the machine had requested it stops `f` on
    # what `gh` said, as `0112` R7's refused `ship` did, so a unit the refusal left behind `main`
    # is still integrated by the pass (`0112` R1) rather than awaiting a person.
    if kind == PR_MACHINE and outcome != "done":
        detail = str(last.get("detail") or "") or ", ".join(str(r) for r in last.get("reasons") or [])
        if last.get("merge_refused"):
            return _stop("f", f"ship was refused: {detail or 'no reason given'}")
        return _stop("e",f"the last {last.get('stage')} was {last.get('result') or outcome}" + (f": {detail}" if detail else ""))
    # `0111`: the screenshots could not be taken again before `review`, which did not start.
    # No retry, as above: a person runs it again, and a retake that is taken lifts the stop.
    if kind == "screens" and outcome == "failed":
        return _stop("e", SCREENS_FAILED)

    # c. `ship`, while the workspace has not allowed it.
    if stage == "ship" and not may_ship:
        return _stop("c", "ship waits for a person: the autopilot may not ship in this workspace")

    # `0106` R2: a draft whose questions are all answered is a stage to run again, not a
    # stop. Whether it may be is the pass's to say (`reruns_of`, `answered_since_start`).
    if not stage and nxt.get("rerun"):
        return None
    if not stage and is_waiting_on_dependency(nxt):
        return None
    # f. Nothing to run, and not because CI is still running.
    if not stage and not is_ci_pending(nxt):
        return _stop("f", action or "cos.mjs next named no stage")
    return None


# `0124` R2, R6. How many `impl` steps the autopilot runs after its own integration turned CI
# red (`intent.md ## Answers`, câu 1). Not `COS_REVIEW_ROUNDS`, which counts review rounds.
IMPL_PER_INTEGRATION = 1
# `0124` R2, R8. The stop `e` once that `impl` ran and CI is still red.
STILL_RED = "CI is still red after the impl that followed the autopilot's last integration"


def since_integration(
    records: Iterable[dict[str, Any]], workspace: str, unit: str,
) -> list[dict[str, Any]] | None:
    """`0124`. The unit's records after its latest `integration`, up to the first `review`
    `start` after it — or `None` when it has no `integration`, or that `start` has come."""
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
    """`0124` R1–R3: what follows CI red on the autopilot's own pushed integration.

    `integration` is the board's integration block of the unit, `last_integration` its latest
    `integration` record, `after` what `since_integration` returns for it, `nxt` `next`'s
    answer. `("impl", None)` runs the `impl` `next` names, once (R1); `("", stop)` is a stop
    `e` (R2, R3 b); `None` leaves the unit to the rest of the pass — a person's integration,
    or one CI is not red on. Gebo is never started again: no retry is the rule (`0043`
    `spec.md ## Answers`, câu 1). `exhausted` is how many `impl` steps of the unit ended
    `exhausted` (`exhausted_of`): while it is 1, the autopilot's `impl` that ran out is not
    the one `impl`, which runs once more (`0120 intent.md ## Answers`, câu 4; review F3).
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


def spent_today(records: Iterable[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """`spent_on` the machine's day of `now`. An `end` with no `cost_usd` is counted at its
    estimate, not as the cap reached (`0105`)."""
    return spent_on(records, today(now))


def reservation(stage: str) -> float:
    """What a step of `stage` is counted at before it ends: the largest `max_budget_usd` any
    label can give it (`coscc/agent/policy.py` `grant_for_step`). Jera's is the most any of
    its sessions may cost (`0101` R3, R5): the pass holds a picked one at its own ceiling."""
    if stage == JERA:
        return precedent.PRECEDENT_MAX_USD
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
    process holds (`(workspace, unit, stage)`) that has not written its `start` yet. An open
    `start` of Jera counts at the ceiling it recorded (`0101` C13)."""
    opened = open_starts(records, now)
    total = sum(_held_at(r) for r in opened.values())
    for workspace, unit, stage in active:
        if (workspace, unit) not in opened:
            total += reservation(stage)
    return total


def _held_at(start: dict[str, Any]) -> float:
    stage = str(start.get("stage") or "")
    ceiling = start.get("max_budget_usd")
    if stage == JERA and isinstance(ceiling, (int, float)) and not isinstance(ceiling, bool):
        return float(ceiling)
    return reservation(stage)


def cap_allows(spent: float, running: float, need: float, limit: float) -> bool:
    """R7: spent (known and estimated) + running + this step's reservation must fit under
    the limit."""
    return spent + running + need <= limit


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
    room: float,
    open_prs: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """R8 a–f and R7 over the steps that could start, highest on the shortlist first
    (`0104` R4).

    Each candidate and each running entry is `{unit, stage, files}`, and a candidate also
    carries `rank`, its place on the shortlist, and `need`, its reservation. `room` is the
    money left under the cap. Returns `{"chosen": [...], "capped": [...], "held": {...}}`:
    what to start, what the cap alone held back, and `(reason, detail)` for each candidate
    another rule held back (`0104` R6). What `max_parallel` held back is in none of them:
    nothing after it is chosen, so nothing passes over it.

    `open_prs` is `0136` R22: `{unit, number, files}` for each pull request of the workspace
    that is open and not merged, `files` `None` when its diff could not be read. An `impl` of
    a unit with no pull request of its own waits while one of another unit's touches a file
    its plan names, so two open pull requests on one file never wait on each other.
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


# --- `0104`, the shortlist's order --------------------------------------------


def reason_for(
    nxt: dict[str, Any], stage: str, stop: dict[str, str] | None,
) -> tuple[str, str] | None:
    """`0104` R6. Why a unit is no candidate, as `(reason, detail)`, or `None` when it is one.

    Read off what the pass already has — `next`'s answer, the stage it settled on and the
    stop it found — and never made up (spec C3): a case none of these explains raises.
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
    """`0104` R6. For each of `chosen`, in order, every unit ranked above it on `units` that
    was not chosen before it this pass, each `{unit, reason, detail}`. A unit with no reason
    raises rather than be passed over for none."""
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


# --- `0106`, a draft whose questions are all answered -------------------------

# R3. How often one artifact runs again after its answers before a person decides the next
# run (`intent.md ## Answers`, câu 4).
MAX_RERUNS = 2


def reruns_of(records: Iterable[dict[str, Any]], workspace: str, unit: str, stage: str) -> int:
    """R3. How many `start`s of `stage` on `unit` followed an earlier one with at least one
    `answer` record of the same unit and stage between them. Whoever started either step
    counts (`intent.md ## Answers`, câu 7), so `started_by` is not read."""
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
    """`0120`. How many steps of `stage` on `unit` ended `exhausted`. Whoever started them,
    out of turns or out of budget alike, and over the whole run log (spec C1, C2)."""
    return sum(
        1 for r in records
        if r.get("kind") == "end" and r.get("outcome") == "exhausted" and is_step(r)
        and r.get("workspace") == workspace and r.get("unit") == unit and r.get("stage") == stage
    )


def _lacks_opening(record: dict[str, Any]) -> bool:
    """`0127` R8. The `detail` `opening_reason` opens with (`coscc/runner/reply.py`), for the
    record's own stage; a repair turn that failed too keeps it on the first line."""
    return str(record.get("detail") or "").startswith(f"{record.get('stage')}.md lacks its opening:")


def _unopened(last: dict[str, Any] | None) -> bool:
    """`0127` R8. `last` is a prose stage's `end` that failed because its reply lacked its opening."""
    last = last or {}
    return (
        last.get("kind") == "end" and last.get("outcome") == "failed"
        and is_prose_stage(str(last.get("stage") or "")) and _lacks_opening(last)
    )


def unopened_of(records: Iterable[dict[str, Any]], workspace: str, unit: str, stage: str) -> int:
    """`0127` R8. How many steps of `stage` on `unit` ended `failed` because their reply
    lacked its opening. Whoever started them, and over the whole run log, as `exhausted_of`
    counts; the two counts are apart."""
    return sum(
        1 for r in records
        if r.get("kind") == "end" and r.get("outcome") == "failed" and is_step(r)
        and r.get("workspace") == workspace and r.get("unit") == unit and r.get("stage") == stage
        and _lacks_opening(r)
    )


def answered_since_start(records: Iterable[dict[str, Any]], workspace: str, unit: str, stage: str) -> bool:
    """R3. Whether an `answer` record of `stage` on `unit` came after its last `start`. A run
    again that ends `draft` keeps its answered questions' numbers (`runner._ANSWERS_ADVICE`),
    so `cos.mjs next` can say `rerun` with no new answer behind it; without this, `reruns_of`
    would stay where it was and the autopilot would run it again on every pass."""
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
    """R4. Whether `artifact`, of a stage whose answered draft runs again, has no numbered
    question left unanswered: each is answered on the board read `unit_row` came from, or its
    number is in `answered`, the blocks written since that read up to and including this one.
    Those stages are `unit_row["after_answers"]`, as `cos.mjs` listed them on that read
    (`0115` R4); a row without the list completes nothing."""
    stage = next((s.get("stage") for s in unit_row.get("stages") or [] if s.get("file") == artifact), "")
    if not stage or stage not in (unit_row.get("after_answers") or ()):
        return False
    given = set(answered)
    asked = [q for q in unit_row.get("questions") or [] if q.get("artifact") == artifact]
    return bool(asked) and all(q.get("answered") or q.get("n") in given for q in asked)


def rerun_stop(artifact: str) -> dict[str, str]:
    """R3: the draft has run again `MAX_RERUNS` times after its answers."""
    return _stop("reruns", f"{artifact} was run again {MAX_RERUNS} times after its answers; a person decides the next run.")


def full_stop(stage: str, max_parallel: int) -> dict[str, str]:
    """R5: a run again that `max_parallel` alone held back. Not a wait for a person."""
    running = "1 step is" if max_parallel == 1 else f"{max_parallel} steps are"
    return _stop("full", f"{stage} waits to run again: {running} already running, the most this workspace allows.")


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

    Since `0101` R11 each unit also carries what `_jera_of` counts, read before Jera's lines
    are left out.
    """
    window = [
        r for r in records
        if r.get("workspace") == workspace and since <= spend.local_day(r.get("at")) <= until
    ]
    rows = [r for r in window if is_step(r)]
    jera = _jera_of(window)
    per_unit: dict[str, dict[str, Any]] = {}
    stopped: dict[str, str] = {}
    last_end: dict[str, str] = {}

    def blank(unit: str) -> dict[str, Any]:
        return {"unit": unit, "reached": False, "autopilot": 0, "person": 0, "outside": [],
                **(jera.get(unit) or _no_jera())}

    for unit in jera:
        per_unit[unit] = blank(unit)
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


def _no_jera() -> dict[str, Any]:
    return {"to_person": 0, "by_jera": 0, "jera_cost_usd": 0.0, "unpicked_precedent": []}


def _jera_of(window: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`0101` R11, per unit of `window`:

    - `to_person`: the questions whose latest `precedent` row said `needs-person`, plus every
      `answer` not written through Jera;
    - `by_jera`: every `answer` written through Jera;
    - `jera_cost_usd`: what Jera's `end`s cost, those that say;
    - `unpicked_precedent`: each `start` of Jera with no `autopilot-pick` of Jera on the unit
      since the one before it, `{at, started_by}` — a person's press, or a pick not recorded.

    An answer written at a terminal leaves no `answer` row, so it is not counted (`spec.md` C6).
    """
    out: dict[str, dict[str, Any]] = {}
    verdict: dict[tuple[str, str, Any], str] = {}
    picked: dict[str, bool] = {}
    for r in window:
        unit = str(r.get("unit") or "")
        if not unit:
            continue
        kind, stage = r.get("kind"), r.get("stage")
        if kind == "precedent":
            verdict[(unit, str(r.get("artifact") or ""), _n(r.get("n")))] = str(r.get("verdict") or "")
            out.setdefault(unit, _no_jera())
        elif kind == "answer":
            u = out.setdefault(unit, _no_jera())
            u["by_jera" if r.get("via") == precedent.VIA else "to_person"] += 1
        elif kind == "end" and stage == JERA and r.get("cost_usd") is not None:
            u = out.setdefault(unit, _no_jera())
            u["jera_cost_usd"] = round(u["jera_cost_usd"] + float(r["cost_usd"]), 6)
        elif kind == "autopilot-pick" and stage == JERA:
            picked[unit] = True
        elif kind == "start" and stage == JERA:
            u = out.setdefault(unit, _no_jera())
            if not picked.get(unit):
                u["unpicked_precedent"].append({"at": r.get("at"), "started_by": started_by(r)})
            picked[unit] = False
    for (unit, _, _), said in verdict.items():
        if said == precedent.PERSON:
            out[unit]["to_person"] += 1
    return out


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
    """`0104` R8. From `workspace`'s first `autopilot-pick` to the end of the machine's day
    `until`: how many steps the autopilot started, and every violation of the shortlist's
    order, each `{v, unit, stage, at, why}`.

    V1 a pick of a unit not on its own shortlist. V2 a unit's first pick in the whole log
    passing over one ranked above it with no reason, or with one outside `REASONS` — one
    picked earlier in the same `pass` is not passed over. V3 a step with no pick of its unit
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


# The outcome of `0105`'s intent, per day: its four clauses.
ENOUGH_INTEGRATIONS = 5


def _local_midnight_utc(day: date) -> str:
    return datetime(day.year, day.month, day.day).astimezone().astimezone(timezone.utc).isoformat()


def measure_days(
    records: Iterable[dict[str, Any]], workspace: str, since: str, until: str, cap: float,
) -> list[dict[str, Any]]:
    """`0105`. Per machine's day `since`..`until` inclusive: how many integrations and
    failed steps `workspace` had, how many starts the autopilot made there, and the day's
    spend over every workspace, since the cap is the app's. The spend is `spent_on`'s, no
    second sum. `utc_from`/`utc_to` are that day's local midnights in UTC, since the intent
    counts UTC days and the cap does not.

    An `integration` record carries no cost field at all, so the intent's clause "at least
    one integration with no `cost_usd`" holds whenever `enough_integrations` does.
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


# `0106` R7. The intent's deadlines, in seconds: the next pass, which `POLL_SECONDS` stands in
# for (spec C3), and ten minutes when `max_parallel` held the run back.
ON_TIME = 300.0
ON_TIME_FULL = 600.0
# What `next` says of a draft; a stop `f` in other words is the gate's refusal.
FINISH = "finish and accept"
# The classes that count against the intent's outcome.
_MISSED = ("late", "cap", "none")


def _rerun_class(answer: dict[str, Any], later: list[dict[str, Any]]) -> tuple[str, float | None]:
    """One case of `measure_reruns`: its class and, when the stage was reached, the seconds
    from the answer to the first pick or start of it."""
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
    """`0106` R7. Every `answer` record of `workspace` over the machine's days `since`..`until`
    that finished the questions of a `draft` of a shortlisted unit while the autopilot was on,
    each classed by what the unit's records after it show first.

    `on-time`, `held`, `reruns` and `gate` meet the intent; `late`, `cap`, `stop:<kind>` and
    `none` miss it. `met` is `None` when there is no case, which is not met (`intent.md`).
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
# read from here, and `; <origin note>` may follow (`service_autopilot`, `0112` R4).
_RAN_OUT = re.compile(r"^the last (\S+) step ended exhausted(?:;|$)")


def measure_exhausted(
    records: Iterable[dict[str, Any]], workspace: str, since: str, until: str,
) -> dict[str, Any]:
    """`0120` R6. Every stop of `workspace` over the machine's days `since`..`until` on an
    `exhausted` step of a stage other than `ship`, with no other `exhausted` end of that unit
    and stage before it in the window: a stop at the first time it ran out, which R1 forbids.

    `exhausted` counts the window's `exhausted` ends of a stage other than `ship`; `met` is
    `None` when there is none, which is not met (`intent.md ## Proposed outcome`).
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
    """`0127` R9. What became of the prose steps of `workspace` over the machine's days
    `since`..`until` whose reply lacked its opening.

    `failed` is every such `end`; `repaired` every `done` one a repair turn wrote, with that
    turn's cost; `reruns` every `start` of the same unit and stage after one of `failed`;
    `stops` every stop `e` on a unit and stage whose last `end` is one of `failed`, with
    `attempt`, how many of them there were by then. `met` is `None` with neither `failed`
    nor `repaired`, which is not met (`intent.md ## Proposed outcome`), else `not stops`.
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
