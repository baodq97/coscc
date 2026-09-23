"""What a step is allowed to do, keyed on the stage and the mode it runs in.

This is not a fifth knob. `Config` keeps meaning one thing — the app's default, which
`coscc/config.py:44` states as *chat only, no tools at all* — and this table says what
a **board step** may do instead. This table exists because that sentence in `config.py` was not
true; making `Config` answer for two different things as well is how it would stop being
true again.

Three properties, each deliberate:

- **Deny by default.** A pair this table does not name gets `Grant()`, which is no tools,
  one turn and no budget. A stage invented tomorrow is therefore locked, not open.
- **Pure.** Nothing here reads the environment, the store, or a request. There is no path
  from HTTP to these values, the same way there is none to `COS_WORKING_DIR`.
- **Mode matters.** `manual` never carries tools for any stage. Choosing `autonomous` is
  the act that grants them, and it is recorded in the journal when it happens.

That measurement is why `Grant.tools` is not the whole enforcement. A list handed to
the SDK covers the built-in set and nothing else — eleven MCP tools walked past `tools=[]`
on this machine. So the grant also carries what `Runner` must refuse at the moment of use,
and `can_use_tool` is where that happens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Stages whose artifact is prose. The app writes these from the text the session returns,
# so the session itself needs no ability to write at all — see `plan.md` Risk 1 for why the
# spec's design section is wrong about this, and why it is recorded there rather than
# quietly fixed here.
#
# `ship` left this list in `0015`. It merges now — `pr` stops at an open pull request and
# the merge waits for a review that passed — and a stage that runs `gh pr merge` is not
# one whose artifact the app can write from a reply.
PROSE_STAGES = ("idea", "intent", "spec", "plan", "review")


@dataclass(frozen=True)
class Grant:
    """What one step may do. The default is the locked position."""

    tools: tuple[str, ...] = ()
    # Commands the step may run, matched on the first word of the command line. Empty
    # means none, which is the only safe default for a field like this.
    commands: tuple[str, ...] = ()
    # Chosen, not measured: they exist to turn a loop that will not end into a
    # named failure, not to describe what a step ought to cost.
    max_turns: int = 1
    max_budget_usd: float = 0.0
    # Whether the app writes the artifact from the reply (prose stages) or the session
    # writes it itself (stages that touch code).
    app_writes_artifact: bool = True
    # Shown on the page *before* the step is started. `spec.md` C4: a capability that comes
    # from the machine's own configuration is exactly the kind that is invisible in an app,
    # and this unit opens one on purpose — so it has to be said out loud where the button
    # is, not only in a design document.
    warning: str = ""
    # Command prefixes refused even though their first word is allowed, each with the
    # reason given. Matched on the leading tokens of a segment, so it catches the plain
    # spelling and nothing cleverer — see `plan.md` Risk 5 of `0015`.
    denied: tuple[tuple[tuple[str, ...], str], ...] = ()

    @property
    def opens_anything(self) -> bool:
        return bool(self.tools or self.commands)


# Tools that only read. Safe for a step that has to understand a repository before changing
# it, and listed separately so the write set is short enough to read in one go.
READ_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")
EXEC_TOOLS = ("Bash",)

# Commands `impl` may run, matched on the first word of every segment of the command line.
# Deliberately short: this is the list that lets a step check its own work, not a shell.
IMPL_COMMANDS = (
    "git", "npm", "node", "uv", "python", "python3", "pytest",
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "diff", "mkdir", "true",
    "echo", "printf", "test", "which", "pwd", "sort", "uniq",
)

# What `pr` may run. Shorter than `impl`'s on purpose: this step proposes a change that
# already exists, so it needs version control and the reading to describe it, and nothing
# that builds or installs.
#
# `node` is here for one reason: `cos.mjs` is a node script, and `.claude/skills/write-pr/
# SKILL.md` opens by telling this stage to run `node .claude/scripts/cos.mjs gate <unit>
# pr`. This table did not carry it, so on 2026-09-23 a real `pr` step was refused with
# `this step may not run 'node'` and stopped — correctly, rather than deciding the gate's
# answer by reading its rules. That is the same shape as `plan` above: a skill requiring
# what the grant forbade, found by running a unit through the product and not by reading
# either file.
#
# It is not a small addition and is not written here as one. `node -e` runs anything, so
# this word widens the step by more than the one command it was added for. What bounds the
# step is unchanged, and `TheKnownLimit` in `coscc/policy_test.py` already states it: the
# session's `cwd`, the write check, and the turn and budget ceilings — never this list.
#
# `npm` and `uv` stay off. Nothing asks this stage to build or install, and the sentence
# above about that is still true.
PR_COMMANDS = (
    "git", "gh", "node",
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "diff",
    "echo", "printf", "test", "which", "pwd",
)

# Said on the page before the step starts. `gh` is logged in at the machine level — checked
# on 2026-09-21, account `baodq97` in `~/.config/gh/hosts.yml` — so a step that may run it
# can reach every repository that account can reach, not only this workspace. That is the
# same shape of hazard the zero-tool default is about, opened deliberately this time.
PR_WARNING = (
    "This step runs `git` and `gh` with the GitHub login already on this machine. "
    "That reaches every repository that account can reach, not just this workspace."
)

# `0015`: the pull request `pr` opens is merged by `ship`, after a review that passed, and
# never by the stage that opened it.
MERGE_IS_SHIPS = ((("gh", "pr", "merge"), "merging is the ship stage's"),)

SHIP_WARNING = (
    "This step merges the pull request into main with `gh pr merge`, using the GitHub "
    "login already on this machine. That login reaches every repository its account can "
    "reach. The gate has checked that the review passed with nothing open and that no code "
    "landed after it; nobody but an agent has read the change."
)

# Only pairs that appear here get anything. Everything else — every prose stage, every
# stage in `manual`, and anything invented later — falls through to `Grant()`.
GRANTS: dict[tuple[str, str], Grant] = {
    # The one entry whose ceilings are measured rather than chosen. Four `impl` steps ran
    # through the board on 2026-09-23 and three of them died at the turn ceiling:
    #
    #   0001, run 1   51/50 turns   $2.5317   exhausted, no impl.md
    #   0001, run 2   51/50 turns   $1.7866   exhausted, no impl.md
    #   0001, run 3   23/50 turns   $0.6611   done -- most of the work already existed
    #   0016, run 1   51/50 turns   $2.4099   exhausted, no impl.md, nothing committed
    #
    # Fifty was never a measurement. It was picked to end a loop that would not end, and
    # what it actually ended was three steps in the middle of working: the 0016 run left
    # 580 uncommitted lines across 7 files and a board that said the stage had not started.
    #
    # The run that finished did so in 23 turns *because two exhausted runs had already
    # done the work*, so it is not evidence that 23 is enough for a unit from cold. 120 is
    # roughly twice the highest real attempt, and the budget goes with it -- at the
    # measured $0.047/turn a 120-turn step lands near $5.6, so leaving the cap at $5 would
    # only move the same premature stop from one ceiling to the other.
    #
    # This raises the ceiling. It does not fix what happens at it: a step that hits one
    # still spends the money and leaves no record of what it did.
    # `0019_a-failed-step-destroys-the-work-that-succeeded` is that, and it is the real fix.
    ("impl", "autonomous"): Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS,
        commands=IMPL_COMMANDS,
        max_turns=120,
        max_budget_usd=8.0,
        app_writes_artifact=False,
    ),
    # `plan` reads, and only reads. `.claude/skills/write-plan/SKILL.md` has told this
    # stage to open the files it is about to name since it was written -- *"Read the files
    # the plan will touch before naming them"*, and invariant 1 requires every path under
    # `## Files that change` to be verified before it is written down. This table gave it
    # nothing, so a plan produced by the board named paths it had never seen.
    #
    # It went unnoticed until 2026-09-23 because until then every plan in this repository
    # had been typed by hand, by a session that did have tools. The first plan actually run
    # through the product is what found it.
    #
    # No write tools and no commands: the app still writes `plan.md` from the reply, which
    # is what stops a plan from authoring itself, and `beyond_reading` below is what keeps
    # that true if this entry is ever widened.
    ("plan", "autonomous"): Grant(
        tools=READ_TOOLS,
        # Chosen, not measured. Five files named in a spec is the case in front of me;
        # twenty turns leaves room to follow a reference and still ends a loop that will not.
        max_turns=20,
        max_budget_usd=2.0,
    ),
    ("pr", "autonomous"): Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS,
        commands=PR_COMMANDS,
        max_turns=30,
        max_budget_usd=3.0,
        app_writes_artifact=False,
        warning=PR_WARNING,
        denied=MERGE_IS_SHIPS,
    ),
    # `0015`: a separate agent session reviews the open pull request, before the merge. It
    # reads and only reads, like `plan`: the app still writes `review.md` from the reply.
    # It cannot run `git diff`, so it sees the working tree and `impl.md`, not the diff —
    # `0015` plan, Risk 3, and a later unit.
    ("review", "autonomous"): Grant(
        tools=READ_TOOLS,
        # Chosen, not measured; the same ceilings as `plan`.
        max_turns=20,
        max_budget_usd=2.0,
    ),
    # `0015`: `ship` merges, so it needs what `pr` has. Its ceilings are copied from `pr`,
    # chosen rather than measured.
    ("ship", "autonomous"): Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS,
        commands=PR_COMMANDS,
        max_turns=30,
        max_budget_usd=3.0,
        app_writes_artifact=False,
        warning=SHIP_WARNING,
    ),
}


def beyond_reading(grant: Grant) -> tuple[str, ...]:
    """What a grant carries that a prose stage may not — which is anything beyond reading.

    A prose stage is one whose artifact **the app** writes from the reply. That is the
    property worth defending: a step holding write tools could write its own artifact
    behind the app's back, and a step holding commands is not a prose stage at all.
    Reading is neither of those, and `plan` was required to read long before it was
    allowed to.

    So the guard in `coscc/runner.py` asks this rather than asking whether the grant is
    empty. The old question — empty or not — read as *no tools* and meant *no capability*;
    the two stopped being the same thing on 2026-09-23.
    """
    return tuple(t for t in grant.tools if t not in READ_TOOLS) + tuple(grant.commands)


def grant_for(stage: str, mode: str) -> Grant:
    """The grant for one step. Unknown pairs are locked, not open."""
    return GRANTS.get((stage, mode), Grant())


def is_prose_stage(stage: str) -> bool:
    return stage in PROSE_STAGES


# --- deciding one call -------------------------------------------------------
#
# Measured: the list handed to the SDK is not enough on its own. Eleven MCP tools
# arrived at a session created with `tools=[]`, because `--tools` names the built-in set and
# nothing else. A callback sits on the path every call takes, whatever declared it.

# Shell metacharacters that make the first word of a segment stop predicting what runs.
_SUBSTITUTION = ("$(", "`", "${", "<(", ">(")
_SEPARATORS = (";", "&&", "||", "|", "\n", "&")

# Redirection into a file, which is a write that no write-tool check would ever see.
# Measured on 2026-09-22: a real `impl` step was refused four times, and one of those was
# `Write` aimed at the working folder above the workspace — so the boundary matters and a
# shell that can reach past it matters just as much. `2>&1` is not this: the `&` says the
# target is another descriptor, not a path.
_REDIRECT = re.compile(r">>?\s*(?![&\s])")

# `2>&1` and friends: a redirect between descriptors, touching no file. Removed before the
# line is split, because the `&` in it would otherwise be read as a separator and the `1`
# as a command — which is exactly what `npm test 2>&1` did on 2026-09-22.
_FD_REDIRECT = re.compile(r"\d?>&\d?")


def _segments(command: str) -> list[str]:
    """Split a command line into the pieces that each start a process."""
    parts = [_FD_REDIRECT.sub(" ", command)]
    for sep in _SEPARATORS:
        parts = [piece for part in parts for piece in part.split(sep)]
    return [p.strip() for p in parts if p.strip()]


def check_command(grant: Grant, command: str) -> str:
    """"" if the command may run, else why not.

    **This is a best-effort reading of a shell command, and it is the weakest guard here.**
    `plan.md` Risk 3 says so: a first-word allowlist does not bound what `git` or `npm` can
    be told to do, and it cannot. What actually bounds the step is that the session runs
    with `cwd` set to the workspace and that writes are checked against it. Treat this as
    the thing that turns obvious mistakes into refusals, not as a sandbox.
    """
    text = (command or "").strip()
    if not text:
        return "an empty command"
    for token in _SUBSTITUTION:
        if token in text:
            # With substitution in play the first word no longer says what runs.
            return f"command substitution is not allowed: {token}"
    if _REDIRECT.search(text):
        # A redirect writes a file without any write tool being called, so the path check
        # in `decide` never sees it. The step has `Write` and `Edit` for making files.
        return "redirecting into a file is not allowed — use the write tools"
    for segment in _segments(text):
        word = segment.split()[0] if segment.split() else ""
        # `VAR=x cmd` puts the assignment first; step over any of them.
        while "=" in word and not word.startswith("-") and len(segment.split()) > 1:
            segment = segment.split(maxsplit=1)[1]
            word = segment.split()[0] if segment.split() else ""
        base = word.rsplit("/", 1)[-1]
        if base not in grant.commands:
            return f"this step may not run {base!r}"
        tokens = (base, *segment.split()[1:])
        for prefix, reason in grant.denied:
            if tokens[: len(prefix)] == prefix:
                return f"this step may not run {' '.join(prefix)!r}: {reason}"
    return ""


def _paths_in(tool_input: dict) -> list[str]:
    """Every path-shaped argument a write tool was given."""
    out = []
    for key in ("file_path", "path", "notebook_path", "target_file"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    return out


def decide(
    grant: Grant,
    tool: str,
    tool_input: dict,
    workspace: str,
    unit_dir: str | None = None,
) -> str:
    """"" if this call may proceed, else the reason it may not.

    Checked in this order on purpose: the tool has to be granted at all before anything
    about its arguments matters.

    **`unit_dir` widens the write boundary by exactly one directory, and `0014` `spec.md`
    C2 is why it had to.** A step that writes its own artifact — `impl` and `pr`, the two
    with `app_writes_artifact=False` — used to write it inside the workspace. `0014` moved
    every artifact into the product's own store so that nothing of coscc's lands in a
    repository a team shares, and that put the file the step must write outside the only
    place the step may write.

    Two properties keep this from being a hole. It is **one** directory, not a prefix of
    the store: a step may write its own unit's files and no other unit's. And the path is
    not the caller's — it comes from `coscc/units.py`, built from the data root (which is
    read from the environment, `coscc/config.py`) and a workspace that already passed the
    membership gate. There is no route from a request to this value.

    `None` means no second root, which is the shape every prose stage runs with: they are
    granted no write tools at all, so the question never arises for them.
    """
    from pathlib import Path

    if tool not in grant.tools:
        # Covers MCP tools by construction: their names are never in a grant.
        return f"this step was not granted {tool}"

    if tool in EXEC_TOOLS:
        reason = check_command(grant, str(tool_input.get("command", "")))
        if reason:
            return reason

    if tool in WRITE_TOOLS:
        roots = []
        for candidate in (workspace, unit_dir):
            if not candidate:
                continue
            try:
                roots.append(Path(candidate).expanduser().resolve())
            except OSError:
                return "the workspace path could not be resolved"
        if not roots:
            return "the workspace path could not be resolved"
        for raw in _paths_in(tool_input):
            try:
                target = Path(raw).expanduser().resolve()
            except OSError:
                return f"that path could not be resolved: {raw}"
            if not any(target == root or root in target.parents for root in roots):
                return f"writing outside the workspace is not allowed: {raw}"
    return ""
