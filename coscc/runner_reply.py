"""Checking what a session returned before it becomes an artifact: its opening line, its
fences, and whether it was cut at a ceiling. Split from `coscc/runner.py` (`0095`).
"""

from __future__ import annotations

import re
from pathlib import Path


# An artifact has to carry one of these on its first line, or the gate cannot read it and
# `cos.mjs` will report the unit as broken. Checked before anything is written.
STATUS_RE = re.compile(r"\bStatus:\s*([A-Za-z]+)")


class RunError(Exception):
    """A step that cannot start, or one whose reply cannot be stored."""


class _Stopped(Exception):
    """`0034`: a Stop came before `steps.seal`, so the artifact is not to be written."""


# A status as `cos.mjs` `parseStatus` reads it: the first `Status:` in the file, hyphenated
# words as one. Only the first -- a round or a finding quoting "Status: changes-requested"
# further down must not send a review that passed back to `impl`.
HEADER_STATUS_RE = re.compile(r"\bStatus:\s*([A-Za-z]+(?:-[A-Za-z]+)*)")


# How much of an unusable reply to keep beside the reason it was refused. Long enough to
# show whether the artifact is in there behind a preamble; short enough that a journal row
# stays a row. Chosen, not measured.
REPLY_KEPT = 2000


# `0019_a-failed-step-destroys-the-work-that-succeeded` plan step 3. Chosen, and measured
# to be too short. 8000 is the starting point `spec.md ## Answers, câu 2` names. Measured
# 2026-09-24 with `scripts/measure_0019_excerpt.py` on `0032`'s two exhausted `impl`
# transcripts, matching that unit's plan step 2 measurement commands (`measure_context.py
# --json` / `--strict-mcp`, `check_command(grant_for`, `npm test … wc -c`, `claude
# --help`, the `PermissionResultAllow` probe): the earliest such output started 87656
# characters from the end in `752523a2` and 101788 in `1a2ae5a7`. 8000 kept none of them
# in the first and only the last one (`--json --baseline`, 7868) in the second. Past plan step 3's
# 40000 stop line, so whether to filter by command or raise this is the initiator's call
# (plan.md step 3, the update of 2026-09-24); the number is left where the plan put it.
ATTEMPT_EXCERPT = 8000


def _with_reply(reason: str, collected: str) -> str:
    """The reason a step failed, with the reply that caused it when there is one."""
    body = (collected or "").strip()
    if not body:
        return reason
    kept = body[-REPLY_KEPT:]
    more = "" if len(body) <= REPLY_KEPT else f" (last {REPLY_KEPT} of {len(body)} chars)"
    return f"{reason}\n--- what the session replied{more} ---\n{kept}"


def _unfence(text: str) -> str:
    """The reply stripped, and out of the fence it came wrapped in, if it came in one."""
    body = (text or "").strip()
    if not body:
        raise RunError("the session returned nothing")
    # A model that wrapped the file in a fence is easy to recover from and pointless to
    # fail on. Anything else is left exactly as it came.
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            body = "\n".join(lines[1:-1]).strip()
    return body


def check_reply(text: str) -> str:
    """The reply, ready to be written, or a reason it is not an artifact.

    Refusing here rather than writing and letting the gate complain later keeps a
    half-formed file from ever reaching the directory a human reads. Since `0099` the
    write path asks `opening_problem` instead; `closing_round_problem` still asks this.
    """
    body = _unfence(text)
    if not STATUS_RE.search(body):
        raise RunError("the reply carries no `Status:` line, so the gate could not read it")
    return body + "\n"


def _title(artifact: str) -> str:
    """`# Plan:` for `plan.md`: how every `write-*` skill's template opens its file."""
    return "# " + Path(artifact).stem.capitalize() + ":"


def from_title(text: str, artifact: str) -> str:
    """`0099` R1, R2, R9. `text` from its last title line outside a code fence, or all of it.

    The last, not the first: a session that drafts the whole artifact, reads again and
    writes it anew has made the draft narration before the one that counts.
    """
    title = _title(artifact)
    fenced, start = False, None
    at = 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and line.startswith(title):
            start = at
        at += len(line)
    return text if start is None else text[start:]


def opening_problem(text: str, artifact: str) -> str | None:
    """`0099` R3. `None` when `text` opens with its title and a `Status:` header, else what
    it lacks. Nothing else of the template is checked.
    """
    lines = text.splitlines()
    first = lines[0] if lines else ""
    header = next((line for line in lines[1:] if line.strip()), "")
    missing = []
    if not first.startswith(_title(artifact)):
        missing.append(f"no `{_title(artifact)}` title")
    if not HEADER_STATUS_RE.search(header):
        missing.append("no `Status:` line in its header")
    return " and ".join(missing) or None


def opening_reason(artifact: str, problem: str, blocks: int | None) -> str:
    """`0099` R5. English, as every other reason in this module is (spec C4)."""
    reason = f"{artifact} lacks its opening: {problem}"
    if blocks is not None:
        reason += f" (the session replied in {blocks} block{'s' if blocks != 1 else ''})"
    return reason


def _after_tool(text: str) -> str:
    """`0099` spec *Design* 1, C2. The text so far, ending a line before the next piece."""
    return text if not text or text.endswith("\n") else text + "\n"


def _unwrapped(piece: str, artifact: str) -> str:
    """`0099` R9 for one piece. A piece that is one fence with the artifact's title at its
    top comes out of the fence; any other piece is left as it came.

    Review round 1, F1: narration, a tool call, then the artifact in a fence. Before `0099`
    only the fenced piece was kept, and `_unfence` took it out. Joined to the narration it
    no longer opens with the fence, and `from_title` reads its title as quoted.
    """
    body = piece.strip()
    if body.startswith("```"):
        inside = _unfence(body)
        if inside != body and inside.startswith(_title(artifact)):
            return inside + "\n"
    return piece


def _joined(pieces: list[str], artifact: str | None = None) -> str:
    """The pieces a session said between its tool calls, each on a line of its own. Given
    `artifact`, a piece wrapped whole in a fence around it is unwrapped first."""
    text = ""
    for piece in pieces:
        text = _after_tool(text) + (_unwrapped(piece, artifact) if artifact else piece)
    return text


# How the SDK says a turn ran out of room. `terminal_reason` is the field that carries it;
# older CLIs leave it unset and put a hint in `subtype`, so both are folded into one string
# before this looks at it.
CEILING_MARKERS = ("max_turns", "max_budget", "budget")


def _hit_ceiling(terminal: str) -> bool:
    text = (terminal or "").lower()
    return any(marker in text for marker in CEILING_MARKERS)
