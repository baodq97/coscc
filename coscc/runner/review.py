"""A review's rounds and findings: merging a round into `review.md`, reading which findings
are open, and checking a closing round.
"""

from __future__ import annotations

import re
from typing import Any

from coscc.runner.reply import HEADER_STATUS_RE, RunError, check_reply


# # One `## Round N` section of review.md: from its heading to the next `## ` heading.
_ROUND_RE = re.compile(r"^## Round \d+\b.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)


def _round_number(section: str) -> int:
    found = re.match(r"## Round (\d+)", section)
    # Every section `_ROUND_RE` finds starts so.
    return int(found.group(1)) if found else 0


def merge_review(existing: str, reply: str) -> str:
    """`review.md` from what is on disk and a reply carrying only the new round.

    The header (everything before the first `## Round`) comes from the reply, since the status
    moves every round. Earlier rounds come from the file, verbatim. A round in the reply whose
    number is already on disk must match it exactly, and one that differs is refused. A reply
    that adds no round is refused.
    """
    kept = _rounds(existing)
    on_disk = {_round_number(r): r for r in kept}
    changed, new = [], []
    for r in _rounds(reply):
        n = _round_number(r)
        if n not in on_disk:
            new.append(r)
        elif r != on_disk[n]:
            changed.append(r.splitlines()[0])
    if changed:
        raise RunError(
            "the reply changes an earlier review round, so review.md was left as it "
            f"was: {', '.join(changed)}"
        )
    if not new:
        raise RunError("the reply adds no review round, so review.md was left as it was")
    first = re.search(r"^## Round \d+\b", reply, re.MULTILINE)
    header = reply[: first.start()].rstrip() if first else reply.rstrip()
    return header + "\n\n" + "\n\n".join(kept + new) + "\n"


def _rounds(text: str) -> list[str]:
    """Every round already recorded, each exactly as it stands in the file."""
    return [m.group(0).rstrip() for m in _ROUND_RE.finditer(text or "")]


# # The standard a round's screenshots are judged against, as `cos.mjs` names it.
UI_STANDARD = ".claude/rules/ui-standard.md"


def finding_line(f: dict[str, Any]) -> str:
    """One finding of a round's object: `- F<k> [label] path:lines — severity — S<n> text`."""
    label = f"fixed {f['fixed_in']}" if f["state"] == "fixed" else f["state"]
    where = f"{f['path']}:{f['lines']}" if f["path"] and f["lines"] else (f["path"] or "(none)")
    text_lines = ((f"{f['rule']} " if f["rule"] else "") + f["text"].strip()).splitlines() or [""]
    out = [f"- {f['id']} [{label}] {where} — {f['severity']} — {text_lines[0]}"]
    out += [f"  {line}" if line.strip() else "" for line in text_lines[1:]]
    return "\n".join(out).rstrip()


def render_round(
    section: str, n: int, head: str, obj: dict[str, Any], screens: dict[str, Any] | None
) -> str:
    """A round of `review.md` from the object its run handed back.

    The app decides the heading's number, the line naming the head it read and the verdict,
    `### Findings` and `### Screens`. Other `###` sections the session wrote are kept in place;
    whatever it wrote above its first `###` is not. A missing `### Findings` or `### Screens` is
    added at the end; one written while the object names no screenshot is dropped.
    """
    body = section.splitlines()[1:]
    first = next((i for i, line in enumerate(body) if line.startswith("### ")), len(body))
    sections: list[tuple[str, list[str]]] = []
    for line in body[first:]:
        if line.startswith("### "):
            sections.append((line.rstrip(), []))
        else:
            sections[-1][1].append(line)
    findings = "\n".join(finding_line(f) for f in obj.get("findings") or ()) or "None."
    shots = list(obj.get("screens") or ())
    rendered = {"### Findings": findings}
    if shots and screens is not None:
        rendered["### Screens"] = (
            f"Taken at: {screens.get('taken') or '(no manifest)'}. Standard: {screens['standard']}. "
            f"Looked at by: {screens['by']}, from screenshots.\n\n"
            + "\n".join(
                f"- {s['path']} — {s['size'].replace('x', '×')} — {s['address']} — {s['result']}"
                for s in shots
            )
        )
    out = [f"## Round {n}", "", f"Reviewed: {head}. Verdict: {obj['verdict']}.", ""]
    done: set[str] = set()
    for heading, lines in sections:
        if heading in ("### Findings", "### Screens"):
            if heading in rendered and heading not in done:
                out += [heading, "", rendered[heading], ""]
                done.add(heading)
            continue
        out += [heading, *lines]
    for heading, text in rendered.items():
        if heading not in done:
            out += [heading, "", text, ""]
    return "\n".join(out).rstrip()


def replace_new_rounds(
    text: str, before: set[int], rendered: str, status: str | None = None
) -> str:
    """`review.md` with every round not numbered in `before` taken out and `rendered` put where
    the first of them stood. Earlier rounds and the header stay byte for byte, except the value
    of the header's first `Status:` when `status` is given.
    """
    if status is not None:
        first = re.search(r"^## Round \d+\b", text, re.MULTILINE)
        m = HEADER_STATUS_RE.search(text, 0, first.start() if first else len(text))
        if m is not None:
            text = text[: m.start(1)] + status + text[m.end(1) :]
    kept: list[str] = []
    placed = False
    last = 0
    for m in _ROUND_RE.finditer(text):
        kept.append(text[last : m.start()])
        last = m.end()
        if _round_number(m.group(0)) in before:
            kept.append(m.group(0))
        elif not placed:
            kept.append(rendered + "\n\n")
            placed = True
    kept.append(text[last:])
    out = "".join(kept)
    if not placed:
        out = out.rstrip() + "\n\n" + rendered + "\n"
    return out.rstrip() + "\n"


def _header_status(text: str) -> str | None:
    """The artifact's own status, read the way `cos.mjs` reads it."""
    m = HEADER_STATUS_RE.search(text or "")
    return m.group(1).lower() if m else None


# # A finding line as `cos.mjs` `FINDING` reads it, and the one label it counts closed: `fixed`
# # with a sha. An `[answered]` is closed only by a validated block under `## Answers`, which
# # is left to `cos.mjs`, so it is kept here.
_FINDING_RE = re.compile(r"^- (F\d+)\s+\[([^\]]*)\]")
_FIXED_RE = re.compile(r"^fixed\s+[0-9a-f]{7,40}$", re.IGNORECASE)


def open_findings(text: str) -> tuple[str, int | None, str]:
    """`review.md`'s header line, its last round's number, and that round's findings still open,
    each with the indented lines under it.

    Only cuts text out to embed in a prompt; `cos.mjs` is the one reader of findings that opens
    anything. "Open" is every label but `fixed <sha>` (a `fixed` with no sha and unreadable
    labels included), so nothing `cos.mjs` may count open is dropped from the prompt.

    The header is the line holding the first `Status:`. A file with no `## Round` is read whole
    below that line, as a single round with no number.
    """
    text = text or ""
    m = HEADER_STATUS_RE.search(text)
    start = text.rfind("\n", 0, m.start()) + 1 if m else 0
    end = text.find("\n", m.end()) if m else -1
    header = text[start : end if end != -1 else len(text)].strip() if m else ""
    rounds = _rounds(text)
    if rounds:
        body, number = rounds[-1], _round_number(rounds[-1])
    else:
        body, number = (text[end:] if m and end != -1 else ""), None
        body = re.split(r"^## Answers\s*$", body, maxsplit=1, flags=re.MULTILINE)[0]
    kept: list[str] = []
    keeping = False
    for line in body.splitlines():
        found = _FINDING_RE.match(line)
        if found:
            keeping = not _FIXED_RE.match(found.group(2).strip())
        elif not (line[:1].isspace() and line.strip()):
            keeping = False
            continue
        if keeping:
            kept.append(line)
    return header, number, "\n".join(kept)


# # The three sections of a round the closing turn writes, in this order.
INCOMPLETE_SECTIONS = ("### Reviewed so far", "### Findings", "### What was not reviewed")
# # A round's first non-blank line as `cos.mjs` `ROUND_META` reads it.
_ROUND_META_RE = re.compile(
    r"^Reviewed:\s*([0-9a-f]{7,40})\.?\s+Verdict:\s*(pass|changes-requested|needs-person|incomplete)\.?\s*$",
    re.IGNORECASE,
)


def _round_meta(section: str) -> tuple[str, str] | None:
    """A round's `(reviewed, verdict)`, lower-cased, or `None` when `cos.mjs` could not read it."""
    for line in section.splitlines()[1:]:
        if line.strip():
            m = _ROUND_META_RE.match(line.strip())
            return (m.group(1).lower(), m.group(2).lower()) if m else None
    return None


def _headings_in_order(section: str, headings: tuple[str, ...]) -> bool:
    lines = [line.rstrip() for line in section.splitlines()]
    at = 0
    for heading in headings:
        try:
            at = lines.index(heading, at) + 1
        except ValueError:
            return False
    return True


def closing_prompt(head: str, number: int) -> str:
    """What the app sends when it reopens a review that ran out of turns. English: an
    instruction to the model.
    """
    return (
        "You have run out of turns. You have no tools now; do not try to call one. Write "
        "down what this review has established so far, so the next review can go on from "
        "it instead of starting again.\n\n"
        "Reply with the title, the header line and one new round only, and nothing else — "
        "no preamble, no code fence. The earlier rounds of `review.md` are the app's to "
        "keep; do not copy them. Exactly this shape:\n\n"
        "- The header line carries `Status: draft.`\n"
        f"- Then `## Round {number}`.\n"
        f"- Its first line is exactly `Reviewed: {head}. Verdict: incomplete.`\n"
        "- Then three sections, in this order: `### Reviewed so far` (every file you "
        "opened and what you concluded about it), `### Findings` (every finding you have, "
        "in the usual `- F<k> [open] path:line — severity — text` form, and every finding "
        "an earlier round raised, carried forward with its id and label), and "
        "`### What was not reviewed` (what you did not reach, and what to read first).\n\n"
        "Never write `Verdict: pass` or `Verdict: changes-requested` here: a round the app "
        "cannot read as incomplete is not written at all."
    )


def closing_round_problem(existing: str, reply: str, head: str) -> str | None:
    """`None` when `reply` is a closing turn's round the app may write, else why not.

    Only an incomplete round, under a `draft` header, which `cos.mjs` reads as "review again".
    A full round is refused: it would open or close `ship` on a review that did not finish.
    `merge_review` decides the rest when the round is written.
    """
    try:
        body = check_reply(reply)
    except RunError as e:
        return str(e)
    if _header_status(body) != "draft":
        return f"its header is {_header_status(body) or 'unreadable'}, not draft"
    on_disk = {_round_number(r) for r in _rounds(existing)}
    new = [r for r in _rounds(body) if _round_number(r) not in on_disk]
    if len(new) != 1:
        return f"it adds {len(new)} review rounds, not one"
    # The number `closing_prompt` was given. `merge_review` keeps any number not on disk, and a
    # round skipped or reused closes `ship` for good.
    number = max(on_disk, default=0) + 1
    if _round_number(new[0]) != number:
        return f"{new[0].splitlines()[0]} is not ## Round {number}, the next round"
    meta = _round_meta(new[0])
    if meta is None or meta[1] != "incomplete":
        return f"{new[0].splitlines()[0]} does not open with Verdict: incomplete"
    if meta[0] != (head or "").lower():
        return f"{new[0].splitlines()[0]} names {meta[0]}, not the head this step ran on, {head}"
    if not _headings_in_order(new[0], INCOMPLETE_SECTIONS):
        return f"{new[0].splitlines()[0]} lacks {', '.join(INCOMPLETE_SECTIONS)}, in that order"
    return None
