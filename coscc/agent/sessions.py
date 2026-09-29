"""Listing, creating and resuming sessions.

The read layer (`list_for_directory`, `history`) is pure disk and needs no live client, so it
holds across a restart. The session layer (`Sessions`) owns one SDK client per live session;
the client spawns its own CLI process. A session is a transcript, not a process: resuming
continues a record on disk.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
import uuid
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import claude_agent_sdk as sdk
from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ServerToolUseBlock,
    TextBlock,
    ToolUseBlock,
)

from coscc import config as cfg
from coscc import frontend
from coscc.agent import harness, instructions, transcript
from coscc.config import Config
from coscc.data import Data


# The app's own environment must not reach a session, and it cannot be removed, only
# overridden: `claude_agent_sdk` lays `options.env` over `os.environ`, so a key left out is
# inherited. Tests must assert the value the child would read, not absence from the dict.
#
# `coscc/run.py` sets `REFLEX_WEB_WORKDIR` process-wide, pointing at the served bundle
# (`coscc/_web`); a step that runs a build would compile into the installed package and the
# page would 404 while `/api/health` stays 200. `<cwd>/.web` is where a checkout's build belongs.
#
# A step's `cwd` is the unit's own worktree, so `VIRTUAL_ENV` points into the worktree, `PATH`
# loses every entry under the workspace or the installed package (a workspace's `.venv/bin`
# would run the workspace's code), and every `__REFLEX_*` this process set is overridden empty.
#
# `COS_DATA_DIR` is pointed at a directory of the session's own, not blanked: blank read as
# unset, unset read as `~/.cos`, and a step's `npm test` migrated the running app's `cos.db`.
# `config.PROTECTED_DB_VAR` names that database too.
def child_env(
    cwd: str, workspace: str | None = None, *, data_dir: str, app_db: Path, bash: bool = False
) -> dict[str, str]:
    """What to lay over the environment a session would otherwise inherit whole.

    Every name this app puts into its own environment appears here with a value safe for
    somebody else's repository, because leaving one out hands the child this app's own.
    `data_dir` is the session's throwaway data root (`scratch_dir`) and `app_db` this app's
    `cos.db`; both are required. `bash` is true when the session holds `Bash`; it then also
    gets `FOREGROUND_ENV`.
    """
    env = {
        frontend.WEB_WORKDIR_VAR: str(Path(cwd) / ".web"),
        "VIRTUAL_ENV": str(Path(cwd) / ".venv"),
        "PATH": harness.clean_path(workspace),
    }
    # This app's settings describe this app, not the workspace. Empty reads as unset to
    # `coscc/config.py` `from_env` and to `cos.mjs` for `COS_REVIEW_ROUNDS`.
    env.update({name: "" for name in os.environ if name.startswith(("COS_", "__REFLEX_"))})
    env["COS_DATA_DIR"] = data_dir
    env[cfg.PROTECTED_DB_VAR] = cfg.protect(app_db)
    if bash:
        env.update(FOREGROUND_ENV)
    return env


# The app closes a step's session once its turn ends, so a command must end in the foreground
# or be killed, never be left running where nothing reads its end.
#
# - `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`: two roads to the background never reach
#   `can_use_tool` (a command the CLI takes for read-only runs there unasked; a foreground
#   command past its timeout is moved there). With this set the first is refused by the CLI's
#   own schema and the second is killed.
# - `BASH_DEFAULT_TIMEOUT_MS`: what a call asking no `timeout` gets. The longest proof (a build
#   and `npm run e2e`) took about 196 s; 600000 is over twice that, and the CLI's default ceiling.
# - `BASH_MAX_TIMEOUT_MS`: the most a call may ask for, the same; above 600000 was never run.
FOREGROUND_ENV = {
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
    "BASH_DEFAULT_TIMEOUT_MS": "600000",
    "BASH_MAX_TIMEOUT_MS": "600000",
}


# What every throwaway data root starts with. `_drop` removes nothing without it.
SCRATCH_PREFIX = "coscc-session-"

# The project's instructions, in a session's data root, for the CLI to read.
PROMPT_FILE = "project-instructions.md"


def scratch_dir(app_root: Path) -> Path:
    """A new, empty data root for one session: `0700`, unguessable, in the OS temp dir.

    Refused, and removed again, if it landed inside `app_root` or holds it (a `TMPDIR` pointed
    into the data root would hand a step the app's own directory under another name).
    """
    made = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX)).resolve()
    root = Path(app_root).resolve()
    if made == root or root in made.parents or made in root.parents:
        made.rmdir()
        raise Refused(f"a session's data directory {made} would overlap the app's {root}")
    return made


def _drop(path: Path | None) -> None:
    """Remove a directory `scratch_dir` made, and nothing else.

    An `rmtree`, so it asks twice: the name carries `SCRATCH_PREFIX`, and it sits directly in
    the OS temp dir, not through a symlink. Otherwise silent: this runs in `finally` blocks,
    where raising would hide the step's own outcome.
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


class Suspended(Exception):
    """An update paused this session; it has a `suspend` row and is resumed on the next start.
    Its owner writes no `end` for it: the `ResultMessage` the CLI sends after `interrupt()` is
    never handed on as a `done`."""


# What a stream begun after `suspend_all` is refused with.
PAUSED = "every session was paused for an update, so no new one may open"


# --- Read layer ---


def _text_of(content: Any) -> str:
    """Flatten one stored message's content to text (a bare string or a list of blocks).
    Only text blocks are kept."""
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
    """Sessions belonging to one project directory. `cwd` is carried on every entry so a
    caller can check nothing from another project leaked in."""
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
    """Conversation read back from the SDK's session store. The app keeps no copy: a second
    store would be a second truth."""
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
    """The text of a `tool_result` block only, never the tool call that produced it: an
    excerpt is what a session *did*, not what it was asked to do."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def transcript_excerpt(session_id: str, directory: str | None, limit: int) -> tuple[str, int]:
    """The last `limit` characters of what a session *did*, and the full length.

    Assembled from the assistant's own text and the results tool calls came back with, never
    the prompt that started the turn. Subagent traffic is excluded, as `history` excludes it.
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


# --- Session layer ---


@dataclass
class Live:
    client: ClaudeSDKClient
    session_id: str
    cwd: str
    # What this session had cost as of the last turn. See `cumulative`.
    spent: dict[str, float] = field(default_factory=dict)
    # The chat's own `COS_DATA_DIR`. It lives as long as the client and is removed on close.
    scratch: Path | None = None


# How long `_shut` lets the SDK close the CLI its own way before signalling the process
# itself. Chosen, not measured.
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

    The SDK's close waits 5s for the CLI to exit on stdin EOF before SIGTERM then SIGKILL, but
    a raw asyncio cancel skips that escalation (as `asyncio.wait_for` would), so the SDK's
    close runs as its own shielded task. Past `DISCONNECT_TIMEOUT` the process is signalled
    from here. `_process` is the SDK transport's private name, read with `getattr` like
    `_transport` and `_query`; a stand-in without it is not signalled.

    `reached` is whether `connect` got as far as the control protocol; without it the SDK's
    `disconnect` closes nothing and only drops the transport, so it is closed here.
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

    A step is never resumed, so `stream(step=...)` closes it however the step ends and
    `Service.stop_step` closes it early. `close` may be called before the client exists: the
    client is then closed the moment it connects and the prompt is never sent. Every later
    call waits on the one closing the first began; cancelling a caller does not cancel it.
    """

    cwd: str = ""
    client: Any = None
    closed: bool = False
    _closing: asyncio.Task | None = None
    # The step's own `COS_DATA_DIR`, removed by `stream` once the client is closed.
    scratch: Path | None = None
    # The step's `coscc/runlog/events.py` recorder, set by `Service.run_step`. `_stream` hands
    # it every message first; chat has no handle, so none.
    recorder: Any = None
    # Whose session this is (`stream`'s `owner`), its id once `init` names it, the model it was
    # opened on, and whether `Sessions.suspend_all` paused it.
    owner: dict[str, Any] | None = None
    session_id: str = ""
    model: str | None = None
    suspended: bool = False
    # The model the session's `init` named: what the CLI resolved and will run, `[1m]` and all.
    # `""` until it arrives.
    init_model: str = ""

    async def close(self) -> None:
        self.closed = True
        if self.client is None:
            return
        if self._closing is None:
            transport = getattr(self.client, "_transport", None)
            self._closing = _begin(_shut(self.client, transport, reached=True))
        await asyncio.shield(self._closing)

    def drop_scratch(self) -> None:
        """Remove the step's data root once its client is closed.

        A Stop's cancel can land on the close itself and the CLI may live
        `DISCONNECT_TIMEOUT + KILL_AFTER` longer; a `Data` it opened in that time would remake
        the directory with nobody to remove it, so this waits for that closing, or for the one
        `_stream` began when `connect` failed.
        """
        scratch = self.scratch
        if self._closing is not None and not self._closing.done():
            self._closing.add_done_callback(lambda _: _drop(scratch))
        else:
            _drop(scratch)


def _abandon(client: Any) -> asyncio.Task:
    """Begin closing a client whose `connect` did not finish, and return the closing.

    One stopped inside the transport's own `connect` may already have spawned the CLI, and the
    SDK's `disconnect` would drop that transport without closing it, so `_shut` closes it. Both
    are read before anything is closed. The caller keeps the task on its handle as `_closing`,
    so a Stop's cancel still leaves the data root until it ends.
    """
    transport = getattr(client, "_transport", None)
    reached = getattr(client, "_query", None) is not None
    return _begin(_shut(client, transport, reached))


def _set(flow: Any, **values: Any) -> None:
    """Note something about a stream on its `StepHandle` or its `_turns` entry."""
    if flow is None:
        return
    if isinstance(flow, dict):
        flow.update(values)
    else:
        for name, value in values.items():
            setattr(flow, name, value)


def _paused(flow: Any) -> bool:
    if flow is None:
        return False
    return bool(flow.get("suspended") if isinstance(flow, dict) else flow.suspended)


# How long `suspend_all` gives `interrupt()`; it returns in under 0.01 s. Chosen, not measured.
INTERRUPT_TIMEOUT = 1.0


def _descendants(pid: int, proc: Path = Path("/proc")) -> list[tuple[int, str]]:
    """Every process under `pid`, as `(pid, start time)`, read from `/proc/*/stat`.

    A CLI killed with SIGKILL leaves its Bash tree running and writing into the worktree. The
    start time is kept so a pid reused before the kill is not the one killed. Empty where there
    is no `/proc`.
    """
    children: dict[int, list[tuple[int, str]]] = {}
    for stat in proc.glob("[0-9]*/stat"):
        try:
            text = stat.read_text()
        except OSError:
            continue
        # The command name may hold spaces and parentheses; the fields start after the last.
        fields = text[text.rfind(")") + 2 :].split()
        if len(fields) < 20:
            continue
        children.setdefault(int(fields[1]), []).append((int(stat.parent.name), fields[19]))
    out: list[tuple[int, str]] = []
    todo = [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            out.append(child)
            todo.append(child[0])
    return out


def _kill_left(taken: list[tuple[int, str]], proc: Path = Path("/proc")) -> int:
    """SIGKILL each process of `taken` still alive with the same start time; how many."""
    killed = 0
    for pid, started in taken:
        try:
            text = (proc / str(pid) / "stat").read_text()
        except OSError:
            continue
        fields = text[text.rfind(")") + 2 :].split()
        if len(fields) < 20 or fields[19] != started or fields[0] == "Z":
            continue
        with suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
            killed += 1
    return killed


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


def cumulative(message: Any) -> dict[str, float]:
    """Everything this *session* has spent so far, summed over models.

    `model_usage` is cumulative, not per-turn (each reading is the session to date), so adding
    per-turn readings would double-count. The top-level `usage` dict reports only the last
    iteration within a turn. A turn's own cost is the difference between two cumulative
    figures; `stream` does that subtraction.
    """
    total = {name: 0.0 for name in COST_FIELDS}
    total["cost_usd"] = float(getattr(message, "total_cost_usd", None) or 0.0)
    for entry in (getattr(message, "model_usage", None) or {}).values():
        if not isinstance(entry, dict):
            continue
        for name, key in _USAGE_KEYS.items():
            total[name] += float(entry.get(key) or 0)
    return total


# The longest single line of the CLI's stdout a session may receive. Left unset, the SDK's
# 1 048 576 applies, and a `Read` of a large screenshot arrives as one line and dies on
# `CLIJSONDecodeError` with no `tool_result`. The SDK compares `len()` of a `str`, so this
# counts characters; for base64 JSON the two agree. Nothing is allocated up front, so a large
# cap costs nothing until a line that long arrives. 32 MiB is chosen, not measured: a line
# weighs more than 2.08 times its file, so this holds a screenshot unless the CLI multiplies
# it by more than about 66.
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
    settings: str | None = None,
    *,
    data_dir: str,
    resume_at: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    agents: dict[str, dict[str, Any]] | None = None,
) -> ClaudeAgentOptions:
    """Map the four knobs onto the SDK.

    `fork_session=False` is written out so deleting it is a visible edit: the forking flavour
    of resume returns a *new* id and the resume guarantee fails silently.

    `max_turns` is set per step (a board step that edits files cannot finish in one turn; a
    chat turn must not quietly become several); the default is 1.

    `model` is what `coscc/agent/models.py` resolved for this stage or chat; `None` means
    `COS_MODEL` applies.

    `system_prompt` is `None` unless a caller asks: the SDK then hands the CLI an empty system
    prompt, as every chat turn and tool-less step gets. A board step with tools passes
    `runner.CLAUDE_CODE_PRESET`; it changes nothing else, and `can_use_tool` still decides.

    No settings source is loaded for any session. The project's instructions come back through
    `coscc/agent/instructions.py`, written to `PROMPT_FILE` in `data_dir`: appended to the
    preset when there is one (`--append-system-prompt-file`), the whole system prompt when
    there is not (`--system-prompt-file`), nothing written when `cwd` holds none.

    `data_dir` is the session's own data root; building a `Data` touches no disk.

    `settings` is the agent's `{"attribution": ...}` from `agents.settings_json`, passed as
    `--settings` and only beside a preset (attribution replaces the preset's commit guidance).

    `mcp_servers` is the app's own in-process servers, `{"cos": <submit>}` for a step that
    hands back an object; `strict_mcp_config` stays, so those are the only ones.
    """
    # A board step brings its own list from `policy.Grant`; everything else gets the app
    # default, empty. `tools=[]` and `tools=None` differ for the SDK, so test `is None`. Read
    # once so the environment below follows the same list.
    resolved = config.effective_tools() if tools is None else list(tools)
    options = ClaudeAgentOptions(
        cwd=cwd,
        # Laid over what the child would inherit. See `child_env`.
        env=child_env(
            cwd,
            workspace,
            data_dir=data_dir,
            app_db=Data(config.data_dir).db_path,
            bash="Bash" in resolved,
        ),
        tools=resolved,
        permission_mode=config.permission_mode(),
        resume=resume,
        fork_session=False,  # resume needs the same id back, not a branch
        model=model if model is not None else config.model,
        max_turns=max(1, int(max_turns)),
        # No source at all, not user, project, local, nor what claude.ai adds. `None` passes no
        # flag and the CLI then loads every source (machine MCP servers, skills, plugins and
        # `permissions.allow`, which answers a `Bash` call before `can_use_tool` is asked). `[]`
        # passes `--setting-sources=` with nothing after it.
        setting_sources=[],
        # And no MCP server but the ones declared here: none, or the app's own `submit`.
        strict_mcp_config=True,
        # Without this the CLI rewrites the prompt before the model sees it: an `@path` is
        # replaced by that file's contents and a leading `/word` is dispatched as a slash
        # command. `POST /api/units/answer` takes free text that goes verbatim into the next
        # stage's prompt, so an answer reading `@~/.ssh/id_rsa` would put the key there.
        verbatim_prompts=True,
        # One screenshot is one line; see `MAX_BUFFER`.
        max_buffer_size=MAX_BUFFER,
    )
    if can_use_tool is not None:
        # The second layer, and the one that matters: `--tools` names the built-in set only, so
        # MCP tools reach a session created with `tools=[]`. This callback is on the path every
        # call takes; `strict_mcp_config` keeps those tools out of init but this decides.
        options.can_use_tool = can_use_tool
    if max_budget_usd:
        options.max_budget_usd = float(max_budget_usd)
    if mcp_servers:
        options.mcp_servers = dict(mcp_servers)
    if agents:
        # `policy.SUBAGENTS`: helpers inside this session, held by its own `can_use_tool`.
        options.agents = {name: AgentDefinition(**spec) for name, spec in agents.items()}
    project = instructions.read(cwd).text
    if system_prompt is not None:
        options.system_prompt = dict(system_prompt)
        if settings is not None:
            options.settings = settings
    if project:
        # Through a file, never as a value in argv: the SDK passes a string or an `append` as one
        # argument and Linux refuses an `execve` argument past `MAX_ARG_STRLEN` with `E2BIG`,
        # an error that names nothing of why. The file sits in the session's data root, `0700`.
        path = Path(data_dir) / PROMPT_FILE
        path.write_text(project, encoding="utf-8")
        if system_prompt is not None:
            # The SDK has no file form for a preset's `append`; the CLI has the flag.
            options.extra_args["append-system-prompt-file"] = str(path)
        else:
            options.system_prompt = {"type": "file", "path": str(path)}
    if effort is not None:
        # What `coscc/agent/models.py` resolved for this stage and label. Unset, the SDK default.
        options.effort = effort
    if resume_at is not None:
        # The session goes on from its safe point, and nothing past it is read.
        # `resume_drops_turn` is never set: the CLI refused 3 of 5 mid-turn cuts with it.
        options.resume_session_at = resume_at
        # The resumed session keeps the system prompt it recorded at its start and reads no skill
        # or rule an update changed. Only a preset or custom prompt carries `snapshot`; the file
        # form becomes an empty custom prompt with the same file appended, never the file's text
        # in argv (`E2BIG`).
        sp = options.system_prompt
        if isinstance(sp, dict) and sp.get("type") == "file":
            options.extra_args["append-system-prompt-file"] = sp.get("path")
            options.system_prompt = {"type": "custom", "prompt": "", "snapshot": True}
        elif isinstance(sp, dict):
            options.system_prompt = {**sp, "snapshot": True}
        else:
            options.system_prompt = {"type": "custom", "prompt": "", "snapshot": True}
    return options


def _resolve(directory: str) -> Path | None:
    """The directory as one comparable value, or `None` if it is not a path at all.

    `ValueError` is caught alongside `OSError` because an embedded null raises that one; a
    crash here would turn a question about sessions into a 500.
    """
    try:
        return Path(directory).expanduser().resolve()
    except OSError, ValueError:
        return None


class Sessions:
    """Holds the live clients. One per session id, created on demand."""

    def __init__(self, config: Config):
        self.config = config
        # Who counts as a workspace. Defaults to the env list; `Service` replaces it with the
        # union of env and store. Injected so a store-backed workspace that passed the service
        # gate is not refused here; the guard stays as the last thing before a CLI spawns.
        self.membership = config.is_workspace
        self._live: dict[str, Live] = {}
        self._created_here: set[str] = set()
        # Board steps in flight, each with the one client it spawned. Never in `_live`: a step
        # is not resumed, so its client is closed when the step ends.
        self._steps: set[StepHandle] = set()
        self._lock = asyncio.Lock()
        # Chat turns answering now, by an id of their own so a new session with no id yet can
        # still be named and cut. Each is `{id, session_id, workspace, started}` plus the task
        # reading it. Board steps are never here: `_steps` has those.
        self._turns: dict[str, dict[str, Any]] = {}
        # Told when a turn ends, so the updater waiting on it need not guess by the clock.
        self.on_turn_end: Any = None
        # Set by `suspend_all`: from then on no stream opens, so a step between two sessions
        # while the update waits ends saying why, rather than open one the hand-off cuts with no
        # `suspend` row. `resume_after_update` clears it.
        self.paused = False

    def created_here(self, session_id: str) -> bool:
        return session_id in self._created_here

    def live_in(self, directory: str) -> list[str]:
        """Session ids with a live client in this directory, newest registration last.

        `pull` rewrites files under a running turn, so the service asks this before `git`
        touches a workspace. It sees this process only: `_live` is in memory, so a second app
        on the same working folder is invisible. Do not read an empty list as "nobody is working".

        Compared by resolved path, not by string: the caller builds the directory from the
        store and `stream` was given whatever the browser sent.
        """
        target = _resolve(directory)
        if target is None:
            return []
        found = [sid for sid, live in self._live.items() if _resolve(live.cwd) == target]
        # A step in flight counts too, with no session id to name it by until the step ends.
        found += ["(running step)" for h in self._steps if _resolve(h.cwd) == target]
        return found

    def _begin_turn(
        self,
        cwd: str,
        session_id: str | None,
        owner: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        turn = {
            "id": uuid.uuid4().hex,
            "session_id": session_id or "",
            "workspace": cwd,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "task": asyncio.current_task(),
            # What `suspend_all` needs of a stream with no step: chat, Gebo.
            "owner": owner,
            "client": None,
            "cwd": cwd,
            "model": model,
            "suspended": False,
        }
        self._turns[turn["id"]] = turn
        return turn

    def in_flight(self) -> list[dict[str, Any]]:
        """The chat turns answering now, oldest first. This process only."""
        return [
            {k: t[k] for k in ("id", "session_id", "workspace", "started")}
            for t in sorted(self._turns.values(), key=lambda t: t["started"])
        ]

    async def cut_turn(self, turn_id: str) -> bool:
        """End one chat turn: its reader is cancelled and its client closed.

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
        """Record a session as this app's; otherwise a session created and resumed in one
        process would look foreign to knob 4."""
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
        settings: str | None = None,
        owner: dict[str, Any] | None = None,
        resume_at: str | None = None,
        spent_before: dict[str, float] | None = None,
        mcp_servers: dict[str, Any] | None = None,
        agents: dict[str, dict[str, Any]] | None = None,
    ):
        """Send one prompt and yield the reply as it arrives.

        `workspace` is who is asked about membership; `cwd` is where the session runs. They
        differ for a board step, which runs in the unit's worktree, a directory that is not a
        workspace and must not become one.

        Yields ``("chunk", text)`` zero or more times, then exactly one ``("done", {...})``.
        The browser and the proof command both consume this: no test-only path. Creates the
        session when `session_id` is None, resumes it otherwise.

        `step` is a board step's handle. With one, the client is closed however this ends
        (finished, raised, closed early, abandoned by its reader) and never kept for resuming.
        Without one, chat needs `_live`.

        `owner` says whose session this is (`kind`: `step`, `opening`, `closing`, `integrate`,
        `estimate`, `chat`, and what that owner needs to take it up again) and is
        what `suspend_all` writes into a `suspend` row. `resume_at` goes on from a safe point of
        `session_id` (`_options`). `spent_before` is what the session cost before this client:
        `{}` makes `done.cost` the whole session's, since the CLI's total carries over a resume.
        A stream `suspend_all` paused raises `Suspended` and yields no `done`; one begun after
        it is `Refused`. `mcp_servers` goes to `_options` as it is.
        """
        if self.paused:
            raise Refused(PAUSED)
        resolved_model = model if model is not None else self.config.model
        if step is None:
            flow: Any = self._begin_turn(cwd, session_id, owner, resolved_model)
        else:
            flow = step
            step.cwd, step.owner, step.model = cwd, owner, resolved_model
            step.session_id = session_id or ""
        inner = self._stream(
            cwd,
            text,
            session_id,
            max_turns,
            can_use_tool,
            tools,
            max_budget_usd,
            workspace,
            model,
            system_prompt,
            effort,
            step,
            settings,
            flow=flow,
            resume_at=resume_at,
            spent_before=spent_before,
            mcp_servers=mcp_servers,
            agents=agents,
        )
        if step is None:
            turn = flow
            try:
                async with aclosing(inner):
                    async for item in inner:
                        if item[0] == "session":
                            turn["session_id"] = item[1]
                        yield item
            except Exception as e:
                if turn["suspended"] and not isinstance(e, Suspended):
                    raise Suspended(f"session {turn['session_id']} was paused for an update") from e
                raise
            finally:
                self._turns.pop(turn["id"], None)
                if self.on_turn_end is not None:
                    self.on_turn_end()
            return
        self._steps.add(step)
        try:
            async with aclosing(inner):
                async for item in inner:
                    yield item
        except Exception as e:
            if step.suspended and not isinstance(e, Suspended):
                raise Suspended(f"session {step.session_id} was paused for an update") from e
            raise
        finally:
            try:
                await step.close()
            finally:
                # A Stop's cancel can land on this very close; the closing goes on without
                # us, and the handle must still leave the set.
                self._steps.discard(step)
                # After the close, so the CLI is gone before its data root is.
                step.drop_scratch()

    async def _stream(
        self,
        cwd,
        text,
        session_id,
        max_turns,
        can_use_tool,
        tools,
        max_budget_usd,
        workspace,
        model,
        system_prompt,
        effort,
        step,
        settings=None,
        *,
        flow: Any = None,
        resume_at: str | None = None,
        spent_before: dict[str, float] | None = None,
        mcp_servers: dict[str, Any] | None = None,
        agents: dict[str, dict[str, Any]] | None = None,
    ):
        member = workspace if workspace is not None else cwd
        if not self.membership(member):
            raise Refused(f"not a configured workspace: {member}")
        if session_id is not None and not self.config.may_resume(self.created_here(session_id)):
            # The transcript is visible in the listing, but writing to it would put a second
            # process on a record another one may still hold open.
            raise Refused(
                f"session {session_id} was not created by this app; "
                "resuming it is off until spec.md open question 3 is tested"
            )

        # A chat's data root this call made and `_live` does not own yet: removed here if the
        # call ends before the session is kept. A step's is `stream`'s to remove.
        made: Path | None = None
        try:
            async with self._lock:
                live = self._live.get(session_id) if session_id and step is None else None
                if live is None:
                    # Made before the client, so a client that fails to build or connect still
                    # leaves it with an owner.
                    scratch = scratch_dir(Data(self.config.data_dir).root)
                    if step is None:
                        made = scratch
                    else:
                        step.scratch = scratch
                    client = ClaudeSDKClient(
                        options=_options(
                            self.config,
                            cwd,
                            session_id,
                            max_turns,
                            can_use_tool=can_use_tool,
                            tools=tools,
                            max_budget_usd=max_budget_usd,
                            workspace=workspace,
                            model=model,
                            system_prompt=system_prompt,
                            effort=effort,
                            settings=settings,
                            data_dir=str(scratch),
                            resume_at=resume_at,
                            mcp_servers=mcp_servers,
                            agents=agents,
                        )
                    )
                    if step is None:
                        await client.connect()
                        _set(flow, client=client)
                        if _paused(flow):
                            # Paused while it was starting: nothing is sent.
                            await _begin(_shut(client, getattr(client, "_transport", None), True))
                            raise Suspended(
                                "the session was paused for an update before its prompt was sent"
                            )
                    else:
                        try:
                            await client.connect()
                        except BaseException:
                            # A cancel or failure while the CLI was starting. The handle has no
                            # client yet, so nothing else closes what `connect` spawned.
                            step._closing = _abandon(client)
                            await asyncio.shield(step._closing)
                            raise
                        # Only now: `disconnect` during `connect` closes nothing and drops the
                        # transport, so a Stop before this point only marks the handle closed.
                        step.client = client
                    live = Live(
                        client=client,
                        session_id=session_id or "",
                        cwd=cwd,
                        scratch=made,
                        spent=dict(spent_before or {}),
                    )
                else:
                    _set(flow, client=live.client)
            if step is not None and step.closed:
                if step.suspended:
                    raise Suspended("the step was paused for an update before its prompt was sent")
                raise Refused("the step was stopped before its prompt was sent")

            resolved = live.session_id
            collected: list[str] = []
            turn: dict[str, float] = {}
            turns = 0
            duration_ms = 0
            terminal = ""
            # Which model ids the SDK billed this session to: the keys of `model_usage`, the
            # session's own record of the model it ran on.
            used: list[str] = []
            # Yielded once, the first moment `resolved` has a value, so a caller that dies before
            # `done` still has a session id to read a transcript excerpt back with. `Service.stream`
            # (chat) drops this kind; `api.py` would turn it into a spurious `done` line.
            told_session = bool(resolved)
            if told_session:
                yield ("session", resolved)
            # The tokens of the first API call this client made: what reloading a resumed
            # session cost.
            first_call: dict[str, int] | None = None
            await live.client.query(text)
            async for message in live.client.receive_response():
                if step is not None and step.recorder is not None:
                    # Synchronous and swallowing: the kinds this yields, and when, are unchanged.
                    try:
                        step.recorder.message(message)
                    except Exception:  # noqa: BLE001
                        pass
                if isinstance(message, sdk.SystemMessage) and message.subtype == "init":
                    # The id is known here, before the first reply, so an update that pauses the
                    # session now can still name it.
                    said = str((message.data or {}).get("session_id") or "")
                    if step is not None and (message.data or {}).get("model"):
                        # Set before `session` is yielded below, so the caller reads it there.
                        step.init_model = str(message.data["model"])
                    if said and session_id and said != session_id:
                        # A resume that came back as another session is refused.
                        await _begin(
                            _shut(live.client, getattr(live.client, "_transport", None), True)
                        )
                        self._live.pop(session_id, None)
                        raise Refused(
                            f"resume returned {said} instead of {session_id} — "
                            "this is the fork branch spec.md C7 warns about"
                        )
                    if said:
                        resolved = said
                        _set(flow, session_id=said)
                        if not told_session:
                            told_session = True
                            yield ("session", resolved)
                if isinstance(message, AssistantMessage):
                    if first_call is None and isinstance(message.usage, dict):
                        first_call = {
                            "input_tokens": int(message.usage.get("input_tokens") or 0),
                            "cache_creation_tokens": int(
                                message.usage.get("cache_creation_input_tokens") or 0
                            ),
                            "cache_read_tokens": int(
                                message.usage.get("cache_read_input_tokens") or 0
                            ),
                        }
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collected.append(block.text)
                            yield ("chunk", block.text)
                        elif isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
                            # Tells a caller assembling an artifact from the reply where one
                            # piece of text ends and the next begins; the runner cuts the
                            # artifact at its own title line.
                            yield ("tool", getattr(block, "name", "") or "tool")
                    if message.session_id:
                        resolved = message.session_id
                        _set(flow, session_id=resolved)
                    if resolved and not told_session:
                        told_session = True
                        yield ("session", resolved)
                elif isinstance(message, sdk.ResultMessage):
                    resolved = message.session_id or resolved
                    if resolved and not told_session:
                        told_session = True
                        yield ("session", resolved)
                    # The one message carrying what this cost.
                    total = cumulative(message)
                    used = sorted(str(k) for k in (getattr(message, "model_usage", None) or {}))
                    turn = {k: total[k] - live.spent.get(k, 0.0) for k in total}
                    live.spent = total
                    turns += int(getattr(message, "num_turns", 0) or 0)
                    duration_ms += int(getattr(message, "duration_ms", 0) or 0)
                    # Why the loop stopped: a turn that hit its ceiling must be
                    # distinguishable from one that finished.
                    terminal = getattr(message, "terminal_reason", None) or (
                        getattr(message, "subtype", "") or ""
                    )

            if _paused(flow):
                # The `ResultMessage` after `interrupt()` ends the loop above like any other; it is
                # not this session's end, and its owner must not read it as one.
                raise Suspended(f"session {resolved} was paused for an update")

            if session_id and resolved != session_id:
                # Never observed, but the failure is silent, so it is checked.
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
            # Kept as a float and rounded, not truncated: a turn can cost under a cent.
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
                    "first_call": first_call,
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
        """Every client is a CLI process, so shutdown is explicit rather than left to the
        garbage collector."""
        for session_id in list(self._live):
            await self.close(session_id)
        for step in list(self._steps):
            await step.close()
            self._steps.discard(step)
            step.drop_scratch()

    async def suspend_all(self) -> list[dict[str, Any]]:
        """Pause every stream this process has open, all at once, and say where each one can be
        taken up again.

        Each is marked first, so its owner gets `Suspended` instead of an end; then its CLI's
        descendants are listed, the transcript's boundary read, `interrupt()` sent, the client
        closed by `_shut`, and whatever of that list is still alive killed. Returns one
        `suspend` row's fields per stream, for the updater to write. A stream with no session id
        or no client yet is closed all the same and marked `unresumable`. Nothing here writes to
        a transcript or runs git.
        """
        self.paused = True
        flows: list[Any] = [*self._steps, *self._turns.values()]
        records = await asyncio.gather(*(self._suspend(f) for f in flows))
        for f in flows:
            if isinstance(f, dict):
                self._turns.pop(f["id"], None)
                live = self._live.get(f.get("session_id") or "")
                if live is not None and live.client is f.get("client"):
                    self._live.pop(f["session_id"], None)
                    _drop(live.scratch)
            else:
                self._steps.discard(f)
        return list(records)

    async def _suspend(self, flow: Any) -> dict[str, Any]:
        _set(flow, suspended=True)
        get = flow.get if isinstance(flow, dict) else (lambda k: getattr(flow, k))
        client, sid, cwd = get("client"), str(get("session_id") or ""), str(get("cwd") or "")
        owner = dict(get("owner") or {})
        record: dict[str, Any] = {
            "owner": owner,
            "cwd": cwd,
            "session_id": sid,
            "model": get("model"),
            "start_at": owner.get("start_at"),
        }

        async def close() -> None:
            if isinstance(flow, StepHandle):
                await flow.close()
            elif client is not None:
                await _begin(_shut(client, getattr(client, "_transport", None), True))

        if client is None or not sid:
            await close()
            return {**record, "unresumable": "no session id yet"}
        process = getattr(getattr(client, "_transport", None), "_process", None)
        taken = _descendants(process.pid) if getattr(process, "pid", None) else []
        path = transcript.path_for(cwd, sid)
        edge = transcript.boundary(path)
        try:
            await asyncio.wait_for(client.interrupt(), INTERRUPT_TIMEOUT)
        except Exception:  # noqa: BLE001 - a CLI that does not answer is closed all the same
            pass
        await close()
        _kill_left(taken)
        record["boundary"] = edge
        try:
            found = transcript.cut(path, edge)
        except (transcript.Unreadable, OSError) as e:
            return {**record, "unresumable": f"the transcript could not be read: {e}"}
        spent = transcript.spent_after(path, edge)
        record.update(
            safe_uuid=found["safe_uuid"],
            dropped=found["dropped"],
            api_calls=found["api_calls"],
            **({"spent_usd": spent} if spent is not None else {"cost_unknown": True}),
        )
        return record
