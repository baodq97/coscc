"""What a step is allowed to do, keyed on the stage it runs.

`Config` is the app's default (chat only, no tools); this table says what a board step may do
instead.

- Deny by default: a stage this table does not name gets `Grant()`: no tools, one turn, no budget.
- Pure: nothing here reads the environment, the store or a request.
- The tools go with the stage, not the mode; the mode is recorded in the journal and decides
  nothing here.

`Grant.tools` is not the whole enforcement: a list handed to the SDK covers the built-in set
only and MCP tools walk past `tools=[]`. Every session runs Claude Code's `auto` mode, and what
no session may do whatever `auto` thinks is `critical` below, asked by the gate's hook before
every call (`coscc/agent/helpers.py`).
"""

from __future__ import annotations

import functools
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
    # Chosen, not measured: they turn a loop that will not end into a named failure.
    max_turns: int = 1
    max_budget_usd: float = 0.0
    # Whether the app writes the artifact from the reply (prose stages) or the session writes it
    # itself (stages that touch code).
    app_writes_artifact: bool = True
    # Shown on the page before the step is started: a capability from the machine's own
    # configuration is invisible in an app, so it is said where the button is.
    warning: str = ""
    # Whether the session is handed `submit` (`coscc/units/submit.py`), the one tool beyond this
    # grant's list the gate lets through. It writes nothing and runs nothing, and is not in
    # `tools`: `--tools` names the built-in set, and an SDK server's tool reaches the session anyway.
    submits: bool = False
    # Full names of MCP tools a feature's server holds, derived by `coscc/kernel.py`. Not in `tools`
    # (`--tools` names the built-in set) and not in `opens_anything`.
    mcp: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in self.mcp:
            m = MCP_NAME.fullmatch(name)
            if m is None or m.group(1) == "cos":
                raise ValueError(f"not a feature's MCP tool name: {name!r}")

    @property
    def opens_anything(self) -> bool:
        return bool(self.tools)


# Tools that only read, listed separately so the write set is short.
READ_TOOLS = ("Read", "Glob", "Grep")
# `coscc/units/submit.py`'s `NAME`, spelled here so this module imports nothing of it.
SUBMIT_TOOL = "mcp__cos__submit"
# A feature's MCP tool, `mcp__<server>__<name>`; the server is captured. `cos` is the app's own.
MCP_NAME = re.compile(r"mcp__([a-z][a-z0-9-]*)__[a-z][a-z0-9_]*")
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")
EXEC_TOOLS = ("Bash",)
# Hands work to one of `SUBAGENTS` inside the same session. Every tool call a helper makes
# reaches the same gate and grant as the session's own, with its `agent_id`, and its spend is in
# the session's cost.
AGENT_TOOL = "Agent"
# The same tool under its older name.
TASK_TOOL = "Task"
# The SDK's own message between the agents of one session; the same hook holds where it goes.
SEND_MESSAGE = "SendMessage"
# The kernel's own tool listing the helpers of the run, on `submit`'s server.
PEERS_TOOL = "mcp__cos__peers"
# Lists every Claude session on the machine, not only this run's helpers.
LIST_AGENTS = "ListAgents"
# What a helper calls to hand its result back to the leading session.
HANDBACK = "SubagentHandback"
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

INTEGRATE_WARNING = (
    "Integrating runs `git` and `gh` with the GitHub login already on this machine, and "
    "force-pushes (with a lease) to this unit's branch. That login reaches every repository "
    "its account can reach, not just this workspace. What it resolves is an agent's word, "
    "not a person's approval."
)

# An alias or an included config file made during the step renames `push` into a word
# `_may_be_push` never sees (`git -c alias.p=push p`, `git config alias.p push`, or the same
# through `GIT_CONFIG_*`). Matched on the whole segment, assignments included, so a commit
# message naming one is refused too, with this reason.
_GIT_CONFIG_ROAD = re.compile(
    r"(?:^|[\s='\"])(?:alias|include|includeif)\.|\bGIT_CONFIG", re.IGNORECASE
)

SPIKE_WARNING = (
    "This step runs arbitrary code (`python`, `node`, `npm`, `uv`) under this process's "
    "user, in a throwaway directory the app deletes afterwards. Nothing is a sandbox: Claude "
    "Code's auto mode and the app's few hard blocks hold the session, and what they miss is "
    "not undone."
)

# Said on the Backlog panel above the button, before it is pressed.
ESTIMATE_WARNING = (
    "Proposing estimates opens one paid session (1 turn, $2.00 ceiling) on the model of the "
    "Agents page row `estimate`. Whoever holds the password or a live session can press it, and "
    "can rewrite any estimate, relation or the shortlist under any name they type."
)

# Only stages that appear here get anything. The rest (`idea`, any stage invented later) falls
# through to `Grant()`. Keyed by stage alone.
GRANTS: dict[str, Grant] = {
    # The one entry whose ceilings are measured rather than chosen. Fifty turns ended three of
    # four `impl` steps mid-work (51/50 turns, $1.8-2.5, no `impl.md`); the one that finished did
    # so because earlier runs had done the work. 120 is about twice the highest real attempt,
    # and the budget goes with it: at the measured $0.047/turn a 120-turn step lands near $5.6,
    # so a $5 cap would only move the same premature stop to the other ceiling.
    "impl": Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS + (AGENT_TOOL, SEND_MESSAGE),
        max_turns=120,
        max_budget_usd=8.0,
        app_writes_artifact=False,
    ),
    # `plan` reads, and only reads: `write-plan` requires every path under `## Files that change`
    # to be verified before it is written down, and without read tools a plan names paths it
    # never saw. No write tools and no Bash: the app still writes `plan.md` from the reply,
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
    # Bash; the app writes `spec.md` from the reply. Both ceilings are `plan`'s, not measured.
    "spec": Grant(
        tools=READ_TOOLS,
        max_turns=40,
        max_budget_usd=4.0,
    ),
    # `intent` reads, and only reads: `write-intent` checks the idea's problem against the
    # worktree's code before `## Problem` is written, and without read tools it restates the idea
    # about code it never opened. No write tools and no Bash; the app writes `intent.md` from
    # the reply. Both ceilings are `spec`'s, chosen, not measured.
    "intent": Grant(
        tools=READ_TOOLS,
        max_turns=40,
        max_budget_usd=4.0,
    ),
    # The first grant that both holds Bash and has the app write its artifact from the reply.
    # `beyond_reading` guards only `PROSE_STAGES`, which this is not. Writing is held to the
    # session's `cwd`, a throwaway directory `runner.steps.Steps.run_step` makes and removes.
    #
    # Ceilings chosen, not measured. Spikes that finished were recorded at 36-44 turns and 40
    # turns / $4.0 stopped several before writing `spike.md`. The recorded `turns` is not the
    # counter `max_turns` stops on (see `plan`), so 80 doubles the ceiling that was hit. Runs
    # cost $0.032-0.055 a recorded turn, so 80 turns is about $2.6-4.4, and $4 would stop the
    # dearer ones before the turn ceiling; $8.0 is `impl`'s.
    "spike": Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS,
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
        max_turns=120,
        max_budget_usd=8.0,
        app_writes_artifact=False,
        warning=INTEGRATE_WARNING,
    ),
    # Not a stage either: the backlog's *Propose estimates* button, one session per press. No
    # tools, like `idea`; it hands its estimate back through
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
    tools could write its own artifact behind the app's back, and one holding Bash is not
    a prose stage at all; reading is neither. The guard in `coscc/runner/step.py` asks
    this rather than whether the grant is empty ("no tools" is not "no capability").
    """
    return tuple(t for t in grant.tools if t not in READ_TOOLS)


# The stages whose run hands back an object through `submit`: a stage result, or `review`'s
# round. `coscc/units/submit.py` holds the same stages as `STAGE_RESULT` and `ROUND`;
# `policy_test` pins the two. A set, not the loop's order: that is the loop's alone.
SUBMITTING = ("idea", "impl", "intent", "plan", "review", "spec", "spike")
# The fewest turns such a step gets: a call to `submit` ends a turn, and a refused object is
# submitted again after one more turn, so four holds a call, a refusal, a second call and the
# reply. Chosen, not measured.
SUBMIT_TURNS = 4
# The sessions that are no stage and hand back an object through `submit`: Gebo, the
# estimate, and each feature's (`add_session`); `coscc/units/submit.py`'s `SESSIONS`.
SUBMITTING_SESSIONS = {"estimate", "integrate"}
# The sessions whose own `max_turns` holds below `SUBMIT_TURNS`, because one more turn could
# pass their budget: a refused object is not submitted again.
OWN_TURNS: set[str] = set()


def add_session(kind: str, grant: Grant, own_turns: bool) -> None:
    """A feature's session (`kernel.Session`), added when the app is built; adding the same
    one again changes nothing, and taking a name another grant holds is a `ValueError`."""
    if GRANTS.get(kind, grant) != grant:
        raise ValueError(f"the grant {kind!r} is taken")
    GRANTS[kind] = grant
    SUBMITTING_SESSIONS.add(kind)
    if own_turns:
        OWN_TURNS.add(kind)


def grant_for(stage: str) -> Grant:
    """The grant for one step. A stage the table does not name is locked, not open.

    A stage in `SUBMITTING` or a session in `SUBMITTING_SESSIONS` gets `submits`, and at least
    `SUBMIT_TURNS` turns, unless it is one of `OWN_TURNS`.
    """
    grant = GRANTS.get(stage, Grant())
    if stage not in SUBMITTING and stage not in SUBMITTING_SESSIONS:
        return grant
    return replace(grant, submits=True, max_turns=turns_floor(stage, grant.max_turns))


def turns_floor(stage: str, turns: int) -> int:
    """`turns`, raised to `SUBMIT_TURNS` for a stage or session that submits, unless it is one
    of `OWN_TURNS`. A person's override of the ceiling gets the same floor."""
    if stage in OWN_TURNS or (stage not in SUBMITTING and stage not in SUBMITTING_SESSIONS):
        return turns
    return max(turns, SUBMIT_TURNS)


def grant_for_step(stage: str, label: str | None) -> Grant:
    """The grant for one step run under a plan's effective label.

    Only the exact `novel` label, on a stage `NOVEL_CEILINGS` names, changes anything, and
    only the two ceilings: the tools and refusals are `grant_for(stage)`'s.
    """
    grant = grant_for(stage)
    if label == NOVEL and stage in NOVEL_CEILINGS:
        turns, budget = NOVEL_CEILINGS[stage]
        return replace(grant, max_turns=turns, max_budget_usd=budget)
    return grant


def is_prose_stage(stage: str) -> bool:
    return stage in PROSE_STAGES


# The app's database file in its data root, `coscc/store/db.py`'s `DB_FILENAME`; spelled here so
# this module imports nothing of the store, and pinned by a test.
DB_FILE = "cos.db"


def protected_paths(data_root: str, config_home: str, home: str = "") -> tuple[str, ...]:
    """The secrets no tool may reach: the vault's store `<data root>/vault`, the app's database (with its `-wal`
    and `-shm`), the app's config `<config home>/coscc` (`env`, `vault.key`), `gh`'s login
    `<config home>/gh`, and `~/.ssh`, `~/.aws`, `~/.gnupg`. Each as a command word may spell it:
    as given, symlinks resolved, and below `home` with `~`, `$HOME` or `${HOME}` in front. An
    empty `config_home` or `home` leaves its own entries out.

    The kernel keeps this list, not the feature, so every session is held to it with the vault off.
    """
    import os
    from pathlib import Path

    db = os.path.join(data_root, DB_FILE)
    dirs = [os.path.join(data_root, "vault"), db, f"{db}-wal", f"{db}-shm"]
    if config_home:
        dirs += [os.path.join(config_home, "coscc"), os.path.join(config_home, "gh")]
    if home:
        dirs += [os.path.join(home, d) for d in (".ssh", ".aws", ".gnupg")]
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


def check_push(words: list[str], branch: str, lease_head: str = "") -> str:
    """ "" if `git push <words>` is the one push allowed, else why not.

    `words` are the tokens after `push`. The one allowed shape is `origin <branch>`, or
    `HEAD:<branch>` or `HEAD:refs/heads/<branch>` in its place, never forced. With `lease_head` (Gebo's) it carries exactly one
    `--force-with-lease=<branch>:<lease_head>` with a full SHA; without, no lease at all. Pure:
    the branch and the head come from the app, never the session.
    """
    if not branch:
        return (
            "this session has no branch of its own (the trunk, a detached HEAD or no worktree), "
            "so it may not push"
        )
    if lease_head and not _FULL_SHA.match(lease_head):
        return "no lease was fixed for this step, so it may not push"
    leases = []
    positional = []
    for token in words:
        if token in ("--force", "-f") or (
            token.startswith("-") and not token.startswith("--") and "f" in token[1:]
        ):
            return "a push may not use --force: only the integration step's lease may overwrite"
        if token == "--force-with-lease" or token.startswith("--force-with-lease="):
            if not lease_head:
                return "a push may not use --force-with-lease: only the integration step's push carries a lease"
            if "=" not in token:
                return "--force-with-lease needs a value: --force-with-lease=<branch>:<head this step began at>"
            leases.append(token.split("=", 1)[1])
            continue
        if token in _PUSH_WIDE:
            return f"a push may not use {token}: it reaches more than this unit's branch"
        if token.startswith("-"):
            if token not in _PUSH_HARMLESS:
                return f"a push may not use {token}"
            continue
        positional.append(token)
    if lease_head and len(leases) != 1:
        return "a push must carry exactly one --force-with-lease=<branch>:<head this step began at>"
    if lease_head and leases[0] != f"{branch}:{lease_head}":
        return f"the lease must be bound to {branch}:{lease_head}, the head this step began at"
    if positional not in (
        ["origin", branch],
        ["origin", f"HEAD:{branch}"],
        ["origin", f"HEAD:refs/heads/{branch}"],
    ):
        return f"push with `git push origin {branch}`: a push names this unit's branch"
    return ""


def _glob_reaches(text: str, protected: str) -> bool:
    """Whether a word with `*`, `?` or `[` in it could expand into `protected`: its path, from
    the word's start or after an `=`, matches `protected` part by part."""
    import fnmatch

    if not any(c in text for c in "*?["):
        return False
    want = protected.split("/")
    for path in (text, text.partition("=")[2]):
        parts = path.split("/")
        if len(parts) >= len(want) and all(map(fnmatch.fnmatchcase, want, parts)):
            return True
    return False


def programs_of(command: str) -> tuple[str, ...]:
    """The program each simple command of the line runs, read as `bash_refused` reads it: the
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


def _scratch_of(raw: str, scratch: tuple[str, str] | None) -> int | None:
    """Which of `scratch` (0 the ram directory, 1 the disk one) the absolute path `raw` lies
    below, and is not the directory itself; `None` for neither.

    Both sides are resolved when the call is checked, not when it writes: a directory
    swapped for a symlink in between is not seen. A symlink inside either directory that points
    out of it resolves out, so it is not a way to write elsewhere.
    """
    from pathlib import Path

    if not scratch or not raw.startswith("/"):
        return None
    try:
        target = Path(raw).resolve()
        for i, root in enumerate(scratch):
            if root and Path(root).resolve() in target.parents:
                return i
    except OSError, RuntimeError:
        return None
    return None


def _listed(where: str) -> list:
    """The entries directly in `where`, none when it cannot be read."""
    import os

    try:
        with os.scandir(where) as entries:
            return list(entries)
    except OSError:
        return []


def _ram_bytes(ram: str) -> int:
    """The sizes of the files below `ram` added up, symlinks counted as themselves and not
    followed. What cannot be read counts as nothing."""
    import stat

    total, pending = 0, [ram]
    while pending:
        for entry in _listed(pending.pop()):
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                pending.append(entry.path)
            else:
                total += st.st_size
    return total


def _scratch_refused(raw: str, scratch: tuple[str, str] | None, ram_cap: int) -> str | None:
    """`None` when `raw` is not below a scratch directory (the caller refuses it); "" when it is
    and may be written; else why not: the ram directory is full, and the disk one is named."""
    where = _scratch_of(raw, scratch)
    if where is None or not scratch:
        return None
    if where == 0:
        used = _ram_bytes(scratch[0])
        if used >= ram_cap:
            return (
                f"the ram scratch directory {scratch[0]} holds {used} bytes, its cap is {ram_cap}: "
                f"write below {scratch[1]} ($COS_SCRATCH_DISK) instead"
            )
    return ""


# Flags `gh` reads a value after, anywhere on the line. Their values are dropped with them, so
# `gh -R o/r pr merge` and `gh pr --repo o/r merge` read as `gh pr merge`. Every other `-x` /
# `--x` / `--x=v` is dropped alone.
#
_GH_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})
# The same for `git`, in front of the subcommand: `git -C . rebase main` must read as
# `git rebase main`. Only the two git itself reads a separate value after.
_GIT_VALUE_FLAGS = frozenset({"-C", "-c"})


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
    return (base, *(rest[i] for i in _positions(base, rest)))


def _positions(base: str, rest: list[str]) -> list[int]:
    """Where in `rest` the positional words `_words` keeps stand."""
    out: list[int] = []
    skip = False
    for i, token in enumerate(rest):
        if skip:
            skip = False
            continue
        if token.startswith("-"):
            skip = (base == "gh" and token in _GH_VALUE_FLAGS) or (
                base == "git" and not out and token in _GIT_VALUE_FLAGS
            )
            continue
        out.append(i)
    return out


_PATH_KEYS = ("file_path", "path", "notebook_path", "target_file")


def _paths_in(tool_input: dict) -> list[str]:
    """Every path-shaped argument a write tool was given."""
    out = []
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    return out


def _write_refused(
    tool_input: dict, roots: list, scratch: tuple[str, str] | None, ram_cap: int
) -> str:
    """Why a write tool's paths may not be written, or "": each lies in `roots` or below `scratch`."""
    for raw in _paths_in(tool_input):
        if _inside(raw, roots, None):
            continue
        full = _scratch_refused(raw, scratch, ram_cap)
        if full is None:
            return f"writing outside the workspace is not allowed: {raw}"
        if full:
            return full
    return ""


def _helper_git(parsed: _Parsed, agent_id: str | None) -> str:
    """Why a helper's `git` is refused, or "": its subcommand must be one of `HELPER_GIT`. The
    leading session's call (`agent_id` `None`) is never refused here.

    Read on the words `_words` leaves, so `git -C . commit` reads as `git commit`. Every word
    `git` counts, so a wrapper (`timeout 5 git commit`, `xargs git add`) hides nothing, except
    after a program that only reads its words (`grep -rn git .`). A tripwire like the rest:
    `git diff --output=<file>` still writes, and `python -c` hides the program.
    """
    if agent_id is None:
        return ""
    for simple in parsed.commands:
        words = list(simple.words)
        launched = [k for k in _launched(words) if k < len(words)]
        reader = bool(launched) and words[launched[0]].rsplit("/", 1)[-1] in _READ_ONLY
        for k, word in enumerate(words):
            first = k in launched
            if first:
                if word.rsplit("/", 1)[-1] != "git":
                    continue
            elif reader or not (word == "git" or (word[:1] == "/" and word.endswith("/git"))):
                continue
            sub = _words("git", words[k + 1 :])[1:2]
            if (first and not sub) or (sub and sub[0] not in HELPER_GIT):
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


# The characters that end the fixed part of a `Glob` pattern.
_GLOB_CHARS = "*?[{"


# --- the critical calls ------------------------------------------------------
#
# Every session runs Claude Code's `auto` mode. These are what the app refuses before `auto` sees
# a call, from one `PreToolUse` hook (`coscc/agent/helpers.py` `Gate`); each reason opens with the
# rule it breaks. Words are read, nothing is run: a program that builds a path or pushes by itself
# walks past them, and `auto`'s classifier is the layer behind.

WRITES = "a session writes only in its worktree, its unit's folder and its scratch"
SECRETS = "no tool reaches the app's or the machine's secrets"
HOST = "pushing, merging and releasing on the host are the app's, but for the unit's own branch"
REMOVAL = "rm -r stays inside the unit's places, and no Claude Code runs inside a session"
HELPERS = (
    "helpers are named, in the foreground, read-only in git, and nothing runs in the background"
)
HELD = "only the MCP tools this session holds"


@dataclass(frozen=True)
class Places:
    """What one session may touch, fixed by the app as it opens; nothing here is the session's."""

    # Where the write tools may write: the session's `cwd` first (a relative path a command names
    # is read from there), then the unit's folder for a step that writes its own artifact.
    roots: tuple[str, ...] = ()
    # The unit's `(ram, disk)` directories (`coscc/units/scratch.py`), written only by a step
    # holding Bash, the ram one while its files add up to less than `ram_cap` bytes.
    scratch: tuple[str, str] | None = None
    ram_cap: int = 0
    # The branch the worktree stood on as the session opened, the one `git push` may name. ""
    # (the trunk, a detached HEAD, no worktree) pushes nothing.
    branch: str = ""
    # Gebo's lease, the head its pull request had when it began: its every push carries
    # `--force-with-lease=<branch>:<lease>`. "" refuses the flag.
    lease: str = ""
    # `protected_paths`: no tool may reach one.
    secrets: tuple[str, ...] = ()
    # What `~` and `$HOME` name in a command; "" reads this process's own.
    home: str = ""


def allowed_mcp(grant: Grant) -> tuple[str, ...]:
    """The MCP tools a grant holds: `submit` with `submits`, `peers` with `Agent`, and `Grant.mcp`.
    The session allows them by name, so they skip the classifier; the hook still asks first."""
    return (
        *((SUBMIT_TOOL,) if grant.submits else ()),
        *((PEERS_TOOL,) if AGENT_TOOL in grant.tools else ()),
        *grant.mcp,
    )


def critical(
    grant: Grant, places: Places, tool: str, tool_input: dict, agent_id: str | None
) -> str:
    """ "" unless the call is one every session is refused, else why, opening with its item.

    `agent_id` is the CLI's, set when one of `SUBAGENTS` made the call. Reads no command list
    and no read boundary: what is not here is for `auto` to judge.
    """
    if tool.startswith("mcp__"):
        if tool in allowed_mcp(grant):
            return ""
        return f"{HELD}: {tool} is not one"
    if tool in (AGENT_TOOL, TASK_TOOL):
        return _agent_refused(tool_input, agent_id)
    if tool == LIST_AGENTS:
        return f"{HELPERS}: ListAgents lists sessions outside this step; call {PEERS_TOOL}"
    reason = _secret_refused(tool, tool_input, places)
    if reason:
        return reason
    if tool in WRITE_TOOLS:
        roots = _resolved(places.roots)
        if not roots:
            return f"{WRITES}: this session has no place to write"
        if not _paths_in(tool_input) or any(
            k in tool_input and not (isinstance(tool_input[k], str) and tool_input[k])
            for k in _PATH_KEYS
        ):
            return f"{WRITES}: a write tool must name the file it writes"
        own = places.scratch if any(t in grant.tools for t in EXEC_TOOLS) else None
        reason = _write_refused(tool_input, roots, own, places.ram_cap)
        return f"{WRITES}: {reason}" if reason else ""
    if tool in EXEC_TOOLS:
        if tool_input.get("run_in_background"):
            return f"{HELPERS}: run_in_background is refused: {BACKGROUND_REFUSAL}"
        return bash_refused(places, str(tool_input.get("command") or ""), agent_id)
    return ""


def _agent_refused(tool_input: dict, agent_id: str | None) -> str:
    if agent_id is not None:
        return f"{HELPERS}: a helper may not start another helper"
    if tool_input.get("subagent_type") not in SUBAGENTS:
        return f"{HELPERS}: only these helpers may be started: {', '.join(SUBAGENTS)}"
    if tool_input.get("run_in_background"):
        return f"{HELPERS}: a helper runs in the foreground: this session ends when its turn ends"
    return ""


def _resolved(paths) -> list:
    """Each non-empty path, `~` expanded and symlinks resolved; one that cannot be is left out."""
    from pathlib import Path

    out = []
    for p in paths:
        try:
            if p:
                out.append(Path(p).expanduser().resolve())
        except OSError, RuntimeError, ValueError:
            continue
    return out


def _secret_refused(tool: str, tool_input: dict, places: Places) -> str:
    """For a file tool: a path in a secret, or a folder Grep or Glob searches holding one."""
    from pathlib import Path

    if tool not in READ_TOOLS + WRITE_TOOLS or not places.secrets:
        return ""
    secrets = _resolved(p for p in places.secrets if p.startswith("/"))
    cwd = _resolved(places.roots[:1])
    searches = tool in ("Grep", "Glob")
    targets = [(raw, searches) for raw in _paths_in(tool_input)]
    pattern = tool_input.get("pattern")
    if tool == "Glob" and isinstance(pattern, str) and pattern:
        fixed = []
        for part in pattern.split("/"):
            if any(c in part for c in _GLOB_CHARS):
                break
            fixed.append(part)
        start = str(tool_input.get("path") or "")
        joined = "/".join(fixed) or ("/" if pattern.startswith("/") else "")
        targets.append((str(Path(start) / joined) if start else joined, True))
    if searches and not tool_input.get("path"):
        targets.append((".", True))
    for raw, folder in targets:
        try:
            path = Path(raw or ".").expanduser()
            if not path.is_absolute() and cwd:
                path = cwd[0] / path
            path = path.resolve()
        except OSError, RuntimeError, ValueError:
            return f"{SECRETS}: {raw} could not be resolved, so it may be a secret"
        for s in secrets:
            if path == s or s in path.parents or (folder and path in s.parents):
                return f"{SECRETS}: {s}"
    return ""


# What a line names when a command hidden in it could be one of the critical roads.
_ROADS = re.compile(r"\b(?:push|merge(?!-base)|release|rm|gh|claude)\b")
# A word shaped like a line that names a road, `git` or a move: a script another program may run
# (`sh -c`, `ssh host`, `watch`, `eval`, `echo … | bash`), so it is read again as a line.
_SCRIPT_SHAPE = re.compile(r"[\s;|&]")
_SCRIPT_WORDS = re.compile(r"\b(?:push|merge(?!-base)|release|rm|gh|claude|git|cd|pushd|find)\b")
# Where an unreadable script would start a critical program.
_AT_COMMAND = re.compile(r"(?:^|[;&|\n(`])\s*(?:[\w./-]*/)?(?:git|gh|rm|claude|cd|pushd)\b")
# Programs that run the words after them as a program.
_WRAPPERS = frozenset(
    {"env", "exec", "command", "nohup", "nice", "setsid", "stdbuf", "time", "timeout", "xargs"}
    | {"npx", "uvx", "sudo"}
)
_CLAUDE = frozenset({"claude", "claude-code"})
_CD = frozenset({"cd", "pushd"})
# `gh <group> <verb>`: these verbs only read. Any other may write to the host, and a group
# `gh` does not ship may be an extension, whose code is not read here.
_GH_READS = frozenset(
    {"view", "list", "diff", "checks", "status", "checkout", "clone", "watch", "download", "get"}
)
_GH_LOCAL = frozenset({"search", "status", "browse", "help", "version", "completion"})
_GH_WHOLE = frozenset({"release", "secret", "variable", "extension"})
_GH_FIELDS = ("-f", "-F", "--field", "--raw-field", "--input")
# `git config`: what reads it. Any other form writes, and a write can turn a push.
_CONFIG_READS = frozenset({"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l"})
_CONFIG_WRITES = frozenset(
    {"--unset", "--unset-all", "--add", "--replace-all", "--rename-section", "--remove-section"}
    | {"--edit", "-e"}
)
_CONFIG_SCOPE = ("--global", "--local", "--system", "--worktree", "--show-", "--type", "--null")
_REMOTE_WRITES = frozenset({"add", "set-url", "rename", "remove", "rm"})


def bash_refused(
    places: Places, command: str, agent_id: str | None = None, strict: bool = False
) -> str:
    """ "" unless a Bash line is critical, else why. Read as bash reads it (`_read`):
    a line it cannot read is refused, and so is a substitution or a variable program on a line
    that names a critical road, since what runs there is not read.

    `git`, `gh`, `rm` and `find` are checked wherever they stand among a command's words, so a
    wrapper (`timeout 9 git push`) does not hide them; a quoted word shaped like a script is
    read again as a line. Paths are read from where the line stands, `cd` and `pushd` followed.

    `strict` is for a line that runs with secrets in it (the vault's): any substitution, of any
    kind, is refused too, since what runs must be what the line spells.
    """
    roots = _resolved(places.roots[:1])
    cwds = [str(roots[0]) if roots else None]
    known = _scratch_known(places, command)
    return _line_refused(places, command, agent_id, cwds, known, strict)


def _line_refused(
    places: Places,
    command: str,
    agent_id: str | None,
    cwds: list[str | None],
    known: dict,
    strict: bool = False,
) -> str:
    parsed = _read(command)
    if isinstance(parsed, _Unreadable):
        return (
            f"this command could not be read as the shell reads it: {parsed.what} "
            f"at character {parsed.at + 1}; nothing was guessed"
        )
    if parsed.background:
        # `&&`, `&>`, `&>>`, `>&`, `<&` and `|&` are read elsewhere and never land here.
        return f"{HELPERS}: `&` at character {parsed.background[0] + 1} runs a command in the background: {BACKGROUND_REFUSAL}"
    if strict and parsed.substitutions:
        token = parsed.substitutions[0][0]
        kind = {"$((": "arithmetic", "<(": "process", ">(": "process"}.get(token, "command")
        return f"{kind} substitution is not allowed here: {token}; the line runs with secrets in it"
    hidden = any(t != "$((" for t, _ in parsed.substitutions) or any(
        s.expanded[k] and not _ASSIGNMENT.match(s.words[k])
        for s in parsed.commands
        for k in _launched(list(s.words))
        if k < len(s.words)
    )
    if hidden and _ROADS.search(command):
        return (
            "a substitution or a variable program hides what runs, on a line that names push, "
            "merge, release, rm, gh or claude: spell each command out"
        )
    for form in _braces(command):
        for p in places.secrets:
            if re.search(re.escape(p) + r"(?![\w.-])", form):
                return f"{SECRETS}: {p}"
    runs_git = any(w.rsplit("/", 1)[-1] == "git" for s in parsed.commands for w in s.words)
    if runs_git and (
        _GIT_CONFIG_ROAD.search(command)
        or any(_GIT_CONFIG_ROAD.search(" " + w) for s in parsed.commands for w in s.words)
    ):
        return (
            f"{HOST}: a git alias, an include or GIT_CONFIG_* can rename `push`: none may be made"
        )
    reason = _helper_git(parsed, agent_id)
    if reason:
        return f"{HELPERS}: {reason}"
    cwds = list(cwds)
    for simple in parsed.commands:
        reason = _simple_refused(places, simple, agent_id, cwds, command, known)
        if reason:
            return reason
    return ""


def _simple_refused(
    places: Places,
    simple: _Simple,
    agent_id: str | None,
    cwds: list[str | None],
    line: str,
    known: dict,
) -> str:
    """Why one simple command is critical, or ""; a `cd` or `pushd` moves `cwds` on."""
    words, unknown = list(simple.words), list(simple.expanded)
    for word in (*words, *(r.target for r in simple.redirects)):
        hit = _word_secret(places, word, cwds)
        if hit:
            return f"{SECRETS}: {hit}"
    names = [w.rsplit("/", 1)[-1] for w in words]
    for i, name in enumerate(names):
        rest, rest_unknown = words[i + 1 :], unknown[i + 1 :]
        if name == "git":
            reason = _git_refused(places, rest, rest_unknown)
        elif name == "gh":
            reason = _gh_refused(rest, rest_unknown)
        elif name == "rm":
            fed = any(n == "xargs" or w in _FIND_EXEC for n, w in zip(names[:i], words[:i]))
            reason = _rm_refused(places, rest, rest_unknown, cwds, fed, known)
        elif name == "find":
            reason = _find_refused(places, rest, rest_unknown, cwds, known)
        else:
            continue
        if reason:
            return reason
    launched = [k for k in _launched(words) if k < len(words)]
    wrapped = bool(launched) and names[launched[0]] in _WRAPPERS
    if any(names[k] in _CLAUDE for k in launched) or (
        wrapped and any(n in _CLAUDE for n in names[launched[0] + 1 :])
    ):
        return f"{REMOVAL}: a session may not start Claude Code inside itself"
    inert = _inert(words)
    for k, word in enumerate(words):
        reason = "" if k in inert else _script_refused(places, word, agent_id, cwds, line, known)
        if reason:
            return reason
    if launched and names[launched[0]] in _CD:
        return _cd_refused(places, words[launched[0] + 1 :], cwds)
    return ""


def _script_refused(
    places: Places,
    word: str,
    agent_id: str | None,
    cwds: list[str | None],
    line: str,
    known: dict,
) -> str:
    """A word shaped like a line, read again as one: what `sh -c`, `ssh`, `watch` or a pipe into
    a shell would run. `NAME=` or `--flag=` in front is the value's, not the script's."""
    if not _SCRIPT_SHAPE.search(word) or not _SCRIPT_WORDS.search(word):
        return ""
    head, eq, value = word.partition("=")
    script = value if eq and head and not _SCRIPT_SHAPE.search(head) else word
    if len(script) >= len(line):
        # A substitution reads back as itself; `hidden` has judged it.
        return ""
    if isinstance(_read(script), _Unreadable) and not _AT_COMMAND.search(script):
        # Prose, such as a commit message: a shell could not run it as it stands either.
        return ""
    return _line_refused(places, script, agent_id, cwds, known)


def _cd_refused(places: Places, args: list[str], cwds: list[str | None]) -> str:
    """Where `cd` or `pushd` moves the line: added to `cwds`, since it may fail and leave the
    line where it was. A folder that is or holds a secret is refused."""
    target = next((a for a in args if a == "-" or not a.startswith("-")), "~")
    text = _expand(target, places.home)
    if target == "-" or "$" in text or "`" in text:
        cwds.append(None)
        return ""
    secrets = _secret_dirs(places.secrets)
    for form in _braces(text):
        for real in _real(form, cwds):
            for s in secrets:
                if real == s or real.startswith(s + "/") or s.startswith(real.rstrip("/") + "/"):
                    return f"{SECRETS}: {real} holds {s}"
            cwds.append(real)
    return ""


_BRACE = re.compile(r"\{([^{}]*)\}")


def _braces(word: str, limit: int = 64) -> list[str]:
    """The words a brace expansion makes of `word` (`~/.{ssh,aws}` is two); a range (`{1..9}`),
    or more than `limit` forms, reads as `*`."""
    out, todo = [], [word]
    while todo:
        if len(out) + len(todo) > limit:
            return [_BRACE.sub("*", word)]
        w = todo.pop()
        m = next((m for m in _BRACE.finditer(w) if "," in m.group(1)), None)
        if m is None:
            out.append(_BRACE.sub(lambda r: "*" if ".." in r.group(1) else r.group(0), w))
        else:
            todo += [w[: m.start()] + part + w[m.end() :] for part in m.group(1).split(",")]
    return out


def _expand(word: str, home: str) -> str:
    """`~`, `$HOME` or `${HOME}` at the start of `word`, as the shell expands it."""
    import os

    for lead in ("~", "${HOME}", "$HOME"):
        if word == lead or word.startswith(lead + "/"):
            return (home or os.path.expanduser("~")) + word[len(lead) :]
    return word


def _real(text: str, cwds: list[str | None]) -> list[str]:
    """`text` from each place the line may stand, links followed; a relative one from an
    unknown place is left out."""
    import os

    bases = [""] if text.startswith("/") else [c for c in cwds if c]
    out = []
    for base in bases:
        try:
            out.append(os.path.realpath(os.path.join(base, text) if base else text))
        except OSError, ValueError:
            continue
    return out


@functools.lru_cache(maxsize=16)
def _secret_dirs(secrets: tuple[str, ...]) -> tuple[str, ...]:
    """The absolute secrets as given and with their links followed."""
    import os

    absolute = [p for p in secrets if p.startswith("/")]
    return tuple(
        dict.fromkeys(
            [os.path.normpath(p) for p in absolute] + [str(r) for r in _resolved(absolute)]
        )
    )


def _word_secret(places: Places, word: str, cwds: list[str | None]) -> str:
    """The secret one word reaches, or "": by its text, a glob, a brace expansion, or the path
    it names from where the line stands, links followed. A `--flag=` or `NAME=` value counts."""
    import os

    if not word or not places.secrets:
        return ""
    secrets = _secret_dirs(places.secrets)
    for form in {f for w in (word, word.partition("=")[2]) if w for f in _braces(w)}:
        for text in (form, os.path.normpath(form)):
            for p in places.secrets:
                if re.search(re.escape(p) + r"(?![\w.-])", text) or _glob_reaches(text, p):
                    return p
        for real in _real(_expand(form, places.home), cwds):
            for s in secrets:
                if real == s or real.startswith(s + "/") or _glob_reaches(real, s):
                    return s
    return ""


def _git_refused(places: Places, rest: list[str], unknown: list[bool]) -> str:
    at = _positions("git", rest)
    if at and unknown[at[0]]:
        return f"{HOST}: git's subcommand may not be a variable ({rest[at[0]]}): it can hide a push"
    sub = rest[at[0]] if at else ""
    if sub in ("send-pack", "http-push"):
        return f"{HOST}: git {sub} pushes past the one push allowed"
    if sub == "config" and _config_writes(rest[at[0] + 1 :]):
        return f"{HOST}: git config is only read here: a setting can send a push elsewhere"
    if sub == "remote" and len(at) > 1 and rest[at[1]] in _REMOTE_WRITES:
        return f"{HOST}: git remote {rest[at[1]]} changes where a push goes"
    if not _may_be_push(rest):
        return ""
    if rest[0] != "push":
        return f"{HOST}: a push must be spelled `git push …`, with nothing between"
    reason = check_push(rest[1:], places.branch, places.lease)
    return f"{HOST}: {reason}" if reason else ""


def _config_writes(args: list[str]) -> bool:
    """Whether `git config <args>` may write: anything but a read flag, `get`, `list`, or one
    name with only scope and format flags."""
    if any(a.split("=", 1)[0] in _CONFIG_WRITES for a in args):
        return True
    if any(a in _CONFIG_READS for a in args):
        return False
    named = [a for a in args if not a.startswith("-")]
    if named[:1] in (["get"], ["list"]):
        return False
    flags = [a for a in args if a.startswith("-")]
    return len(named) != 1 or not all(f.startswith(_CONFIG_SCOPE) or f == "-z" for f in flags)


def _gh_refused(rest: list[str], unknown: list[bool]) -> str:
    at = _positions("gh", rest)
    if any(unknown[i] for i in at[:2]):
        return f"{HOST}: gh's command may not be a variable: it can hide a merge"
    words = tuple(rest[i] for i in at)
    if not words or words[0] in _GH_LOCAL:
        return ""
    if words[:2] == ("auth", "token"):
        return f"{SECRETS}: `gh auth token` prints the machine's GitHub login"
    if words[0] == "api":
        if _api_writes(rest):
            return f"{HOST}: a `gh api` call that writes (a method other than GET, or a field) is refused; read with `gh pr view` or `gh pr checks`"
        return ""
    if words[0] not in _GH_WHOLE:
        if words[1:2] and words[1] in _GH_READS:
            return ""
        if len(words) == 1 and ("--help" in rest or "-h" in rest):
            return ""
    return (
        f"{HOST}: `gh {' '.join(words[:2])}` may write to the host or run an extension; "
        "read with view, list, diff, checks or status"
    )


def _api_writes(rest: list[str]) -> bool:
    for j, token in enumerate(rest):
        name, eq, value = token.partition("=")
        if token in _GH_FIELDS or name in _GH_FIELDS or re.fullmatch(r"-[fF].+", token):
            return True
        if token in ("-X", "--method"):
            value = rest[j + 1] if j + 1 < len(rest) else ""
        elif token.startswith("-X"):
            value = token[2:]
        elif not (name == "--method" and eq):
            continue
        if value.upper() != "GET":
            return True
    return False


def _rm_refused(
    places: Places,
    rest: list[str],
    unknown: list[bool],
    cwds: list[str | None],
    fed: bool,
    known: dict,
) -> str:
    """`rm -r` of a target outside the unit's places, of a variable, or of what `xargs` or
    `find -exec` hands it (where that points is not read here)."""
    flags, targets, ended = [], [], False
    for token, var in zip(rest, unknown):
        if not ended and token == "--":
            ended = True
        elif not ended and token.startswith("-") and token != "-":
            flags.append(token)
        else:
            targets.append((token, var))
    if not any(f == "--recursive" or (f[1:2] != "-" and ("r" in f or "R" in f)) for f in flags):
        return ""
    if fed or not targets:
        return (
            f"{REMOVAL}: rm -r of what another program hands it: where it points is not known here"
        )
    for raw, var in targets:
        target = _put_scratch(raw, known) if var else raw
        if target is None or "{}" in target:
            return f"{REMOVAL}: rm -r of a variable ({raw}): where it points is not known here"
        if _outside(places, target, cwds):
            return f"{REMOVAL}: rm -r outside this unit's places: {raw}"
    return ""


def _find_refused(
    places: Places, rest: list[str], unknown: list[bool], cwds: list[str | None], known: dict
) -> str:
    """`find <start> -delete` removes below `start`, as `rm -r` would."""
    if "-delete" not in rest:
        return ""
    starts = []
    for token, var in zip(rest, unknown):
        if token.startswith("-") or token in ("(", "!", ")"):
            break
        starts.append((token, var))
    for start, var in starts or [(".", False)]:
        path = _put_scratch(start, known) if var else start
        if path is None or _outside(places, path, cwds):
            return f"{REMOVAL}: find -delete outside this unit's places: {start}"
    return ""


def _outside(places: Places, target: str, cwds: list[str | None]) -> bool:
    """Whether a removal target may lie outside the unit's places, from any place the line may
    stand; a relative one from an unknown place may."""
    roots = _resolved(places.roots)
    for form in _braces(_expand(target, places.home)):
        if not form.startswith("/") and (not cwds or None in cwds):
            return True
        for real in _real(form, cwds):
            if not (_inside(real, roots, None) or _scratch_of(real, places.scratch) is not None):
                return True
    return False


# The scratch the session's environment names (`sessions.child_env`).
_SCRATCH_NAMES = ("COS_SCRATCH_RAM", "COS_SCRATCH_DISK")


def _scratch_known(places: Places, command: str) -> dict[str, str]:
    """The scratch names a removal may be read through: each holds its `places.scratch` path,
    unless the line names it anywhere but as `$NAME` or `${NAME}` (it may set, export, declare,
    read, loop over or `printf -v` it), in which case it is not known."""
    if not places.scratch:
        return {}
    out = {}
    for name, path in zip(_SCRATCH_NAMES, places.scratch):
        rest = re.sub(r"\$(?:" + name + r"\b|\{" + name + r"\})", "", command)
        if not re.search(r"\b" + name + r"\b", rest):
            out[name] = path
    return out


def _put_scratch(word: str, known: dict[str, str]) -> str | None:
    """`word` with each known scratch name put in, or `None` if it holds any other variable."""
    out = re.sub(
        r"\$(?:\{(\w+)\}|(\w+))",
        lambda m: known.get(m.group(1) or m.group(2), m.group(0)),
        word,
    )
    return None if "$" in out or "`" in out else out


# Text no shell runs, so a re-read as a script skips it: a commit's or a tag's message, a
# search's pattern. Never skipped by the secret checks.
_GREPS = frozenset({"grep", "egrep", "fgrep", "rg"})
_GREP_VALUED = frozenset(
    {"-A", "-B", "-C", "-m", "-d", "-D", "-g", "-t", "-T", "-M", "--max-count", "--glob"}
    | {"--type", "--type-not", "--context", "--after-context", "--before-context"}
)
_DURATION = re.compile(r"\d+(?:\.\d+)?[smhd]?")


def _program_at(words: list[str]) -> int | None:
    """Where the program of one command stands: past its assignments, and past wrappers that
    run it directly (`timeout 5`, `env X=1`, `nohup`) with their flags and durations."""
    launched = [k for k in _launched(words) if k < len(words)]
    if not launched:
        return None
    k = launched[0]
    while k < len(words) and words[k].rsplit("/", 1)[-1] in _WRAPPERS:
        k += 1
        while k < len(words) and (
            words[k].startswith("-") or _ASSIGNMENT.match(words[k]) or _DURATION.fullmatch(words[k])
        ):
            k += 1
    return k if k < len(words) else None


def _inert(words: list[str]) -> set[int]:
    """Where in one command's words stands text its program never runs: the message of
    `git commit` or `git tag` (`-m`, `--message`, `--message=…`, or `-am`-like clusters of flags
    with no value, before any `--`), or the pattern of a search (`-e`, `--regexp`, or, with no
    `-f`, the first word that is no flag). Only for the program the command runs; a word with a
    substitution is never inert."""
    k = _program_at(words)
    if k is None:
        return set()
    name, rest = words[k].rsplit("/", 1)[-1], words[k + 1 :]
    out: set[int] = set()
    if name == "git":
        at = _positions("git", rest)
        if at and rest[at[0]] in ("commit", "tag"):
            for i in range(at[0] + 1, len(rest)):
                w = rest[i]
                if w == "--":
                    break
                if w == "--message" or re.fullmatch(r"-[aqvs]*m", w):
                    out.add(k + 2 + i)
                elif w.startswith("--message="):
                    out.add(k + 1 + i)
    elif name in _GREPS:
        out = {k + 1 + i for i in _patterns(rest)}
    return {i for i in out if i < len(words) and "$(" not in words[i] and "`" not in words[i]}


def _patterns(rest: list[str]) -> set[int]:
    files = any(w in ("-f", "--file") or w.startswith("--file=") for w in rest)
    out, skip, ended = set(), False, False
    for i, w in enumerate(rest):
        if skip:
            skip = False
        elif not ended and w in ("-e", "--regexp"):
            out.add(i + 1)
            skip = True
        elif not ended and w.startswith("--regexp="):
            out.add(i)
        elif not ended and w == "--":
            ended = True
        elif not ended and w in _GREP_VALUED:
            skip = True
        elif ended or not w.startswith("-"):
            if not out and not files:
                out.add(i)
            break
    return out


# Programs `auto` runs without its classifier when every command of a line is one of them.
_READ_ONLY = frozenset(
    {"ls", "cat", "head", "tail", "wc", "grep", "rg", "pwd", "echo", "printf", "which", "sort"}
    | {"uniq", "diff", "true", "test", "stat", "file", "tree", "find", "cut", "tr", "date"}
)


def classified(grant: Grant, places: Places, tool: str, tool_input: dict) -> bool:
    """Whether `auto` is estimated to send a call the hook let through to its classifier (as
    measured): not a read, an edit inside the working directory outside `.git` and `.claude`, a line of
    read-only commands, or an MCP tool the session allows by name; anything else Bash, a write
    elsewhere, `Agent`, `SendMessage` and a helper's hand-back. An estimate, the time signal of
    the run's `end`."""
    from pathlib import Path

    if tool in READ_TOOLS:
        return False
    if tool.startswith("mcp__"):
        return tool not in allowed_mcp(grant)
    if tool in WRITE_TOOLS:
        cwd = _resolved(places.roots[:1])
        return not cwd or any(
            not _inside(p, cwd, cwd[0]) or bool({".git", ".claude"} & set(Path(p).parts))
            for p in _paths_in(tool_input)
        )
    if tool in EXEC_TOOLS:
        return not _reads_only(str(tool_input.get("command") or ""))
    return tool in (AGENT_TOOL, SEND_MESSAGE, HANDBACK)


# A redirect that only reads, or moves between descriptors, writes no file.
_READ_REDIRECTS = frozenset({"<", "<<", "<<-", "<<<", "<&"})
_DESCRIPTOR = re.compile(r"\d*-?")


def _reads_only(command: str) -> bool:
    parsed = _read(command)
    if isinstance(parsed, _Unreadable) or parsed.substitutions:
        return False
    for simple in parsed.commands:
        for r in simple.redirects:
            to_fd = r.op == ">&" and bool(_DESCRIPTOR.fullmatch(r.target))
            if r.op not in _READ_REDIRECTS and not to_fd and r.target != "/dev/null":
                return False
        words = [w for w in simple.words if not _ASSIGNMENT.match(w)]
        name = words[0].rsplit("/", 1)[-1] if words else ""
        if name == "git":
            if _words("git", words[1:])[1:2] not in [(s,) for s in HELPER_GIT]:
                return False
        elif words and name not in _READ_ONLY:
            return False
    return True
