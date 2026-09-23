"""Running one step of one unit, and recording what it cost.

The shape is fixed by two things the spec settled and one the plan had to.

`spec.md` R4: a step reads the step before it. The prompt is built from the unit's
`intent.md` plus the artifact of the previous stage, verbatim, and the names of what went
in are recorded so a reader can check that it happened rather than take it on trust.

`spec.md` R9 and `plan.md` Risk 1: the six prose stages get no tools **in either mode**, so
the session cannot write its own artifact — a session with no tools cannot write a file.
The spec's design section says the agent writes it; that and R9 cannot both hold. This
module implements the reading that keeps R9 and the zero-tool default: **the app
holds the pen for `.cos/`, and the session only returns text.**

The rules a stage follows come from **this app's own** skills, never the workspace's, for
the same reason `board.py` runs its own `cos.mjs`: a workspace is a repository somebody
cloned, and its files are that repository's to write. `coscc/harness.py` is the only thing
that answers where those skills are, and a step whose rules it cannot find does not run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, AsyncIterator

import claude_agent_sdk as sdk

from coscc import harness
from coscc.journal import Journal
from coscc.policy import Grant, beyond_reading, decide, grant_for, is_prose_stage
from coscc.sessions import Refused, Sessions

# An artifact has to carry one of these on its first line, or the gate cannot read it and
# `cos.mjs` will report the unit as broken. Checked before anything is written.
STATUS_RE = re.compile(r"\bStatus:\s*([A-Za-z]+)")


class RunError(Exception):
    """A step that cannot start, or one whose reply cannot be stored."""


def skill_for(stage: str) -> str:
    """The rules for a stage, from this app's copy. Missing stops the step.

    **This reverses a decision that had words on it.** Until 0012 the line above read
    *"Missing is not fatal"* and this returned `""`, and `build_prompt` below simply left
    the rules section out. `.cos/0012_installed-copy-runs-no-stage/intent.md` measured what
    that bought: on `v0.2.2` installed from the release, every skill resolved to nothing,
    so a step ran against a prompt 4.569 characters shorter, spent real quota, and wrote
    `included=['intent.md']` -- the same record a step with its full rules writes. Not
    fatal is only safe when the absence is small; the measurement says it was not.

    **It refuses in `RunError`, not in `MissingRules`.** `coscc/service.py` maps this
    module's refusals with one `except RunError`, and `coscc/api.py` turns that into a 400
    that names what went wrong. A second exception type crossing that boundary is not a
    second kind of refusal, it is a 500: measured 2026-09-22 on a workspace whose harness
    had `cos.mjs` but no skills -- an incomplete copy step, which is exactly the shape
    `wheel_complaints` exists to catch -- `MissingRules` escaped `run_step` and reached the
    route unhandled. Found by review, not by these tests.

    `spec.md` R4 and C2 carry the reversal and who decided it.
    """
    try:
        return harness.read_skill(f"write-{stage}", stage)
    except harness.MissingRules as e:
        raise RunError(f"no rules for the {stage} stage: {e}") from e


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def build_prompt(
    workspace: str | Path,
    directory: str | Path,
    unit: str,
    stage: str,
    stages: list[str],
    artifact: str,
    writes_own: bool = False,
) -> tuple[str, list[str]]:
    """The prompt for one step, and the list of artifacts that went into it (`spec.md` R4).

    The list is returned rather than inferred later because R4 is checked against it: if a
    step ran without the previous stage's artifact in the prompt, the record says so.

    `directory` is handed in rather than worked out here. Until `0014` this module derived
    it from `workspace`, and so did `coscc/board.py` and `coscc/service.py` — three copies
    of one formula, which is the shape `0012` paid a unit for. `coscc/units.py` is the one
    place that answers it now, and the two paths are no longer the same thing: the
    artifacts live in the product's own store while `workspace` stays the repository the
    work is done in, which is the whole of `0014` `spec.md` R2.
    """
    directory = Path(directory)
    included: list[str] = []
    parts: list[str] = []

    # First, and outside any `try`. `Runner.run` calls this before it touches the journal
    # and before `Sessions.stream` exists as a coroutine, so a `MissingRules` raised here
    # is `spec.md` R4's "0 requests to the SDK" by structure rather than by promise.
    parts.append(f"# The rules for this stage\n\n{skill_for(stage)}")

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

    location = directory / artifact
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


# How much of an unusable reply to keep beside the reason it was refused. Long enough to
# show whether the artifact is in there behind a preamble; short enough that a journal row
# stays a row. Chosen, not measured.
REPLY_KEPT = 2000


def _with_reply(reason: str, collected: str) -> str:
    """The reason a step failed, with the reply that caused it when there is one."""
    body = (collected or "").strip()
    if not body:
        return reason
    kept = body[-REPLY_KEPT:]
    more = "" if len(body) <= REPLY_KEPT else f" (last {REPLY_KEPT} of {len(body)} chars)"
    return f"{reason}\n--- what the session replied{more} ---\n{kept}"


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


def permission_gate(grant: Grant, workspace: str, denials: Denials, unit_dir: str | None = None):
    """The callback the SDK asks before every tool call.

    This is the enforcement `spec.md` R10 asks for, and it is separate from the tool list
    on purpose: the list was measured, and it does not cover every source of capability.
    """

    async def can_use_tool(tool: str, tool_input: dict, context: Any):
        reason = decide(grant, tool, tool_input or {}, workspace, unit_dir)
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
        directory: str | Path,
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
        directory = Path(directory)
        if not directory.exists():
            raise RunError(f"no such work unit for {workspace}: {unit}")

        if is_prose_stage(stage):
            # Belt and braces against a future edit to the table: a prose stage that
            # somehow acquired the ability to write, or to run a command, would silently
            # stop being covered.
            #
            # It asks `beyond_reading` rather than `opens_anything` since 2026-09-23.
            # `plan` now holds `Read`, `Glob` and `Grep`, because its own skill has always
            # required it to open the files it names and this guard was half of why it
            # never could. What the guard is actually for is unchanged: the app writes a
            # prose stage's artifact, so the stage must not be able to write it instead.
            beyond = beyond_reading(grant)
            if beyond:
                raise RunError(
                    f"{stage} is a prose stage and must not carry {', '.join(beyond)}"
                )

        prompt, included = build_prompt(
            workspace, directory, unit, stage, stages, artifact,
            writes_own=not grant.app_writes_artifact,
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
                # which is the one the zero-tool default is about.
                can_use_tool=(
                    permission_gate(grant, workspace, denials, str(directory))
                    if grant.opens_anything
                    else None
                ),
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
            # What the session said, kept. Until `0014` a prose step that produced an
            # unusable reply threw it away: the money was spent, the artifact was not
            # written, and the only record was the reason. A reply with no `Status:` line
            # is often a good artifact with a preamble in front of it, and a person who
            # can see it can decide that in a second — measured 2026-09-22, when a `spec`
            # step failed this way inside a paid proof run and left nothing to look at.
            detail = _with_reply(detail, collected)
            # A step stopped by its own ceiling did not fail in the ordinary sense — it was
            # bounded. `journal.OUTCOMES` keeps the two apart so a reader can tell a defect
            # from a limit working as intended (`spec.md` R11).
            if _hit_ceiling(terminal):
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal} — {detail}"
        except Exception as e:  # surfaced as data; the process keeps serving
            detail = _with_reply(f"{type(e).__name__}: {e}", collected)
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
