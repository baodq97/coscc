"""A unit, as `read_unit` builds it: its files' prose and the app's snapshot entry.

The parsers above `read_unit` through `entry_unit`, the links, the walk of the unit's process
(`route`), and the review-round helpers `readUnit` and the gates share. Which file holds what is
asked of the unit's process (`proc`), never named here. A unit is
a dict whose keys are inserted in a fixed order, so `status --json` prints them alike. Every
regex that reads `\\d`, `\\w` or `\\b` is `re.ASCII`, as the loop's regexes always have.
"""

from __future__ import annotations

import os
import re
from typing import Any

from coscc.loop import (
    BRANCH_TYPES,
    DECIDERS,
    IDEAS,
    REVIEW_ROUNDS,
    SLUG_MAX,
    SLUG_RE,
    UNDEFINED,
    UNIT_RE,
    Proc,
    conditions,
    dig,
    js,
    nullish,
    proc_of,
    trim,
    truthy,
)
from coscc.agent import pack
from coscc.units import guards

A = re.ASCII

# --- reading -------------------------------------------------------------------------

HOLD_TO = {"Paused": "paused", "Dropped": "dropped", "Resumed": "active"}
HOLD_MOVES = {
    "active": ["paused", "dropped"],
    "paused": ["dropped", "active"],
    "dropped": ["paused"],
}
HOLD_HEAD_OF = {to: head for head, to in HOLD_TO.items()}


def fold_holds(rows):
    problems = []
    state = "active"
    hold = None
    for i, r in enumerate(rows):
        if dig(r, "by") in (None, UNDEFINED):
            continue
        to = dig(r, "state")
        if to not in HOLD_MOVES[state]:
            problems.append(
                f"hold block {i + 1} (### {js(HOLD_HEAD_OF.get(to, UNDEFINED) if isinstance(to, str) else UNDEFINED)}) "
                f"is not a valid move from {state} — it is ignored"
            )
            continue
        state = to
        hold = None if to == "active" else {
            "state": to, "reason": dig(r, "reason"), "by": dig(r, "by"), "date": dig(r, "date"),
        }  # fmt: skip
    return {"hold": hold, "problems": problems}


def join_answers(questions, answers):
    if questions is None:
        return None
    latest = {}
    for a in answers:
        if dig(a, "n") is not None:
            latest[dig(a, "n")] = a
    out = []
    for q in questions:
        a = latest.get(dig(q, "n"))
        out.append({**q, "answered": a is not None, "answer": a})
    return out


def proc(unit) -> Proc:
    """The process the unit walks: the one its snapshot entry records."""
    return proc_of(unit.get("process") if isinstance(unit, dict) else None)


def unit_questions(unit):
    counted = None
    stages = proc(unit).stages
    for s in stages:
        if dig(unit["artifacts"], s["file"], "questions") not in (None, UNDEFINED):
            counted = s["file"]
    questions = []
    for s in stages:
        for q in nullish(dig(unit["artifacts"], s["file"], "questions"), []):
            asked = {k: v for k, v in q.items() if k not in ("answered", "answer")}
            questions.append({
                "artifact": s["file"], **asked,
                "answered": q["answered"], "counted": s["file"] == counted,
            })  # fmt: skip
    asked = unit["artifacts"][counted]["questions"] if counted else []
    open_ = len([q for q in asked if not q["answered"]]) if counted else 0
    return {"questions": questions, "open": open_, "counted": counted}


# --- the pull request and the review rounds -------------------------------------------


TYPE_LIST = ", ".join(BRANCH_TYPES)

PERSON_LABELS = ["needs-person", "claim-rejected", "answered"]
SEVERITY = re.compile(r"^\S+\s+—\s+(high|medium|low)\s+—\s", A | re.I)


def with_dropped(rounds):
    seen = []
    out = []
    for r in rounds:
        listed = list(dict.fromkeys(f["id"] for f in r["findings"]))
        dropped = [i for i in seen if i not in listed]
        for i in listed:
            if i not in seen:
                seen.append(i)
        out.append(
            {
                **r,
                "dropped": dropped,
                "unfinished": r["verdict"] == "changes-requested" and len(dropped) > 0,
            }
        )
    return {"rounds": out}


def _finding_from_row(f):
    path, lines, rule = f.get("path"), f.get("lines"), f.get("rule")
    where = (f"{js(path)}:{js(lines)}" if lines else js(path)) if path else "(none)"
    out = {"id": dig(f, "id"), "label": dig(f, "label")}
    out["fixedBy"] = dig(f, "fixedIn") if dig(f, "label") == "fixed" else None
    out["text"] = (
        f"{where} — {js(dig(f, 'severity'))} — {f'{js(rule)} ' if rule else ''}{js(dig(f, 'text'))}"
    )
    out["severity"] = dig(f, "severity")
    out["rule"] = rule or None
    return out


def round_text(verdict, findings):
    """A round's words, built from its rows: its verdict, then a line per finding."""
    said = [f"Verdict: {js(verdict)}."]
    said += [f"- {js(f['id'])} [{js(f['label'])}] {f['text']}" for f in findings]
    return "\n".join(said)


def review_from(rows):
    """The review rounds the app recorded, each with its findings and the words they make."""
    by_n: dict[Any, dict[str, Any]] = {}
    for row in rows:
        shots = nullish(dig(row, "screens", "shots"), [])
        findings = [_finding_from_row(f) for f in row["findings"]]
        by_n[row["n"]] = {
            "n": row["n"],
            "reviewed": row.get("reviewed") or None,
            "verdict": dig(row, "verdict"),
            "findings": findings,
            "screens": {
                "taken": nullish(row["screens"].get("taken")),
                "standard": nullish(row["screens"].get("standard")),
                "by": nullish(row["screens"].get("by")),
                "header": None,
                "shots": [
                    {k: dig(s, k) for k in ("path", "size", "address", "result")} for s in shots
                ],
            } if shots else None,
            "text": round_text(dig(row, "verdict"), findings),
        }  # fmt: skip
    ordered = sorted(by_n.values(), key=lambda r: r["n"])
    return with_dropped(
        [{k: v for k, v in r.items() if k not in ("dropped", "unfinished")} for r in ordered]
    )


# --- the review limit -------------------------------------------------------------------


def review_rounds(env=os.environ):
    """The review limit in force, or `ValueError` naming `COS_REVIEW_ROUNDS` (exit 2)."""
    raw = env.get("COS_REVIEW_ROUNDS")
    if raw is None or raw == "":
        return REVIEW_ROUNDS
    if not re.fullmatch(r"\d+", trim(raw), A) or int(trim(raw)) < 1:
        raise ValueError(f'COS_REVIEW_ROUNDS must be a positive integer, got "{raw}"')
    return int(trim(raw))


# --- the app's snapshot -----------------------------------------------------------------

NO_ENTRY = {
    "artifacts": {},
    "type": None,
    "links": {"idea": None, "dependsOn": None},
    "holds": [],
    "answers": [],
    "unknowns": [],
    "merged": False,
    "shipped": False,
    "reruns": [],
    "roundsGranted": 0,
}


def entry_of(state, ws, name):
    units = dig(state, "units")
    e = units.get(f"{js(ws)}/{name}") if isinstance(units, dict) else None
    return nullish(e)


def status_in(e, file):
    return nullish(dig(e, "artifacts", file, "status"))


def answers_in(e, file):
    return [
        {k: v for k, v in a.items() if k != "artifact"}
        for a in nullish(dig(e, "answers"), [])
        if dig(a, "artifact") == file
    ]


def status_of(u, f):
    return nullish(dig(u, "artifacts", f, "status"))


NO_STATUS = "{file} has no status: no record was handed back for it"
NO_TYPE = "the intent has handed back no type"


def present(u, f):
    return f in u["artifacts"]


def settled(s):
    return s in ("accepted", "skipped")


def ended_of(unit):
    # A unit that shipped stays finished, whatever ran again after it.
    if unit.get("shipped"):
        return "finished"
    rejected = any(status_of(unit, f) == "rejected" for f in proc(unit).files)
    return "closed" if rejected else None


def entry_unit(e):
    unit: dict[str, Any] = {"artifacts": {}, "process": nullish(dig(e, "process"))}
    for file in proc(unit).files:
        if truthy(dig(e, "artifacts", file)):
            unit["artifacts"][file] = {"status": status_in(e, file)}
    if dig(e, "shipped") is True:
        unit["shipped"] = True
    if ended_of(unit):
        unit["hold"] = None
    else:
        unit["hold"] = fold_holds(nullish(dig(e, "holds"), []))["hold"]
    return unit


# --- one idea, several units, several repositories ---------------------------

WS_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$", A)
IDEA_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9]+(?:-[a-z0-9]+)*)\.md$", A)


def valid_ws(s):
    return isinstance(s, str) and bool(WS_RE.fullmatch(s)) and s not in (".", "..")


def parse_unit_ref(ref):
    parts = js(nullish(ref, "")).split("/")
    if len(parts) == 1:
        ws, name = None, parts[0]
    elif len(parts) == 2:
        ws, name = parts
    else:
        return None
    if not UNIT_RE.fullmatch(name) or (ws is not None and not valid_ws(ws)):
        return None
    return {"ws": ws, "name": name}


def store_of(ws, state):
    if ws is not None and ws in nullish(dig(state, "workspaces"), []):
        return {"ws": ws}
    if ws is None or ws == state["workspace"]:
        return {"ws": state["workspace"]}
    return {"why": f"the app has no workspace named {ws}"}


# `LINKS`: what `read_unit` learned of a unit's links, kept off the unit
# so `status --json` carries `idea` and `dependsOn` alone. Keyed by `id`, the unit kept
# beside its entry so the id is never reused while the entry stands.
LINKS: dict[int, tuple[object, dict]] = {}


def links_of(unit):
    kept = LINKS.get(id(unit))
    return kept[1] if kept and kept[0] is unit else {"waiting": []}


def dependency(raw, unit, state):
    ref = parse_unit_ref(raw)
    if not ref:
        return {"ref": raw, "merged": None, "why": "not NNNN_<slug> or <ws>/NNNN_<slug>"}
    if ref["name"] == unit["name"] and (ref["ws"] is None or ref["ws"] == state["workspace"]):
        return {"ref": raw, "merged": None, "why": "a unit cannot depend on itself"}
    store = store_of(ref["ws"], state)
    if "why" in store:
        return {"ref": raw, "merged": None, "why": store["why"]}
    e = entry_of(state, store["ws"], ref["name"])
    if not truthy(e):
        return {
            "ref": raw,
            "merged": None,
            "why": f"unreadable: the app knows no unit {store['ws']}/{ref['name']}",
        }
    other = entry_unit(e)
    if dig(other, "hold", "state") == "dropped":
        return {"ref": raw, "merged": False, "why": "dropped"}
    rejected = next(
        (s for s in proc(other).stages if status_of(other, s["file"]) == "rejected"), None
    )
    if rejected:
        return {"ref": raw, "merged": False, "why": f"rejected: its {rejected['file']} is rejected"}
    if dig(e, "merged") is True:
        return {"ref": raw, "merged": True, "why": "merged"}
    return {"ref": raw, "merged": False, "why": "not merged: the app holds no merge of it"}


def resolve_links(unit, links, state):
    idea = nullish(dig(links, "idea"))
    depends_on = nullish(dig(links, "dependsOn"))
    related = []
    for b in nullish(dig(links, "backlog"), []):
        d = {**dependency(dig(b, "ref"), unit, state), "source": dig(b, "source")}
        if d["why"] != "dropped" and not d["why"].startswith("rejected"):
            related.append(d)
    if idea is None and depends_on is None and not related:
        return
    if idea is not None:
        unit["idea"] = idea
    if depends_on is not None or related:
        unit["dependsOn"] = [
            *(dependency(ref, unit, state) for ref in nullish(depends_on, [])),
            *related,
        ]
        for d in unit["dependsOn"]:
            if d["merged"] is None:
                where = "backlog relation" if d.get("source") else "Depends on:"
                unit["problems"].append(f"{where} {js(d['ref'])} — {d['why']}")
    waiting = [d for d in unit.get("dependsOn", []) if d["merged"] is not True]
    LINKS[id(unit)] = (unit, {"waiting": waiting})


def link_needs(unit):
    kept = links_of(unit)
    return [
        f"waits on {js(d['ref'])}: {d['why']} "
        f"({'backlog relation' if d.get('source') else 'Depends on:'})"
        for d in kept["waiting"]
    ]


WAITING_ON = "waiting on "


def wait_on_dependencies(unit, answer):
    if answer.get("stage") not in proc(unit).waits:
        return answer
    kept = links_of(unit)
    if kept["waiting"]:
        refs = ", ".join(js(d["ref"]) for d in kept["waiting"])
        return {
            "blocked": True,
            "action": f"{WAITING_ON}{refs} to merge",
            "stage": "",
            "why": "dependency",
        }
    return answer


# --- deciding: the pieces `read_unit` and every rule share ----------------------------


def agent_skip(u, f):
    return status_of(u, f) == "skipped" and bool(dig(u, "artifacts", f, "agentSkip"))


def skipped_by(u, f):
    by = nullish(dig(u, "artifacts", f, "agentSkip", "by"), "no one the app knows")
    return f"{f} is skipped by {by}, not by a person"


def missing(u, f):
    return NO_STATUS.format(file=f) if present(u, f) else f"{f} does not exist"


def unmeasured_of(u):
    return nullish(dig(u, "artifacts", proc(u).file(proc(u).measured), "unmeasured"), {"ids": []})


# What `read_unit` learned of the unit's outputs that `status --json` does not carry: each
# artifact's record (`results`) and the marks of the `fast-lane` guard. Kept as `LINKS` is.
FACTS: dict[int, tuple[object, dict]] = {}


def facts_of(unit):
    kept = FACTS.get(id(unit))
    return kept[1] if kept and kept[0] is unit else {"results": {}, "marks": None}


def _said(x):
    return isinstance(x, str) and trim(x) != ""


def field_value(unit, name, field):
    """The value of `field` in the last output of state `name`, as the loop reads it: `judgement`
    and `verdict` from the status they set; a skipped state has none."""
    p = proc(unit)
    f = p.file(name)
    status = status_of(unit, f)
    if status == "skipped" or not present(unit, f):
        return None
    if field == "judgement":
        return "ready" if status == "accepted" else "not-ready"
    if field == "verdict":
        return {"accepted": "pass", "changes-requested": "changes-requested"}.get(status)
    if field == p.measure and name == p.measured:
        return unmeasured_of(unit)["ids"]
    return dig(facts_of(unit)["results"], f, field) or None


def _is(value, want):
    if want == "non-empty":
        return bool(value) and (not isinstance(value, str) or _said(value))
    if want == "empty":
        return not _is(value, "non-empty")
    return value == want


def fast_marks(unit):
    """The `fast-lane` guard's marks for the unit, `None` when its process has no such branch."""
    return facts_of(unit)["marks"]


def holds(unit, name, c):
    """Whether condition `c` of a way out of state `name` holds for the unit."""
    if "field" in c:
        return _is(field_value(unit, name, c["field"]), c["is"])
    g = c.get("guard")
    if g == "fast-lane":
        marks = fast_marks(unit)
        return bool(marks) and all(marks.values())
    if g == "spike-holds":
        found = spike_findings(unit)
        return not found["fails"] and not found["missing"]
    if g == "dependency-merged":
        return not links_of(unit)["waiting"]
    return True


def walk(unit):
    """`(path, via)`: the states the unit walks from its process's start, and the guards of the
    ways it took off the main line. From each state the first way on (to a state not walked yet)
    whose `when` holds is taken, else the last such, the main line."""
    p = proc(unit)
    path, via, at = [], [], p.start
    while at is not None and at not in path:
        path.append(at)
        ways = [e for e in p.info[at]["next"] if e["to"] not in path]
        if not ways:
            break
        taken = next(
            (e for e in ways if all(holds(unit, at, c) for c in conditions(e.get("when")))),
            ways[-1],
        )
        if taken is not ways[-1]:
            via += [c["guard"] for c in conditions(taken.get("when")) if "guard" in c]
        at = taken["to"]
    return path, via


def route(unit):
    return walk(unit)[0]


def passed_over(unit, name):
    """Why state `name` is off the unit's walk, `(code, words)`, or `None` when it is on it or
    nothing says why."""
    p = proc(unit)
    path = route(unit)
    if name in path:
        return None
    fast = p.fast
    if fast and name in fast["over"] and fast["from"] in path:
        at = path.index(fast["from"])
        if path[at + 1 : at + 2] == [fast["to"]]:
            return (
                "not-in-lane",
                f"{name} is not a stage of the fast lane: {p.file(fast['from'])} carries the fix's "
                f"reproduction, expected and actual result, so {fast['to']} follows {fast['from']}",
            )
    when = (p.by_name.get(name) or {}).get("when")
    if when:
        source = next((e["from"] for e in p.into[name]), None)
        return ("", f"{name} is not required: {p.file(source)} has no [{when}] item")
    return None


def spike_findings(unit):
    p = proc(unit)
    spike = nullish(dig(unit, "artifacts", p.file(p.spike), "spike"), {"round": None, "items": {}})
    fails = []
    missing_ids = []
    reasons = []
    for id_ in unmeasured_of(unit)["ids"]:
        item = dig(spike, "items", id_)
        if not item:
            missing_ids.append(id_)
            reasons.append(f"{id_}: the {p.spike} record does not measure {id_}")
        elif dig(item, "verdict") is None:
            missing_ids.append(id_)
            reasons.append(f"{id_}: the {p.spike} record gives no verdict for {id_}")
        elif item["verdict"] == "fails":
            fails.append(id_)
            reasons.append(
                f"{id_}: {p.file(p.spike)} measured that it does not hold — "
                f"{p.file(p.measured)} must be rewritten on it"
            )
    return {
        "round": nullish(dig(spike, "round"), 1),
        "fails": fails,
        "missing": missing_ids,
        "reasons": reasons,
    }


def spike_needs(unit):
    need = []
    p = proc(unit)
    if not p.spike or p.spike not in route(unit):
        return need
    status = status_of(unit, p.file(p.spike))
    if status != "accepted":
        said = "missing" if status is None else f'"{status}"'
        need.extend(
            f"{id_}: {p.file(p.spike)} is {said}, not accepted"
            for id_ in unmeasured_of(unit)["ids"]
        )
        return need
    return [*need, *spike_findings(unit)["reasons"]]


def review_file(unit):
    return proc(unit).file(proc(unit).review)


def review_of(unit):
    return nullish(dig(unit, "artifacts", review_file(unit), "review", "rounds"), [])


def last_round(unit):
    rounds = review_of(unit)
    return rounds[-1] if rounds else None


def incomplete_draft(unit):
    last = last_round(unit)
    return (
        status_of(unit, review_file(unit)) == "draft"
        and last is not None
        and last["verdict"] == "incomplete"
    )


def rounds_used(unit):
    rounds = review_of(unit)
    asked = len(
        [r for r in rounds if r["verdict"] == "changes-requested" and not r.get("unfinished")]
    )
    waived = any(r["verdict"] == "needs-person" for r in rounds)
    last = rounds[-1] if rounds else None
    unfinished = bool(last and last.get("unfinished"))
    floor = (status_of(unit, review_file(unit)) == "changes-requested" and not unfinished) or (
        (incomplete_draft(unit) or unfinished) and any(r["verdict"] is None for r in rounds)
    )
    return max(asked, 1 if floor and not waived else 0)


def review_limit(unit, limit=REVIEW_ROUNDS):
    return limit + nullish(dig(unit, "artifacts", review_file(unit), "roundsGranted"), 0)


def out_of_rounds(unit, limit):
    return (
        status_of(unit, review_file(unit)) == "changes-requested" or incomplete_draft(unit)
    ) and rounds_used(unit) >= review_limit(unit, limit)


def person_answers(unit):
    return set(nullish(dig(unit, "artifacts", review_file(unit), "personAnswers"), []))


def needs_person_claims(unit):
    return nullish(dig(unit, "artifacts", proc(unit).file(proc(unit).fixer), "needsPerson"), [])


def pr_of(unit):
    """The pull request the unit's `open-pr` state recorded, or `None`."""
    return nullish(dig(unit, "artifacts", proc(unit).file(proc(unit).pr), "pr"))


def against_standard(text):
    m = SEVERITY.match(text)
    return bool(re.match(r"S\d+\b", text[len(m[0]) if m else len(text) :], A))


def severity_rule(unit):
    rounds = review_of(unit)
    if not rounds:
        return {"nonBlocking": [], "demoted": []}
    last = rounds[-1]
    higher = {}
    for r in rounds[:-1]:
        for f in r["findings"]:
            if f.get("severity") in ("high", "medium") and f["id"] not in higher:
                higher[f["id"]] = {"round": r["n"], "severity": f["severity"]}
    low = [
        f
        for f in last["findings"]
        if f["label"] == "open" and f.get("severity") == "low" and not against_standard(f["text"])
    ]
    return {
        "nonBlocking": [{"id": f["id"], "text": f["text"]} for f in low if f["id"] not in higher],
        "demoted": [{"id": f["id"], **higher[f["id"]]} for f in low if f["id"] in higher],
    }


def non_blocking(unit):
    return severity_rule(unit)["nonBlocking"]


def non_blocking_ids(unit):
    return {f["id"] for f in non_blocking(unit)}


def person_findings(unit):
    if status_of(unit, review_file(unit)) != "changes-requested":
        return None
    last = last_round(unit)
    if not last or last["verdict"] != "needs-person":
        return None
    answered = person_answers(unit)
    if not any(f["label"] == "needs-person" for f in last["findings"]):
        return None
    low = non_blocking_ids(unit)

    def closed(f):
        return (
            f["label"] in ("fixed", "needs-person")
            or (f["label"] == "answered" and f["id"] in answered)
            or f["id"] in low
        )

    if not all(closed(f) for f in last["findings"]):
        return None
    return [
        {
            "id": f["id"],
            "reason": f["text"],
            "answered": f["id"] in answered,
        }
        for f in last["findings"]
        if f["label"] == "needs-person"
    ]


def every_open_claimed(unit):
    last = last_round(unit)
    if not last or last["verdict"] != "changes-requested":
        return False
    low = non_blocking_ids(unit)
    open_ = [f for f in last["findings"] if f["label"] == "open" and f["id"] not in low]
    if not open_:
        return False
    judged = {
        f["id"] for r in review_of(unit) for f in r["findings"] if f["label"] in PERSON_LABELS
    }
    claimed = {c["id"] for c in needs_person_claims(unit) if c["id"] not in judged}
    if not all(f["id"] in claimed for f in open_):
        return False
    answered = person_answers(unit)
    return not any(
        f["label"] in ("claim-rejected", "unreadable")
        or (f["label"] == "answered" and f["id"] not in answered)
        for f in last["findings"]
    )


def needs_a_person(used, limit):
    return f"needs a person — review used {used} of {limit} rounds and findings are still open"


# --- branch names ---------------------------------------------------------------------


def branch_slug(slug):
    if len(slug) <= SLUG_MAX:
        return slug
    cut = slug[: SLUG_MAX + 1].rfind("-")
    return slug[:cut] if cut > 0 else slug[:SLUG_MAX]


def branch_problem(name):
    if not name:
        return "no branch name"
    if name == "main":
        return "main is the trunk, not a work branch"
    slash = name.find("/")
    if slash == -1:
        return f"no type prefix: expected <type>/<slug>, type one of {TYPE_LIST}"
    type_, slug = name[:slash], name[slash + 1 :]
    if type_ not in BRANCH_TYPES:
        return f'"{type_}" is not one of {TYPE_LIST}'
    if not slug:
        return "the slug is empty"
    if "/" in slug:
        return "the slug carries a second slash"
    if len(slug) > SLUG_MAX:
        return f"the slug is {len(slug)} characters, over the {SLUG_MAX} allowed"
    if not SLUG_RE.fullmatch(slug):
        return "the slug takes lowercase letters, digits and single hyphens"
    return None


def not_a_work_branch(name, problem):
    return f'"{name}" is not a work branch: {problem}'


def branch_for(unit_name, type_):
    match = UNIT_RE.fullmatch(js(nullish(unit_name, "")))
    if not match:
        return {"error": f'"{js(unit_name)}" does not match NNNN_<slug>'}
    if not type_:
        return {"error": f"{unit_name}: {NO_TYPE} — one of {TYPE_LIST}"}
    if type_ not in BRANCH_TYPES:
        return {"error": f'"{js(type_)}" is not one of {TYPE_LIST}'}
    return {"branch": f"{type_}/{branch_slug(match[2])}"}


# --- a unit -------------------------------------------------------------------------------


def _artifact(unit, known, file):
    p = proc(unit)
    name = file.removesuffix(".md")
    fields = p.info[name]["fields"]
    status = status_in(known, file)
    if status is None:
        unit["problems"].append(NO_STATUS.format(file=file))
    elif status not in p.valid[file]:
        unit["problems"].append(
            f'{file} has status "{js(status)}", not one of {", ".join(p.valid[file])}'
        )
    a: dict[str, Any] = {
        "status": status,
        "skipReason": nullish(dig(known, "artifacts", file, "reason"))
        if status == "skipped"
        else None,
    }
    unit["artifacts"][file] = a
    by = nullish(dig(known, "artifacts", file, "authority"))
    if status == "skipped" and by not in DECIDERS:
        a["agentSkip"] = {"by": by}
    if name == p.pr:
        a["pr"] = nullish(dig(known, "artifacts", file, "pr"))
    if name == p.review:
        a["review"] = review_from(nullish(dig(known, "artifacts", file, "rounds"), []))
        answers = answers_in(known, file)
        ids = [a_.get("id") for a_ in answers if not ("id" in a_ and a_["id"] is None)]
        a["personAnswers"] = list(dict.fromkeys(ids))
        granted = nullish(dig(known, "roundsGranted"), 0)
        if granted > 0:
            a["roundsGranted"] = granted
    result = nullish(dig(known, "artifacts", file, "result"))
    if name == p.fixer:
        # Only the ids: a claim's sentence quotes its finding (`person_findings`).
        a["needsPerson"] = [{"id": i} for i in nullish(dig(result, "needs_person"), [])]
    if name == p.measured:
        ids = list(nullish(dig(result, p.measure), []))
        if ids:
            a["unmeasured"] = {"ids": ids}
    if name == p.spike and "verdicts" in fields:
        a["spike"] = {
            "round": nullish(dig(known, "artifacts", file, "round")),
            "items": {
                js(dig(v, "id")): {"verdict": dig(v, "verdict")}
                for v in nullish(dig(result, "verdicts"), [])
            },
        }
    if name == p.merge:
        ship = nullish(dig(known, "artifacts", file, "ship"))
        if ship and (ship.get("round") is not None or ship.get("refused") is not None):
            a["ship"] = ship
    if name == p.rests and "rests_on" in fields and p.spike in route(unit):
        a["restsOn"] = list(nullish(dig(result, "rests_on"), []))
    questions = join_answers(
        nullish(dig(known, "artifacts", file, "questions")), answers_in(known, file)
    )
    if questions is not None:
        a["questions"] = questions


def stale_marks(unit, known):
    """Each settled artifact a person's rerun named at the record it still holds is stale."""
    reruns = nullish(dig(known, "reruns"), [])
    for file, a in unit["artifacts"].items():
        record = dig(known, "artifacts", file, "record")
        if not reruns or record in (None, UNDEFINED) or not settled(status_of(unit, file)):
            continue
        if file == proc(unit).file(proc(unit).spike) and not unmeasured_of(unit)["ids"]:
            continue
        by = next((r for r in reversed(reruns) if dig(r, "stale", file) == record), None)
        if by:
            a["stale"] = {"stage": by["stage"], "date": by["date"]}


def hold_state_gone(unit, known):
    """A unit whose recorded process is in no pack shows that process and is held `state-gone`
    (unless a person's hold is already on it): the loop walks the default in its place, and
    nothing runs on it."""
    ref = nullish(dig(known, "process"))
    if isinstance(ref, str) and ref and pack.process(ref) is None:
        unit["process"] = ref
        unit["hold"] = unit["hold"] or {
            "state": "paused",
            "by": "app",
            "date": "",
            "reason": f"its process {ref} is in no pack",
            "code": "state-gone",
        }


def read_unit(dir_, name, state):  # noqa: PLR0915 - `readUnit` kept whole
    """`readUnit`: `state` is the snapshot, the one source; the directory says which artifacts
    are present."""
    known = nullish(entry_of(state, state["workspace"], name), NO_ENTRY)
    p = proc_of(nullish(dig(known, "process")))
    unit: dict[str, Any] = {"name": name, "artifacts": {}, "problems": [], "process": p.ref}
    results = {
        f: dig(known, "artifacts", f, "result")
        for f in p.files
        if isinstance(dig(known, "artifacts", f, "result"), dict)
    }
    FACTS[id(unit)] = (unit, {"results": results, "marks": None})
    match = UNIT_RE.fullmatch(name)
    if not match:
        unit["problems"].append("directory name does not match NNNN_<slug>")
    else:
        unit["number"] = int(match[1])
        unit["slug"] = match[2]

    for file in p.files:
        if os.path.exists(os.path.join(dir_, file)):
            _artifact(unit, known, file)
    opener = p.file(p.opener)
    has_intent = present(unit, opener)
    for file, a in nullish(dig(known, "artifacts"), {}).items():
        if not dig(a, "status") or file in unit["artifacts"]:
            continue
        if a["status"] == "skipped" and dig(a, "authority") in DECIDERS:
            unit["artifacts"][file] = {
                "status": a["status"],
                "skipReason": nullish(dig(a, "reason")),
            }
        else:
            unit["problems"].append(
                f"the app records {file} as {js(a['status'])}, but the file does not exist"
            )
    for u in nullish(dig(known, "unknowns"), []):
        if dig(u, "field") == "ingest":
            unit["problems"].append(
                f"the app could not read this unit after its last step: {js(dig(u, 'reason'))}"
            )

    unit.update(unit_questions(unit))
    unit["personFindings"] = nullish(person_findings(unit), [])
    unit["nonBlocking"] = non_blocking(unit)

    stray = [f for f in sorted(os.listdir(dir_)) if f not in p.files]
    if stray:
        unit["problems"].append(f"unexpected file(s): {', '.join(stray)}")

    unit["phase"] = "started"
    if not unit["artifacts"].get(opener):
        at = p.at(p.opener)
        before = [unit["artifacts"].get(f) for f in p.files[:at]]
        valid = any(
            a and a["status"] is not None and a["status"] in p.valid[f]
            for f, a in zip(p.files[:at], before)
        )
        later = any(present(unit, f) for f in p.files[at + 1 :])
        if valid and not later:
            unit["phase"] = "pre-intent"
        else:
            unit["problems"].append(f"no {opener} — every unit opens with one")
    else:
        type_ = nullish(dig(known, "type"))
        if type_ is None:
            unit["problems"].append(NO_TYPE)
        elif type_ not in BRANCH_TYPES:
            unit["problems"].append(
                f'intent.md has type "{js(type_)}", not one of {", ".join(BRANCH_TYPES)}'
            )
        else:
            unit["type"] = type_
        unit["branch"] = nullish(branch_for(name, type_).get("branch"))
    unit["process"] = unit.pop("process")
    if p.fast:
        target = p.file(p.fast["to"])
        back = any(_said(dig(results, target, f)) for f in p.fast["back"])
        marks = guards.fast_lane_marks(
            {
                "type": unit.get("type"),
                "fix": dig(results, p.file(p.fast["from"]), "fix"),
                "bypassed": back or any(present(unit, p.file(n)) for n in p.fast["passed"]),
            }
        )
        FACTS[id(unit)][1]["marks"] = marks
        entered = all(marks[k] for k in "abcd")
        if entered and status_of(unit, target) == "draft" and back:
            unit["artifacts"][target]["leftLane"] = True

    # An idea is held too; with no intent.md a problem names no file.
    held = fold_holds(nullish(dig(known, "holds"), []))
    where = "intent.md: " if has_intent else ""
    unit["problems"].extend(f"{where}{p}" for p in held["problems"])
    unit["hold"] = held["hold"]
    hold_state_gone(unit, known)
    # Only when true, so a unit that never shipped reads as it did before.
    if dig(known, "shipped") is True:
        unit["shipped"] = True
    ended = ended_of(unit)
    if ended and unit["hold"]:
        carrier = opener if has_intent else "the unit"
        unit["problems"].append(
            f"{carrier} carries a hold block, but the unit is {ended} — it is ignored"
        )
        unit["hold"] = None
    hold_state = dig(unit, "hold", "state") if unit["hold"] else "active"
    unit["holdMoves"] = [] if ended else list(HOLD_MOVES[hold_state])
    if dig(unit, "hold", "code") == "state-gone":
        unit["holdMoves"] = ["dropped"]

    stale_marks(unit, known)

    if has_intent:
        resolve_links(unit, nullish(dig(known, "links"), NO_ENTRY["links"]), state)
    return unit


def read_all(cos_dir, state):
    """`readAll`: every directory under `cos_dir` but `ideas/`, by name."""
    if not os.path.exists(cos_dir):
        return []
    with os.scandir(cos_dir) as it:
        names = [e.name for e in it if e.is_dir(follow_symlinks=False) and e.name != IDEAS]
    return [read_unit(os.path.join(cos_dir, n), n, state) for n in sorted(names)]
