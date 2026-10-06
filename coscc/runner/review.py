"""A review's rounds as `review.md` holds them: merging a round into it, rendering a round from
its object. Which findings are open is read from `cos.db`'s rounds, never from this file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from coscc.runner.reply import RunError


def _sections(text: str) -> list[tuple[int, str]]:
    """`text` cut at each `## ` heading: `(start, section)`, the part before the first included."""
    starts = [0, *(m.start() for m in re.finditer(r"^## ", text, re.MULTILINE))]
    return [(a, text[a:b]) for a, b in zip(starts, [*starts[1:], len(text)]) if b > a]


def _is_round(section: str) -> bool:
    return re.match(r"## Round \d+\b", section) is not None


def _round_number(section: str) -> int:
    found = re.match(r"## Round (\d+)", section)
    # Every section `_is_round` keeps starts so.
    return int(found.group(1)) if found else 0


def merge_review(existing: str, reply: str) -> str:
    """`review.md` from what is on disk and a reply carrying only the new round.

    The header (everything before the first `## Round`) comes from the reply. Earlier rounds come
    from the file, verbatim. A round in the reply whose
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
    return [sec.rstrip() for _, sec in _sections(text or "") if _is_round(sec)]


# # The standard a round's screenshots are judged against, as the loop names it.
UI_STANDARD = ".claude/rules/ui-standard.md"


def finding_line(f: dict[str, Any]) -> str:
    """One finding of a round's object: `- F<k> [label] path:lines — severity — <criterion> text`."""
    label = f"fixed {f['fixed_in']}" if f["state"] == "fixed" else f["state"]
    where = f"{f['path']}:{f['lines']}" if f["path"] and f["lines"] else (f["path"] or "(none)")
    text_lines = (
        (f"{f['criterion']} " if f.get("criterion") else "") + f["text"].strip()
    ).splitlines() or [""]
    out = [f"- {f['id']} [{label}] {where} — {f['severity']} — {text_lines[0]}"]
    out += [f"  {line}" if line.strip() else "" for line in text_lines[1:]]
    return "\n".join(out).rstrip()


def criteria_table(criteria: list[Mapping[str, str]]) -> str:
    """The criteria a round graded, one row each: its id, met or not, what it was read from and
    the evidence."""
    cell = lambda t: " ".join(str(t).split()).replace("|", "\\|")
    rows = [
        f"| {c['criterion']} | {c['met']} | {cell(c['source'])} | {cell(c['evidence'])} |"
        for c in criteria
    ]
    return "\n".join(["| Criterion | Met | Source | Evidence |", "|---|---|---|---|", *rows])


def render_round(
    section: str, n: int, head: str, obj: dict[str, Any], screens: dict[str, Any] | None
) -> str:
    """A round of `review.md` from the object its run handed back.

    The app decides the heading's number, the line naming the head it read and the verdict,
    `### Criteria`, `### Findings` and `### Screens`. Other `###` sections the session wrote are kept in place;
    whatever it wrote above its first `###` is not. A missing one of them is
    added at the end; `### Screens` written while the object names no screenshot is dropped.
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
    rendered = {
        "### Criteria": criteria_table(obj["criteria"]) if obj.get("criteria") else "None.",
        "### Findings": findings,
    }
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
        if heading in ("### Criteria", "### Findings", "### Screens"):
            if heading in rendered and heading not in done:
                out += [heading, "", rendered[heading], ""]
                done.add(heading)
            continue
        out += [heading, *lines]
    for heading, text in rendered.items():
        if heading not in done:
            out += [heading, "", text, ""]
    return "\n".join(out).rstrip()


def replace_new_rounds(text: str, before: set[int], rendered: str) -> str:
    """`review.md` with every round not numbered in `before` taken out and `rendered` put where
    the first of them stood. Earlier rounds and the header stay byte for byte.
    """
    kept: list[str] = []
    placed = False
    last = 0
    for start, section in _sections(text):
        if not _is_round(section):
            continue
        kept.append(text[last:start])
        last = start + len(section)
        if _round_number(section) in before:
            kept.append(section)
        elif not placed:
            kept.append(rendered + "\n\n")
            placed = True
    kept.append(text[last:])
    out = "".join(kept)
    if not placed:
        out = out.rstrip() + "\n\n" + rendered + "\n"
    return out.rstrip() + "\n"
