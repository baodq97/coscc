"""What a step is told: the stage's rules, the artifacts it reads, and the sections the app
adds to them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from coscc.agent import agents, harness
from coscc.units import submit
from coscc.agent.policy import is_prose_stage
from coscc.runner.review import (
    INCOMPLETE_SECTIONS,
    _ROUND_RE,
    _header_status,
    _round_meta,
    _round_number,
    _rounds,
    open_findings,
)
from coscc.runner.reply import RunError


def skill_for(stage: str) -> str:
    """The rules for a stage, from this app's copy. Missing stops the step.

    A step run without its rules spends quota and records the same `included` as a full one,
    so absence is fatal. It refuses with `RunError`, not `MissingRules`: `service` maps this
    module's refusals with one `except RunError` into a 400 that names the problem; another
    exception type would surface as a 500.
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


# The `## Answers` section as bytes, from its first heading line to the end of the file; `None`
# when absent. Bytes, so decoding and newline translation cannot move a byte.
def answers_section(raw: bytes) -> bytes | None:
    idx = 0
    while True:
        nl = raw.find(b"\n", idx)
        line = raw[idx : nl if nl != -1 else len(raw)]
        if line.rstrip(b" \t\r") == b"## Answers":
            return raw[idx:]
        if nl == -1:
            return None
        idx = nl + 1


def strip_answers(body: str) -> str:
    """A reply, with everything from its own first `## Answers` line onward dropped.

    Whatever a reply says under that heading carries no authority. The only section that
    reaches disk is the one already there, found by `answers_section`. A reply with no such
    line is returned unchanged.
    """
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() == "## Answers":
            return "\n".join(lines[:i]).rstrip() + "\n"
    return body


def with_answers(body: str, section: bytes | None) -> bytes:
    """The bytes to write: the stage's own text, and the Answers section, unmoved.

    `section` is `None` when the artifact never had one; then this is `body` UTF-8 encoded and
    nothing else. Otherwise `body`, one blank line, and the section on disk, byte for byte.
    """
    if section is None:
        return body.encode("utf-8")
    return body.rstrip("\n").encode("utf-8") + b"\n\n" + section


# Heads of the blocks `cos.db` carries; every other block under `## Answers` is the file's alone.
_ROW_HEAD = re.compile(r"^###\s+(?:Câu\s+\d+|F\d+|Paused|Dropped|Resumed)\s*$")
_HOLD_HEADS = {"paused": "Paused", "dropped": "Dropped", "active": "Resumed"}


def _file_blocks(section: str) -> list[str]:
    """The blocks of a file's `## Answers` that `cos.db` does not carry, verbatim and in file
    order. An answer or hold block is cut: the row is rendered in its place, so a prompt
    carries each answer once.
    """
    blocks: list[list[str]] = []
    keep = False
    for line in section.splitlines()[1:]:
        if line.startswith("###"):
            keep = not _ROW_HEAD.match(line)
            if keep:
                blocks.append([])
        if keep:
            blocks[-1].append(line)
    return ["\n".join(b).strip() for b in blocks]


def _row_blocks(meta: dict[str, Any], artifact: str) -> list[str]:
    """`artifact`'s answers from `cos.db`, and `intent.md`'s holds, each as an appended block."""

    def block(head: str, who: str, row: dict[str, Any], text: Any) -> str:
        return (
            f"### {head}\n{who}: {row.get('by')}. Date: {row.get('date')}. Via: {row.get('via')}.\n\n"
            f"{text or ''}"
        ).rstrip()

    blocks = [
        block(a.get("id") or f"Câu {a.get('n')}", "Answered by", a, a.get("text"))
        for a in meta.get("answers") or []
        if a.get("artifact") == artifact
    ]
    if artifact == "intent.md":
        blocks += [
            block(
                _HOLD_HEADS.get(str(h.get("state")), str(h.get("state"))),
                "Decided by",
                h,
                h.get("reason"),
            )
            for h in meta.get("holds") or []
        ]
    return blocks


def answers_for(raw: bytes, artifact: str, meta: dict[str, Any] | None) -> str | None:
    """The `## Answers` a prompt shows for `artifact`, whose bytes are `raw`: its rows from
    `cos.db` first, then the file's own blocks the database does not carry. `None` when there
    is neither. `meta` is the unit's entry in the snapshot; `None` means the file's section as
    it stands.
    """
    section = answers_section(raw)
    if meta is None:
        return None if section is None else section.decode("utf-8", errors="replace")
    blocks = _row_blocks(meta, artifact)
    if section is not None:
        blocks += _file_blocks(section.decode("utf-8", errors="replace"))
    return "## Answers\n\n" + "\n\n".join(blocks) if blocks else None


def with_rows(raw: bytes, artifact: str, meta: dict[str, Any] | None) -> str:
    """An artifact as a prompt carries it whole: its text above `## Answers`, and `answers_for` in
    place of the section the file holds.
    """
    section = answers_section(raw)
    above = (raw if section is None else raw[: len(raw) - len(section)]).decode(
        "utf-8", errors="replace"
    )
    answers = answers_for(raw, artifact, meta)
    return above if answers is None else f"{above.rstrip()}\n\n{answers}\n"


def _open_questions(text: str) -> str:
    """The `## Open questions` section of an artifact, verbatim: from that heading to the next
    `## ` heading or the end of the file. Mirrors `cos.mjs`'s `section()`; the numbers are what
    `### Câu N` in the Answers section refers back to.
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.rstrip() == "## Open questions"), None)
    if start is None:
        return "It carries no `## Open questions` section."
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).rstrip()


# One fixed string wherever the block appears.
_ANSWERS_ADVICE = (
    "This is a person's decision, already made. Cite it as `<artifact> ## Answers, câu N` "
    "rather than reporting it back as if you had found it yourself. Do not copy this "
    "section into your reply — the app keeps these answers itself and gives them to every "
    "later step. If you still mention a question already answered here, keep its number."
)


def _answers_block(
    directory: Path, artifact: str, repeat_content: bool, meta: dict[str, Any] | None = None
) -> str | None:
    """What a re-run is told about the answers its own artifact already carries. `None` when the
    artifact has never been written or has no answer.

    `repeat_content` is false only for `intent`, whose file is already in the prompt in full.
    """
    try:
        raw = (directory / artifact).read_bytes()
    except OSError:
        return None
    section = answers_for(raw, artifact, meta)
    if section is None:
        return None
    if not repeat_content:
        return f"# The answers already given to this artifact\n\n{_ANSWERS_ADVICE}"
    text = raw.decode("utf-8", errors="replace")
    return (
        "# The answers already given to this artifact\n\n"
        f"{_open_questions(text)}\n\n"
        f"{section}\n\n"
        f"{_ANSWERS_ADVICE}"
    )


# `impl` writes `impl.md` itself, so nothing writes the section back after it.
_IMPL_ANSWERS_ADVICE = (
    "This is a person's decision, already made. Cite it as `impl.md ## Answers, câu N` "
    "rather than reporting it back as if you had found it yourself. You write `impl.md` "
    "yourself: do not edit, move or add a block to its `## Answers` section, and put "
    "everything you write into `impl.md` above that section, leaving it the end of the file "
    "byte for byte. A question already answered keeps its number; a new question takes a "
    "number not yet used anywhere in the file."
)


def _impl_answers_block(directory: Path, meta: dict[str, Any] | None = None) -> str | None:
    """What an `impl` step is told about a person's answers to its own `impl.md`: its
    `## Open questions` verbatim, the answers as `answers_for` renders them, and
    `_IMPL_ANSWERS_ADVICE`. `None` when `impl.md` cannot be read or has no answer. The rest of
    `impl.md` stays named by path only.
    """
    try:
        raw = (directory / "impl.md").read_bytes()
    except OSError:
        return None
    section = answers_for(raw, "impl.md", meta)
    if section is None:
        return None
    return (
        "# The answers already given to this artifact\n\n"
        f"{_open_questions(raw.decode('utf-8', errors='replace'))}\n\n"
        f"{section}\n\n"
        f"{_IMPL_ANSWERS_ADVICE}"
    )


# Headings and advice for `impl` only.
PLAN_MAP_HEADING = "# The files this plan changes, as they stand"
PLAN_MAP_ADVICE = (
    "Each file above is one the plan's `## Files that change` names, with its line count and, "
    "for Python and JavaScript, the line each top-level and class-level definition starts on, "
    "as the tree stood when this step began; `new` is a file not there yet. Read the part you "
    "need with `Read` and `offset`/`limit` instead of searching for it again. The numbers move "
    "once you edit a file."
)
COMMANDS_HEADING = "# The commands this step may run"
COMMANDS_ADVICE = (
    "Every segment of a command line, each part after `;`, `&&`, `||` or `|`, must open with "
    "one of these words; any other is refused, `cd` and `timeout` among them. To read part of "
    "a file, use `Read` with `offset` and `limit`. A redirect that writes a file, anywhere but "
    "`/dev/null` or under a `/tmp` directory named after this unit, is refused, and so is a "
    "command or process substitution (`$(…)`, backticks, `<(…)`): write with `Write` or "
    "`Edit` instead."
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

# The step's `cos.mjs` is this app's copy, not the skills' path; the app says where it is.
HARNESS_HEADING = "# The harness script"


def harness_advice(directory: Path, state_file: str | Path | None = None) -> str:
    """Where `cos.mjs` is and which `--root` a unit in `directory` takes, and the snapshot file its
    deciding commands read, which `cos.mjs` refuses to decide without.
    """
    said = (
        f"Where your rules say `node .claude/scripts/cos.mjs <command>`, run "
        f"`node {harness.script()} <command> --root {directory.parent.parent}`. That is this "
        "app's copy and this unit's store; no other copy is the one the app reads. Do not "
        "search the filesystem for it."
    )
    if state_file:
        said += (
            f"\n\n`status`, `gate`, `next`, `rerun`, `unit-branch`, `pr-text` and `screens` also "
            f"take `--state {state_file}`: the app's snapshot of this unit's metadata, written as "
            "this step began. Without it they exit 2; no other command takes it."
        )
    return said


# Stages that get their artifacts by path, and the one artifact each carries whole.
_EMBED: dict[str, tuple[str, ...]] = {
    "impl": ("plan.md",),
    "review": ("impl.md",),
}
_POINTING = frozenset(_EMBED)

UNIT_FILES_ADVICE = (
    "The files not embedded above are not in this prompt; Read one when your rules or your "
    "task need it."
)


def _rerun_block(directory: Path, stage: str, artifact: str, note: str) -> str:
    """What a stage run again from the board is told about why. For `spec` and `plan` it carries
    the artifact as it stands, above its `## Answers`: no other part of the prompt does.
    `intent`'s own file is already above, and `spike` and `pr` are handed or named theirs.
    """
    note = note.strip()
    told = (
        f"Their note, verbatim, between the two `~~~` lines:\n\n~~~\n{note}\n~~~"
        if note
        else "No note was given: rewrite it on what changed since — new answers, or main."
    )
    text = (
        "# Why this stage runs again\n\n"
        f"`{artifact}` was already accepted. The person holding this app's login, recorded "
        "as `owner` rather than by name, asked for this stage to run again. That is a "
        "request, not a decision anyone approved. Write the artifact again from it; every "
        "stage after this one runs again after you.\n\n"
        f"{told}"
    )
    if stage in ("spec", "plan"):
        try:
            raw = (directory / artifact).read_bytes()
        except OSError:
            return text
        section = answers_section(raw)
        above = raw[: len(raw) - len(section)] if section is not None else raw
        text += (
            f"\n\n`{artifact}` as it stands, above its `## Answers`:\n\n"
            f"{above.decode('utf-8', errors='replace').rstrip()}"
        )
    return text


def build_prompt(*args: Any, **kwargs: Any) -> tuple[str, list[str]]:
    """`compose_prompt` without `pointed`."""
    prompt, included, _ = compose_prompt(*args, **kwargs)
    return prompt, included


def _embedded(directory: Path, name: str, unit_meta: dict[str, Any] | None) -> str:
    """An artifact as a prompt embeds it, with its answers from `cos.db` (`with_rows`);
    `unit_meta=None` is the file.
    """
    text = _read(directory / name)
    if text and unit_meta is not None:
        text = with_rows((directory / name).read_bytes(), name, unit_meta)
    return text


def _opening(stage: str, agent: dict[str, Any] | None) -> list[str]:
    """The rules of the stage, and above them who the session is."""
    # Outside any `try`: a missing rule set stops the step before any request.
    blocks = [f"# The rules for this stage\n\n{skill_for(stage)}"]
    # Who the session is, before its rules; no row adds nothing.
    if agent is not None:
        blocks.insert(0, agents.identity_section(agent))
    return blocks


def _already_asked(gate_said: str, base_note: str, drift_note: str) -> list[str]:
    """What the app checked before it started the step, and the answer it got."""
    blocks: list[str] = []
    # The rules tell a stage to run `cos.mjs gate`, which a toolless stage cannot; the app asked, so
    # a running step has an open gate.
    if gate_said:
        blocks.append(
            "# The gate, already asked\n\n"
            "The app ran `cos.mjs gate` for this stage before starting this step, and it "
            "is open. It would not have started otherwise. This is what the gate said:\n\n"
            f"    {gate_said}\n\n"
            "Do not ask it again and do not treat it as unasked — you may have no tools "
            "to run it with, and that is not a reason to hold back an artifact."
        )

    # The base refresh may have failed while the step still runs; say so.
    if base_note:
        blocks.append(f"# The base this step runs on\n\n{base_note}")

    # Files the plan names that `main` changed since; `""` adds nothing.
    if drift_note:
        blocks.append(f"# The files main changed since the plan\n\n{drift_note}")
    return blocks


def _what_it_follows(
    directory: Path,
    stage: str,
    stages: list[str],
    unit_meta: dict[str, Any] | None,
    included: list[str],
) -> list[str]:
    """The intent, the stage before this one, and for a fix in the fast lane the intent again."""
    blocks: list[str] = []
    pointing = stage in _POINTING
    intent = "" if pointing else _embedded(directory, "intent.md", unit_meta)
    if intent:
        included.append("intent.md")
        blocks.append(f"# The intent this work is authorised by\n\n{intent}")

    # The stage before this one, from the board's stage list; a pointing stage carries only its `_EMBED` files.
    position = stages.index(stage) if stage in stages else -1
    for earlier in reversed(stages[:position]):
        name = f"{earlier}.md"
        if pointing and name not in _EMBED[stage]:
            continue
        text = _embedded(directory, name, unit_meta)
        if text and name not in included:
            included.append(name)
            blocks.append(f"# The {earlier} it follows\n\n{text}")
            break

    # With no plan, the intent is what impl builds from.
    if stage == "impl" and "plan.md" not in included:
        text = _embedded(directory, "intent.md", unit_meta)
        if text:
            included.append("intent.md")
            blocks.append(f"# The intent it follows\n\n{text}")
    return blocks


def _shared(stage: str, idea_note: str, siblings_note: str) -> list[str]:
    """Right after the stage before: a child unit has no `idea.md`, and the idea it shares
    stands where that would have.
    """
    blocks: list[str] = []
    if idea_note and stage == "intent":
        blocks.append(f"# The idea this unit was opened from\n\n{idea_note.rstrip()}")
    if siblings_note and stage == "impl":
        blocks.append(f"# The sibling repositories this step may read\n\n{siblings_note.rstrip()}")
    return blocks


def _second_artifact(
    directory: Path,
    stage: str,
    unit_meta: dict[str, Any] | None,
    included: list[str],
) -> list[str]:
    """The stage before is one artifact, and three stages need a second. `plan` follows `spike`
    when one ran, but the requirements it orders are still in `spec.md`. A `spec` re-run after
    a spike must be rewritten on that measurement. A `spike` re-run needs the one before it to
    set `Round:`.
    """
    blocks: list[str] = []
    spike = _read(directory / "spike.md") if stage in ("spec", "spike") else ""
    if stage == "plan" and "spike.md" in included and "spec.md" not in included:
        spec = _embedded(directory, "spec.md", unit_meta)
        if spec:
            included.append("spec.md")
            blocks.append(f"# The spec the spike measured\n\n{spec}")
    if stage == "spec" and spike and "spike.md" not in included:
        included.append("spike.md")
        blocks.append(
            "# What the spike measured\n\n"
            "A spike ran on the spec before this one. Rewrite the spec on these results: a "
            "question whose verdict is `fails` does not hold, so drop its `U<n>` and every "
            "requirement that rested on it. A new question takes a new `U<n>`.\n\n"
            f"{spike}"
        )
    if stage == "spike" and spike and "spike.md" not in included:
        included.append("spike.md")
        blocks.append(f"# The previous spike\n\n{spike}")
    return blocks


def _where_you_work(
    stage: str,
    workspace: str | Path,
    worktree: str,
    ceilings: tuple[int, float] | None,
) -> list[str]:
    """`spike` only: its scratch directory, the worktree it may read, its progress file."""
    if stage != "spike":
        return []
    scratch = Path(workspace).expanduser().resolve()
    # `ceilings` is the grant `Runner.run` holds; `None` leaves the sentence out.
    within = (
        f"This step has {ceilings[0]} turns and ${ceilings[1]:.2f}. "
        if ceilings is not None
        else ""
    )
    return [
        "# Where you work\n\n"
        f"Your working directory is `{scratch}`, a "
        "throwaway directory the app deletes when this step ends. Write probe code "
        "there and nowhere else.\n\n"
        + (
            f"The unit's worktree is `{worktree}`. Read it; never write to it. The app "
            "records its `HEAD` and `git status --porcelain` before this step and again "
            "after, and fails the step, writing no `spike.md`, if either changed."
            if worktree
            else "No worktree was named for this step."
        )
        + "\n\n"
        f"Your progress file is `{scratch / PROGRESS_FILE}` (the rules' *The progress "
        f"file*). {within}If this step ends without a usable final reply — a turn or "
        "budget ceiling, a reply with no `Status:` line, a session that broke — the app "
        "writes `spike.md` from this file."
    ]


def _tools(
    stage: str,
    directory: Path,
    plan_map: str,
    commands: tuple[str, ...],
    runs_commands: bool,
    state_file: str | Path | None,
    included: list[str],
) -> list[str]:
    """What `impl` may read and run, and what a step holding `Bash` is told about its session."""
    blocks: list[str] = []
    # Placed only: the plan map and the grant's words are built elsewhere.
    if plan_map and stage == "impl":
        included.append("plan-map")
        blocks.append(f"{PLAN_MAP_HEADING}\n\n{plan_map}\n\n{PLAN_MAP_ADVICE}")
    if commands and stage == "impl":
        included.append("commands")
        words = ", ".join(f"`{c}`" for c in commands)
        blocks.append(f"{COMMANDS_HEADING}\n\n{words}\n\n{COMMANDS_ADVICE}")
    if runs_commands:
        blocks.append(f"{SESSION_ENDS_HEADING}\n\n{SESSION_ENDS_ADVICE}")
        blocks.append(f"{HARNESS_HEADING}\n\n{harness_advice(directory, state_file)}")
    return blocks


_FAST_IMPL = (
    "# The fast lane\n\n"
    "This is a `Type: fix` with no spec or plan; `intent.md` is the plan and the open gate is "
    "what lets you code. In this order:\n\n"
    "1. Check that the file `intent.md ## Expected` names under `Source:` says what "
    "`## Expected` says.\n"
    "2. Commit a test that reproduces the bug, and nothing else. Run it and keep its failing "
    "output.\n"
    "3. Commit the fix. Run the same test and keep its passing output.\n"
    "4. `## What was measured` gives both shas, the test command, and both outputs. The header "
    "names no `Plan:`."
)
_FAST_REVIEW = (
    "# The fast lane\n\n"
    "This is a `Type: fix` with no spec or plan. Check three things; a missing one is a "
    "finding of `medium` or more:\n\n"
    "- the commit holding only the reproducing test comes before the fix;\n"
    "- `impl.md` shows that test failing at the first commit and passing at the fix;\n"
    "- the file `Source:` names says what `intent.md ## Expected` says."
)
_LANE_BLOCKS = {"impl": _FAST_IMPL, "review": _FAST_REVIEW}


def _lane(stage: str, unit_meta: dict[str, Any] | None) -> list[str]:
    """What only a unit in the fast lane is told."""
    if (unit_meta or {}).get("lane") == "fast" and stage in _LANE_BLOCKS:
        return [_LANE_BLOCKS[stage]]
    return []


def _answers(
    stage: str,
    directory: Path,
    artifact: str,
    writes_own: bool,
    unit_meta: dict[str, Any] | None,
) -> list[str]:
    """The answers a person gave to the artifact this stage writes again."""
    blocks: list[str] = []
    # A prose stage re-run must see a person's decision; `review` gets its own block later.
    if is_prose_stage(stage) and not writes_own and stage != "review":
        block = _answers_block(
            directory, artifact, repeat_content=stage != "intent", meta=unit_meta
        )
        if block:
            blocks.append(block)
    # A draft `impl.md` that asked a person gets the answers back, untouchable.
    if stage == "impl":
        block = _impl_answers_block(directory, unit_meta)
        if block:
            blocks.append(block)
    return blocks


def _sent_back(stage: str, directory: Path, review: str, included: list[str]) -> list[str]:
    """`impl` only: the findings the last review left open.

    A review that asked for changes sends the unit back to `impl`, and the point of going back
    is the findings; without this block the step is built from `intent.md` and `plan.md` only
    and cannot see one.

    Only while the review is `changes-requested`: a pass has nothing to act on, and a reject
    closed the unit. It carries the header and the findings still open in the last round; the
    whole file is named by path in *The unit's files*.
    """
    if stage != "impl":
        return []
    if not review or _header_status(review) != "changes-requested" or "review-findings" in included:
        return []
    header, number, findings = open_findings(review)
    included.append("review-findings")
    return [
        "# The review that sent this back\n\n"
        "The last review asked for changes. Fix every finding marked `[open]` below "
        "on the branch, one commit per finding where that is possible, then push "
        "the branch, then record in impl.md which commit fixed which finding. The "
        "next review is offered only once a fix is on the pull request, so a fix "
        "left unpushed keeps this unit on impl.\n\n"
        "Below are the review's header line and the findings its last round"
        + (f" (Round {number})" if number is not None else "")
        + f" left open. The whole review, every round, is `{directory.resolve() / 'review.md'}`.\n\n"
        f"{header}\n\n{findings or '(no finding is left open)'}"
    ]


def _rounds_so_far(stage: str, directory: Path, review: str, included: list[str]) -> list[str]:
    """`review` only: the last round's number and what it left open.

    `review.md` accumulates rounds and the app writes it from the reply, so writing must not
    erase the rounds already there (`merge_review`), or a loop whose counter resets every run
    never reaches its limit. The prompt carries the last round's number and what it left open;
    `impl.md`, `pr.md` and the earlier rounds are named by path in *The unit's files*.
    """
    if stage != "review" or not _rounds(review):
        return []
    _, number, findings = open_findings(review)
    included.append("review-findings")
    return [
        "# The rounds so far\n\n"
        f"`review.md` already holds rounds up to Round {number}, and the app keeps "
        "them. Do not copy them into your reply. Reply with the title, the header "
        "line and the next `## Round N` section only; the app writes the earlier "
        "rounds back under your header, unchanged, and appends yours after them.\n\n"
        f"The findings Round {number} left open are below. Every earlier round is "
        f"in `{directory.resolve() / 'review.md'}`.\n\n"
        f"{findings or '(no finding is left open)'}"
    ]


def _unfinished_round(
    stage: str, unfinished_round: dict[str, Any] | None, included: list[str]
) -> list[str]:
    """`review` only: why it runs again after a round `cos.mjs` did not count.

    The last round asked for changes but dropped ids an earlier round raised, so `cos.mjs` does
    not count it and sent the unit here again. The block above may say nothing is left open;
    this says why the review runs anyway. The ids are `cos.mjs`'s, carried by
    `service.steps.run_step`.
    """
    if stage != "review" or not unfinished_round:
        return []
    number = int(unfinished_round["n"])
    dropped = ", ".join(f"`{i}`" for i in unfinished_round.get("dropped") or [])
    included.append("review-unfinished")
    return [
        "# The round that did not count\n\n"
        f"Round {number} asked for changes but does not list {dropped}, which an earlier "
        "round raised, so `cos.mjs` does not count it against `COS_REVIEW_ROUNDS` and "
        f"this review runs again. Write Round {number + 1} as a full round for the commit "
        "named below. List every finding of every earlier round with its label, "
        f"{dropped} among them."
    ]


def _incomplete_round(stage: str, review: str, included: list[str]) -> list[str]:
    """`review` only: where to go on from when the last round is one the app's closing turn
    wrote for a review that ran out of turns. The next review goes on from it: its three
    sections verbatim, and what the last full round before it left open.
    """
    if stage != "review":
        return []
    found = list(_ROUND_RE.finditer(review))
    last = found[-1].group(0).rstrip() if found else ""
    meta = _round_meta(last) if last else None
    if meta is None or meta[1] != "incomplete":
        return []
    number = _round_number(last)
    start = last.find(INCOMPLETE_SECTIONS[0])
    sections = last[start:] if start != -1 else last
    before = review[: found[-1].start()]
    carried = ""
    if any((_round_meta(r) or ("", ""))[1] != "incomplete" for r in _rounds(before)):
        _, full, left = open_findings(before)
        carried = (
            f"\n\nThe findings Round {full}, the last full round, left open:\n\n"
            f"{left or '(no finding is left open)'}"
        )
    included.append("review-incomplete")
    return [
        "# The incomplete round\n\n"
        f"Round {number} is incomplete: the review before this one ran out of turns, "
        "and the app asked it, with no tools left, to write down where it stood. Go "
        "on from it. Read what it lists under *What was not reviewed* first, then "
        f"write Round {number + 1} as a full round for the commit named below. Carry "
        f"forward every finding of Round {number} and of every earlier round, with "
        "its id: the `ship` gate stays closed on a round that drops one. Never write "
        "`Verdict: incomplete` yourself; only the app's closing turn writes it.\n\n"
        f"Round {number}, from its first section on, verbatim:\n\n{sections}"
        f"{carried}"
    ]


def _review_answers(
    stage: str,
    directory: Path,
    artifact: str,
    writes_own: bool,
    unit_meta: dict[str, Any] | None,
) -> list[str]:
    """`review`'s own copy of the answers block, placed after *The rounds so far*: a person can
    answer under `## Answers` while a review is at `changes-requested`, and the next review
    must not treat that decision as an unread finding.
    """
    if stage != "review" or not is_prose_stage(stage) or writes_own:
        return []
    block = _answers_block(directory, artifact, repeat_content=True, meta=unit_meta)
    return [block] if block else []


def _commit_reviewed(stage: str, head: str) -> list[str]:
    """`review` only: the commit it reviews.

    `write-review` needs the commit it reviewed, and the `ship` gate reads that line. A step
    runs in the unit's worktree, whose `.git` is a file pointing into the main repository, and
    a `Read` there is refused as outside both the worktree and the unit. The app has already
    read the head for the run log, so it hands the same value over rather than widen the boundary.
    """
    if stage != "review":
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
    """What `service.steps.run_step` built for this step; this only places it."""
    blocks: list[str] = []
    # Built by `service.steps.run_step`; placed only.
    if last_attempt:
        included.append("last-attempt")
        blocks.append(f"# The attempt before this one\n\n{last_attempt}")

    # Only `review`, after an integration; built by `integrate.describe_for_review`.
    if integration_note and stage == "review":
        included.append("integration")
        blocks.append(integration_note.rstrip())

    # Only `review`, after a retake; built by `retake.describe_for_review`.
    if screens_note and stage == "review":
        included.append("screens")
        blocks.append(screens_note.rstrip())
    return blocks


def _unit_files(
    directory: Path, stage: str, stages: list[str], included: list[str]
) -> tuple[list[str], list[str]]:
    """Every artifact of the unit that exists, by absolute path, so what the prompt no longer
    carries can still be found; `pointed` is what is here and not above.
    """
    pointed: list[str] = []
    if stage not in _POINTING:
        return [], pointed
    lines = []
    for s in stages:
        name = f"{s}.md"
        if not (directory / name).is_file():
            continue
        # The plan is enough to implement from; what it cites, it cites by section.
        if stage == "impl" and s in ("intent", "spec", "spike"):
            continue
        above = name in included
        if not above:
            pointed.append(name)
        lines.append(f"- {directory.resolve() / name}" + (" (above)" if above else ""))
    if not pointed:
        return [], pointed
    return ["# The unit's files\n\n" + "\n".join(lines) + "\n\n" + UNIT_FILES_ADVICE], pointed


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
            "That file must carry the `Status:` line the rules above describe. "
            f"{_LANGUAGE} Write it yourself with your tools — do not paste it into your reply."
        )
    return (
        f"# Your task\n\n"
        f"Write `{artifact}` for the work unit `{unit}`.\n\n"
        "Reply with the file's complete contents and nothing else — no preamble, no "
        "code fence, no commentary. The first lines must carry the `Status:` line the "
        f"rules above describe. {_LANGUAGE}"
    )


def compose_prompt(
    workspace: str | Path,
    directory: str | Path,
    unit: str,
    stage: str,
    stages: list[str],
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
    ceilings: tuple[int, float] | None = None,
    rerun: bool = False,
    rerun_note: str = "",
    plan_map: str = "",
    commands: tuple[str, ...] = (),
    unfinished_round: dict[str, Any] | None = None,
    idea_note: str = "",
    siblings_note: str = "",
    runs_commands: bool = False,
    agent: dict[str, Any] | None = None,
    unit_meta: dict[str, Any] | None = None,
    state_file: str | Path | None = None,
    blocks: tuple[tuple[str, str], ...] = (),
) -> tuple[str, list[str], list[str]]:
    """The prompt for one step, the artifacts that went into it whole, and the ones it names by
    path only.

    Every optional section below adds not one byte when empty or `None`, or for a stage it is
    not placed for.

    `agent` is the stage's resolved row of the agent table; its section opens the prompt.
    `idea_note` is the shared idea a unit was opened from (`intent` only); `siblings_note` the
    sibling checkouts `impl` may read (`impl` only). `rerun` is true only for a stage a person
    ran again from the board, with `rerun_note` their note. `plan_map` is what `planmap.for_step` built and
    `commands` the words of the step's grant (`impl` only). `unfinished_round` is
    `{"n", "dropped"}` of a last round `cos.mjs` read as unfinished (`review` only).
    `runs_commands` is true when the step's grant holds `Bash`. `unit_meta` is the unit's entry
    in the snapshot: its answers and holds, rendered where the file's `## Answers` blocks were.

    The list of included artifacts is returned rather than inferred later: if a step ran
    without the previous stage's artifact in the prompt, the record says so.

    `blocks` are the named texts features add, in order, before the rerun block and the task; an
    empty text adds nothing.

    `directory` is handed in: the artifacts live in the product's own store while `workspace`
    stays the repository the work is done in.
    """
    directory = Path(directory)
    included: list[str] = []
    parts = _opening(stage, agent)
    # Nothing writes `review.md` while the prompt is composed.
    review = _read(directory / "review.md") if stage in ("impl", "review") else ""
    # The order of these calls is the order of the prompt.
    parts += [
        *_already_asked(gate_said, base_note, drift_note),
        *_what_it_follows(directory, stage, stages, unit_meta, included),
        *_shared(stage, idea_note, siblings_note),
        *_second_artifact(directory, stage, unit_meta, included),
        *_where_you_work(stage, workspace, worktree, ceilings),
        *_tools(stage, directory, plan_map, commands, runs_commands, state_file, included),
        *_lane(stage, unit_meta),
        *_answers(stage, directory, artifact, writes_own, unit_meta),
        *_sent_back(stage, directory, review, included),
        *_rounds_so_far(stage, directory, review, included),
        *_unfinished_round(stage, unfinished_round, included),
        *_incomplete_round(stage, review, included),
        *_review_answers(stage, directory, artifact, writes_own, unit_meta),
        *_commit_reviewed(stage, head),
        *_handed(stage, last_attempt, integration_note, screens_note, included),
    ]
    files, pointed = _unit_files(directory, stage, stages, included)
    parts += files
    parts += [text for _name, text in blocks if text]
    # Just before the task, so the note is the last thing read before it.
    if rerun:
        parts.append(_rerun_block(directory, stage, artifact, rerun_note))
    parts.append(_task(workspace, directory, unit, artifact, writes_own))
    block = submit_block(stage, artifact, writes_own)
    if block:
        parts.append(block)
    return "\n\n---\n\n".join(parts), included, pointed


def submit_block(stage: str, artifact: str, writes_own: bool) -> str:
    """How a stage hands back its judgement: the object, never the `Status:` line, is what the app
    reads. `""` for a stage that hands back no stage result. English: an instruction to the
    model.
    """
    if stage == submit.ROUND:
        return round_block()
    if stage not in submit.STAGE_RESULT:
        return ""
    fields = [
        f"- `stage`: `{stage}`.",
        "- `judgement`: `ready` when the file is finished (its header says `Status: accepted`), "
        "`not-ready` when it is not (`Status: draft`).",
        "- `questions`: every item under `## Open questions` still waiting on a person, as "
        "`{n, text}` with the number the file gives it; `[]` when there is none.",
    ]
    if stage == "spec":
        fields.append(
            "- `unmeasured`: every `U<n>` id a `## Concerns` item opens with `[unmeasured]`; `[]` for none."
        )
    if stage == "spike":
        fields.append(
            "- `verdicts`: one `{id, verdict}` per `## U<n>` section, `verdict` being `holds` or `fails`."
        )
    if stage == "impl":
        fields.append(
            "- `needs_person`: the `F<k>` of every open finding of the last review round that this "
            "stage cannot close and lists under `## Needs a person`; `[]` for none. Only an open "
            "finding of the last round may be named."
        )
    when = (
        f"Call it once `{artifact}` is written and final. Writing the file again after that "
        "makes the object stale, and the app refuses it."
        if writes_own
        else f"Call it before you reply; once it answers, reply with `{artifact}` and nothing after it."
    )
    return (
        "# Hand back your judgement\n\n"
        f"The app does not read `Status:` or `## Open questions` out of `{artifact}`: it takes "
        f"them from the object you hand it through the `submit` tool (`{submit.NAME}`). "
        f"{when} The object:\n\n" + "\n".join(fields) + "\n\n"
        "If `submit` returns an error, the app has checked your object against the unit: "
        f"{submit.AGAIN} Keep calling it until it is accepted. A step that hands back no object "
        "ends failed, whatever its file says."
    )


def round_block() -> str:
    """How a review hands back its round. English: an instruction to the model."""
    return (
        "# Hand back your round\n\n"
        "The app does not read the verdict, the findings or the screenshots out of `review.md`: "
        f"it takes them from the object you hand it through the `submit` tool (`{submit.NAME}`), "
        "and writes the round's `Reviewed:` line, its `### Findings` and its `### Screens` from "
        "it, with the head this step ran on and the round's number. Call it before you reply; "
        "once it answers, reply with `review.md` and nothing after it. The object:\n\n"
        "- `verdict`: `pass`, `changes-requested` or `needs-person`.\n"
        "- `findings`: every finding of this round, those an earlier round raised carried forward "
        "with their id, as `{id, state, fixed_in, severity, rule, path, lines, text}`. `state` is "
        "`open`, `fixed`, `needs-person`, `claim-rejected` or `answered`; `fixed_in` is the commit "
        'of a `fixed` one and `""` otherwise; `rule` is the `S<n>` of the UI standard it names, '
        'or `""`; `text` is what the finding says, without its id, label, place or severity.\n'
        "- `screens`: one `{path, size, address, result}` per screenshot you opened, `size` as "
        "`1440x900`; `[]` when you opened none.\n\n"
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
        if stage == submit.ROUND
        else ("judgement", "Hand back your judgement")
    )
    return (
        f"Your step ended without handing back its object: {why}\n\n"
        f"Call the `submit` tool now with your {what} of `{artifact}` as it stands, as "
        f"*{section}* above sets out. Do not change any file and do not reply "
        f"with the artifact again. {submit.AGAIN}"
    )


# A spike's progress file, read when its reply is not an artifact.
PROGRESS_FILE = "spike.md"
