"""Running one step of one unit, and recording what it cost.

The shape is fixed by two things the spec settled and one the plan had to.

`spec.md` R4: a step reads the step before it. The prompt is built from the unit's
`intent.md` plus the artifact of the previous stage, verbatim, and the names of what went
in are recorded so a reader can check that it happened rather than take it on trust.

`spec.md` R9 and `plan.md` Risk 1: the six prose stages get no tools **in either mode**, so
the session cannot write its own artifact — a session with no tools cannot write a file.
The spec's design section says the agent writes it; that and R9 cannot both hold. This
module implements the reading that keeps R9, the zero-tool default and `chat-only-sessions-have-tools`: **the app
holds the pen for `.cos/`, and the session only returns text.**

The rules a stage follows come from **this app's** `.claude/skills/`, never the
workspace's, for the same reason `board.py` runs its own `cos.mjs`: a workspace is a
repository somebody cloned, and its files are that repository's to write.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, AsyncIterator

import claude_agent_sdk as sdk

from cos_baodo.journal import Journal
from cos_baodo.policy import Grant, decide, grant_for, is_prose_stage
from cos_baodo.sessions import Refused, Sessions

# Where a unit lives, and what may be a unit name. Same shape `cos.mjs` enforces; matched
# here because this module builds a path out of it and a path built from unlaundered text
# is how a directory traversal starts.
UNIT_RE = re.compile(r"^\d{4}_[a-z0-9]+(?:-[a-z0-9]+)*$")
COS_DIR = ".cos"

# The app's own skills directory. `cos_baodo/` sits beside `.claude/` in the flat layout.
SKILLS = Path(__file__).resolve().parent.parent / ".claude" / "skills"

# An artifact has to carry one of these on its first line, or the gate cannot read it and
# `cos.mjs` will report the unit as broken. Checked before anything is written.
STATUS_RE = re.compile(r"\bStatus:\s*([A-Za-z]+)")


class RunError(Exception):
    """A step that cannot start, or one whose reply cannot be stored."""


def unit_dir(workspace: str | Path, unit: str) -> Path:
    """The directory of one unit, built from the workspace rather than read from input."""
    if not UNIT_RE.fullmatch(unit or ""):
        raise RunError(f"not a work unit name: {unit!r}")
    return Path(workspace).expanduser().resolve() / COS_DIR / unit


def skill_for(stage: str) -> str:
    """The rules for a stage, from this app's copy. Missing is not fatal."""
    for name in (f"write-{stage}", stage):
        path = SKILLS / name / "SKILL.md"
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def build_prompt(
    workspace: str | Path,
    unit: str,
    stage: str,
    stages: list[str],
    artifact: str,
    writes_own: bool = False,
) -> tuple[str, list[str]]:
    """The prompt for one step, and the list of artifacts that went into it (`spec.md` R4).

    The list is returned rather than inferred later because R4 is checked against it: if a
    step ran without the previous stage's artifact in the prompt, the record says so.
    """
    directory = unit_dir(workspace, unit)
    included: list[str] = []
    parts: list[str] = []

    rules = skill_for(stage)
    if rules:
        parts.append(f"# The rules for this stage\n\n{rules}")

    intent = _read(directory / "intent.md")
    if intent:
        included.append("intent.md")
        parts.append(f"# The intent this work is authorised by\n\n{intent}")

    # The stage immediately before this one, whatever it is. Taken from the stage list the
    # board was read with, so the order is not restated here.
    position = stages.index(stage) if stage in stages else -1
    for earlier in reversed(stages[:position]):
        name = f"{earlier}.md"
        text = _read(directory / name)
        if text and name not in included:
            included.append(name)
            parts.append(f"# The {earlier} it follows\n\n{text}")
            break

    location = Path(COS_DIR) / unit / artifact
    if writes_own:
        # A stage with tools does the work and then records it. Asking it to *reply* with
        # the file as well would mean the file and the reply could disagree.
        parts.append(
            f"# Your task\n\n"
            f"Do the work this unit's plan authorises, in the repository at "
            f"`{Path(workspace).expanduser().resolve()}`, then write `{location}` "
            "recording what you did.\n\n"
            "That file must carry the `Status:` line the rules above describe. Prose in "
            "Vietnamese; filenames and headings in English. Write it yourself with your "
            "tools — do not paste it into your reply."
        )
    else:
        parts.append(
            f"# Your task\n\n"
            f"Write `{artifact}` for the work unit `{unit}`.\n\n"
            "Reply with the file's complete contents and nothing else — no preamble, no "
            "code fence, no commentary. The first lines must carry the `Status:` line the "
            "rules above describe. Prose in Vietnamese; filenames and headings in English."
        )
    return "\n\n---\n\n".join(parts), included


def check_reply(text: str) -> str:
    """The reply, ready to be written, or a reason it is not an artifact.

    Refusing here rather than writing and letting the gate complain later keeps a
    half-formed file from ever reaching the directory a human reads.
    """
    body = (text or "").strip()
    if not body:
        raise RunError("the session returned nothing")
    # A model that wrapped the file in a fence is easy to recover from and pointless to
    # fail on. Anything else is left exactly as it came.
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            body = "\n".join(lines[1:-1]).strip()
    if not STATUS_RE.search(body):
        raise RunError("the reply carries no `Status:` line, so the gate could not read it")
    return body + "\n"


# How the SDK says a turn ran out of room. `terminal_reason` is the field that carries it;
# older CLIs leave it unset and put a hint in `subtype`, so both are folded into one string
# before this looks at it.
CEILING_MARKERS = ("max_turns", "max_budget", "budget")


def _hit_ceiling(terminal: str) -> bool:
    text = (terminal or "").lower()
    return any(marker in text for marker in CEILING_MARKERS)


class Denials:
    """Counts what a step was refused, and keeps the first few reasons.

    Counting matters more than it looks. A step that finished having been told no fifty
    times did not do what it was asked; it worked around it, and the journal is the only
    place that difference is visible afterwards.
    """

    KEEP = 5

    def __init__(self) -> None:
        self.count = 0
        self.reasons: list[str] = []

    def record(self, tool: str, reason: str) -> None:
        self.count += 1
        if len(self.reasons) < self.KEEP:
            self.reasons.append(f"{tool}: {reason}")


def permission_gate(grant: Grant, workspace: str, denials: Denials):
    """The callback the SDK asks before every tool call.

    This is the enforcement `spec.md` R10 asks for, and it is separate from the tool list
    on purpose: `chat-only-sessions-have-tools` measured that the list does not cover every source of capability.
    """

    async def can_use_tool(tool: str, tool_input: dict, context: Any):
        reason = decide(grant, tool, tool_input or {}, workspace)
        if reason:
            denials.record(tool, reason)
            return sdk.PermissionResultDeny(message=reason)
        return sdk.PermissionResultAllow()

    return can_use_tool


class Runner:
    """Runs one step. Owns no state of its own beyond what it was handed."""

    def __init__(self, sessions: Sessions, journal: Journal | None):
        self.sessions = sessions
        self.journal = journal

    async def run(
        self,
        workspace: str,
        journal_key: str,
        unit: str,
        stage: str,
        artifact: str,
        stages: list[str],
        mode: str,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield `("chunk", text)` while the reply arrives, then one `("done", {...})`.

        The same shape `Sessions.stream` uses, so the page and a proof command consume one
        stream rather than two.
        """
        grant = grant_for(stage, mode)
        directory = unit_dir(workspace, unit)
        if not directory.exists():
            raise RunError(f"no such work unit in {workspace}: {unit}")

        if grant.opens_anything and is_prose_stage(stage):
            # Belt and braces against a future edit to the table: a prose stage that
            # somehow acquired tools would silently stop being covered by `chat-only-sessions-have-tools`.
            raise RunError(f"{stage} is a prose stage and must not carry tools")

        prompt, included = build_prompt(
            workspace, unit, stage, stages, artifact, writes_own=not grant.app_writes_artifact
        )

        if self.journal is not None:
            self.journal.started(
                journal_key, unit, stage, mode,
                prompt_chars=len(prompt), included=included,
                granted=list(grant.tools), max_turns=grant.max_turns,
            )

        denials = Denials()
        collected = ""
        terminal = ""
        session_id = ""
        cost: dict[str, Any] = {}
        outcome = "failed"
        detail = ""
        try:
            async for kind, payload in self.sessions.stream(
                workspace,
                prompt,
                None,
                max_turns=grant.max_turns,
                # Only pass a list and a gate when something was actually granted. A step
                # with an empty grant gets exactly the session the app makes by default,
                # which is the one `chat-only-sessions-have-tools` is about.
                can_use_tool=permission_gate(grant, workspace, denials) if grant.opens_anything else None,
                tools=list(grant.tools) if grant.opens_anything else None,
                max_budget_usd=grant.max_budget_usd or None,
            ):
                if kind == "chunk":
                    collected += payload
                    yield ("chunk", payload)
                else:
                    session_id = payload.get("session_id", "")
                    cost = payload.get("cost", {}) or {}
                    terminal = str(payload.get("terminal_reason") or "")

            if grant.app_writes_artifact:
                (directory / artifact).write_text(check_reply(collected), encoding="utf-8")
            else:
                # The session had the tools to write it. Believing it did, rather than
                # looking, is how a step reports success for a file that is not there.
                written = directory / artifact
                if not written.exists():
                    raise RunError(f"the step did not write {artifact}")
                if not STATUS_RE.search(written.read_text(encoding="utf-8", errors="replace")):
                    raise RunError(f"{artifact} carries no `Status:` line")
            outcome = "done"
        except (RunError, Refused) as e:
            detail = str(e)
            # A step stopped by its own ceiling did not fail in the ordinary sense — it was
            # bounded. `journal.OUTCOMES` keeps the two apart so a reader can tell a defect
            # from a limit working as intended (`0005` R11).
            if _hit_ceiling(terminal):
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal} — {detail}"
        except Exception as e:  # surfaced as data; the process keeps serving
            detail = f"{type(e).__name__}: {e}"
            if _hit_ceiling(terminal):
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal}"
        else:
            if _hit_ceiling(terminal):
                # It wrote something, but it ran out of room doing it. Saying `done` here
                # would hide that the work may be half finished.
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal}"
        finally:
            if self.journal is not None:
                self.journal.finished(
                    journal_key, unit, stage, outcome,
                    session_id=session_id,
                    artifact=artifact if outcome == "done" else None,
                    detail=detail or None,
                    denials=denials.count,
                    denied=denials.reasons or None,
                    **cost,
                )

        yield (
            "done",
            {
                "unit": unit,
                "stage": stage,
                "outcome": outcome,
                "artifact": artifact if outcome == "done" else None,
                "session_id": session_id,
                "included": included,
                "error": detail,
                "cost": cost,
            },
        )
