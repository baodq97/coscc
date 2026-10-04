"""A unit, as `read_unit` builds it: its files' prose and the app's snapshot entry.

The parsers above `read_unit` through `entry_unit`, the
links, the lane, and the review-round helpers `readUnit` and the gates share. A unit is
a dict whose keys are inserted in a fixed order, so `status --json` prints them alike. Every
regex that reads `\\d`, `\\w` or `\\b` is `re.ASCII`, as the loop's regexes always have.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import unicodedata
from typing import Any

from coscc.loop import (
    ARTIFACTS,
    BRANCH_TYPES,
    DECIDERS,
    IDEAS,
    RERUNNABLE,
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
    read_text,
    split_lines,
    trim,
    trim_end,
    truthy,
)

A = re.ASCII

# --- reading -------------------------------------------------------------------------

STATUS_RE = re.compile(r"\bStatus:\s*([A-Za-z]+(?:-[A-Za-z]+)*)", A)


def parse_status(text):
    m = STATUS_RE.search(text)
    return m[1].lower() if m else None


def parse_skip_reason(text):
    m = re.search(r"\bSpec:\s*skipped\s*\(([^)]*)\)", text, A | re.I)
    return trim(m[1]) if m else None


def section(text, title):
    """The lines of `## <title>` up to the next `## `, or `None` with no such heading."""
    lines = split_lines(text)
    start = next((i for i, l in enumerate(lines) if trim_end(l) == f"## {title}"), -1)
    if start == -1:
        return None
    rest = lines[start + 1 :]
    end = next((i for i, l in enumerate(rest) if l.startswith("## ")), -1)
    return rest if end == -1 else rest[:end]


def _find(items, pred):
    return next((i for i, x in enumerate(items) if pred(x)), -1)


def parse_questions(text):
    lines = section(text, "Open questions")
    if lines is None:
        return None
    found: list[Any] = []
    for line in lines:
        m = re.match(r"^(\d+)\.\s+(.*)$", line, A)
        if m:
            found.append({"n": int(m[1]), "lines": [m[2]]})
        elif found:
            found[-1]["lines"].append(line)

    def asks(q):
        blank = _find(q["lines"], lambda l: trim(l) == "")
        return "?" in "\n".join(q["lines"] if blank == -1 else q["lines"][:blank])

    return [{"n": q["n"], "text": trim("\n".join(q["lines"]))} for q in found if asks(q)]


ANSWER_META = re.compile(r"^Answered by:\s*(.+?)\.\s+Date:\s*(\S+?)\.\s+Via:\s*(\S+?)\.?\s*$", A)
HOLD_HEAD = re.compile(r"^###\s+(Paused|Dropped|Resumed)\s*$", A)
HOLD_META = re.compile(r"^Decided by:\s*(.+?)\.\s+Date:\s*(\S+?)\.\s+Via:\s*(\S+?)\.?\s*$", A)
HOLD_TO = {"Paused": "paused", "Dropped": "dropped", "Resumed": "active"}
HOLD_MOVES = {
    "active": ["paused", "dropped"],
    "paused": ["dropped", "active"],
    "dropped": ["paused"],
}
HOLD_HEAD_OF = {to: head for head, to in HOLD_TO.items()}
MORE_ROUNDS_HEAD = re.compile(r"^###\s+More rounds\s*$", A)
MORE_ROUNDS_N = re.compile(r"^Rounds:\s*(\d+)\s*$", A)
RERUN_HEAD = re.compile(r"^###\s+Rerun\s*$", A)
RERUN_META = re.compile(r"^Requested by:\s*(.+?)\.\s+Date:\s*(\S+?)\.\s+Via:\s*(\S+?)\.?\s*$", A)
RERUN_STAGE = re.compile(r"^Stage:\s*([a-z]+)\.?\s*$", A)
RERUN_STALE = re.compile(r"^Stale:\s*(\S+)\s+sha256:([0-9a-f]{64})\s*$", A)
H3 = re.compile(r"^###\s", A)


def answer_blocks(text):
    lines = section(text, "Answers")
    if lines is None:
        return []
    blocks: list[Any] = []
    for line in lines:
        m = re.match(r"^###\s+(?:Câu\s+(\d+)|(F\d+)|(Outcome))\s*$", line, A)
        if m:
            blocks.append(
                {
                    "n": int(m[1]) if m[1] is not None else None,
                    "id": m[2],
                    "outcome": m[3] is not None,
                    "lines": [],
                }
            )
        elif HOLD_HEAD.match(line):
            blocks.append({"hold": True, "lines": []})
        elif RERUN_HEAD.match(line):
            blocks.append({"rerun": True, "lines": []})
        elif MORE_ROUNDS_HEAD.match(line):
            blocks.append({"moreRounds": True, "lines": []})
        elif blocks:
            blocks[-1]["lines"].append(line)
    return blocks


def parse_answers(text):
    answers = []
    for b in answer_blocks(text):
        if b.get("outcome") or b.get("hold") or b.get("rerun") or b.get("moreRounds"):
            continue
        at = _find(b["lines"], lambda l: trim(l) != "")
        meta = None if at == -1 else ANSWER_META.match(b["lines"][at])
        if not meta:
            continue
        answers.append(
            {
                "n": b["n"],
                "id": b["id"],
                "by": trim(meta[1]),
                "date": meta[2],
                "via": meta[3],
                "text": trim("\n".join(b["lines"][at + 1 :])),
            }
        )
    return answers


def hold_blocks(text):
    lines = section(text or "", "Answers")
    if lines is None:
        return []
    blocks: list[Any] = []
    for line in lines:
        m = HOLD_HEAD.match(line)
        if m:
            blocks.append({"head": m[1], "lines": []})
        elif H3.match(line):
            blocks.append({"head": None, "lines": []})
        elif blocks:
            blocks[-1]["lines"].append(line)
    out = []
    for b in blocks:
        if not b["head"]:
            continue
        at = _find(b["lines"], lambda l: trim(l) != "")
        meta = None if at == -1 else HOLD_META.match(b["lines"][at])
        state = HOLD_TO[b["head"]]
        if not meta:
            out.append({"state": state, "reason": None, "by": None, "date": None, "via": None})
            continue
        out.append(
            {
                "state": state,
                "reason": trim("\n".join(b["lines"][at + 1 :])),
                "by": trim(meta[1]),
                "date": meta[2],
                "via": meta[3],
            }
        )
    return out


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


def parse_more_rounds(text):
    lines = section(text or "", "Answers")
    problems = []
    if lines is None:
        return {"granted": 0, "problems": problems}
    blocks: list[Any] = []
    for line in lines:
        if MORE_ROUNDS_HEAD.match(line):
            blocks.append({"more": True, "lines": []})
        elif H3.match(line):
            blocks.append({"more": False, "lines": []})
        elif blocks:
            blocks[-1]["lines"].append(line)
    granted = 0
    k = 0
    for b in blocks:
        if not b["more"]:
            continue
        k += 1
        body = [l for l in b["lines"] if trim(l) != ""]
        first = body[0] if body else None
        second = body[1] if len(body) > 1 else None
        if first is None or not HOLD_META.match(first):
            problems.append(
                f"more rounds block {k} has no valid Decided by line — it is not counted"
            )
            continue
        n = MORE_ROUNDS_N.match(second) if second is not None else None
        if not n or int(n[1]) < 1:
            problems.append(f"more rounds block {k} has no valid Rounds line — it is not counted")
            continue
        granted += int(n[1])
    return {"granted": granted, "problems": problems}


def above_answers(text):
    lines = split_lines(text)
    at = _find(lines, lambda l: trim_end(l) == "## Answers")
    above = trim_end("\n".join(lines if at == -1 else lines[:at]))
    return hashlib.sha256(above.encode("utf-8", "surrogatepass")).hexdigest()


def parse_reruns(text):
    lines = section(text or "", "Answers")
    reruns = []
    problems = []
    if lines is None:
        return {"reruns": reruns, "problems": problems}
    blocks: list[Any] = []
    for line in lines:
        if RERUN_HEAD.match(line):
            blocks.append({"rerun": True, "lines": []})
        elif H3.match(line):
            blocks.append({"rerun": False, "lines": []})
        elif blocks:
            blocks[-1]["lines"].append(line)
    n = 0
    for b in blocks:
        if not b["rerun"]:
            continue
        n += 1
        body = [l for l in b["lines"] if trim(l) != ""]
        meta = RERUN_META.match(body[0]) if body else None
        stage = RERUN_STAGE.match(body[1]) if len(body) > 1 else None
        if not meta or not stage or stage[1] not in RERUNNABLE:
            problems.append(
                f"rerun block {n} has no well-formed Requested by: and Stage: lines — it is ignored"
            )
            continue
        stale = {}
        for l in body[2:]:
            m = RERUN_STALE.match(l)
            if m and m[1] in ARTIFACTS:
                stale[m[1]] = m[2]
        reruns.append({"stage": stage[1], "by": trim(meta[1]), "date": meta[2], "stale": stale})
    return {"reruns": reruns, "problems": problems}


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
        out.append(
            {"n": dig(q, "n"), "text": dig(q, "text"), "answered": a is not None, "answer": a}
        )
    return out


def answered_questions(text):
    return join_answers(parse_questions(text), parse_answers(text))


# --- the outcome a unit was measured against ------------------------------------------


def _real_date(s):
    y, m, d = (int(x) for x in s.split("-"))
    try:
        # Year 0 is a leap year in JavaScript's proleptic calendar, as 2000 is.
        datetime.date(y or 2000, m, d)
    except ValueError:
        return False
    return True


def parse_deadline(text):
    lines = section(text, "Proposed outcome")
    if lines is None:
        return None
    for m in re.finditer(r"\b(\d{4}-\d{2}-\d{2})\b", "\n".join(lines), A):
        if _real_date(m[1]):
            return m[1]
    return None


OUTCOME_RESULTS = {"đạt": "met", "trượt": "missed", "không đo được": "unmeasurable"}
OUTCOME_KEY = re.compile(r"^(Result|Measured by|Source|Reason):\s*(.*)$", A)


def parse_outcome(text):
    blocks: list[Any] = []
    invalid = 0
    for b in answer_blocks(text):
        if not b.get("outcome"):
            continue
        at = _find(b["lines"], lambda l: trim(l) != "")
        meta = None if at == -1 else ANSWER_META.match(b["lines"][at])
        if not meta:
            invalid += 1
            continue
        rest = b["lines"][at + 1 :]
        i = _find(rest, lambda l: trim(l) != "")
        if i == -1:
            i = len(rest)
        keys = {}
        note = []
        while i < len(rest) and trim(rest[i]) != "":
            k = OUTCOME_KEY.match(trim(rest[i]))
            if k:
                keys[k[1]] = trim(k[2])
            else:
                note.append(rest[i])
            i += 1
        note.extend(rest[i:])
        result = OUTCOME_RESULTS.get(unicodedata.normalize("NFC", keys.get("Result", "")).lower())
        measured_by = keys.get("Measured by") or None
        source = keys.get("Source") or None
        reason = keys.get("Reason") or None
        ok = (
            result is not None
            and measured_by is not None
            and (reason is not None if result == "unmeasurable" else source is not None)
        )
        if not ok:
            invalid += 1
            continue
        blocks.append({
            "result": result, "by": trim(meta[1]), "date": meta[2], "via": meta[3],
            "measuredBy": measured_by, "source": source, "reason": reason,
            "note": trim("\n".join(note)) or None,
        })  # fmt: skip
    return {"blocks": blocks, "invalid": invalid}


def unit_outcome(text):
    parsed = parse_outcome(text)
    last = parsed["blocks"][-1] if parsed["blocks"] else {}
    return {
        "deadline": parse_deadline(text),
        "result": last.get("result"),
        "by": last.get("by"),
        "date": last.get("date"),
        "measuredBy": last.get("measuredBy"),
        "source": last.get("source"),
        "reason": last.get("reason"),
        "note": last.get("note"),
        "invalid": parsed["invalid"],
    }


def unit_questions(unit):
    counted = None
    for s in STAGES:
        if dig(unit["artifacts"], s["file"], "questions") not in (None, UNDEFINED):
            counted = s["file"]
    questions = []
    for s in STAGES:
        for q in nullish(dig(unit["artifacts"], s["file"], "questions"), []):
            questions.append({
                "artifact": s["file"], "n": q["n"], "text": q["text"],
                "answered": q["answered"], "counted": s["file"] == counted,
            })  # fmt: skip
    asked = unit["artifacts"][counted]["questions"] if counted else []
    open_ = len([q for q in asked if not q["answered"]]) if counted else 0
    return {"questions": questions, "open": open_, "counted": counted}


# --- the pull request and the review rounds -------------------------------------------


def parse_pr(text):
    m = re.search(r"\bPR:\s*(\S+?/pull/(\d+))", text, A)
    return {"url": m[1], "number": int(m[2])} if m else None


TITLE_LINE = re.compile(r"^# PR:([^\n\r  ]*?)\r?$")


def pr_text(text):
    lines = text.split("\n")
    title_at = _find(lines, lambda l: TITLE_LINE.fullmatch(l) is not None)
    title_line = TITLE_LINE.fullmatch(lines[title_at]) if title_at != -1 else None
    title = (trim(title_line[1]) or None) if title_line else None
    m = STATUS_RE.search(text)
    status_at = len(text[: m.start()].split("\n")) - 1 if m else -1
    kept = [l for i, l in enumerate(lines) if i != status_at and i != title_at]
    while kept and trim(kept[0]) == "":
        kept.pop(0)
    pr = parse_pr(text)
    return {
        "title": title,
        "body": "\n".join(kept),
        "url": pr["url"] if pr else None,
        "scope": pr_scope(text),
    }


TYPE_LIST = ", ".join(BRANCH_TYPES)


def title_problem(title, type_, number):
    if not title:
        return "pr.md has no title: its # PR: line is missing or empty"
    m = re.fullmatch(r"([a-z]+)\((\d{4})\): ([^\n\r  ]+)", title, A)
    if not m:
        return f'the title "{title}" is not <type>(<NNNN>): <text>'
    if m[1] not in BRANCH_TYPES:
        return f'the title\'s type "{m[1]}" is not one of {TYPE_LIST}'
    if not type_:
        return f'intent.md declares no Type, so the title\'s type "{m[1]}" cannot be checked against it'
    if m[1] != type_:
        return f'the title\'s type is "{m[1]}", but intent.md declares Type: {type_}'
    if m[2] != number:
        return f"the title names unit {m[2]}, not {number}"
    if re.match(r"wip(?![a-z0-9])", m[3], A | re.I):
        return 'the title\'s text opens with "wip" — it names the change, not how far it got'
    marked = re.search("[À-ɏḀ-ỿ]", unicodedata.normalize("NFC", title))
    if marked:
        return f'the title carries "{marked[0]}", a letter with a diacritic — the title is English'
    return None


def title_needs(unit):
    artifact = unit["artifacts"].get("pr.md")
    if not artifact:
        return []
    wrong = title_problem(artifact.get("title"), unit.get("type"), unit["name"][:4])
    return [f"{wrong} — the pr stage writes the # PR: line of pr.md again"] if wrong else []


def pr_scope(text):
    lines = [l.removesuffix("\r") for l in text.split("\n")]
    at = _find(lines, lambda l: re.fullmatch(r"## Scope of the diff\s*", l, A) is not None)
    if at == -1:
        return None
    nxt = next((i for i, l in enumerate(lines) if i > at and l.startswith("## ")), -1)
    part = lines[at + 1 : len(lines) if nxt == -1 else nxt]
    i = _find(part, lambda l: trim(l) != "")
    m = None if i == -1 else re.fullmatch(r"(\d+) files, \+(\d+)/-(\d+)\s*", part[i], A)
    if not m:
        return None
    i += 1
    while i < len(part) and trim(part[i]) == "":
        i += 1
    paths = []
    while i < len(part):
        p = re.fullmatch(r"- `([^`]+)`\s*", part[i], A)
        if not p:
            break
        paths.append(p[1])
        i += 1
    if len(set(paths)) != len(paths):
        return None
    return {"files": int(m[1]), "additions": int(m[2]), "deletions": int(m[3]), "paths": paths}


ROUND_HEAD = re.compile(r"^## Round (\d+)\s*$", A)
ROUND_META = re.compile(
    r"^Reviewed:\s*([0-9a-f]{7,40})\.?\s+Verdict:\s*"
    r"(pass|changes-requested|needs-person|incomplete)\.?\s*$",
    A | re.I,
)
PERSON_LABELS = ["needs-person", "claim-rejected", "answered"]
FINDING = re.compile(r"^- (F\d+)\s+\[([^\]]*)\]\s*([^\n\r  ]*)$", A)
SEVERITY = re.compile(r"^\S+\s+—\s+(high|medium|low)\s+—\s", A | re.I)
SCREENS_HEAD = re.compile(
    r"^Taken at:\s*`?([0-9a-f]{7,40})`?\.\s+Standard:\s*`?([^`\s]+?)`?\.\s+"
    r"Looked at by:\s*(.+?),\s*from screenshots\.?\s*$",
    A | re.I,
)
SCREENS_SHOT = re.compile(
    r"^- `?(\S+?\.png)`?\s+—\s+(\d+)\s*[×x]\s*(\d+)(?:\s*\([^()]*\))?\s+—\s+`?([^`\s]+)`?\s+—\s+"
    r"(\S[^\n\r  ]*)$",
    A,
)


def parse_review(text):
    lines = split_lines(text)
    stop = _find(lines, lambda l: trim_end(l) == "## Answers")
    rounds: list[Any] = []
    in_findings = False
    in_screens = False
    for line in lines if stop == -1 else lines[:stop]:
        head = ROUND_HEAD.match(line)
        if head:
            rounds.append({
                "n": int(head[1]), "reviewed": None, "verdict": None, "findings": [],
                "screens": None, "seenText": False, "closed": False, "lines": [line],
            })  # fmt: skip
            in_findings = in_screens = False
            continue
        if line.startswith("## "):
            if rounds:
                rounds[-1]["closed"] = True
            in_findings = in_screens = False
            continue
        r = rounds[-1] if rounds else None
        if not r or r["closed"]:
            continue
        r["lines"].append(line)
        if not r["seenText"] and trim(line) != "":
            r["seenText"] = True
            meta = ROUND_META.match(trim(line))
            if meta:
                r["reviewed"] = meta[1].lower()
                r["verdict"] = meta[2].lower()
                continue
        if line.startswith("### "):
            in_findings = trim_end(line) == "### Findings"
            in_screens = trim_end(line) == "### Screens"
            if in_screens and not r["screens"]:
                r["screens"] = {
                    "taken": None,
                    "standard": None,
                    "by": None,
                    "header": None,
                    "shots": [],
                }
            continue
        if in_screens:
            if trim(line) == "":
                continue
            if r["screens"]["header"] is None:
                r["screens"]["header"] = trim(line)
                m = SCREENS_HEAD.match(r["screens"]["header"])
                if m:
                    r["screens"].update({"taken": m[1].lower(), "standard": m[2], "by": trim(m[3])})
                continue
            shot = SCREENS_SHOT.match(trim(line))
            if shot:
                r["screens"]["shots"].append({
                    "path": shot[1], "size": f"{shot[2]}x{shot[3]}",
                    "address": shot[4], "result": trim(shot[5]),
                })  # fmt: skip
            continue
        if not in_findings:
            continue
        f = FINDING.match(line)
        if not f:
            continue
        label = trim(f[2])
        lower = label.lower()
        fixed = re.fullmatch(r"fixed\s+([0-9a-f]{7,40})", label, A | re.I)
        body = trim(f[3])
        severity = SEVERITY.match(body)
        r["findings"].append({
            "id": f[1],
            "label": "open" if lower == "open" else "fixed" if fixed
            else lower if lower in PERSON_LABELS else "unreadable",
            "fixedBy": fixed[1].lower() if fixed else None,
            "text": body,
            "severity": severity[1].lower() if severity else None,
        })  # fmt: skip
    return with_dropped([
        {
            "n": r["n"], "reviewed": r["reviewed"], "verdict": r["verdict"],
            "findings": r["findings"], "screens": r["screens"],
            "text": trim_end("\n".join(r["lines"])),
        }
        for r in rounds
    ])  # fmt: skip


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


def review_from(rows, parsed):
    by_n = {r["n"]: r for r in parsed["rounds"]}
    for row in rows:
        shots = nullish(dig(row, "screens", "shots"), [])
        text = nullish(dig(by_n.get(row["n"], {}), "text"), "")
        by_n[row["n"]] = {
            "n": row["n"],
            "reviewed": row.get("reviewed") or None,
            "verdict": dig(row, "verdict"),
            "findings": [_finding_from_row(f) for f in row["findings"]],
            "screens": {
                "taken": nullish(row["screens"].get("taken")),
                "standard": nullish(row["screens"].get("standard")),
                "by": nullish(row["screens"].get("by")),
                "header": None,
                "shots": [
                    {k: dig(s, k) for k in ("path", "size", "address", "result")} for s in shots
                ],
            } if shots else None,
            "text": text,
        }  # fmt: skip
    ordered = sorted(by_n.values(), key=lambda r: r["n"])
    return with_dropped(
        [{k: v for k, v in r.items() if k not in ("dropped", "unfinished")} for r in ordered]
    )


def parse_needs_person(text):
    lines = split_lines(text)
    stop = _find(lines, lambda l: trim_end(l) == "## Answers")
    own = section("\n".join(lines if stop == -1 else lines[:stop]), "Needs a person")
    if own is None:
        return []
    claims = []
    for line in own:
        m = re.match(r"^- (F\d+):\s*(\S[^\n\r  ]*)$", line, A)
        if m:
            claims.append({"id": m[1], "reason": trim(m[2])})
    return claims


# --- the questions a spec could not answer, and what a spike measured -----------------


def parse_unmeasured(text):
    lines = nullish(section(text, "Concerns"), [])
    ids = []
    problems = []
    for line in lines:
        m = re.match(r"^(?:[-*]|\d+\.)\s+\[unmeasured\]([^\n\r  ]*)$", line, A)
        if not m:
            continue
        found = re.match(r"\s(U\d+)\b", m[1], A)
        id_ = found[1] if found else None
        if not id_:
            problems.append(f'spec.md: an [unmeasured] item carries no U<n>: "{trim(line)}"')
        elif id_ in ids:
            problems.append(f"spec.md: {id_} is marked [unmeasured] twice")
        else:
            ids.append(id_)
    return {"ids": ids, "problems": problems}


def _header_line(own):
    first = _find(own, lambda l: l.startswith("## "))
    head = own if first == -1 else own[:first]
    return next((l for l in head if re.search(r"\bStatus:", l, A)), None)


def parse_spike(text):
    lines = split_lines(text)
    stop = _find(lines, lambda l: trim_end(l) == "## Answers")
    own = lines if stop == -1 else lines[:stop]
    header = _header_line(own)
    round_ = re.search(r"\bRound:\s*(\d+)", header, A) if header is not None else None
    items: dict[str, Any] = {}
    at: Any = None
    fence = None
    for line in own:
        if fence is None and line.startswith("## "):
            head = re.match(r"^## (U\d+)\s*$", line, A)
            if head:
                item: dict[str, Any] = {"verdict": None, "hasBlock": False, "_body": False}
                at = items[head[1]] = item
            else:
                at = None
            continue
        if not at:
            continue
        if fence is not None:
            if trim(line).startswith(fence):
                if at["_body"]:
                    at["hasBlock"] = True
                fence = None
            elif trim(line) != "":
                at["_body"] = True
            continue
        opened = re.match(r"(`{3,}|~{3,})", trim(line))
        if opened:
            fence = opened[1]
            at["_body"] = False
            continue
        v = re.match(r"^Verdict:\s*(holds|fails)\.?\s*$", line, A | re.I)
        if v:
            at["verdict"] = v[1].lower()
    for item in items.values():
        del item["_body"]
    return {"round": int(round_[1]) if round_ else None, "items": items}


def parse_ship(text):
    lines = split_lines(text)
    stop = _find(lines, lambda l: trim_end(l) == "## Answers")
    own = lines if stop == -1 else lines[:stop]
    header = _header_line(own)
    round_ = re.search(r"\bRound:\s*(\d+)", header, A) if header is not None else None
    out = nullish(section("\n".join(own), "What went out"), [])
    refused = next((l for l in out if l.startswith("Refused:")), None)
    return {
        "round": int(round_[1]) if round_ else None,
        "refused": (trim(refused[len("Refused:") :]) or None) if refused is not None else None,
    }


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
    "links": {"idea": None, "repo": None, "dependsOn": None},
    "holds": [],
    "answers": [],
    "unknowns": [],
    "merged": False,
}


def entry_of(state, ws, name):
    units = dig(state, "units")
    e = units.get(f"{js(ws)}/{name}") if isinstance(units, dict) else None
    return nullish(e)


def status_in(e, file):
    return nullish(dig(e, "artifacts", file, "status"), nullish(dig(e, "artifacts", file, "raw")))


def answers_in(e, file):
    return [
        {k: v for k, v in a.items() if k != "artifact"}
        for a in nullish(dig(e, "answers"), [])
        if dig(a, "artifact") == file
    ]


def status_of(u, f):
    return nullish(dig(u, "artifacts", f, "status"))


def present(u, f):
    return f in u["artifacts"]


def settled(s):
    return s in ("accepted", "skipped", "done")


def ended_of(unit):
    if status_of(unit, "plan.md") == "done":
        return "finished"
    return "closed" if any(status_of(unit, s["file"]) == "rejected" for s in STAGES) else None


def entry_unit(e):
    unit: dict[str, Any] = {"artifacts": {}}
    for file in ARTIFACTS:
        if truthy(dig(e, "artifacts", file)):
            unit["artifacts"][file] = {"status": status_in(e, file)}
    if ended_of(unit) or not unit["artifacts"].get("intent.md"):
        unit["hold"] = None
    else:
        unit["hold"] = fold_holds(nullish(dig(e, "holds"), []))["hold"]
    return unit


# --- one idea, several units, several repositories ---------------------------

WS_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$", A)
IDEA_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9]+(?:-[a-z0-9]+)*)\.md$", A)


def valid_ws(s):
    return isinstance(s, str) and bool(WS_RE.fullmatch(s)) and s not in (".", "..")


def parse_idea_ref(ref):
    parts = js(nullish(ref, "")).split("/")
    if len(parts) == 2:
        ws, dir_, file = None, parts[0], parts[1]
    elif len(parts) == 3:
        ws, dir_, file = parts
    else:
        return None
    if dir_ != IDEAS or not IDEA_FILE_RE.fullmatch(file) or (ws is not None and not valid_ws(ws)):
        return None
    return {"ws": ws, "file": file}


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


def header_field(text, key):
    lines = split_lines(text)
    first = _find(lines, lambda l: l.startswith("## "))
    header = "\n".join((lines if first == -1 else lines[:first])[1:])
    m = re.search(rf"(?:^|\s){key}:[ \t]*([^\s,]+(?:,[ \t]*[^\s,]+)*)", header, A | re.M)
    return re.sub(r"\.$", "", m[1]) if m else None


def parse_links(text):
    deps = header_field(text, "Depends on")
    return {
        "idea": header_field(text, "Idea"),
        "repo": header_field(text, "Repo"),
        "dependsOn": None if deps is None else re.split(r",[ \t]*", deps),
    }


def store_of(ws, repo, state):
    if ws is not None and ws in nullish(dig(state, "workspaces"), []):
        return {"ws": ws}
    if ws is None or ws == repo:
        return {"ws": state["workspace"]}
    return {"why": f"the app has no workspace named {ws}"}


# `LINKS`: what `read_unit` learned of a unit's links, kept off the unit
# so `status --json` carries `idea`, `repo` and `dependsOn` alone. Keyed by `id`, the unit kept
# beside its entry so the id is never reused while the entry stands.
LINKS: dict[int, tuple[object, dict]] = {}


def links_of(unit):
    kept = LINKS.get(id(unit))
    return kept[1] if kept and kept[0] is unit else {"needs": [], "waiting": []}


def dependency(raw, unit, repo, state):
    ref = parse_unit_ref(raw)
    if not ref:
        return {"ref": raw, "merged": None, "why": "not NNNN_<slug> or <ws>/NNNN_<slug>"}
    if ref["name"] == unit["name"] and (ref["ws"] is None or ref["ws"] == repo):
        return {"ref": raw, "merged": None, "why": "a unit cannot depend on itself"}
    store = store_of(ref["ws"], repo, state)
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


def idea_line(idea, unit, repo, state):
    ref = parse_idea_ref(idea)
    if not ref:
        return {"why": "not ideas/NNNN_<slug>.md or <ws>/ideas/NNNN_<slug>.md"}
    if repo is None:
        return {"why": "intent.md declares no Repo:, so its line under ## Units cannot be found"}
    store = store_of(ref["ws"], repo, state)
    if "why" in store:
        return {"why": store["why"]}
    ideas = nullish(dig(state, "ideas", store["ws"]), [])
    read = next((i for i in ideas if f"{js(dig(i, 'id'))}.md" == ref["file"]), None)
    if not read:
        return {"why": f"the app knows no {store['ws']}/{IDEAS}/{ref['file']}"}
    line = next((u for u in read["units"] if dig(u, "ref") == f"{repo}/{unit['name']}"), None)
    if line:
        return {"line": line}
    return {"why": f"it does not list {repo}/{unit['name']} under ## Units"}


def resolve_links(unit, links, state):
    idea = nullish(dig(links, "idea"))
    repo = nullish(dig(links, "repo"))
    depends_on = nullish(dig(links, "dependsOn"))
    related = []
    for b in nullish(dig(links, "backlog"), []):
        d = {**dependency(dig(b, "ref"), unit, repo, state), "source": dig(b, "source")}
        if d["why"] != "dropped" and not d["why"].startswith("rejected"):
            related.append(d)
    if idea is None and repo is None and depends_on is None and not related:
        return
    needs = []
    if idea is not None:
        unit["idea"] = idea
    if repo is not None:
        unit["repo"] = repo
        if not valid_ws(repo):
            unit["problems"].append(f'intent.md: Repo: "{repo}" is not a workspace name')
    if depends_on is not None or related:
        unit["dependsOn"] = [
            *(dependency(ref, unit, repo, state) for ref in nullish(depends_on, [])),
            *related,
        ]
        for d in unit["dependsOn"]:
            if d["merged"] is None:
                where = "backlog relation" if d.get("source") else "intent.md: Depends on:"
                unit["problems"].append(f"{where} {js(d['ref'])} — {d['why']}")
    if idea is not None:
        found = idea_line(idea, unit, repo, state)
        if "why" in found:
            unit["problems"].append(f"intent.md: Idea: {idea} — {found['why']}")
            needs.append(f"Idea: {idea} cannot be read: {found['why']}")
        else:

            def full(r):
                ref = parse_unit_ref(r)
                return f"{repo}/{r}" if ref and ref["ws"] is None else r

            listed = sorted(full(r) for r in found["line"]["dependsOn"])
            declared = sorted(full(r) for r in nullish(depends_on, []))
            if ", ".join(listed) != ", ".join(declared):
                needs.append(
                    f"{idea} lists {repo}/{unit['name']} with Depends on: "
                    f"{', '.join(listed) or 'nothing'}, but intent.md declares "
                    f"{', '.join(declared) or 'none'} — the two must match"
                )
    waiting = [d for d in unit.get("dependsOn", []) if d["merged"] is not True]
    LINKS[id(unit)] = (unit, {"needs": needs, "waiting": waiting})


def link_needs(unit):
    kept = links_of(unit)
    return [
        *kept["needs"],
        *(
            f"waits on {js(d['ref'])}: {d['why']} "
            f"({'backlog relation' if d.get('source') else 'Depends on:'})"
            for d in kept["waiting"]
        ),
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
    if kept["needs"]:
        return {
            "blocked": True,
            "action": f"fix the idea link — {kept['needs'][0]}",
            "stage": "",
            "why": "unreadable",
        }
    return answer


# --- deciding: the pieces `read_unit` and every rule share ----------------------------


def agent_skip(u, f):
    return status_of(u, f) == "skipped" and bool(dig(u, "artifacts", f, "agentSkip"))


def skipped_by(u, f):
    by = nullish(dig(u, "artifacts", f, "agentSkip", "by"), "no one the app knows")
    return f"{f} is skipped by {by}, not by a person or their delegate"


def missing(u, f):
    return f"{f} exists but carries no Status line" if present(u, f) else f"{f} does not exist"


def unmeasured_of(u):
    return nullish(dig(u, "artifacts", "spec.md", "unmeasured"), {"ids": [], "problems": []})


LANE_FULL = re.compile(r"\bLane:\s*full\b", A)


def header_of(text):
    lines = split_lines(text)
    end = _find(lines, lambda l: l.startswith("## "))
    return "\n".join(lines if end == -1 else lines[:end])


def fenced_filled(lines):
    inside = None
    for l in lines or []:
        if re.match(r"^\s*```", l, A):
            if inside is not None and any(trim(x) != "" for x in inside):
                return True
            inside = [] if inside is None else None
        elif inside is not None:
            inside.append(l)
    return False


SOURCE_LINE = re.compile(r"^Source:\s*(`?)([^\s`:]+)(?::(\d+)-(\d+))?\1\s*$", A)


def expected_cited(lines):
    if not lines:
        return False
    sources = [l for l in lines if trim(l).startswith("Source:")]
    if len(sources) != 1:
        return False
    m = SOURCE_LINE.match(trim(sources[0]))
    if not m:
        return False
    path, start, end = m[2], m[3], m[4]
    if (
        path.startswith("/")
        or path == ".cos"
        or path.startswith(".cos/")
        or ".." in path.split("/")
    ):
        return False
    if start is not None and (int(start) < 1 or int(end) < int(start)):
        return False
    return any(trim(l) != "" and not trim(l).startswith("Source:") for l in lines)


def lane_of(unit, intent=None, impl=None):
    def lines(title):
        return None if intent is None else section(intent, title)

    marks = {
        "a": unit.get("type") == "fix",
        "b": fenced_filled(lines("Reproduction")),
        "c": expected_cited(lines("Expected")),
        "d": any(trim(l) != "" for l in nullish(lines("Actual"), [])),
        "e": not present(unit, "spec.md")
        and not present(unit, "plan.md")
        and not LANE_FULL.search(header_of(impl or "")),
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
            reasons.append(f"{id_}: spike.md has no ## {id_}")
        elif dig(item, "verdict") is None:
            missing_ids.append(id_)
            reasons.append(
                f"{id_}: spike.md ## {id_} has no readable Verdict: holds. or Verdict: fails."
            )
        elif not item.get("hasBlock"):
            missing_ids.append(id_)
            reasons.append(
                f"{id_}: spike.md ## {id_} carries no fenced block with the command and what it printed"
            )
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
    need = list(unmeasured_of(unit)["problems"])
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
    claims = needs_person_claims(unit)
    return [
        {
            "id": f["id"],
            "reason": next((c["reason"] for c in claims if c["id"] == f["id"]), None),
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


def parse_type(text):
    m = re.search(r"\bType:\s*([A-Za-z]+)", text, A)
    return m[1].lower() if m else None


def branch_for(unit_name, type_):
    match = UNIT_RE.fullmatch(js(nullish(unit_name, "")))
    if not match:
        return {"error": f'"{js(unit_name)}" does not match NNNN_<slug>'}
    if not type_:
        return {"error": f"{unit_name}/intent.md declares no Type: — one of {TYPE_LIST}"}
    if type_ not in BRANCH_TYPES:
        return {"error": f'"{js(type_)}" is not one of {TYPE_LIST}'}
    return {"branch": f"{type_}/{branch_slug(match[2])}"}


# --- a unit -------------------------------------------------------------------------------


def _artifact(unit, known, file, text):
    status = status_in(known, file)
    if status is None:
        unit["problems"].append(f"{file} carries no Status line")
    elif status not in VALID[file]:
        unit["problems"].append(
            f'{file} has status "{js(status)}", not one of {", ".join(VALID[file])}'
        )
    a: dict[str, Any] = {
        "status": status,
        "skipReason": parse_skip_reason(text) if file == "plan.md" else None,
    }
    unit["artifacts"][file] = a
    by = nullish(dig(known, "artifacts", file, "authority"))
    if status == "skipped" and by not in DECIDERS:
        a["agentSkip"] = {"by": by}
    if file == "pr.md":
        a["pr"] = parse_pr(text)
        title = pr_text(text)["title"]
        if title is not None:
            a["title"] = title
    if file == "review.md":
        rows = nullish(dig(known, "artifacts", file, "rounds"), [])
        a["review"] = review_from(rows, parse_review(text)) if rows else parse_review(text)
        answers = answers_in(known, file)
        ids = [a_.get("id") for a_ in answers if not ("id" in a_ and a_["id"] is None)]
        a["personAnswers"] = list(dict.fromkeys(ids))
        more = parse_more_rounds(text)
        if more["granted"] > 0:
            a["roundsGranted"] = more["granted"]
        unit["problems"].extend(f"review.md: {p}" for p in more["problems"])
    if file == "impl.md":
        said = parse_needs_person(text)
        claimed = dig(known, "artifacts", file, "result", "needs_person")
        a["needsPerson"] = (
            [
                {"id": i, "reason": next((c["reason"] for c in said if c["id"] == i), None)}
                for i in claimed
            ]
            if isinstance(claimed, list)
            else said
        )
    result = nullish(dig(known, "artifacts", file, "result"))
    if file == "spec.md" and truthy(result):
        ids = list(nullish(dig(result, "unmeasured"), []))
        if ids:
            a["unmeasured"] = {"ids": ids, "problems": []}
    elif file == "spec.md":
        unmeasured = parse_unmeasured(text)
        if unmeasured["ids"] or unmeasured["problems"]:
            a["unmeasured"] = unmeasured
            unit["problems"].extend(unmeasured["problems"])
    if file == "spike.md":
        parsed = parse_spike(text)
        a["spike"] = (
            {
                "round": parsed["round"],
                "items": {
                    js(dig(v, "id")): {"verdict": dig(v, "verdict"), "hasBlock": True}
                    for v in nullish(dig(result, "verdicts"), [])
                },
            }
            if truthy(result)
            else parsed
        )
    if file == "ship.md":
        ship = parse_ship(text)
        if ship["round"] is not None or ship["refused"] is not None:
            a["ship"] = ship
    if file == "plan.md" and required(unit, SPIKE):
        a["citesSpike"] = "spike.md" in text
    questions = join_answers(
        nullish(dig(known, "artifacts", file, "questions")), answers_in(known, file)
    )
    if questions is not None:
        a["questions"] = questions


def read_unit(dir_, name, state):  # noqa: C901, PLR0915 - `readUnit` kept whole
    """`readUnit`: `state` is the snapshot; the files are read for what it does not keep."""
    unit: dict[str, Any] = {"name": name, "artifacts": {}, "problems": []}
    known = nullish(entry_of(state, state["workspace"], name), NO_ENTRY)
    intent_text = None
    texts = {}
    match = UNIT_RE.fullmatch(name)
    if not match:
        unit["problems"].append("directory name does not match NNNN_<slug>")
    else:
        unit["number"] = int(match[1])
        unit["slug"] = match[2]

    for file in ARTIFACTS:
        path = os.path.join(dir_, file)
        if not os.path.exists(path):
            continue
        text = read_text(path)
        texts[file] = text
        if file == "intent.md":
            intent_text = text
        _artifact(unit, known, file, text)
    for file, a in nullish(dig(known, "artifacts"), {}).items():
        if not dig(a, "status") or file in unit["artifacts"]:
            continue
        if a["status"] == "skipped" and dig(a, "authority") in DECIDERS:
            unit["artifacts"][file] = {"status": a["status"], "skipReason": None}
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
            unit["problems"].append(
                f'intent.md declares no Type — add "Type: <{BRANCH_TYPES[0]}|…>" to its header'
            )
        elif type_ not in BRANCH_TYPES:
            unit["problems"].append(
                f'intent.md has type "{js(type_)}", not one of {", ".join(BRANCH_TYPES)}'
            )
        else:
            unit["type"] = type_
        unit["branch"] = nullish(branch_for(name, type_).get("branch"))
    unit.update(lane_of(unit, intent=intent_text, impl=texts.get("impl.md")))
    if (
        unit["enteredFast"]
        and status_of(unit, "impl.md") == "draft"
        and LANE_FULL.search(header_of(texts.get("impl.md", "")))
    ):
        unit["artifacts"]["impl.md"]["leftLane"] = True
    unit["outcome"] = None if intent_text is None else unit_outcome(intent_text)

    held: Any = {"hold": None, "problems": []}
    if intent_text is not None:
        held = fold_holds(nullish(dig(known, "holds"), []))
    unit["problems"].extend(f"intent.md: {p}" for p in held["problems"])
    unit["hold"] = held["hold"]
    ended = ended_of(unit)
    if ended and unit["hold"]:
        unit["problems"].append(
            f"intent.md carries a hold block, but the unit is {ended} — it is ignored"
        )
        unit["hold"] = None
    hold_state = dig(unit, "hold", "state") if unit["hold"] else "active"
    unit["holdMoves"] = [] if ended or intent_text is None else list(HOLD_MOVES[hold_state])

    rerun = parse_reruns(intent_text)
    unit["problems"].extend(f"intent.md: {p}" for p in rerun["problems"])
    for file, text in texts.items():
        if not rerun["reruns"] or not settled(status_of(unit, file)):
            continue
        if file == "spike.md" and not unmeasured_of(unit)["ids"]:
            continue
        digest = above_answers(text)
        by = next((r for r in reversed(rerun["reruns"]) if r["stale"].get(file) == digest), None)
        if by:
            unit["artifacts"][file]["stale"] = {"stage": by["stage"], "date": by["date"]}

    # The app's `{stage: {decisions, main}}` for an accepted spec or plan something came after;
    # only when there is one, so `status --json` of every other unit stays as it was.
    outdated = dig(known, "outdated")
    if isinstance(outdated, dict) and outdated:
        unit["outdated"] = outdated

    if intent_text is not None:
        resolve_links(unit, nullish(dig(known, "links"), NO_ENTRY["links"]), state)
    return unit


def read_all(cos_dir, state):
    """`readAll`: every directory under `cos_dir` but `ideas/`, by name."""
    if not os.path.exists(cos_dir):
        return []
    with os.scandir(cos_dir) as it:
        names = [e.name for e in it if e.is_dir(follow_symlinks=False) and e.name != IDEAS]
    return [read_unit(os.path.join(cos_dir, n), n, state) for n in sorted(names)]
