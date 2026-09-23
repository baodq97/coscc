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
import shlex
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
#
# Matched on the words left once flags are removed (`_words` below), so a `-R o/r` in front
# does not walk past it. `gh alias set` is refused too: an alias `pr` defines is an alias
# `pr` can then run under another name.
MERGE_IS_SHIPS = (
    (("gh", "pr", "merge"), "merging is the ship stage's"),
    (("gh", "alias", "set"), "an alias is a merge under another name; merging is the ship stage's"),
)

SHIP_WARNING = (
    "This step merges the pull request into main with `gh pr merge`, using the GitHub "
    "login already on this machine. That login reaches every repository its account can "
    "reach. The gate has checked that the review passed with nothing open and that no code "
    "landed after it; nobody but an agent has read the change."
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
    # Both ceilings are copied from `plan` above, not measured for `spec`. `review` below
    # keeps 20 turns and $2.00 although its comment says "the same ceilings as `plan`";
    # that mismatch predates `0020` and is not this unit's to settle (`0020` R6).
    "spec": Grant(
        tools=READ_TOOLS,
        max_turns=40,
        max_budget_usd=4.0,
    ),
    "pr": Grant(
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
    "review": Grant(
        tools=READ_TOOLS,
        # Chosen, not measured; the same ceilings as `plan`.
        max_turns=20,
        max_budget_usd=2.0,
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


def is_prose_stage(stage: str) -> bool:
    return stage in PROSE_STAGES


# --- deciding one call -------------------------------------------------------
#
# Measured: the list handed to the SDK is not enough on its own. Eleven MCP tools
# arrived at a session created with `tools=[]`, because `--tools` names the built-in set and
# nothing else. A callback sits on the path every call takes, whatever declared it.

# The prefix of every reason `check_command` and `decide` return, named once so nothing
# else has to spell them again. `coscc/transcript.py` matches a tool result's text against
# this tuple to tell a refusal this table made from any other error a step can hit — so a
# reason built anywhere in this module from a string not listed here would undercount
# `0032_impl-fills-its-context-with-whole-files-and-refusals` R3's tiêu chí 1 without
# either side ever failing a test. Wording is unchanged from before this constant existed;
# only the duplication is gone.
REFUSAL_EMPTY = "an empty command"
REFUSAL_SUBSTITUTION = "command substitution is not allowed"
REFUSAL_REDIRECT = "redirecting into a file is not allowed"
REFUSAL_NOT_RUNNABLE = "this step may not run"
REFUSAL_MERGE_ENDPOINT = "this step may not call the merge endpoint"
REFUSAL_NOT_GRANTED = "this step was not granted"
REFUSAL_WRITE_OUTSIDE = "writing outside the workspace is not allowed"
REFUSAL_READ_OUTSIDE = "reading outside the workspace is not allowed"
REFUSAL_BAD_WORKSPACE = "the workspace path could not be resolved"
# `0032_impl-fills-its-context-with-whole-files-and-refusals` R5/R6/R8: the scanner reads
# quotes and heredocs now, and these are the shapes it refuses outright because a simple
# two-state reading of them cannot promise it agrees with bash — spec.md `## Design`.
REFUSAL_UNBALANCED_QUOTE = "an unbalanced quote is not allowed"
REFUSAL_HEREDOC_UNCLOSED = "a heredoc that never closes is not allowed"
REFUSAL_HEREDOC_MULTIPLE = "more than one heredoc on the same line is not allowed"
REFUSAL_ANSI_QUOTE = "an ANSI-C or a locale-quoted string is not allowed"

REFUSALS = (
    REFUSAL_EMPTY,
    REFUSAL_SUBSTITUTION,
    REFUSAL_REDIRECT,
    REFUSAL_NOT_RUNNABLE,
    REFUSAL_MERGE_ENDPOINT,
    REFUSAL_NOT_GRANTED,
    REFUSAL_WRITE_OUTSIDE,
    REFUSAL_READ_OUTSIDE,
    REFUSAL_BAD_WORKSPACE,
    REFUSAL_UNBALANCED_QUOTE,
    REFUSAL_HEREDOC_UNCLOSED,
    REFUSAL_HEREDOC_MULTIPLE,
    REFUSAL_ANSI_QUOTE,
)

# R4/R7: what to do instead, said once and appended to the refusal it belongs to *and* to
# `command_rules` below, so the prompt and the refusal a step meets can never disagree.
SUBSTITUTION_ALTERNATIVE = (
    "pipe a commit message in instead of substituting it: `git commit -F - <<'EOF'`, "
    "ending the heredoc on its own line; read any other value with a separate command "
    "first, then use what it printed"
)
REDIRECT_ALTERNATIVE = (
    "pipe long output through `tail -n N` or `head -n N` instead of redirecting it, and "
    "create a file with the Write tool rather than a shell redirect"
)
NO_TEMP_FILE_ALTERNATIVE = (
    "there is nothing to delete a temporary file for, because nothing here should be "
    "creating one — see the two alternatives above"
)

# R9: the size a `Read` with no `limit` is capped to when the file runs past it. Chosen —
# the same figure `permission_gate` below enforces — not measured; 20000 leaves headroom
# under the 25000-character ceiling `intent.md`'s tiêu chí 2 sets, for the `+8` per line
# `read_limit` already charges.
READ_CEILING = 20000


def command_rules(grant: Grant) -> str:
    """A plain restatement of what `check_command` and `decide` enforce for `grant`, meant
    to sit in a step's own prompt (R4) so a step sees the rule before it meets the refusal
    that would otherwise be its first word of it.

    Pure: built only from `grant` and this module's own constants — the same ones
    `check_command` builds its refusals from — so the prompt and a refusal a step actually
    meets can never disagree (R7).
    """
    commands = ", ".join(f"`{c}`" for c in sorted(grant.commands))
    lines = [
        f"You may run these, matched on the first word of each command: {commands}. "
        f"Anything else is refused: `{REFUSAL_NOT_RUNNABLE} '<word>'`.",
        f"Command substitution ({', '.join(_SUBSTITUTION)}) is refused: "
        f"{SUBSTITUTION_ALTERNATIVE}.",
        f"A shell redirect (`>`, `>>`) is refused: {REDIRECT_ALTERNATIVE}.",
    ]
    if "rm" not in grant.commands:
        lines.append(f"There is no `rm` here: {NO_TEMP_FILE_ALTERNATIVE}.")
    for prefix, reason in grant.denied:
        lines.append(
            f"`{' '.join(prefix)}` is refused even though its first word is allowed: "
            f"{reason}."
        )
    lines.append(
        f"A `Read` with no `limit` given, on a file that would otherwise run past "
        f"{READ_CEILING} characters, is given one automatically so it stops under that "
        "ceiling instead — ask for your own `limit` or `offset` if you want a different "
        "window. A `Grep` with no `head_limit` given is capped to 200 matching lines the "
        "same way."
    )
    return "\n".join(lines)

# Shell metacharacters that make the first word of a segment stop predicting what runs.
_SUBSTITUTION = ("$(", "`", "${", "<(", ">(")
_SEPARATORS = (";", "&&", "||", "|", "\n", "&")
_MULTI_SEPARATORS = tuple(s for s in _SEPARATORS if len(s) > 1)  # ("&&", "||")
_SINGLE_SEPARATORS = tuple(s for s in _SEPARATORS if len(s) == 1 and s != "\n")  # (";", "|", "&")

# Redirection into a file, which is a write that no write-tool check would ever see.
# Measured on 2026-09-22: a real `impl` step was refused four times, and one of those was
# `Write` aimed at the working folder above the workspace — so the boundary matters and a
# shell that can reach past it matters just as much. `2>&1` is not this: the `&` says the
# target is another descriptor, not a path.
_REDIRECT = re.compile(r">>?\s*(?![&\s])")

# `2>&1` and friends: a redirect between descriptors, touching no file. Skipped whole while
# scanning, because the `&` in it would otherwise be read as a separator and the `1` as a
# command — which is exactly what `npm test 2>&1` did on 2026-09-22.
_FD_REDIRECT = re.compile(r"\d?>&\d?")

# Metacharacters that stop a heredoc's delimiter word, the same way whitespace does.
_DELIM_STOP = ";|&()<>"


def _scan(text: str) -> tuple[list[str], str, str]:
    """Split a command line into segments, reading quotes and heredocs the way bash does
    enough to tell a real command apart from text inside them — R5, R6.

    Returns ``(segments, checked_text, error)``. When ``error`` is not `""`, `segments` and
    `checked_text` are both empty: nothing downstream should trust a line that did not scan
    cleanly, and `check_command` returns `error` before it looks at either.

    Three states carry the read: outside any quote, inside `'...'`, inside `"..."`.
    **Outside a quote, a backslash keeps the character after it literal** — the same
    reading bash gives it — so it can never itself open a quote. Without this,
    `echo \\"; rm x; echo \\"` would look like one long quoted argument to a scanner that
    read the first `\\"` as opening a real double quote, and `rm` would never become its
    own segment even though bash never opens one either. Inside `'...'` a backslash is
    nothing special; inside `"..."` it keeps the next character literal, same as outside.

    `$'` and `$"` are refused on sight, whatever state they are found in outside a single
    quote. ANSI-C quoting lets `\\'` sit inside `$'...'` without closing it — `$'\\''`
    closes after three characters, not two — and a plain two-state reading of `'...'` gets
    that wrong in the dangerous direction: it would swallow a real separator that follows
    into what it mistakes for an open quote, hiding a command bash does run. Refusing the
    whole line is cheap next to reading ANSI-C escapes correctly.

    A heredoc (`<<WORD` or `<<-WORD`, never `<<<`) is only recognised outside a quote. Its
    delimiter word is read under the same quote rules; any quote in it marks the body
    quoted, and the quotes are dropped from the word each following line is compared
    against. `<<-` also drops a leading tab from each line before comparing, same as bash.

    The newline that follows the operator always ends the segment being built, exactly
    like any other newline — a heredoc does not keep the rest of the physical line part of
    the same segment, so a command placed right after a heredoc's closing line is still its
    own segment and is still checked. The body, and its closing delimiter line, are then
    skipped whole before the next segment starts: they are never split into command
    segments, quoted or not. Only when the delimiter itself was quoted is the body also
    dropped from `checked_text` — substitution and redirect are still checked inside an
    **unquoted** heredoc's body, because bash still expands it.

    A second `<<`/`<<-` before the first is resolved, an unterminated quote, an
    unterminated heredoc delimiter, and a heredoc whose closing line never comes are each
    refused rather than guessed at.
    """
    n = len(text)
    i = 0
    state = "N"  # "N" outside quotes, "S" single-quoted, "D" double-quoted
    heredoc_pending: tuple[bool, str, bool] | None = None  # (quoted, delimiter, dash)
    segments: list[str] = []
    checked_parts: list[str] = []
    seg_last = 0
    chk_last = 0

    def flush_segment(end: int) -> None:
        nonlocal seg_last
        piece = text[seg_last:end].strip()
        if piece:
            segments.append(piece)
        seg_last = end

    def exclude_from_checked(a: int, b: int) -> None:
        nonlocal chk_last
        checked_parts.append(text[chk_last:a])
        chk_last = b

    while i < n:
        ch = text[i]
        if state == "S":
            if ch == "'":
                state = "N"
            i += 1
            continue
        if state == "D":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                state = "N"
            i += 1
            continue
        # state == "N"
        if ch == "\\":
            i += 2
            continue
        if ch == "'":
            state = "S"
            i += 1
            continue
        if ch == '"':
            state = "D"
            i += 1
            continue
        if text[i : i + 2] in ("$'", '$"'):
            return [], "", f"{REFUSAL_ANSI_QUOTE}: {text[i:i+2]}"
        if text[i : i + 3] == "<<<":
            i += 3
            continue
        if text[i : i + 2] == "<<":
            if heredoc_pending is not None:
                return [], "", REFUSAL_HEREDOC_MULTIPLE
            j = i + 2
            dash = j < n and text[j] == "-"
            if dash:
                j += 1
            while j < n and text[j] in " \t":
                j += 1
            delim_chars: list[str] = []
            quoted = False
            dstate = "N"
            while j < n:
                c = text[j]
                if dstate == "N":
                    if c in " \t\n" or c in _DELIM_STOP:
                        break
                    if c == "\\":
                        if j + 1 < n:
                            delim_chars.append(text[j + 1])
                            j += 2
                        else:
                            j += 1
                        continue
                    if c == "'":
                        quoted, dstate = True, "S"
                        j += 1
                        continue
                    if c == '"':
                        quoted, dstate = True, "D"
                        j += 1
                        continue
                    delim_chars.append(c)
                    j += 1
                    continue
                if dstate == "S":
                    if c == "'":
                        dstate = "N"
                    else:
                        delim_chars.append(c)
                    j += 1
                    continue
                # dstate == "D"
                if c == "\\" and j + 1 < n:
                    delim_chars.append(text[j + 1])
                    j += 2
                    continue
                if c == '"':
                    dstate = "N"
                else:
                    delim_chars.append(c)
                j += 1
            if dstate != "N":
                return [], "", f"{REFUSAL_UNBALANCED_QUOTE}: an unterminated heredoc delimiter"
            delim = "".join(delim_chars)
            if not delim:
                return [], "", f"{REFUSAL_HEREDOC_UNCLOSED}: no delimiter word after '<<'"
            heredoc_pending = (quoted, delim, dash)
            i = j
            continue
        if ch == ">":
            m = _FD_REDIRECT.match(text, i)
            if m:
                i = m.end()
                continue
            i += 1
            continue
        if ch == "\n":
            if heredoc_pending is not None:
                quoted, delim, dash = heredoc_pending
                heredoc_pending = None
                body_start = i + 1
                pos = body_start
                body_end = None
                while pos <= n:
                    nl = text.find("\n", pos)
                    line_end = nl if nl != -1 else n
                    line = text[pos:line_end]
                    compare = line.lstrip("\t") if dash else line
                    if compare == delim:
                        body_end = line_end + 1 if nl != -1 else n
                        break
                    if nl == -1:
                        break
                    pos = nl + 1
                if body_end is None:
                    return [], "", f"{REFUSAL_HEREDOC_UNCLOSED}: no line matches {delim!r}"
                flush_segment(i)
                if quoted:
                    exclude_from_checked(body_start, body_end)
                seg_last = body_end
                i = body_end
                continue
            flush_segment(i)
            seg_last = i + 1
            i += 1
            continue
        if text[i : i + 2] in _MULTI_SEPARATORS:
            flush_segment(i)
            seg_last = i + 2
            i += 2
            continue
        if ch in _SINGLE_SEPARATORS:
            flush_segment(i)
            seg_last = i + 1
            i += 1
            continue
        i += 1

    if state != "N":
        return [], "", REFUSAL_UNBALANCED_QUOTE
    if heredoc_pending is not None:
        return [], "", f"{REFUSAL_HEREDOC_UNCLOSED}: '<<' with no line left to close it"

    flush_segment(n)
    checked_parts.append(text[chk_last:n])
    return segments, "".join(checked_parts), ""


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
        return REFUSAL_EMPTY
    segments, checked_text, error = _scan(text)
    if error:
        return error
    for token in _SUBSTITUTION:
        if token in checked_text:
            # With substitution in play the first word no longer says what runs. Checked
            # against `checked_text`, not the raw line: the only text this ever excludes is
            # the body of a heredoc whose delimiter was quoted, which bash does not expand
            # either (R6). Substitution inside a plain `'...'` is still refused here on
            # purpose — spec.md `## Design` says so; nothing asked for that to widen.
            return f"{REFUSAL_SUBSTITUTION}: {token} — {SUBSTITUTION_ALTERNATIVE}"
    if _REDIRECT.search(checked_text):
        # A redirect writes a file without any write tool being called, so the path check
        # in `decide` never sees it. The step has `Write` and `Edit` for making files.
        return f"{REFUSAL_REDIRECT} — {REDIRECT_ALTERNATIVE}"
    for segment in segments:
        # `shlex.split`, not `str.split()`: a segment `_scan` cut is quote-balanced by
        # construction (a split only ever happens while outside every quote), so this
        # cannot raise for text that reached here, and it is what lets `"rm"`, `'gh' pr
        # merge` and `gh "pr" merge` all compare equal to their bare spelling (R8).
        words = shlex.split(segment, posix=True)
        if not words:
            continue
        word = words[0]
        # `VAR=x cmd` puts the assignment first; step over any of them.
        while "=" in word and not word.startswith("-") and len(words) > 1:
            words = words[1:]
            word = words[0]
        base = word.rsplit("/", 1)[-1]
        if base not in grant.commands:
            allowed = ", ".join(sorted(grant.commands))
            extra = f" — allowed: {allowed}" if allowed else ""
            if base == "rm":
                extra += f"; {NO_TEMP_FILE_ALTERNATIVE}"
            return f"{REFUSAL_NOT_RUNNABLE} {base!r}{extra}"
        checked_words = _words(base, words[1:])
        for prefix, reason in grant.denied:
            if checked_words[: len(prefix)] == prefix:
                return f"{REFUSAL_NOT_RUNNABLE} {' '.join(prefix)!r}: {reason}"
        if base == "gh" and grant.denied and any(_MERGE_ENDPOINT.search(t) for t in checked_words):
            # `gh api -X PUT repos/o/r/pulls/7/merge` is the same merge by another road.
            return f"{REFUSAL_MERGE_ENDPOINT}: merging is the ship stage's"
    return ""


def read_limit(line_lengths: list[int], offset: int = 0, ceiling: int = READ_CEILING) -> int | None:
    """R9: the largest `limit` (a count of lines) a `Read` starting at `offset` may be given
    without its output running past `ceiling` characters — or `None` when it would not run
    past it at all, meaning no cap is needed.

    Pure — `permission_gate` in `coscc/runner.py` is the only caller, and it does the file
    reading; this only counts. Each line costs `len(line) + 8`: 8 is chosen, not measured,
    to cover the line-number column `Read` prints ahead of every line, so the count stays
    an upper bound rather than an exact one.

    Never returns less than 1 even when the single next line alone would run past
    `ceiling` — a step must see *something* of a large file, not a refusal in its place
    (`plan.md ## What was chosen not to be done`: "Không từ chối `Read` lớn").
    """
    total = 0
    count = 0
    for length in line_lengths[offset:]:
        total += length + 8
        if total > ceiling:
            return max(count, 1)
        count += 1
    return None


# Flags `gh` reads a value after, anywhere on the line. Their values are dropped with them,
# so `gh -R o/r pr merge` and `gh pr --repo o/r merge` read as `gh pr merge` (`0015` review
# round 1, F1). Every other `-x` / `--x` / `--x=v` is dropped alone.
_GH_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})
_MERGE_ENDPOINT = re.compile(r"pulls/[^/\s]+/merge\b")


def _words(base: str, rest: list[str]) -> tuple[str, ...]:
    """The command and its positional words, flags removed — what a deny prefix is matched on.

    Still a reading of tokens, not of what the program will do: an alias defined before
    the step, or `node -e` spawning `gh`, is not seen. `.claude/CLAUDE.md` says so.
    """
    out = [base]
    skip = False
    for token in rest:
        if skip:
            skip = False
            continue
        if token.startswith("-"):
            skip = base == "gh" and token in _GH_VALUE_FLAGS
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
    """
    if tool not in grant.tools:
        # Covers MCP tools by construction: their names are never in a grant.
        return f"{REFUSAL_NOT_GRANTED} {tool}"

    if tool in EXEC_TOOLS:
        reason = check_command(grant, str(tool_input.get("command", "")))
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
                return f"{REFUSAL_WRITE_OUTSIDE}: {raw}"

    if tool in READ_TOOLS:
        # `0020` `spec.md` `## Answers`, answer 2: reading is held to the same two roots as
        # writing. Before this a step that could `Read` could read anything the app's own
        # process could — `~/.ssh`, `~/.config/coscc/env`, every other unit in the store.
        # Relative paths resolve against the workspace, because that is the session's `cwd`
        # and so what the tool itself will read.
        roots, reason = _roots(workspace, unit_dir)
        if reason:
            return reason
        for raw in _read_paths_in(tool, tool_input):
            if raw is _TRAVERSAL:
                return f"{REFUSAL_READ_OUTSIDE}: {tool_input.get('pattern')}"
            if not _inside(raw, roots, roots[0]):
                return f"{REFUSAL_READ_OUTSIDE}: {raw}"
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
            return [], REFUSAL_BAD_WORKSPACE
    if not roots:
        return [], REFUSAL_BAD_WORKSPACE
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
