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

from coscc import gitops, harness
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
    gate_said: str = "",
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

    # The rules above open by telling this stage to run `cos.mjs gate` and stop if it
    # exits non-zero. Four of the six prose stages have no tools and never could, and on
    # 2026-09-23 the `ship` step of `0001` wrote `Status: draft` naming the unasked gate
    # as a reason -- while its gate was open. So the app asks, refuses to start the step
    # at all when the answer is no, and says so here. A step that is running has an open
    # gate by construction; this tells it that, so it stops treating "I could not check"
    # as "I must not proceed".
    if gate_said:
        parts.append(
            "# The gate, already asked\n\n"
            "The app ran `cos.mjs gate` for this stage before starting this step, and it "
            "is open. It would not have started otherwise. This is what the gate said:\n\n"
            f"    {gate_said}\n\n"
            "Do not ask it again and do not treat it as unasked — you may have no tools "
            "to run it with, and that is not a reason to hold back an artifact."
        )

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

    # A review that asked for changes sends the unit back to `impl`, and the whole point of
    # going back is the findings. Without this block the step that is meant to fix them
    # was built from `intent.md` and `plan.md` only -- it could not see a single one.
    #
    # Found 2026-09-23 on the first real round: `0015`'s review came back
    # `changes-requested` with five findings, the gate reopened `impl`, and nothing in the
    # prompt the app would have built mentioned any of them. `0015` promised the loop
    # "fix, then review again" and had built only the second half.
    #
    # Only while the review is `changes-requested`. A review that passed has nothing for
    # `impl` to act on, and one that rejected closed the unit.
    if stage in ("impl", "implement"):
        review = _read(directory / "review.md")
        if review and _header_status(review) == "changes-requested" and "review.md" not in included:
            included.append("review.md")
            parts.append(
                "# The review that sent this back\n\n"
                "The last review asked for changes. Fix every finding marked `[open]` below "
                "on the branch, one commit per finding where that is possible, then push "
                "the branch, then record in impl.md which commit fixed which finding. The "
                "next review is offered only once a fix is on the pull request, so a fix "
                "left unpushed keeps this unit on impl.\n\n"
                f"{review}"
            )

    # `review.md` accumulates rounds, and the app writes it from the reply -- so writing
    # it must not erase the rounds already there (`merge_review`). Found 2026-09-23 on
    # `0015`'s second review: round 1 and its five findings vanished from the file, and
    # with them the count `cos.mjs` reads to stop at N rounds and ask for a person. A loop
    # whose counter resets every run never reaches its limit.
    # A review is asked to check what was measured, and `impl.md` is where that is written.
    # The stage before `review` is `pr`, so without this `impl.md` never reached it -- and
    # since `0014` the unit lives outside the repository, so it could not be found by
    # looking either. Round 2 of `0015`'s review, 2026-09-23, left a finding open for
    # exactly that reason while the evidence it asked for sat in `impl.md`.
    if stage == "review":
        measured = _read(directory / "impl.md")
        if measured and "impl.md" not in included:
            included.append("impl.md")
            parts.append(f"# What was built and measured\n\n{measured}")

    if stage == "review":
        earlier = _rounds(_read(directory / "review.md"))
        if earlier:
            included.append("review.md")
            parts.append(
                "# The rounds so far\n\n"
                "These are already in `review.md` and the app keeps them. Do not copy them "
                "into your reply. Reply with the title, the header line and the next "
                "`## Round N` section only; the app writes the earlier rounds back under "
                "your header, unchanged, and appends yours after them.\n\n"
                + "\n".join(earlier)
            )

    location = directory / artifact
    if writes_own and stage == "ship":
        # `ship` runs outside every checkout (`service.step_cwd`): inside the unit's
        # worktree, `gh pr merge --delete-branch` merges and then exits 1. Calling this
        # directory "the repository" would send the session looking for one.
        parts.append(
            f"# Your task\n\n"
            f"Merge this unit's pull request, then write `{location}` recording what went "
            "out.\n\n"
            "You are deliberately not inside a git checkout. Name the pull request by the "
            "URL in `pr.md`'s `PR:` field in every `gh` command; a bare number cannot be "
            "resolved from here.\n\n"
            "That file must carry the `Status:` line the rules above describe. Prose in "
            "Vietnamese; filenames and headings in English. Write it yourself with your "
            "tools — do not paste it into your reply."
        )
    elif writes_own:
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


# One `## Round N` section of review.md: from its heading to the next `## ` heading.
_ROUND_RE = re.compile(r"^## Round \d+\b.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)


def _round_number(section: str) -> int:
    return int(re.match(r"## Round (\d+)", section).group(1))


def merge_review(existing: str, reply: str) -> str:
    """`review.md` from what is on disk and a reply carrying only the new round.

    Until 2026-09-23 the reply had to copy every earlier round byte for byte, and the app
    refused one that did not. On `0017` that copy is where it broke: twice a review was
    stopped mid-reply while reproducing round 2 -- once leaving a truncated round 2 in the
    file, once leaving nothing -- and each attempt paid to regenerate ~10k characters it
    was not asked to judge. The earlier rounds are the app's to keep, so the app keeps them.

    The header (everything before the first `## Round`) comes from the reply: the status
    moves every round. Earlier rounds come from the file, verbatim. A round in the reply
    whose number is already on disk must match it exactly -- so a reply written the old
    way is still accepted -- and one that differs is refused, as before. A reply that adds
    no round is refused: a review step that did not review has nothing to write.
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


# A status as `cos.mjs` `parseStatus` reads it: the first `Status:` in the file, hyphenated
# words as one. Only the first -- a round or a finding quoting "Status: changes-requested"
# further down must not send a review that passed back to `impl`.
HEADER_STATUS_RE = re.compile(r"\bStatus:\s*([A-Za-z]+(?:-[A-Za-z]+)*)")


def _header_status(text: str) -> str | None:
    """The artifact's own status, read the way `cos.mjs` reads it."""
    m = HEADER_STATUS_RE.search(text or "")
    return m.group(1).lower() if m else None


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


async def _head_of(cwd: str) -> str:
    """The commit a step ran on, for its `start` record — `""` when there is none to name.

    `0020` R5: the outcome is measured by checking a `spec.md`'s citations at the commit
    the stage read, and until this the run log never said which commit that was. A failure
    here costs the record one field; it never stops the step.
    """
    path = Path(cwd)
    if not (path / ".git").exists():
        return ""
    try:
        return await gitops.rev_parse(path, "HEAD")
    except gitops.GitError:
        return ""


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
        gate_said: str = "",
        cwd: str | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield `("chunk", text)` while the reply arrives, then one `("done", {...})`.

        The same shape `Sessions.stream` uses, so the page and a proof command consume one
        stream rather than two.

        `cwd` is where the step works — since `0017` the unit's own worktree. It is the
        session's directory, the write boundary and the repository the prompt names.
        `workspace` stays the membership question and the journal's subject. Unset, the
        two are the same directory, as they were before.
        """
        grant = grant_for(stage)
        directory = Path(directory)
        cwd = cwd or workspace
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
            cwd, directory, unit, stage, stages, artifact,
            writes_own=not grant.app_writes_artifact,
            gate_said=gate_said,
        )

        if self.journal is not None:
            self.journal.started(
                journal_key, unit, stage, mode,
                prompt_chars=len(prompt), included=included,
                granted=list(grant.tools), max_turns=grant.max_turns,
                head=await _head_of(cwd),
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
                cwd,
                prompt,
                None,
                max_turns=grant.max_turns,
                # Only pass a list and a gate when something was actually granted. A step
                # with an empty grant gets exactly the session the app makes by default,
                # which is the one the zero-tool default is about.
                can_use_tool=(
                    permission_gate(grant, cwd, denials, str(directory))
                    if grant.opens_anything
                    else None
                ),
                tools=list(grant.tools) if grant.opens_anything else None,
                max_budget_usd=grant.max_budget_usd or None,
                # Only named when it differs, so a stand-in `stream` written before `0017`
                # without a `workspace` parameter keeps working for a plain step.
                **({"workspace": workspace} if cwd != workspace else {}),
            ):
                if kind == "chunk":
                    collected += payload
                    yield ("chunk", payload)
                elif kind == "tool":
                    # Everything said before a tool call was said on the way to using it.
                    # For a stage whose artifact the app writes, that text is narration and
                    # the artifact is what comes after the last one.
                    #
                    # This cost nothing while no prose stage had tools. `plan` got `Read`,
                    # `Glob` and `Grep` on 2026-09-23 to fix a different defect, and from
                    # that hour every `plan.md` the board produced began with the step
                    # thinking out loud -- glued to the heading, so the file no longer
                    # opened with `# Plan:` and the `Status:` line was no longer the second.
                    # Measured on `0016_no-human-in-the-loop`: two sentences ahead of the
                    # title. `cos.mjs` still parsed it, because it looks for `Status:`
                    # anywhere, which is why this corrupted quietly instead of failing.
                    #
                    # Not forwarded. `coscc/api.py:227-231` treats every kind that is not
                    # `chunk` as the terminal `done` row, so a third kind reaching it would
                    # arrive at the client as a malformed `done`.
                    collected = ""
                else:
                    session_id = payload.get("session_id", "")
                    cost = payload.get("cost", {}) or {}
                    terminal = str(payload.get("terminal_reason") or "")

            if grant.app_writes_artifact:
                body = check_reply(collected)
                if artifact == "review.md":
                    body = merge_review(_read(directory / artifact), body)
                (directory / artifact).write_text(body, encoding="utf-8")
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
