"""The helpers one run started, and the hooks that hold what the agents of that run may do.

`Agent`, `SendMessage` and `ListAgents` never reach `can_use_tool` in the `default` mode, so the
`PreToolUse` hook here is what refuses them: a helper `policy.SUBAGENTS` does not name, one in
the background, a helper starting a helper, `ListAgents` (it lists every Claude session on the
machine) and a message to anyone but `"main"` or a helper of this run. `decide` still asks every
other call, with the helper's `agent_id`.

`Helpers` is one run's ledger, fed by `SubagentStart`/`SubagentStop` and the stream's `task_*`
system messages; it backs `peers`, the message filter and what `tell` hands the recorder
(`worker_start`, `worker_end`, `worker_write`). It is dropped with the run.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from claude_agent_sdk import HookMatcher, tool
from claude_agent_sdk.types import HookEvent, SyncHookJSONOutput

from coscc.agent.policy import AGENT_TOOL, PEERS_TOOL, SEND_MESSAGE, SUBAGENTS, WRITE_TOOLS

log = logging.getLogger(__name__)

LIST_AGENTS = "ListAgents"
# The leading session, as `SendMessage` names it.
MAIN = "main"
# `PEERS_TOOL` without its server's prefix.
PEERS = PEERS_TOOL.rsplit("__", 1)[-1]
# The first word of every message between the agents of a step.
KINDS = ("need", "changed", "done", "blocked")

# In the leading session's prompt and in every `worker`'s, the same words.
PROTOCOL = """# Working with helpers

When the plan's `## Parallelization` names two or more steps, finish and commit the steps of
`## Order of work` first. Then start one `worker` per parallel step with `Agent`, all in the same
turn and never with `run_in_background`: the step's name as `description`, its paths and what it
reports in the prompt. Once all are done, read each one's diff, commit, and run
`## Verification` once on the whole worktree. Only the leading session commits: a helper runs
git only to read.

The agents of this step talk through `SendMessage`, to `"main"` (the leading session) or to an id
`mcp__cos__peers` lists; any other address is refused.
- A worker sends a message only when it needs something outside its own paths, changes an
  interface another step uses, or is done or blocked.
- A message is 1 to 20 lines. Its first line opens with one of `need:`, `changed:`, `done:`,
  `blocked:`; a `changed:` or `done:` names each `path:line` it means.
- No message for courtesy or to say one arrived. When agents disagree, the leading session
  decides.
- Every worker ends with exactly one `done:` or `blocked:` to `"main"`: that is the report its
  step names."""


# `policy.SUBAGENTS` as the session gets them: `worker`'s prompt carries `PROTOCOL`.
DEFINITIONS = {
    **SUBAGENTS,
    "worker": {**SUBAGENTS["worker"], "prompt": f"{SUBAGENTS['worker']['prompt']}\n\n{PROTOCOL}"},
}


class Told(TypedDict, total=False):
    """What `tell` hands the recorder with a `worker_*` kind."""

    agent_id: str
    helper: str
    step: str
    at_ms: int | None
    status: str
    total_tokens: int | None
    duration_ms: int | None
    tool: str
    path: str


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Helper:
    id: str
    kind: str = ""
    # The `description` of the `Agent` call that started it: a worker's step.
    step: str = ""
    started_ms: int | None = None
    ended_ms: int | None = None
    # From `task_notification`; the SDK gives no cost per helper.
    total_tokens: int | None = None
    duration_ms: int | None = None
    status: str = ""
    told_start: bool = False
    told_end: bool = False


def _deny(reason: str) -> SyncHookJSONOutput:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


class Helpers:
    """One run's helpers, by the `agent_id` the CLI gave each, which is also its task's id.

    `tell(kind, fields)` is the recorder's, or `None`: told once each of `worker_start` (when both
    its start and its step are known), `worker_end` (when both its end and its usage are) and
    every `worker_write`. `close` tells what is left.
    """

    def __init__(self, tell: Callable[[str, Told], None] | None = None):
        self.tell = tell
        self.seen: dict[str, Helper] = {}

    def _of(self, agent_id: str) -> Helper:
        return self.seen.setdefault(agent_id, Helper(agent_id))

    def _tell(self, kind: str, fields: Told) -> None:
        if self.tell is None:
            return
        try:
            self.tell(kind, fields)
        except Exception:
            # A measurement, never a reason to stop a helper.
            log.exception("%s was not recorded", kind)

    def _fields(self, h: Helper) -> Told:
        return {"agent_id": h.id, "helper": h.kind, "step": h.step}

    def _started(self, h: Helper, force: bool = False) -> None:
        if h.told_start or h.started_ms is None or (not h.step and not force):
            return
        h.told_start = True
        self._tell("worker_start", {**self._fields(h), "at_ms": h.started_ms})

    def _ended(self, h: Helper, force: bool = False) -> None:
        if h.told_end or (not force and (h.ended_ms is None or h.total_tokens is None)):
            return
        self._started(h, force=True)
        h.told_end = True
        self._tell(
            "worker_end",
            {
                **self._fields(h),
                "at_ms": h.ended_ms,
                "status": h.status,
                "total_tokens": h.total_tokens,
                "duration_ms": h.duration_ms,
            },
        )

    def refused(
        self, tool_name: str, tool_input: Mapping[str, object], agent_id: str | None
    ) -> str:
        """Why the hook denies this call, or ""."""
        if tool_name == AGENT_TOOL:
            if agent_id is not None:
                return "a helper may not start another helper"
            if tool_input.get("subagent_type") not in SUBAGENTS:
                return f"only these helpers may be started: {', '.join(SUBAGENTS)}"
            if tool_input.get("run_in_background"):
                return "a helper runs in the foreground: this session ends when its turn ends"
        if tool_name == LIST_AGENTS:
            return f"ListAgents lists sessions outside this step; call {PEERS_TOOL}"
        if tool_name == SEND_MESSAGE:
            to = tool_input.get("to")
            if to != MAIN and to not in self.seen:
                return (
                    f'SendMessage goes only to "{MAIN}" or to a helper {PEERS_TOOL} lists: {to!r}'
                )
        return ""

    async def pre_tool_use(
        self, hook_input: Any, _tool_use_id: str | None, _context: Any
    ) -> SyncHookJSONOutput:
        tool_name = str(hook_input.get("tool_name") or "")
        tool_input = hook_input.get("tool_input") or {}
        agent_id = hook_input.get("agent_id")
        reason = self.refused(tool_name, tool_input, agent_id)
        if reason:
            return _deny(reason)
        if agent_id is not None and tool_name in WRITE_TOOLS:
            h = self._of(agent_id)
            path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            self._tell(
                "worker_write",
                {**self._fields(h), "tool": tool_name, "path": str(path), "at_ms": _now_ms()},
            )
        # No opinion: `can_use_tool` still decides.
        return {}

    async def subagent_start(
        self, hook_input: Any, _tool_use_id: str | None, _context: Any
    ) -> SyncHookJSONOutput:
        h = self._of(str(hook_input.get("agent_id") or ""))
        h.kind = h.kind or str(hook_input.get("agent_type") or "")
        h.started_ms = _now_ms()
        self._started(h)
        return {}

    async def subagent_stop(
        self, hook_input: Any, _tool_use_id: str | None, _context: Any
    ) -> SyncHookJSONOutput:
        h = self._of(str(hook_input.get("agent_id") or ""))
        h.ended_ms = _now_ms()
        self._ended(h)
        return {}

    def system(self, message: Any) -> None:
        """One of the stream's `SystemMessage`s; only `task_*` ones count."""
        subtype = str(getattr(message, "subtype", "") or "")
        data = getattr(message, "data", None) or {}
        if not subtype.startswith("task_") or not data.get("task_id"):
            return
        h = self._of(str(data["task_id"]))
        if subtype == "task_started":
            h.step = h.step or str(data.get("description") or "")
            h.kind = h.kind or str(data.get("subagent_type") or "")
            self._started(h)
        elif subtype == "task_notification":
            usage = data.get("usage") or {}
            h.status = str(data.get("status") or "")
            h.total_tokens = int(usage.get("total_tokens") or 0)
            h.duration_ms = int(usage.get("duration_ms") or 0)
            self._ended(h)
        elif subtype == "task_updated":
            h.status = str((data.get("patch") or {}).get("status") or h.status)

    def close(self) -> None:
        """Tells what the run never finished telling: a helper whose end or usage never came."""
        for h in self.seen.values():
            if h.started_ms is not None or h.step:
                self._ended(h, force=True)

    def listing(self) -> str:
        lines = [
            f"{h.id} · {h.step or '(no step named)'} · "
            + ("done" if h.ended_ms is not None or h.told_end else "running")
            for h in self.seen.values()
        ]
        return "\n".join(lines) or "no helper has started in this step"

    async def _peers(self, _args: object) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": self.listing()}]}

    def tool(self) -> Any:
        """`peers`, for the `cos` server."""
        return tool(
            PEERS,
            "The helpers of this step, one per line: id · step · running or done. SendMessage "
            f'goes only to "{MAIN}" or to one of these ids.',
            {},
        )(self._peers)

    def hooks(self) -> dict[HookEvent, list[HookMatcher]]:
        """For `ClaudeAgentOptions.hooks`: every tool call, and each helper's start and stop."""
        return {
            "PreToolUse": [HookMatcher(hooks=[self.pre_tool_use])],
            "SubagentStart": [HookMatcher(hooks=[self.subagent_start])],
            "SubagentStop": [HookMatcher(hooks=[self.subagent_stop])],
        }
