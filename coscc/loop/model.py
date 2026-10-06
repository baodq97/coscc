"""A unit, as `read_unit` builds it: its files' prose and the app's snapshot entry.

The parsers above `read_unit` through `entry_unit`, the
links, the lane, and the review-round helpers `readUnit` and the gates share. A unit is
a dict whose keys are inserted in a fixed order, so `status --json` prints them alike. Every
regex that reads `\\d`, `\\w` or `\\b` is `re.ASCII`, as the loop's regexes always have.
"""

from __future__ import annotations

import os
import re
from typing import Any

from coscc.loop import (
    ARTIFACTS,
    BRANCH_TYPES,
    DECIDERS,
    IDEAS,
    REVIEW_ROUNDS,
    SLUG_MAX,
    SLUG_RE,
    SPIKE,
    STAGES,
    UNDEFINED,
    UNIT_RE,
    VALID,
    dig,
    js,
    nullish,
    trim,
    truthy,
)

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


def unit_questions(unit):
    counted = None
    for s in STAGES:
        if dig(unit["artifacts"], s["file"], "questions") not in (None, UNDEFINED):
            counted = s["file"]
    questions = []
    for s in STAGES:
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
    return "closed" if any(status_of(unit, s["file"]) == "rejected" for s in STAGES) else None


def entry_unit(e):
    unit: dict[str, Any] = {"artifacts": {}}
    for file in ARTIFACTS:
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
    rejected = next((s for s in STAGES if status_of(other, s["file"]) == "rejected"), None)
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
    if answer.get("stage") != "impl":
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
    return nullish(dig(u, "artifacts", "spec.md", "unmeasured"), {"ids": []})


_SOURCE = re.compile(r"([^\s:]+)(?::(\d+)-(\d+))?", A)


def _said(x):
    return isinstance(x, str) and trim(x) != ""


def _cited(expected):
    """Whether `expected` says something and names one relative path outside `.cos`, with its
    lines in order when it gives lines."""
    if not isinstance(expected, dict) or not _said(expected.get("text")):
        return False
    m = _SOURCE.fullmatch(str(expected.get("source") or ""))
    if not m:
        return False
    path, start, end = m[1], m[2], m[3]
    if (
        path.startswith("/")
        or path == ".cos"
        or path.startswith(".cos/")
        or ".." in path.split("/")
    ):
        return False
    return start is None or (int(start) >= 1 and int(end) >= int(start))


def lane_of(unit, fix=None, left_lane=None):
    """The lane from the unit's type, intent's `fix`, which files exist, and impl's `left_lane`."""
    fix = fix if isinstance(fix, dict) else {}
    marks = {
        "a": unit.get("type") == "fix",
        "b": _said(fix.get("reproduction")),
        "c": _cited(fix.get("expected")),
        "d": _said(fix.get("actual")),
        "e": not present(unit, "spec.md") and not present(unit, "plan.md") and not _said(left_lane),
    }
    lane = "fast" if all(marks.values()) else "full"
    entered = marks["a"] and marks["b"] and marks["c"] and marks["d"]
    missing_ = [k for k, v in marks.items() if not v] if marks["a"] and lane == "full" else []
    return {"lane": lane, "enteredFast": entered, "laneMissing": missing_}


def in_lane(unit, s):
    return "lanes" not in s or nullish(unit.get("lane"), "full") in s["lanes"]


def required(unit, s):
    if not in_lane(unit, s):
        return False
    if not s.get("when"):
        return not s.get("optional")
    if status_of(unit, "spec.md") == "skipped":
        return False
    return len(unmeasured_of(unit)["ids"]) > 0


def spike_findings(unit):
    spike = nullish(dig(unit, "artifacts", "spike.md", "spike"), {"round": None, "items": {}})
    fails = []
    missing_ids = []
    reasons = []
    for id_ in unmeasured_of(unit)["ids"]:
        item = dig(spike, "items", id_)
        if not item:
            missing_ids.append(id_)
            reasons.append(f"{id_}: the spike record does not measure {id_}")
        elif dig(item, "verdict") is None:
            missing_ids.append(id_)
            reasons.append(f"{id_}: the spike record gives no verdict for {id_}")
        elif item["verdict"] == "fails":
            fails.append(id_)
            reasons.append(
                f"{id_}: spike.md measured that it does not hold — spec.md must be rewritten on it"
            )
    return {
        "round": nullish(dig(spike, "round"), 1),
        "fails": fails,
        "missing": missing_ids,
        "reasons": reasons,
    }


def spike_needs(unit):
    need = []
    if not required(unit, SPIKE):
        return need
    status = status_of(unit, "spike.md")
    if status != "accepted":
        said = "missing" if status is None else f'"{status}"'
        need.extend(
            f"{id_}: spike.md is {said}, not accepted" for id_ in unmeasured_of(unit)["ids"]
        )
        return need
    return [*need, *spike_findings(unit)["reasons"]]


def review_of(unit):
    return nullish(dig(unit, "artifacts", "review.md", "review", "rounds"), [])


def last_round(unit):
    rounds = review_of(unit)
    return rounds[-1] if rounds else None


def incomplete_draft(unit):
    last = last_round(unit)
    return (
        status_of(unit, "review.md") == "draft"
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
    floor = (status_of(unit, "review.md") == "changes-requested" and not unfinished) or (
        (incomplete_draft(unit) or unfinished) and any(r["verdict"] is None for r in rounds)
    )
    return max(asked, 1 if floor and not waived else 0)


def review_limit(unit, limit=REVIEW_ROUNDS):
    return limit + nullish(dig(unit, "artifacts", "review.md", "roundsGranted"), 0)


def out_of_rounds(unit, limit):
    return (
        status_of(unit, "review.md") == "changes-requested" or incomplete_draft(unit)
    ) and rounds_used(unit) >= review_limit(unit, limit)


def person_answers(unit):
    return set(nullish(dig(unit, "artifacts", "review.md", "personAnswers"), []))


def needs_person_claims(unit):
    return nullish(dig(unit, "artifacts", "impl.md", "needsPerson"), [])


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
    if status_of(unit, "review.md") != "changes-requested":
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
    status = status_in(known, file)
    if status is None:
        unit["problems"].append(NO_STATUS.format(file=file))
    elif status not in VALID[file]:
        unit["problems"].append(
            f'{file} has status "{js(status)}", not one of {", ".join(VALID[file])}'
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
    if file == "pr.md":
        a["pr"] = nullish(dig(known, "artifacts", file, "pr"))
    if file == "review.md":
        a["review"] = review_from(nullish(dig(known, "artifacts", file, "rounds"), []))
        answers = answers_in(known, file)
        ids = [a_.get("id") for a_ in answers if not ("id" in a_ and a_["id"] is None)]
        a["personAnswers"] = list(dict.fromkeys(ids))
        granted = nullish(dig(known, "roundsGranted"), 0)
        if granted > 0:
            a["roundsGranted"] = granted
    result = nullish(dig(known, "artifacts", file, "result"))
    if file == "impl.md":
        # Only the ids: a claim's sentence quotes its finding (`person_findings`).
        a["needsPerson"] = [{"id": i} for i in nullish(dig(result, "needs_person"), [])]
    if file == "spec.md":
        ids = list(nullish(dig(result, "unmeasured"), []))
        if ids:
            a["unmeasured"] = {"ids": ids}
    if file == "spike.md":
        a["spike"] = {
            "round": nullish(dig(known, "artifacts", file, "round")),
            "items": {
                js(dig(v, "id")): {"verdict": dig(v, "verdict")}
                for v in nullish(dig(result, "verdicts"), [])
            },
        }
    if file == "ship.md":
        ship = nullish(dig(known, "artifacts", file, "ship"))
        if ship and (ship.get("round") is not None or ship.get("refused") is not None):
            a["ship"] = ship
    if file == "plan.md" and required(unit, SPIKE):
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
        if file == "spike.md" and not unmeasured_of(unit)["ids"]:
            continue
        by = next((r for r in reversed(reruns) if dig(r, "stale", file) == record), None)
        if by:
            a["stale"] = {"stage": by["stage"], "date": by["date"]}


def read_unit(dir_, name, state):  # noqa: PLR0915 - `readUnit` kept whole
    """`readUnit`: `state` is the snapshot, the one source; the directory says which artifacts
    are present."""
    unit: dict[str, Any] = {"name": name, "artifacts": {}, "problems": []}
    known = nullish(entry_of(state, state["workspace"], name), NO_ENTRY)
    match = UNIT_RE.fullmatch(name)
    if not match:
        unit["problems"].append("directory name does not match NNNN_<slug>")
    else:
        unit["number"] = int(match[1])
        unit["slug"] = match[2]

    for file in ARTIFACTS:
        if os.path.exists(os.path.join(dir_, file)):
            _artifact(unit, known, file)
    has_intent = present(unit, "intent.md")
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

    stray = [f for f in sorted(os.listdir(dir_)) if f not in ARTIFACTS]
    if stray:
        unit["problems"].append(f"unexpected file(s): {', '.join(stray)}")

    unit["phase"] = "started"
    if not unit["artifacts"].get("intent.md"):
        idea = unit["artifacts"].get("idea.md")
        idea_valid = (
            bool(idea) and idea["status"] is not None and idea["status"] in VALID["idea.md"]
        )
        at = next(i for i, s in enumerate(STAGES) if s["name"] == "intent")
        later = any(present(unit, s["file"]) for s in STAGES[at + 1 :])
        if idea_valid and not later:
            unit["phase"] = "pre-intent"
        else:
            unit["problems"].append("no intent.md — every unit opens with one")
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
    left_lane = dig(known, "artifacts", "impl.md", "result", "left_lane")
    unit.update(lane_of(unit, dig(known, "artifacts", "intent.md", "result", "fix"), left_lane))
    if unit["enteredFast"] and status_of(unit, "impl.md") == "draft" and _said(left_lane):
        unit["artifacts"]["impl.md"]["leftLane"] = True

    # An idea is held too; with no intent.md a problem names no file.
    held = fold_holds(nullish(dig(known, "holds"), []))
    where = "intent.md: " if has_intent else ""
    unit["problems"].extend(f"{where}{p}" for p in held["problems"])
    unit["hold"] = held["hold"]
    # Only when true, so a unit that never shipped reads as it did before.
    if dig(known, "shipped") is True:
        unit["shipped"] = True
    ended = ended_of(unit)
    if ended and unit["hold"]:
        carrier = "intent.md" if has_intent else "the unit"
        unit["problems"].append(
            f"{carrier} carries a hold block, but the unit is {ended} — it is ignored"
        )
        unit["hold"] = None
    hold_state = dig(unit, "hold", "state") if unit["hold"] else "active"
    unit["holdMoves"] = [] if ended else list(HOLD_MOVES[hold_state])

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
