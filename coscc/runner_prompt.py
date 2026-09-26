"""What a step is told: the stage's rules, the artifacts it reads, and the sections the
app adds to them. Split from `coscc/runner.py` (`0095`), which re-exports every name.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from coscc import harness
from coscc.knowledge import STAGES as KNOWLEDGE_STAGES
from coscc.policy import is_prose_stage
from coscc.runner_review import (
    INCOMPLETE_SECTIONS,
    _ROUND_RE,
    _header_status,
    _round_meta,
    _round_number,
    _rounds,
    open_findings,
)
from coscc.runner_reply import RunError


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


# The Answers section of an artifact, exactly as `coscc/service.py:900` and
# `.claude/scripts/cos.mjs:194` read it: the byte range from the start of the first line
# that is `## Answers` -- recognised with its own trailing whitespace stripped away -- to
# the end of the file. `None` when no such line exists.
#
# Bytes in, bytes out, on purpose (`0025` `spec.md` R1, `plan.md` Risk 3). `_read` above
# decodes with `errors="replace"` and `Path.read_text` translates `\r\n` to `\n`; either
# can move a byte, and R1 asks that none does. Only `POST /api/units/answer` ever writes
# into this section (`.claude/CLAUDE.md`, *A unit of work*), and only by appending to it.
def answers_section(raw: bytes) -> bytes | None:
    idx = 0
    while True:
        nl = raw.find(b"\n", idx)
        line = raw[idx: nl if nl != -1 else len(raw)]
        if line.rstrip(b" \t\r") == b"## Answers":
            return raw[idx:]
        if nl == -1:
            return None
        idx = nl + 1


def strip_answers(body: str) -> str:
    """A reply, with everything from its own first `## Answers` line onward dropped.

    `spec.md` R3: whatever a reply says under a heading of that name -- copied from the
    artifact, forged, or simply a model answering its own question -- carries no
    authority. The only section that reaches disk under `## Answers` is the one already
    there, found by `answers_section` above; this is what keeps a reply's own attempt at
    one from ever being mistaken for it. A reply with no such line is returned unchanged.
    """
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() == "## Answers":
            return "\n".join(lines[:i]).rstrip() + "\n"
    return body


def with_answers(body: str, section: bytes | None) -> bytes:
    """The bytes to write: the stage's own text, and the Answers section, unmoved.

    `section` is `None` when the artifact never had one, or this is the first time it is
    written -- and then this returns exactly the bytes written before `0025`: `body`,
    UTF-8 encoded, nothing else. Otherwise `body` is followed by one blank line and the
    section already on disk, byte for byte.
    """
    if section is None:
        return body.encode("utf-8")
    return body.rstrip("\n").encode("utf-8") + b"\n\n" + section


def _open_questions(text: str) -> str:
    """The `## Open questions` section of an artifact, verbatim: from that heading to the
    next `## ` heading or the end of the file. Mirrors `.claude/scripts/cos.mjs`'s own
    `section()` (`:73-79`), so a question's number here means what it means there -- the
    numbers are what `### Câu N` in the Answers section refers back to.
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.rstrip() == "## Open questions"), None)
    if start is None:
        return "It carries no `## Open questions` section."
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).rstrip()


# `spec.md` R7's three instructions, the same words wherever the block appears so a test
# can check for one fixed string rather than three.
_ANSWERS_ADVICE = (
    "This is a person's decision, already made. Cite it as `<artifact> ## Answers, câu N` "
    "rather than reporting it back as if you had found it yourself. Do not copy this "
    "section into your reply — the app writes it back onto the artifact after your reply, "
    "on its own. If you still mention a question already answered here, keep its number."
)


def _answers_block(directory: Path, artifact: str, repeat_content: bool) -> str | None:
    """`spec.md` R7: what a re-run is told about the answers its own artifact already
    carries. `None` when the artifact has never been written, or was written with no
    `## Answers` section -- a first run has nothing of a person's to protect.

    `repeat_content` is false only for `intent`, whose file is already in the prompt in
    full (`build_prompt`, *The intent this work is authorised by*, just above): repeating
    it here would put the same text in the prompt twice for no reason.
    """
    try:
        raw = (directory / artifact).read_bytes()
    except OSError:
        return None
    section = answers_section(raw)
    if section is None:
        return None
    if not repeat_content:
        return f"# The answers already given to this artifact\n\n{_ANSWERS_ADVICE}"
    text = raw.decode("utf-8", errors="replace")
    return (
        "# The answers already given to this artifact\n\n"
        f"{_open_questions(text)}\n\n"
        f"{section.decode('utf-8', errors='replace')}\n\n"
        f"{_ANSWERS_ADVICE}"
    )


# `0044` R15. The header `Service.precedent` writes (`Answered by: Jera. … Via: precedent.`).
_JERA_META = re.compile(r"^Answered by:\s*Jera\.", re.IGNORECASE)
_BLOCK_HEAD = re.compile(r"^###\s+(.+?)\s*$")

JERA_ADVICE = (
    "Those blocks were written by Jera, an agent that infers an answer from precedent — "
    "decisions already recorded in this project — not by the person who started this work. "
    "Cite each as Jera's inference, never as that person's decision. A later block for the "
    "same question from a person replaces it."
)

# `0090` R3, spec Design 1. The same words after every section of the store, so a test can
# look for one fixed string.
KNOWLEDGE_ADVICE = (
    "Each entry above is what an earlier unit measured, with its source and the date it was "
    "measured: a measurement, not a guarantee. When you rely on one, cite it as "
    "`knowledge K<n>` instead of measuring it again. When the work in front of you "
    "contradicts an entry, say so under `## Concerns`, or in the spike's verdict, rather "
    "than quietly picking one side."
)

# `0110` R6. The heading and the same words after every section `priorfindings` built, for
# `impl` only; `write-impl` carries the same advice (R8).
PRIOR_FINDINGS_HEADING = "# What earlier reviews said about these files"
PRIOR_FINDINGS_ADVICE = (
    "Each line above is a finding an earlier unit's review raised on a file this plan's "
    "`## Files that change` names, prefixed with that unit and round. Before you push the "
    "branch, check whether your change repeats any of them. They are not requirements and do "
    "not change the plan."
)

# `0096` R9, R10. Two headings, and the same words after each, for `impl` only; `write-impl`
# describes both. The commands advice is prose about `policy.check_command`, and
# `runner_test.TheCommandsAStepMayRun` asks that function each thing it says is refused.
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


def _jera_answers(directory: Path, names: list[str]) -> str:
    """`# Answers an agent gave`, listing every `<artifact> ### Câu N` block Jera wrote in
    `names`; `""` when there is none."""
    found: list[str] = []
    for name in names:
        try:
            section = answers_section((directory / name).read_bytes())
        except OSError:
            continue
        if section is None:
            continue
        head = ""
        for line in section.decode("utf-8", errors="replace").splitlines():
            m = _BLOCK_HEAD.match(line)
            if m:
                head = m.group(1)
            elif head and _JERA_META.match(line.strip()):
                found.append(f"- {name} ### {head}")
                head = ""
    if not found:
        return ""
    return "# Answers an agent gave\n\n" + "\n".join(found) + "\n\n" + JERA_ADVICE


# `0094` R14. The stages whose prompt names the unit's artifacts by path instead of carrying
# them, and the one artifact each still carries whole. Every one of them may `Read` the
# unit's folder (`Runner.run` hands it to `decide` as `unit_dir`), which is R15's condition;
# `coscc/runner_prompt_test.py` `EveryPathAPromptNamesCanBeRead` fails the day one cannot.
_EMBED: dict[str, tuple[str, ...]] = {
    "impl": ("plan.md",),
    "implement": ("plan.md",),
    "pr": (),
    "ship": ("pr.md",),
    "review": (),
}
_POINTING = frozenset(_EMBED)

UNIT_FILES_ADVICE = (
    "The files not embedded above are not in this prompt; Read one when your rules or your "
    "task need it."
)


def _rerun_block(directory: Path, stage: str, artifact: str, note: str) -> str:
    """`0054` R7. What a stage run again from the board is told about why. For `spec` and
    `plan` it carries the artifact as it stands, above its `## Answers`: no other part of the
    prompt does. `intent`'s own file is already above, and `spike` and `pr` are handed or
    named theirs, so theirs is not repeated."""
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
    """`compose_prompt` without `pointed`, for every caller written before `0094`."""
    prompt, included, _ = compose_prompt(*args, **kwargs)
    return prompt, included


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
    pr_note: str = "",
    ceilings: tuple[int, float] | None = None,
    knowledge: str = "",
    rerun: bool = False,
    rerun_note: str = "",
    prior_findings: str = "",
    plan_map: str = "",
    commands: tuple[str, ...] = (),
) -> tuple[str, list[str], list[str]]:
    """The prompt for one step, the artifacts that went into it whole (`spec.md` R4), and
    the ones it names by path only (`0094` R16).

    `rerun` (`0054` R7) is true only for a stage a person ran again from the board, with
    `rerun_note` their note; false adds not one byte.

    `prior_findings` (`0110` R6) is what `priorfindings.for_step` built, placed for `impl`
    only; `""`, or any other stage, adds not one byte (R9).

    `plan_map` (`0096` R9) is what `planmap.for_step` built and `commands` (R10) the words
    of the step's grant, both placed for `impl` only; empty, or any other stage, adds not
    one byte (R11).

    The list is returned rather than inferred later because R4 is checked against it: if a
    step ran without the previous stage's artifact in the prompt, the record says so.

    `directory` is handed in rather than worked out here. `coscc/units.py` is the one place
    that answers it: the artifacts live in the product's own store while `workspace` stays
    the repository the work is done in (`0014` `spec.md` R2).
    """
    directory = Path(directory)
    included: list[str] = []
    parts: list[str] = []
    pointing = stage in _POINTING

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

    # `0030_a-unit-branch-starts-from-a-stale-main` R1, câu 2. The app refreshes a step's
    # detached tree from `origin/main` before running it, but a refresh can itself fail —
    # the remote unreachable, the tree dirty, a commit on it not an ancestor of the fetched
    # tip — and the step still runs. `service.describe_base` is the one sentence saying so;
    # this hands it to the step rather than leaving it to guess from a `git log` it may
    # have no tool to run.
    if base_note:
        parts.append(f"# The base this step runs on\n\n{base_note}")

    # `0042`. Which files the plan names that `main` changed since the plan ran, or why
    # that could not be checked. `drift.describe` built it; `""` adds nothing at all, so a
    # plan nobody overtook reaches `impl` byte for byte as it did before.
    if drift_note:
        parts.append(f"# The files main changed since the plan\n\n{drift_note}")

    intent = "" if pointing else _read(directory / "intent.md")
    if intent:
        included.append("intent.md")
        parts.append(f"# The intent this work is authorised by\n\n{intent}")

    # The stage immediately before this one, whatever it is. Taken from the stage list the
    # board was read with, so the order is not restated here. `0094` R14: a pointing stage
    # carries only what `_EMBED` names, under the same heading.
    position = stages.index(stage) if stage in stages else -1
    for earlier in reversed(stages[:position]):
        name = f"{earlier}.md"
        if pointing and name not in _EMBED[stage]:
            continue
        text = _read(directory / name)
        if text and name not in included:
            included.append(name)
            parts.append(f"# The {earlier} it follows\n\n{text}")
            break

    # `0039` R14. The loop above takes one artifact, and two stages need a second. `plan`
    # follows `spike` when one ran, but the requirements it orders are still in `spec.md`.
    # A `spec` re-run after a spike found a question that does not hold must be rewritten on
    # that measurement. A `spike` re-run needs the one before it to set `Round:`.
    spike = _read(directory / "spike.md")
    if stage == "plan" and "spike.md" in included and "spec.md" not in included:
        spec = _read(directory / "spec.md")
        if spec:
            included.append("spec.md")
            parts.append(f"# The spec the spike measured\n\n{spec}")
    if stage == "spec" and spike and "spike.md" not in included:
        included.append("spike.md")
        parts.append(
            "# What the spike measured\n\n"
            "A spike ran on the spec before this one. Rewrite the spec on these results: a "
            "question whose verdict is `fails` does not hold, so drop its `U<n>` and every "
            "requirement that rested on it. A new question takes a new `U<n>`.\n\n"
            f"{spike}"
        )
    if stage == "spike" and spike and "spike.md" not in included:
        included.append("spike.md")
        parts.append(f"# The previous spike\n\n{spike}")
    if stage == "spike":
        scratch = Path(workspace).expanduser().resolve()
        # `0080` R2. `ceilings` is the grant `Runner.run` holds, so no second number is
        # written by hand; `None` (a caller that has no grant) leaves the sentence out.
        within = (
            f"This step has {ceilings[0]} turns and ${ceilings[1]:.2f}. "
            if ceilings is not None
            else ""
        )
        parts.append(
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
        )

    # `0090` R3. The slice of the store `service.run_step` read for this workspace, already
    # capped (`knowledge.slice_for`); this only places it. `""` -- the flag off, or nothing
    # applies -- adds not one byte (R2).
    if knowledge and stage in KNOWLEDGE_STAGES:
        included.append("knowledge")
        parts.append(f"# What earlier units measured\n\n{knowledge}\n\n{KNOWLEDGE_ADVICE}")

    # `0096` R9, R10. The files the plan changes as they stand, already capped
    # (`planmap.select`), and the first words the grant allows, taken from it rather than
    # written here; this only places them.
    if plan_map and stage in ("impl", "implement"):
        included.append("plan-map")
        parts.append(f"{PLAN_MAP_HEADING}\n\n{plan_map}\n\n{PLAN_MAP_ADVICE}")
    if commands and stage in ("impl", "implement"):
        included.append("commands")
        words = ", ".join(f"`{c}`" for c in commands)
        parts.append(f"{COMMANDS_HEADING}\n\n{words}\n\n{COMMANDS_ADVICE}")

    # `0110` R6. The finding lines earlier reviews raised on the files this plan changes,
    # already chosen and capped (`priorfindings.select`); this only places it, before the
    # review that sent this unit back.
    if prior_findings and stage in ("impl", "implement"):
        included.append("prior-findings")
        parts.append(f"{PRIOR_FINDINGS_HEADING}\n\n{prior_findings}\n\n{PRIOR_FINDINGS_ADVICE}")

    # `spec.md` R7. A prose stage re-run against an artifact that already carries
    # `## Answers` is one of `intent.md ## Affected users and systems`' "later stages" too:
    # without this, it cannot see a person's decision and may ask the same question again.
    # `review` gets the same block, but placed after *The rounds so far* below instead --
    # `stage != "review"` here keeps it from also landing in this earlier position.
    own_answers = False
    if is_prose_stage(stage) and not writes_own and stage != "review":
        block = _answers_block(directory, artifact, repeat_content=stage != "intent")
        if block:
            own_answers = True
            parts.append(block)

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
    #
    # `0094` R14: the header and the findings still open in the last round, not every round
    # the file holds; the whole of it is named by path in *The unit's files*.
    review_path = directory.resolve() / "review.md"
    if stage in ("impl", "implement"):
        review = _read(directory / "review.md")
        if review and _header_status(review) == "changes-requested" and "review-findings" not in included:
            header, number, findings = open_findings(review)
            included.append("review-findings")
            parts.append(
                "# The review that sent this back\n\n"
                "The last review asked for changes. Fix every finding marked `[open]` below "
                "on the branch, one commit per finding where that is possible, then push "
                "the branch, then record in impl.md which commit fixed which finding. The "
                "next review is offered only once a fix is on the pull request, so a fix "
                "left unpushed keeps this unit on impl.\n\n"
                "Below are the review's header line and the findings its last round"
                + (f" (Round {number})" if number is not None else "")
                + f" left open. The whole review, every round, is `{review_path}`.\n\n"
                f"{header}\n\n{findings or '(no finding is left open)'}"
            )

    # `review.md` accumulates rounds, and the app writes it from the reply -- so writing
    # it must not erase the rounds already there (`merge_review`). A loop whose counter
    # resets every run never reaches its limit. `0094` R14: the prompt carries the last
    # round's number and what it left open; `impl.md`, `pr.md` and the earlier rounds are
    # named by path in *The unit's files*, and the review reads what it needs of them.
    if stage == "review":
        review = _read(directory / "review.md")
        if _rounds(review):
            _, number, findings = open_findings(review)
            included.append("review-findings")
            parts.append(
                "# The rounds so far\n\n"
                f"`review.md` already holds rounds up to Round {number}, and the app keeps "
                "them. Do not copy them into your reply. Reply with the title, the header "
                "line and the next `## Round N` section only; the app writes the earlier "
                "rounds back under your header, unchanged, and appends yours after them.\n\n"
                f"The findings Round {number} left open are below. Every earlier round is "
                f"in `{review_path}`.\n\n"
                f"{findings or '(no finding is left open)'}"
            )

    # `0085` R10. The last round is one the app's closing turn wrote for a review that ran
    # out of turns. The next review goes on from it rather than starting again: its three
    # sections verbatim, and what the last full round before it left open.
    if stage == "review":
        review = _read(directory / "review.md")
        found = list(_ROUND_RE.finditer(review or ""))
        last = found[-1].group(0).rstrip() if found else ""
        meta = _round_meta(last) if last else None
        if meta is not None and meta[1] == "incomplete":
            number = _round_number(last)
            start = last.find(INCOMPLETE_SECTIONS[0])
            sections = last[start:] if start != -1 else last
            earlier = [r for r in _rounds(review[: found[-1].start()])
                       if (_round_meta(r) or ("", ""))[1] != "incomplete"]
            carried = ""
            if earlier:
                _, full, left = open_findings(review[: found[-1].start()])
                carried = (
                    f"\n\nThe findings Round {full}, the last full round, left open:\n\n"
                    f"{left or '(no finding is left open)'}"
                )
            included.append("review-incomplete")
            parts.append(
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
            )

    # `spec.md` R7, `review`'s own copy. Placed here rather than with the other stages'
    # above so it reads after *The rounds so far*, which it is about: a person can answer
    # under `## Answers` while a review is at `changes-requested`, and the next review
    # must not treat that decision as an unread finding.
    if stage == "review" and is_prose_stage(stage) and not writes_own:
        block = _answers_block(directory, artifact, repeat_content=True)
        if block:
            own_answers = True
            parts.append(block)

    # `write-review` step 2 needs the commit it reviewed, and the `ship` gate reads that
    # line. Until `0020` the stage read it out of `.git/` itself. Since `0017` a step runs
    # in the unit's worktree, whose `.git` is a file pointing into the main repository's
    # `.git/worktrees/`, and since `0020` a `Read` there is refused: it lies outside both
    # the worktree and the unit (`0020` review round 1, F1). The app has already read the
    # head for the run log, so it hands the same value over rather than widen the boundary.
    if stage == "review" and head:
        parts.append(
            "# The commit you are reviewing\n\n"
            "The app read the head of this checkout before starting the step. Record it "
            "on the round's `Reviewed:` line:\n\n"
            f"    {head}\n\n"
            "It is the same value the run log's `start` record carries. Do not read it out "
            "of `.git/`: the checkout is a git worktree whose git directory lies outside "
            "what this step may read."
        )
    elif stage == "review":
        parts.append(
            "# The commit you are reviewing\n\n"
            "The app could not read the head of this checkout: it is not a git checkout, "
            "or `git rev-parse HEAD` failed. Do not guess one. Leave the round's "
            "`Reviewed:` line without a commit and say why under "
            "`### What was not reviewed`; the `ship` gate will stay closed, which is right."
        )

    # `0019` plan step 5 / `spec.md` R6. Only when the last run of this unit and stage did
    # not end `done` — `service.run_step` is the one place that decides that and builds
    # this string (`journal.failed_attempts` + `describe_attempt`); this function only
    # places it, the same way it places `base_note`.
    if last_attempt:
        included.append("last-attempt")
        parts.append(f"# The attempt before this one\n\n{last_attempt}")

    # `0035` R10. Only for `review`, and only when `service.run_step` found an integration
    # recorded after the last review round; it is built by `integrate.describe_for_review`
    # and already opens with its own heading.
    if integration_note and stage == "review":
        included.append("integration")
        parts.append(integration_note.rstrip())

    # `0111` R7. Only for `review`, and only when `service.run_step` took the screenshots
    # again before it; `retake.describe_for_review` built it, heading included.
    if screens_note and stage == "review":
        included.append("screens")
        parts.append(screens_note.rstrip())

    # `0041` R2. Only for `pr`; `service.run_step` looked the pull request up and
    # `integrate.describe_pr_lookup` built the block, heading included.
    if pr_note and stage == "pr":
        included.append("pull-request")
        parts.append(pr_note.rstrip())

    # `0094` R14. Every artifact of the unit that exists, by absolute path, so what the
    # prompt no longer carries can still be found; `pointed` is what is here and not above.
    pointed: list[str] = []
    if pointing:
        lines = []
        for s in stages:
            name = f"{s}.md"
            if not (directory / name).is_file():
                continue
            above = name in included
            if not above:
                pointed.append(name)
            lines.append(f"- {directory.resolve() / name}" + (" (above)" if above else ""))
        if lines:
            parts.append("# The unit's files\n\n" + "\n".join(lines) + "\n\n" + UNIT_FILES_ADVICE)

    # `0044` R15. Only when one of the files this prompt carries or names holds a block Jera
    # wrote: otherwise not one byte is added, and a unit that never asked Jera gets the prompt
    # it got before.
    seen = [n for n in included if n.endswith(".md")] + pointed + ([artifact] if own_answers else [])
    jera = _jera_answers(directory, list(dict.fromkeys(seen)))
    if jera:
        parts.append(jera)

    # `0054` R7. Just before the task, so the note is the last thing read before it.
    if rerun:
        parts.append(_rerun_block(directory, stage, artifact, rerun_note))

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
    elif writes_own and stage == "pr":
        # `0041` R1. `pr` shared `impl`'s sentence — "do the work this unit's plan
        # authorises" — and on 2026-09-24 two runs of `0019`'s `pr` were reported to spend
        # every turn without writing `pr.md`: one rebasing onto `main`, one searching the
        # store for another unit's `pr.md` to copy (`0041` intent.md; not checked against
        # the run log). This says where the file goes, where its
        # shape is, the order that writes it before anything waits, and when to stop.
        parts.append(
            f"# Your task\n\n"
            f"Open this unit's pull request from the repository at "
            f"`{Path(workspace).expanduser().resolve()}`, then write `{location}` "
            "recording it.\n\n"
            "Its shape is `## Output` in the rules above. You do not need another unit's "
            "`pr.md` as an example, and the read boundary refuses one.\n\n"
            "In this order: find out whether the pull request already exists — the block "
            "above says, or ask `gh pr view` once. If it does not, push the branch and "
            "`gh pr create`. As soon as you have its URL, write `PR:` and "
            "`Status: accepted` into pr.md. Only after that read `gh pr checks` — once, "
            "never `--watch` — and record what it said.\n\n"
            "If the branch conflicts with `main`, or a required check is red, write that "
            "under `## Where` and stop. Do not rebase, merge, pull or change code: a "
            "conflict is *Integrate*'s on the board, and a red check sends the unit back "
            "to `impl`.\n\n"
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
    return "\n\n---\n\n".join(parts), included, pointed


# `0080` R1, R3. The file a spike keeps in its `cwd` as it measures, read when its reply is
# not an artifact. The same name as the unit's, so the skill names one file.
PROGRESS_FILE = "spike.md"
