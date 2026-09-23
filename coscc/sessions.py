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
from dataclasses import dataclass, field
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

from coscc import frontend
from coscc.config import Config


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
def child_env(cwd: str, workspace: str | None = None) -> dict[str, str]:
    """What to lay over the environment a session would otherwise inherit whole.

    Every name this app puts into its own environment appears here with a value that is
    safe for somebody else's repository, because leaving one out hands the child this
    app's own.
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
    return env


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
    """
    options = ClaudeAgentOptions(
        cwd=cwd,
        # Laid over what the child would inherit. See `child_env` and what it cost twice.
        env=child_env(cwd, workspace),
        # A board step brings its own list from `policy.Grant`; everything else gets the
        # app default, which is empty. `tools=[]` and `tools=None` mean different things to
        # the SDK, so the distinction is `is None`, not truthiness.
        tools=config.effective_tools() if tools is None else list(tools),
        permission_mode=config.permission_mode(),
        resume=resume,
        fork_session=False,  # spec.md C7 — R3 needs the same id back, not a branch
        model=model if model is not None else config.model,
        max_turns=max(1, int(max_turns)),
        setting_sources=None,  # no project/user settings can widen the tool list
        # Without this the CLI rewrites the prompt before the model sees it: an `@path`
        # anywhere in it is replaced by that file's contents, and a leading `/word` is
        # dispatched as a slash command. Neither is anything this app ever means to do.
        #
        # Measured 2026-09-23 on claude-agent-sdk 0.2.158: a session with `tools=[]` --
        # no way to read a file -- was sent `@/tmp/canary.txt` and repeated the word
        # inside it. With this set it saw only the path. That mattered from `0016` on,
        # because `POST /api/units/answer` takes free text from anyone who can reach the
        # port, no login, bound to `0.0.0.0`, and puts it verbatim into the next stage's
        # prompt. An answer reading `@~/.ssh/id_rsa` would have put the key there.
        verbatim_prompts=True,
    )
    if can_use_tool is not None:
        # The second layer, and the one that matters. Eleven MCP tools were measured
        # reaching a session created with `tools=[]`, because `--tools` names the built-in
        # set only. This callback is on the path every call takes, whatever declared it.
        options.can_use_tool = can_use_tool
    if max_budget_usd:
        options.max_budget_usd = float(max_budget_usd)
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
        self._lock = asyncio.Lock()

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
        return [sid for sid, live in self._live.items() if _resolve(live.cwd) == target]

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
        """
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

        async with self._lock:
            live = self._live.get(session_id) if session_id else None
            if live is None:
                client = ClaudeSDKClient(
                    options=_options(
                        self.config, cwd, session_id, max_turns,
                        can_use_tool=can_use_tool,
                        tools=tools,
                        max_budget_usd=max_budget_usd,
                        workspace=workspace,
                        model=model,
                    )
                )
                await client.connect()
                live = Live(client=client, session_id=session_id or "", cwd=cwd)

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
        await live.client.query(text)
        async for message in live.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        collected.append(block.text)
                        yield ("chunk", block.text)
                    elif isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
                        # Said out loud so a caller assembling an artifact from the reply
                        # can tell narration from the artifact. Text that arrives before a
                        # tool call is a step thinking out loud on its way somewhere; it is
                        # never the file. See `coscc/runner.py` for what is done with it.
                        yield ("tool", getattr(block, "name", "") or "tool")
                if message.session_id:
                    resolved = message.session_id
            elif isinstance(message, sdk.ResultMessage):
                resolved = message.session_id or resolved
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
            raise Refused(
                f"resume returned {resolved} instead of {session_id} — "
                "this is the fork branch spec.md C7 warns about"
            )

        live.session_id = resolved
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
            await live.client.disconnect()

    async def close_all(self) -> None:
        """Every client is a CLI process. The plan lists leaking them as a risk, so
        shutdown is explicit rather than left to the garbage collector.
        """
        for session_id in list(self._live):
            await self.close(session_id)
