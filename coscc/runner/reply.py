"""Checking what a session returned before it becomes an artifact: its title, its
fences, and whether it was cut at a ceiling.
"""

from __future__ import annotations

from pathlib import Path


class RunError(Exception):
    """A step that cannot start, or one whose reply cannot be stored."""


class OpeningError(RunError):
    """A reply refused for its opening alone, and what it lacked: the one refusal a repair turn
    may follow.
    """

    def __init__(self, reason: str, problem: str):
        super().__init__(reason)
        self.problem = problem


class _Stopped(Exception):
    """A Stop came before `steps.seal`, so the artifact is not to be written."""


# # How much of an unusable reply to keep beside the reason it was refused. Chosen, not measured.
REPLY_KEPT = 2000


# # Characters of transcript kept for a failed attempt. Chosen, and too short: the earliest
# # relevant tool output in a run that hit its ceiling started 88k-102k characters from the end.
ATTEMPT_EXCERPT = 8000


def _with_reply(reason: str, collected: str) -> str:
    """The reason a step failed, with the reply that caused it when there is one."""
    body = (collected or "").strip()
    if not body:
        return reason
    kept = body[-REPLY_KEPT:]
    more = "" if len(body) <= REPLY_KEPT else f" (last {REPLY_KEPT} of {len(body)} chars)"
    return f"{reason}\n--- what the session replied{more} ---\n{kept}"


def unfence(text: str) -> str:
    """The reply stripped, and out of the fence it came wrapped in, if it came in one."""
    body = (text or "").strip()
    if not body:
        raise RunError("the session returned nothing")
    # A model that wrapped the file in a fence is easy to recover from; anything else is left
    # exactly as it came.
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            body = "\n".join(lines[1:-1]).strip()
    return body


def _title(artifact: str) -> str:
    """`# Plan:` for `plan.md`: how every `write-*` skill's template opens its file."""
    return "# " + Path(artifact).stem.capitalize() + ":"


def from_title(text: str, artifact: str) -> str:
    """`text` from its last title line outside a code fence, or all of it.

    The last, not the first: a session that drafts the artifact, reads again and writes it
    anew has made the draft narration.
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
    """`None` when `text` opens with its title, else what it lacks. Nothing else of the template
    is checked.
    """
    lines = text.splitlines()
    first = lines[0] if lines else ""
    return None if first.startswith(_title(artifact)) else f"no `{_title(artifact)}` title"


def opening_reason(artifact: str, problem: str, blocks: int | None) -> str:
    """English, as every other reason in this module is."""
    reason = f"{artifact} lacks its opening: {problem}"
    if blocks is not None:
        reason += f" (the session replied in {blocks} block{'s' if blocks != 1 else ''})"
    return reason


def opening_prompt(artifact: str, problem: str) -> str:
    """What the app sends when it reopens a prose step whose reply lacked its opening. English:
    an instruction to the model.
    """
    title = _title(artifact)
    if artifact == "review.md":
        # The earlier rounds are the app's to keep.
        whole = (
            "Reply with the title, the header line and your new round only. The earlier "
            "rounds of `review.md` are the app's to keep; do not copy them."
        )
    else:
        whole = f"Reply with the whole of `{artifact}` again, and nothing else."
    return (
        f"Your reply could not be written as `{artifact}`: it lacks its opening: {problem}.\n\n"
        "You have no tools now; do not try to call one. "
        f"{whole} No preamble, no code fence. Its first line is the title, opening with "
        f"`{title}`."
    )


def _after_tool(text: str) -> str:
    """The text so far, ending a line before the next piece."""
    return text if not text or text.endswith("\n") else text + "\n"


def _unwrapped(piece: str, artifact: str) -> str:
    """One piece. A piece that is one fence with the artifact's title at its top comes out of the
    fence; any other piece is left as it came.

    Narration, a tool call, then the artifact in a fence: joined to the narration the piece no
    longer opens with the fence, and `from_title` would read its title as quoted.
    """
    body = piece.strip()
    if body.startswith("```"):
        inside = unfence(body)
        if inside != body and inside.startswith(_title(artifact)):
            return inside + "\n"
    return piece


def _joined(pieces: list[str], artifact: str | None = None) -> str:
    """The pieces a session said between its tool calls, each on a line of its own. Given
    `artifact`, a piece wrapped whole in a fence around it is unwrapped first.
    """
    text = ""
    for piece in pieces:
        text = _after_tool(text) + (_unwrapped(piece, artifact) if artifact else piece)
    return text
