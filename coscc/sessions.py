"""Listing, creating and resuming sessions.

Two layers live here, and the split from `spec.md` matters:

- The **read layer** (`list_for_directory`, `history`) is pure disk. It needs no live
  client, which is what lets R1 and R6 hold across a restart of this app.
- The **session layer** (`Sessions`) owns one SDK client per live session. The client
  spawns its own CLI process, so the lifetime is this app's to decide.

A session is a transcript, not a process (`spec.md`). Resuming means continuing a record
on disk, not attaching to something still running.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import claude_agent_sdk as sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ServerToolUseBlock,
    TextBlock,
    ToolUseBlock,
)

from coscc import config as cfg
from coscc import frontend, instructions
from coscc.config import Config
from coscc.data import Data


# The app's own environment must not reach a session, and it cannot be removed -- only
# overridden. `claude_agent_sdk` builds the child environment as
# `{k: v for k, v in os.environ.items() if k != "CLAUDECODE"}` and then lays `options.env`
# on top, so a key left out of `options.env` is a key the child *inherits*. Absence is not
# deletion here.
#
# `coscc/run.py` sets `REFLEX_WEB_WORKDIR` process-wide, pointing at the bundle this app
# serves -- `coscc/_web` inside the installed package. A step that runs a build reads it
# and compiles **into the installed package**: `index.html` is replaced by a fresh
# scaffold and the page answers 404 while `/api/health` stays 200. Measured twice on
# 2026-09-23, on the machine the step was running for, to the copy running the step.
#
# The first attempt at this fix left the key out of `options.env` and asserted it was
# absent *from the dictionary*. That test passed and the bundle was destroyed again an
# hour later, because the assertion was about this process and the damage was in the
# child. It is overridden now, and the test asks what value the child would read.
#
# `<cwd>/.web` is where a checkout's build belongs: `coscc/frontend.py` `web_dir` returns
# exactly that for anything not packaged, and a session's `cwd` is the workspace.
#
# Since `0017` a step's `cwd` is the unit's own worktree, not the workspace, and three more
# names are laid over for the same reason: `VIRTUAL_ENV` points into the worktree, `PATH`
# loses every entry under the workspace or the installed package (a workspace's
# `.venv/bin` first on `PATH` runs the workspace's code, not the unit's), and every
# `__REFLEX_*` this process set (`coscc/run.py:56,172`) is overridden with an empty value.
#
# Since `0076` `COS_DATA_DIR` is not blanked but pointed at a directory of the session's
# own. Blank read as unset, unset read as `~/.cos`, and a step's `npm test` migrated the
# running app's `cos.db` to a schema the app could not read. `config.PROTECTED_DB_VAR`
# names that database too, for the code that reaches `~/.cos` without reading the setting.
def child_env(
    cwd: str, workspace: str | None = None, *, data_dir: str, app_db: Path
) -> dict[str, str]:
    """What to lay over the environment a session would otherwise inherit whole.

    Every name this app puts into its own environment appears here with a value that is
    safe for somebody else's repository, because leaving one out hands the child this
    app's own.

    `data_dir` is the session's throwaway data root (`scratch_dir`) and `app_db` this
    app's `cos.db`. Both are required, so no session environment can be built without them.
    """
    from coscc import worktrees  # here, not at the top: worktrees imports prcomment

    env = {
        frontend.WEB_WORKDIR_VAR: str(Path(cwd) / ".web"),
        "VIRTUAL_ENV": str(Path(cwd) / ".venv"),
        "PATH": worktrees.clean_path(workspace),
    }
    # This app's settings describe this app, not the workspace. Empty reads as unset to
    # `coscc/config.py` `from_env` for every one of them (host and port only since the
    # `0017` review, F1), and to `cos.mjs` for `COS_REVIEW_ROUNDS`. `sessions_test.py`
    # loads the config from what the child reads.
    env.update({name: "" for name in os.environ if name.startswith(("COS_", "__REFLEX_"))})
    env["COS_DATA_DIR"] = data_dir
    env[cfg.PROTECTED_DB_VAR] = cfg.protect(app_db)
    return env


# What every throwaway data root starts with. `_drop` removes nothing without it.
SCRATCH_PREFIX = "coscc-session-"

# The project's instructions, in a session's data root, for the CLI to read (`0088`, F1).
PROMPT_FILE = "project-instructions.md"


def scratch_dir(app_root: Path) -> Path:
    """A new, empty data root for one session: `0700`, unguessable, in the OS temp dir.

    `0076` R1 and R2. Refused, and removed again, if it landed inside `app_root` or holds
    it -- a `TMPDIR` pointed into the data root would otherwise hand a step the app's own
    directory under another name.
    """
    made = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX)).resolve()
    root = Path(app_root).resolve()
    if made == root or root in made.parents or made in root.parents:
        made.rmdir()
        raise Refused(f"a session's data directory {made} would overlap the app's {root}")
    return made


def _drop(path: Path | None) -> None:
    """Remove a directory `scratch_dir` made, and nothing else.

    An `rmtree`, so it asks twice: the name carries `SCRATCH_PREFIX`, and it sits directly
    in the OS temp dir, not through a symlink. Anything else is left alone, silently --
    this runs in `finally` blocks, where raising would hide the step's own outcome.
    """
    if path is None:
        return
    p = Path(path)
    if not p.name.startswith(SCRATCH_PREFIX) or p.is_symlink():
        return
    if p.resolve().parent != Path(tempfile.gettempdir()).resolve():
        return
    shutil.rmtree(p, ignore_errors=True)


class Refused(Exception):
    """A request the config does not allow. Carries a reason the caller can show."""


# ---------------------------------------------------------------------------
# Read layer
# ---------------------------------------------------------------------------


def _text_of(content: Any) -> str:
    """Flatten one stored message's content to text.

    The transcript stores content either as a bare string or as a list of blocks. Only
    text blocks are kept: tool blocks have no reading in a chat-only app, and rendering
    them is explicitly a later intent.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def list_for_directory(directory: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Sessions belonging to one project directory (R1).

    `cwd` is carried through on every entry so a caller can check that nothing from
    another project leaked in — R1 asks for exactly that.
    """
    infos = sdk.list_sessions(directory=directory, limit=limit, include_worktrees=False)
    return [
        {
            "session_id": i.session_id,
            "summary": i.custom_title or i.summary or i.first_prompt or "(no summary)",
            "cwd": i.cwd,
            "last_modified": i.last_modified,
            "created_at": i.created_at,
            "git_branch": i.git_branch,
        }
        for i in infos
    ]


def history(session_id: str, directory: str | None = None) -> list[dict[str, Any]]:
    """Conversation read back from the SDK's session store (R6).

    The app keeps no copy. `spec.md` C6: a second store would be a second truth, and the
    one that counts is the one Claude actually reads.
    """
    messages = sdk.get_session_messages(session_id, directory=directory)
    out = []
    for m in messages:
        if m.parent_tool_use_id or m.parent_agent_id:
            continue  # subagent traffic, not this conversation
        text = _text_of((m.message or {}).get("content"))
        if not text.strip():
            continue
        out.append({"role": m.type, "text": text, "uuid": m.uuid})
    return out


def exists(session_id: str, directory: str | None = None) -> bool:
    return sdk.get_session_info(session_id, directory=directory) is not None


def _tool_result_text(content: Any) -> str:
    """The text of a `tool_result` block only — never the tool call that produced it.

    `0019` plan step 2: an excerpt is what a session *did*, not what it was asked to do,
    so a `ToolUseBlock`'s own input is skipped here the same way `history` skips it.
    """
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def transcript_excerpt(
    session_id: str, directory: str | None, limit: int
) -> tuple[str, int]:
    """The last `limit` characters of what a session *did*, and the full length.

    `0019` plan step 2. Assembled from the assistant's own text and the results tool
    calls came back with — never the prompt that started the turn, which is not
    something the session did. Subagent traffic is excluded, as `history` excludes it.
    """
    if not session_id:
        raise ValueError("transcript_excerpt needs a session id")
    messages = sdk.get_session_messages(session_id, directory=directory)
    pieces: list[str] = []
    for m in messages:
        if m.parent_tool_use_id or m.parent_agent_id:
            continue
        content = (m.message or {}).get("content")
        if m.type == "assistant":
            text = _text_of(content)
        elif m.type == "user":
            found = []
            for block in content or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    found.append(_tool_result_text(block.get("content")))
            text = "\n".join(p for p in found if p)
        else:
            continue
        if text.strip():
            pieces.append(text)
    full = "\n".join(pieces)
    return full[-limit:], len(full)


# ---------------------------------------------------------------------------
# Session layer
# ---------------------------------------------------------------------------


@dataclass
class Live:
    client: ClaudeSDKClient
    session_id: str
    cwd: str
    # What this session had cost as of the last turn. See `_cumulative` for why a running
    # total has to be kept here rather than read fresh each time.
    spent: dict[str, float] = field(default_factory=dict)
    # `0076`. The chat's own `COS_DATA_DIR`. It lives as long as the client, across turns,
    # and is removed when the session is closed.
    scratch: Path | None = None


# How long `_shut` lets the SDK close the CLI its own way before signalling the process
# itself. Chosen, not measured: with `KILL_AFTER` it stays under the 10 seconds `0034`'s
# intent gives a step's process to be gone.
DISCONNECT_TIMEOUT = 5.0

# How long `_shut` waits after its SIGTERM before SIGKILL. Chosen, not measured.
KILL_AFTER = 3.0

# Closings in flight. asyncio keeps only a weak reference to a task, and a closing must
# outlive the task that began it when that one is cancelled.
_CLOSING: set[asyncio.Task] = set()


def _begin(coro: Any) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _CLOSING.add(task)
    task.add_done_callback(_CLOSING.discard)
    return task


async def _shut(client: Any, transport: Any, reached: bool) -> None:
    """Close a client, and see that the CLI it spawned is gone.

    `0034` review round 2, F3. The SDK's own close waits 5s for the CLI to exit on stdin
    EOF before it sends SIGTERM, then SIGKILL -- but a raw asyncio cancel skips that
    escalation (its own docstring says so), and an `asyncio.wait_for` around it is one. So
    the SDK's close runs as its own task, shielded: nothing here cancels it. Past
    `DISCONNECT_TIMEOUT` the process is signalled from here, and the SDK's close, still
    waiting on it, then finishes. `_process` is the SDK transport's private name, read
    with `getattr` like `_transport` and `_query`; a stand-in without it is not signalled.

    `reached` is whether `connect` got as far as the control protocol. Without it the SDK's
    `disconnect` closes nothing and only drops the transport (review round 1, F1), so the
    transport is closed here.
    """
    process = getattr(transport, "_process", None)

    async def sdk() -> None:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 - closing is best-effort; the step's end must not wait on it
            pass
        if transport is not None and not reached:
            try:
                await transport.close()
            except Exception:  # noqa: BLE001
                pass

    closing = _begin(sdk())
    try:
        await asyncio.wait_for(asyncio.shield(closing), DISCONNECT_TIMEOUT)
    except TimeoutError:
        pass
    process = process or getattr(transport, "_process", None)
    if process is None or process.returncode is not None:
        return
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), KILL_AFTER)
    except Exception:  # noqa: BLE001 - a timeout, or anything else: SIGKILL either way
        with suppress(ProcessLookupError):
            process.kill()


@dataclass(eq=False)  # identity, not value: handles live in a set
class StepHandle:
    """The one client a board step spawned, and the one way to close it.

    `0034`. A board step used to leave its client in `_live` for the life of the app, so
    every step ever run kept a CLI process (measured: 14 of them, 230-285 MB each). A step
    is never resumed, so it has no reason to stay: `stream(step=...)` closes it however
    the step ends, and `Service.stop_step` closes it early.

    `close` may be called before the client exists -- `client` is set only once `connect`
    has returned; `stream` then closes the client the moment it connects and never sends
    the prompt. Every later call waits on the one closing the first began, and cancelling
    a caller does not cancel that closing (review round 2, F3 and F4).
    """

    cwd: str = ""
    client: Any = None
    closed: bool = False
    _closing: asyncio.Task | None = None
    # `0076`. The step's own `COS_DATA_DIR`, set before the client is built and removed by
    # `stream` once the client is closed, however the step ended.
    scratch: Path | None = None
    # `0073`. The step's `coscc/events.py` recorder, set by `Service.run_step`. `_stream`
    # hands it every message before anything else reads it; chat has no handle, so none.
    recorder: Any = None

    async def close(self) -> None:
        self.closed = True
        if self.client is None:
            return
        if self._closing is None:
            transport = getattr(self.client, "_transport", None)
            self._closing = _begin(_shut(self.client, transport, reached=True))
        await asyncio.shield(self._closing)

    def drop_scratch(self) -> None:
        """Remove the step's data root once its client is closed (`0076` R3).

        A Stop's cancel can land on the close itself: the closing goes on without its
        caller, and the CLI may live `DISCONNECT_TIMEOUT + KILL_AFTER` longer. A `Data` it
        opened in that time would make the directory again with nobody left to remove it
        (`0076` review round 1, F1), so the removal waits for that closing -- or for the
        one `_stream` began when `connect` failed (round 2, F2).
        """
        scratch = self.scratch
        if self._closing is not None and not self._closing.done():
            self._closing.add_done_callback(lambda _: _drop(scratch))
        else:
            _drop(scratch)


def _abandon(client: Any) -> asyncio.Task:
    """Begin closing a client whose `connect` did not finish, and return the closing.

    One stopped inside the transport's own `connect` may already have spawned the CLI,
    and the SDK's `disconnect` would drop that transport without closing it, so `_shut`
    closes the transport itself. Both are read before anything is closed.

    The caller keeps the task on its handle as `_closing`, so a Stop's cancel landing on
    this closing still leaves the step's data root until it ends (`0076` review round 2, F2).
    """
    transport = getattr(client, "_transport", None)
    reached = getattr(client, "_query", None) is not None
    return _begin(_shut(client, transport, reached))


# What one turn cost, in the shape `journal.COST_FIELDS` adds up.
COST_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)

# How `ResultMessage.model_usage` spells them. Its keys come through verbatim from the CLI
# and are camelCase; ours are not, and translating in one place keeps that from spreading.
_USAGE_KEYS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "cache_read_tokens": "cacheReadInputTokens",
    "cache_creation_tokens": "cacheCreationInputTokens",
}


def _cumulative(message: Any) -> dict[str, float]:
    """Everything this *session* has spent so far, summed over models.

    **`model_usage` is cumulative, not per-turn.** Measured on 2026-09-21 by running two
    turns on one client: `cacheReadInputTokens` came back 1608 then 5512, and
    `total_cost_usd` 0.0169 then 0.0363 — each reading is the session to date. Adding them
    up per turn would therefore double-count, which is exactly the failure `plan.md` Risk 4
    names: the total looks measured and is wrong.

    The top-level `usage` dict is not the answer either. It reports only the last iteration
    within a turn — the same run showed `input_tokens: 2` where `model_usage` showed 1171.

    So the cumulative figure is what the SDK gives honestly, and a turn's own cost is the
    difference between two of them. `stream` does that subtraction.
    """
    total = {name: 0.0 for name in COST_FIELDS}
    total["cost_usd"] = float(getattr(message, "total_cost_usd", None) or 0.0)
    for entry in (getattr(message, "model_usage", None) or {}).values():
        if not isinstance(entry, dict):
            continue
        for name, key in _USAGE_KEYS.items():
            total[name] += float(entry.get(key) or 0)
    return total


# The longest single line of the CLI's stdout a session may receive (`0091`). Left unset,
# the SDK's `_DEFAULT_MAX_BUFFER_SIZE` of 1 048 576 applies, and a `Read` of an image
# arrives as one line: on 2026-09-25 run `c58b7e48` of `0082`'s `impl` read
# `.screens/settings-1440x900.png` -- 504 656 bytes, 1440x4298 -- and died on
# `CLIJSONDecodeError`, five commits in, with no `tool_result`. Since `0083` every UI unit's
# `impl` and `review` must open such images.
#
# The SDK compares `len()` of a `str`, so this counts characters, not bytes; for JSON
# carrying base64 the two agree (`0091` `spike.md ## U1`). Nothing is allocated up front:
# the transport keeps the pieces of the line it has, and RSS grew the same 152 KiB with a
# 1 MiB cap and a 16 MiB one (`spike.md ## U3`), so a large cap costs nothing until a line
# that long arrives.
#
# 32 MiB is chosen, not measured. What a line weighs against the file it carries is known
# only from below -- more than 2.08 times (`spike.md ## U2`) -- so this holds that
# screenshot unless the CLI multiplies it by more than about 66 (`spec.md` C1). A longer
# line still ends the session exactly as before.
MAX_BUFFER = 32 * 1024 * 1024


def _options(
    config: Config,
    cwd: str,
    resume: str | None,
    max_turns: int = 1,
    can_use_tool: Any = None,
    tools: list[str] | None = None,
    max_budget_usd: float | None = None,
    workspace: str | None = None,
    model: str | None = None,
    system_prompt: dict[str, str] | None = None,
    effort: str | None = None,
    *,
    data_dir: str,
) -> ClaudeAgentOptions:
    """Map the four knobs onto the SDK.

    `fork_session=False` is the line `spec.md` C7 warns about: the forking flavour of
    resume returns a *new* id, every other part of the app keeps working, and R3 fails
    silently. It is written out rather than left to the default so that deleting it is a
    visible edit.

    `max_turns` is a parameter rather than the constant it was, because `spec.md` R11 puts the
    ceiling on the step: a board step that has to edit files cannot finish in one turn, and
    a chat turn must not quietly become several. The default is still 1, so every caller
    that does not ask gets the old behaviour (`plan.md` C6).

    `model` is what `coscc/models.py` resolved for this stage or for chat. `None` means
    nobody resolved one, and `COS_MODEL` applies as it always did.

    `system_prompt` is `None` unless a caller asks (`0037`). Left unset, the SDK hands the
    CLI an empty system prompt, which is what every chat turn and every tool-less step
    still gets. A board step with tools passes `runner.CLAUDE_CODE_PRESET`, so the session
    carries Claude Code's own guidance on using those tools. It changes nothing else here:
    the tool list, the permission mode, `setting_sources` and the callback are what they
    would have been without it, and what a step may do is still decided by `can_use_tool`.

    Since `0088` no settings source is loaded, for any session, so the CLI reads nothing of
    the user's, the machine's or the project's own configuration. The project's
    instructions come back through `coscc/instructions.py`, written to `PROMPT_FILE` in
    `data_dir`: appended to the preset when there is one (`--append-system-prompt-file`),
    as the whole system prompt when there is not (`--system-prompt-file`), and not at all
    when `cwd` holds none -- the session is then exactly the one it was before, and
    nothing is written.

    `data_dir` is the session's own data root (`0076`); the database it protects is the
    one `config` names. Building a `Data` touches no disk.
    """
    options = ClaudeAgentOptions(
        cwd=cwd,
        # Laid over what the child would inherit. See `child_env` and what it cost twice.
        env=child_env(
            cwd, workspace, data_dir=data_dir, app_db=Data(config.data_dir).db_path
        ),
        # A board step brings its own list from `policy.Grant`; everything else gets the
        # app default, which is empty. `tools=[]` and `tools=None` mean different things to
        # the SDK, so the distinction is `is None`, not truthiness.
        tools=config.effective_tools() if tools is None else list(tools),
        permission_mode=config.permission_mode(),
        resume=resume,
        fork_session=False,  # spec.md C7 — R3 needs the same id back, not a branch
        model=model if model is not None else config.model,
        max_turns=max(1, int(max_turns)),
        # `0088`. No source at all -- not user, project, local, nor what claude.ai adds.
        # `None` said "no project/user settings" here and meant the opposite: on this SDK
        # it passes no flag, and the CLI then loads every source, so each session carried
        # the machine's MCP servers, skills, plugins and `permissions.allow` -- the last
        # one answering a `Bash` call before `can_use_tool` was asked. `[]` passes
        # `--setting-sources=` with nothing after it; `0088` `spike.md ## U6` measured the
        # argv, the init and the gate on 0.2.159.
        setting_sources=[],
        # And no MCP server but the ones declared here, which are none.
        strict_mcp_config=True,
        # Without this the CLI rewrites the prompt before the model sees it: an `@path`
        # anywhere in it is replaced by that file's contents, and a leading `/word` is
        # dispatched as a slash command. Neither is anything this app ever means to do.
        #
        # Measured 2026-09-23 on claude-agent-sdk 0.2.158: a session with `tools=[]` --
        # no way to read a file -- was sent `@/tmp/canary.txt` and repeated the word
        # inside it. With this set it saw only the path. That mattered from `0016` on,
        # because `POST /api/units/answer` takes free text -- from anyone who could reach
        # the port until `0070`, from anyone holding the password or a session since -- and
        # puts it verbatim into the next stage's
        # prompt. An answer reading `@~/.ssh/id_rsa` would have put the key there.
        verbatim_prompts=True,
        # `0091`. One screenshot is one line; see `MAX_BUFFER`.
        max_buffer_size=MAX_BUFFER,
    )
    if can_use_tool is not None:
        # The second layer, and the one that matters. Eleven MCP tools were measured
        # reaching a session created with `tools=[]`, because `--tools` names the built-in
        # set only. This callback is on the path every call takes, whatever declared it.
        # Since `0088` `strict_mcp_config` keeps those tools out of the session's init;
        # this is still what decides whether a call runs.
        options.can_use_tool = can_use_tool
    if max_budget_usd:
        options.max_budget_usd = float(max_budget_usd)
    project = instructions.read(cwd).text
    if system_prompt is not None:
        options.system_prompt = dict(system_prompt)
    if project:
        # Through a file, never as a value in argv (`0088` review round 1, F1): the SDK
        # passes a string or an `append` as one argument, and Linux refuses an `execve`
        # whose single argument passes `MAX_ARG_STRLEN` (32 pages) with `E2BIG` -- every
        # session in a workspace whose instructions grew past it would fail to start, with
        # an error that names nothing of why. The file sits in the session's own data root,
        # `0700`, removed with it.
        path = Path(data_dir) / PROMPT_FILE
        path.write_text(project, encoding="utf-8")
        if system_prompt is not None:
            # The SDK has no file form for a preset's `append`; the CLI has the flag.
            options.extra_args["append-system-prompt-file"] = str(path)
        else:
            options.system_prompt = {"type": "file", "path": str(path)}
    if effort is not None:
        # `0033`: what `coscc/models.py` resolved for this stage and label. Unset, the
        # SDK's own default applies, as it did before.
        options.effort = effort
    return options


def _resolve(directory: str) -> Path | None:
    """The directory as one comparable value, or `None` if it is not a path at all.

    `ValueError` is caught alongside `OSError` because an embedded null raises that one,
    not the other — and a crash here would turn a question about sessions into a 500.
    """
    try:
        return Path(directory).expanduser().resolve()
    except (OSError, ValueError):
        return None


class Sessions:
    """Holds the live clients. One per session id, created on demand."""

    def __init__(self, config: Config):
        self.config = config
        # Who counts as a workspace. Defaults to the env list, and `Service` replaces it
        # with the union of env and store (`spec.md` R21).
        #
        # The reason this has to be injected rather than hardcoded: this
        # layer used to ask `config.is_workspace` directly, so a store-backed workspace
        # passed the service gate and was refused here — two implementations of one
        # question, which is exactly what R10 exists to prevent. The guard stays (it is
        # the last thing before a CLI process is spawned); only the answer is shared.
        self.membership = config.is_workspace
        self._live: dict[str, Live] = {}
        self._created_here: set[str] = set()
        # `0034`. Board steps in flight, each with the one client it spawned. Never in
        # `_live`: a step is not resumed, so its client is closed when the step ends.
        self._steps: set[StepHandle] = set()
        self._lock = asyncio.Lock()
        # `0068` R8. Chat turns answering now, by an id of their own so a new session with
        # no id yet can still be named and cut. Each is `{id, session_id, workspace,
        # started}` plus the task reading it. Board steps are never here: `_steps` has those.
        self._turns: dict[str, dict[str, Any]] = {}
        # Told when a turn ends, so the updater waiting on it need not guess by the clock.
        self.on_turn_end: Any = None

    def created_here(self, session_id: str) -> bool:
        return session_id in self._created_here

    def live_in(self, directory: str) -> list[str]:
        """Session ids with a live client in this directory, newest registration last.

        `spec.md` R6: `pull` rewrites files under a running turn, so the service asks this
        before it lets `git` near a workspace.

        **It sees this process only.** `_live` is a dict in memory, so a second app on the
        same working folder is invisible here and `pull` will proceed under its session.
        That is `spec.md` C2, recorded and not fixed — closing it needs a mark on disk, which
        `intent.md` did not authorise. Do not read an empty list as "nobody is working".

        Compared by resolved path, not by string: the caller builds the directory from the
        store and `stream` was given whatever the browser sent.
        """
        target = _resolve(directory)
        if target is None:
            return []
        found = [sid for sid, live in self._live.items() if _resolve(live.cwd) == target]
        # A step in flight counts too (`0034`), with no session id to name it by until the
        # step ends. A finished one no longer blocks `pull` until the next restart.
        found += ["(running step)" for h in self._steps if _resolve(h.cwd) == target]
        return found

    def _begin_turn(self, cwd: str, session_id: str | None) -> dict[str, Any]:
        turn = {
            "id": uuid.uuid4().hex,
            "session_id": session_id or "",
            "workspace": cwd,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "task": asyncio.current_task(),
        }
        self._turns[turn["id"]] = turn
        return turn

    def in_flight(self) -> list[dict[str, Any]]:
        """`0068` R8. The chat turns answering now, oldest first. This process only."""
        return [
            {k: t[k] for k in ("id", "session_id", "workspace", "started")}
            for t in sorted(self._turns.values(), key=lambda t: t["started"])
        ]

    async def cut_turn(self, turn_id: str) -> bool:
        """`0068` R10. End one chat turn: its reader is cancelled and its client closed.

        `False` when the turn had already ended. The reply stops where it was; nothing is
        written for it, like a chat whose tab was closed.
        """
        turn = self._turns.pop(turn_id, None)
        if turn is None:
            return False
        task = turn.get("task")
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        if turn["session_id"]:
            await self.close(turn["session_id"])
        if self.on_turn_end is not None:
            self.on_turn_end()
        return True

    def adopt(self, session_id: str) -> None:
        """Record a session as this app's.

        The proof command creates a session, closes it, and resumes in the same process;
        without this the app's own session would look foreign to knob 4.
        """
        self._created_here.add(session_id)

    async def stream(
        self,
        cwd: str,
        text: str,
        session_id: str | None = None,
        max_turns: int = 1,
        can_use_tool: Any = None,
        tools: list[str] | None = None,
        max_budget_usd: float | None = None,
        workspace: str | None = None,
        model: str | None = None,
        system_prompt: dict[str, str] | None = None,
        effort: str | None = None,
        step: StepHandle | None = None,
    ):
        """Send one prompt and yield the reply as it arrives.

        `workspace` is who is asked about membership; `cwd` is where the session runs.
        They are the same thing except for a board step since `0017`, which runs in the
        unit's worktree — a directory that is not a workspace and must not become one.

        Yields ``("chunk", text)`` zero or more times, then exactly one
        ``("done", {...})``. The browser and the proof command both consume this, which
        is what keeps `spec.md`'s "no separate route for tests" true — a test-only path is
        a path nobody runs for real.

        Creates the session when `session_id` is None (R2), resumes it otherwise (R3).

        `step` is a board step's handle (`0034`). With one, the client is closed however
        this ends -- finished, raised, closed early through the handle, or abandoned by
        its reader -- and is never kept for resuming. Without one nothing here changes:
        chat needs `_live`.
        """
        inner = self._stream(
            cwd, text, session_id, max_turns, can_use_tool, tools, max_budget_usd,
            workspace, model, system_prompt, effort, step,
        )
        if step is None:
            turn = self._begin_turn(cwd, session_id)
            try:
                async with aclosing(inner):
                    async for item in inner:
                        if item[0] == "session":
                            turn["session_id"] = item[1]
                        yield item
            finally:
                self._turns.pop(turn["id"], None)
                if self.on_turn_end is not None:
                    self.on_turn_end()
            return
        step.cwd = cwd
        self._steps.add(step)
        try:
            async with aclosing(inner):
                async for item in inner:
                    yield item
        finally:
            try:
                await step.close()
            finally:
                # A Stop's cancel can land on this very close (review round 2, F4); the
                # closing goes on without us, and the handle must still leave the set.
                self._steps.discard(step)
                # After the close, so the CLI is gone before its data root is (`0076` R3).
                step.drop_scratch()

    async def _stream(
        self, cwd, text, session_id, max_turns, can_use_tool, tools, max_budget_usd,
        workspace, model, system_prompt, effort, step,
    ):
        member = workspace if workspace is not None else cwd
        if not self.membership(member):
            raise Refused(f"not a configured workspace: {member}")
        if session_id is not None and not self.config.may_resume(self.created_here(session_id)):
            # spec.md C1. The transcript is visible in the listing, but writing to it
            # would put a second process on a record another one may still hold open.
            raise Refused(
                f"session {session_id} was not created by this app; "
                "resuming it is off until spec.md open question 3 is tested"
            )

        # `0076`. A chat's data root this call made and `_live` does not own yet: removed
        # here if the call ends before the session is kept. A step's is `stream`'s to remove.
        made: Path | None = None
        try:
            async with self._lock:
                live = self._live.get(session_id) if session_id and step is None else None
                if live is None:
                    # Made before the client, so a client that fails to build or connect
                    # still leaves it with an owner (`0076` R3).
                    scratch = scratch_dir(Data(self.config.data_dir).root)
                    if step is None:
                        made = scratch
                    else:
                        step.scratch = scratch
                    client = ClaudeSDKClient(
                        options=_options(
                            self.config, cwd, session_id, max_turns,
                            can_use_tool=can_use_tool,
                            tools=tools,
                            max_budget_usd=max_budget_usd,
                            workspace=workspace,
                            model=model,
                            system_prompt=system_prompt,
                            effort=effort,
                            data_dir=str(scratch),
                        )
                    )
                    if step is None:
                        await client.connect()
                    else:
                        try:
                            await client.connect()
                        except BaseException:
                            # A cancel or a failure while the CLI was starting. The handle
                            # has no client yet, so nothing else will close what `connect`
                            # got as far as spawning (`0034` review round 1, F1).
                            step._closing = _abandon(client)
                            await asyncio.shield(step._closing)
                            raise
                        # Only now: `disconnect` during `connect` closes nothing and drops the
                        # transport, so a Stop before this point only marks the handle closed.
                        step.client = client
                    live = Live(
                        client=client, session_id=session_id or "", cwd=cwd, scratch=made
                    )
            if step is not None and step.closed:
                raise Refused("the step was stopped before its prompt was sent")

            resolved = live.session_id
            collected: list[str] = []
            turn: dict[str, float] = {}
            turns = 0
            duration_ms = 0
            terminal = ""
            # Which model ids the SDK billed this session to: the keys of `model_usage`. This
            # is the session's own record of the model it ran on, as opposed to the model the
            # app asked for (`0004_no-setting-says-which-model-runs-a-stage`, outcome 4).
            used: list[str] = []
            # `0019` plan step 2. Yielded once, the first moment `resolved` has a value, so a
            # caller that dies before `done` — the whole reason this unit exists — still has a
            # session id to read a transcript excerpt back with. `Service.stream` (chat) drops
            # this kind; `api.py` would otherwise turn it into a spurious `done` line in chat.
            told_session = bool(resolved)
            if told_session:
                yield ("session", resolved)
            await live.client.query(text)
            async for message in live.client.receive_response():
                if step is not None and step.recorder is not None:
                    # `0073` R3, R5. Synchronous and swallowing: the kinds this yields, and
                    # when, are what they were without it.
                    try:
                        step.recorder.message(message)
                    except Exception:  # noqa: BLE001
                        pass
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collected.append(block.text)
                            yield ("chunk", block.text)
                        elif isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
                            # Said out loud so a caller assembling an artifact from the reply
                            # knows where one piece of text ends and the next begins. The
                            # runner keeps every piece and cuts the artifact at its own title
                            # line (`0099`). See `coscc/runner.py` for what is done with it.
                            yield ("tool", getattr(block, "name", "") or "tool")
                    if message.session_id:
                        resolved = message.session_id
                    if resolved and not told_session:
                        told_session = True
                        yield ("session", resolved)
                elif isinstance(message, sdk.ResultMessage):
                    resolved = message.session_id or resolved
                    if resolved and not told_session:
                        told_session = True
                        yield ("session", resolved)
                    # The one message carrying what this cost. An earlier version read `session_id` off it
                    # and dropped the rest, so every turn the app ran was unaccounted for.
                    total = _cumulative(message)
                    used = sorted(str(k) for k in (getattr(message, "model_usage", None) or {}))
                    turn = {k: total[k] - live.spent.get(k, 0.0) for k in total}
                    live.spent = total
                    turns += int(getattr(message, "num_turns", 0) or 0)
                    duration_ms += int(getattr(message, "duration_ms", 0) or 0)
                    # Why the loop stopped. A turn that ran into its ceiling has to be
                    # distinguishable from one that finished, or the turn bound turns a bounded
                    # failure back into a silent one.
                    terminal = getattr(message, "terminal_reason", None) or (
                        getattr(message, "subtype", "") or ""
                    )

            if session_id and resolved != session_id:
                # Never observed, but the failure C7 describes is silent, so it is checked
                # rather than assumed.
                await live.client.disconnect()
                self._live.pop(session_id, None)
                _drop(live.scratch)
                raise Refused(
                    f"resume returned {resolved} instead of {session_id} — "
                    "this is the fork branch spec.md C7 warns about"
                )

            live.session_id = resolved
            if step is None:
                self._live[resolved] = live
            self._created_here.add(resolved)
            cost = {name: int(turn.get(name, 0.0)) for name in COST_FIELDS}
            cost["turns"] = turns
            cost["duration_ms"] = duration_ms
            # Kept as a float and rounded rather than truncated: a turn can cost less than a
            # cent, and `int()` would report every one of those as free.
            cost["cost_usd"] = round(turn.get("cost_usd", 0.0), 6)
            yield (
                "done",
                {
                    "session_id": resolved,
                    "text": "".join(collected),
                    "cwd": cwd,
                    "cost": cost,
                    "terminal_reason": terminal,
                    "models_used": used,
                },
            )
        finally:
            if made is not None and (live is None or self._live.get(live.session_id) is not live):
                _drop(made)

    async def send(self, cwd: str, text: str, session_id: str | None = None) -> dict[str, Any]:
        """`stream` collected into one result, for callers that do not want the pieces."""
        result: dict[str, Any] = {}
        async for kind, payload in self.stream(cwd, text, session_id):
            if kind == "done":
                result = payload
        return result

    async def close(self, session_id: str) -> None:
        live = self._live.pop(session_id, None)
        if live is not None:
            try:
                await live.client.disconnect()
            finally:
                _drop(live.scratch)

    async def close_all(self) -> None:
        """Every client is a CLI process. The plan lists leaking them as a risk, so
        shutdown is explicit rather than left to the garbage collector.
        """
        for session_id in list(self._live):
            await self.close(session_id)
        for step in list(self._steps):
            await step.close()
            self._steps.discard(step)
            step.drop_scratch()
