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
    # `0035` R6: every `git push` must carry `--force-with-lease` bound to the head the pull
    # request had when the step began, and name the unit's own branch. The lease itself is
    # not in the grant — it is per run — and reaches `decide` as `lease`.
    push_needs_lease: bool = False

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


def check_command(grant: Grant, command: str, lease: tuple[str, str] | None = None) -> str:
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
        whole = segment
        word = segment.split()[0] if segment.split() else ""
        # `VAR=x cmd` puts the assignment first; step over any of them.
        while "=" in word and not word.startswith("-") and len(segment.split()) > 1:
            segment = segment.split(maxsplit=1)[1]
            word = segment.split()[0] if segment.split() else ""
        base = word.rsplit("/", 1)[-1]
        if base not in grant.commands:
            return f"this step may not run {base!r}"
        words = _words(base, segment.split()[1:])
        for prefix, reason in grant.denied:
            if words[: len(prefix)] == prefix:
                return f"this step may not run {' '.join(prefix)!r}: {reason}"
        if base == "gh" and grant.denied and any(_MERGE_ENDPOINT.search(t) for t in words):
            # `gh api -X PUT repos/o/r/pulls/7/merge` is the same merge by another road.
            return "this step may not call the merge endpoint: merging is the ship stage's"
        raw = segment.split()[1:]
        if base == "git" and grant.push_needs_lease and _GIT_CONFIG_ROAD.search(whole):
            return "this step may not define a git alias, an include or GIT_CONFIG_*: it can rename `push` past the lease"
        if base == "git" and grant.push_needs_lease and _may_be_push(raw):
            # `0035` R6. `push` must be the first word after `git`, so a `-C dir` or
            # `-c k=v` in front cannot hide what it pushes.
            if raw[0] != "push":
                return "a push must be spelled `git push …`, with nothing between"
            branch, head = lease if lease else ("", "")
            reason = check_push(raw[1:], branch, head)
            if reason:
                return reason
    return ""


# Flags `gh` reads a value after, anywhere on the line. Their values are dropped with them,
# so `gh -R o/r pr merge` and `gh pr --repo o/r merge` read as `gh pr merge` (`0015` review
# round 1, F1). Every other `-x` / `--x` / `--x=v` is dropped alone.
_GH_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})
_MERGE_ENDPOINT = re.compile(r"pulls/[^/\s]+/merge\b")


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
    `integrate` grant also refuses an alias made during the step (`_GIT_CONFIG_ROAD`).
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
        reason = check_command(grant, str(tool_input.get("command", "")), lease)
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
