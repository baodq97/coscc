"""`0074`. Which units are waiting, what each is worth and costs, and the order to take them.

Display only (`intent.md ## Answers, câu 5`): nothing here reaches `cos.mjs`, a gate, `next`
or the run button. What it reads is the board (`board.read`'s units) and four kinds of
run-log record; what it returns is a fold of them. There is no stored "current shortlist"
anywhere — the last record wins, the way `transitions` are read (`spec.md`, Design).

Every function here is pure: no file, no database, no session. The service reads, calls in,
and writes. A record that does not parse — a hand edit, a field of the wrong type — is
skipped and named in `problems`; nothing here raises on data, because `fold` runs on every
board read and a raise there is a 500 on every screen (`plan.md` Risk 1).

The numbers below are chosen, not measured (`spec.md` C5).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable

from coscc import journal
from coscc.hold import _line_problem

VALUES = range(1, 6)
EFFORTS = ("S", "M", "L")
# The first two are symmetric, the last two have a direction: `unit` replaces `other`,
# `unit` depends on `other` (`spec.md ## Answers, câu 1` added the fourth).
RELATIONS = ("liên quan", "trùng", "thay thế", "phụ thuộc")
SYMMETRIC = ("liên quan", "trùng")
OPS = ("add", "remove")
SHORTLIST_MAX = 7
BASIS_MAX = 500
TERCILE_MIN = 6
SIMILAR_MAX = 3
# `intent.md ## Answers, câu 4`, word for word; `check_estimate` matches an agent's basis on these.
VALUE_GOALS = ("bớt can thiệp tay", "bớt chi phí", "nỗi đau đã gặp thật")
KINDS = ("estimate-value", "relation", "shortlist", "estimate")
AGENT_PREFIX = "agent:"


def is_agent(by: str) -> bool:
    return str(by or "").startswith(AGENT_PREFIX)


# -- the set being ranked ---------------------------------------------------------------


def in_backlog(unit: dict[str, Any]) -> bool:
    """R1. Has an idea or intent, is not finished, closed (rejected) or dropped."""
    has_start = any(
        r.get("stage") in ("idea", "intent") and (r.get("status") or "not started") != "not started"
        for r in unit.get("stages") or []
    )
    nxt = str(unit.get("next") or "")
    if not has_start or nxt == "finished" or nxt.startswith("closed"):
        return False
    return (unit.get("hold") or {}).get("state") != "dropped"


def _status_of(unit: dict[str, Any]) -> str:
    """`finished`, `dropped`, `rejected`, `backlog` or `other` — for dependency warnings."""
    nxt = str(unit.get("next") or "")
    if nxt == "finished":
        return "finished"
    if (unit.get("hold") or {}).get("state") == "dropped":
        return "dropped"
    if nxt.startswith("closed"):
        return "rejected"
    return "backlog" if in_backlog(unit) else "other"


# -- effort, from the run log --------------------------------------------------------------


def _unknown_cost(rows: list[dict[str, Any]]) -> bool:
    """`0092` R11. At least one run of the unit ended with no cost reported."""
    return journal.totals_of(rows)["unknown"] > 0


def measured(timelines: dict[str, list[dict[str, Any]]], units: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """R4, R5. `{unit: {cost_usd, turns}}` for every finished unit with a reported cost.

    `0092` R11: a unit with any run whose cost is unknown is left out -- neither met nor
    missed, and in no tercile or median (spec C8). `undetermined` names those.
    """
    out: dict[str, dict[str, Any]] = {}
    for u in units:
        if u.get("next") != "finished":
            continue
        rows = timelines.get(u.get("name") or "", [])
        if not any(r.get("reported") for r in rows) or _unknown_cost(rows):
            continue
        total = journal.totals_of(rows)
        out[u["name"]] = {"cost_usd": total["cost_usd"], "turns": total["turns"]}
    return out


def undetermined(timelines: dict[str, list[dict[str, Any]]], units: Iterable[dict[str, Any]]) -> list[str]:
    """`0092` R11. The finished units `measured` leaves out for a cost nobody knows, by name."""
    return sorted(
        u["name"] for u in units
        if u.get("next") == "finished" and _unknown_cost(timelines.get(u.get("name") or "", []))
    )


def terciles(values: Iterable[float]) -> tuple[float, float]:
    """`(t1, t2)`: at or under t1 is `S`, at or under t2 is `M`, the rest `L`."""
    v = sorted(values)
    n = len(v)
    return v[math.ceil(n / 3) - 1], v[math.ceil(2 * n / 3) - 1]


def _band(value: float, cuts: tuple[float, float]) -> str:
    return "S" if value <= cuts[0] else "M" if value <= cuts[1] else "L"


def _median(values: list[float]) -> float:
    v = sorted(values)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def effort_from(similar: Iterable[str], found: dict[str, dict[str, Any]], undetermined: int = 0) -> dict[str, Any]:
    """R4, R5. `{effort, effort_source, effort_basis}`; `effort` is `None` for a guess.

    `0092` R11: how many finished units were left out for an unknown cost is said in
    `effort_basis`, in English (spec C9), so the smaller set is never hidden.
    """
    got = _effort_from(similar, found)
    if undetermined > 0:
        got["effort_basis"] += f"; {undetermined} finished units with an unknown cost left out"
    return got


def _effort_from(similar: Iterable[str], found: dict[str, dict[str, Any]]) -> dict[str, Any]:
    named = [s for s in similar if s in found]
    if len(found) < TERCILE_MIN:
        return {"effort": None, "effort_source": "guess",
                "effort_basis": f"chỉ có {len(found)} unit finished có báo chi phí, dưới {TERCILE_MIN}"}
    if not named:
        return {"effort": None, "effort_source": "guess",
                "effort_basis": "không unit tương tự nào được nêu có chi phí đo được"}
    cost_cuts = terciles(f["cost_usd"] for f in found.values())
    turn_cuts = terciles(f["turns"] for f in found.values())
    cost = _median([found[s]["cost_usd"] for s in named])
    turns = _median([found[s]["turns"] for s in named])
    by_cost, by_turns = _band(cost, cost_cuts), _band(turns, turn_cuts)
    effort = max(by_cost, by_turns, key=EFFORTS.index)
    listed = ", ".join(f"{s} (${found[s]['cost_usd']:.2f}, {found[s]['turns']} lượt)" for s in named)
    return {
        "effort": effort, "effort_source": "measured",
        "effort_basis": (
            f"tương tự: {listed}; trung vị ${cost:.2f} → {by_cost}, {turns:g} lượt → {by_turns} "
            f"(tercile chi phí ≤${cost_cuts[0]:.2f} S, ≤${cost_cuts[1]:.2f} M; "
            f"lượt ≤{turn_cuts[0]} S, ≤{turn_cuts[1]} M; trên {len(found)} unit)"
        ),
    }


# -- checks -----------------------------------------------------------------------------


def check_estimate(value: Any, effort: Any, basis: Any, by: Any, agent: bool) -> str:
    """R2, R3, R7. The first reason this estimate is refused, or `""`."""
    if isinstance(value, bool) or not isinstance(value, int) or value not in VALUES:
        return f"value must be a whole number 1–5, got {value!r}"
    if effort not in EFFORTS:
        return f"effort must be one of {', '.join(EFFORTS)}, got {effort!r}"
    if not isinstance(basis, str) or not basis.strip():
        return "the basis is empty"
    if len(basis) > BASIS_MAX:
        return f"the basis is {len(basis)} characters; at most {BASIS_MAX}"
    said = _line_problem("name", str(by or "")) if isinstance(by, str) else "the name is empty"
    if said:
        return said
    if agent != is_agent(by):
        return f"a person's name may not start with {AGENT_PREFIX!r}" if not agent else "an agent's name must start with agent:"
    if agent and not any(goal in basis.casefold() for goal in VALUE_GOALS):
        return "the basis names none of: " + "; ".join(VALUE_GOALS)
    return ""


def _relation_key(rtype: str, unit: str, other: str) -> tuple[str, str, str]:
    if rtype in SYMMETRIC:
        a, b = sorted((unit, other))
        return (rtype, a, b)
    return (rtype, unit, other)


def _depends_path(edges: dict[str, set[str]], start: str, goal: str) -> bool:
    seen, todo = set(), [start]
    while todo:
        at = todo.pop()
        if at == goal:
            return True
        if at in seen:
            continue
        seen.add(at)
        todo.extend(edges.get(at, ()))
    return False


def check_relation(
    unit: str, other: str, rtype: str, op: str, reason: str, by: str,
    store_names: Iterable[str], active: list[dict[str, Any]], agent: bool = False,
) -> str:
    """R8 and `spec.md ## Answers, câu 1`. The first reason this is refused, or `""`."""
    names = set(store_names)
    if rtype not in RELATIONS:
        return f"type must be one of {', '.join(RELATIONS)}, got {rtype!r}"
    if op not in OPS:
        return f"op must be add or remove, got {op!r}"
    if not unit or not other:
        return "name both units"
    if unit == other:
        return "a unit has no relation with itself"
    for name in (unit, other):
        if name not in names:
            return f"no such work unit in this workspace: {name}"
    for what, value in (("reason", reason), ("name", by)):
        said = _line_problem(what, str(value or ""))
        if said:
            return said
    if agent != is_agent(by):
        return f"a person's name may not start with {AGENT_PREFIX!r}" if not agent else "an agent's name must start with agent:"
    key = _relation_key(rtype, unit, other)
    keys = {_relation_key(r["type"], r["unit"], r["other"]) for r in active}
    if op == "remove":
        return "" if key in keys else f"{unit} {rtype} {other} is not in effect"
    if key in keys:
        return f"{unit} {rtype} {other} is already in effect"
    if rtype == "thay thế" and ("thay thế", other, unit) in keys:
        return f"{other} already replaces {unit}; one of the two has to be removed first"
    if rtype == "phụ thuộc":
        edges: dict[str, set[str]] = {}
        for r in active:
            if r["type"] == "phụ thuộc":
                edges.setdefault(r["unit"], set()).add(r["other"])
        if _depends_path(edges, other, unit):
            return f"{other} already depends on {unit}, directly or through others; this would close a cycle"
    return ""


def check_shortlist(names: Any, backlog: Iterable[str], estimates: dict[str, dict[str, Any]]) -> str:
    """R10. No ceiling on `lệch` positions (`spec.md ## Answers, câu 2`)."""
    if not isinstance(names, list) or not names:
        return "the shortlist is empty"
    if len(names) > SHORTLIST_MAX:
        return f"the shortlist has {len(names)} units; at most {SHORTLIST_MAX}"
    if not all(isinstance(n, str) and n for n in names):
        return "every entry must be a unit name"
    if len(set(names)) != len(names):
        return "a unit appears twice"
    waiting = set(backlog)
    for n in names:
        if n not in waiting:
            return f"{n} is not in the backlog"
        if n not in estimates:
            return f"{n} has no estimate in effect"
    return ""


# -- folding the records ------------------------------------------------------------------


def _estimate_problem(r: dict[str, Any]) -> str:
    if not isinstance(r.get("unit"), str) or not r.get("unit"):
        return "names no unit"
    return check_estimate(r.get("value"), r.get("effort"), r.get("basis"), r.get("by"), is_agent(r.get("by")))


def estimates_of(records: Iterable[dict[str, Any]], problems: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """R6. Per unit: `effective`, `person` (latest), `agent` (latest), `history`."""
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        if r.get("kind") != "estimate-value":
            continue
        said = _estimate_problem(r)
        if said:
            if problems is not None:
                problems.append(f"estimate at {r.get('at')}: {said}")
            continue
        slot = out.setdefault(r["unit"], {"person": None, "agent": None, "history": []})
        slot["agent" if is_agent(r["by"]) else "person"] = r
        slot["history"].append(r)
    for slot in out.values():
        slot["effective"] = slot["person"] or slot["agent"]
    return out


def relations_of(records: Iterable[dict[str, Any]], problems: list[str] | None = None) -> list[dict[str, Any]]:
    """R8. The relations in effect: the last record of each key decides."""
    state: dict[tuple[str, str, str], dict[str, Any] | None] = {}
    for r in records:
        if r.get("kind") != "relation":
            continue
        unit, other, rtype, op = r.get("unit"), r.get("other"), r.get("type"), r.get("op")
        if not (isinstance(unit, str) and isinstance(other, str) and unit and other and unit != other
                and rtype in RELATIONS and op in OPS):
            if problems is not None:
                problems.append(f"relation at {r.get('at')}: not a relation this app reads")
            continue
        state[_relation_key(rtype, unit, other)] = r if op == "add" else None
    return [
        {"unit": r["unit"], "other": r["other"], "type": r["type"], "reason": r.get("reason") or "",
         "by": r.get("by") or "", "at": r.get("at") or ""}
        for r in state.values() if r is not None
    ]


def _sort_key(est: dict[str, Any], number: Any) -> tuple:
    try:
        n = int(number)
    except (TypeError, ValueError):
        n = 10**9
    return (-int(est["value"]), EFFORTS.index(est["effort"]), n)


def computed_order(
    estimated: dict[str, dict[str, Any]], relations: list[dict[str, Any]],
    numbers: dict[str, Any], statuses: dict[str, str],
) -> tuple[list[str], list[dict[str, str]]]:
    """R11 and `câu 1`. Kahn over `phụ thuộc`, best-ready first by value, effort, number.

    `estimated` is the backlog units with an estimate in effect. An edge to a finished unit
    is met; one to a unit outside `estimated` is ignored with a warning. A cycle (only a
    hand-edited run log can hold one) puts what is left last, with a warning, never loops.
    """
    warnings: list[dict[str, str]] = []
    needs: dict[str, set[str]] = {n: set() for n in estimated}
    for r in relations:
        if r["type"] != "phụ thuộc" or r["unit"] not in estimated:
            continue
        other = r["other"]
        if other in estimated:
            needs[r["unit"]].add(other)
            continue
        status = statuses.get(other, "missing")
        if status == "finished":
            continue
        # `0082` D32: the app's own words, in English (S6).
        why = {"dropped": "is dropped", "rejected": "was rejected", "backlog": "has no estimate",
               "missing": "is not in the store"}.get(status, "is not in the backlog")
        warnings.append({"unit": r["unit"], "text": f"depends on {other}, but {other} {why}; ignored in the order"})
    key = {n: _sort_key(estimated[n], numbers.get(n)) for n in estimated}
    order: list[str] = []
    left = dict(needs)
    while left:
        ready = [n for n, deps in left.items() if not (deps & left.keys())]
        if not ready:
            rest = sorted(left, key=key.__getitem__)
            for n in rest:
                warnings.append({"unit": n, "text": "is in a dependency cycle; placed last"})
            order.extend(rest)
            break
        best = min(ready, key=key.__getitem__)
        order.append(best)
        del left[best]
    return order, warnings


def shortlist_of(records: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, int]:
    """The last well-formed `shortlist` record and its 1-based position among all of them."""
    last, seq, n = None, 0, 0
    for r in records:
        if r.get("kind") != "shortlist":
            continue
        n += 1
        units = r.get("units")
        if isinstance(units, list) and all(isinstance(u, str) for u in units):
            last, seq = r, n
    return last, seq


def stamp(records: Iterable[dict[str, Any]], unit: str) -> dict[str, Any]:
    """R14. Where `unit` stands in the shortlist in effect now."""
    last, seq = shortlist_of(records)
    if last is None:
        return {"rank": None, "of": None, "record": None}
    names = last["units"]
    return {
        "rank": names.index(unit) + 1 if unit in names else None,
        "of": len(names),
        "record": {"at": last.get("at"), "n": seq},
    }


def _warnings_for(
    shortlist: list[str], relations: list[dict[str, Any]], statuses: dict[str, str],
) -> dict[str, list[str]]:
    """R9, plus the dependency cases `câu 1` brings. Warn, never remove."""
    out: dict[str, list[str]] = {n: [] for n in shortlist}
    pos = {n: i for i, n in enumerate(shortlist)}
    for r in relations:
        unit, other, rtype = r["unit"], r["other"], r["type"]
        if rtype == "thay thế" and other in pos:
            out[other].append(f"replaced by {unit}")
        elif rtype == "trùng" and unit in pos and other in pos:
            out[unit].append(f"duplicates {other}, also in the shortlist")
            out[other].append(f"duplicates {unit}, also in the shortlist")
        elif rtype == "phụ thuộc" and unit in pos:
            status = statuses.get(other, "missing")
            if status in ("dropped", "rejected"):
                out[unit].append(f"depends on {other}, which {'is dropped' if status == 'dropped' else 'was rejected'}")
            elif status == "backlog" and pos.get(other, len(shortlist)) > pos[unit]:
                out[unit].append(f"depends on {other}, which is not before it in the shortlist")
    return out


def _brief(r: dict[str, Any]) -> dict[str, Any]:
    """What the page shows of one estimate record."""
    return {
        "unit": r.get("unit") or "", "value": r.get("value"), "effort": r.get("effort"),
        "effort_source": r.get("effort_source") or "",
        "similar": [s for s in r["similar"] if isinstance(s, str)] if isinstance(r.get("similar"), list) else [],
        "basis": r.get("basis") or "", "effort_basis": r.get("effort_basis") or "",
        "by": r.get("by") or "", "at": r.get("at") or "",
    }


def fold(
    units: list[dict[str, Any]], records: list[dict[str, Any]], found: dict[str, dict[str, Any]],
    undetermined: Iterable[str] = (),
) -> dict[str, Any]:
    """Everything the page shows, from one read of the board and the run log."""
    problems: list[str] = []
    backlog = [u["name"] for u in units if in_backlog(u)]
    statuses = {u["name"]: _status_of(u) for u in units}
    numbers = {u["name"]: u.get("number") for u in units}
    ests = estimates_of(records, problems)
    rels = relations_of(records, problems)
    estimated = {n: ests[n]["effective"] for n in backlog if n in ests}
    order, order_warnings = computed_order(estimated, rels, numbers, statuses)
    rank_of = {n: i + 1 for i, n in enumerate(order)}

    last, seq = shortlist_of(records)
    names = list(last["units"]) if last else []
    warned = _warnings_for(names, rels, statuses)
    for w in order_warnings:
        if w["unit"] in warned:
            warned[w["unit"]].append(w["text"])
    waiting = set(backlog)
    entries = []
    for i, n in enumerate(names):
        est = ests.get(n, {})
        computed = rank_of.get(n)
        entries.append({
            "rank": i + 1, "unit": n,
            "estimate": _brief(est["effective"]) if est.get("effective") else None,
            "agent_differs": _differs(est),
            "computed": computed,
            "drift": computed != i + 1,
            "in_backlog": n in waiting,
            "warnings": warned.get(n, []) + ([] if n in waiting else ["no longer in the backlog"]),
        })
    rest = [
        {"unit": n, "computed": rank_of[n], "estimate": _brief(estimated[n]),
         "agent_differs": _differs(ests.get(n, {}))}
        for n in order if n not in names
    ]
    unestimated = [n for n in backlog if n not in estimated]

    per_unit: dict[str, dict[str, Any]] = {}
    for n in set(backlog) | set(names) | {r["unit"] for r in rels} | {r["other"] for r in rels}:
        eff = (ests.get(n) or {}).get("effective")
        per_unit[n] = {
            "rank": names.index(n) + 1 if n in names else None,
            "value": eff.get("value") if eff else None,
            "effort": eff.get("effort") if eff else None,
            "effort_source": eff.get("effort_source") if eff else None,
            "relations": [
                {"type": r["type"], "other": r["other"] if r["unit"] == n else r["unit"],
                 "direction": "out" if r["unit"] == n else "in"}
                for r in rels if n in (r["unit"], r["other"])
            ],
        }
    history: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if r.get("kind") == "estimate-value" and isinstance(r.get("unit"), str):
            history.setdefault(r["unit"], []).append({"kind": "estimate", **_brief(r)})
        elif r.get("kind") == "relation" and isinstance(r.get("unit"), str):
            item = {"kind": "relation", "unit": r.get("unit"), "other": r.get("other"), "type": r.get("type"),
                    "op": r.get("op"), "reason": r.get("reason") or "", "by": r.get("by") or "",
                    "at": r.get("at") or ""}
            other = r.get("other")
            for n in {r["unit"], other} if isinstance(other, str) else {r["unit"]}:
                history.setdefault(n, []).append(item)

    cuts = None
    if len(found) >= TERCILE_MIN:
        c, t = terciles(f["cost_usd"] for f in found.values()), terciles(f["turns"] for f in found.values())
        cuts = {"cost_usd": list(c), "turns": list(t)}
    return {
        "backlog": backlog,
        "shortlist": entries,
        "shortlist_record": ({"at": last.get("at"), "n": seq, "by": last.get("by") or "",
                              "reason": last.get("reason") or ""} if last else None),
        "order": rest,
        "unestimated": unestimated,
        "relations": rels,
        "warnings": order_warnings,
        "suggested": order[:SHORTLIST_MAX],
        "undiscriminating": len(backlog) <= SHORTLIST_MAX,
        "measured_count": len(found),
        # `0092` R11: finished units left out of `measured_count` for a cost nobody knows.
        "undetermined_count": len(list(undetermined)),
        "terciles": cuts,
        "history": history,
        "per_unit": per_unit,
        "problems": problems,
    }


def _differs(est: dict[str, Any]) -> dict[str, Any] | None:
    """R6: the agent's latest, when a person's estimate is in effect and says otherwise."""
    person, agent = est.get("person"), est.get("agent")
    if not person or not agent:
        return None
    if (person.get("value"), person.get("effort")) == (agent.get("value"), agent.get("effort")):
        return None
    return _brief(agent)


# -- the agent's proposal ----------------------------------------------------------------


def build_prompt(
    backlog_texts: list[dict[str, str]], finished_rows: list[dict[str, Any]], undetermined: int = 0,
) -> str:
    """R17. Instructions in English; the three value goals stay in the words `check_estimate` matches."""
    lines = [
        "You estimate the backlog of a software project. You have no tools and one turn.",
        "",
        "For every unit listed under *Backlog*, give:",
        "- `value`: a whole number 1-5, judged against the goal \"CoS thay người\" — "
        + "; ".join(f"\"{g}\"" for g in VALUE_GOALS) + ". 5 serves it most.",
        "- `effort`: S, M or L, your own judgement. The app replaces it with a measured one when "
        "the units you name as similar have a recorded cost.",
        f"- `similar`: 0 to {SIMILAR_MAX} names taken only from *Finished units*, the ones closest in size. "
        "Name none when nothing is close, and say why in `basis`.",
        f"- `basis`: at most {BASIS_MAX} characters, in Vietnamese. It must copy verbatim at least one of: "
        + "; ".join(f"\"{g}\"" for g in VALUE_GOALS) + ". An estimate whose basis names none is dropped.",
        f"- `relations`: zero or more, each `{{\"type\", \"other\", \"reason\"}}`, `type` one of "
        + ", ".join(f"\"{r}\"" for r in RELATIONS)
        + ". \"thay thế\" means this unit replaces `other`; \"phụ thuộc\" means this unit needs `other` done first. "
        "`reason` is one line.",
        "",
        "Reply with one JSON block and nothing else:",
        "```json",
        '{"units":[{"unit":"NNNN_slug","value":3,"effort":"M","similar":["NNNN_slug"],'
        '"basis":"...","relations":[{"type":"liên quan","other":"NNNN_slug","reason":"..."}]}]}',
        "```",
        "",
        "## Backlog",
    ]
    for b in backlog_texts:
        lines += ["", f"### {b['unit']}"]
        for head, key in (("In their own words", "idea"), ("Problem", "problem"), ("Proposed outcome", "outcome")):
            if b.get(key):
                lines += [f"**{head}**", b[key].strip()]
    lines += ["", "## Finished units", ""]
    if undetermined > 0:
        # `0092` R11: never listed, and never hidden.
        lines += [f"({undetermined} finished units have an unknown cost and are left out)", ""]
    if not finished_rows:
        lines.append("(none with a recorded cost)")
    for f in finished_rows:
        lines.append(f"- {f['unit']} — {f.get('title') or ''} — ${float(f['cost_usd']):.2f}, {f['turns']} turns")
    return "\n".join(lines) + "\n"


def section(text: str, heading: str) -> str:
    """The body under `## <heading>`, up to the next `## `; `""` when there is none."""
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text or "", re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def title_of(text: str) -> str:
    first = (text or "").strip().splitlines()[:1]
    return first[0].lstrip("# ").strip() if first else ""


_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def parse_proposal(
    reply: str, backlog: Iterable[str], store_names: Iterable[str],
    found: dict[str, dict[str, Any]], session: str, active: list[dict[str, Any]],
    workspace: str = "", undetermined: int = 0,
) -> dict[str, Any]:
    """R18. `{records, rejected, failed}`. Relations are checked one after another, so two in
    one reply cannot close a cycle between them."""
    m = _JSON_BLOCK.search(reply or "")
    raw = m.group(1) if m else (reply or "")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as e:
        return {"records": [], "rejected": [], "failed": f"the reply is not JSON: {e}"}
    entries = data.get("units") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {"records": [], "rejected": [], "failed": "the reply has no \"units\" list"}
    by = f"{AGENT_PREFIX}{session or 'unknown'}"
    waiting, names = set(backlog), list(store_names)
    live = list(active)
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for e in entries:
        if not isinstance(e, dict):
            rejected.append({"unit": "", "reason": "an entry is not an object"})
            continue
        unit = e.get("unit")
        if not isinstance(unit, str) or unit not in waiting:
            rejected.append({"unit": str(unit), "reason": "not in the backlog"})
            continue
        named = e.get("similar") if isinstance(e.get("similar"), list) else []
        similar = [s for s in named if isinstance(s, str)][:SIMILAR_MAX]
        said = check_estimate(e.get("value"), e.get("effort"), e.get("basis"), by, agent=True)
        if said:
            rejected.append({"unit": unit, "reason": said})
        else:
            got = effort_from(similar, found, undetermined)
            records.append({
                "kind": "estimate-value", "workspace": workspace, "unit": unit, "value": e["value"],
                "effort": got["effort"] or e["effort"], "effort_source": got["effort_source"],
                "effort_basis": got["effort_basis"], "similar": similar, "basis": e["basis"], "by": by,
            })
        proposed = e.get("relations") if isinstance(e.get("relations"), list) else []
        for rel in proposed:
            if not isinstance(rel, dict):
                rejected.append({"unit": unit, "reason": "a relation is not an object"})
                continue
            rtype, other, reason = rel.get("type"), rel.get("other"), rel.get("reason")
            said = check_relation(unit, str(other or ""), str(rtype or ""), "add", str(reason or ""), by,
                                  names, live, agent=True)
            if said:
                rejected.append({"unit": unit, "reason": f"relation {rtype} {other}: {said}"})
                continue
            rec = {"kind": "relation", "workspace": workspace, "unit": unit, "other": other, "type": rtype,
                   "op": "add", "reason": reason, "by": by}
            records.append(rec)
            live.append({"unit": unit, "other": other, "type": rtype})
    return {"records": records, "rejected": rejected, "failed": None}
