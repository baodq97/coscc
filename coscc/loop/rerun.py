"""`rerun`: which accepted stages the board may run again, and the records it makes stale.

`rerun_later`, `rerun_closed`, `rerun_refusal`, `rerun_offers`, `rerun_stale` and `cmd_rerun`.
It reads the snapshot and prints; it writes nothing, the app records the person's rerun.
"""

from __future__ import annotations

import os

from coscc.loop import (
    REVIEW_ROUNDS,
    RERUNNABLE,
    STAGE_NAMES,
    STAGES,
    UNDEFINED,
    UNIT_RE,
    dig,
    nullish,
    stage_of,
    stringify,
    truthy,
)
from coscc.loop.model import (
    entry_of,
    present,
    read_unit,
    required,
    status_of,
    unmeasured_of,
)
from coscc.loop.rules import evaluate


def rerun_later(unit, name):
    """the stages after `name` in `STAGES` order, those without an artifact included,
    `idea` never, and `spike` only while it is required."""
    at = next((i for i, s in enumerate(STAGES) if s["name"] == name), -1)
    return [s["name"] for s in STAGES[at + 1 :] if not s.get("optional") and required(unit, s)]


def rerun_closed(unit):
    """and (c): why nothing may be run again on `unit` at all, or `None`."""
    if unit.get("shipped"):
        return "the unit is finished: it shipped"
    rejected = next((s for s in STAGES if status_of(unit, s["file"]) == "rejected"), None)
    if rejected:
        return f"the unit is closed: {rejected['file']} is rejected"
    if truthy(unit.get("hold")):
        return f"the unit is {unit['hold']['state']}"
    return None


def rerun_refusal(unit, name, limit=REVIEW_ROUNDS):
    """why `name` may not be run again on `unit`, or `None` when it may. Files only:
    no rerunnable stage's gate needs `--repo`."""
    if name not in RERUNNABLE:
        return f"{name} cannot be run again from the board — only {', '.join(RERUNNABLE)}"
    closed = rerun_closed(unit)
    if closed:
        return closed
    s = stage_of(name)
    status = status_of(unit, s["file"])
    if s.get("when") and not len(unmeasured_of(unit)["ids"]):
        return f"{name} is not required: spec.md has no [unmeasured] item"
    if not present(unit, s["file"]):
        return f"{s['file']} does not exist — {name} has not run yet"
    if not (status == "accepted" or (status == "skipped" and "skipped" in s["statuses"])):
        return f'{s["file"]} is "{status if status is not None else "null"}", not accepted'
    if truthy(unit["artifacts"][s["file"]].get("stale")):
        return f"{s['file']} is already stale — run {name} from the next step"
    g = evaluate(unit, name, probe=None, limit=limit)
    return None if g["ok"] else "; ".join(g["need"])


def rerun_offers(unit, limit=REVIEW_ROUNDS):
    """Every stage `rerun_refusal` lets through, each with what it makes run again."""
    return [
        {"stage": stage, "later": rerun_later(unit, stage)}
        for stage in RERUNNABLE
        if rerun_refusal(unit, stage, limit) is None
    ]


def rerun_stale(unit, name, known):
    """`{file: record}`: the record each artifact of `name` and the stages after it holds now, for
    those present that have one. The rerun's row names them; one still at it is stale."""
    files = [stage_of(n)["file"] for n in [name, *rerun_later(unit, name)]]
    records = {f: dig(known, "artifacts", f, "record") for f in files if present(unit, f)}
    return {f: r for f, r in records.items() if r not in (None, UNDEFINED)}


def cmd_rerun(unit_name, stage, cos_dir, limit, state, out, err):
    """`cmdRerun`: `{unit, offers, why}` with no `stage`, else `{unit, stage, later, stale}`
    and exit 0, or the reason and exit 1. Exit 2 is misuse, as `gate`'s."""
    if not unit_name:
        err(f"usage: python -m coscc.loop rerun <NNNN_slug> [{'|'.join(RERUNNABLE)}]")
        return 2
    if not UNIT_RE.fullmatch(unit_name):
        err(f'Invalid unit name "{unit_name}": expected NNNN_slug.')
        return 2
    dir_ = os.path.join(cos_dir, unit_name)
    if not os.path.exists(dir_):
        err(f"No such work unit: {unit_name}")
        return 2
    unit = read_unit(dir_, unit_name, state)
    if stage is None:
        offers = rerun_offers(unit, limit)
        why = (
            "" if offers else nullish(rerun_closed(unit), "no accepted stage can be run again now")
        )
        out(stringify({"unit": unit_name, "offers": offers, "why": why}))
        return 0
    if not stage_of(stage):
        err(f'unknown stage "{stage}" — use one of {", ".join(STAGE_NAMES)}')
        return 2
    refused = rerun_refusal(unit, stage, limit)
    if refused:
        err(f"{stage} cannot be run again for {unit_name}: {refused}")
        return 1
    known = entry_of(state, state["workspace"], unit_name)
    out(
        stringify(
            {
                "unit": unit_name,
                "stage": stage,
                "later": rerun_later(unit, stage),
                "stale": rerun_stale(unit, stage, known),
            }
        )
    )
    return 0


def run(args, out, err) -> int:
    rest = args.rest
    return cmd_rerun(
        rest[0] if rest else None,
        rest[1] if len(rest) > 1 else None,
        args.cos_dir,
        args.limit,
        args.state,
        out,
        err,
    )
