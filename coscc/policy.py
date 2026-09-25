"""What a step is allowed to do, keyed on the stage it runs.

This is not a fifth knob. `Config` keeps meaning one thing — the app's default, which
`coscc/config.py:44` states as *chat only, no tools at all* — and this table says what
a **board step** may do instead. This table exists because that sentence in `config.py` was not
true; making `Config` answer for two different things as well is how it would stop being
true again.

Three properties, each deliberate:

- **Deny by default.** A stage this table does not name gets `Grant()`, which is no tools,
  one turn and no budget. A stage invented tomorrow is therefore locked, not open.
- **Pure.** Nothing here reads the environment, the store, or a request. There is no path
  from HTTP to these values, the same way there is none to `COS_WORKING_DIR`.
- **The tools go with the stage, not the mode.** Until `0020` `manual` carried nothing and
  `autonomous` carried the grant. `0020` `spec.md` `## Answers`, answer 1, ended that: a
  stage's tools follow from its task, in every mode. The mode is still recorded in the
  journal; it decides nothing here.

That measurement is why `Grant.tools` is not the whole enforcement. A list handed to
the SDK covers the built-in set and nothing else — eleven MCP tools walked past `tools=[]`
on this machine. So the grant also carries what `Runner` must refuse at the moment of use,
and `can_use_tool` is where that happens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from coscc.labels import NOVEL

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
    # `0035` R6: every `git push` must carry `--force-with-lease` bound to the head the pull
    # request had when the step began, and name the unit's own branch. The lease itself is
    # not in the grant — it is per run — and reaches `decide` as `lease`.
    push_needs_lease: bool = False
    # `0041` R3: no `git push` may force — `--force`, `-f`, `--force-with-lease`,
    # `--force-if-includes` or a `+` refspec. A plain push stays open.
    push_no_force: bool = False

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
    "That reaches every repository that account can reach, not just this workspace. "
    "After the step, the app itself rewrites the pull request's title and body from pr.md under that login."
)

# `0015`: the pull request `pr` opens is merged by `ship`, after a review that passed, and
# never by the stage that opened it.
#
# Matched on the words left once flags are removed (`_words` below), so a `-R o/r` in front
# does not walk past it. `gh alias set` is refused too: an alias `pr` defines is an alias
# `pr` can then run under another name.
MERGE_IS_SHIPS = (
    (("gh", "pr", "merge"), "merging is the ship stage's"),
    (("gh", "alias", "set"), "an alias is a merge under another name; merging is the ship stage's"),
)

# `0041` R3: on 2026-09-24 a `pr` step of `0019` met a conflict with `main`, rebased it
# itself, and ran out of turns in the middle — no `pr.md`, and a rebase left half done in
# the tree. Bringing `main` in is integration's (`0035`, Gebo), never `pr`'s. The push
# itself stays open; forcing it is refused by `push_no_force`.
_INTEGRATION_IS_NOT_PRS = (
    "bringing main in is integration's — record the conflict in pr.md, accept it and stop; "
    "a person presses *Integrate* on the board"
)
PR_DENIED = MERGE_IS_SHIPS + (
    (("git", "rebase"), _INTEGRATION_IS_NOT_PRS),
    (("git", "merge"), _INTEGRATION_IS_NOT_PRS),
    (("git", "pull"), _INTEGRATION_IS_NOT_PRS),
    (("gh", "pr", "update-branch"), _INTEGRATION_IS_NOT_PRS),
)

SHIP_WARNING = (
    "This step merges the pull request into main with `gh pr merge`, using the GitHub "
    "login already on this machine. That login reaches every repository its account can "
    "reach. The gate has checked that the review passed with nothing open and that no code "
    "landed after it; nobody but an agent has read the change."
)

# `0035`: Gebo, the integration step. Not a stage — it runs outside the loop, on a unit
# between `pr` and `ship`, only when a person presses the button — but keyed in the same
# table so it starts from the locked position like everything else.
#
# `impl`'s commands, because resolving a conflict means running the repository's tests
# before pushing, plus `gh` to read the pull request and its CI.
INTEGRATE_COMMANDS = IMPL_COMMANDS + ("gh",)

INTEGRATE_WARNING = (
    "Integrating runs `git` and `gh` with the GitHub login already on this machine, and "
    "force-pushes (with a lease) to this unit's branch. That login reaches every repository "
    "its account can reach, not just this workspace. What it resolves is an agent's word, "
    "not a person's approval."
)

# R6: rebase only. `git merge` and `git pull` would bring `main` in by merging, and
# `gh pr update-branch` would move the head on GitHub's side under the lease the push is
# bound to — the push has exactly one road.
INTEGRATE_DENIED = MERGE_IS_SHIPS + (
    (("git", "merge"), "integration is by rebase, never by merge"),
    (("git", "pull"), "integration is by rebase, never by merge"),
    (("gh", "pr", "update-branch"), "the head the push is leased to would move under it"),
    # `0035` review round 2, F4: roads to the branch that are not `git push` and so never
    # meet the lease. `gh api` reaches `git/refs` with `force=true`; the pull request and
    # its checks are read with `gh pr view` and `gh pr checks`, which stay open.
    (("gh", "api"), "it can move the branch on GitHub with no lease; read with `gh pr view` or `gh pr checks`"),
    (("gh", "repo", "sync"), "it can force the branch on GitHub with no lease"),
    (("gh", "extension"), "an extension is a command this grant cannot read"),
    (("git", "send-pack"), "it pushes without the lease; push only with `git push --force-with-lease`"),
    (("git", "http-push"), "it pushes without the lease; push only with `git push --force-with-lease`"),
)

# `0035` review round 2, F4: an alias or an included config file made during the step
# renames `push` into a word `_may_be_push` never sees — `git -c alias.p=push p`, `git
# config alias.p push`, or the same through `GIT_CONFIG_*`. Matched on the whole segment,
# assignments included, so a commit message naming one is refused too, with this reason.
_GIT_CONFIG_ROAD = re.compile(r"(?:^|[\s='\"])(?:alias|include|includeif)\.|\bGIT_CONFIG", re.IGNORECASE)

# `0039`: ᛈ Perthro, the spike step. `impl`'s commands without `git`: `git -C <worktree>
# commit` is the shortest road for throwaway code into the unit's branch (`spec.md ##
# Answers, câu 2`). Everything else is kept, because measuring means running things.
SPIKE_COMMANDS = tuple(c for c in IMPL_COMMANDS if c != "git")

SPIKE_WARNING = (
    "This step runs arbitrary code (`python`, `node`, `npm`, `uv`) under this process's "
    "user, in a throwaway directory the app deletes afterwards. Nothing is a sandbox: a "
    "write outside that directory is caught only inside the unit's worktree, where it "
    "fails the step, and is not undone. Anywhere else, `~` included, it is not seen."
)

# `0074` R19. Said on the Backlog panel above the button, before it is pressed.
ESTIMATE_WARNING = (
    "Proposing estimates opens one paid session (1 turn, $2.00 ceiling) on the model of the "
    "Settings row `estimate`. Whoever holds the password or a live session can press it, and "
    "can rewrite any estimate, relation or the shortlist under any name they type."
)

# Only stages that appear here get anything. The rest — `idea`, `intent`, and any
# stage invented later — falls through to `Grant()`. Keyed by stage alone since `0020`:
# the mode a step is started in is recorded, and grants nothing.
GRANTS: dict[str, Grant] = {
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
    "impl": Grant(
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
    "plan": Grant(
        tools=READ_TOOLS,
        # Twenty was chosen, not measured, and on 2026-09-23 it cut a plan mid-read:
        # `0021_review-findings-never-reach-the-pull-request` stopped at the ceiling after
        # $1.0777 and returned nothing, because what it had to read had grown -- the gates,
        # the runner and the service, plus everything `0015` added to all three. Earlier
        # plans finished under the same ceiling.
        #
        # The `turns` the app records is not the counter `max_turns` stops on (plans that
        # finished were recorded at 30 and 34), so there is no measured number to set this
        # from. Forty doubles the ceiling that was hit; the budget moves with it so the
        # other limit does not become the real one.
        max_turns=40,
        max_budget_usd=4.0,
    ),
    # `spec` reads, and only reads, for the reason `plan` does. `write-spec` invariant 7
    # requires every figure to name its source and every citation to carry a path and a
    # line range, and until `0020` this table gave the stage no way to open a file. So it
    # wrote from descriptions: `0016`'s R9 required a commit in the store, which has never
    # been a git repository, and only `plan` — which could read — caught it.
    #
    # No write tools and no commands: the app still writes `spec.md` from the reply.
    # Both ceilings are copied from `plan` above, not measured for `spec`.
    "spec": Grant(
        tools=READ_TOOLS,
        max_turns=40,
        max_budget_usd=4.0,
    ),
    # `0039`. The first grant that both holds commands and has the app write its artifact
    # from the reply. `beyond_reading` guards only `PROSE_STAGES`, which this is not, so
    # `policy_test` pins `git` out of `commands` instead (`spec.md` C2). Writing is held to
    # the session's `cwd`, a throwaway directory `service.run_step` makes and removes; the
    # worktree and the unit are read through `read_also`.
    #
    # `0080` R8. Ceilings chosen, not measured. The first ones were `spec`'s and `plan`'s,
    # 40 turns / $4.0, and on 2026-09-24 and 2026-09-25 three spikes (`0070`, `0078`,
    # `0053`) stopped at them without writing `spike.md`; spikes that finished were recorded
    # at 36-44 turns (`0080` `intent.md ## Answers, câu 2`). The `turns` the app records is
    # not the counter `max_turns` stops on (see `plan` above), so 80 doubles the ceiling
    # that was hit, as `plan` went from 20 to 40. The seven runs that answer lists cost
    # $0.032-0.055 a recorded turn ($1.33/41 to $2.03/37), so 80 turns would be about
    # $2.6-4.4, and $4 would stop the dearer ones before the turn ceiling; $8.0 is `impl`'s.
    "spike": Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS,
        commands=SPIKE_COMMANDS,
        max_turns=80,
        max_budget_usd=8.0,
        app_writes_artifact=True,
        warning=SPIKE_WARNING,
    ),
    "pr": Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS,
        commands=PR_COMMANDS,
        max_turns=30,
        max_budget_usd=3.0,
        app_writes_artifact=False,
        warning=PR_WARNING,
        denied=PR_DENIED,
        push_no_force=True,
    ),
    # `0015`: a separate agent session reviews the open pull request, before the merge. It
    # reads and only reads, like `plan`: the app still writes `review.md` from the reply.
    # It cannot run `git diff`, so it sees the working tree and `impl.md`, not the diff —
    # `0015` plan, Risk 3, and a later unit.
    "review": Grant(
        tools=READ_TOOLS,
        # `0085` R1. Chosen, not measured: 20/$2.0 was `plan`'s ceiling before it went to
        # 40/$4.0, and five review sessions on 2026-09-25 stopped at it without writing a
        # round (`0085` `intent.md ## Answers, câu 4`). A review that still stops at it
        # gets one closing turn from the app (`runner.Runner.run`), which this budget does
        # not bound: the CLI compares the session's whole cost, after the turn has run
        # (`0085` `spike.md ## U2`, point 3).
        max_turns=40,
        max_budget_usd=4.0,
    ),
    # `0015`: `ship` merges, so it needs what `pr` has. Its ceilings are copied from `pr`,
    # chosen rather than measured.
    "ship": Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS,
        commands=PR_COMMANDS,
        max_turns=30,
        max_budget_usd=3.0,
        app_writes_artifact=False,
        warning=SHIP_WARNING,
    ),
    # `0035`. Ceilings chosen, not measured: `spec.md ## Answers`, answer 1 — "start from
    # impl's ceilings (120 turns, $8)", and lower them once real runs are recorded. No
    # Gebo run existed when this was written.
    "integrate": Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS,
        commands=INTEGRATE_COMMANDS,
        max_turns=120,
        max_budget_usd=8.0,
        app_writes_artifact=False,
        warning=INTEGRATE_WARNING,
        denied=INTEGRATE_DENIED,
        push_needs_lease=True,
    ),
    # `0074`. Not a stage either: the backlog's *Propose estimates* button, one session per
    # press. No tools and no commands, like `idea` and `intent`; the app reads the reply.
    # Ceilings chosen, not measured (`spec.md` C5): nobody has measured a prompt of ~70 units.
    "estimate": Grant(
        max_turns=1,
        max_budget_usd=2.0,
        warning=ESTIMATE_WARNING,
    ),
}

# `0062`. The ceilings a step gets when its plan's label is `novel` (`coscc/labels.py`),
# as `(max_turns, max_budget_usd)`; everything else about the grant stays the stage's own.
# A stage not named here runs the same grant whatever its label.
#
# 250 is chosen, not measured (`0062` spec C1): `intent.md ## Answers, câu 1` — "Đề xuất
# novel: max_turns 250, ngân sách tương ứng ×2 impl thường" — read as 2 × $8.0 (spec C5).
# `spike.md ## U1` measured the dearest turn at $0.0419 across the `novel` runs (250 turns
# → $10.48) and $0.0568 across all 60 `impl` runs (→ $14.21), so $16 leaves a thin margin,
# and nothing between 181 and 250 turns has ever been measured. A `novel` impl that stops
# on the budget instead is not escalated and not counted by the intent's outcome.
NOVEL_CEILINGS: dict[str, tuple[int, float]] = {
    "impl": (250, 16.0),
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


def grant_for(stage: str) -> Grant:
    """The grant for one step. A stage the table does not name is locked, not open.

    No mode: `0020` `spec.md` `## Answers`, answer 1 — the tools go with the stage's task.
    """
    return GRANTS.get(stage, Grant())


def grant_for_step(stage: str, label: str | None) -> Grant:
    """The grant for one step run under a plan's effective label.

    Only the exact `novel` label, on a stage `NOVEL_CEILINGS` names, changes anything, and
    only the two ceilings: the tools, commands and refusals are `grant_for(stage)`'s.
    """
    grant = grant_for(stage)
    if label == NOVEL and stage in NOVEL_CEILINGS:
        turns, budget = NOVEL_CEILINGS[stage]
        return replace(grant, max_turns=turns, max_budget_usd=budget)
    return grant


def is_prose_stage(stage: str) -> bool:
    return stage in PROSE_STAGES


# --- deciding one call -------------------------------------------------------
#
# Measured: the list handed to the SDK is not enough on its own. Eleven MCP tools
# arrived at a session created with `tools=[]`, because `--tools` names the built-in set and
# nothing else. A callback sits on the path every call takes, whatever declared it.

# `0060`: the line is read the way bash reads it, not split as raw text. Until then `;`, `|`
# and `&&` were split on wherever they stood, quotes and heredoc bodies included, and
# `$(`, `` ` `` and `${` were refused wherever they stood, single quotes included. The
# originator counted 165 refusals naming a "command" that was only a fragment of text, on
# 2026-09-23..24 (`0060 intent.md ## Problem`; the transcripts are not in this repository).
#
# Bash's own rules, copied: `0060 spike.md ## U2` measured that a board step's `Bash` runs
# `bash -c "… eval '<command>'"`, bash 5.3, `extglob` off. Anything this reader is not sure
# of is `_Unreadable`, and an unreadable line is refused, never guessed (`0060` R6).


@dataclass(frozen=True)
class _Redirect:
    op: str
    fd: str
    # Quotes removed. For `<<` and `<<-` this is the delimiter.
    target: str
    # A parameter expansion, an unquoted leading `~`, or an unquoted `*`, `?` or `[`:
    # bash will open some other path than the one written here.
    expanded: bool


@dataclass(frozen=True)
class _Simple:
    """One simple command: assignments, words and redirects, up to the next operator."""

    # The command's own text, quotes kept — what `_GIT_CONFIG_ROAD` reads.
    source: str
    # Quotes removed, nothing expanded: `"$X"` is the word `$X`.
    words: tuple[str, ...]
    # Per word: a parameter expansion outside single quotes.
    expanded: tuple[bool, ...]
    redirects: tuple[_Redirect, ...]


@dataclass(frozen=True)
class _Parsed:
    commands: tuple[_Simple, ...]
    # Every substitution in effect, as `(token, at)`: `$(`, `` ` ``, `<(`, `>(`, `$((`.
    substitutions: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _Unreadable:
    what: str
    at: int


class _Stop(Exception):
    def __init__(self, what: str, at: int):
        super().__init__(what)
        self.what, self.at = what, at


class _Word:
    def __init__(self, at: int):
        self.at = at
        self.buf: list[str] = []
        self.quoted = False
        self.expanded = False
        self.glob = False

    @property
    def text(self) -> str:
        return "".join(self.buf)


_NAME_START = re.compile(r"[A-Za-z_]")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SPECIAL_PARAMS = "0123456789@*#?$!-"
# Bash's own test for `NAME=value` in front of a command, `NAME[i]=` and `NAME+=` included.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")
_ANSI_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}


class _Reader:
    """A state machine over one command line. `_read` is the only caller."""

    def __init__(self, s: str, base: int = 0):
        self.s = s
        self.n = len(s)
        self.i = 0
        # Where `s` sits in the line the caller passed, so a heredoc body read on its own
        # still reports its position in the whole command.
        self.base = base
        self.subs: list[tuple[str, int]] = []

    def at(self, j: int) -> int:
        return self.base + j

    def skip(self, j: int) -> int:
        """`j`, moved past any `\\` + newline: bash removes those before reading a token."""
        while self.s.startswith("\\\n", j):
            j += 2
        return j

    def char(self, j: int) -> str:
        return self.s[j] if j < self.n else ""

    # --- the command level ---------------------------------------------------

    def commands(self, opened: int | None = None) -> list[_Simple]:
        """Read commands until the end, or — when `opened` is the position of a `$(`, `<(`
        or `>(` — until the `)` that closes it."""
        s = self.s
        out: list[_Simple] = []
        start = self.i
        words: list[str] = []
        flags: list[bool] = []
        redirects: list[_Redirect] = []
        word: _Word | None = None
        pending: tuple[str, str, int] | None = None
        heredocs: list[tuple[str, bool, bool, int]] = []
        depth = 0

        def end_word() -> None:
            nonlocal word, pending
            if word is None:
                return
            if pending is not None:
                op, fd, _ = pending
                if op in ("<<", "<<-"):
                    heredocs.append((word.text, word.quoted, op == "<<-", word.at))
                    redirects.append(_Redirect(op, fd, word.text, False))
                else:
                    redirects.append(_Redirect(op, fd, word.text, word.expanded or word.glob))
                pending = None
            else:
                words.append(word.text)
                flags.append(word.expanded)
            word = None

        def end_command(j: int) -> None:
            nonlocal start, words, flags, redirects
            end_word()
            if pending is not None:
                raise _Stop(f"a redirect ({pending[0]}) with no target", self.at(pending[2]))
            if words or redirects:
                out.append(_Simple(s[start:j].strip(), tuple(words), tuple(flags), tuple(redirects)))
            words, flags, redirects = [], [], []
            start = j + 1

        def at_command_start() -> bool:
            return word is None and not words and not redirects and pending is None

        while True:
            self.i = self.skip(self.i)
            if self.i >= self.n:
                break
            c = s[self.i]
            if c in " \t":
                end_word()
                self.i += 1
            elif c == "\n":
                end_command(self.i)
                self.i += 1
                if heredocs:
                    self.bodies(heredocs)
                    heredocs = []
                    start = self.i
            elif c == "#" and word is None:
                # A comment runs to the end of the line; the newline itself is still read.
                nl = s.find("\n", self.i)
                self.i = self.n if nl < 0 else nl
            elif c == ";":
                end_command(self.i)
                self.i += 1
            elif c == "&":
                j = self.skip(self.i + 1)
                if self.char(j) == ">":
                    amp = self.i
                    k = self.skip(j + 1)
                    op, self.i = ("&>>", k + 1) if self.char(k) == ">" else ("&>", j + 1)
                    end_word()
                    if pending is not None:
                        raise _Stop(f"a redirect ({pending[0]}) with no target", self.at(pending[2]))
                    pending = (op, "", amp)
                else:
                    end_command(self.i)
                    self.i = j + 1 if self.char(j) == "&" else self.i + 1
            elif c == "|":
                j = self.skip(self.i + 1)
                end_command(self.i)
                self.i = j + 1 if self.char(j) in ("|", "&") else self.i + 1
            elif c in "<>":
                j = self.skip(self.i + 1)
                if self.char(j) == "(":
                    # `<(…)` and `>(…)`: a process whose output is a path. Part of a word.
                    if word is None:
                        word = _Word(self.i)
                    self.subs.append((c + "(", self.at(self.i)))
                    word.expanded = True
                    self.i = j + 1
                    self.commands(opened=self.i - 2)
                    continue
                op, self.i = self.redirect_op(c, j)
                fd = ""
                if pending is None and word is not None and word.text.isdigit() and not word.quoted \
                        and not word.expanded:
                    fd, word = word.text, None
                end_word()
                if pending is not None:
                    raise _Stop(f"a redirect ({pending[0]}) with no target", self.at(pending[2]))
                pending = (op, fd, self.i - len(op))
            elif c == "(" and at_command_start():
                depth += 1
                self.i += 1
                start = self.i
            elif c == "(" and word is not None and pending is None and _is_array_open(word, words):
                self.array(word)
            elif c == ")" and depth:
                end_command(self.i)
                depth -= 1
                self.i += 1
            elif c == ")" and opened is not None:
                end_command(self.i)
                self.i += 1
                if heredocs:
                    raise _Stop(f"a here-document ({heredocs[0][0]}) with no line to end it", self.at(heredocs[0][3]))
                return out
            else:
                # `(` and `)` anywhere else are kept as text: bash refuses the line as a
                # syntax error, so nothing runs, and the words around them are still read.
                if word is None:
                    word = _Word(self.i)
                self.part(word)
        if opened is not None:
            raise _Stop(f"an unclosed {s[opened:opened + 2]}", self.at(opened))
        end_command(self.n)
        if heredocs:
            raise _Stop(f"a here-document ({heredocs[0][0]}) with no line to end it", self.at(heredocs[0][3]))
        if depth:
            raise _Stop("an unclosed (", self.at(start))
        return out

    def redirect_op(self, c: str, j: int) -> tuple[str, int]:
        """The redirect operator starting with `c`, whose next character is at `j`, and where
        the text after it begins."""
        nxt = self.char(j)
        if c == ">":
            if nxt in (">", "&", "|"):
                return ">" + nxt, j + 1
            return ">", j
        if nxt == "<":
            k = self.skip(j + 1)
            if self.char(k) == "<":
                return "<<<", k + 1
            if self.char(k) == "-":
                return "<<-", k + 1
            return "<<", k
        if nxt in ("&", ">"):
            return "<" + nxt, j + 1
        return "<", j

    def array(self, word: _Word) -> None:
        """`NAME=(a b c)`: one word, blanks and newlines included, up to its `)`."""
        depth = 0
        opened = self.i
        while True:
            self.i = self.skip(self.i)
            c = self.char(self.i)
            if not c:
                raise _Stop("an unclosed (", self.at(opened))
            if c in ";&|<>":
                raise _Stop("an operator inside an array assignment", self.at(self.i))
            if c in "( \t\n)":
                depth += {"(": 1, ")": -1}.get(c, 0)
                word.buf.append(c)
                self.i += 1
                if depth == 0:
                    return
            else:
                self.part(word)

    def bodies(self, heredocs: list[tuple[str, bool, bool, int]]) -> None:
        """Every here-document queued on the line just ended, in order."""
        s = self.s
        for delimiter, literal, strip, opened in heredocs:
            begin = self.i
            logical, line_start = "", self.i
            while True:
                if self.i >= self.n:
                    raise _Stop(f"a here-document ({delimiter}) with no line to end it", self.at(opened))
                nl = s.find("\n", self.i)
                end = self.n if nl < 0 else nl
                line = s[self.i:end]
                self.i = end + 1
                if strip:
                    line = line.lstrip("\t")
                if not literal and (len(line) - len(line.rstrip("\\"))) % 2 == 1:
                    # Bash joins a line ending in `\` to the next before comparing it.
                    logical += line[:-1]
                    continue
                logical += line
                if logical == delimiter:
                    if not literal:
                        body = _Reader(s[begin:line_start], self.at(begin))
                        body.expanding()
                        self.subs.extend(body.subs)
                    break
                logical, line_start = "", self.i
            self.i = min(self.i, self.n)

    # --- inside a word -------------------------------------------------------

    def part(self, word: _Word) -> None:
        """One piece of a word outside quotes, at `self.i`."""
        s = self.s
        c = s[self.i]
        if c == "\\":
            if self.i + 1 >= self.n:
                word.buf.append("\\")
                self.i += 1
                return
            word.buf.append(s[self.i + 1])
            word.quoted = True
            self.i += 2
        elif c == "'":
            close = s.find("'", self.i + 1)
            if close < 0:
                raise _Stop("an unclosed '", self.at(self.i))
            word.buf.append(s[self.i + 1:close])
            word.quoted = True
            self.i = close + 1
        elif c == '"':
            self.double(word)
        elif c == "$":
            self.dollar(word, quoted=False)
        elif c == "`":
            self.backtick(word)
        else:
            if c in "*?[" or (c == "~" and not word.buf and not word.quoted):
                word.glob = True
            word.buf.append(c)
            self.i += 1

    def double(self, word: _Word) -> None:
        """A `"…"` at `self.i`: `$`, `` ` `` and `\\` keep their meaning inside."""
        s = self.s
        opened = self.i
        word.quoted = True
        self.i += 1
        while True:
            if self.i >= self.n:
                raise _Stop('an unclosed "', self.at(opened))
            c = s[self.i]
            if c == '"':
                self.i += 1
                return
            if c == "\\" and self.char(self.i + 1) in ("$", "`", '"', "\\", "\n"):
                if s[self.i + 1] != "\n":
                    word.buf.append(s[self.i + 1])
                self.i += 2
            elif c == "$":
                self.dollar(word, quoted=True)
            elif c == "`":
                self.backtick(word)
            else:
                word.buf.append(c)
                self.i += 1

    def expanding(self) -> None:
        """A heredoc body whose delimiter was not quoted: as `"…"`, but `"` is only a
        character and there is no closing quote."""
        s = self.s
        word = _Word(0)
        while self.i < self.n:
            c = s[self.i]
            if c == "\\" and self.char(self.i + 1) in ("$", "`", "\\", "\n"):
                self.i += 2
            elif c == "$":
                self.dollar(word, quoted=True)
            elif c == "`":
                self.backtick(word)
            else:
                self.i += 1

    def dollar(self, word: _Word, quoted: bool) -> None:
        """A `$` at `self.i`, in or out of double quotes."""
        s = self.s
        opened = self.i
        j = self.skip(self.i + 1)
        c = self.char(j)
        if c == "(":
            k = self.skip(j + 1)
            word.expanded = True
            if self.char(k) == "(" and self.arithmetic(k + 1):
                self.subs.append(("$((", self.at(opened)))
            else:
                self.subs.append(("$(", self.at(opened)))
                self.i = j + 1
                self.commands(opened=opened)
            # The word keeps the text as written: nothing here is expanded.
            word.buf.append(s[opened:self.i])
        elif c == "{":
            word.expanded = True
            self.i = j + 1
            self.brace(opened)
            word.buf.append(s[opened:self.i])
        elif c == "'" and not quoted:
            self.ansi(word, j + 1)
        elif c == '"' and not quoted:
            # `$"…"` is a translated string: to this reader, a double-quoted one.
            self.i = j
            self.double(word)
        elif c and _NAME_START.match(c):
            name = _NAME.match(s, j)
            word.expanded = True
            word.buf.append("$" + name.group(0))
            self.i = name.end()
        elif c and c in _SPECIAL_PARAMS:
            word.expanded = True
            word.buf.append("$" + c)
            self.i = j + 1
        else:
            # `$[` is bash's old arithmetic: it runs nothing, but it is not the text either.
            if c == "[":
                word.expanded = True
            word.buf.append("$")
            self.i += 1

    def arithmetic(self, j: int) -> bool:
        """Whether `$((` has its `))` from `j`; if so, `self.i` moves past it. If not, bash
        reads it as `$( (`, and so does the caller."""
        s = self.s
        depth = 0
        while j < self.n:
            c = s[j]
            if c == "(":
                depth += 1
            elif c == ")":
                if depth:
                    depth -= 1
                elif self.char(self.skip(j + 1)) == ")":
                    self.i = self.skip(j + 1) + 1
                    return True
                else:
                    return False
            j += 1
        return False

    def brace(self, opened: int) -> None:
        """The inside of `${…}`, from `self.i`. Braces nest; quotes and substitutions keep
        their meaning, single quotes included, even inside `"…"` — measured on bash 5.3."""
        s = self.s
        depth = 1
        scratch = _Word(0)
        while True:
            self.i = self.skip(self.i)
            if self.i >= self.n:
                raise _Stop("an unclosed ${", self.at(opened))
            c = s[self.i]
            if c == "\\":
                self.i += 2
            elif c == "'":
                close = s.find("'", self.i + 1)
                if close < 0:
                    raise _Stop("an unclosed '", self.at(self.i))
                self.i = close + 1
            elif c == '"':
                self.double(scratch)
            elif c == "$":
                self.dollar(scratch, quoted=False)
            elif c == "`":
                self.backtick(scratch)
            else:
                depth += {"{": 1, "}": -1}.get(c, 0)
                self.i += 1
                if depth == 0:
                    return

    def backtick(self, word: _Word) -> None:
        s = self.s
        opened = self.i
        self.subs.append(("`", self.at(opened)))
        word.expanded = True
        j = self.i + 1
        while j < self.n and s[j] != "`":
            j += 2 if s[j] == "\\" else 1
        if j >= self.n:
            raise _Stop("an unclosed `", self.at(opened))
        self.i = j + 1
        word.buf.append(s[opened:self.i])

    def ansi(self, word: _Word, j: int) -> None:
        """`$'…'` from `j`: only `\\` means anything inside."""
        s = self.s
        opened = j - 2
        word.quoted = True
        while True:
            if j >= self.n:
                raise _Stop("an unclosed $'", self.at(opened))
            c = s[j]
            if c == "'":
                self.i = j + 1
                return
            if c == "\\" and j + 1 < self.n:
                e = s[j + 1]
                if e in _ANSI_ESCAPES:
                    word.buf.append(_ANSI_ESCAPES[e])
                    j += 2
                    continue
                hexa = re.match(r"x([0-9A-Fa-f]{1,2})", s[j + 1:j + 4])
                if hexa:
                    word.buf.append(chr(int(hexa.group(1), 16)))
                    j += 1 + len(hexa.group(0))
                    continue
                word.buf.append(s[j:j + 2])
                j += 2
                continue
            word.buf.append(c)
            j += 1


def _is_array_open(word: _Word, words: list[str]) -> bool:
    """`NAME=(`: an array assignment, in front of any command word."""
    return all(_ASSIGNMENT.match(w) for w in words) and bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\+?=", word.text))


def _read(command: str) -> _Parsed | _Unreadable:
    """The simple commands and the substitutions in effect in `command`, or where reading
    it failed. Pure: nothing here runs, expands or looks anything up."""
    reader = _Reader(command)
    try:
        commands = reader.commands()
    except _Stop as stop:
        return _Unreadable(stop.what, stop.at)
    return _Parsed(tuple(commands), tuple(sorted(reader.subs, key=lambda t: t[1])))


_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
# Flags a leased push may carry besides the lease: they change what is printed or tracked,
# never what is overwritten. Anything else is refused by name.
_PUSH_HARMLESS = frozenset({"-u", "--set-upstream", "-q", "--quiet", "-v", "--verbose", "--porcelain"})
_PUSH_WIDE = frozenset({"--all", "--mirror", "--tags", "--delete", "-d", "--prune", "--follow-tags"})


def check_push(words: list[str], branch: str, lease_head: str) -> str:
    """"" if `git push <words>` is the one push `0035` R6 allows, else why not.

    `words` are the tokens after `push`. The one allowed shape is `origin <branch>` or
    `origin HEAD:<branch>`, carrying exactly one `--force-with-lease=<branch>:<lease_head>`
    with a full SHA. Pure: the branch and the head come from the app, never the session.
    """
    if not branch or not _FULL_SHA.match(lease_head or ""):
        return "no lease was fixed for this step, so it may not push"
    leases = []
    positional = []
    for token in words:
        if token in ("--force", "-f") or (token.startswith("-") and not token.startswith("--") and "f" in token[1:]):
            return "a push may not use --force: only --force-with-lease bound to the head this step began at"
        if token == "--force-with-lease":
            return "--force-with-lease needs a value: --force-with-lease=<branch>:<head this step began at>"
        if token.startswith("--force-with-lease="):
            leases.append(token.split("=", 1)[1])
            continue
        if token in _PUSH_WIDE:
            return f"a push may not use {token}: it reaches more than this unit's branch"
        if token.startswith("-"):
            if token not in _PUSH_HARMLESS:
                return f"a push may not use {token}"
            continue
        positional.append(token)
    if len(leases) != 1:
        return "a push must carry exactly one --force-with-lease=<branch>:<head this step began at>"
    if leases[0] != f"{branch}:{lease_head}":
        return f"the lease must be bound to {branch}:{lease_head}, the head this step began at"
    if positional not in (["origin", branch], ["origin", f"HEAD:{branch}"]):
        return f"a push may only name `origin {branch}` or `origin HEAD:{branch}`"
    return ""


def check_command(grant: Grant, command: str, lease: tuple[str, str] | None = None, unit: str = "") -> str:
    """"" if the command may run, else why not.

    **This is a best-effort reading of a shell command, and it is the weakest guard here.**
    `plan.md` Risk 3 says so: a first-word allowlist does not bound what `git` or `npm` can
    be told to do, and it cannot. What actually bounds the step is that the session runs
    with `cwd` set to the workspace and that writes are checked against it. Treat this as
    the thing that turns obvious mistakes into refusals, not as a sandbox.

    Since `0060` the line is read as bash reads it (`_read`), and checked in this order: a
    line that cannot be read, a substitution in effect, a redirect that writes, then every
    simple command. `unit` is the step's own unit, `NNNN_<slug>`: a redirect may write under
    a `/tmp` directory naming it (`_redirect_refused`). **That write is outside the write
    boundary `decide` keeps**, and nothing creates or removes the directory.
    """
    text = (command or "").strip()
    if not text:
        return "an empty command"
    parsed = _read(command)
    if isinstance(parsed, _Unreadable):
        return (
            f"this command could not be read as the shell reads it: {parsed.what} "
            f"at character {parsed.at + 1}; nothing was guessed"
        )
    if parsed.substitutions:
        # With substitution in play the first word no longer says what runs.
        token = parsed.substitutions[0][0]
        kind = {"$((": "arithmetic", "<(": "process", ">(": "process"}.get(token, "command")
        return f"{kind} substitution is not allowed: {token}"
    for simple in parsed.commands:
        for redirect in simple.redirects:
            reason = _redirect_refused(redirect, unit)
            if reason:
                return reason
    for simple in parsed.commands:
        reason = _check_simple(grant, simple, lease)
        if reason:
            return reason
    return ""


def _check_simple(grant: Grant, simple: _Simple, lease: tuple[str, str] | None) -> str:
    """"" if one simple command may run, else why not."""
    all_words = list(simple.words)
    if not all_words:
        # Redirects alone: `_redirect_refused` has already read them.
        return ""
    # `VAR=x cmd` puts the assignment first; step over any of them. One standing alone is
    # still refused by its name.
    k = 0
    while k < len(all_words) - 1 and _ASSIGNMENT.match(all_words[k]):
        k += 1
    word = all_words[k]
    if _ASSIGNMENT.match(word):
        # Still refused, as before `0060` — but by what it is. Named by the last `/` of its
        # value it read `this step may not run 'coscc-fb0599d12eeb'` for `S=/home/…/coscc-
        # fb0599d12eeb`, a name that is no command at all (R6).
        name = _ASSIGNMENT.match(word).group(0)
        return f"a command that only assigns ({name}…) is not allowed: this step runs only the commands it names"
    if simple.expanded[k]:
        # `0060` R4: what runs is whatever the variable holds, which this reader cannot know.
        return f"the command's name is a variable ({word}): this step runs only names it can read"
    base = word.rsplit("/", 1)[-1]
    if base not in grant.commands:
        return f"this step may not run {base!r}"
    if base in ("git", "gh") and (grant.denied or grant.push_needs_lease or grant.push_no_force):
        # `0060` R4: `gh $P merge` is `gh pr merge` once `P=pr`. Refused by the variable's
        # name, since the value is not known here.
        for other, expanded in zip(all_words, simple.expanded):
            if expanded:
                return f"this step may not pass {other} to {base}: a variable can hide a refused word"
    raw = all_words[k + 1:]
    words = _words(base, raw)
    for prefix, reason in grant.denied:
        if words[: len(prefix)] == prefix:
            return f"this step may not run {' '.join(prefix)!r}: {reason}"
    if base == "gh" and grant.denied and any(_MERGE_ENDPOINT.search(t) for t in words):
        # `gh api -X PUT repos/o/r/pulls/7/merge` is the same merge by another road.
        return "this step may not call the merge endpoint: merging is the ship stage's"
    if base == "gh" and grant.push_no_force and any(_UPDATE_BRANCH_ENDPOINT.search(t) for t in all_words):
        # `0041` review round 1, F1: `gh pr update-branch` by the API, REST or GraphQL.
        return f"this step may not call the update-branch endpoint: {_INTEGRATION_IS_NOT_PRS}"
    # Read on the text as written, quotes kept, as before `0060` — and on the words with
    # their quotes removed too, so `al\ias.p` or `$'\x61lias.p'` is not a way round it.
    config_road = bool(_GIT_CONFIG_ROAD.search(simple.source)) or any(
        _GIT_CONFIG_ROAD.search(" " + t) for t in all_words
    )
    if base == "git" and grant.push_needs_lease and config_road:
        return "this step may not define a git alias, an include or GIT_CONFIG_*: it can rename `push` past the lease"
    if base == "git" and grant.push_no_force and config_road:
        # `0041` review round 1, F1: `git -c alias.r=rebase r main`, or `git config
        # alias.p push` and then `git p --force`, renames the refused words.
        return f"this step may not define a git alias, an include or GIT_CONFIG_*: it can rename a refused command; {_INTEGRATION_IS_NOT_PRS}"
    if base == "git" and grant.push_needs_lease and _may_be_push(raw):
        # `0035` R6. `push` must be the first word after `git`, so a `-C dir` or
        # `-c k=v` in front cannot hide what it pushes.
        if raw[0] != "push":
            return "a push must be spelled `git push …`, with nothing between"
        branch, head = lease if lease else ("", "")
        reason = check_push(raw[1:], branch, head)
        if reason:
            return reason
    if base == "git" and grant.push_no_force and _may_be_push(raw):
        forced = _forces(raw[raw.index("push") + 1:])
        if forced:
            return f"a push may not use {forced}: {_INTEGRATION_IS_NOT_PRS}"
    return ""


# Redirection into a file, which is a write that no write-tool check would ever see.
# Measured on 2026-09-22: a real `impl` step was refused four times, and one of those was `Write` aimed at the working
# folder above the workspace — so the boundary matters and a shell that can reach past it
# matters just as much. A redirect writes a file without any write tool being called, so
# the path check in `decide` never sees it; the step has `Write` and `Edit` for files.
#
# `0060` `intent.md ## Answers, câu 3` names what is safe: "`> /dev/null`, `2>&1`, và ghi
# vào thư mục tạm riêng của bước (dưới /tmp, tên có unit). Ghi vào file trong worktree vẫn
# bị chặn — phải dùng công cụ Write/Edit."
_READ_REDIRECTS = frozenset({"<", "<<", "<<-", "<<<", "<&"})
_DESCRIPTOR = re.compile(r"\d*-?")
# Copied from `coscc/units.py:53`, not imported: this module depends on no other of the app's.
_UNIT_NAME = re.compile(r"\d{4}_[a-z0-9]+(?:-[a-z0-9]+)*")


def _redirect_refused(redirect: _Redirect, unit: str) -> str:
    """"" if the redirect may happen, else why not."""
    if redirect.op in _READ_REDIRECTS:
        return ""
    if redirect.op == ">&" and redirect.target and _DESCRIPTOR.fullmatch(redirect.target):
        # `2>&1`, `>&2`, `3>&-`: between descriptors, touching no file. `>&word` is `&>word`.
        return ""
    allowed = "a redirect may go only to /dev/null or to another descriptor (2>&1)"
    if _UNIT_NAME.fullmatch(unit or ""):
        allowed = (
            "a redirect may go only to /dev/null, to another descriptor (2>&1), "
            f"or under a /tmp/<directory naming {unit}>/"
        )
    else:
        allowed += ": this step has no /tmp directory of its own"
    if redirect.op == "<>":
        return f"redirecting into a file is not allowed: {redirect.target} (<> opens it for writing) — use the write tools; {allowed}"
    if redirect.target == "/dev/null" and not redirect.expanded:
        return ""
    if _in_step_tmp(redirect, unit):
        return ""
    return f"redirecting into a file is not allowed: {redirect.target} — use the write tools; {allowed}"


def _in_step_tmp(redirect: _Redirect, unit: str) -> bool:
    """Whether the target, symlinks resolved now, lies below a directory directly under
    `/tmp` whose name carries `unit`, and is not that directory itself.

    Resolved when `decide` runs, not when bash opens the file: a directory swapped for a
    symlink in between is not seen (`0060 plan.md` Risk 2). `/tmp` is shared, so anyone can
    make a directory carrying a unit's name before the step does (`0060 spec.md` C1).
    """
    from pathlib import Path

    # `"" in name` is always true, so a step with no unit must never get this far.
    if not _UNIT_NAME.fullmatch(unit or ""):
        return False
    if redirect.expanded or not redirect.target.startswith("/"):
        return False
    try:
        tmp = Path("/tmp").resolve()
        rel = Path(redirect.target).resolve().relative_to(tmp)
    except (OSError, RuntimeError, ValueError):
        return False
    return len(rel.parts) >= 2 and unit in rel.parts[0]


def _forces(words: list[str]) -> str:
    """The first token after `push` that overwrites what is on the remote, or ""."""
    for token in words:
        if token in ("--force", "-f", "--force-with-lease", "--force-if-includes"):
            return token
        if token.startswith("--force-with-lease="):
            return "--force-with-lease"
        # A cluster of short flags carrying `f`, read as `check_push` reads it.
        if token.startswith("-") and not token.startswith("--") and "f" in token[1:]:
            return token
        if token.startswith("+"):
            return f"the forced refspec {token}"
    return ""


# Flags `gh` reads a value after, anywhere on the line. Their values are dropped with them,
# so `gh -R o/r pr merge` and `gh pr --repo o/r merge` read as `gh pr merge` (`0015` review
# round 1, F1). Every other `-x` / `--x` / `--x=v` is dropped alone.
_GH_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})
# The same for `git`, in front of the subcommand: `git -C . rebase main` must read as
# `git rebase main` (`0041` R3). Only the two git itself reads a separate value after.
_GIT_VALUE_FLAGS = frozenset({"-C", "-c"})
_MERGE_ENDPOINT = re.compile(r"pulls/[^/\s]+/merge\b")
_UPDATE_BRANCH_ENDPOINT = re.compile(r"pulls/[^/\s]+/update-branch\b|updatePullRequestBranch")


def _may_be_push(raw: list[str]) -> bool:
    """Whether `git <raw>` could be a push: a `push` with only options, or an option's
    value, in front of it. `git log --grep push` is not one (`0035` review round 1, F3).

    Leans towards yes: `git --no-pager log push` reads as one, since which of git's
    options take a value is not known here.
    """
    if "push" not in raw:
        return False
    before = raw[: raw.index("push")]
    return all(t.startswith("-") or (i and before[i - 1].startswith("-")) for i, t in enumerate(before))


def _words(base: str, rest: list[str]) -> tuple[str, ...]:
    """The command and its positional words, flags removed — what a deny prefix is matched on.

    Still a reading of tokens, not of what the program will do: an alias defined before
    the step, or `node -e` spawning `gh`, is not seen. `.claude/CLAUDE.md` says so. The
    `integrate` and `pr` grants also refuse an alias made during the step (`_GIT_CONFIG_ROAD`).
    """
    out = [base]
    skip = False
    for token in rest:
        if skip:
            skip = False
            continue
        if token.startswith("-"):
            skip = (base == "gh" and token in _GH_VALUE_FLAGS) or (
                base == "git" and len(out) == 1 and token in _GIT_VALUE_FLAGS
            )
            continue
        out.append(token)
    return tuple(out)


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
    read_also: tuple[str, ...] = (),
    lease: tuple[str, str] | None = None,
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

    Since `0020` the same two roots bound `Read`, `Glob` and `Grep` too — see below.

    `0035`: `read_also` widens **reading only**, by an explicit list of paths the app built
    from the data root (Gebo's own unit folder and the intent/spec/plan of the related
    units). Writing keeps its roots. `lease` is `(branch, head)`, which a grant with
    `push_needs_lease` binds every `git push` to.
    """
    if tool not in grant.tools:
        # Covers MCP tools by construction: their names are never in a grant.
        return f"this step was not granted {tool}"

    if tool in EXEC_TOOLS:
        # `0060`: the unit's name opens a `/tmp` directory to redirects. Writes there are
        # outside the boundary the write tools are held to below.
        from pathlib import Path

        unit = Path(unit_dir).name if unit_dir else ""
        reason = check_command(grant, str(tool_input.get("command", "")), lease, unit)
        if reason:
            return reason

    if tool in WRITE_TOOLS:
        # Relative paths resolve against the app's own directory here, as they always have.
        # Changing that would widen writing in one corner, and nothing asked for it
        # (`0020` plan, step 1).
        roots, reason = _roots(workspace, unit_dir)
        if reason:
            return reason
        for raw in _paths_in(tool_input):
            if not _inside(raw, roots, None):
                return f"writing outside the workspace is not allowed: {raw}"

    if tool in READ_TOOLS:
        # `0020` `spec.md` `## Answers`, answer 2: reading is held to the same two roots as
        # writing. Before this a step that could `Read` could read anything the app's own
        # process could — `~/.ssh`, `~/.config/coscc/env`, every other unit in the store.
        # Relative paths resolve against the workspace, because that is the session's `cwd`
        # and so what the tool itself will read.
        roots, reason = _roots(workspace, unit_dir)
        if reason:
            return reason
        from pathlib import Path

        for extra in read_also:
            try:
                roots.append(Path(extra).expanduser().resolve())
            except OSError:
                return "a path this step may read could not be resolved"
        for raw in _read_paths_in(tool, tool_input):
            if raw is _TRAVERSAL:
                return (
                    "reading outside the workspace is not allowed: "
                    f"{tool_input.get('pattern')}"
                )
            if not _inside(raw, roots, roots[0]):
                return f"reading outside the workspace is not allowed: {raw}"
    return ""


def _roots(workspace: str, unit_dir: str | None) -> tuple[list, str]:
    """The directories a step may touch, resolved: the workspace, then its own unit."""
    from pathlib import Path

    roots = []
    for candidate in (workspace, unit_dir):
        if not candidate:
            continue
        try:
            roots.append(Path(candidate).expanduser().resolve())
        except OSError:
            return [], "the workspace path could not be resolved"
    if not roots:
        return [], "the workspace path could not be resolved"
    return roots, ""


def _inside(raw: str, roots: list, base) -> bool:
    """Whether `raw`, once resolved (symlinks included), lies in one of `roots`.

    `base` is what a relative path is resolved against; `None` means the process's own
    directory, which is what the write check has always used.
    """
    from pathlib import Path

    try:
        path = Path(raw).expanduser()
        if base is not None and not path.is_absolute():
            path = base / path
        target = path.resolve()
    except (OSError, RuntimeError):
        return False
    return any(target == root or root in target.parents for root in roots)


# Stands in for a path when a `Glob` pattern climbs with `..`: there is no fixed prefix to
# check, and the pattern itself says it is leaving.
_TRAVERSAL = object()
_GLOB_CHARS = "*?[{"


def _read_paths_in(tool: str, tool_input: dict) -> list:
    """Every path a read tool was given, plus the fixed prefix of an absolute `Glob` pattern.

    No `path` at all is fine: the SDK then searches the session's `cwd`, which is the
    workspace (`coscc/runner.py`). Reading a pattern this way is best-effort — `0020`
    plan, Risk 3 — and `TheReadBoundaryIsNotASandbox` below pins what it does not see.
    """
    out: list = list(_paths_in(tool_input))
    pattern = tool_input.get("pattern")
    if tool == "Glob" and isinstance(pattern, str) and pattern:
        if ".." in pattern.replace("\\", "/").split("/"):
            out.append(_TRAVERSAL)
        elif pattern.startswith(("/", "~")):
            fixed = []
            for part in pattern.split("/"):
                if any(c in part for c in _GLOB_CHARS):
                    break
                fixed.append(part)
            out.append("/".join(fixed) or "/")
    return out
