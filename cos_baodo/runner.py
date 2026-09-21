"""Running one step of one unit, and recording what it cost.

The shape is fixed by two things the spec settled and one the plan had to.

`spec.md` R4: a step reads the step before it. The prompt is built from the unit's
`intent.md` plus the artifact of the previous stage, verbatim, and the names of what went
in are recorded so a reader can check that it happened rather than take it on trust.

`spec.md` R9 and `plan.md` Risk 1: the six prose stages get no tools **in either mode**, so
the session cannot write its own artifact — a session with no tools cannot write a file.
The spec's design section says the agent writes it; that and R9 cannot both hold. This
module implements the reading that keeps R9, the zero-tool default and `0007`: **the app
holds the pen for `.cos/`, and the session only returns text.**

The rules a stage follows come from **this app's** `.claude/skills/`, never the
workspace's, for the same reason `board.py` runs its own `cos.mjs`: a workspace is a
repository somebody cloned, and its files are that repository's to write.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, AsyncIterator

from cos_baodo.journal import Journal
from cos_baodo.policy import Grant, grant_for, is_prose_stage
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
    workspace: str | Path, unit: str, stage: str, stages: list[str], artifact: str
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

    parts.append(
        f"# Your task\n\n"
        f"Write `{artifact}` for the work unit `{unit}`.\n\n"
        "Reply with the file's complete contents and nothing else — no preamble, no code "
        "fence, no commentary. The first lines must carry the `Status:` line the rules "
        "above describe. Prose in Vietnamese; filenames and headings in English."
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
            # somehow acquired tools would silently stop being covered by `0007`.
            raise RunError(f"{stage} is a prose stage and must not carry tools")

        prompt, included = build_prompt(workspace, unit, stage, stages, artifact)

        if self.journal is not None:
            self.journal.started(
                journal_key, unit, stage, mode,
                prompt_chars=len(prompt), included=included,
            )

        collected = ""
        session_id = ""
        cost: dict[str, Any] = {}
        outcome = "failed"
        detail = ""
        try:
            async for kind, payload in self.sessions.stream(
                workspace, prompt, None, max_turns=grant.max_turns
            ):
                if kind == "chunk":
                    collected += payload
                    yield ("chunk", payload)
                else:
                    session_id = payload.get("session_id", "")
                    cost = payload.get("cost", {}) or {}
            body = check_reply(collected)
            (directory / artifact).write_text(body, encoding="utf-8")
            outcome = "done"
        except (RunError, Refused) as e:
            detail = str(e)
        except Exception as e:  # surfaced as data; the process keeps serving
            detail = f"{type(e).__name__}: {e}"
        finally:
            if self.journal is not None:
                self.journal.finished(
                    journal_key, unit, stage, outcome,
                    session_id=session_id,
                    artifact=artifact if outcome == "done" else None,
                    detail=detail or None,
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
