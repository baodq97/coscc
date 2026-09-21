"""What a step is allowed to do, keyed on the stage and the mode it runs in.

This is not a fifth knob. `Config` keeps meaning one thing — the app's default, which
`cos_baodo/config.py:44` states as *chat only, no tools at all* — and this table says what
a **board step** may do instead. `0007` exists because that sentence in `config.py` was not
true; making `Config` answer for two different things as well is how it would stop being
true again.

Three properties, each deliberate:

- **Deny by default.** A pair this table does not name gets `Grant()`, which is no tools,
  one turn and no budget. A stage invented tomorrow is therefore locked, not open.
- **Pure.** Nothing here reads the environment, the store, or a request. There is no path
  from HTTP to these values, the same way there is none to `COS_WORKING_DIR`.
- **Mode matters.** `manual` never carries tools for any stage. Choosing `autonomous` is
  the act that grants them, and it is recorded in the journal when it happens.

`0007`'s measurement is why `Grant.tools` is not the whole enforcement. A list handed to
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
PROSE_STAGES = ("idea", "intent", "spec", "plan", "review", "ship")


@dataclass(frozen=True)
class Grant:
    """What one step may do. The default is the locked position."""

    tools: tuple[str, ...] = ()
    # Commands the step may run, matched on the first word of the command line. Empty
    # means none, which is the only safe default for a field like this.
    commands: tuple[str, ...] = ()
    # `0008` R11. Chosen, not measured: they exist to turn a loop that will not end into a
    # named failure, not to describe what a step ought to cost.
    max_turns: int = 1
    max_budget_usd: float = 0.0
    # Whether the app writes the artifact from the reply (prose stages) or the session
    # writes it itself (stages that touch code).
    app_writes_artifact: bool = True

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

# Only pairs that appear here get anything. Everything else — every prose stage, every
# stage in `manual`, and anything invented later — falls through to `Grant()`.
GRANTS: dict[tuple[str, str], Grant] = {
    ("impl", "autonomous"): Grant(
        tools=READ_TOOLS + WRITE_TOOLS + EXEC_TOOLS,
        commands=IMPL_COMMANDS,
        max_turns=50,
        max_budget_usd=5.0,
        app_writes_artifact=False,
    ),
}


def grant_for(stage: str, mode: str) -> Grant:
    """The grant for one step. Unknown pairs are locked, not open."""
    return GRANTS.get((stage, mode), Grant())


def is_prose_stage(stage: str) -> bool:
    return stage in PROSE_STAGES


# --- deciding one call -------------------------------------------------------
#
# `0007` measured why the list handed to the SDK is not enough on its own: eleven MCP tools
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
    return ""


def _paths_in(tool_input: dict) -> list[str]:
    """Every path-shaped argument a write tool was given."""
    out = []
    for key in ("file_path", "path", "notebook_path", "target_file"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    return out


def decide(grant: Grant, tool: str, tool_input: dict, workspace: str) -> str:
    """"" if this call may proceed, else the reason it may not.

    Checked in this order on purpose: the tool has to be granted at all before anything
    about its arguments matters.
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
        try:
            root = Path(workspace).expanduser().resolve()
        except OSError:
            return "the workspace path could not be resolved"
        for raw in _paths_in(tool_input):
            try:
                target = Path(raw).expanduser().resolve()
            except OSError:
                return f"that path could not be resolved: {raw}"
            if target != root and root not in target.parents:
                return f"writing outside the workspace is not allowed: {raw}"
    return ""
