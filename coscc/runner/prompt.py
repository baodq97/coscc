"""What a step is told: the stage's rules, the envelope its row declares (its row's
`input`: artifacts whole, earlier records, answers and findings from `cos.db`, the app's data),
and the sections the app adds to them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from coscc.agent import agents, pack
from coscc.units import contracts, states, submit
from coscc.runner.review import finding_line
from coscc.runner.reply import RunError


def skill_for(key: str) -> str:
    """The rules for a step: the text of each skill its agent's row `key` names (`pack.skill`, the
    owner's copy first). A row the built-in pack does not ship (the owner's, an imported pack's)
    that names none runs on its own body. Neither stops the step: a built-in stage's body only
    describes it.

    A step run without its rules spends quota and records the same `included` as a full one,
    so absence is fatal. It refuses with `RunError`: the routes map this module's refusals with
    one `except RunError` into a 400 that names the problem; another exception type would surface
    as a 500.
    """
    row = pack.row(key) or {}
    names = list(row.get("skills") or [])
    if (
        not names
        and row.get("pack") != pack.manifest()["name"]
        and str(row.get(pack.BODY) or "").strip()
    ):
        body = str(row[pack.BODY]).strip()
        output = row.get("output") or {}
        if output.get("kind") == "artifact" and output.get("by") != "session":
            # A skill says how its reply opens; a body the owner or Dagaz wrote may not.
            body += (
                f"\n\nYour last reply is this step's record, saved as `{key}.md`: open it with"
                f" the line `# {key.capitalize()}:` and say in a few lines what you did."
            )
        return body
    if not names:
        raise RunError(f"no rules for the {key} stage: its row names no skill")
    try:
        return "\n\n".join(pack.skill(n) for n in names)
    except LookupError as e:
        raise RunError(f"no rules for the {key} stage: {e}") from e


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _artifacts(stage: str, directory: Path, included: list[str]) -> list[str]:
    """Each artifact `stage` declares, whole, under a heading naming it; an absent one adds
    nothing (`contracts.missing` refused the step before a required one could be)."""
    blocks: list[str] = []
    for name in contracts.input_of(stage)["artifacts"]:
        bare = name.rstrip("?")
        text = contracts.artifact_text(directory, bare)
        if text:
            included.append(f"{bare}.md")
            blocks.append(f"# The unit's {bare}.md\n\n{text.rstrip()}")
    return blocks


def _outputs(stage: str, unit_meta: dict[str, Any] | None, included: list[str]) -> list[str]:
    """Each earlier stage's record `stage` declares, as the app holds it."""
    blocks: list[str] = []
    for name in contracts.input_of(stage)["outputs"]:
        record = contracts.record(unit_meta, name)
        if record is not None:
            bare = name.rstrip("?")
            included.append(f"{bare}-record")
            blocks.append(
                f"# The {bare}'s record\n\nWhat {bare} handed back through `submit`; the app "
                f"decides from it, not from `{bare}.md`.\n\n```json\n"
                f"{json.dumps(record, indent=1, ensure_ascii=False)}\n```"
            )
    return blocks


def _answer_line(a: dict[str, Any]) -> str:
    """Who answered, whose decision it is, when and through what: the one line under each answer."""
    return (
        f"Answered by: {a.get('name')} ({a.get('by')}). Date: {a.get('date')}. Via: {a.get('via')}."
    )


_HOLD_HEADS = {"paused": "Paused", "dropped": "Dropped", "active": "Resumed"}

_ANSWERS_ADVICE = (
    "Each is a decision already made, a person's (`person`) or made for them (`delegated`): "
    "write to it, cite it as `<artifact> câu N`, "
    "and never ask it again. The app keeps them and hands them to every later step; do not "
    "copy them into a file."
)


def _answers(stage: str, unit_meta: dict[str, Any] | None, included: list[str]) -> list[str]:
    """The unit's answers and holds, from `cos.db`, each under the question it answers."""
    if not contracts.input_of(stage)["answers"]:
        return []
    meta = unit_meta or {}
    arts = meta.get("artifacts") or {}
    blocks: list[str] = []
    for a in meta.get("answers") or []:
        artifact = str(a.get("artifact"))
        ref = a.get("id") or f"câu {a.get('n')}"
        q = next(
            (
                q
                for q in (arts.get(artifact) or {}).get("questions") or []
                if a.get("n") is not None and q.get("n") == a.get("n")
            ),
            {},
        )
        head = f"### {artifact} {ref}" + (f": {q['text']}" if q.get("text") else "")
        if q.get("recommendation"):
            head += f" (recommended: {q['recommendation']})"
        blocks.append(f"{head}\n{_answer_line(a)}\n\n{a.get('text') or ''}".rstrip())
    for h in meta.get("holds") or []:
        head = _HOLD_HEADS.get(str(h.get("state")), str(h.get("state")))
        blocks.append(
            f"### {head}\nDecided by: {h.get('by')}. Date: {h.get('date')}. Via: {h.get('via')}."
            f"\n\n{h.get('reason') or ''}".rstrip()
        )
    if not blocks:
        return []
    included.append("answers")
    return ["# The answers already given\n\n" + _ANSWERS_ADVICE + "\n\n" + "\n\n".join(blocks)]


def _review_rounds(unit_meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The review rounds handed back through `submit`, oldest first, as `cos.db` holds them."""
    arts = (unit_meta or {}).get("artifacts") or {}
    return next(
        (
            list(a["rounds"])
            for f in states.files_where(kind="review")
            if (a := arts.get(f) or {}).get("rounds")
        ),
        [],
    )


def _left_open(round_: dict[str, Any]) -> str:
    """A round's findings not fixed, one line each."""
    return "\n".join(
        finding_line(
            {
                **f,
                "state": f.get("label"),
                "fixed_in": f.get("fixedIn") or "",
                "text": f.get("text") or "",
            }
        )
        for f in round_.get("findings") or []
        if f.get("label") != "fixed"
    )


# Headings and advice for `impl` only.
PLAN_MAP_HEADING = "# The files this plan changes, as they stand"
PLAN_MAP_ADVICE = (
    "Each file above is one the plan's record names in `files`, with its line count and, "
    "for Python and JavaScript, the line each top-level and class-level definition starts on, "
    "as the tree stood when this step began; `new` is a file not there yet. Read the part you "
    "need with `Read` and `offset`/`limit` instead of searching for it again. The numbers move "
    "once you edit a file."
)
# The app closes the session when a turn ends, so a step that ends its turn to wait has ended.
SESSION_ENDS_HEADING = "# Your last turn ends this session"
SESSION_ENDS_ADVICE = (
    "This session ends when your turn ends. Nothing arrives after it: no notice that a command "
    "finished, no further turn. A command run in the background — `run_in_background`, or a "
    "lone `&` — is refused. Run long commands, the proof among them, in the foreground, and "
    "give one that may pass two minutes a `timeout` of up to 600000 ms. Write what this step "
    "must leave — its file, or the reply the app writes it from — before you end your turn; "
    "never end it saying you will wait for something or come back."
)

# The step's loop is this app's copy, not the worktree's; the app says how to run it.
HARNESS_HEADING = "# The loop"


def harness_advice(directory: Path, state_file: str | Path | None = None) -> str:
    """How to run the loop and which `--root` a unit in `directory` takes, and the snapshot file its
    deciding commands read, which the loop refuses to decide without.

    `sys.executable` is this app's interpreter, so the app's copy of the loop runs; `-P` keeps the
    working directory, the unit's worktree with a `coscc/` of its own, off `sys.path`.
    """
    said = (
        "Where your rules say `uv run python -m coscc.loop <command>`, run "
        f"`{sys.executable} -P -m coscc.loop <command> --root {directory.parent.parent}`. That is "
        "this app's copy and this unit's store; no other copy is the one the app reads. Do not "
        "search the filesystem for it."
    )
    if state_file:
        said += (
            f"\n\n`status`, `gate`, `next`, `rerun`, `unit-branch` and `screens` also "
            f"take `--state {state_file}`: the app's snapshot of this unit's metadata, written as "
            "this step began. Without it they exit 2; no other command takes it."
        )
    return said


def _why_it_runs_again(artifact: str, note: str) -> str:
    """What a stage run again from the board is told about why; the artifact as it stands is
    in the envelope when the stage declares it."""
    note = note.strip()
    told = (
        f"Their note, verbatim, between the two `~~~` lines:\n\n~~~\n{note}\n~~~"
        if note
        else "No note was given: rewrite it on what changed since — new answers, or main."
    )
    return (
        "# Why this stage runs again\n\n"
        f"`{artifact}` was already accepted. The person holding this app's login, recorded "
        "as `owner` rather than by name, asked for this stage to run again. That is a "
        "request, not a decision anyone approved. Write the artifact again from it; every "
        "stage after this one runs again after you.\n\n"
        f"{told}"
    )


def _app_note_block(note: str) -> str:
    """What the app itself tells a step the autopilot queued: why it runs now. Apart from a
    person's note, so the agent never reads it as the intent's owner's wish."""
    return (
        "# What the app noted\n\n"
        "The autopilot queued this step and wrote the note below. It is the app's reading of the "
        "unit, not a person's request or decision.\n\n"
        f"~~~\n{note.strip()}\n~~~"
    )


def _opening(stage: str, agent: dict[str, Any] | None) -> list[str]:
    """The rules of the stage, and above them who the session is."""
    # Outside any `try`: a missing rule set stops the step before any request.
    # The row the process binds the state to, when the step was handed it (`pack.agent_for`).
    key = str(agent.get("key") or stage) if agent is not None else stage
    blocks = [f"# The rules for this stage\n\n{skill_for(key)}"]
    # Who the session is, before its rules; no row adds nothing.
    if agent is not None:
        blocks.insert(0, agents.identity_section(agent))
    return blocks


def _data(stage: str, name: str, heading: str, text: str, included: list[str]) -> list[str]:
    """One source of the app's data under its heading, when `stage` declares it and it says
    something."""
    if not text or name not in contracts.input_of(stage)["data"]:
        return []
    included.append(name)
    return [f"{heading}\n\n{text.rstrip()}"]


def _already_asked(gate_said: str, base_note: str) -> list[str]:
    """What the app checked before it started the step, and the answer it got."""
    blocks: list[str] = []
    # The rules tell a stage to run `coscc.loop gate`, which a toolless stage cannot; the app asked, so
    # a running step has an open gate.
    if gate_said:
        blocks.append(
            "# The gate, already asked\n\n"
            "The app ran `coscc.loop gate` for this stage before starting this step, and it "
            "is open. It would not have started otherwise. This is what the gate said:\n\n"
            f"    {gate_said}\n\n"
            "Do not ask it again and do not treat it as unasked — you may have no tools "
            "to run it with, and that is not a reason to hold back an artifact."
        )

    # The base refresh may have failed while the step still runs; say so.
    if base_note:
        blocks.append(f"# The base this step runs on\n\n{base_note}")

    return blocks


def _shared(
    stage: str,
    drift_note: str,
    idea_note: str,
    siblings_note: str,
    mentions_note: str,
    included: list[str],
) -> list[str]:
    """The app's data beside the artifacts: what `main` changed under the plan, the idea a child
    unit shares, the sibling checkouts, the units this one names."""
    return [
        *_data(stage, "drift", "# The files main changed since the plan", drift_note, included),
        *_data(stage, "idea", "# The idea this unit was opened from", idea_note, included),
        *_data(
            stage,
            "siblings",
            "# The sibling repositories this step may read",
            siblings_note,
            included,
        ),
        *_data(stage, "mentions", "# The units this unit names", mentions_note, included),
    ]


def _where_you_work(
    stage: str,
    workspace: str | Path,
    worktree: str,
    artifact: str,
    process: str | None,
) -> list[str]:
    """A state written `by: scratch` only: its scratch directory, the worktree it may read, its
    progress file."""
    if states.by_of(process, stage) != "scratch":
        return []
    scratch = Path(workspace).expanduser().resolve()
    return [
        "# Where you work\n\n"
        f"Your working directory is `{scratch}`, a "
        "throwaway directory the app deletes when this step ends. Write probe code "
        "there and nowhere else.\n\n"
        + (
            f"The unit's worktree is `{worktree}`. Read it; never write to it. The app "
            "records its `HEAD` and `git status --porcelain` before this step and again "
            f"after, and fails the step, writing no `{artifact}`, if either changed."
            if worktree
            else "No worktree was named for this step."
        )
        + "\n\n"
        f"Your progress file is `{scratch / artifact}` (the rules' *The progress "
        f"file*). If this step ends without a usable final reply — a reply with no usable end, a "
        f"session that broke — the app writes `{artifact}` from this file."
    ]


def _room(ceilings: tuple[int, float | None] | None, included: list[str]) -> list[str]:
    """The step's budget, so it knows its room: both ceilings, and what happens at one."""
    if ceilings is None:
        return []
    turns, usd = ceilings
    included.append("budget")
    limit = f"{turns} turns" + (f" and ${usd:.2f}" if usd else "")
    return [
        "# Your room\n\n"
        f"This step has at most {limit}. At a ceiling the app pauses you and keeps your session; "
        "a person may raise it, and you go on from where you stopped. Pace the work to it."
    ]


def _tools(
    stage: str,
    directory: Path,
    plan_map: str,
    runs_commands: bool,
    state_file: str | Path | None,
    included: list[str],
) -> list[str]:
    """What `impl` may read and run, and what a step holding `Bash` is told about its session."""
    blocks: list[str] = []
    # Placed only: the plan map is built elsewhere.
    if plan_map:
        blocks += _data(
            stage, "plan-map", PLAN_MAP_HEADING, f"{plan_map}\n\n{PLAN_MAP_ADVICE}", included
        )
    if runs_commands:
        blocks.append(f"{SESSION_ENDS_HEADING}\n\n{SESSION_ENDS_ADVICE}")
        blocks.append(f"{HARNESS_HEADING}\n\n{harness_advice(directory, state_file)}")
    return blocks


_FAST_IMPL = (
    "# The fast lane\n\n"
    "This is a fix with no spec or plan; `intent.md` is the plan and the open gate is what "
    "lets you code. Intent's record names `{source}` as where the expected result is "
    "stated: {expected}\n\n"
    "In this order:\n\n"
    "1. Check that `{source}` says what that sentence says.\n"
    "2. Commit a test that reproduces the bug, and nothing else. Run it and keep its failing "
    "output.\n"
    "3. Commit the fix. Run the same test and keep its passing output.\n"
    "4. `## What was measured` gives both shas, the test command, and both outputs. The header "
    "names no `Plan:`.\n\n"
    "If the source says otherwise, or the fix needs more than this lane, hand back `not-ready` "
    "with `left_lane` saying why: the unit then goes through spec and plan."
)
_FAST_REVIEW = (
    "# The fast lane\n\n"
    "This is a fix with no spec or plan. Check three things; a missing one is a "
    "finding of `medium` or more:\n\n"
    "- the commit holding only the reproducing test comes before the fix;\n"
    "- `impl.md` shows that test failing at the first commit and passing at the fix;\n"
    "- `{source}` says what intent's record says it should: {expected}"
)
# By what the state is: a state that writes in the unit's branch, and a review.
_LANE_BLOCKS = {"session": _FAST_IMPL, "review": _FAST_REVIEW}


def _lane(
    stage: str,
    lane: str,
    unit_meta: dict[str, Any] | None,
    included: list[str],
    process: str | None,
) -> list[str]:
    """What only a unit in the fast lane is told, with the `source` and `expected` of the `fix`
    record the unit's artifacts hold. The gate says the lane; the record is read as the snapshot
    carries it."""
    role = states.by_of(process, stage)
    role = "review" if states.kind_of(process, stage) == "review" else role
    if lane != "fast" or role not in _LANE_BLOCKS:
        return []
    fix = next(
        (
            f
            for a in ((unit_meta or {}).get("artifacts") or {}).values()
            if (f := (a.get("result") or {}).get("fix"))
        ),
        {},
    )
    expected = fix.get("expected") or {}
    included.append("fast-lane")
    return [
        _LANE_BLOCKS[role].format(
            source=expected.get("source", "the file intent names"),
            expected=expected.get("text", ""),
        )
    ]


def _findings(
    stage: str,
    key: str,
    unit_meta: dict[str, Any] | None,
    included: list[str],
    process: str | None,
) -> list[str]:
    """The findings the last review round left open, from `cos.db`: what a session state fixes
    when a review sent the unit back, what a review carries forward into its next round."""
    rounds = _review_rounds(unit_meta)
    if not rounds or not contracts.input_of(key)["findings"]:
        return []
    last = rounds[-1]
    left = _left_open(last) or "(no finding is left open)"
    arts = (unit_meta or {}).get("artifacts") or {}
    status = next(
        (a.get("status") for f in states.files_where(kind="review") if (a := arts.get(f) or {})),
        None,
    )
    fixing = states.by_of(process, stage) == "session"
    if fixing and status != "changes-requested":
        return []
    included.append("findings")
    if fixing:
        return [
            "# The findings the last review round left open\n\n"
            "The last review asked for changes. Fix every finding below on the branch, one "
            "commit per finding where that is possible, then push the branch, then record in "
            "impl.md which commit fixed which finding. The next review is offered only once a "
            "fix is on the pull request, so a fix left unpushed keeps this unit on impl.\n\n"
            f"Round {last.get('n')}:\n\n{left}"
        ]
    return [
        "# The findings the last review round left open\n\n"
        f"`review.md` already holds rounds up to Round {last.get('n')}, and the app keeps them. "
        "Do not copy them into your reply. Reply with the title, the header line and the next "
        "`## Round N` section only; the app writes the earlier rounds back under your header, "
        "unchanged, and appends yours after them. Carry each finding below forward with its "
        "id.\n\n"
        f"Round {last.get('n')}:\n\n{left}"
    ]


def _push(stage: str, branch: str, process: str | None) -> list[str]:
    """A session state only: the one push the gate lets through, spelled with the branch it reads."""
    if states.by_of(process, stage) != "session" or not branch:
        return []
    return [f"# Pushing\n\nPush with `git push origin {branch}`."]


def _unfinished_round(
    stage: str,
    unfinished_round: dict[str, Any] | None,
    included: list[str],
    process: str | None,
) -> list[str]:
    """A review only: why it runs again after a round the loop did not count.

    The last round asked for changes but dropped ids an earlier round raised, so the loop does
    not count it and sent the unit here again. The block above may say nothing is left open;
    this says why the review runs anyway. The ids are the loop's, carried by
    `runner.steps.Steps.run_step`.
    """
    if states.kind_of(process, stage) != "review" or not unfinished_round:
        return []
    number = int(unfinished_round["n"])
    dropped = ", ".join(f"`{i}`" for i in unfinished_round.get("dropped") or [])
    included.append("review-unfinished")
    return [
        "# The round that did not count\n\n"
        f"Round {number} asked for changes but does not list {dropped}, which an earlier "
        "round raised, so the loop does not count it against `COS_REVIEW_ROUNDS` and "
        f"this review runs again. Write Round {number + 1} as a full round for the commit "
        "named below. List every finding of every earlier round with its label, "
        f"{dropped} among them; the earlier rounds are in the unit's `review.md`."
    ]


def _commit_reviewed(stage: str, head: str, process: str | None) -> list[str]:
    """A review only: the commit it reviews.

    `write-review` needs the commit it reviewed, and the `ship` gate reads that line. A step
    runs in the unit's worktree, whose `.git` is a file pointing into the main repository, and
    a `Read` there is refused as outside both the worktree and the unit. The app has already
    read the head for the run log, so it hands the same value over rather than widen the boundary.
    """
    if states.kind_of(process, stage) != "review":
        return []
    if head:
        return [
            "# The commit you are reviewing\n\n"
            "The app read the head of this checkout before starting the step. Record it "
            "on the round's `Reviewed:` line:\n\n"
            f"    {head}\n\n"
            "It is the same value the run log's `start` record carries. Do not read it out "
            "of `.git/`: the checkout is a git worktree whose git directory lies outside "
            "what this step may read."
        ]
    return [
        "# The commit you are reviewing\n\n"
        "The app could not read the head of this checkout: it is not a git checkout, "
        "or `git rev-parse HEAD` failed. Do not guess one. Leave the round's "
        "`Reviewed:` line without a commit and say why under "
        "`### What was not reviewed`; the `ship` gate will stay closed, which is right."
    ]


def _handed(
    stage: str,
    last_attempt: str,
    integration_note: str,
    screens_note: str,
    included: list[str],
) -> list[str]:
    """What `runner.steps.Steps.run_step` built for this step; this only places it."""
    blocks: list[str] = []
    if last_attempt:
        included.append("last-attempt")
        blocks.append(f"# The attempt before this one\n\n{last_attempt}")
    # Built by `integrate.describe_for_review` and `retake.describe_for_review`; each carries
    # its own heading.
    for name, note in (("integration", integration_note), ("screens", screens_note)):
        if note and name in contracts.input_of(stage)["data"]:
            included.append(name)
            blocks.append(note.rstrip())
    return blocks


def _given(directory: Path, included: list[str]) -> list[str]:
    """The envelope's table of contents: which of the unit's files the prompt holds whole, so
    the step does not Read them again."""
    files = [p for p in included if p.endswith(".md")]
    if not files:
        return []
    named = ", ".join(f"`{f}`" for f in files)
    return [
        "# What you are given\n\n"
        f"Each part below has its own heading. {named} are here whole, as `{directory.resolve()}` "
        "holds them now; the answers and findings are the app's rows. Do not Read them again: "
        "Read only what is not here."
    ]


# The project instructions are already in the prompt; the app does not read them for a language.
_LANGUAGE = (
    "Prose in the language the project instructions set (Vietnamese if they set none); "
    "filenames and headings in English."
)


def _task(
    workspace: str | Path,
    directory: Path,
    unit: str,
    artifact: str,
    writes_own: bool,
) -> str:
    """What the step is asked to do, and where it leaves the artifact."""
    if writes_own:
        # The file and a reply could disagree, so the file is the only record.
        return (
            f"# Your task\n\n"
            f"Do the work this unit's plan authorises, in the repository at "
            f"`{Path(workspace).expanduser().resolve()}`, then write `{directory / artifact}` "
            "recording what you did.\n\n"
            f"{_LANGUAGE} Write it yourself with your tools — do not paste it into your reply."
        )
    return (
        f"# Your task\n\n"
        f"Write `{artifact}` for the work unit `{unit}`.\n\n"
        "Reply with the file's complete contents and nothing else — no preamble, no "
        f"code fence, no commentary. {_LANGUAGE}"
    )


def compose_prompt(
    workspace: str | Path,
    directory: str | Path,
    unit: str,
    stage: str,
    artifact: str,
    writes_own: bool = False,
    gate_said: str = "",
    head: str = "",
    base_note: str = "",
    last_attempt: str = "",
    integration_note: str = "",
    screens_note: str = "",
    drift_note: str = "",
    worktree: str = "",
    ceilings: tuple[int, float | None] | None = None,
    rerun: bool = False,
    rerun_note: str = "",
    app_note: str = "",
    plan_map: str = "",
    unfinished_round: dict[str, Any] | None = None,
    idea_note: str = "",
    siblings_note: str = "",
    mentions_note: str = "",
    runs_commands: bool = False,
    agent: dict[str, Any] | None = None,
    unit_meta: dict[str, Any] | None = None,
    state_file: str | Path | None = None,
    blocks: tuple[tuple[str, str], ...] = (),
    branch: str = "",
    lane: str = "full",
    process: str | None = None,
) -> tuple[str, list[str]]:
    """The prompt for one step, and its envelope: the name of every part it was handed.

    The unit's part is what the stage's row declares (`contracts.input_of`) and nothing else:
    its artifacts whole, earlier stages' records, the answers and holds and the open findings
    from `unit_meta` (the unit's snapshot entry), and the app's data — `drift_note`,
    `idea_note`, `siblings_note`, `mentions_note`, `plan_map`, `integration_note`,
    `screens_note` — each placed only when declared. `contracts.missing` refuses a step before
    this runs; an absent optional part adds not one byte, as does every empty section.

    `agent` is the stage's resolved row of the agent table; its section opens the prompt.
    `rerun` is true only for a stage a person ran again from the board, with `rerun_note`
    their note; `app_note` is the autopilot's own, under its own heading. `unfinished_round` is
    `{"n", "dropped"}` of a last round the loop read so (a review only). `process` is the unit's. `lane` is what the gate said of the unit (`fast` or `full`).
    `runs_commands` is true when the step's grant holds `Bash`. `branch` is the unit's branch
    the worktree stands on, the one push `impl` may make. `blocks` are the named texts features
    add, in order, before the rerun block and the task.

    `directory` is handed in: the artifacts live in the product's own store while `workspace`
    stays the repository the work is done in.
    """
    directory = Path(directory)
    included: list[str] = []
    # The row the state's process binds it to: what it declares is read by that key, what the
    # state is (`states`) by the state's name.
    key = (
        (agent or {}).get("key") or pack.agent_for(process or pack.DEFAULT_PROCESS, stage) or stage
    )
    key = str(key)
    opening = _opening(key, agent)
    # The order of these calls is the order of the prompt.
    parts = [
        *_already_asked(gate_said, base_note),
        *_artifacts(key, directory, included),
        *_outputs(key, unit_meta, included),
        *_shared(key, drift_note, idea_note, siblings_note, mentions_note, included),
        *_where_you_work(stage, workspace, worktree, artifact, process),
        *_room(ceilings, included),
        *_tools(key, directory, plan_map, runs_commands, state_file, included),
        *_lane(stage, lane, unit_meta, included, process),
        *_answers(key, unit_meta, included),
        *_findings(stage, key, unit_meta, included, process),
        *_push(stage, branch, process),
        *_unfinished_round(stage, unfinished_round, included, process),
        *_commit_reviewed(stage, head, process),
        *_handed(key, last_attempt, integration_note, screens_note, included),
    ]
    parts = [*opening, *_given(directory, included), *parts]
    parts += [text for _name, text in blocks if text]
    # Just before the task, so the note is the last thing read before it.
    if app_note.strip():
        parts.append(_app_note_block(app_note))
    if rerun:
        parts.append(_why_it_runs_again(artifact, rerun_note))
    parts.append(_task(workspace, directory, unit, artifact, writes_own))
    block = submit_block(key, artifact, writes_own)
    if block:
        parts.append(block)
    return "\n\n---\n\n".join(parts), included


# What the engine says of a field its kind requires; every other field is the agent's, and its
# skill says what goes in it.
_KIND_SAYS = {
    "judgement": "`ready` when the file is finished; a `not-ready` unit stays at this stage.",
    "questions": "Every item under `## Open questions` still waiting on a person, with the number "
    "the file gives it; `[]` when there is none. `text` only asks; `recommendation` is the answer "
    "you recommend and why, in a sentence or two, so a person can take it with one press.",
    "verdict": "`pass` when nothing blocks the merge, `changes-requested` when a finding must be "
    "fixed first, `needs-person` when only a person can settle one.",
    "criteria": "Every criterion you graded, once each: `criterion` is `R<n>` of the spec, `P<n>` the "
    "n-th `## Proof` item of the plan, `O<n>` the n-th sentence of the intent's proposed outcome when "
    "there is no spec or plan, or `S<n>` of the UI standard; `source` quotes it; `evidence` is "
    "`path:lines`. `pass` is refused while one is `no`.",
    "findings": "Every finding of this round, those an earlier round raised carried forward with "
    'their id. `fixed_in` is the commit of a `fixed` one and `""` otherwise; `criterion` is one '
    "of the criteria above that the finding breaks or leaves unclear; `text` is what the finding "
    "says, without its id, label, place or severity.",
    "screens": "One entry per screenshot you opened, `size` as `1440x900`; `[]` when you opened "
    "none.",
}


def _describe(t: contracts.FieldType) -> str:
    """A declared type in words: what `submit` takes for it."""
    if isinstance(t, str):
        if t == "json":
            return "an object"
        return t if t == "text" else "a whole number" if t == "number" else f"text matching `{t}`"
    if isinstance(t, list):
        return "one of " + ", ".join(f"`{w}`" for w in t)
    if set(t) == {"enum"}:
        return _describe(t["enum"])
    if set(t) == {"list"}:
        return f"a list of {_describe(t['list'])}"
    inner = ", ".join(
        f"{n.rstrip('?')}: {_describe(sub)}" + (" (may be left out)" if n.endswith("?") else "")
        for n, sub in t.items()
    )
    return "{" + inner + "}"


def _fields(agent: str) -> list[str]:
    """One line for each field `agent` declares, in the declared order."""
    out = contracts.output(agent)
    says = {f: _KIND_SAYS[f] for f in contracts.READS.get(out["kind"], {}) if f in _KIND_SAYS}
    lines = []
    for name, t in out["fields"].items():
        bare = name.rstrip("?")
        line = f"- `{bare}`: {_describe(t)}" + (" (may be left out)" if name != bare else "")
        lines.append(line + (f". {says[bare]}" if bare in says else "."))
    return lines


def submit_block(stage: str, artifact: str, writes_own: bool) -> str:
    """How a stage hands back its judgement, written from its declaration
    (`contracts.output`): the object is all the app reads of it. `""` for a stage that hands back
    no stage result. English: an instruction to the model.
    """
    kind = (contracts.declarations().get(stage) or {}).get("kind")
    if kind == "review":
        return round_block(stage, artifact)
    if kind != "artifact":
        return ""
    when = (
        f"Call it once `{artifact}` is written and final. Writing the file again after that "
        "makes the object stale, and the app refuses it."
        if writes_own
        else f"Call it before you reply; once it answers, reply with `{artifact}` and nothing after it."
    )
    return (
        "# Hand back your judgement\n\n"
        f"The app takes your judgement of `{artifact}` from the object you hand it through the "
        f"`submit` tool (`{submit.NAME}`), not from the file. {when} The object:\n\n"
        + "\n".join([f"- `stage`: `{stage}`.", *_fields(stage)])
        + "\n\n"
        "If `submit` returns an error, the app has checked your object against the unit: "
        f"{submit.AGAIN} Keep calling it until it is accepted. A step that hands back no object "
        "ends failed, whatever its file says."
    )


def round_block(stage: str, artifact: str) -> str:
    """How a review hands back its round, written from its declaration. English: an
    instruction to the model.
    """
    return (
        "# Hand back your round\n\n"
        "The app takes the verdict, the findings and the screenshots of your round from the "
        f"object you hand it through the `submit` tool (`{submit.NAME}`), and writes the round's "
        "`Reviewed:` line, its `### Findings` and its `### Screens` from it, with the head this "
        "step ran on and the round's number. Call it before you reply; once it answers, reply "
        f"with `{artifact}` and nothing after it. The object:\n\n"
        + "\n".join(_fields(stage))
        + "\n\n"
        "If `submit` returns an error, the app has checked your object against the unit: "
        f"{submit.AGAIN} Keep calling it until it is accepted. A review that hands back no round "
        "ends failed, whatever its file says."
    )


def submit_prompt(stage: str, artifact: str, why: str) -> str:
    """The repair turn a step gets when its session ended without an accepted object: the tool
    again, and nothing else.
    """
    what, section = (
        ("round", "Hand back your round")
        if (contracts.declarations().get(stage) or {}).get("kind") == "review"
        else ("judgement", "Hand back your judgement")
    )
    return (
        f"Your step ended without handing back its object: {why}\n\n"
        f"Call the `submit` tool now with your {what} of `{artifact}` as it stands, as "
        f"*{section}* above sets out. Do not change any file and do not reply "
        f"with the artifact again. {submit.AGAIN}"
    )


# A spike's progress file, read when its reply is not an artifact.
