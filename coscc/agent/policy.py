"""What a step is allowed to do, keyed on the stage it runs.

`Config` is the app's default (chat only, no tools); this table says what a board step may do
instead.

- Deny by default: a stage this table does not name gets `Grant()`: no tools, one turn, no budget.
- Pure: nothing here reads the environment, the store or a request.
- The tools go with the stage, not the mode; the mode is recorded in the journal and decides
  nothing here.

`Grant.tools` is not the whole enforcement: a list handed to the SDK covers the built-in set
only and MCP tools walk past `tools=[]`. So the grant also carries what `Runner` must refuse
at the moment of use, in `can_use_tool`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal, get_args

from coscc.agent.labels import NOVEL

# Stages whose artifact is prose. The app writes these from the text the session returns, so
# the session needs no ability to write. `ship` is not one: it runs `gh pr merge`.
ProseStage = Literal["idea", "intent", "spec", "plan", "review"]
PROSE_STAGES: tuple[ProseStage, ...] = get_args(ProseStage)


@dataclass(frozen=True)
class Grant:
    """What one step may do. The default is the locked position."""

    tools: tuple[str, ...] = ()
    # Commands the step may run, matched on the first word of the command line. Empty means none.
    commands: tuple[str, ...] = ()
    # Chosen, not measured: they turn a loop that will not end into a named failure.
    max_turns: int = 1
    max_budget_usd: float = 0.0
    # Whether the app writes the artifact from the reply (prose stages) or the session writes it
    # itself (stages that touch code).
    app_writes_artifact: bool = True
    # Shown on the page before the step is started: a capability from the machine's own
    # configuration is invisible in an app, so it is said where the button is.
    warning: str = ""
    # Command prefixes refused even though their first word is allowed, each with the reason
    # given. Matched on the leading tokens of a segment: the plain spelling and nothing cleverer.
    denied: tuple[tuple[tuple[str, ...], str], ...] = ()
    # Every `git push` must carry `--force-with-lease` bound to the head the pull request had
    # when the step began, and name the unit's own branch. The lease is per run, so it reaches
    # `decide` as `lease`, not through the grant.
    push_needs_lease: bool = False
    # Whether the session is handed `submit` (`coscc/units/submit.py`), the one tool beyond this
    # grant's list `decide` lets through. It writes nothing and runs nothing, and is not in
    # `tools`: `--tools` names the built-in set, and an SDK server's tool reaches the session anyway.
    submits: bool = False
    # Full names of MCP tools a feature's server holds, derived by `coscc/hooks.py`. Not in `tools`
    # (`--tools` names the built-in set) and not in `opens_anything`.
    mcp: tuple[str, ...] = ()
    # Paths no word of a command may point into (`protected_paths`), whatever `commands` holds.
    # Empty in the table: the runner fills it from the data root when a step runs.
    protected: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in self.mcp:
            m = MCP_NAME.fullmatch(name)
            if m is None or m.group(1) == "cos":
                raise ValueError(f"not a feature's MCP tool name: {name!r}")

    @property
    def opens_anything(self) -> bool:
        return bool(self.tools or self.commands)


# Tools that only read, listed separately so the write set is short.
READ_TOOLS = ("Read", "Glob", "Grep")
# `coscc/units/submit.py`'s `NAME`, spelled here so this module imports nothing of it.
SUBMIT_TOOL = "mcp__cos__submit"
# A feature's MCP tool, `mcp__<server>__<name>`; the server is captured. `cos` is the app's own.
MCP_NAME = re.compile(r"mcp__([a-z][a-z0-9-]*)__[a-z][a-z0-9_]*")
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")
EXEC_TOOLS = ("Bash",)
# Hands work to one of `SUBAGENTS` inside the same session. Every tool call a helper makes
# reaches the same `decide` and grant as the session's own, with its `agent_id`, and its spend
# is in the session's cost. `Agent` itself never reaches `decide` (it is not asked about in the
# `default` mode): `coscc/agent/helpers.py`'s hook holds it.
AGENT_TOOL = "Agent"
# The SDK's own message between the agents of one session; the same hook holds where it goes.
SEND_MESSAGE = "SendMessage"
# The kernel's own tool listing the helpers of the run, on `submit`'s server.
PEERS_TOOL = "mcp__cos__peers"
# The only `git` subcommands a helper may run: only the leading session commits.
HELPER_GIT = ("status", "diff", "log", "show", "blame")
# Named helpers with a bounded report, so big reads and test output stay out of the main
# context. `sessions._options` turns each into an `AgentDefinition`. A helper gets only the
# built-in tools its session's grant holds, so the grant carrying `Agent` lists `worker`'s.
SUBAGENTS = {
    "scout": {
        "description": "Maps where things are in the named files. Read-only.",
        "prompt": (
            "You are given files and a question. Answer with a short map of `path:line` "
            "entries, one per line, each with a few words on what is there; at most 30 "
            'lines. Write "unsure" beside anything you did not confirm. Never edit.'
        ),
        "tools": list(READ_TOOLS),
        "model": "sonnet",
    },
    "worker": {
        "description": (
            "Does one step of the plan's ## Parallelization: edits only that step's paths, runs "
            "only its tests, never commits."
        ),
        "prompt": (
            "You are given one step of the plan: its name, its paths and what to report. Edit "
            "only those paths and run only the tests of that step; the leading session runs the "
            "plan's verification. Run git only to read (status, diff, log, show, blame): the "
            "leading session commits."
        ),
        "tools": list(READ_TOOLS + ("Write", "Edit") + EXEC_TOOLS + (SEND_MESSAGE, PEERS_TOOL)),
        "model": "sonnet",
    },
}

# Commands `impl` may run, matched on the first word of every segment of the command line.
# Deliberately short: enough to check its own work, not a shell.
IMPL_COMMANDS = (
    "git",
    "npm",
    "node",
    "uv",
    "python",
    "python3",
    "pytest",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "rg",
    "find",
    "diff",
    "mkdir",
    "true",
    "echo",
    "printf",
    "test",
    "which",
    "pwd",
    "sort",
    "uniq",
)

# No session merges: a pull request is merged only by the PR machine
# (`coscc/github/prmachine.py`), and no stage has a `pr` or `ship` grant.
#
# Matched on the words left once flags are removed (`_words` below), so a `-R o/r` in front does
# not walk past it. `gh alias set` is refused too: a defined alias can run under another name.
MERGE_IS_SHIPS = (
    (("gh", "pr", "merge"), "merging is the ship stage's"),
    (("gh", "alias", "set"), "an alias is a merge under another name; merging is the ship stage's"),
)

# Gebo, the integration step. Not a stage: it runs outside the loop, between `pr` and `ship`,
# when a person presses the button, but is keyed in the same table so it starts locked.
# `impl`'s commands (resolving a conflict means running the tests), plus `gh` to read the pull
# request and its CI.
INTEGRATE_COMMANDS = IMPL_COMMANDS + ("gh",)

INTEGRATE_WARNING = (
    "Integrating runs `git` and `gh` with the GitHub login already on this machine, and "
    "force-pushes (with a lease) to this unit's branch. That login reaches every repository "
    "its account can reach, not just this workspace. What it resolves is an agent's word, "
    "not a person's approval."
)

# Rebase only. `git merge` and `git pull` would bring `main` in by merging, and
# `gh pr update-branch` would move the head on GitHub's side under the lease: the push has
# exactly one road.
INTEGRATE_DENIED = MERGE_IS_SHIPS + (
    (("git", "merge"), "integration is by rebase, never by merge"),
    (("git", "pull"), "integration is by rebase, never by merge"),
    (("gh", "pr", "update-branch"), "the head the push is leased to would move under it"),
    # Roads to the branch that are not `git push` and so never meet the lease: `gh api` reaches
    # `git/refs` with `force=true`. `gh pr view` and `gh pr checks` stay open.
    (
        ("gh", "api"),
        "it can move the branch on GitHub with no lease; read with `gh pr view` or `gh pr checks`",
    ),
    (("gh", "repo", "sync"), "it can force the branch on GitHub with no lease"),
    (("gh", "extension"), "an extension is a command this grant cannot read"),
    (
        ("git", "send-pack"),
        "it pushes without the lease; push only with `git push --force-with-lease`",
    ),
    (
        ("git", "http-push"),
        "it pushes without the lease; push only with `git push --force-with-lease`",
    ),
)

# An alias or an included config file made during the step renames `push` into a word
# `_may_be_push` never sees (`git -c alias.p=push p`, `git config alias.p push`, or the same
# through `GIT_CONFIG_*`). Matched on the whole segment, assignments included, so a commit
# message naming one is refused too, with this reason.
_GIT_CONFIG_ROAD = re.compile(
    r"(?:^|[\s='\"])(?:alias|include|includeif)\.|\bGIT_CONFIG", re.IGNORECASE
)

# ᛈ Perthro, the spike step. `impl`'s commands without `git`: `git -C <worktree> commit` is the
# shortest road for throwaway code into the unit's branch. Everything else is kept, because
# measuring means running things.
SPIKE_COMMANDS = tuple(c for c in IMPL_COMMANDS if c != "git")

SPIKE_WARNING = (
    "This step runs arbitrary code (`python`, `node`, `npm`, `uv`) under this process's "
    "user, in a throwaway directory the app deletes afterwards. Nothing is a sandbox: a "
    "write outside that directory is caught only inside the unit's worktree, where it "
    "fails the step, and is not undone. Anywhere else, `~` included, it is not seen."
)

# Said on the Backlog panel above the button, before it is pressed.
ESTIMATE_WARNING = (
    "Proposing estimates opens one paid session (1 turn, $2.00 ceiling) on the model of the "
    "Settings row `estimate`. Whoever holds the password or a live session can press it, and "
    "can rewrite any estimate, relation or the shortlist under any name they type."
)

# Only stages that appear here get anything. The rest (`idea`, `intent`, any stage invented
# later) falls through to `Grant()`. Keyed by stage alone.
GRANTS: dict[str, Grant] = {
    # The one entry whose ceilings are measured rather than chosen. Fifty turns ended three of
    # four `impl` steps mid-work (51/50 turns, $1.8-2.5, no `impl.md`); the one that finished did
    # so because earlier runs had done the work. 120 is about twice the highest real attempt,
    # and the budget goes with it: at the measured $0.047/turn a 120-turn step lands near $5.6,
    # so a $5 cap would only move the same premature stop to the other ceiling.
    "impl": Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS + (AGENT_TOOL, SEND_MESSAGE),
        commands=IMPL_COMMANDS,
        max_turns=120,
        max_budget_usd=8.0,
        app_writes_artifact=False,
    ),
    # `plan` reads, and only reads: `write-plan` requires every path under `## Files that change`
    # to be verified before it is written down, and without read tools a plan names paths it
    # never saw. No write tools and no commands: the app still writes `plan.md` from the reply,
    # which stops a plan authoring itself, and `beyond_reading` keeps that true if this widens.
    "plan": Grant(
        tools=READ_TOOLS,
        # Chosen, not measured: 20 cut a plan mid-read at the ceiling ($1.08, nothing returned)
        # as what it had to read grew. The `turns` the app records is not the counter `max_turns`
        # stops on, so there is no number to set this from. Forty doubles the ceiling that was
        # hit; the budget moves with it so the other limit does not become the real one.
        max_turns=40,
        max_budget_usd=4.0,
    ),
    # `spec` reads, and only reads, for the reason `plan` does: `write-spec` requires every
    # figure to name its source and every citation a path and line range. No write tools and no
    # commands; the app writes `spec.md` from the reply. Both ceilings are `plan`'s, not measured.
    "spec": Grant(
        tools=READ_TOOLS,
        max_turns=40,
        max_budget_usd=4.0,
    ),
    # The first grant that both holds commands and has the app write its artifact from the
    # reply. `beyond_reading` guards only `PROSE_STAGES`, which this is not, so `policy_test`
    # pins `git` out of `commands` instead. Writing is held to the session's `cwd`, a throwaway
    # directory `service.steps.run_step` makes and removes; the worktree and the unit are read
    # through `read_also`.
    #
    # Ceilings chosen, not measured. Spikes that finished were recorded at 36-44 turns and 40
    # turns / $4.0 stopped several before writing `spike.md`. The recorded `turns` is not the
    # counter `max_turns` stops on (see `plan`), so 80 doubles the ceiling that was hit. Runs
    # cost $0.032-0.055 a recorded turn, so 80 turns is about $2.6-4.4, and $4 would stop the
    # dearer ones before the turn ceiling; $8.0 is `impl`'s.
    "spike": Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS,
        commands=SPIKE_COMMANDS,
        max_turns=80,
        max_budget_usd=8.0,
        app_writes_artifact=True,
        warning=SPIKE_WARNING,
    ),
    # A separate agent session reviews the open pull request, before the merge. It reads and
    # only reads, like `plan`: the app still writes `review.md` from the reply. It cannot run
    # `git diff`, so it sees the working tree and `impl.md`, not the diff.
    "review": Grant(
        tools=READ_TOOLS,
        # Chosen, not measured: 20/$2.0 stopped review sessions before they wrote a round. A
        # review that still stops at it gets one closing turn from the app (`runner.Runner.run`),
        # which this budget does not bound: the CLI compares the session's whole cost after the
        # turn has run.
        max_turns=40,
        max_budget_usd=4.0,
    ),
    # No `pr` and no `ship` entry: both are the PR machine's, with no session
    # (`coscc/github/prmachine.py`), so a step of either falls through to the locked `Grant()`.
    # Gebo's ceilings are chosen, not measured: impl's (120 turns, $8), to lower once real runs
    # are recorded.
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
    # Not a stage either: the backlog's *Propose estimates* button, one session per press. No
    # tools and no commands, like `idea` and `intent`; it hands its estimate back through
    # `submit` (`SUBMITTING_SESSIONS`) and the reply is not read. Ceilings chosen, not measured.
    "estimate": Grant(
        max_turns=1,
        max_budget_usd=2.0,
        warning=ESTIMATE_WARNING,
    ),
}

# The ceilings a step gets when its plan's label is `novel` (`coscc/agent/labels.py`), as
# `(max_turns, max_budget_usd)`; everything else about the grant stays the stage's own. A stage
# not named here runs the same grant whatever its label.
#
# 250 is chosen, not measured, and $16 is 2 x $8.0. The dearest turn measured $0.0419 across
# `novel` runs (250 turns, $10.48) and $0.0568 across all `impl` runs ($14.21), so $16 leaves a
# thin margin. A `novel` impl that stops on the budget is not escalated.
NOVEL_CEILINGS: dict[str, tuple[int, float]] = {
    "impl": (250, 16.0),
}


def beyond_reading(grant: Grant) -> tuple[str, ...]:
    """What a grant carries that a prose stage may not: anything beyond reading.

    A prose stage's artifact is written by **the app** from the reply. A step holding write
    tools could write its own artifact behind the app's back, and one holding commands is not
    a prose stage at all; reading is neither. The guard in `coscc/runner/step.py` asks
    this rather than whether the grant is empty ("no tools" is not "no capability").
    """
    return tuple(t for t in grant.tools if t not in READ_TOOLS) + tuple(grant.commands)


# The stages whose run hands back an object through `submit`: a stage result, or `review`'s
# round. `coscc/units/submit.py` holds the same stages as `STAGE_RESULT` and `ROUND`;
# `policy_test` pins the two. A set, not the loop's order: that is `cos.mjs`'s alone.
SUBMITTING = ("idea", "impl", "intent", "plan", "review", "spec", "spike")
# The fewest turns such a step gets: a call to `submit` ends a turn, and a refused object is
# submitted again after one more turn, so four holds a call, a refusal, a second call and the
# reply. Chosen, not measured.
SUBMIT_TURNS = 4
# The sessions that are no stage and hand back an object through `submit`: Gebo and the
# estimate, `coscc/units/submit.py`'s `SESSIONS`.
SUBMITTING_SESSIONS = ("estimate", "integrate")


def grant_for(stage: str) -> Grant:
    """The grant for one step. A stage the table does not name is locked, not open.

    A stage in `SUBMITTING` or a session in `SUBMITTING_SESSIONS` gets `submits`, and at least
    `SUBMIT_TURNS` turns.
    """
    grant = GRANTS.get(stage, Grant())
    if stage not in SUBMITTING + SUBMITTING_SESSIONS:
        return grant
    return replace(grant, submits=True, max_turns=max(grant.max_turns, SUBMIT_TURNS))


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


# The one stage a workspace's `allow` and `block` change, and the pref holding them:
# `{workspace key: {"allow": [...], "block": [...]}}`, written by `coscc/service/workspaces.py`.
LISTED_STAGE = "impl"
GRANTS_PREF = "grants.impl"
# What `allow` and `block` may name: a command, never a path. Chosen, not measured.
COMMAND_NAME = re.compile(r"[A-Za-z0-9._+-]{1,64}")


def lists_of(stored: object, key: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(allow, block)` of one workspace in the pref; a name `COMMAND_NAME` refuses is dropped."""
    entry = stored.get(key) if isinstance(stored, dict) else None
    if not isinstance(entry, dict):
        return (), ()

    def names(field: str) -> tuple[str, ...]:
        raw = entry.get(field)
        if not isinstance(raw, list):
            return ()
        return tuple(
            dict.fromkeys(n for n in raw if isinstance(n, str) and COMMAND_NAME.fullmatch(n))
        )

    return names("allow"), names("block")


def with_lists(grant: Grant, allow: tuple[str, ...], block: tuple[str, ...]) -> Grant:
    """`grant` with `allow` added to its commands and `block` taken out; `block` wins. Only
    `commands` changes: `denied`, `protected` and every other rule of `check_command` stay."""
    merged = dict.fromkeys((*grant.commands, *allow))
    return replace(grant, commands=tuple(c for c in merged if c not in block))


def protected_paths(data_root: str, config_home: str, home: str = "") -> tuple[str, ...]:
    """The vault's store, `<data root>/vault`, and the app's own config, `<config home>/coscc`
    (`env`, `vault.key`), as a command word may spell them: as given, symlinks resolved, and
    below `home` with `~`, `$HOME` or `${HOME}` in front. An empty `config_home` protects the
    store alone.

    The kernel keeps this list, not the feature, so `Bash` is held to it with the vault off too.
    """
    import os
    from pathlib import Path

    dirs = [os.path.join(data_root, "vault")]
    if config_home:
        dirs.append(os.path.join(config_home, "coscc"))
    top = os.path.normpath(home) if home else ""
    out: list[str] = []
    for d in dirs:
        for p in (os.path.normpath(d), str(Path(d).resolve())):
            out.append(p)
            if top and p.startswith(top + "/"):
                rel = p[len(top) + 1 :]
                out += [f"~/{rel}", f"$HOME/{rel}", f"${{HOME}}/{rel}"]
    return tuple(dict.fromkeys(out))


# --- deciding one call -------------------------------------------------------
#
# The list handed to the SDK is not enough on its own: `--tools` names the built-in set only,
# and MCP tools reach a `tools=[]` session. A callback sits on the path every call takes.

# A step's session is closed once its turn ends, so a command left running in the background is
# one nobody reads the end of. The words are the ones the model was refused with, after which
# it ran the command again in the foreground, same turn.
BACKGROUND_REFUSAL = (
    "this session ends when your turn ends and nothing wakes it when a background command "
    "finishes; run the command in the foreground"
)

# The line is read the way bash reads it, not split as raw text (`;`, `|` and `&&` inside quotes
# or heredoc bodies, and `$(` inside single quotes, must not count). Bash's own rules, copied: a
# board step's `Bash` runs `bash -c "... eval '<command>'"`, bash 5.3, `extglob` off. Anything
# this reader is not sure of is `_Unreadable`, and an unreadable line is refused, never guessed.


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
    # Where each lone `&` stands: a command it ends runs in the background.
    background: tuple[int, ...] = ()


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
        self.amps: list[int] = []

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

    def commands(self, opened: int | None = None) -> list[_Simple]:  # noqa: C901, PLR0915 - still to split
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
                out.append(
                    _Simple(s[start:j].strip(), tuple(words), tuple(flags), tuple(redirects))
                )
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
                        raise _Stop(
                            f"a redirect ({pending[0]}) with no target", self.at(pending[2])
                        )
                    pending = (op, "", amp)
                else:
                    if self.char(j) != "&":
                        self.amps.append(self.at(self.i))
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
                    current = word if word is not None else _Word(self.i)
                    word = current
                    self.subs.append((c + "(", self.at(self.i)))
                    current.expanded = True
                    self.i = j + 1
                    self.commands(opened=self.i - 2)
                    continue
                op, self.i = self.redirect_op(c, j)
                fd = ""
                if (
                    pending is None
                    and word is not None
                    and word.text.isdigit()
                    and not word.quoted
                    and not word.expanded
                ):
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
                    raise _Stop(
                        f"a here-document ({heredocs[0][0]}) with no line to end it",
                        self.at(heredocs[0][3]),
                    )
                return out
            else:
                # `(` and `)` anywhere else are kept as text: bash refuses the line as a
                # syntax error, so nothing runs, and the words around them are still read.
                current = word if word is not None else _Word(self.i)
                word = current
                self.part(current)
        if opened is not None:
            raise _Stop(f"an unclosed {s[opened : opened + 2]}", self.at(opened))
        end_command(self.n)
        if heredocs:
            raise _Stop(
                f"a here-document ({heredocs[0][0]}) with no line to end it",
                self.at(heredocs[0][3]),
            )
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
                    raise _Stop(
                        f"a here-document ({delimiter}) with no line to end it", self.at(opened)
                    )
                nl = s.find("\n", self.i)
                end = self.n if nl < 0 else nl
                line = s[self.i : end]
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
                        self.amps.extend(body.amps)
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
            word.buf.append(s[self.i + 1 : close])
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
            word.buf.append(s[opened : self.i])
        elif c == "{":
            word.expanded = True
            self.i = j + 1
            self.brace(opened)
            word.buf.append(s[opened : self.i])
        elif c == "'" and not quoted:
            self.ansi(word, j + 1)
        elif c == '"' and not quoted:
            # `$"…"` is a translated string: to this reader, a double-quoted one.
            self.i = j
            self.double(word)
        elif c and _NAME_START.match(c) and (name := _NAME.match(s, j)):
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
        word.buf.append(s[opened : self.i])

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
                hexa = re.match(r"x([0-9A-Fa-f]{1,2})", s[j + 1 : j + 4])
                if hexa:
                    word.buf.append(chr(int(hexa.group(1), 16)))
                    j += 1 + len(hexa.group(0))
                    continue
                word.buf.append(s[j : j + 2])
                j += 2
                continue
            word.buf.append(c)
            j += 1


def _is_array_open(word: _Word, words: list[str]) -> bool:
    """`NAME=(`: an array assignment, in front of any command word."""
    return all(_ASSIGNMENT.match(w) for w in words) and bool(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\+?=", word.text)
    )


def _read(command: str) -> _Parsed | _Unreadable:
    """The simple commands and the substitutions in effect in `command`, or where reading
    it failed. Pure: nothing here runs, expands or looks anything up."""
    reader = _Reader(command)
    try:
        commands = reader.commands()
    except _Stop as stop:
        return _Unreadable(stop.what, stop.at)
    return _Parsed(
        tuple(commands), tuple(sorted(reader.subs, key=lambda t: t[1])), tuple(sorted(reader.amps))
    )


_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
# Flags a leased push may carry besides the lease: they change what is printed or tracked,
# never what is overwritten. Anything else is refused by name.
_PUSH_HARMLESS = frozenset(
    {"-u", "--set-upstream", "-q", "--quiet", "-v", "--verbose", "--porcelain"}
)
_PUSH_WIDE = frozenset(
    {"--all", "--mirror", "--tags", "--delete", "-d", "--prune", "--follow-tags"}
)


def check_push(words: list[str], branch: str, lease_head: str) -> str:
    """ "" if `git push <words>` is the one push allowed, else why not.

    `words` are the tokens after `push`. The one allowed shape is `origin <branch>` or
    `origin HEAD:<branch>`, carrying exactly one `--force-with-lease=<branch>:<lease_head>`
    with a full SHA. Pure: the branch and the head come from the app, never the session.
    """
    if not branch or not _FULL_SHA.match(lease_head or ""):
        return "no lease was fixed for this step, so it may not push"
    leases = []
    positional = []
    for token in words:
        if token in ("--force", "-f") or (
            token.startswith("-") and not token.startswith("--") and "f" in token[1:]
        ):
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


def check_command(
    grant: Grant, command: str, lease: tuple[str, str] | None = None, unit: str = ""
) -> str:
    """ "" if the command may run, else why not.

    **A best-effort reading of a shell command, and the weakest guard here**: a first-word
    allowlist does not bound what `git` or `npm` can be told to do. What bounds the step is that
    the session runs with `cwd` set to the workspace and writes are checked against it. Treat
    this as turning obvious mistakes into refusals, not as a sandbox.

    The line is read as bash reads it (`_read`) and checked in this order: a line that cannot
    be read, a lone `&`, a substitution in effect, a redirect that writes, then every simple
    command. `unit` is the step's own unit, `NNNN_<slug>`: a redirect may write under a `/tmp`
    directory naming it (`_redirect_refused`). **That write is outside the write boundary
    `decide` keeps**, and nothing creates or removes the directory.
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
    if parsed.background:
        # `&&`, `&>`, `&>>`, `>&`, `<&` and `|&` are read elsewhere and never land here.
        return f"`&` at character {parsed.background[0] + 1} runs a command in the background: {BACKGROUND_REFUSAL}"
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
        reason = _protected_refused(grant, simple)
        if reason:
            return reason
    for simple in parsed.commands:
        reason = _check_simple(grant, simple, lease)
        if reason:
            return reason
    return ""


def _protected_refused(grant: Grant, simple: _Simple) -> str:
    """Why a word or redirect target of `simple` points into one of `grant.protected`, or "".

    Read on the text, so `--key=/…/vault.key` counts; a relative path, a variable holding the
    path or a program that builds it is not seen.
    """
    import os

    for word in (*simple.words, *(r.target for r in simple.redirects)):
        for text in {word, os.path.normpath(word)} if word else ():
            for p in grant.protected:
                if re.search(re.escape(p) + r"(?:/|$)", text):
                    return f"this step may not touch {p}: it holds the app's secrets"
    return ""


def programs_of(command: str) -> tuple[str, ...]:
    """The program each simple command of the line runs, read as `check_command` reads it: the
    first word after any `NAME=value`, its directory dropped. `()` for a line it cannot read."""
    parsed = _read(command or "")
    if isinstance(parsed, _Unreadable):
        return ()
    out = []
    for simple in parsed.commands:
        words = [w for w in simple.words if not _ASSIGNMENT.match(w)]
        if words:
            out.append(words[0].rsplit("/", 1)[-1])
    return tuple(out)


def _check_simple(grant: Grant, simple: _Simple, lease: tuple[str, str] | None) -> str:
    """ "" if one simple command may run, else why not."""
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
    if assigned := _ASSIGNMENT.match(word):
        # Refused by what it is: named by the last `/` of its value it would read `this step may
        # not run 'coscc-fb0599d12eeb'` for `S=/home/.../coscc-fb0599d12eeb`, which is no command.
        name = assigned.group(0)
        return f"a command that only assigns ({name}…) is not allowed: this step runs only the commands it names"
    if simple.expanded[k]:
        # What runs is whatever the variable holds, which this reader cannot know.
        return f"the command's name is a variable ({word}): this step runs only names it can read"
    base = word.rsplit("/", 1)[-1]
    if base not in grant.commands:
        return f"this step may not run {base!r}"
    if base in ("git", "gh") and (grant.denied or grant.push_needs_lease):
        # `gh $P merge` is `gh pr merge` once `P=pr`. Refused by the variable's name, since the
        # value is not known here.
        for other, expanded in zip(all_words, simple.expanded):
            if expanded:
                return (
                    f"this step may not pass {other} to {base}: a variable can hide a refused word"
                )
    raw = all_words[k + 1 :]
    words = _words(base, raw)
    for prefix, reason in grant.denied:
        if words[: len(prefix)] == prefix:
            return f"this step may not run {' '.join(prefix)!r}: {reason}"
    if base == "gh" and grant.denied and any(_MERGE_ENDPOINT.search(t) for t in words):
        # `gh api -X PUT repos/o/r/pulls/7/merge` is the same merge by another road.
        return "this step may not call the merge endpoint: merging is the ship stage's"
    # Read on the text as written, quotes kept, and on the words with their quotes removed too,
    # so `al\ias.p` or `$'\x61lias.p'` is not a way round it.
    config_road = bool(_GIT_CONFIG_ROAD.search(simple.source)) or any(
        _GIT_CONFIG_ROAD.search(" " + t) for t in all_words
    )
    if base == "git" and grant.push_needs_lease and config_road:
        return "this step may not define a git alias, an include or GIT_CONFIG_*: it can rename `push` past the lease"
    if base == "git" and grant.push_needs_lease and _may_be_push(raw):
        # `push` must be the first word after `git`, so a `-C dir` or `-c k=v` in front cannot
        # hide what it pushes.
        if raw[0] != "push":
            return "a push must be spelled `git push …`, with nothing between"
        branch, head = lease if lease else ("", "")
        reason = check_push(raw[1:], branch, head)
        if reason:
            return reason
    return ""


# Redirection into a file, which is a write that no write-tool check would ever see: a redirect
# writes a file without any write tool being called, so the path check in `decide` never sees it.
#
# Safe: `> /dev/null`, `2>&1`, and writes to the step's own temp directory (under /tmp, its name
# carrying the unit). Writing a file in the worktree stays refused: use Write/Edit.
_READ_REDIRECTS = frozenset({"<", "<<", "<<-", "<<<", "<&"})
_DESCRIPTOR = re.compile(r"\d*-?")
# Copied from `coscc/units/__init__.py:53`, not imported: this module depends on no other of the app.
_UNIT_NAME = re.compile(r"\d{4}_[a-z0-9]+(?:-[a-z0-9]+)*")


def _redirect_refused(redirect: _Redirect, unit: str) -> str:
    """ "" if the redirect may happen, else why not."""
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
    """Whether the target, symlinks resolved now, lies below a directory directly under `/tmp`
    whose name carries `unit`, and is not that directory itself.

    Resolved when `decide` runs, not when bash opens the file: a directory swapped for a symlink
    in between is not seen. `/tmp` is shared, so anyone can make a directory carrying a unit's
    name before the step does.
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
    except OSError, RuntimeError, ValueError:
        return False
    return len(rel.parts) >= 2 and unit in rel.parts[0]


# Flags `gh` reads a value after, anywhere on the line. Their values are dropped with them, so
# `gh -R o/r pr merge` and `gh pr --repo o/r merge` read as `gh pr merge`. Every other `-x` /
# `--x` / `--x=v` is dropped alone.
#
_GH_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})
# The same for `git`, in front of the subcommand: `git -C . rebase main` must read as
# `git rebase main`. Only the two git itself reads a separate value after.
_GIT_VALUE_FLAGS = frozenset({"-C", "-c"})
_MERGE_ENDPOINT = re.compile(r"pulls/[^/\s]+/merge\b")


def _may_be_push(raw: list[str]) -> bool:
    """Whether `git <raw>` could be a push: a `push` with only options, or an option's value,
    in front of it. `git log --grep push` is not one.

    Leans towards yes: `git --no-pager log push` reads as one, since which of git's options
    take a value is not known here.
    """
    if "push" not in raw:
        return False
    before = raw[: raw.index("push")]
    return all(
        t.startswith("-") or (i and before[i - 1].startswith("-")) for i, t in enumerate(before)
    )


def _words(base: str, rest: list[str]) -> tuple[str, ...]:
    """The command and its positional words, flags removed: what a deny prefix is matched on.

    Still a reading of tokens, not of what the program will do: an alias defined before the
    step, or `node -e` spawning `gh`, is not seen. The `integrate` grant also refuses an alias
    made during the step (`_GIT_CONFIG_ROAD`).
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
    agent_id: str | None = None,
) -> str:
    """ "" if this call may proceed, else the reason it may not.

    Checked in this order on purpose: the tool has to be granted at all before anything about
    its arguments matters.

    `agent_id` is the CLI's, never the session's: set when one of `SUBAGENTS` made the call. A
    helper runs `git` only as `HELPER_GIT`, on top of every other check.

    **`unit_dir` widens the write boundary by exactly one directory.** Every artifact lives in
    the product's own store, outside the workspace, so a step that writes its own artifact
    (`impl`) needs that one place. It is one directory, not a prefix of the store: a step may
    write its own unit's files and no other unit's. And the path is not the caller's: it comes
    from `coscc/units/__init__.py`, built from the data root and a workspace that already passed
    the membership gate; no route leads from a request to this value. `None` means no second
    root, the shape every prose stage runs with (they hold no write tools).

    The same two roots bound `Read`, `Glob` and `Grep` too.

    `read_also` widens **reading only**, by an explicit list of paths the app built from the
    data root (Gebo's own unit folder and the intent/spec/plan of the related units). Writing
    keeps its roots. `lease` is `(branch, head)`, which a grant with `push_needs_lease` binds
    every `git push` to.
    """
    if tool == SUBMIT_TOOL and grant.submits:
        # The one MCP tool a grant lets through, by its exact name: the app's own in-process
        # server, whose handler writes nothing and runs nothing.
        return ""
    if tool.startswith("mcp__") and tool in grant.mcp:
        # Safe because `grant.mcp` holds only names the kernel derived from a feature's declared
        # tools, and `Grant` refuses any entry that is not `mcp__<server>__<name>` or that names
        # the `cos` server: a built-in tool or `submit` can never enter it.
        return ""
    if tool == PEERS_TOOL and AGENT_TOOL in grant.tools:
        # The app's own list of this run's helpers: it reads nothing else and writes nothing.
        return ""
    if tool not in grant.tools:
        # Covers MCP tools by construction: their names are never in a grant.
        return f"this step was not granted {tool}"

    if tool == AGENT_TOOL and tool_input.get("subagent_type") not in SUBAGENTS:
        return f"only these helpers may be started: {', '.join(SUBAGENTS)}"

    if tool in EXEC_TOOLS:
        # Only the calls the CLI asks about reach here: one it takes for read-only runs in the
        # background without asking, which is what `sessions.FOREGROUND_ENV` closes.
        if tool_input.get("run_in_background"):
            return f"run_in_background is refused: {BACKGROUND_REFUSAL}"
        # The unit's name opens a `/tmp` directory to redirects. Writes there are outside the
        # boundary the write tools are held to below.
        from pathlib import Path

        unit = Path(unit_dir).name if unit_dir else ""
        command = str(tool_input.get("command", ""))
        reason = (
            check_command(grant, command, lease, unit)
            or _git_into(command, workspace, read_also)
            or _helper_git(command, agent_id)
        )
        if reason:
            return reason

    if tool in WRITE_TOOLS:
        # Relative paths resolve against the app's own directory here; changing that would widen
        # writing in one corner.
        roots, reason = _roots(workspace, unit_dir)
        if reason:
            return reason
        # Where a redirect may write (`_in_step_tmp`), the write tools may too, for a step that can run commands.
        from pathlib import Path

        unit = Path(unit_dir).name if unit_dir and any(t in grant.tools for t in EXEC_TOOLS) else ""
        for raw in _paths_in(tool_input):
            if not _inside(raw, roots, None) and not _in_step_tmp(
                _Redirect(">", "", raw, False), unit
            ):
                return f"writing outside the workspace is not allowed: {raw}"

    if tool in READ_TOOLS:
        # Reading is held to the same two roots as writing, or a step that could `Read` could read
        # anything the app's process could (`~/.ssh`, `~/.config/coscc/env`, every other unit).
        # Relative paths resolve against the workspace, the session's `cwd` and so what the tool
        # itself will read.
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
                return f"reading outside the workspace is not allowed: {tool_input.get('pattern')}"
            if not _inside(raw, roots, roots[0]):
                return f"reading outside the workspace is not allowed: {raw}"
    return ""


def _helper_git(command: str, agent_id: str | None) -> str:
    """Why a helper's `git` is refused, or "": its subcommand must be one of `HELPER_GIT`. The
    leading session's call (`agent_id` `None`) is never refused here.

    Read on the words `_words` leaves, so `git -C . commit` reads as `git commit`, and on the
    program `_launched` finds, so `uv run git commit` and `find . -exec git commit` read so too.
    A tripwire like the rest: `git diff --output=<file>` still writes, and `python -c` or
    `uv run --with x git` hide the program.
    """
    if agent_id is None:
        return ""
    parsed = _read(command)
    if isinstance(parsed, _Unreadable):
        # `check_command` has refused it already.
        return ""
    for simple in parsed.commands:
        words = list(simple.words)
        for k in _launched(words):
            if k >= len(words) or words[k].rsplit("/", 1)[-1] != "git":
                continue
            sub = _words("git", words[k + 1 :])[1:2]
            if not sub or sub[0] not in HELPER_GIT:
                return (
                    f"a helper runs git only to read ({', '.join(HELPER_GIT)}): "
                    "only the leading session commits"
                )
    return ""


# What `find` runs a command with.
_FIND_EXEC = ("-exec", "-execdir", "-ok", "-okdir")


def _launched(words: list[str]) -> list[int]:
    """Where a program starts in one simple command's words: the first past its assignments,
    the one after `uv run` and its flags, and the one after each `find -exec`."""
    if not words:
        return []
    k = 0
    while k < len(words) - 1 and _ASSIGNMENT.match(words[k]):
        k += 1
    out = [k]
    program = words[k].rsplit("/", 1)[-1]
    if program == "uv" and words[k + 1 : k + 2] == ["run"]:
        j = k + 2
        while j < len(words) and words[j].startswith("-"):
            j += 1
        out.append(j)
    elif program == "find":
        out += [j + 1 for j, w in enumerate(words) if w in _FIND_EXEC]
    return out


# git's own options that take a value, in front of the subcommand.
_GIT_VALUED = ("-C", "-c", "--git-dir", "--work-tree")


def _git_into(command: str, workspace: str, read_also: tuple[str, ...]) -> str:
    """Why a `git` pointed into a path `read_also` names is refused, or "".

    `read_also` widens reading, and `git -C <sibling> commit` would be a write there that no
    write tool made. `_words` drops `-C` and its value, so the deny list never sees it; this
    reads them as git does: each `-C` relative to the one before, and a relative `--git-dir`,
    `--work-tree`, `-c` value or `GIT_*=` assignment against both the workspace and the
    directory the `-C`s end in. `cd <sibling> && git ...` needs `cd`, which no grant holds. A
    path a subcommand takes (`git worktree add <sibling>/x`) is not read, and the rest is still
    the read boundary, which is not a sandbox (`.claude/rules/coscc-policy.md`).
    """
    from pathlib import Path

    if not read_also:
        return ""
    parsed = _read(command)
    if isinstance(parsed, _Unreadable):
        return ""
    try:
        roots = [Path(p).expanduser().resolve() for p in read_also]
        base = Path(workspace).expanduser().resolve()
    except OSError:
        return "a path this step may read could not be resolved"
    refused = "git may not be pointed at {}: this step may read that repository, not change it"
    for simple in parsed.commands:
        words = list(simple.words)
        k = 0
        while k < len(words) - 1 and _ASSIGNMENT.match(words[k]):
            k += 1
        if not words or words[k].rsplit("/", 1)[-1] != "git":
            continue
        # `GIT_DIR=`, `GIT_WORK_TREE=` and the other `GIT_*` paths git reads from its environment.
        values = [w.partition("=")[2] for w in words[:k] if w.startswith("GIT_")]
        if any(e for w, e in zip(words[:k], simple.expanded) if w.startswith("GIT_")):
            # What a variable holds is not known here, and `impl`'s grant lets one reach git.
            return "git may not be given a GIT_* variable's value: this step cannot read where it points"
        rest = words[k + 1 :]
        unknown = simple.expanded[k + 1 :]
        where = base
        i = 0
        while i < len(rest) and rest[i].startswith("-"):
            name, eq, value = rest[i].partition("=")
            if name in _GIT_VALUED and not eq:
                value = rest[i + 1] if i + 1 < len(rest) else ""
                i += 1
            if name in _GIT_VALUED and i < len(unknown) and unknown[i]:
                return f"git may not be given a variable for {name}: this step cannot read where it points"
            if name == "-C" and value:
                # An absolute value replaces `where`; a relative one goes on from it.
                where = where / Path(value).expanduser()
                if _inside(str(where), roots, None):
                    return refused.format(value)
            elif name == "-c":
                # `-c core.worktree=<dir>` moves the work tree as `--work-tree` does.
                values.append(value.partition("=")[2])
            elif name in _GIT_VALUED:
                values.append(value)
            i += 1
        for value in values:
            if value and (_inside(value, roots, base) or _inside(value, roots, where)):
                return refused.format(value)
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
    directory.
    """
    from pathlib import Path

    try:
        path = Path(raw).expanduser()
        if base is not None and not path.is_absolute():
            path = base / path
        target = path.resolve()
    except OSError, RuntimeError:
        return False
    return any(target == root or root in target.parents for root in roots)


# Stands in for a path when a `Glob` pattern climbs with `..`: there is no fixed prefix to
# check, and the pattern itself says it is leaving.
_TRAVERSAL = object()
_GLOB_CHARS = "*?[{"


def _read_paths_in(tool: str, tool_input: dict) -> list:
    """Every path a read tool was given, plus the fixed prefix of an absolute `Glob` pattern.

    No `path` at all is fine: the SDK then searches the session's `cwd`, the workspace. Reading
    a pattern this way is best-effort; `TheReadBoundaryIsNotASandbox` below pins what it misses.
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
