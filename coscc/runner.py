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

import asyncio
import os
import re
from pathlib import Path
from typing import Any, AsyncIterator

import claude_agent_sdk as sdk

from coscc import gitops, harness, instructions, steps
from coscc.integrate import check_started_by
from coscc import sessions as sessions_mod
from coscc.journal import Journal
from coscc.knowledge import STAGES as KNOWLEDGE_STAGES
from coscc.policy import Grant, beyond_reading, decide, grant_for_step, is_prose_stage
from coscc.sessions import Refused, Sessions

# `0095`: these moved to modules of their own. Every name is imported back, so
# `coscc.runner.<name>` still resolves; a patch reaches only the module that looks it up.
from coscc.runner_prompt import (
    skill_for,
    _read,
    answers_section,
    strip_answers,
    with_answers,
    _open_questions,
    _ANSWERS_ADVICE,
    _answers_block,
    _JERA_META,
    _BLOCK_HEAD,
    JERA_ADVICE,
    KNOWLEDGE_ADVICE,
    PRIOR_FINDINGS_HEADING,
    PRIOR_FINDINGS_ADVICE,
    PLAN_MAP_HEADING,
    PLAN_MAP_ADVICE,
    COMMANDS_HEADING,
    COMMANDS_ADVICE,
    _jera_answers,
    _EMBED,
    _POINTING,
    UNIT_FILES_ADVICE,
    _rerun_block,
    build_prompt,
    compose_prompt,
    PROGRESS_FILE,
)
from coscc.runner_review import (
    _ROUND_RE,
    _round_number,
    merge_review,
    _rounds,
    _header_status,
    _FINDING_RE,
    _FIXED_RE,
    open_findings,
    INCOMPLETE_SECTIONS,
    _ROUND_META_RE,
    _round_meta,
    _headings_in_order,
    closing_prompt,
    closing_round_problem,
)
from coscc.runner_reply import (
    STATUS_RE,
    RunError,
    _Stopped,
    HEADER_STATUS_RE,
    REPLY_KEPT,
    ATTEMPT_EXCERPT,
    _with_reply,
    _unfence,
    check_reply,
    _title,
    from_title,
    opening_problem,
    opening_reason,
    _after_tool,
    _unwrapped,
    _joined,
    CEILING_MARKERS,
    _hit_ceiling,
)
from coscc.runner_attempt import (
    Denials,
    CLAUDE_CODE_PRESET,
    permission_gate,
    snapshot,
    _fmt_num,
    describe_attempt,
    _head_of,
    _tree_state,
    describe_tree_change,
    _write_artifact,
)

# How many agent sessions one step starts. `Runner.run` makes exactly one `stream` call,
# so this is a description of the code below, not a setting: Settings shows it per stage
# (`0004_no-setting-says-which-model-runs-a-stage` `intent.md ## Answers, câu 1`). A stage
# that ran several agents would be a different design, and this number would change with it.
SESSIONS_PER_STEP = 1


# `0085` R2. How long the closing turn may take. Chosen, not measured: `spike.md ## U1`
# measured 9.8 s, and the longest closing turn `## U2` measured took 25.5 s.
CLOSING_TIMEOUT = 180.0


async def _closing_turn(
    sessions: Sessions,
    cwd: str,
    prompt: str,
    session_id: str,
    denials: Denials,
    **kw: Any,
) -> tuple[str, dict[str, Any] | None]:
    """`0085` R2. The reply of one more turn on a review's own session, and its `done`.

    In the shape `spike.md ## U1` measured: the same session id, a new handle, no tools, a
    callback that refuses every call, one turn. A new handle has no recorder, so nothing of
    this turn reaches the step's live view (spec C5).
    """

    async def deny_all(tool: str, tool_input: dict, context: Any):
        reason = "the closing turn holds no tools"
        denials.record(tool, reason, tool_input)
        return sdk.PermissionResultDeny(message=reason)

    text, done = "", None
    async for kind, payload in sessions.stream(
        cwd, prompt, session_id, max_turns=1, can_use_tool=deny_all, tools=[],
        step=sessions_mod.StepHandle(), **kw,
    ):
        if kind == "chunk":
            text += payload
        elif kind == "tool":
            text = _after_tool(text)
        elif kind == "done":
            done = payload
    return text, done


async def _from_progress(
    cwd: str,
    watch: str,
    before: tuple[str, str] | None,
    running: steps.Running | None,
    tree_changed: bool,
    directory: Path,
    artifact: str,
) -> tuple[str, str]:
    """`0080` R3, R5. Write a spike's artifact from its progress file, when nothing forbids it.

    Returns one of `0080` R6's values -- `withheld`, `unchecked`, `none`, `unusable` or
    `progress` -- and a sentence for `detail` (`""` for none). Called only for a spike whose
    reply was not written; `outcome` is not this function's to change.
    """
    # R5: a Stop, or a worktree the spike changed, writes nothing from any source.
    if (running is not None and running.stop_requested) or tree_changed:
        return "withheld", ""
    # The same check the reply's road makes, made again: the session is over, but the
    # first reading may have failed, and a reply that failed before it was reached never
    # compared the two. A worktree the app could not read is not proven unchanged, so
    # nothing is written; but no person and no change to the tree stopped it, so it is
    # `unchecked` rather than `withheld`, and `verify_0080 --measure` counts it a failure
    # (`0080` review round 1, F3).
    if before is None:
        return "unchecked", "spike.md not written from the progress file: the worktree's state was not read before the step"
    try:
        changed = describe_tree_change(before, await _tree_state(watch))
    except RunError as e:
        return "unchecked", f"spike.md not written from the progress file: {e}"
    if changed:
        return "withheld", f"spike.md not written from the progress file: the worktree changed during spike: {changed}"
    # `0034`. From here a Stop is refused, exactly as on the reply's road.
    if not steps.seal(running):
        return "withheld", ""
    progress = Path(cwd) / PROGRESS_FILE
    try:
        text = progress.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "none", ""
    try:
        # No `await` from the seal to the write: the rule the reply's road keeps.
        _write_artifact(directory, artifact, text)
    except RunError as e:
        return "unusable", f"the progress file was not an artifact: {e}"
    return "progress", "spike.md written from the progress file"


class Runner:
    """Runs one step. Owns no state of its own beyond what it was handed."""

    def __init__(self, sessions: Sessions, journal: Journal | None, app: dict | None = None):
        self.sessions = sessions
        self.journal = journal
        # `0094` R13: `{"version", "commit"}` of the app running this step, from
        # `update.identity`; `None` (a caller that has none) leaves `start` without them.
        self.app = app

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
        model: str | None = None,
        model_source: str = "",
        base: dict[str, Any] | None = None,
        base_note: str = "",
        last_attempt: str = "",
        integration_note: str = "",
        screens_note: str = "",
        plan_drift: dict[str, Any] | None = None,
        drift_note: str = "",
        shortlist: dict[str, Any] | None = None,
        effort: str | None = None,
        effort_source: str = "",
        label_declared: str | None = None,
        label: str | None = None,
        label_source: str | None = None,
        impl_run: int | None = None,
        end_fields: Any = None,
        watch: str | None = None,
        pr_note: str = "",
        pr_before: str | None = None,
        running: steps.Running | None = None,
        started_by: str = "person",
        knowledge: str = "",
        knowledge_record: dict[str, Any] | None = None,
        rerun: bool = False,
        rerun_note: str = "",
        prior_findings: str = "",
        prior_findings_record: dict[str, Any] | None = None,
        plan_map: str = "",
        plan_map_record: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield `("chunk", text)` while the reply arrives, then one `("done", {...})`.

        The same shape `Sessions.stream` uses, so the page and a proof command consume one
        stream rather than two.

        `cwd` is where the step works — since `0017` the unit's own worktree. It is the
        session's directory, the write boundary and the repository the prompt names.
        `workspace` stays the membership question and the journal's subject. Unset, the
        two are the same directory, as they were before.

        `model` is the one `coscc/models.py` resolved for this stage, and `model_source`
        says where it came from. Both go into the `start` record, which is where a reader
        checks what a step ran on. `None` leaves the session on `COS_MODEL`.

        `0033`: `effort` and the three label fields go into `start` beside it, and so does
        `impl_run` when the caller counted one. `end_fields`, an async callable returning a
        dict, is awaited only for a `done` step and its fields added to `end` — `review`'s
        finding counts. If it raises, the fields are left out and nothing else changes.

        `base` is what `coscc/worktrees.py` said about `cwd`'s freshness against
        `origin/main` before this call was made — `service.py` reads it, this module
        neither reads git itself nor decides what it means, only carries it into the
        `start` record. `base_note` is `service.describe_base(base)`, already worked out,
        so `build_prompt` does not import `service` to ask the same question twice.

        `plan_drift` is what `service.py` worked out with `coscc/drift.py` for an `impl`
        step (`0042`); this module only carries it into the `start` record, and
        `drift_note` into the prompt. `None` leaves the record without the field.

        `shortlist` (`0074` R14) is where the unit stood in the shortlist in effect, as
        `service.run_step` read it with `backlog.stamp`; carried into `start` and nowhere else.
        `None` leaves the record without the field.

        `watch` (`0039`) is the unit's worktree when `cwd` is a spike's throwaway directory.
        Writing is then held to `cwd` alone; the worktree and the unit are read only. Its
        `HEAD` and `git status --porcelain` are read before the session and again after it,
        and a difference fails the step before any artifact is written (R13). Nothing is
        restored — the difference is reported, in `detail`, and left for a person.

        `pr_note` and `pr_before` are `0041` R2's: the pull request `service.run_step`
        looked up before a `pr` step, as a prompt block and as its URL (`""` for none).

        `running` is the step's row in `Service.steps` (`0034`). With it the session's
        client is closed when the step ends, and a person's Stop ends the step as
        `stopped`: decided by `running.stop_requested`, never by the kind of exception the
        stop happened to cause. A stop is honoured only before `steps.seal`, which is
        called before anything of the artifact is written or read, so a stopped step
        leaves no artifact behind it and a sealed one cannot be stopped halfway.
        A cancellation nobody asked for is the app shutting down, and writes no `end`.

        `started_by` (`0043` R3) is `person` or `autopilot`, written into `start` and nowhere
        else; anything else is a `ValueError` before anything is read.

        `knowledge` and `knowledge_record` are `0090` R3/R4's: the slice of the store
        `service.run_step` read, for the prompt, and its `{version, entries, bytes}`, for
        `start`. `None` leaves the record without the field, which is what the flag off is.

        `rerun` and `rerun_note` are `0054` R7's: a stage a person ran again from the board,
        and their note, into the prompt and into `start`. False leaves both as they were.

        `prior_findings` and `prior_findings_record` are `0110` R6/R7's: what
        `priorfindings.for_step` built for an `impl` step, for the prompt, and its
        `{bytes, lines, units, dropped}`, for `start`. `None` leaves the record without the
        field, which is every other stage.

        `plan_map` and `plan_map_record` are `0096` R9/R11's, the same way round: what
        `planmap.for_step` built, and its `{bytes, files, full, short, new, outside}`. An
        `impl` step's prompt also carries its grant's `commands` (R10).
        """
        check_started_by(started_by)
        grant = grant_for_step(stage, label)
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

        head = await _head_of(watch or cwd)
        prompt, included, pointed = compose_prompt(
            cwd, directory, unit, stage, stages, artifact,
            writes_own=not grant.app_writes_artifact,
            gate_said=gate_said,
            head=head,
            base_note=base_note,
            last_attempt=last_attempt,
            integration_note=integration_note,
            screens_note=screens_note,
            drift_note=drift_note,
            worktree=watch or "",
            pr_note=pr_note,
            ceilings=(grant.max_turns, grant.max_budget_usd) if stage == "spike" else None,
            knowledge=knowledge,
            rerun=rerun,
            rerun_note=rerun_note,
            prior_findings=prior_findings,
            plan_map=plan_map,
            commands=grant.commands if stage in ("impl", "implement") else (),
        )

        # `0041` R5 picks the `pr` steps that ran after the fix by this field being there,
        # the way `0037` picks by `system_prompt`. Only `pr` carries it.
        pr_extra = {"pr_before": pr_before or ""} if stage == "pr" else {}

        # `0037`: the same condition that decides whether a gate and a tool list are sent.
        preset = CLAUDE_CODE_PRESET if grant.opens_anything else None

        # `0073`. The step's recorder, when `Service.run_step` gave it one: its `run` goes into
        # `start` and `end`, and it is closed -- everything on disk -- before `end` is written.
        recorder = getattr(running.handle, "recorder", None) if running is not None else None

        if self.journal is not None:
            self.journal.started(
                journal_key, unit, stage, mode,
                started_by=started_by,
                prompt_chars=len(prompt), included=included,
                # `0094` R16: the artifacts named by path only, `[]` for a stage that names none.
                pointed=pointed,
                # `0094` R13: which build ran the step, so `verify_0094 --measure` splits
                # before and after by what ran rather than by a date. A record written
                # before `0094` has neither field.
                **(
                    {"app_version": self.app.get("version", ""), "app_commit": self.app.get("commit", "")}
                    if self.app is not None
                    else {}
                ),
                granted=list(grant.tools), max_turns=grant.max_turns,
                head=head,
                model=model, model_source=model_source,
                effort=effort, effort_source=effort_source,
                label_declared=label_declared, label=label, label_source=label_source,
                **({"impl_run": impl_run} if impl_run is not None else {}),
                agents=SESSIONS_PER_STEP,
                base=base,
                # Which system prompt the step ran on, so a measurement can pick the steps
                # that ran after the fix by what they ran on rather than by a date — the
                # board runs the installed copy, not this checkout. `""` means no preset,
                # and nothing more: since `0088` a step without one whose `cwd` holds
                # project instructions runs on those as its whole system prompt, and only
                # `instructions` below says whether it did. A record written before `0037`
                # has no field, read as `""`.
                system_prompt="claude_code" if preset else "",
                # `0088` R13. Which project files `_options` puts into the system prompt,
                # whole or as a line of contents. Read again there, so a file edited in
                # between is not seen here (spec C7). A record written before `0088` has
                # no field: that step ran on a build that loaded every settings source.
                instructions=instructions.read(cwd).record(),
                **({"plan_drift": plan_drift} if plan_drift is not None else {}),
                **({"shortlist": shortlist} if shortlist is not None else {}),
                # `0090` R4. Beside `model`, and only when the flag was on for this stage.
                **({"knowledge": knowledge_record} if knowledge_record is not None else {}),
                # `0110` R7. Every `impl` start from this build, `bytes: 0` when nothing
                # matched, so `verify_0110` tells "nothing to hand" from an older build.
                **(
                    {"prior_findings": prior_findings_record}
                    if prior_findings_record is not None
                    else {}
                ),
                # `0096` R11. The same: every `impl` start from this build, `bytes: 0` when
                # the plan names no file.
                **({"plan_map": plan_map_record} if plan_map_record is not None else {}),
                # `0054` R7. Only on a stage run again from the board, so every other `start`
                # is what it was.
                **({"rerun": True, "rerun_note": rerun_note} if rerun else {}),
                **pr_extra,
                # `0092` R5: whose step this is, so the next start can tell one this process
                # still runs from one the app went down under.
                **({"run": recorder.run, "pid": os.getpid()} if recorder is not None else {}),
            )

        denials = Denials()
        if recorder is not None:
            denials.listener = recorder.denied
        # `0099` R1. What the session said, one entry per stretch between two tool calls.
        pieces = [""]
        # `0099` R5: how many pieces of text, blank ones aside, the session said.
        blocks = 0
        terminal = ""
        session_id = ""
        cost: dict[str, Any] = {}
        # What the session says it was billed to. The `start` record says what was asked for.
        models_used: list[str] = []
        outcome = "failed"
        detail = ""
        # `0019` plan step 5 / `spec.md` R1 a. Set only in an `except` branch, so a step
        # that finished (even one that merely hit its ceiling) carries no error here.
        error: dict[str, str] | None = None
        # `0039` R11: a spike writes only its `cwd`; the worktree and the unit are read.
        gate_args = (None, (watch, str(directory))) if watch else (str(directory),)
        # `0034`. Set when the task is cancelled with no Stop behind it: the app is going
        # down, and no `end` is what says so.
        shutting_down = False
        # `0080` R6. Where a spike's `spike.md` came from, for its `end` row; only a step
        # with `watch` carries it. `None` until something decides it.
        spike_md: str | None = None
        # `0085` R6. What reached `review.md`, for a review's `end` row: `round`, `incomplete`
        # (the closing turn's), `none` or `withheld`. `closing` is set only when that turn ran.
        review_md: str | None = None
        closing: dict[str, Any] | None = None
        before: tuple[str, str] | None = None
        tree_changed = False

        def stopped() -> bool:
            return running is not None and running.stop_requested

        try:
            before = await _tree_state(watch) if watch else None
            async for kind, payload in self.sessions.stream(
                cwd,
                prompt,
                None,
                max_turns=grant.max_turns,
                # Only pass a gate when something was actually granted. The list is the
                # grant's always, `[]` when it is empty: `None` would fall back to
                # `COS_TOOLS`, and an `idea` on a machine that set it held tools with no
                # gate in front of them (`0088` R4).
                can_use_tool=(
                    permission_gate(grant, cwd, denials, *gate_args)
                    if grant.opens_anything
                    else None
                ),
                tools=list(grant.tools),
                max_budget_usd=grant.max_budget_usd or None,
                # Only named when it differs, so a stand-in `stream` written before `0017`
                # without a `workspace` parameter keeps working for a plain step.
                **({"workspace": workspace} if cwd != workspace else {}),
                # The same reasoning: a stand-in with no `model` parameter keeps working
                # for a step nobody resolved a model for.
                **({"model": model} if model is not None else {}),
                **({"effort": effort} if effort is not None else {}),
                # And again: a tool-less step passes nothing, so it gets the session it
                # always got, and a stand-in without the parameter keeps working for it.
                **({"system_prompt": dict(preset)} if preset else {}),
                # `0034`, the same reasoning once more: only a board step has a row.
                **({"step": running.handle} if running is not None else {}),
            ):
                if kind == "chunk":
                    pieces[-1] += payload
                    if payload.strip():
                        blocks += 1
                    yield ("chunk", payload)
                elif kind == "session":
                    # `0019` plan step 5. The one place this app learns a session id
                    # before the step is over. Not forwarded — see the `else` branch's own
                    # note on why only `chunk` may cross this boundary as itself.
                    session_id = str(payload)
                elif kind == "tool":
                    # Kept: what comes after a tool call is the next piece, and `_joined`
                    # puts it on a line of its own. `plan` got `Read`, `Glob` and `Grep` on 2026-09-23,
                    # and its narration before a tool call began to open every `plan.md`
                    # (`0016_no-human-in-the-loop`). Dropping all text before the last call
                    # fixed that, and lost the head of any artifact written in pieces
                    # (`0099`, measured on `0085`'s plan). `_write_artifact` now drops what
                    # comes before the artifact's last title line instead.
                    #
                    # Not forwarded. `coscc/api.py:227-231` treats every kind that is not
                    # `chunk` as the terminal `done` row, so a third kind reaching it would
                    # arrive at the client as a malformed `done`.
                    pieces.append("")
                else:
                    session_id = payload.get("session_id", "")
                    cost = payload.get("cost", {}) or {}
                    terminal = str(payload.get("terminal_reason") or "")
                    models_used = list(payload.get("models_used") or [])

            if watch:
                # `0039` R13. Before anything is written: a spike that touched the branch
                # it was meant only to read must leave no `spike.md` saying it measured.
                changed = describe_tree_change(before, await _tree_state(watch))
                if changed:
                    spike_md, tree_changed = "withheld", True
                    raise RunError(f"the worktree changed during spike: {changed}")

            # `0034`. Nothing of the artifact has been read or written yet. From here on a
            # Stop is refused; before here, one that already came ends the step unwritten.
            if not steps.seal(running):
                if watch:
                    spike_md = "withheld"
                if stage == "review":
                    review_md = "withheld"
                raise _Stopped()
            if grant.app_writes_artifact:
                # Synchronous, so nothing yields between reading the `## Answers` already
                # on disk and writing the artifact over it.
                #
                # Review round 1, F2. A session stopped at its ceiling was cut off, so a
                # title it wrote before its last tool call is a draft or a first piece, and
                # its header may well say `accepted`. Only what it said after that call is
                # taken, as before `0099`; nothing else leaves a review its closing turn.
                taken = pieces[-1:] if _hit_ceiling(terminal) else pieces
                _write_artifact(directory, artifact, _joined(taken, artifact), blocks=blocks)
                if watch:
                    # `0080` R4: the reply was written, so the progress file is never read.
                    spike_md = "reply"
                if stage == "review":
                    review_md = "round"
            else:
                # The session had the tools to write it. Believing it did, rather than
                # looking, is how a step reports success for a file that is not there.
                written = directory / artifact
                if not written.exists():
                    raise RunError(f"the step did not write {artifact}")
                if not STATUS_RE.search(written.read_text(encoding="utf-8", errors="replace")):
                    raise RunError(f"{artifact} carries no `Status:` line")
            outcome = "done"
        except asyncio.CancelledError:
            if not stopped():
                shutting_down = True
                raise
            # The cancel was `Service.stop_step`'s own. Taken back, so the `end` below is
            # written and the step's reader still gets its `done` row.
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            outcome = "stopped"
        except (RunError, Refused) as e:
            # `spec.md` R1 a: "the step did not write impl.md" is a real reason to stop,
            # so it goes into the attempt record's `error` exactly like any other one.
            error = {"type": type(e).__name__, "message": str(e)}
            detail = str(e)
            # What the session said, kept. Until `0014` a prose step that produced an
            # unusable reply threw it away: the money was spent, the artifact was not
            # written, and the only record was the reason. A reply with no `Status:` line
            # is often a good artifact with a preamble in front of it, and a person who
            # can see it can decide that in a second — measured 2026-09-22, when a `spec`
            # step failed this way inside a paid proof run and left nothing to look at.
            detail = _with_reply(detail, _joined(pieces))
            # A step stopped by its own ceiling did not fail in the ordinary sense — it was
            # bounded. `journal.OUTCOMES` keeps the two apart so a reader can tell a defect
            # from a limit working as intended (`spec.md` R11).
            if _hit_ceiling(terminal):
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal} — {detail}"
        except Exception as e:  # surfaced as data; the process keeps serving
            error = {"type": type(e).__name__, "message": str(e)}
            detail = _with_reply(f"{type(e).__name__}: {e}", _joined(pieces))
            if _hit_ceiling(terminal):
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal}"
        else:
            if _hit_ceiling(terminal):
                # It wrote something, but it ran out of room doing it. Saying `done` here
                # would hide that the work may be half finished.
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal}"
        finally:
            # `0080` R3. A spike whose reply was not written gets its progress file read
            # here, before `service.run_step` removes `cwd`. Here and not in the `except`
            # branches: an exception raised inside one -- a Stop's cancel landing on an
            # `await` -- is not caught by its siblings, and would leave with no `end`.
            # Wrapped the way `snapshot` is below; `outcome` is never changed (R6).
            progress_pending: BaseException | None = None
            if watch and not shutting_down and outcome in ("failed", "exhausted") and spike_md is None:
                try:
                    spike_md, said = await _from_progress(
                        cwd, watch, before, running, tree_changed, directory, artifact
                    )
                    if said:
                        detail = f"{detail}\n--- {said} ---" if detail else said
                except asyncio.CancelledError as e:
                    if stopped():
                        task = asyncio.current_task()
                        if task is not None:
                            task.uncancel()
                        spike_md = "withheld"
                    else:
                        # The app going down: no `end`, and the cancel goes on once the
                        # rest of this block has done what it does for one.
                        shutting_down = True
                        progress_pending = e
                except Exception as e:  # noqa: BLE001 - the `end` row never depends on it
                    spike_md = "unusable"
                    said = f"the progress file was not read: {type(e).__name__}: {e}"
                    detail = f"{detail}\n--- {said} ---" if detail else said
            # `0085` R2. A review that ran out of turns before its reply could be written gets
            # one closing turn on its own session, asking for an incomplete round. Here, for
            # the reason the progress file is read here, and wrapped the same way: `outcome`
            # stays `exhausted` (R6) and the `end` row never depends on this.
            #
            # Sealed first, as the reply's road is: from here a Stop is refused, so the turn
            # cannot be cut halfway; `CLOSING_TIMEOUT` is what bounds it instead. The budget
            # does not: the CLI compares the whole session's cost, after the turn has run
            # (`spike.md ## U2`, point 3; spec C1).
            closing_pending: BaseException | None = None
            if (
                stage == "review" and grant.app_writes_artifact and not shutting_down
                and outcome == "exhausted" and review_md is None
                and session_id and head and not stopped()
            ):
                said = ""
                try:
                    if not steps.seal(running):
                        review_md = "withheld"
                    else:
                        number = max((_round_number(r) for r in _rounds(_read(directory / artifact))), default=0) + 1
                        reply, done = await asyncio.wait_for(
                            _closing_turn(
                                self.sessions, cwd, closing_prompt(head, number), session_id, denials,
                                max_budget_usd=grant.max_budget_usd or None,
                                **({"workspace": workspace} if cwd != workspace else {}),
                                **({"model": model} if model is not None else {}),
                                **({"effort": effort} if effort is not None else {}),
                                **({"system_prompt": dict(preset)} if preset else {}),
                            ),
                            CLOSING_TIMEOUT,
                        )
                        after = str((done or {}).get("terminal_reason") or "")
                        if after:
                            # R7. A `ResultMessage` came back. Its cost is the whole session's
                            # (`spike.md ## U2`, point 2), so it replaces the main one rather
                            # than adding to it, and the turn's own share is the difference.
                            total = ((done or {}).get("cost") or {}).get("cost_usd")
                            before_usd = cost.get("cost_usd")
                            closing = {
                                "terminal": after,
                                "turns": ((done or {}).get("cost") or {}).get("turns"),
                                "cost_usd": (
                                    round(total - before_usd, 6)
                                    if total is not None and before_usd is not None
                                    else None
                                ),
                            }
                            if total is not None:
                                cost = {**cost, "cost_usd": total}
                        else:
                            closing = {"cost_unknown": True}
                        # R5, judged on the text, never on `after`. No `await` from here to
                        # the write, the rule the reply's road keeps.
                        problem = closing_round_problem(_read(directory / artifact), reply, head)
                        if problem:
                            review_md, said = "none", f"review.md: the closing turn's reply was not written: {problem}"
                        else:
                            try:
                                _write_artifact(directory, artifact, reply)
                                review_md, said = "incomplete", f"review.md: Round {number} incomplete, written by the closing turn"
                            except RunError as e:
                                review_md, said = "none", f"review.md: the closing turn's reply was not written: {e}"
                except asyncio.CancelledError as e:
                    if stopped():
                        task = asyncio.current_task()
                        if task is not None:
                            task.uncancel()
                        review_md = "withheld"
                    else:
                        shutting_down = True
                        closing_pending = e
                except Exception as e:  # noqa: BLE001 - the `end` row never depends on it
                    closing = {"cost_unknown": True, "error": f"{type(e).__name__}: {e}"}
                    review_md, said = "none", f"review.md: the closing turn failed: {type(e).__name__}: {e}"
                if said:
                    detail = f"{detail}\n--- {said} ---" if detail else said
            # `0034` review round 1, F2. The outcome is decided here, so the door closes
            # here: a Stop that arrives while the attempt record is captured below is
            # refused (`Finishing`) rather than told "stopped" and logged as something
            # else. `seal` is False only when a Stop already came, and that one is honoured.
            stop_came = not steps.seal(running)
            if not shutting_down and stop_came and outcome != "done":
                # `0034`. Whatever the stop raised on its way in -- a closed stream, a
                # cancel, `_Stopped` at the seal -- a person asked, and that is the outcome.
                outcome = "stopped"
                error = None
                detail = f"stopped by {running.stopped_by}"
                if not terminal:
                    # No `ResultMessage` came back, so nothing was billed that this app
                    # saw. Absent, not zero: `spec.md` R8.
                    cost = {}
            if watch and not shutting_down and spike_md is None:
                # `0080` R6. Nothing wrote it: a Stop withheld it, or there was no file.
                spike_md = "withheld" if outcome == "stopped" else "none"
            if stage == "review" and not shutting_down and review_md is None:
                # `0085` R6, the same way.
                review_md = "withheld" if outcome == "stopped" else "none"
            # `0073` R6. Not for an app going down: `_drive` writes what it can, and no `end`.
            # `0092` R1: closed before the attempt record rather than after it, so the turns
            # it counts go into both.
            run_fields: dict[str, Any] = {}
            stored: int | None = None
            stored_from = ""
            if recorder is not None and not shutting_down:
                try:
                    lost = await recorder.close(outcome, detail)
                except Exception:  # noqa: BLE001 - R5: how many is unknown, so all of them
                    lost = max(1, int(getattr(recorder, "seq", 0) or 0))
                run_fields = {"run": recorder.run, "events_lost": lost}
                if outcome != "done":
                    try:
                        stored, stored_from = await recorder.stored_turns()
                    except Exception:  # noqa: BLE001 - the `end` row never depends on it
                        stored = None
            # `0092` spec Design 2. A step that did not finish counts its turns from its
            # events, and keeps the CLI's own count, when one came, as `cli_turns`. With no
            # `ResultMessage` there is no `cost_usd` at all, never a zero (R2). `done` is
            # written as it always was (R10).
            cost_fields: dict[str, Any] = dict(cost)
            if recorder is not None and not shutting_down and outcome != "done":
                if isinstance(stored, int) and stored > 0:
                    if cost:
                        cost_fields["cli_turns"] = cost_fields.pop("turns", None)
                    cost_fields["turns"] = stored
                    if stored_from == "memory":
                        cost_fields["turns_from"] = "memory"
                if not cost and outcome != "stopped":
                    # A Stop's `end` says this below, beside `stopped_by`, as it has since `0034`.
                    cost_fields["cost_unknown"] = True
            # C6: an app going down writes neither record. No `end` is what an
            # interrupted step looks like, and the next start says nothing about it.
            record = self.journal is not None and not shutting_down
            # `0019` plan step 5 / `spec.md` R1-R3. A stopped step's tree and transcript
            # are captured *before* `end` is written, and only for a run that is not
            # `done` — a step that wrote its artifact needs no attempt record, and R2
            # forbids this from touching anything a `done` run left behind.
            pending: BaseException | None = None
            if record and outcome != "done":
                try:
                    fields, pending = await snapshot(cwd, session_id)
                    self.journal.attempted(
                        journal_key, unit, stage,
                        outcome=outcome,
                        terminal=terminal or None,
                        error=error,
                        turns=cost_fields.get("turns"),
                        cost_usd=cost.get("cost_usd"),
                        session_id=session_id or None,
                        **fields,
                    )
                except Exception:
                    # R3: a failure here must not change the outcome or the `end` record
                    # that follows. The attempt record is best-effort; the run log's own
                    # `end` row is the one thing this unit will not put at risk.
                    pass
            extra: dict[str, Any] = {}
            if record and outcome == "done" and end_fields is not None:
                try:
                    extra = dict(await end_fields())
                except Exception:
                    # The same rule as the attempt record: the `end` row never depends on it.
                    extra = {}
            if record:
                self.journal.finished(
                    journal_key, unit, stage, outcome,
                    session_id=session_id,
                    artifact=artifact if outcome == "done" else None,
                    detail=detail or None,
                    denials=denials.count,
                    denied=denials.reasons or None,
                    models_used=models_used or None,
                    terminal=terminal or None,
                    **(
                        {"stopped_by": running.stopped_by, **({} if cost else {"cost_unknown": True})}
                        if outcome == "stopped"
                        else {}
                    ),
                    **cost_fields,
                    **extra,
                    **run_fields,
                    # `0080` R6: only a spike's `end` carries it.
                    **({"spike_md": spike_md} if watch else {}),
                    # `0085` R6: only a review's; `closing` only when that turn ran.
                    **({"review_md": review_md} if stage == "review" else {}),
                    **({"closing": closing} if closing is not None else {}),
                )
            if progress_pending is not None:
                raise progress_pending
            if closing_pending is not None:
                raise closing_pending
            if pending is not None:
                if not stop_came:
                    raise pending
                # A Stop's cancel that landed in the capture rather than the session.
                task = asyncio.current_task()
                if task is not None:
                    task.uncancel()

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
                "model": model,
                "model_source": model_source,
                **({"stopped_by": running.stopped_by} if outcome == "stopped" else {}),
            },
        )
