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
import re
from pathlib import Path
from typing import Any, AsyncIterator

import claude_agent_sdk as sdk

from coscc import gitops, harness, steps
from coscc import sessions as sessions_mod
from coscc.journal import Journal
from coscc.policy import Grant, beyond_reading, decide, grant_for, is_prose_stage
from coscc.sessions import Refused, Sessions

# An artifact has to carry one of these on its first line, or the gate cannot read it and
# `cos.mjs` will report the unit as broken. Checked before anything is written.
STATUS_RE = re.compile(r"\bStatus:\s*([A-Za-z]+)")

# How many agent sessions one step starts. `Runner.run` makes exactly one `stream` call,
# so this is a description of the code below, not a setting: Settings shows it per stage
# (`0004_no-setting-says-which-model-runs-a-stage` `intent.md ## Answers, câu 1`). A stage
# that ran several agents would be a different design, and this number would change with it.
SESSIONS_PER_STEP = 1


class RunError(Exception):
    """A step that cannot start, or one whose reply cannot be stored."""


class _Stopped(Exception):
    """`0034`: a Stop came before `steps.seal`, so the artifact is not to be written."""


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


def build_prompt(
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
    drift_note: str = "",
    worktree: str = "",
    pr_note: str = "",
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
        parts.append(
            "# Where you work\n\n"
            f"Your working directory is `{Path(workspace).expanduser().resolve()}`, a "
            "throwaway directory the app deletes when this step ends. Write probe code "
            "there and nowhere else.\n\n"
            + (
                f"The unit's worktree is `{worktree}`. Read it; never write to it. The app "
                "records its `HEAD` and `git status --porcelain` before this step and again "
                "after, and fails the step, writing no `spike.md`, if either changed."
                if worktree
                else "No worktree was named for this step."
            )
        )

    # `spec.md` R7. A prose stage re-run against an artifact that already carries
    # `## Answers` is one of `intent.md ## Affected users and systems`' "later stages" too:
    # without this, it cannot see a person's decision and may ask the same question again.
    # `review` gets the same block, but placed after *The rounds so far* below instead --
    # `stage != "review"` here keeps it from also landing in this earlier position.
    if is_prose_stage(stage) and not writes_own and stage != "review":
        block = _answers_block(directory, artifact, repeat_content=stage != "intent")
        if block:
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

    # `spec.md` R7, `review`'s own copy. Placed here rather than with the other stages'
    # above so it reads after *The rounds so far*, which it is about: a person can answer
    # under `## Answers` while a review is at `changes-requested`, and the next review
    # must not treat that decision as an unread finding.
    if stage == "review" and is_prose_stage(stage) and not writes_own:
        block = _answers_block(directory, artifact, repeat_content=True)
        if block:
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

    # `0041` R2. Only for `pr`; `service.run_step` looked the pull request up and
    # `integrate.describe_pr_lookup` built the block, heading included.
    if pr_note and stage == "pr":
        included.append("pull-request")
        parts.append(pr_note.rstrip())

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


# `0019_a-failed-step-destroys-the-work-that-succeeded` plan step 3. Chosen, and measured
# to be too short. 8000 is the starting point `spec.md ## Answers, câu 2` names. Measured
# 2026-09-24 with `scripts/measure_0019_excerpt.py` on `0032`'s two exhausted `impl`
# transcripts, matching that unit's plan step 2 measurement commands (`measure_context.py
# --json` / `--strict-mcp`, `check_command(grant_for`, `npm test … wc -c`, `claude
# --help`, the `PermissionResultAllow` probe): the earliest such output started 87656
# characters from the end in `752523a2` and 101788 in `1a2ae5a7`. 8000 kept none of them
# in the first and only the last one (`--json --baseline`, 7868) in the second. Past plan step 3's
# 40000 stop line, so whether to filter by command or raise this is the initiator's call
# (plan.md step 3, the update of 2026-09-24); the number is left where the plan put it.
ATTEMPT_EXCERPT = 8000


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


# `0037_board-sessions-run-without-claude-codes-system-prompt`. What a board step holding
# any tool runs on, instead of the empty system prompt the SDK sends when none is set.
# Without it a step with `Read` and `Grep` had no guidance on using them, and searched
# with `grep` through Bash instead. Bare on purpose: no `append`. It grants nothing —
# `permission_gate` below and the grant's own tool list still decide every call — and a
# tool-less step (`idea`, `intent`) and chat never get it. A copy is what goes out.
CLAUDE_CODE_PRESET: dict[str, str] = {"type": "preset", "preset": "claude_code"}


def permission_gate(
    grant: Grant,
    workspace: str,
    denials: Denials,
    unit_dir: str | None = None,
    read_also: tuple[str, ...] = (),
    lease: tuple[str, str] | None = None,
):
    """The callback the SDK asks before every tool call.

    This is the enforcement `spec.md` R10 asks for, and it is separate from the tool list
    on purpose: the list was measured, and it does not cover every source of capability.
    `read_also` and `lease` (`0035`) are passed to `decide` unchanged.
    """

    async def can_use_tool(tool: str, tool_input: dict, context: Any):
        reason = decide(grant, tool, tool_input or {}, workspace, unit_dir, read_also, lease)
        if reason:
            denials.record(tool, reason)
            return sdk.PermissionResultDeny(message=reason)
        return sdk.PermissionResultAllow()

    return can_use_tool


async def snapshot(cwd: str, session_id: str) -> tuple[dict[str, Any], BaseException | None]:
    """What a stopped step left behind: git state and a transcript excerpt, read-only.

    `0019_a-failed-step-destroys-the-work-that-succeeded` plan step 5 / `spec.md` R1
    d-g, R2, R3. Every field is attempted independently so one failing costs only that
    field, recorded under `snapshot_errors` rather than raised. Returns `(fields,
    pending)`: `pending` is a `CancelledError` this was interrupted by, for the caller to
    re-raise once it has written what it has (`spec.md` C8) — a step killed mid-snapshot
    must not look like one that was never captured at all.
    """
    fields: dict[str, Any] = {
        "head": None, "branch": None, "base": None, "base_ref": None,
        "commits": None, "status": None, "excerpt": None, "excerpt_total_chars": None,
    }
    errors: list[str] = []
    path = Path(cwd)

    if not (path / ".git").exists():
        errors.append(f"git: {cwd} is not a git checkout")
    else:
        try:
            fields["head"], fields["branch"] = await gitops.head_and_branch(path)
        except asyncio.CancelledError:
            errors.append("head/branch: cancelled while reading")
            fields["snapshot_errors"] = errors
            return fields, asyncio.CancelledError("head/branch")
        except (gitops.GitError, OSError) as e:
            errors.append(f"head/branch: {e}")

        try:
            fields["base"], fields["base_ref"] = await gitops.merge_base(path)
        except asyncio.CancelledError:
            errors.append("base: cancelled while reading")
            fields["snapshot_errors"] = errors
            return fields, asyncio.CancelledError("base")
        except (gitops.GitError, OSError) as e:
            errors.append(f"base: {e}")

        if fields["base"] and fields["head"]:
            try:
                fields["commits"] = await gitops.log_range(path, fields["base"], fields["head"])
            except asyncio.CancelledError:
                errors.append("commits: cancelled while reading")
                fields["snapshot_errors"] = errors
                return fields, asyncio.CancelledError("commits")
            except (gitops.GitError, OSError, ValueError) as e:
                errors.append(f"commits: {e}")

        try:
            fields["status"] = await gitops.status_porcelain(path)
        except asyncio.CancelledError:
            errors.append("status: cancelled while reading")
            fields["snapshot_errors"] = errors
            return fields, asyncio.CancelledError("status")
        except (gitops.GitError, OSError) as e:
            errors.append(f"status: {e}")

    try:
        if session_id:
            excerpt, total = await asyncio.to_thread(
                sessions_mod.transcript_excerpt, session_id, cwd, ATTEMPT_EXCERPT
            )
            fields["excerpt"], fields["excerpt_total_chars"] = excerpt, total
        else:
            errors.append("excerpt: no session id was resolved before the step stopped")
    except asyncio.CancelledError:
        errors.append("excerpt: cancelled while reading")
        fields["snapshot_errors"] = errors
        return fields, asyncio.CancelledError("excerpt")
    except (ValueError, OSError) as e:
        errors.append(f"excerpt: {e}")

    if errors:
        fields["snapshot_errors"] = errors
    return fields, None


def _fmt_num(value: Any, suffix: str = "") -> str:
    return f"{value}{suffix}" if value is not None else "unknown — the session returned no result"


def describe_attempt(found: dict[str, Any]) -> str:
    """The `# The attempt before this one` section, in English (instructions to the model).

    `found` is `Journal.failed_attempts`'s return value: `0019` plan step 5.
    """
    attempt = found.get("attempt")
    latest = found.get("latest") or {}
    earlier = found.get("earlier") or []

    lines: list[str] = [
        "This is the state of the tree and the session at the moment the previous "
        "attempt at this stage stopped. The tree may have changed since then.",
        "",
    ]

    if attempt is None:
        lines.append(
            "No snapshot record was captured for that attempt (the capture itself may "
            "have failed, or ran before this app could take one). What is known comes "
            "only from the run log's own end-of-run record:"
        )
        lines.append(f"Outcome: {latest.get('outcome')}")
        lines.append(f"Turns: {_fmt_num(latest.get('turns'))}")
        lines.append(f"Cost: {_fmt_num(latest.get('cost_usd'), ' USD')}")
    else:
        lines.append(f"Outcome: {attempt.get('outcome')}")
        lines.append(f"Terminal reason: {attempt.get('terminal') or '(none)'}")
        err = attempt.get("error")
        lines.append(f"Error: {err['type']}: {err['message']}" if err else "Error: (none)")
        lines.append(f"Turns: {_fmt_num(attempt.get('turns'))}")
        lines.append(f"Cost: {_fmt_num(attempt.get('cost_usd'), ' USD')}")
        lines.append(f"Session: {attempt.get('session_id') or '(none resolved)'}")
        if attempt.get("head"):
            lines.append(
                f"Head: {attempt['head']} on {attempt.get('branch') or '(unknown branch)'}"
            )
        if attempt.get("base"):
            lines.append(
                f"Base: {attempt['base']} ({attempt.get('base_ref') or '(unknown ref)'})"
            )
        for c in attempt.get("commits") or []:
            lines.append(f"{c['sha']} {c['subject']}")
        for s in attempt.get("status") or []:
            lines.append(s)
        errs = attempt.get("snapshot_errors") or []
        if errs:
            lines.append("Could not read: " + "; ".join(errs))
        excerpt = attempt.get("excerpt")
        if excerpt:
            total = attempt.get("excerpt_total_chars") or len(excerpt)
            lines.append(
                f"--- excerpt: last {len(excerpt)} of {total} characters, verbatim ---"
            )
            lines.append(excerpt)
            lines.append("--- end of excerpt ---")

    if earlier:
        lines.append("")
        lines.append("Earlier attempts before that one, oldest first:")
        for e in earlier:
            lines.append(
                f"at {e.get('at')}: {e.get('outcome')}, "
                f"{_fmt_num(e.get('turns'), ' turns')}, cost {_fmt_num(e.get('cost_usd'), ' USD')}"
            )

    return "\n".join(lines)


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


async def _tree_state(path: str) -> tuple[str, str]:
    """`gitops.tree_state`, with a git failure turned into a reason the step stops for."""
    try:
        return await gitops.tree_state(Path(path))
    except gitops.GitError as e:
        raise RunError(f"could not read the worktree's state: {e}") from e


def describe_tree_change(before: tuple[str, str], after: tuple[str, str]) -> str:
    """`""` when the two `(HEAD, porcelain)` readings agree, else what moved (`0039` R13).

    Lists the porcelain lines on one side only — a new file, a file edited, one reverted —
    and a `HEAD` that moved as `HEAD <a>→<b>`.
    """
    parts: list[str] = []
    if before[0] != after[0]:
        parts.append(f"HEAD {before[0][:12]}→{after[0][:12]}")
    old, new = before[1].splitlines(), after[1].splitlines()
    moved = [line for line in new if line not in old] + [
        f"{line} (no longer)" for line in old if line not in new
    ]
    parts.extend(line.strip() for line in moved)
    return ", ".join(parts)


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
        model: str | None = None,
        model_source: str = "",
        base: dict[str, Any] | None = None,
        base_note: str = "",
        last_attempt: str = "",
        integration_note: str = "",
        plan_drift: dict[str, Any] | None = None,
        drift_note: str = "",
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

        head = await _head_of(watch or cwd)
        prompt, included = build_prompt(
            cwd, directory, unit, stage, stages, artifact,
            writes_own=not grant.app_writes_artifact,
            gate_said=gate_said,
            head=head,
            base_note=base_note,
            last_attempt=last_attempt,
            integration_note=integration_note,
            drift_note=drift_note,
            worktree=watch or "",
            pr_note=pr_note,
        )

        # `0041` R5 picks the `pr` steps that ran after the fix by this field being there,
        # the way `0037` picks by `system_prompt`. Only `pr` carries it.
        pr_extra = {"pr_before": pr_before or ""} if stage == "pr" else {}

        # `0037`: the same condition that decides whether a gate and a tool list are sent.
        preset = CLAUDE_CODE_PRESET if grant.opens_anything else None

        if self.journal is not None:
            self.journal.started(
                journal_key, unit, stage, mode,
                prompt_chars=len(prompt), included=included,
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
                # board runs the installed copy, not this checkout. `""` is the SDK's
                # empty one; a record written before `0037` has no field, read as `""`.
                system_prompt="claude_code" if preset else "",
                **({"plan_drift": plan_drift} if plan_drift is not None else {}),
                **pr_extra,
            )

        denials = Denials()
        collected = ""
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

        def stopped() -> bool:
            return running is not None and running.stop_requested

        try:
            before = await _tree_state(watch) if watch else None
            async for kind, payload in self.sessions.stream(
                cwd,
                prompt,
                None,
                max_turns=grant.max_turns,
                # Only pass a list and a gate when something was actually granted. A step
                # with an empty grant gets exactly the session the app makes by default,
                # which is the one the zero-tool default is about.
                can_use_tool=(
                    permission_gate(grant, cwd, denials, *gate_args)
                    if grant.opens_anything
                    else None
                ),
                tools=list(grant.tools) if grant.opens_anything else None,
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
                    collected += payload
                    yield ("chunk", payload)
                elif kind == "session":
                    # `0019` plan step 5. The one place this app learns a session id
                    # before the step is over. Not forwarded — see the `else` branch's own
                    # note on why only `chunk` may cross this boundary as itself.
                    session_id = str(payload)
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
                    models_used = list(payload.get("models_used") or [])

            if watch:
                # `0039` R13. Before anything is written: a spike that touched the branch
                # it was meant only to read must leave no `spike.md` saying it measured.
                changed = describe_tree_change(before, await _tree_state(watch))
                if changed:
                    raise RunError(f"the worktree changed during spike: {changed}")

            # `0034`. Nothing of the artifact has been read or written yet. From here on a
            # Stop is refused; before here, one that already came ends the step unwritten.
            if not steps.seal(running):
                raise _Stopped()
            if grant.app_writes_artifact:
                # `0025` `spec.md` R1-R6. The reply's own `## Answers`, if it has one, is
                # never what reaches disk (R3) -- only the section already there is, and
                # it is read as late as this module ever reads anything: right here,
                # after every `await` in this step has already happened, not at the
                # step's start (R6). Nothing between this read and the write below can
                # yield, so a block a person appended while the step ran is still on
                # disk when this runs and is carried through untouched.
                body = strip_answers(check_reply(collected))
                # `check_reply` looked at the whole reply, the reply's own `## Answers`
                # included. A `Status:` line that lived only there has just been cut, and
                # the gate reads nothing below the header anyway, so ask again of what
                # will actually be written (`0025` review round 1, F1).
                if not STATUS_RE.search(body):
                    raise RunError(
                        "the reply carries no `Status:` line above its own `## Answers`, "
                        "so the gate could not read it"
                    )
                target = directory / artifact
                try:
                    raw = target.read_bytes()
                except FileNotFoundError:
                    raw = b""
                section = answers_section(raw)
                above = raw[: len(raw) - len(section)] if section is not None else raw
                if artifact == "review.md":
                    # `merge_review` never sees the Answers section, so its own rounds
                    # regex has nothing of that shape to (not) swallow (spec.md Design).
                    # Its refusals are unchanged: a reply that rewrites an earlier round,
                    # or adds none, still raises before anything below is written (R4, R5).
                    body = merge_review(above.decode("utf-8", errors="replace"), body)
                target.write_bytes(with_answers(body, section))
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
            detail = _with_reply(detail, collected)
            # A step stopped by its own ceiling did not fail in the ordinary sense — it was
            # bounded. `journal.OUTCOMES` keeps the two apart so a reader can tell a defect
            # from a limit working as intended (`spec.md` R11).
            if _hit_ceiling(terminal):
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal} — {detail}"
        except Exception as e:  # surfaced as data; the process keeps serving
            error = {"type": type(e).__name__, "message": str(e)}
            detail = _with_reply(f"{type(e).__name__}: {e}", collected)
            if _hit_ceiling(terminal):
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal}"
        else:
            if _hit_ceiling(terminal):
                # It wrote something, but it ran out of room doing it. Saying `done` here
                # would hide that the work may be half finished.
                outcome, detail = "exhausted", f"stopped at the ceiling: {terminal}"
        finally:
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
                        turns=cost.get("turns"),
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
                    **cost,
                    **extra,
                )
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
