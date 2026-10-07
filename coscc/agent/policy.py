"""Each agent's row as the engine holds it (its tools and ceilings), the per-run `Grant`, and the
critical calls.

- `row_for` builds a `Row` from the agent's row of the pack (`coscc/agent/pack.py`): the tools it
  allows, its ceilings, who writes its artifact, its warning. Deny by default: a key no row names
  gets `Row()`: no tools, one turn, no budget.
- A `Grant` is what one run may do, issued by the engine as the run opens
  (`coscc/runner/run.py`'s `issue`) and gone with it. `critical` reads only it.
- Nothing here reads the environment, the store or a request.

`Row.tools` is not the whole enforcement: a list handed to the SDK covers the built-in set
only and MCP tools walk past `tools=[]`. Every session runs Claude Code's `auto` mode, and what
no session may do whatever `auto` thinks is `critical` below, asked by the gate's hook before
every call (`coscc/agent/helpers.py`).
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, replace
from collections.abc import Callable, Mapping
from typing import Any, Literal

from coscc.agent import pack


# How a plan rates its work (its record's `impl`): `novel` runs under the row's `novel` variant,
# `routine` in the model trial.
Label = Literal["routine", "novel"]
ROUTINE: Label = "routine"
NOVEL: Label = "novel"


@dataclass(frozen=True)
class Row:
    """One agent's data as a run uses it: the catalog tools it holds and its ceilings. The default
    is the locked position. Not a permission: what a run may do is the `Grant` issued from this."""

    tools: tuple[str, ...] = ()
    max_turns: int = 1
    max_budget_usd: float = 0.0
    # Whether the app writes the artifact from the reply or the session writes it itself.
    app_writes_artifact: bool = True
    # Shown on the page before the step is started: a capability from the machine's own
    # configuration is invisible in an app, so it is said where the button is.
    warning: str = ""
    # Whether the run is handed `submit` (`coscc/units/submit.py`). It writes nothing and runs
    # nothing, and is not in `tools`: the kernel's own, issued with the grant.
    submits: bool = False
    # The app writes the artifact from the reply of a row that only reads (`output.by: app`).
    prose: bool = False
    # The helper rows `Agent` may start, when the row holds it.
    helpers: tuple[str, ...] = ()
    # Of `tools`, those set to `ask`: offered to the session, and every call refused `ASKS`.
    asks: tuple[str, ...] = ()

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
# Hands work to one of the row's helpers inside the same session. Every tool call a helper makes
# reaches the same gate and grant as the session's own, with its `agent_id`, and its spend is in
# the session's cost.
AGENT_TOOL = pack.AGENT_TOOL
# The same tool under its older name.
TASK_TOOL = "Task"
# The SDK's own message between the agents of one session; the same hook holds where it goes.
SEND_MESSAGE = "SendMessage"
# The kernel's own tool listing the helpers of the run, on `submit`'s server.
PEERS_TOOL = "mcp__cos__peers"
# The kernel's own tool Leif's chat starts a triggered row with (`coscc/runner/triggers.py`).
RUN_AGENT_TOOL = "mcp__cos__run_agent"
# Lists every Claude session on the machine, not only this run's helpers.
LIST_AGENTS = "ListAgents"
# What a helper calls to hand its result back to the leading session.
HANDBACK = "SubagentHandback"
# The only `git` subcommands a helper may run: only the leading session commits.
HELPER_GIT = ("status", "diff", "log", "show", "blame")

# An alias or an included config file made during the step renames `push` into a word
# `_may_be_push` never sees (`git -c alias.p=push p`, `git config alias.p push`, or the same
# through `GIT_CONFIG_*`). Matched on the whole segment, assignments included, so a commit
# message naming one is refused too, with this reason.
_GIT_CONFIG_ROAD = re.compile(
    r"(?:^|[\s='\"])(?:alias|include|includeif)\.|\bGIT_CONFIG", re.IGNORECASE
)

# The `output.kind`s whose run hands back an object through `submit`.
SUBMIT_KINDS = ("artifact", "review", "session", "proposal", "verdict", "draft")
# The fewest turns such a step gets: a call to `submit` ends a turn, and a refused object is
# submitted again after one more turn, so four holds a call, a refusal, a second call and the
# reply. Chosen, not measured.
SUBMIT_TURNS = 4


def part_of(found: Mapping[str, Any], top: str, label: str | None) -> dict[str, Any]:
    """The row's `top` (`model`, `ceilings`), its `novel` variant's laid over it for a `novel`
    step, unless the owner set `top` and left the variants as the pack has them: an owner's edit
    is not undone by a variant they never saw. A part of the wrong shape (a hand-edited owner
    file, its runs refused) reads as none."""
    own = _obj(found.get(top))
    edited = found.get("edited") or ()
    if label == NOVEL and not (top in edited and "variants" not in edited):
        own.update(_obj(_obj(_obj(found.get("variants")).get(NOVEL)).get(top)))
    return own


def _obj(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _row(key: str, label: str | None) -> Row:
    found = pack.row(key)
    if found is None:
        return Row()
    output = found.get("output") or {}
    ceilings = part_of(found, "ceilings", label)
    return Row(
        tools=pack.tools(found) + pack.tools(found, "ask"),
        asks=pack.tools(found, "ask"),
        max_turns=int(ceilings.get("turns") or 1),
        max_budget_usd=float(ceilings.get("usd") or 0.0),
        app_writes_artifact=output.get("by") != "session",
        warning=str(found.get("warning") or ""),
        submits=output.get("kind") in SUBMIT_KINDS,
        prose=output.get("by") == "app",
        helpers=tuple(found.get("helpers") or ()),
    )


def row_for(key: str) -> Row:
    """The row of one agent. A key no row names is locked, not open. A row that submits gets
    at least `SUBMIT_TURNS` turns."""
    row = _row(key, None)
    return replace(row, max_turns=turns_floor(key, row.max_turns)) if row.submits else row


def turns_floor(stage: str, turns: int) -> int:
    """`turns`, raised to `SUBMIT_TURNS` for a row that submits. A person's ceiling gets the same
    floor."""
    if not _row(stage, None).submits:
        return turns
    return max(turns, SUBMIT_TURNS)


def row_for_step(stage: str, label: str | None) -> Row:
    """The row for one step run under a plan's effective label: a `novel` step takes the ceilings
    of its row's `novel` variant where it has them; the tools are `row_for(stage)`'s."""
    row = row_for(stage)
    variants = (pack.row(stage) or {}).get("variants")
    variant = variants.get(NOVEL) if isinstance(variants, dict) else None
    if label != NOVEL or not isinstance(variant, dict) or "ceilings" not in variant:
        return row
    novel = _row(stage, NOVEL)
    return replace(
        row, max_turns=turns_floor(stage, novel.max_turns), max_budget_usd=novel.max_budget_usd
    )


def helper_tools(kind: str) -> tuple[str, ...]:
    """The tools a helper row allows; none for a key that is no helper row."""
    found = pack.row(kind) or {}
    return pack.tools(found) if (found.get("output") or {}).get("kind") == "helper" else ()


# The app's database file in its data root, `coscc/store/db.py`'s `DB_FILENAME`; spelled here so
# this module imports nothing of the store, and pinned by a test.
DB_FILE = "cos.db"


def protected_paths(data_root: str, config_home: str, home: str = "") -> tuple[str, ...]:
    """The secrets no tool may reach: the vault's store `<data root>/vault`, the owner's agent rows `<data root>/packs`, the app's database (with its `-wal`
    and `-shm`), the app's config `<config home>/coscc` (`env`, `vault.key`), `gh`'s login
    `<config home>/gh`, and `~/.ssh`, `~/.aws`, `~/.gnupg`. Each as a command word may spell it:
    as given, symlinks resolved, and below `home` with `~`, `$HOME` or `${HOME}` in front. An
    empty `config_home` or `home` leaves its own entries out.

    The kernel keeps this list, not the feature, so every session is held to it with the vault off.
    """
    import os
    from pathlib import Path

    db = os.path.join(data_root, DB_FILE)
    dirs = [
        os.path.join(data_root, "vault"),
        os.path.join(data_root, "packs"),
        db,
        f"{db}-wal",
        f"{db}-shm",
    ]
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
    # Where the target (a heredoc's delimiter) starts in the line.
    at: int = -1


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
    # Per word: `(start, end)` of its text in the line read, quotes included.
    spans: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class _Parsed:
    commands: tuple[_Simple, ...]
    # Every substitution in effect, as `(token, at)`: `$(`, `` ` ``, `<(`, `>(`, `$((`.
    substitutions: tuple[tuple[str, int], ...]
    # Where each lone `&` stands: a command it ends runs in the background.
    background: tuple[int, ...] = ()
    # Each here-document whose delimiter is quoted (its body is text, nothing expanded):
    # `(where its delimiter stands, body start, body end)`.
    bodies: tuple[tuple[int, int, int], ...] = ()


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
        self.docs: list[tuple[int, int, int]] = []

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
        spans: list[tuple[int, int]] = []
        redirects: list[_Redirect] = []
        word: _Word | None = None
        pending: tuple[str, str, int] | None = None
        heredocs: list[tuple[str, bool, bool, int]] = []
        depth = 0

        def end_word(end: int | None = None) -> None:
            nonlocal word, pending
            if word is None:
                return
            if pending is not None:
                op, fd, _ = pending
                if op in ("<<", "<<-"):
                    heredocs.append((word.text, word.quoted, op == "<<-", word.at))
                    redirects.append(_Redirect(op, fd, word.text, False, word.at))
                else:
                    redirects.append(
                        _Redirect(op, fd, word.text, word.expanded or word.glob, word.at)
                    )
                pending = None
            else:
                words.append(word.text)
                flags.append(word.expanded)
                spans.append((word.at, self.i if end is None else end))
            word = None

        def end_command(j: int) -> None:
            nonlocal start, words, flags, spans, redirects
            end_word()
            if pending is not None:
                raise _Stop(f"a redirect ({pending[0]}) with no target", self.at(pending[2]))
            if words or redirects:
                out.append(
                    _Simple(
                        s[start:j].strip(),
                        tuple(words),
                        tuple(flags),
                        tuple(redirects),
                        tuple(spans),
                    )
                )
            words, flags, spans, redirects = [], [], [], []
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
                    end_word(amp)
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
                here = self.i
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
                end_word(here)
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
                    if literal:
                        self.docs.append((self.at(opened), self.at(begin), self.at(line_start)))
                    else:
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
        tuple(commands),
        tuple(sorted(reader.subs, key=lambda t: t[1])),
        tuple(sorted(reader.amps)),
        tuple(reader.docs),
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


def check_push(
    words: list[str],
    branch: str,
    lease_head: str = "",
    head: Callable[[bool], bool] | None = None,
) -> str:
    """ "" if `git push <words>` is the one push allowed, else why not.

    `words` are the tokens after `push`. The one allowed shape is `origin <branch>`, or
    `HEAD:<branch>` or `HEAD:refs/heads/<branch>` in its place, never forced. With `lease_head` (Gebo's) it carries exactly one
    `--force-with-lease=<branch>:<lease_head>` with a full SHA; without, no lease at all. Pure:
    the branch and the head come from the app, never the session. `git push origin HEAD` and
    `git push` alone pass too, without a lease, when `head(bare)` says they land on `branch`.
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
    if positional in ([], ["origin", "HEAD"]) and not lease_head and head and head(not positional):
        return ""
    if positional not in (
        ["origin", branch],
        ["origin", f"HEAD:{branch}"],
        ["origin", f"HEAD:refs/heads/{branch}"],
    ):
        return f"push with `git push origin {branch}`: a push names this unit's branch"
    return ""


def _checked_out(cwd: str) -> str:
    """The branch checked out at `cwd`, read from its `.git` (a folder, or a worktree's file
    naming one); "" on a detached HEAD or when it cannot be read."""
    from pathlib import Path

    try:
        here = Path(cwd).resolve()
        for d in (here, *here.parents):
            dot = d / ".git"
            if dot.is_dir():
                gitdir = dot
                break
            if dot.is_file():
                text = dot.read_text().strip()
                if not text.startswith("gitdir: "):
                    return ""
                gitdir = (d / text[len("gitdir: ") :]).resolve()
                break
        else:
            return ""
        ref = (gitdir / "HEAD").read_text().strip()
    except OSError, ValueError:
        return ""
    return ref.removeprefix("ref: refs/heads/") if ref.startswith("ref: refs/heads/") else ""


def _push_lands(cwd: str, bare: bool) -> str:
    """The branch on `origin` that `git push origin HEAD` run in `cwd` updates, or with `bare`
    `git push` alone; "" when not known. Read from the checkout's files and settings, nothing
    pushed. `git push` alone is known only when it sends the one branch (no `remote.*.push`,
    `push.default` not `matching`) to `origin` under its own name (`@{push}`)."""
    import os
    import subprocess

    branch = _checked_out(cwd)
    if not branch or not bare:
        return branch
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10, env=env
        )

    try:
        settings = git("config", "--get-regexp", r"^(push\.default|remote\..*\.push)$").stdout
        target = git("rev-parse", "--symbolic-full-name", "@{push}")
    except OSError, subprocess.SubprocessError:
        return ""
    if target.returncode or any(
        line.startswith("remote.") or line.split()[-1:] == ["matching"]
        for line in settings.splitlines()
    ):
        return ""
    return branch if target.stdout.strip() == f"refs/remotes/origin/{branch}" else ""


# `git` subcommands that leave HEAD on the branch it stands on, so a `git push origin HEAD` after
# one still pushes it. Any other may move it (`checkout`, `switch`, `branch -m`, `rebase a b`).
_KEEPS_HEAD = frozenset(
    {*HELPER_GIT, "add", "commit", "fetch", "rev-parse", "ls-files", "ls-remote", "push"}
    | {"restore", "rm", "mv", "config", "remote"}
)


def _moves_head(simple: _Simple) -> bool:
    """Whether a command may leave the line on another branch or point git elsewhere: a `git`
    not known to keep HEAD, a `GIT_*` word, or a word or redirect naming a `.git` or `HEAD`
    path outside git."""
    words = list(simple.words)
    gits = [i for i, w in enumerate(words) if w.rsplit("/", 1)[-1] == "git"]
    for i in gits:
        sub = _words("git", words[i + 1 :])[1:2]
        if not sub or sub[0] not in _KEEPS_HEAD:
            return True
    texts = [*(r.target for r in simple.redirects), *(() if gits else words)]
    return any("GIT_" in w for w in words) or any(
        ".git/" in t or t.endswith(".git") or "HEAD" in t for t in texts
    )


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


def pushes(grant: Grant, tool_name: str, tool_input: object) -> bool:
    """Whether a call may push: a command naming `git` and `push`, on a grant holding a branch.
    Leans towards yes, so the guards are asked before any push the grant lets through."""
    if tool_name not in EXEC_TOOLS or not grant.branch or not isinstance(tool_input, dict):
        return False
    command = str(tool_input.get("command") or "")
    return bool(re.search(r"\bgit\b", command) and re.search(r"\bpush\b", command))


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
# A tool the row sets to `ask`: no person is asked in a run yet, so the call is refused, the code
# naming who it waits for.
ASKS = "asks-a-person"
# A push the grant allows that a feature's guard (`vault-leak`) denies, asked again at the push.
GUARDED = "a feature's guard refuses this push"


# What a refusal lacked, by the rule its reason opens with: the grant that would have allowed the
# call, or `NEVER` for what no grant allows. `REMOVAL` is `write`'s: `rm -r` stays inside it.
NEVER = "never granted"
LACKED = {
    WRITES: "write",
    REMOVAL: "write",
    HOST: "push",
    HELPERS: "helpers",
    HELD: "mcp",
    ASKS: ASKS,
}


def lacked(reason: str) -> str:
    """The grant a refusal names: `write`, `push`, `helpers`, `mcp`, `ASKS` for a tool waiting on a
    person; `NEVER` for a secret; "" for
    a refusal that is not the hook's rule (a line it could not read, `auto`'s own)."""
    if reason.startswith(SECRETS):
        return NEVER
    return next((g for rule, g in LACKED.items() if reason.startswith(rule)), "")


@dataclass(frozen=True)
class Grant:
    """What one run may do: issued by the engine as the run opens (`coscc/runner/run.py`'s
    `issue`), held by that session's `Gate` only, and gone with the run. Nothing here is the
    agent's to choose, and nobody declares one. No grant, no action: the hook refuses a write, a
    push, a helper or an MCP tool this does not hold, and reaches no path in `secrets` whatever it
    holds."""

    # Where the session runs; a relative path a command or a tool names is read from here.
    cwd: str = ""
    # Where the write tools and `rm -r` may write: the session's `cwd` and the unit's folder, only
    # for a run whose row holds a write tool.
    write: tuple[str, ...] = ()
    # The unit's `(ram, disk)` directories (`coscc/units/scratch.py`), only for a run holding Bash,
    # the ram one while its files add up to less than `ram_cap` bytes.
    scratch: tuple[str, str] | None = None
    ram_cap: int = 0
    # The push: the branch the worktree stood on as the session opened, the one `git push` may
    # name; "" (the trunk, a detached HEAD, a spike, no worktree) pushes nothing. `lease` is Gebo's,
    # the head its pull request had when it began: its every push carries
    # `--force-with-lease=<branch>:<lease>`; "" refuses the flag.
    branch: str = ""
    lease: str = ""
    # The helper rows `Agent` may start (the row's `helpers`), only for a run whose row holds `Agent`.
    helpers: tuple[str, ...] = ()
    # Full names of the MCP tools it holds: `submit`, `peers` with helpers, and the catalog tools
    # its row lists and whose `when` admitted the run.
    mcp: tuple[str, ...] = ()
    # `(tool, resource)` it may use through that tool, as `("vault", "ws:db")`.
    use: tuple[tuple[str, str], ...] = ()
    # `protected_paths`: no tool may reach one. `issue` and `Gate` refuse a grant without them.
    secrets: tuple[str, ...] = ()
    # What `~` and `$HOME` name in a command; "" reads this process's own.
    home: str = ""
    # Claude Code's own tools it holds, what `--tools` names.
    tools: tuple[str, ...] = ()
    # The features' catalog entries its row names that are on for the workspace
    # (`coscc/kernel.py`), whether or not their `when` admitted the run: what a prompt block that
    # teaches one is shown for.
    held: tuple[str, ...] = ()
    # The tools its row sets to `ask`, as the session names them (`Read`, `mcp__vault__get`):
    # offered, and every call refused `ASKS` until a person can be asked.
    asks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in self.mcp:
            m = MCP_NAME.fullmatch(name)
            if m is None or (
                m.group(1) == "cos" and name not in (SUBMIT_TOOL, PEERS_TOOL, RUN_AGENT_TOOL)
            ):
                raise ValueError(f"not an MCP tool name a grant may hold: {name!r}")


def granted(grant: Grant) -> list[str]:
    """What a run was granted, one phrase each, as the run log's first line shows it:
    `write: worktree, unit folder, scratch`, `push: feat/x`, `helpers: scout, worker`,
    `vault: ws:db`, one per other MCP server (`codegraph`, `submit`)."""
    out = []
    if grant.write:
        where = (
            ["worktree"]
            + ["unit folder"] * (len(grant.write) > 1)
            + ["scratch"] * bool(grant.scratch)
        )
        out.append("write: " + ", ".join(where))
    if grant.branch:
        out.append(f"push: {grant.branch}" + (" (with a lease)" if grant.lease else ""))
    if grant.helpers:
        out.append("helpers: " + ", ".join(grant.helpers))
    used: dict[str, list[str]] = {}
    for tool, resource in grant.use:
        used.setdefault(tool, []).append(resource)
    for full in grant.mcp:
        server, name = full.split("__")[1:3]
        label = name if server == "cos" else server
        if full == PEERS_TOOL or any(p == label or p.startswith(f"{label}:") for p in out):
            continue
        out.append(f"{label}: {', '.join(used[label])}" if used.get(label) else label)
    return out


def record(grant: Grant) -> dict:
    """The grant as the run's `start` and its first event keep it: `granted`'s phrases, and each
    field but `secrets` (the deny list, the same for every run of this app)."""
    return {
        "granted": granted(grant),
        "cwd": grant.cwd,
        "write": list(grant.write),
        "scratch": list(grant.scratch or ()),
        "branch": grant.branch,
        "lease": grant.lease,
        "helpers": list(grant.helpers),
        "mcp": list(grant.mcp),
        "use": [list(u) for u in grant.use],
        "tools": list(grant.tools),
        "held": list(grant.held),
        "asks": list(grant.asks),
    }


def critical(
    grant: Grant, tool: str, tool_input: dict, agent_id: str | None, kind: str = ""
) -> str:
    """ "" unless the call is one every session is refused, else why, opening with its item.

    `agent_id` is the CLI's, set when one of the run's helpers made the call, and `kind` that helper's
    type: a helper holds no MCP tool but `PEERS_TOOL`, and writes or runs a command only when its
    kind's own tools list the tool (a `scout` neither). Reads only the run's grant: no command list
    and no read boundary; what is not here is for `auto` to judge.
    """
    if agent_id is not None:
        reason = _helper_tool_refused(tool, kind)
        if reason:
            return reason
    if tool in grant.asks:
        return f"{ASKS}: {tool} is set to ask, and a run cannot ask a person yet"
    if tool.startswith("mcp__"):
        if tool in grant.mcp:
            return ""
        return f"{HELD}: {tool} is not one"
    if tool in (AGENT_TOOL, TASK_TOOL):
        return _agent_refused(grant, tool_input, agent_id)
    if tool == LIST_AGENTS:
        return f"{HELPERS}: ListAgents lists sessions outside this step; call {PEERS_TOOL}"
    reason = _secret_refused(tool, tool_input, grant)
    if reason:
        return reason
    if tool in WRITE_TOOLS:
        roots = _resolved(grant.write)
        if not roots:
            return f"{WRITES}: this session has no place to write"
        if not _paths_in(tool_input) or any(
            k in tool_input and not (isinstance(tool_input[k], str) and tool_input[k])
            for k in _PATH_KEYS
        ):
            return f"{WRITES}: a write tool must name the file it writes"
        reason = _write_refused(tool_input, roots, grant.scratch, grant.ram_cap)
        return f"{WRITES}: {reason}" if reason else ""
    if tool in EXEC_TOOLS:
        if tool_input.get("run_in_background"):
            return f"{HELPERS}: run_in_background is refused: {BACKGROUND_REFUSAL}"
        return bash_refused(grant, str(tool_input.get("command") or ""), agent_id)
    return ""


def _helper_tool_refused(tool: str, kind: str) -> str:
    """Why a helper of `kind` may not call `tool`, beyond the grant it shares with its session."""
    if tool.startswith("mcp__"):
        return "" if tool == PEERS_TOOL else f"{HELD}: a helper holds only {PEERS_TOOL}"
    if tool in WRITE_TOOLS + EXEC_TOOLS and tool not in helper_tools(kind):
        return f"{HELPERS}: a {kind or 'helper of no known kind'} may not use {tool}"
    return ""


def _agent_refused(grant: Grant, tool_input: dict, agent_id: str | None) -> str:
    if agent_id is not None:
        return f"{HELPERS}: a helper may not start another helper"
    if not grant.helpers:
        return f"{HELPERS}: this run holds no helpers"
    if tool_input.get("subagent_type") not in grant.helpers:
        return f"{HELPERS}: only these helpers may be started: {', '.join(grant.helpers)}"
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


def _secret_refused(tool: str, tool_input: dict, grant: Grant) -> str:
    """For a file tool: a path in a secret, or a folder Grep or Glob searches holding one."""
    from pathlib import Path

    if tool not in READ_TOOLS + WRITE_TOOLS or not grant.secrets:
        return ""
    secrets = _resolved(p for p in grant.secrets if p.startswith("/"))
    cwd = _resolved((grant.cwd,))
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
# The one word after `claude` that prints and starts no session.
_CLAUDE_PRINTS = frozenset({"--version", "-v", "--help", "-h"})
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
    grant: Grant, command: str, agent_id: str | None = None, strict: bool = False
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
    roots = _resolved((grant.cwd,))
    cwds = [str(roots[0]) if roots else None]
    known = _scratch_known(grant, command)
    return _line_refused(grant, command, agent_id, cwds, known, strict, top=True)


def _line_refused(
    grant: Grant,
    command: str,
    agent_id: str | None,
    cwds: list[str | None],
    known: dict,
    strict: bool = False,
    top: bool = False,
    code: bool = False,
) -> str:
    """`top` for the line the session sent, not a script read out of one of its words: only
    there is a name set on the line followed, and a push of HEAD read. `code` for a program
    `node -e` runs: a backtick or `$(` in it is JavaScript, no shell's substitution."""
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
    if hidden and not code and _ROADS.search(command):
        return (
            "a substitution or a variable program hides what runs, on a line that names push, "
            "merge, release, rm, gh or claude: spell each command out"
        )
    if top:
        known = {**known, **_bound(grant, parsed, command, known)}
    text = command
    for a, b in sorted(_data(grant, parsed, cwds), reverse=True):
        text = text[:a] + " " * (b - a) + text[b:]
    for form in _braces(text):
        for p in grant.secrets:
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
    moved = not top
    for simple in parsed.commands:
        reason = _simple_refused(grant, simple, agent_id, cwds, command, known, moved, code)
        if reason:
            return reason
        moved = moved or _moves_head(simple)
    return ""


def _simple_refused(
    grant: Grant,
    simple: _Simple,
    agent_id: str | None,
    cwds: list[str | None],
    line: str,
    known: dict,
    moved: bool = True,
    code: bool = False,
) -> str:
    """Why one simple command is critical, or ""; a `cd` or `pushd` moves `cwds` on. `moved`
    unless the line is known to stand on the branch it started on: no push of HEAD then."""
    words, unknown = list(simple.words), list(simple.expanded)
    message = _messages(words)
    for word in (
        *(w for k, w in enumerate(words) if k not in message),
        *(r.target for r in simple.redirects),
    ):
        hit = _word_secret(grant, word, cwds)
        if hit:
            return f"{SECRETS}: {hit}"
    names = [w.rsplit("/", 1)[-1] for w in words]
    for i, name in enumerate(names):
        rest, rest_unknown = words[i + 1 :], unknown[i + 1 :]
        if name == "git":
            here = not moved and not any("GIT_" in w for w in words[:i])
            head = (lambda bare: _on_branch(grant, cwds, bare)) if here else None
            reason = _git_refused(grant, rest, rest_unknown, head)
        elif name == "gh":
            reason = _gh_refused(rest, rest_unknown)
        elif name == "rm":
            fed = any(n == "xargs" or w in _FIND_EXEC for n, w in zip(names[:i], words[:i]))
            reason = _rm_refused(grant, rest, rest_unknown, cwds, fed, known)
        elif name == "find":
            reason = _find_refused(grant, rest, rest_unknown, cwds, known)
        else:
            continue
        if reason:
            return reason
    launched = [k for k in _launched(words) if k < len(words)]
    wrapped = bool(launched) and names[launched[0]] in _WRAPPERS
    if any(
        names[k] in _CLAUDE and not (len(words) == k + 2 and words[k + 1] in _CLAUDE_PRINTS)
        for k in launched
    ) or (wrapped and any(n in _CLAUDE for n in names[launched[0] + 1 :])):
        return f"{REMOVAL}: a session may not start Claude Code inside itself"
    inert, js = _inert(words), _code(words)
    for k, word in enumerate(words):
        reason = (
            ""
            if k in inert
            else _script_refused(grant, word, agent_id, cwds, line, known, code or k in js)
        )
        if reason:
            return reason
    if launched and names[launched[0]] in _CD:
        return _cd_refused(grant, words[launched[0] + 1 :], cwds)
    return ""


def _script_refused(
    grant: Grant,
    word: str,
    agent_id: str | None,
    cwds: list[str | None],
    line: str,
    known: dict,
    code: bool = False,
) -> str:
    """A word shaped like a line, read again as one: what `sh -c`, `ssh`, `watch` or a pipe into
    a shell would run. `NAME=` or `--flag=` in front is the value's, not the script's. A name
    the line set is not followed in there: the script may set it again."""
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
    scratch = {k: v for k, v in known.items() if k in _SCRATCH_NAMES}
    return _line_refused(grant, script, agent_id, cwds, scratch, code=code)


def _on_branch(grant: Grant, cwds: list[str | None], bare: bool) -> bool:
    """Whether a push of HEAD (`bare`: `git push` alone) lands on the grant's branch from every
    place the line may stand."""
    return bool(grant.branch and cwds) and all(
        c is not None and _push_lands(c, bare) == grant.branch for c in cwds
    )


def _cd_refused(grant: Grant, args: list[str], cwds: list[str | None]) -> str:
    """Where `cd` or `pushd` moves the line: added to `cwds`, since it may fail and leave the
    line where it was. A folder that is or holds a secret is refused."""
    target = next((a for a in args if a == "-" or not a.startswith("-")), "~")
    text = _expand(target, grant.home)
    if target == "-" or "$" in text or "`" in text:
        cwds.append(None)
        return ""
    secrets = _secret_dirs(grant.secrets)
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


def _word_secret(grant: Grant, word: str, cwds: list[str | None]) -> str:
    """The secret one word reaches, or "": by its text, a glob, a brace expansion, or the path
    it names from where the line stands, links followed. A `--flag=` or `NAME=` value counts."""
    import os

    if not word or not grant.secrets:
        return ""
    secrets = _secret_dirs(grant.secrets)
    for form in {f for w in (word, word.partition("=")[2]) if w for f in _braces(w)}:
        for text in (form, os.path.normpath(form)):
            for p in grant.secrets:
                if re.search(re.escape(p) + r"(?![\w.-])", text) or _glob_reaches(text, p):
                    return p
        for real in _real(_expand(form, grant.home), cwds):
            for s in secrets:
                if real == s or real.startswith(s + "/") or _glob_reaches(real, s):
                    return s
    return ""


def _git_refused(
    grant: Grant,
    rest: list[str],
    unknown: list[bool],
    head: Callable[[bool], bool] | None = None,
) -> str:
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
    reason = check_push(rest[1:], grant.branch, grant.lease, head)
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
    grant: Grant,
    rest: list[str],
    unknown: list[bool],
    cwds: list[str | None],
    fed: bool,
    known: dict,
) -> str:
    """`rm -r` of a target outside the unit's grant, of a variable, or of what `xargs` or
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
        if _outside(grant, target, cwds):
            return f"{REMOVAL}: rm -r outside this unit's places: {raw}"
    return ""


def _find_refused(
    grant: Grant, rest: list[str], unknown: list[bool], cwds: list[str | None], known: dict
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
        if path is None or _outside(grant, path, cwds):
            return f"{REMOVAL}: find -delete outside this unit's places: {start}"
    return ""


def _outside(grant: Grant, target: str, cwds: list[str | None]) -> bool:
    """Whether a removal target may lie outside the unit's grant, from any place the line may
    stand; a relative one from an unknown place may."""
    roots = _resolved(grant.write)
    for form in _braces(_expand(target, grant.home)):
        if not form.startswith("/") and (not cwds or None in cwds):
            return True
        for real in _real(form, cwds):
            if not (_inside(real, roots, None) or _scratch_of(real, grant.scratch) is not None):
                return True
    return False


# The scratch the session's environment names (`sessions.child_env`).
_SCRATCH_NAMES = ("COS_SCRATCH_RAM", "COS_SCRATCH_DISK")


def _scratch_known(grant: Grant, command: str) -> dict[str, str]:
    """The scratch names a removal may be read through: each holds its `grant.scratch` path,
    unless the line names it anywhere but as `$NAME` or `${NAME}` (it may set, export, declare,
    read, loop over or `printf -v` it), in which case it is not known."""
    if not grant.scratch:
        return {}
    out = {}
    for name, path in zip(_SCRATCH_NAMES, grant.scratch):
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
    if name not in _GREPS:
        return _messages(words)
    out = {k + 1 + i for i in _patterns(rest)}
    return {i for i in out if i < len(words) and "$(" not in words[i] and "`" not in words[i]}


def _messages(words: list[str]) -> set[int]:
    """Where the message of `git commit` or `git tag` stands in one command's words (`-m`,
    `--message`, `--message=…`, or `-am`-like clusters of flags with no value, before any `--`):
    text git only stores. A word with a substitution is none."""
    k = _program_at(words)
    if k is None or words[k].rsplit("/", 1)[-1] != "git":
        return set()
    rest, out = words[k + 1 :], set()
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
    return {i for i in out if i < len(words) and "$(" not in words[i] and "`" not in words[i]}


# `node`'s flags whose value is its program.
_CODE_FLAGS = frozenset({"-e", "-p", "--eval", "--print"})


def _code(words: list[str]) -> set[int]:
    """Where `node -e` (`-p`, `--eval`, `--print`) takes its program: JavaScript, read again
    for what it names, but its backticks and `${…}` are templates, not substitutions."""
    k = _program_at(words)
    if k is None or words[k].rsplit("/", 1)[-1] != "node" or len(words) < k + 3:
        return set()
    return {k + 2} if words[k + 1] in _CODE_FLAGS else set()


# Programs that run text as shell code in the line's own shell, where it may set any name.
_SETS_BY_NAME = frozenset({"eval", "source", "."})


def _bound(grant: Grant, parsed: _Parsed, command: str, known: dict[str, str]) -> dict[str, str]:
    """A name the line's first command sets alone, to a fixed path below the scratch
    (`S=$COS_SCRATCH_DISK/x && rm -rf $S`), and that nothing else on the line may set: a removal
    reads it as that path. Not known when it is set after a pipe or `||`, in a subshell or a group,
    to anything with another variable, a glob or a blank in it, or again anywhere on the line
    (`S=`, `S+=`, `read S`, `for S`, `${S:=…}`, `((…))`, `eval`, `source`)."""
    if not grant.scratch or not parsed.commands:
        return {}
    first = parsed.commands[0]
    m = re.fullmatch(r"([A-Za-z_]\w*)=(.*)", first.words[0], re.S) if first.words else None
    if m is None or len(first.words) != 1 or first.redirects or m.group(1) in _SCRATCH_NAMES:
        return {}
    name, value = m.groups()
    line = command.lstrip()
    if not line.startswith(first.source) or not re.match(
        r"\s*(?:;|&&|\n|$)", line[len(first.source) :]
    ):
        return {}
    path = _put_scratch(value, known)
    if path is None or not re.fullmatch(r"/[\w./+@:%-]*", path):
        return {}
    if _scratch_of(path, grant.scratch) is None:
        return {}
    again = re.compile(rf"^{name}(?:\+?=|\[)|\$\{{{name}[^}}\w]")
    named = re.compile(rf"\b{name}\b")
    for simple in parsed.commands[1:]:
        words = list(simple.words)
        if (
            any(w.rsplit("/", 1)[-1] in _SETS_BY_NAME for w in words[:1])
            or name in words
            or any(
                again.search(w) or ("((" in w and named.search(w))
                for w in (*words, *(r.target for r in simple.redirects))
            )
        ):
            return {}
    return {name: path}


def _data(grant: Grant, parsed: _Parsed, cwds: list[str | None]) -> list[tuple[int, int]]:
    """Where the line holds text nothing reads as a path, left out of the line's secret search:
    a commit's or a tag's message, and a quoted here-document that `cat` writes into one file in
    the unit's places or `git commit -F -` takes as its message."""
    bodies = {at: (b, e) for at, b, e in parsed.bodies}
    places, out = list(cwds), []
    for simple in parsed.commands:
        words = list(simple.words)
        out += [simple.spans[i] for i in _messages(words)]
        docs = [r for r in simple.redirects if r.op in ("<<", "<<-")]
        if len(docs) == 1 and docs[0].at in bodies and _takes_text(grant, simple, places):
            out.append(bodies[docs[0].at])
        if words[:1] and words[0] in _CD:
            _cd_refused(grant, words[1:], places)
    return out


def _takes_text(grant: Grant, simple: _Simple, places: list[str | None]) -> bool:
    """`cat` whose one output is a file in the unit's places, or `git commit -F -`."""
    words = list(simple.words)
    out = [r for r in simple.redirects if r.op not in ("<<", "<<-")]
    if words == ["cat"]:
        return (
            len(out) == 1
            and out[0].op in (">", ">>", ">|")
            and out[0].fd in ("", "1")
            and not out[0].expanded
            and not _outside(grant, out[0].target, places)
        )
    at = _positions("git", words[1:]) if words[:1] == ["git"] else []
    if not at or words[1 + at[0]] != "commit":
        return False
    rest = words[2 + at[0] :]
    return "--file=-" in rest or any(
        a in ("-F", "--file") and b == "-" for a, b in zip(rest, rest[1:])
    )


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


def classified(grant: Grant, tool: str, tool_input: dict) -> bool:
    """Whether `auto` is estimated to send a call the hook let through to its classifier (as
    measured): not a read, an edit inside the working directory outside `.git` and `.claude`, a line of
    read-only commands, or an MCP tool the session allows by name; anything else Bash, a write
    elsewhere, `Agent`, `SendMessage` and a helper's hand-back. An estimate, the time signal of
    the run's `end`."""
    from pathlib import Path

    if tool in READ_TOOLS:
        return False
    if tool.startswith("mcp__"):
        return tool not in grant.mcp
    if tool in WRITE_TOOLS:
        cwd = _resolved((grant.cwd,))
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
