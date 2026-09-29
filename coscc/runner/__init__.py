"""Running one step of one unit, and recording what it cost.

A step reads the step before it: the prompt is `intent.md` plus the previous stage's
artifact verbatim, and the names of what went in are recorded. The six prose stages get no
tools, so the session cannot write its own artifact: **the app holds the pen for `.cos/`,
and the session only returns text.**

A stage's rules come from **this app's own** skills, never the workspace's (a workspace is
a repository somebody cloned). `coscc/agent/harness.py` answers where they are, and a step
whose rules it cannot find does not run.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import claude_agent_sdk as sdk

from coscc.agent import agents, instructions, steps, transcript
from coscc.github.integrate import check_started_by
from coscc.agent import sessions as sessions_mod
from coscc.runlog.journal import Journal
from coscc.agent import submit as submit_mod
from coscc.agent.policy import AGENT_TOOL, SUBAGENTS, Grant, beyond_reading, grant_for_step, is_prose_stage
from coscc.units import guards
from coscc.units import states as unit_states
from coscc.agent.sessions import Refused, Sessions, Suspended
from coscc.knowledge import TRIAL_FIELD as KNOWLEDGE_TRIAL_FIELD
from coscc.knowledge import modeltrial

# # These live in modules of their own and are imported back so `coscc.runner.<name>` still
# # resolves; a patch reaches only the module that looks it up.
from coscc.runner.prompt import (
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
    submit_prompt,
    PROGRESS_FILE,
)
from coscc.runner.review import (
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
    render_round,
    replace_new_rounds,
    UI_STANDARD,
)
from coscc.runner.reply import (
    RunError,
    OpeningError,
    opening_prompt,
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
from coscc.runner.attempt import (
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

# # How many agent sessions one step starts. `Runner.run` makes exactly one `stream` call, so
# # this describes the code below rather than being a setting; Settings shows it per stage.
SESSIONS_PER_STEP = 1


# # How long the closing turn may take. Chosen, not measured: one took 9.8 s, the longest 25.5 s.
CLOSING_TIMEOUT = 180.0

# # How long the repair turn may take. Rewriting a 26,535-character plan took about 94 s and
# # a spec 79 s; the text comes at once at the end of the turn, so a turn cut here leaves
# # nothing to write.
OPENING_TIMEOUT = 180.0


async def _closing_turn(
    sessions: Sessions,
    cwd: str,
    prompt: str,
    session_id: str,
    denials: Denials,
    **kw: Any,
) -> tuple[str, dict[str, Any] | None]:
    """The reply of one more turn on a review's own session, and its `done`.

    Same session id, a new handle, no tools, a callback that refuses every call, one turn. A
    new handle has no recorder, so nothing of this turn reaches the step's live view.
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


async def _opening_turn(
    sessions: Sessions,
    cwd: str,
    prompt: str,
    session_id: str,
    denials: Denials,
    **kw: Any,
) -> tuple[str, int, dict[str, Any] | None]:
    """The reply of one more turn on a prose step's own session, how many pieces of text it said,
    and its `done`.

    `_closing_turn`'s shape: same session id, a new handle with no recorder, no tools, a
    callback that refuses every call, one turn.
    """

    async def deny_all(tool: str, tool_input: dict, context: Any):
        reason = "the opening turn holds no tools"
        denials.record(tool, reason, tool_input)
        return sdk.PermissionResultDeny(message=reason)

    text, blocks, done = "", 0, None
    async for kind, payload in sessions.stream(
        cwd, prompt, session_id, max_turns=1, can_use_tool=deny_all, tools=[],
        step=sessions_mod.StepHandle(), **kw,
    ):
        if kind == "chunk":
            text += payload
            if payload.strip():
                blocks += 1
        elif kind == "tool":
            text = _after_tool(text)
        elif kind == "done":
            done = payload
    return text, blocks, done


async def _submit_turn(
    sessions: Sessions,
    cwd: str,
    prompt: str,
    session_id: str,
    denials: Denials,
    channel: submit_mod.Channel,
    **kw: Any,
) -> dict[str, Any] | None:
    """One more turn on a step's own session, holding `submit` and nothing else, and its `done`.
    What it says is not kept: the artifact was written before it.
    """
    gate = permission_gate(Grant(submits=True), cwd, denials)
    done = None
    async for kind, payload in sessions.stream(
        cwd, prompt, session_id, max_turns=SUBMIT_TURNS, can_use_tool=gate, tools=[],
        step=sessions_mod.StepHandle(), mcp_servers={submit_mod.SERVER: channel.server()}, **kw,
    ):
        if kind == "done":
            done = payload
    return done


# # The turns `_submit_turn` gets: a call, a refusal, a call again and the reply. Chosen, not
# # measured.
SUBMIT_TURNS = 4


def _manifest_head(cwd: str) -> str | None:
    """The head `.screens/manifest.json` in the step's tree says its screenshots were taken at:
    the app's read, so no model copies it. `None` when there is none.
    """
    try:
        head = json.loads((Path(cwd) / ".screens" / "manifest.json").read_text(encoding="utf-8")).get("head")
    except (OSError, ValueError, AttributeError):
        return None
    return str(head) if head else None


def _titled(pieces: list[str], artifact: str) -> bool:
    title = _title(artifact)
    return any(line.startswith(title) for piece in pieces for line in piece.splitlines())


def _unsubmitted(channel: submit_mod.Channel) -> str:
    """`""` once the run holds an object its channel's guard still opens on, with the app's hash
    taken now; else why not, the object dropped when it went stale. The run itself ends `done`
    only as the lane's `run-submitted` guard says.
    """
    got = channel.received
    if got is not None:
        verdict = channel.verdict(got["object"], got["revision"])
        if not verdict.open:
            channel.received = None
            return (
                f"guard {channel.guard_id} refused the object it had accepted ({', '.join(verdict.reasons)}): "
                "the unit's files changed after it was submitted"
            )
    lane = unit_states.default_lanes().lane("full")
    ran = guards.guard(lane.guard_for("run", "submitted")).check({"submitted": channel.received is not None})
    return "" if ran.open else f"{', '.join(ran.reasons)}: no object reached submit"


async def _nothing() -> AsyncIterator[tuple[str, Any]]:
    """The main reply of an `opening` or `closing` turn taken up again: already said."""
    return
    yield


def _turn_cost(done: dict[str, Any] | None, cost: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The `closing` field of one more turn on a step's session, and the step's cost once that
    turn is counted in. The repair turn is costed the same way.

    A `ResultMessage` came back when `terminal_reason` is set. Its cost is the whole session's,
    so it replaces the main one rather than adding to it; the turn's own share is the difference.
    """
    after = str((done or {}).get("terminal_reason") or "")
    if not after:
        return {"cost_unknown": True}, cost
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
    return closing, ({**cost, "cost_usd": total} if total is not None else cost)


async def _from_progress(
    cwd: str,
    watch: str,
    before: tuple[str, str] | None,
    running: steps.Running | None,
    tree_changed: bool,
    directory: Path,
    artifact: str,
) -> tuple[str, str]:
    """Write a spike's artifact from its progress file, when nothing forbids it.

    Returns one of `withheld`, `unchecked`, `none`, `unusable` or `progress` and a sentence for
    `detail` (`""` for none). Called only for a spike whose reply was not written; `outcome` is
    not this function's to change.
    """
    # A Stop, or a worktree the spike changed, writes nothing from any source.
    if (running is not None and running.stop_requested) or tree_changed:
        return "withheld", ""
    # The same check the reply's road makes, made again: the first reading may have failed. A
    # worktree the app could not read is not proven unchanged, so nothing is written; but nothing
    # stopped it, so it is `unchecked` rather than `withheld`.
    if before is None:
        return "unchecked", "spike.md not written from the progress file: the worktree's state was not read before the step"
    try:
        changed = describe_tree_change(before, await _tree_state(watch))
    except RunError as e:
        return "unchecked", f"spike.md not written from the progress file: {e}"
    if changed:
        return "withheld", f"spike.md not written from the progress file: the worktree changed during spike: {changed}"
    # From here a Stop is refused, as on the reply's road.
    if not steps.seal(running):
        return "withheld", ""
    progress = Path(cwd) / PROGRESS_FILE
    try:
        text = progress.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "none", ""
    try:
        # No `await` from the seal to the write, as on the reply's road.
        _write_artifact(directory, artifact, text)
    except RunError as e:
        return "unusable", f"the progress file was not an artifact: {e}"
    return "progress", "spike.md written from the progress file"


class Runner:
    """Runs one step. Owns no state beyond what it was handed."""

    def __init__(self, sessions: Sessions, journal: Journal | None, app: dict | None = None):
        self.sessions = sessions
        self.journal = journal
        # `{"version", "commit"}` of the app running this step, from `update.identity`; `None` leaves
        # `start` without them.
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
        gate_reasons: tuple[str, ...] = (),
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
        trial_record: dict[str, Any] | None = None,
        knowledge_trial: dict[str, Any] | None = None,
        rerun: bool = False,
        rerun_note: str = "",
        prior_findings: str = "",
        prior_findings_record: dict[str, Any] | None = None,
        plan_map: str = "",
        plan_map_record: dict[str, Any] | None = None,
        unfinished_round: dict[str, Any] | None = None,
        open_findings: tuple[str, ...] = (),
        claims_round: int | None = None,
        rounds_known: tuple[int, ...] = (),
        idea_note: str = "",
        siblings_note: str = "",
        read_also: tuple[str, ...] = (),
        agent: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        state_file: str | None = None,
        resume: dict[str, Any] | None = None,
        owner_extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield `("chunk", text)` while the reply arrives, then one `("done", {...})`.

        The same shape `Sessions.stream` uses, so the page and a proof command consume one stream.
        Optional arguments left `None`/empty leave the prompt, argv and records as they were.

        `cwd` is where the step works (the unit's worktree): the session's directory, the write
        boundary and the repository the prompt names. `workspace` stays the membership question
        and the journal's subject; unset, the two are the same.

        `model`, `model_source`, `effort`, the label fields, `impl_run`, `base`, `plan_drift`,
        `shortlist`, `started_by` (`person` or `autopilot`, else `ValueError`), `knowledge*`,
        `trial_record`, `knowledge_trial`, `prior_findings*`, `plan_map*`, `rerun*`, `agent`
        (the stage's resolved agent-table row; a preset session gets its commit attribution as
        `settings`) and `meta` (the unit's snapshot entry) are carried into the prompt or the
        `start` record and nowhere else; this module reads no git and decides no meaning.
        `base_note` is `service.describe_base(base)`, already worked out, so `build_prompt` does not
        import `service`. `end_fields`, an async callable, is awaited only for a `done` step and its
        fields added to `end`; if it raises they are left out. `model_trial.model` is filled in once
        the session's `init` names it. `ci_red` may come alone.

        `watch` is the unit's worktree when `cwd` is a spike's throwaway directory: writing is held
        to `cwd`, the worktree and the unit are read only. Its `HEAD` and `git status --porcelain`
        are read before and after the session and a difference fails the step before any artifact
        is written; nothing is restored, the difference goes in `detail`.

        `pr_note` and `pr_before` are the pull request looked up before a `pr` step, as a prompt
        block and as its URL. `idea_note`, `siblings_note` and `read_also` are the shared idea, the
        sibling checkouts `impl` may read, and the paths the read boundary lets through (never
        writes, never `git -C`). `unfinished_round` is `{n, dropped}` for a `review` prompt only.

        `running` is the step's row in `Service.steps`. With it the client is closed when the step
        ends, and a person's Stop ends it as `stopped`, decided by `running.stop_requested`, never
        by the kind of exception. A stop is honoured only before `steps.seal`, which is called
        before anything of the artifact is written or read, so a stopped step leaves no artifact
        and a sealed one cannot be stopped halfway. A cancellation nobody asked for is the app
        shutting down and writes no `end`.

        `resume` is a `suspend` row, with the `message` to send and the `pieces` the transcript held
        before its safe point. The step goes on in the same session under what is left of the
        grant's two ceilings and writes no `start`; one whose ceiling is used up opens no session and
        ends `exhausted`. A row whose `owner.kind` is `opening` or `closing` goes through the main
        reply again from those pieces and takes up that one turn. `owner_extra` is what `Service`
        adds to the owner a `suspend` row carries.
        """
        check_started_by(started_by)
        grant = grant_for_step(stage, label)
        directory = Path(directory)
        cwd = cwd or workspace
        if not directory.exists():
            raise RunError(f"no such work unit for {workspace}: {unit}")
        was = dict((resume or {}).get("owner") or {})
        turn_kind = str(was.get("kind") or "step") if resume is not None else ""

        if is_prose_stage(stage):
            # Belt and braces against a future edit to the table: a prose stage that acquired the ability
            # to write or run a command would stop being covered. Asks `beyond_reading`, not
            # `opens_anything`, because `plan` holds `Read`, `Glob` and `Grep`; the guard's purpose is that
            # the app writes a prose stage's artifact, so the stage must not be able to.
            beyond = beyond_reading(grant)
            if beyond:
                raise RunError(
                    f"{stage} is a prose stage and must not carry {', '.join(beyond)}"
                )

        if resume is not None:
            # Nothing of git is read or run on a step taken up again; what the first start read is in its
            # owner.
            head = str(was.get("head") or "")
            prompt, included, pointed = str(resume.get("message") or ""), [], []
        else:
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
                unfinished_round=unfinished_round,
                idea_note=idea_note,
                siblings_note=siblings_note,
                runs_commands="Bash" in grant.tools,
                agent=agent,
                unit_meta=meta,
                state_file=state_file,
            )

        # Only `pr` carries this field.
        pr_extra = {"pr_before": pr_before or ""} if stage == "pr" else {}
        # The autopilot tells a recording `ship` that ran out from a merging one by this field. Only
        # a `ship` whose gate named the merge already made carries it, by the code `recording-ship`.
        ship_extra = {"ship_mode": "record"} if stage == "ship" and "recording-ship" in gate_reasons else {}

        # The same condition that decides whether a gate and a tool list are sent.
        preset = CLAUDE_CODE_PRESET if grant.opens_anything else None
        # Only beside a preset: a tool-less session's argv is unchanged.
        settings = agents.settings_json(agent) if agent is not None and preset else None

        # The step's recorder, when `Service.run_step` gave it one: its `run` goes into `start` and
        # `end`, and it is closed, everything on disk, before `end` is written.
        recorder = getattr(running.handle, "recorder", None) if running is not None else None

        # This run's `submit`, bound to it: a stage that hands back a stage result ends `done` only
        # once the channel holds an object its guard still opens on.
        channel = (
            submit_mod.Channel(
                run=str(recorder.run) if recorder is not None else uuid.uuid4().hex,
                stage=stage, directory=directory, artifact=artifact,
                own=not grant.app_writes_artifact,
                head=head, open_findings=tuple(open_findings), claims_round=claims_round,
            )
            if grant.submits
            else None
        )
        servers = {"mcp_servers": {submit_mod.SERVER: channel.server()}} if channel is not None else {}
        # The index of the piece that began after the last `submit` call, `None` before one.
        after_submit: int | None = None
        # What the repair turn cost, when it ran.
        submit_turn: dict[str, Any] | None = None
        # The rounds `review.md` held before this step's reply was written.
        rounds_before: set[int] | None = None

        start_at = was.get("start_at")
        if self.journal is not None and resume is None:
            start_at = self.journal.started(
                journal_key, unit, stage, mode,
                started_by=started_by,
                prompt_chars=len(prompt), included=included,
                # The artifacts named by path only, `[]` for a stage that names none.
                pointed=pointed,
                # Which build ran the step, so a measurement splits by what ran rather than by a date.
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
                # Which system prompt the step ran on. `""` means no preset, and nothing more: a step
                # without one whose `cwd` holds project instructions runs on those as its whole system
                # prompt, and only `instructions` below says whether it did.
                system_prompt="claude_code" if preset else "",
                # Which project files `_options` puts into the system prompt, whole or as a line of contents.
                # Read again there, so a file edited in between is not seen here.
                instructions=instructions.read(cwd).record(),
                **({"plan_drift": plan_drift} if plan_drift is not None else {}),
                **({"shortlist": shortlist} if shortlist is not None else {}),
                # Beside `model`, and only when the flag was on for this stage.
                **({"knowledge": knowledge_record} if knowledge_record is not None else {}),
                # Every stage, only with `COS_KNOWLEDGE` on.
                **({KNOWLEDGE_TRIAL_FIELD: knowledge_trial} if knowledge_trial is not None else {}),
                # Every routine `impl`'s `model_trial`, and `ci_red`.
                **(trial_record or {}),
                # Every `impl` start, `bytes: 0` when nothing matched, so "nothing to hand" is told from an
                # older build.
                **(
                    {"prior_findings": prior_findings_record}
                    if prior_findings_record is not None
                    else {}
                ),
                # The same: every `impl` start, `bytes: 0` when the plan names no file.
                **({"plan_map": plan_map_record} if plan_map_record is not None else {}),
                # Only on a stage run again from the board.
                **({"rerun": True, "rerun_note": rerun_note} if rerun else {}),
                **pr_extra,
                **ship_extra,
                # Top level, the name when the step began; none for a stage the agent table has no row for.
                **({"agent": agent["name"]} if agent is not None else {}),
                # Whose step this is, so the next start can tell one this process still runs from one the app
                # went down under.
                **({"run": recorder.run, "pid": os.getpid()} if recorder is not None else {}),
            ).get("at")

        # Whose session this is, for a `suspend` row, and all an update's next start needs to take it
        # up again without reading git.
        owner: dict[str, Any] = {
            "kind": "step", "workspace": journal_key, "unit": unit, "stage": stage,
            "start_at": start_at, "max_turns": grant.max_turns,
            "max_budget_usd": grant.max_budget_usd, "head": head, "label": label,
            "effort": effort, "artifact": artifact,
            "segments": list(was.get("segments") or []),
            **({"run": recorder.run} if recorder is not None else {}),
            **(owner_extra or {}),
        }
        # A routine `impl` in the model trial has its `start` told the model the session's `init`
        # named, once, when it comes, or `never-started` at the end.
        trial_at = start_at if (trial_record or {}).get(modeltrial.FIELD) and start_at else None

        def trial_model(said: str) -> None:
            nonlocal trial_at
            if trial_at is None or self.journal is None:
                return
            try:
                self.journal.set_trial_model(journal_key, unit, stage, trial_at, said)
            except Exception:  # noqa: BLE001 — a measurement, never a reason to fail the step
                pass
            trial_at = None

        # What is left of the two ceilings after the part of the session before the cut.
        turns_left, budget_left = grant.max_turns, grant.max_budget_usd or None
        used_up = ""
        if resume is not None:
            turns_left, budget_left, used_up = transcript.ceilings_left(
                grant.max_turns, grant.max_budget_usd, resume
            )
            # This segment, filled in when its first `done` comes.
            owner["segments"].append({
                "suspend_id": resume.get("suspend_id"),
                **({"cost_unknown": True} if resume.get("cost_unknown") else {}),
            })
        # The budget a closing or repair turn is given. The CLI compares it only after the turn has
        # run, so on one turn it bounds nothing; one already spent is not passed at all, since `0`
        # reaches `_options` as no ceiling. `turn_spent` is what that turn would then have ended with.
        turn_spent = budget_left is not None and budget_left <= 0
        turn_budget = None if turn_spent else budget_left
        # The `done` of the first call on the resumed session, which fills that segment in.
        segment_done: dict[str, Any] | None = None
        # Whether the `opening` or `closing` turn the update paused was reached again.
        turn_taken = False

        denials = Denials()
        if recorder is not None:
            denials.listener = recorder.denied
        # What the session said, one entry per stretch between two tool calls. A step taken up again
        # starts from what it had said before its safe point.
        pieces = [*((resume or {}).get("pieces") or []), ""] if resume is not None else [""]
        # How many pieces of text, blank ones aside, the session said.
        blocks = sum(1 for p in pieces if p.strip())
        terminal = ""
        session_id = str((resume or {}).get("session_id") or "")
        cost: dict[str, Any] = {}
        if turn_kind in ("opening", "closing"):
            # The main reply ended before the update; what it ended with is in the owner.
            terminal = str(was.get("main_terminal") or "")
            cost = dict(was.get("main_cost") or {})
        elif resume is not None and resume.get("spent_usd") is not None:
            cost = {"cost_usd": float(resume["spent_usd"])}
        # What the session says it was billed to. The `start` record says what was asked for.
        models_used: list[str] = []
        outcome = "failed"
        detail = ""
        # Set only in an `except` branch, so a step that finished (even one that merely hit its
        # ceiling) carries no error here.
        error: dict[str, str] | None = None
        # A spike writes only its `cwd`; the worktree and the unit are read. Only when a sibling was
        # named, so every other step's gate is unchanged.
        gate_args = (
            (None, (watch, str(directory))) if watch
            else (str(directory), tuple(read_also)) if read_also
            else (str(directory),)
        )
        # Set when the task is cancelled with no Stop behind it: the app is going down, and no `end`
        # is what says so.
        shutting_down = False
        # Where a spike's `spike.md` came from, for its `end` row; only a step with `watch` carries
        # it. `None` until something decides it.
        spike_md: str | None = None
        # What reached `review.md`, for a review's `end` row: `round`, `incomplete` (the closing
        # turn's), `none` or `withheld`. `closing` is set only when that turn ran.
        review_md: str | None = None
        closing: dict[str, Any] | None = None
        # The refusal of a reply that lacked its opening, which a repair turn may follow, and what
        # came of that turn: `repaired`, `none` or `withheld`.
        unopened: OpeningError | None = None
        opening: str | None = None
        before: tuple[str, str] | None = None
        tree_changed = False

        def stopped() -> bool:
            return running is not None and running.stop_requested

        try:
            if resume is not None:
                before = tuple(was["before"]) if watch and was.get("before") else None
            else:
                before = await _tree_state(watch) if watch else None
                if before is not None:
                    owner["before"] = list(before)
            if used_up and turn_kind not in ("opening", "closing"):
                # Nothing left to resume with, so no session is opened. Not for a closing or repair turn:
                # that is one turn of its own, bounded by its timeout.
                terminal = used_up
                raise RunError("the ceiling was used up before the update, so the step was not resumed")
            # An `opening` or `closing` turn taken up again has its main reply already.
            main = _nothing() if turn_kind in ("opening", "closing") else self.sessions.stream(
                cwd,
                prompt,
                session_id or None,
                max_turns=turns_left,
                # Only pass a gate when something was actually granted. The list is always the grant's, `[]`
                # when empty: `None` would fall back to `COS_TOOLS`, and an `idea` on a machine that set it
                # held tools with no gate in front of them.
                can_use_tool=(
                    permission_gate(grant, cwd, denials, *gate_args)
                    if grant.opens_anything or channel is not None
                    else None
                ),
                tools=list(grant.tools),
                max_budget_usd=budget_left,
                # Only named when it differs, so a stand-in `stream` without a `workspace` parameter keeps
                # working for a plain step.
                **({"workspace": workspace} if cwd != workspace else {}),
                # The same: a stand-in with no `model` parameter keeps working for a step nobody resolved a
                # model for.
                **({"model": model} if model is not None else {}),
                **({"effort": effort} if effort is not None else {}),
                # And again: a tool-less step passes nothing, so it gets the session it always got.
                **({"system_prompt": dict(preset)} if preset else {}),
                # Only a preset session with an agent row passes it.
                **({"settings": settings} if settings is not None else {}),
                # Only a board step has a row.
                **({"step": running.handle, "owner": owner} if running is not None else {}),
                # Only on a step taken up again.
                **({"resume_at": resume.get("safe_uuid")} if resume is not None else {}),
                # Only a step with a channel.
                **servers,
                # Only a grant holding the helpers' tool gets them: impl.
                **({"agents": SUBAGENTS} if AGENT_TOOL in grant.tools else {}),
            )
            async for kind, payload in main:
                if kind == "chunk":
                    pieces[-1] += payload
                    if payload.strip():
                        blocks += 1
                    yield ("chunk", payload)
                elif kind == "session":
                    # The one place this app learns a session id before the step is over. Not forwarded: only
                    # `chunk` may cross this boundary as itself.
                    session_id = str(payload)
                    if running is not None and running.handle.init_model:
                        trial_model(running.handle.init_model)
                elif kind == "tool":
                    # Kept: what comes after a tool call is the next piece, and `_joined` puts it on a line of
                    # its own. Dropping all text before the last call lost the head of any artifact written in
                    # pieces; `_write_artifact` drops what comes before the artifact's last title line instead.
                    #
                    # Not forwarded: `coscc/web/api.py` treats every kind that is not `chunk` as the terminal
                    # `done` row, so a third kind would arrive at the client as a malformed `done`.
                    pieces.append("")
                    if payload == submit_mod.NAME:
                        after_submit = len(pieces) - 1
                else:
                    session_id = payload.get("session_id", "")
                    cost = payload.get("cost", {}) or {}
                    terminal = str(payload.get("terminal_reason") or "")
                    models_used = list(payload.get("models_used") or [])
                    if resume is not None and segment_done is None:
                        segment_done = payload
            # What the main reply ended with, for an `opening` or `closing` turn an update pauses: its
            # next start goes through this reply again without a session.
            owner.update(main_terminal=terminal, main_cost=dict(cost))

            if watch:
                # Before anything is written: a spike that touched the branch it was meant only to read must
                # leave no `spike.md` saying it measured.
                changed = describe_tree_change(before, await _tree_state(watch))
                if changed:
                    spike_md, tree_changed = "withheld", True
                    raise RunError(f"the worktree changed during spike: {changed}")

            # Nothing of the artifact has been read or written yet. From here on a Stop is refused;
            # before here, one that already came ends the step unwritten.
            if not steps.seal(running):
                if watch:
                    spike_md = "withheld"
                if stage == "review":
                    review_md = "withheld"
                raise _Stopped()
            if grant.app_writes_artifact:
                # Synchronous, so nothing yields between reading the `## Answers` already on disk and writing
                # the artifact over it.
                #
                # A session stopped at its ceiling was cut off, so a title it wrote before its last tool call
                # is a draft or a first piece whose header may say `accepted`. Only what it said after that
                # call is taken.
                taken = pieces
                if channel is not None and after_submit is not None and not _titled(pieces[after_submit:], artifact):
                    # What came after the last `submit` call, with no title in it, is the session saying it
                    # called the tool: never part of the artifact.
                    taken = pieces[:after_submit]
                taken = taken[-1:] if _hit_ceiling(terminal) else taken
                if channel is not None and stage == submit_mod.ROUND:
                    rounds_before = {_round_number(r) for r in _rounds(_read(directory / artifact))}
                _write_artifact(directory, artifact, _joined(taken, artifact), blocks=blocks)
                if watch:
                    # The reply was written, so the progress file is never read.
                    spike_md = "reply"
                if stage == "review":
                    review_md = "round"
            else:
                # The session had the tools to write it. Believing it did, rather than looking, is how a step
                # reports success for a file that is not there.
                written = directory / artifact
                if not written.exists():
                    raise RunError(f"the step did not write {artifact}")
                # Its `Status:` line is not looked at: what decides is the object.
            outcome = "done"
        except asyncio.CancelledError:
            if not stopped():
                shutting_down = True
                raise
            # The cancel was `Service.stop_step`'s own. Taken back, so the `end` below is written and the
            # step's reader still gets its `done` row.
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            outcome = "stopped"
        except Suspended:
            # An update paused the session and wrote its `suspend` row: like an app going down, no
            # `attempt` and no `end`. The next start takes it up.
            shutting_down = True
            raise
        except (RunError, Refused) as e:
            # "The step did not write impl.md" is a real reason to stop, so it goes into the attempt
            # record's `error` like any other.
            error = {"type": type(e).__name__, "message": str(e)}
            detail = str(e)
            unopened = e if isinstance(e, OpeningError) else None
            # What the session said, kept: the money was spent, and a reply with no `Status:` line is
            # often a good artifact with a preamble that a person can judge in a second.
            detail = _with_reply(detail, _joined(pieces))
            # A step stopped by its own ceiling was bounded, not failed. `journal.OUTCOMES` keeps the two
            # apart so a reader can tell a defect from a limit working as intended.
            if _hit_ceiling(terminal):
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal} — {detail}"
        except Exception as e:
            error = {"type": type(e).__name__, "message": str(e)}
            detail = _with_reply(f"{type(e).__name__}: {e}", _joined(pieces))
            if _hit_ceiling(terminal):
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal}"
        else:
            if _hit_ceiling(terminal):
                # It wrote something, but it ran out of room doing it. Saying `done` here would hide that
                # the work may be half finished.
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal}"
        finally:
            # A spike whose reply was not written gets its progress file read here, before
            # `service.run_step` removes `cwd`. Here and not in the `except` branches: an exception raised
            # inside one (a Stop's cancel landing on an `await`) is not caught by its siblings and would
            # leave with no `end`. Wrapped like `snapshot` below; `outcome` is never changed.
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
                        # The app going down: no `end`, and the cancel goes on once the rest of this block has done
                        # what it does for one.
                        shutting_down = True
                        progress_pending = e
                except Exception as e:  # noqa: BLE001 - the `end` row never depends on it
                    spike_md = "unusable"
                    said = f"the progress file was not read: {type(e).__name__}: {e}"
                    detail = f"{detail}\n--- {said} ---" if detail else said
            # A review that ran out of turns before its reply could be written gets one closing turn on
            # its own session, asking for an incomplete round. Here and wrapped, for the reason the
            # progress file is read here: `outcome` stays `exhausted` and the `end` row never depends on it.
            #
            # Sealed first, as the reply's road is: from here a Stop is refused, so the turn cannot be
            # cut halfway; `CLOSING_TIMEOUT` bounds it instead. The budget does not: the CLI compares the
            # whole session's cost after the turn has run.
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
                        # The closing turn an update paused is taken up, not asked again.
                        take_up = turn_kind == "closing"
                        reply, done = await asyncio.wait_for(
                            _closing_turn(
                                self.sessions, cwd,
                                str(resume.get("message") or "") if take_up else closing_prompt(head, number),
                                session_id, denials,
                                max_budget_usd=turn_budget,
                                **({"workspace": workspace} if cwd != workspace else {}),
                                **({"model": model} if model is not None else {}),
                                **({"effort": effort} if effort is not None else {}),
                                **({"system_prompt": dict(preset)} if preset else {}),
                                **({"settings": settings} if settings is not None else {}),
                                **({"owner": {**owner, "kind": "closing"}} if running is not None else {}),
                                **({"resume_at": resume.get("safe_uuid")} if take_up else {}),
                            ),
                            CLOSING_TIMEOUT,
                        )
                        if take_up:
                            turn_taken, segment_done = True, segment_done or done
                        closing, cost = _turn_cost(done, cost)
                        # Judged on the text, never on `after`. No `await` from here to the write, as on the reply's
                        # road.
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
                except Suspended as e:
                    # Paused by an update, like the main reply: no `end`.
                    shutting_down = True
                    closing_pending = e
                except Exception as e:  # noqa: BLE001 - the `end` row never depends on it
                    closing = {"cost_unknown": True, "error": f"{type(e).__name__}: {e}"}
                    review_md, said = "none", f"review.md: the closing turn failed: {type(e).__name__}: {e}"
                if said:
                    detail = f"{detail}\n--- {said} ---" if detail else said
            # A prose step whose reply was refused for its opening alone gets one more turn on its own
            # session, with no tools, asking for the artifact again. Not a spike (progress file), and not
            # a step at its ceiling (closing turn). Here and wrapped, for the closing turn's reasons: the
            # `end` row never depends on it.
            #
            # Sealed first, so a Stop is refused until the turn is over; `OPENING_TIMEOUT` bounds it, and
            # the budget does not.
            opening_pending: BaseException | None = None
            if (
                unopened is not None and is_prose_stage(stage) and grant.app_writes_artifact
                and not watch and outcome == "failed" and not shutting_down
            ):
                said = ""
                try:
                    if not session_id:
                        opening, said = "none", f"{artifact}: the opening was not repaired: the session has no id"
                    elif not steps.seal(running):
                        opening = "withheld"
                    elif turn_spent:
                        # The turn would stop at the ceiling, and be refused.
                        turn_taken = turn_kind == "opening"
                        opening, said = "none", f"{artifact}: the repair turn's reply was not written: it stopped at the ceiling: error_max_budget_usd"
                    else:
                        take_up = turn_kind == "opening"
                        reply, again, done = await asyncio.wait_for(
                            _opening_turn(
                                self.sessions, cwd,
                                str(resume.get("message") or "") if take_up else opening_prompt(artifact, unopened.problem),
                                session_id, denials,
                                max_budget_usd=turn_budget,
                                **({"workspace": workspace} if cwd != workspace else {}),
                                **({"model": model} if model is not None else {}),
                                **({"effort": effort} if effort is not None else {}),
                                **({"system_prompt": dict(preset)} if preset else {}),
                                **({"settings": settings} if settings is not None else {}),
                                **({"owner": {**owner, "kind": "opening"}} if running is not None else {}),
                                **({"resume_at": resume.get("safe_uuid")} if take_up else {}),
                            ),
                            OPENING_TIMEOUT,
                        )
                        if take_up:
                            turn_taken, segment_done = True, segment_done or done
                        closing, cost = _turn_cost(done, cost)
                        after = str((done or {}).get("terminal_reason") or "")
                        # A turn that stopped at a ceiling writes nothing. At `max_turns` it was cut off: an MCP tool
                        # still reaches a session with `tools=[]`, and one call refused by `deny_all` ends the only
                        # turn, so what came before may be a draft whose header says `accepted`. At the budget the
                        # turn ran whole and the CLI compared the cost after, so its reply may be complete; it is
                        # refused all the same, because the reply's road never ends a step past its ceiling `done`.
                        if _hit_ceiling(after):
                            opening, said = "none", f"{artifact}: the repair turn's reply was not written: it stopped at the ceiling: {after}"
                        else:
                            # The road every reply takes, and nothing of the first reply joined to it. No `await` from
                            # here to the write.
                            try:
                                _write_artifact(directory, artifact, reply, blocks=again)
                            except RunError as e:
                                opening, said = "none", f"{artifact}: the repair turn's reply was not written: {e}"
                            else:
                                outcome, error, detail, opening = "done", None, "", "repaired"
                                if stage == "review":
                                    review_md = "round"
                except asyncio.CancelledError as e:
                    if stopped():
                        task = asyncio.current_task()
                        if task is not None:
                            task.uncancel()
                        opening = "withheld"
                    else:
                        shutting_down = True
                        opening_pending = e
                except Suspended as e:
                    shutting_down = True
                    opening_pending = e
                except Exception as e:  # noqa: BLE001 - the `end` row never depends on it
                    closing = {"cost_unknown": True, "error": f"{type(e).__name__}: {e}"}
                    opening, said = "none", f"{artifact}: the repair turn failed: {type(e).__name__}: {e}"
                if said:
                    # Under the first refusal and what the session first replied.
                    detail = f"{detail}\n--- {said} ---" if detail else said
            # A step that wrote its artifact with no object the guard still opens on gets one more turn
            # on its own session, holding `submit` and nothing else, as the opening's repair is one.
            # Still none, and it ends `failed`, whatever its file says. Sealed and wrapped as that turn
            # is: the `end` row never depends on it.
            submit_pending: BaseException | None = None
            if channel is not None and outcome == "done" and not shutting_down:
                why = _unsubmitted(channel)
                said = ""
                if why:
                    try:
                        if not session_id:
                            said = "the session has no id"
                        elif not steps.seal(running):
                            said = "a Stop came first"
                        elif turn_spent:
                            said = "the budget was spent"
                        else:
                            done = await asyncio.wait_for(
                                _submit_turn(
                                    self.sessions, cwd, submit_prompt(stage, artifact, why),
                                    session_id, denials, channel,
                                    max_budget_usd=turn_budget,
                                    **({"workspace": workspace} if cwd != workspace else {}),
                                    **({"model": model} if model is not None else {}),
                                    **({"effort": effort} if effort is not None else {}),
                                    **({"system_prompt": dict(preset)} if preset else {}),
                                    **({"settings": settings} if settings is not None else {}),
                                    **({"owner": {**owner, "kind": "submit"}} if running is not None else {}),
                                ),
                                OPENING_TIMEOUT,
                            )
                            submit_turn, cost = _turn_cost(done, cost)
                            if _hit_ceiling(str((done or {}).get("terminal_reason") or "")):
                                channel.received = None
                                said = "the repair turn stopped at its ceiling"
                            else:
                                why = _unsubmitted(channel)
                    except asyncio.CancelledError as e:
                        if stopped():
                            task = asyncio.current_task()
                            if task is not None:
                                task.uncancel()
                            said = "a Stop came during the repair turn"
                        else:
                            shutting_down = True
                            submit_pending = e
                    except Suspended as e:
                        shutting_down = True
                        submit_pending = e
                    except Exception as e:  # noqa: BLE001 - the `end` row never depends on it
                        submit_turn = {"cost_unknown": True, "error": f"{type(e).__name__}: {e}"}
                        said = f"the repair turn failed: {type(e).__name__}: {e}"
                    if why and not shutting_down:
                        outcome = "failed"
                        error = {"type": "NoSubmission", "message": why}
                        detail = f"no-submission: {why}" + (f" ({said})" if said else "")
            # The round the review handed back, written into `review.md` by the app: its number, the
            # head the app read, the verdict, the findings and the screenshots.
            if (
                channel is not None and stage == submit_mod.ROUND and outcome == "done"
                and channel.received is not None and not shutting_down
            ):
                try:
                    # Past every round the file held and every one the app has a row for, so a number is never
                    # given twice (`review_rounds_n`).
                    number = max({*(rounds_before or ()), *rounds_known}, default=0) + 1
                    screens = {
                        "taken": _manifest_head(cwd), "standard": UI_STANDARD,
                        "by": f"{(agent or {}).get('name') or 'the review session'} (agent, review)",
                    }
                    text = _read(directory / artifact)
                    new = [r for r in _rounds(text) if _round_number(r) not in (rounds_before or set())]
                    rendered = render_round(new[-1] if new else "## Round", number, head, channel.received["object"], screens)
                    status = submit_mod.ROUND_STATES[str(channel.received["object"]["verdict"])]
                    (directory / artifact).write_text(
                        replace_new_rounds(text, rounds_before or set(), rendered, status), encoding="utf-8")
                    channel.extra = {"n": number, "screens": screens}
                except Exception as e:  # noqa: BLE001 - the `end` row never depends on it
                    outcome = "failed"
                    error = {"type": type(e).__name__, "message": str(e)}
                    detail = f"review.md: the round was not written from its object: {type(e).__name__}: {e}"
            # The outcome is decided here, so the door closes here: a Stop that arrives while the attempt
            # record is captured below is refused (`Finishing`) rather than told "stopped" and logged as
            # something else. `seal` is False only when a Stop already came, and that one is honoured.
            stop_came = not steps.seal(running)
            if not shutting_down and stop_came and outcome != "done":
                # Whatever the stop raised on its way in (a closed stream, a cancel, `_Stopped` at the seal),
                # a person asked, and that is the outcome.
                outcome = "stopped"
                error = None
                detail = f"stopped by {running.stopped_by}"
                if not terminal:
                    # No `ResultMessage` came back, so nothing was billed that this app saw. Absent, not zero.
                    cost = {}
            if watch and not shutting_down and spike_md is None:
                # Nothing wrote it: a Stop withheld it, or there was no file.
                spike_md = "withheld" if outcome == "stopped" else "none"
            if stage == "review" and not shutting_down and review_md is None:
                # The same way.
                review_md = "withheld" if outcome == "stopped" else "none"
            # Not for an app going down: `_drive` writes what it can, and no `end`. Closed before the
            # attempt record rather than after it, so the turns it counts go into both.
            run_fields: dict[str, Any] = {}
            stored: int | None = None
            stored_from = ""
            if recorder is not None and not shutting_down:
                try:
                    lost = await recorder.close(outcome, detail)
                except Exception:  # noqa: BLE001 - how many is unknown, so all of them
                    lost = max(1, int(getattr(recorder, "seq", 0) or 0))
                run_fields = {"run": recorder.run, "events_lost": lost}
                if outcome != "done":
                    try:
                        stored, stored_from = await recorder.stored_turns()
                    except Exception:  # noqa: BLE001 - the `end` row never depends on it
                        stored = None
            # A step that did not finish counts its turns from its events, and keeps the CLI's own count,
            # when one came, as `cli_turns`. With no `ResultMessage` there is no `cost_usd` at all, never
            # a zero. `done` is written as it always was.
            cost_fields: dict[str, Any] = dict(cost)
            if recorder is not None and not shutting_down and outcome != "done":
                if isinstance(stored, int) and stored > 0:
                    if cost:
                        cost_fields["cli_turns"] = cost_fields.pop("turns", None)
                    cost_fields["turns"] = stored
                    if stored_from == "memory":
                        cost_fields["turns_from"] = "memory"
                if not cost and outcome != "stopped":
                    # A Stop's `end` says this below, beside `stopped_by`.
                    cost_fields["cost_unknown"] = True
            # Each segment an update's resume began, with the tokens of its first call and what it cost;
            # `cost_partial` when one before it left no cost.
            segment_fields: dict[str, Any] = {}
            if owner["segments"]:
                if resume is not None and segment_done is not None:
                    segment = owner["segments"][-1]
                    segment["first_call"] = segment_done.get("first_call")
                    total = (segment_done.get("cost") or {}).get("cost_usd")
                    if total is not None:
                        # A CLI killed without its `cost-state` leaves the next one counting from zero, so its total
                        # is this segment's alone.
                        spent = 0.0 if resume.get("cost_unknown") else float(resume.get("spent_usd") or 0.0)
                        segment["cost_usd"] = round(float(total) - spent, 6)
                segment_fields["segments"] = owner["segments"]
                if any(s.get("cost_unknown") for s in owner["segments"]):
                    segment_fields["cost_partial"] = True
            if turn_kind in ("opening", "closing") and not turn_taken and not shutting_down:
                said = f"the {turn_kind} turn an update paused was not reached again, so it was dropped"
                detail = f"{detail}\n--- {said} ---" if detail else said
            # An app going down writes neither record. No `end` is what an interrupted step looks like.
            record = self.journal is not None and not shutting_down
            # A stopped step's tree and transcript are captured *before* `end` is written, and only for a
            # run that is not `done`: a step that wrote its artifact needs no attempt record, and this
            # must not touch anything a `done` run left behind.
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
                    # A failure here must not change the outcome or the `end` record that follows. The attempt
                    # record is best-effort; the run log's `end` row is the one thing never put at risk.
                    pass
            extra: dict[str, Any] = {}
            if record and outcome == "done" and end_fields is not None:
                try:
                    extra = dict(await end_fields())
                except Exception:
                    # The same rule as the attempt record: the `end` row never depends on it.
                    extra = {}
            if record and trial_at is not None:
                trial_model(
                    (running.handle.init_model if running is not None else "") or modeltrial.NEVER_STARTED
                )
            if record:
                self.journal.finished(
                    journal_key, unit, stage, outcome,
                    session_id=session_id,
                    artifact=artifact if outcome == "done" else None,
                    detail=detail or None,
                    denials=denials.count,
                    denied=denials.reasons or None,
                    # Every step that may run a command says how many it was refused for running in the
                    # background, zero included.
                    **({"background": denials.background} if "Bash" in grant.tools else {}),
                    models_used=models_used or None,
                    terminal=terminal or None,
                    **(
                        {"stopped_by": running.stopped_by, **({} if cost else {"cost_unknown": True})}
                        if outcome == "stopped"
                        else {}
                    ),
                    **cost_fields,
                    # The `Author:` the artifact carries, as written: `""` for none, never checked against the
                    # table and never a reason to refuse.
                    **({"author": agents.author_of(_read(directory / artifact))} if outcome == "done" else {}),
                    **extra,
                    **run_fields,
                    # Only a spike's `end` carries it.
                    **({"spike_md": spike_md} if watch else {}),
                    # Only a review's; `closing` only when that turn ran.
                    **({"review_md": review_md} if stage == "review" else {}),
                    **({"closing": closing} if closing is not None else {}),
                    # Only once a reply lacked its opening.
                    **({"opening": opening} if opening is not None else {}),
                    **({"opening_reason": str(unopened)} if opening == "repaired" else {}),
                    # Whether the run's `submit` held an object at the end, and the repair turn's cost when one
                    # ran. Only a step with a channel carries either.
                    **({"submitted": channel.received is not None} if channel is not None else {}),
                    **({"submit_turn": submit_turn} if submit_turn is not None else {}),
                    **segment_fields,
                )
            if progress_pending is not None:
                raise progress_pending
            if closing_pending is not None:
                raise closing_pending
            if opening_pending is not None:
                raise opening_pending
            if submit_pending is not None:
                raise submit_pending
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
                # What guard `stage-result` read, for `Service._ingest` to apply.
                **(
                    {"submitted": channel.inputs(channel.received["object"], channel.received["revision"])}
                    if channel is not None and channel.received is not None and outcome == "done"
                    else {}
                ),
            },
        )
